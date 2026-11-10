"""Bounded runtime reads through Lovrabet CLI. No direct SQL/MySQL fallback."""

import json
import re
import subprocess
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


class ContractError(RuntimeError):
    pass


# Recognizable placeholder for datasets not yet registered in the data center.
# Any access through this adapter must fail closed before a CLI call is made.
PENDING_DATASET_REGISTRATION = "PENDING_DATASET_REGISTRATION"
ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
                          r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$")


def _json_default(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class LovrabetAdapter:
    def __init__(self, config=None, *, executor=subprocess.run, max_pages=100):
        self.config = config or json.loads(Path(__file__).with_name("datasets.json").read_text())
        self.executor = executor
        self.max_pages = max_pages
        self.requests = 0
        self.root = Path(__file__).resolve().parent.parent

    def dataset_code(self, table):
        datasets = self.config.get("datasets", {})
        if table not in datasets:
            raise ContractError(f"No verified dataset mapping: {table}")
        code = datasets[table]
        if not isinstance(code, str) or not code or code.startswith(PENDING_DATASET_REGISTRATION):
            raise ContractError(f"Dataset pending registration (待数据集注册), access refused: {table}")
        return code

    def _tenantless(self, table):
        return table in set(self.config.get("tenantless_datasets", []))

    def _pk(self, table):
        return self.config.get("primary_keys", {}).get(table, "id")

    def _scoped_where(self, table, where):
        if self._tenantless(table):
            return where or {}
        return {"$and": [{"tenant_id": {"$eq": self.config["tenant_id"]}}, where or {}]}

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
                     "BUSINESS_ROWS_MISSING", "WINDOW_ROW_BUDGET", "INVALID_OBSERVATION_HASH",
                     "PERSISTED_LIST_HASH_MISMATCH")
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
        code = self.dataset_code(table)
        condition = self._scoped_where(table, where)
        pk = self._pk(table)
        base_fields = [pk] if self._tenantless(table) else [pk, "tenant_id"]
        fields = list(dict.fromkeys([*base_fields, *(select or [])])) if select else None
        params = {"where": condition, "currentPage": page, "pageSize": size,
                  "orderBy": [{pk: "asc"}]}
        if fields:
            params["select"] = fields
        result = self.invoke("data", "filter", "--code", code,
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
            if not self._tenantless(table) and str(row.get("tenant_id")) != self.config["tenant_id"]:
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
        keys = [row.get(self._pk(table)) for row in rows]
        if len(set(keys)) != len(keys) or any(key is None for key in keys):
            raise ContractError("Missing or duplicate row ID across pages")
        return rows

    def count(self, table, *, where):
        code = self.dataset_code(table)
        params = {"where": self._scoped_where(table, where),
                  "aggregate": [{"column": self._pk(table), "type": "COUNT", "alias": "row_count"}]}
        result = self.invoke("data", "aggregate", "--code", code,
                             "--params", json.dumps(params))
        rows = result.get("tableData") if isinstance(result, dict) else None
        if not isinstance(rows, list) or len(rows) != 1 or type(rows[0].get("row_count")) is not int:
            raise ContractError("Invalid aggregate count")
        if rows[0]["row_count"] < 0:
            raise ContractError("Negative aggregate count")
        return rows[0]["row_count"]

    @staticmethod
    def _same(observed, intended):
        if observed is None or intended is None:
            return observed is None and intended is None
        if observed == intended or str(observed) == str(intended):
            return True
        if (isinstance(observed, str) and isinstance(intended, str)
                and observed.lstrip()[:1] in ('{', '[')
                and intended.lstrip()[:1] in ('{', '[')):
            try:
                return json.loads(observed) == json.loads(intended)
            except (TypeError, ValueError):
                pass
        if (isinstance(observed, (int, float)) and not isinstance(observed, bool)
                and isinstance(intended, str) and ISO_DATETIME.fullmatch(intended)):
            try:
                parsed = datetime.fromisoformat(intended.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
                return abs(int(parsed.timestamp() * 1000) - int(observed)) < 1000
            except (ValueError, OverflowError, OSError):
                return False
        return False

    def _find_unique(self, table, row, unique_keys):
        where = {key: {"$eq": row[key]} for key in unique_keys}
        select = list(dict.fromkeys([*unique_keys, *row.keys()]))
        rows, _ = self.page(table, where=where, select=select, size=2)
        if len(rows) > 1:
            raise ContractError(f"Declared unique keys {tuple(unique_keys)} match multiple rows: {table}")
        return rows[0] if rows else None

    def upsert(self, table, row, *, unique_keys):
        """Read-modify-write via the standard model API: look up by unique_keys,
        data update when present, data create when absent. After any write the row
        is read back by unique_keys and every field of the caller's row is
        compared (unique keys, status/hash fields and business fields alike,
        with the same int/str value normalization as _same); a mismatch or an
        unconfirmable write raises ContractError('UPSERT_OUTCOME_UNKNOWN') —
        never a blind retry.
        Returns {'action': 'created'|'updated'|'unchanged', 'row': <read-back row>}.
        """
        if not unique_keys:
            raise ValueError("upsert requires at least one unique key")
        if not isinstance(row, dict):
            raise ValueError("upsert row must be a dict")
        missing = [key for key in unique_keys if key not in row]
        if missing:
            raise ValueError(f"upsert row misses unique keys: {missing}")
        tenant = self.config["tenant_id"]
        if "tenant_id" in row and str(row["tenant_id"]) != str(tenant):
            raise ValueError("upsert row tenant_id conflicts with the adapter tenant scope")
        code = self.dataset_code(table)  # fail closed on placeholders before any CLI call
        compared = list(dict.fromkeys([*unique_keys, *row.keys()]))

        def matches(candidate):
            return candidate is not None and all(self._same(candidate.get(key), row.get(key))
                                                 for key in compared)

        existing = self._find_unique(table, row, unique_keys)
        if existing is None:
            action, verb = "created", "create"
            payload = dict(row) if self._tenantless(table) else {**row, "tenant_id": tenant}
        else:
            changes = {key: value for key, value in row.items()
                       if key not in unique_keys and not self._same(existing.get(key), value)}
            if not changes:
                action, verb, payload = "unchanged", None, None
            else:
                action, verb = "updated", "update"
                pk = self._pk(table)
                payload = {pk: existing[pk], **changes}
                # Lovrabet CLI validates the generic `id` routing argument even
                # when dataset metadata declares a differently named primary key.
                # Keep that real key as well; never send a different row identity.
                if pk != 'id':
                    payload['id'] = existing[pk]
        if verb:
            try:
                self.invoke("data", verb, "--code", code,
                            "--params", json.dumps(payload, ensure_ascii=False,
                                                   default=_json_default))
            except ContractError as error:
                # Write outcome unknown: judge by read-back, never rewrite blindly.
                try:
                    landed = self._find_unique(table, row, unique_keys)
                except ContractError:
                    landed = None
                if matches(landed):
                    return {"action": action, "row": landed}
                raise ContractError(f"UPSERT_OUTCOME_UNKNOWN: {verb} on {table} could not "
                                    "be confirmed by read-back; resume and verify") from error
        readback = self._find_unique(table, row, unique_keys)
        if not matches(readback):
            raise ContractError(f"UPSERT_OUTCOME_UNKNOWN: read-back of {table} does not reflect "
                                f"the {action} row; resume and verify")
        return {"action": action, "row": readback}

    def reconcile_children(self, table, document_id, rows, before, *, measure=None):
        from .child_batch import reconcile
        return reconcile(self, table, document_id, rows, before, measure=measure)

    def batch_create(self, table, rows, *, unique_keys, preflight=True):
        """Create only absent rows in bounded batches, then verify one bulk read-back.

        Preflight and read-back use the declared unique keys. Any duplicate,
        pre-existing row or unconfirmed field value fails closed without a
        blind retry. Tenant-less child tables never receive a tenant column.
        """
        if not unique_keys:
            raise ValueError("batch_create requires at least one unique key")
        rows = list(rows)
        if not rows:
            return {"action": "created", "count": 0, "rows": []}

        def key(row):
            return tuple(str(row.get(field)) for field in unique_keys)

        if len({key(row) for row in rows}) != len(rows):
            raise ContractError(f"Duplicate batch keys for {table}")
        code = self.dataset_code(table)

        def readback():
            if len(unique_keys) == 1:
                field = unique_keys[0]
                where = {field: {"$in": [row[field] for row in rows]}}
            else:
                where = {"$or": [{field: {"$eq": row[field]} for field in unique_keys}
                                 for row in rows]}
            found = self.read_all(table, where=where)
            mapped = {}
            for row in found:
                identity = key(row)
                if identity in mapped:
                    raise ContractError(f"Declared unique keys {tuple(unique_keys)} "
                                        f"match multiple rows: {table}")
                mapped[identity] = row
            return mapped

        if preflight and readback():
            raise ContractError(f"BATCH_CREATE_CONFLICT: rows already exist in {table}")
        payloads = []
        for row in rows:
            if any(field not in row for field in unique_keys):
                raise ValueError("batch_create row misses unique keys")
            payloads.append(dict(row) if self._tenantless(table)
                            else {**row, "tenant_id": self.config["tenant_id"]})
        try:
            for offset in range(0, len(payloads), 200):
                self.invoke("data", "batchCreate", "--code", code,
                            "--params", json.dumps(payloads[offset:offset + 200],
                                                   ensure_ascii=False, default=_json_default))
        except ContractError as error:
            saved_after_error = readback()
            if not all(self._same(saved_after_error.get(key(row), {}).get(field), row.get(field))
                       for row in rows for field in row):
                raise ContractError(f"UPSERT_OUTCOME_UNKNOWN: batch create on {table} "
                                    "could not be confirmed by read-back; resume and verify") from error
        saved = readback()
        for row in rows:
            observed = saved.get(key(row))
            if observed is None or any(not self._same(observed.get(field), value)
                                       for field, value in row.items()):
                raise ContractError(f"UPSERT_OUTCOME_UNKNOWN: read-back of {table} "
                                    "does not reflect every batch row; resume and verify")
        return {"action": "created", "count": len(rows),
                "rows": [saved[key(row)] for row in rows]}

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
