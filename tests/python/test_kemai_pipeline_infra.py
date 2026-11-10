"""B1 infrastructure: adapter upsert, source.document_detail, dataset placeholders."""

import json
import subprocess
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from unittest.mock import Mock

from kemai_pipeline.lovrabet_adapter import LovrabetAdapter, ContractError
from kemai_pipeline.source import OpenCLISource, SourceError


CONFIG = {"appcode": "app-test", "tenant_id": "t", "datasets": {"docs": "code-1"}}

ROW = {"document_id": "PO-1", "status": "success", "payload_hash": "abc123",
       "last_error": None, "attempts": 1}


def cli_ok(result):
    return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "data": {"result": result}}),
                           stderr="")


def page(rows, size=2):
    return {"tableData": rows, "paging": {"currentPage": 1, "pageSize": size,
                                          "totalCount": len(rows)}}


def stored(**overrides):
    row = {"id": 7, "tenant_id": "t", **ROW, **overrides}
    return row


def source_process(rows):
    return SimpleNamespace(returncode=0, stdout=json.dumps(rows), stderr="")


def commands(executor):
    return [call.args[0][1:3] for call in executor.call_args_list]


class UpsertTests(unittest.TestCase):
    def adapter(self, responses):
        executor = Mock(side_effect=responses)
        return LovrabetAdapter(dict(CONFIG), executor=executor), executor

    def test_upsert_creates_when_absent(self):
        client, executor = self.adapter([cli_ok(page([])), cli_ok({"id": 7}),
                                         cli_ok(page([stored()]))])
        result = client.upsert("docs", dict(ROW), unique_keys=("document_id",))
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["row"]["document_id"], "PO-1")
        self.assertEqual(commands(executor), [["data", "filter"], ["data", "create"], ["data", "filter"]])
        create_args = executor.call_args_list[1].args[0]
        payload = json.loads(create_args[create_args.index("--params") + 1])
        self.assertEqual(payload["tenant_id"], "t")
        self.assertEqual(payload["document_id"], "PO-1")
        self.assertEqual(payload["payload_hash"], "abc123")

    def test_upsert_updates_when_present(self):
        client, executor = self.adapter([cli_ok(page([stored(status="partial", payload_hash="old")])),
                                         cli_ok({"updated": 1}),
                                         cli_ok(page([stored()]))])
        result = client.upsert("docs", dict(ROW), unique_keys=("document_id",))
        self.assertEqual(result["action"], "updated")
        self.assertEqual(commands(executor), [["data", "filter"], ["data", "update"], ["data", "filter"]])
        update_args = executor.call_args_list[1].args[0]
        payload = json.loads(update_args[update_args.index("--params") + 1])
        self.assertEqual(payload["id"], 7)
        self.assertNotIn("document_id", payload)  # unique keys never rewritten
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["payload_hash"], "abc123")
        self.assertNotIn("attempts", payload)  # unchanged fields are not resent

    def test_upsert_unchanged_when_readback_matches(self):
        client, executor = self.adapter([cli_ok(page([stored()])), cli_ok(page([stored()]))])
        result = client.upsert("docs", dict(ROW), unique_keys=("document_id",))
        self.assertEqual(result["action"], "unchanged")
        self.assertEqual(result["row"]["id"], 7)
        self.assertEqual(commands(executor), [["data", "filter"], ["data", "filter"]])

    def test_upsert_unknown_outcome_raises_without_blind_retry(self):
        # Write acknowledged, but the read-back does not reflect it: outcome unknown.
        client, executor = self.adapter([cli_ok(page([])), cli_ok({"id": 7}),
                                         cli_ok(page([stored(payload_hash="different")]))])
        with self.assertRaisesRegex(ContractError, "UPSERT_OUTCOME_UNKNOWN"):
            client.upsert("docs", dict(ROW), unique_keys=("document_id",))
        self.assertEqual(commands(executor), [["data", "filter"], ["data", "create"], ["data", "filter"]])

    def test_upsert_write_transport_error_readback_decides(self):
        # Transport failure after a create: judge by read-back, never blindly rewrite.
        client, executor = self.adapter([cli_ok(page([])),
                                         subprocess.TimeoutExpired("lovrabet", 90),
                                         cli_ok(page([stored()]))])
        result = client.upsert("docs", dict(ROW), unique_keys=("document_id",))
        self.assertEqual(result["action"], "created")
        self.assertEqual(commands(executor), [["data", "filter"], ["data", "create"], ["data", "filter"]])
        # Same transport failure, but read-back shows nothing landed.
        client, executor = self.adapter([cli_ok(page([])),
                                         subprocess.TimeoutExpired("lovrabet", 90),
                                         cli_ok(page([]))])
        with self.assertRaisesRegex(ContractError, "UPSERT_OUTCOME_UNKNOWN"):
            client.upsert("docs", dict(ROW), unique_keys=("document_id",))
        self.assertEqual(commands(executor), [["data", "filter"], ["data", "create"], ["data", "filter"]])

    def test_upsert_requires_unique_keys_and_tenant_predicate(self):
        client, executor = self.adapter([])
        with self.assertRaises(ValueError):
            client.upsert("docs", dict(ROW), unique_keys=())
        with self.assertRaises(ValueError):
            client.upsert("docs", {"status": "success"}, unique_keys=("document_id",))
        with self.assertRaises(ValueError):
            client.upsert("docs", {**ROW, "tenant_id": "other"}, unique_keys=("document_id",))
        executor.assert_not_called()
        client, executor = self.adapter([cli_ok(page([stored()])), cli_ok(page([stored()]))])
        client.upsert("docs", dict(ROW), unique_keys=("document_id",))
        filter_args = executor.call_args_list[0].args[0]
        params = json.loads(filter_args[filter_args.index("--params") + 1])
        self.assertEqual(params["where"]["$and"][0], {"tenant_id": {"$eq": "t"}})
        self.assertEqual(params["where"]["$and"][1], {"document_id": {"$eq": "PO-1"}})

    def test_upsert_duplicate_unique_match_rejected(self):
        client, executor = self.adapter([cli_ok(page([stored(), stored(id=8)]))])
        with self.assertRaisesRegex(ContractError, "unique"):
            client.upsert("docs", dict(ROW), unique_keys=("document_id",))
        self.assertEqual(commands(executor), [["data", "filter"]])

    def test_upsert_readback_compares_every_business_field(self):
        # F3: read-back comparison covers all caller fields, not just
        # unique/status/hash keys. A tampered quantity must fail closed.
        row = {**ROW, "quantity": 100, "amount": "12.5"}
        client, executor = self.adapter([cli_ok(page([])), cli_ok({"id": 7}),
                                         cli_ok(page([stored(quantity=999, amount="12.5")]))])
        with self.assertRaisesRegex(ContractError, "UPSERT_OUTCOME_UNKNOWN"):
            client.upsert("docs", dict(row), unique_keys=("document_id",))
        self.assertEqual(commands(executor), [["data", "filter"], ["data", "create"], ["data", "filter"]])
        # Identical content with int/str representation differences still matches.
        client, _ = self.adapter([cli_ok(page([])), cli_ok({"id": 7}),
                                  cli_ok(page([{**stored(), "quantity": "100", "amount": "12.5"}]))])
        result = client.upsert("docs", dict(row), unique_keys=("document_id",))
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["row"]["document_id"], "PO-1")

    def test_upsert_serializes_decimal_amounts_without_losing_precision(self):
        row = {**ROW, "quantity": Decimal("1.2345"), "unit_price": Decimal("0"),
               "amount": Decimal("12.50")}
        client, executor = self.adapter([cli_ok(page([])), cli_ok({"id": 7}),
                                         cli_ok(page([stored(quantity="1.2345", unit_price="0",
                                                             amount="12.50")]))])
        result = client.upsert("docs", row, unique_keys=("document_id",))
        self.assertEqual(result["action"], "created")
        create_args = executor.call_args_list[1].args[0]
        payload = json.loads(create_args[create_args.index("--params") + 1])
        self.assertEqual(payload["quantity"], "1.2345")
        self.assertEqual(payload["unit_price"], "0")
        self.assertEqual(payload["amount"], "12.50")

    def test_tenantless_dataset_upsert_does_not_inject_tenant_column(self):
        config = {**CONFIG, "datasets": {**CONFIG["datasets"], "lines": "code-2"},
                  "tenantless_datasets": ["lines"]}
        executor = Mock(side_effect=[cli_ok(page([])), cli_ok({"id": 7}),
                                     cli_ok(page([{"id": 7, **ROW}]))])
        client = LovrabetAdapter(config, executor=executor)
        result = client.upsert("lines", dict(ROW), unique_keys=("document_id",))
        self.assertEqual(result["action"], "created")
        filter_args = executor.call_args_list[0].args[0]
        where = json.loads(filter_args[filter_args.index("--params") + 1])["where"]
        self.assertEqual(where, {"document_id": {"$eq": "PO-1"}})
        create_args = executor.call_args_list[1].args[0]
        payload = json.loads(create_args[create_args.index("--params") + 1])
        self.assertNotIn("tenant_id", payload)

    def test_tenantless_dataset_count_does_not_inject_tenant_column(self):
        config = {**CONFIG, "datasets": {**CONFIG["datasets"], "lines": "code-2"},
                  "tenantless_datasets": ["lines"]}
        executor = Mock(return_value=cli_ok({"tableData": [{"row_count": 3}]}))
        client = LovrabetAdapter(config, executor=executor)
        self.assertEqual(client.count("lines", where={}), 3)
        args = executor.call_args.args[0]
        where = json.loads(args[args.index("--params") + 1])["where"]
        self.assertEqual(where, {})

    def test_upsert_readback_compares_json_payload_by_content_not_formatting(self):
        row = {**ROW, 'payload': '{"a":1,"b":[2,3]}'}
        client, _ = self.adapter([cli_ok(page([])), cli_ok({'id': 7}),
                                  cli_ok(page([stored(payload='{\n  "b": [2, 3],\n  "a": 1\n}')]))])
        result = client.upsert('docs', row, unique_keys=('document_id',))
        self.assertEqual(result['action'], 'created')
        client, _ = self.adapter([cli_ok(page([])), cli_ok({'id': 7}),
                                  cli_ok(page([stored(payload='{"a":2,"b":[2,3]}')]))])
        with self.assertRaisesRegex(ContractError, 'UPSERT_OUTCOME_UNKNOWN'):
            client.upsert('docs', row, unique_keys=('document_id',))

    def test_upsert_readback_accepts_equivalent_epoch_and_iso_datetimes(self):
        attempted = '2026-11-09T17:49:21.123456+08:00'
        approved = '2026-11-08 10:00:00'
        attempted_ms = int(datetime.fromisoformat(attempted).timestamp() * 1000)
        attempted_ms = ((attempted_ms + 500) // 1000) * 1000  # DATETIME(0) rounds to seconds
        approved_ms = int(datetime(2026, 11, 8, 10,
                                   tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() * 1000)
        row = {**ROW, 'attempted_at': attempted, 'approved_at': approved}
        client, _ = self.adapter([cli_ok(page([])), cli_ok({'id': 7}),
                                  cli_ok(page([stored(attempted_at=attempted_ms,
                                                      approved_at=approved_ms)]))])
        result = client.upsert('docs', row, unique_keys=('document_id',))
        self.assertEqual(result['action'], 'created')

    def test_upsert_readback_missing_business_field_fails_closed(self):
        # A read-back row that silently dropped a caller field is a mismatch.
        row = {**ROW, "quantity": 100}
        client, _ = self.adapter([cli_ok(page([])), cli_ok({"id": 7}),
                                  cli_ok(page([stored()]))])  # no quantity key at all
        with self.assertRaisesRegex(ContractError, "UPSERT_OUTCOME_UNKNOWN"):
            client.upsert("docs", dict(row), unique_keys=("document_id",))


class BatchCreateTests(unittest.TestCase):
    def test_batch_create_writes_once_and_verifies_all_rows_in_one_readback(self):
        rows = [dict(ROW), {**ROW, 'document_id': 'PO-2', 'quantity': Decimal('1.20')}]
        executor = Mock(side_effect=[cli_ok(page([], size=100)), cli_ok({'ids': [7, 8]}),
                                     cli_ok(page([stored(), stored(id=8, document_id='PO-2',
                                                                   quantity='1.20')], size=100))])
        client = LovrabetAdapter(dict(CONFIG), executor=executor)
        result = client.batch_create('docs', rows, unique_keys=('document_id',))
        self.assertEqual(result['count'], 2)
        self.assertEqual(commands(executor), [['data', 'filter'], ['data', 'batchCreate'],
                                              ['data', 'filter']])
        create_args = executor.call_args_list[1].args[0]
        payload = json.loads(create_args[create_args.index('--params') + 1])
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]['tenant_id'], 't')
        self.assertEqual(payload[1]['quantity'], '1.20')

    def test_batch_create_can_reuse_caller_preflight_read(self):
        executor = Mock(side_effect=[cli_ok({'ids': [7]}), cli_ok(page([stored()], size=100))])
        client = LovrabetAdapter(dict(CONFIG), executor=executor)
        result = client.batch_create('docs', [dict(ROW)], unique_keys=('document_id',),
                                     preflight=False)
        self.assertEqual(result['count'], 1)
        self.assertEqual(commands(executor), [['data', 'batchCreate'], ['data', 'filter']])

    def test_batch_create_tenantless_and_unconfirmed_outcome_fails_without_retry(self):
        config = {**CONFIG, 'datasets': {'lines': 'code-2'}, 'tenantless_datasets': ['lines']}
        executor = Mock(side_effect=[cli_ok(page([], size=100)), cli_ok({'ids': [7]}),
                                     cli_ok(page([], size=100))])
        client = LovrabetAdapter(config, executor=executor)
        with self.assertRaisesRegex(ContractError, 'UPSERT_OUTCOME_UNKNOWN'):
            client.batch_create('lines', [dict(ROW)], unique_keys=('document_id',))
        self.assertEqual(commands(executor), [['data', 'filter'], ['data', 'batchCreate'],
                                              ['data', 'filter']])
        create_args = executor.call_args_list[1].args[0]
        payload = json.loads(create_args[create_args.index('--params') + 1])
        self.assertNotIn('tenant_id', payload[0])


