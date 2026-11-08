"""P1.1: purchase source contract, scope validation and stream promotion gate."""

import json
import unittest
from pathlib import Path

from kemai_pipeline import acceptance
from kemai_pipeline.source import SourceError, validate_purchase_scope


class PurchaseScopeTests(unittest.TestCase):
    def scope(self, **patch):
        base = {"dateType": "0", "operDateFrom": "2026-11-07", "operDateTo": "2026-11-07",
                "keyword": "", "itemPackId": None, "status": ""}
        base.update(patch)
        return base

    def test_observed_scope_is_accepted(self):
        for stream in ("purchase-orders", "purchase-receipts", "purchase-returns"):
            validate_purchase_scope(stream, self.scope(), "2026-11-07")

    def test_approve_time_window_is_a_different_contract(self):
        with self.assertRaisesRegex(SourceError, "create time"):
            validate_purchase_scope("purchase-orders", self.scope(dateType="1"), "2026-11-07")

    def test_hidden_filters_must_stay_empty(self):
        for patch in ({"keyword": "PO123"}, {"status": "2"}, {"itemPackId": "42"},
                      {"unknownFilter": "active"}):
            with self.assertRaises(SourceError):
                validate_purchase_scope("purchase-orders", self.scope(**patch), "2026-11-07")

    def test_window_fields_must_match_the_day(self):
        with self.assertRaises(SourceError):
            validate_purchase_scope("purchase-orders",
                                    self.scope(operDateTo="2026-11-08"), "2026-11-07")


class PurchasePageCaptureTests(unittest.TestCase):
    def _proc(self, scope):
        envelope = [{"schema_version": 1, "stream": "purchase-orders", "from": "2026-11-07",
                     "to": "2026-11-07", "page": 1, "page_size": 2, "total_rows": None,
                     "observed_at": "2026-11-08T10:00:00Z", "query_scope": scope,
                     "rows": [{"id": "PO1", "gmtCreate": "2026-11-07 10:00:00"}]}]
        return type("Proc", (), {"returncode": 0, "stdout": json.dumps(envelope), "stderr": ""})()

    def test_create_time_page_is_accepted(self):
        from unittest.mock import Mock
        from kemai_pipeline.source import OpenCLISource
        scope = {"dateType": "0", "operDateFrom": "2026-11-07", "operDateTo": "2026-11-07",
                 "keyword": "", "itemPackId": None, "status": ""}
        source = OpenCLISource(executor=Mock(return_value=self._proc(scope)), max_operations=1)
        pages = list(source.pages("purchase-orders", "2026-11-07", page_size=2))
        self.assertEqual(len(pages), 1)

    def test_approve_time_page_is_rejected_at_the_source_port(self):
        from unittest.mock import Mock
        from kemai_pipeline.source import OpenCLISource
        scope = {"dateType": "1", "operDateFrom": "2026-11-07", "operDateTo": "2026-11-07",
                 "keyword": "", "itemPackId": None, "status": ""}
        source = OpenCLISource(executor=Mock(return_value=self._proc(scope)), max_operations=1)
        with self.assertRaisesRegex(SourceError, "create time"):
            list(source.pages("purchase-orders", "2026-11-07", page_size=2))


class ContractFactsTests(unittest.TestCase):
    def test_contract_records_unreliable_total_and_late_modification(self):
        contract = json.loads((Path("kemai_pipeline") / "source_contracts.json").read_text())
        purchase = contract["purchase_scope"]
        self.assertFalse(purchase["total_record_reliable"])
        self.assertEqual(purchase["create_time_value"], "0")
        self.assertEqual(purchase["approve_time_value"], "1")
        self.assertIn("late_modification_policy", purchase["feedback"])


class AcceptanceTests(unittest.TestCase):
    def test_only_evidenced_streams_may_write(self):
        self.assertEqual(acceptance.accepted_streams(), ["retail-orders", "retail-sales"])
        for stream in ("purchase-orders", "purchase-receipts", "purchase-returns",
                       "purchase-receive-history"):
            self.assertIsNone(acceptance.acceptance(stream))
            with self.assertRaisesRegex(ValueError, "STREAM_ACCEPTANCE_REQUIRED"):
                acceptance.require(stream)

    def test_promotion_is_a_registry_change(self):
        record = acceptance.require("retail-sales")
        self.assertIn("evidence", record)
        self.assertEqual(record["coverage"], "list_only")


if __name__ == "__main__":
    unittest.main()
