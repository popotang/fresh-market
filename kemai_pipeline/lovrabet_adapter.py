"""Bounded runtime reads through Lovrabet CLI. No direct SQL/MySQL fallback."""

import json
import subprocess
from pathlib import Path


class ContractError(RuntimeError):
    pass


class LovrabetAdapter:
    def __init__(self, config=None, *, executor=subprocess.run, max_pages=100):
        self.config = config or json.loads(Path(__file__).with_name("datasets.json").read_text())
        self.executor = executor
        self.max_pages = max_pages
        self.requests = 0
        self.root = Path(__file__).resolve().parent.parent

    def invoke(self, *args):
        self.requests += 1
        try:
            result = self.executor(["lovrabet", *args, "--appcode", self.config["appcode"],
                                    "--format", "compress"], capture_output=True, text=True,
                                   timeout=90, cwd=self.root)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ContractError(f"Lovrabet transport: {type(error).__name__}") from error
        try:
            envelope = json.loads(result.stdout)
        except (ValueError, TypeError) as error:
            # CLI errors are sometimes stderr text even with --format compress.
            # Expose only fixed contract codes, never arbitrary SQL/auth payloads.
            known = ("BATCH_CONFLICT", "DIGEST_MISMATCH", "CAPTURE_NOT_ACTIVE", "CAPTURE_SUPERSEDED",
                     "STREAM_BUSY_RESUME_ACTIVE_CAPTURE", "INCOMPLETE_CAPTURE", "COUNT_MISMATCH",
                     "DATABASE_WINDOW_COUNT_MISMATCH", "WRITE_ACCEPTANCE_REQUIRED", "STREAM_ACCEPTANCE_REQUIRED",
                     "BATCH_BYTE_LIMIT", "BATCH_ROW_LIMIT", "ROW_OUTSIDE_WINDOW", "INCOMPLETE_MODEL_READ",
                     "OBSERVED_ROW_CHANGED", "WINDOW_MANIFEST_CONFLICT", "INCOMPLETE_BATCH_MANIFEST_READ",
                     "BUSINESS_ROWS_MISSING", "WINDOW_ROW_BUDGET", "INVALID_OBSERVATION_HASH")
            signal = result.stderr or ""
            code = next((code for code in known if code in signal), "INVALID_CLI_ENVELOPE")
            if "Statement count limit" in signal: code = "SERVER_STATEMENT_LIMIT"
            raise ContractError(code) from error
        if result.returncode or envelope.get("ok") is not True:
            # Raw stderr/envelopes can contain URLs or auth diagnostics: do not echo.
            raise ContractError("Lovrabet request failed; inspect CLI authentication/permissions")
        data = envelope.get("data")
        if not isinstance(data, dict) or "result" not in data:
            raise ContractError("Lovrabet response has no data.result")
        return data["result"]

    def page(self, table, *, where=None, select=None, page=1, size=100):
        if table not in self.config["datasets"]:
            raise ContractError(f"No verified dataset mapping: {table}")
        condition = {"$and": [{"tenant_id": {"$eq": self.config["tenant_id"]}}, where or {}]}
        fields = list(dict.fromkeys(["id", "tenant_id", *(select or [])])) if select else None
        params = {"where": condition, "currentPage": page, "pageSize": size,
                  "orderBy": [{"id": "asc"}]}
        if fields:
            params["select"] = fields
        result = self.invoke("data", "filter", "--code", self.config["datasets"][table],
                             "--params", json.dumps(params, ensure_ascii=False))
        if not isinstance(result, dict) or not isinstance(result.get("tableData"), list):
            raise ContractError("Missing tableData")
        paging = result.get("paging", {})
        total = paging.get("totalCount")
        if (paging.get("currentPage") != page or paging.get("pageSize") != size
                or type(total) is not int or total < 0):
            raise ContractError("Invalid pagination metadata")
        rows = result["tableData"]
        if len(rows) != min(size, max(0, total - (page - 1) * size)):
            raise ContractError("Truncated or inconsistent page")
        for row in rows:
            if str(row.get("tenant_id")) != self.config["tenant_id"]:
                raise ContractError("Tenant predicate was not respected")
        return rows, total

    def read_all(self, table, *, where=None, select=None):
        rows, expected = self.page(table, where=where, select=select)
        pages = max(1, (expected + 99) // 100)
        if pages > self.max_pages:
            raise ContractError("Read budget exceeded; narrow the date or stream range")
        for page in range(2, pages + 1):
            chunk, total = self.page(table, where=where, select=select, page=page)
            if total != expected:
                raise ContractError("Dataset changed during pagination; retry the bounded read")
            rows.extend(chunk)
        keys = [row.get("id") for row in rows]
        if len(set(keys)) != len(keys) or any(key is None for key in keys):
            raise ContractError("Missing or duplicate row ID across pages")
        return rows

    def count(self, table, *, where):
        if table not in self.config["datasets"]:
            raise ContractError(f"No verified dataset mapping: {table}")
        params = {"where": {"$and": [{"tenant_id": {"$eq": self.config["tenant_id"]}}, where]},
                  "aggregate": [{"column": "id", "type": "COUNT", "alias": "row_count"}]}
        result = self.invoke("data", "aggregate", "--code", self.config["datasets"][table],
                             "--params", json.dumps(params))
        rows = result.get("tableData") if isinstance(result, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or type(rows[0].get("row_count")) is not int:
            raise ContractError("Invalid aggregate count")
        if rows[0]["row_count"] < 0:
            raise ContractError("Negative aggregate count")
        return rows[0]["row_count"]

    def gateway(self, op, **params):
        return self.invoke("bff", "exec", "--name", "kemaiSyncGateway", "--params",
                           json.dumps({"op": op, "tenant": self.config["tenant_id"], **params},
                                      ensure_ascii=False, allow_nan=False))

    def begin_capture(self, state):
        return self.gateway("capture.begin", **self.capture_scope(state))

    @staticmethod
    def capture_scope(state):
        return {k: state[k] for k in ("stream", "capture_id", "window_key", "mode", "source_key_version")}

    def batch_upsert(self, batch):
        value = dict(batch)
        records = value.pop("records")
        value["records_json"] = json.dumps(records, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False)
        value.setdefault("mode", self.write_mode)
        return self.gateway("batch.upsert", **value)

    def batch_result(self, batch_id):
        return self.gateway("batch.result", stream=self.write_stream, batch_id=batch_id)

    def bind_capture(self, state):
        self.write_stream, self.write_mode = state["stream"], state["mode"]

    def commit_window(self, manifest):
        return self.gateway("window.commit", **manifest)

    def fail_capture(self, state, reason):
        return self.gateway("capture.fail", **self.capture_scope(state), reason=reason)


    def note_capture(self, state, status, reason):
        return self.gateway("capture.note", **self.capture_scope(state), status=status, reason=reason)

    def batch_results(self, capture_id):
        rows = self.read_all("sync_pipeline_batches", where={"stream":{"$eq":self.write_stream},
            "capture_id":{"$eq":capture_id}}, select=["batch_id","receipt"])
        values = {}
        for row in rows:
            receipt = row["receipt"]
            if isinstance(receipt, str): receipt = json.loads(receipt)
            if row["batch_id"] in values: raise ContractError("Duplicate batch receipt")
            values[row["batch_id"]] = receipt
        return values