class PlaceholderDatasetTests(unittest.TestCase):
    def test_unregistered_placeholder_dataset_fails_closed_before_any_cli_call(self):
        executor = Mock()
        # Placeholders no longer live in the real datasets.json (datasets are
        # registered now); simulate the pre-registration config explicitly.
        config = {"appcode": "app-test", "tenant_id": "t", "datasets": {
            table: "PENDING_DATASET_REGISTRATION"
            for table in ("purchase_document_detail_state", "purchase_document_lines",
                          "purchase_document_branches", "purchase_document_approvals")}}
        client = LovrabetAdapter(config, executor=executor)
        for table in ("purchase_document_detail_state", "purchase_document_lines",
                      "purchase_document_branches", "purchase_document_approvals"):
            with self.assertRaisesRegex(ContractError, table):
                client.page(table)
            with self.assertRaisesRegex(ContractError, "待数据集注册"):
                client.upsert(table, dict(ROW), unique_keys=("document_id",))
        executor.assert_not_called()
        self.assertEqual(client.requests, 0)
        with self.assertRaisesRegex(ContractError, "No verified dataset mapping"):
            client.read_all("never_registered_table")
        executor.assert_not_called()


class DocumentDetailTests(unittest.TestCase):
    def test_document_detail_returns_single_entity(self):
        executor = Mock(return_value=source_process([{"id": 42, "details": [], "version": 3}]))
        source = OpenCLISource(executor=executor)
        entity = source.document_detail("order", 42)
        self.assertEqual(entity["id"], 42)
        args = executor.call_args.args[0]
        self.assertEqual(args[:2], ["opencli", "kemai"])
        self.assertEqual(args[2], "purchase-detail")
        self.assertEqual(args[args.index("--kind") + 1], "order")
        self.assertEqual(args[args.index("--id") + 1], "42")

    def test_document_detail_identity_reccheck(self):
        executor = Mock(return_value=source_process([{"id": 43, "details": []}]))
        source = OpenCLISource(executor=executor)
        with self.assertRaisesRegex(SourceError, "identity"):
            source.document_detail("receipt", "42")
        executor = Mock(return_value=source_process([{"id": 42}, {"id": 42}]))
        source = OpenCLISource(executor=executor)
        with self.assertRaises(SourceError):
            source.document_detail("return", "42")

    def test_document_detail_rejects_invalid_kind(self):
        executor = Mock()
        source = OpenCLISource(executor=executor)
        for bad in ("invoice", "", None):
            with self.assertRaises(ValueError):
                source.document_detail(bad, "42")
        for bad_id in ("", "  ", None, True):
            with self.assertRaises(ValueError):
                source.document_detail("order", bad_id)
        executor.assert_not_called()
        self.assertEqual(source.operations, 0)

    def test_document_detail_counts_against_source_budget(self):
        executor = Mock(return_value=source_process([{"id": 42}]))
        source = OpenCLISource(executor=executor, max_operations=1)
        source.document_detail("order", "42")
        self.assertEqual(source.operations, 1)
        with self.assertRaisesRegex(SourceError, "budget"):
            source.document_detail("order", "42")
        self.assertEqual(executor.call_count, 1)


if __name__ == "__main__":
    unittest.main()
