"""Offline invariants for destructive detail writes and historical checkpoints."""

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import kemai_commerce_reconcile as reconcile
import kemai_commerce_sync as commerce
import kemai_purchase_detail_backfill as backfill
import kemai_retail_daily_audit as retail_audit


class DetailWriteSafetyTests(unittest.TestCase):
    def test_purchase_missing_array_cannot_write_header_or_delete_children(self):
        entity = {"id": "42", "branchs": [], "approves": []}
        with patch.object(commerce, "purchase_header") as header, patch.object(commerce, "sql") as write:
            with self.assertRaisesRegex(ValueError, "details"):
                commerce.purchase(object(), "order", entity)
        header.assert_not_called()
        write.assert_not_called()

    def test_purchase_valid_empty_arrays_record_complete_zero_rows(self):
        entity = {"id": "42", "details": [], "branchs": [], "approves": []}
        conn = MagicMock()
        with patch.object(commerce, "purchase_header"), patch.object(commerce, "one", return_value={"id": 7}), \
             patch.object(commerce, "sql") as write:
            commerce.purchase(conn, "order", entity)
        statements = [call.args[1] for call in write.call_args_list]
        self.assertEqual(sum("DELETE FROM purchase_document_" in s for s in statements), 3)
        self.assertIn("purchase_document_detail_state", statements[-1])
        self.assertEqual(write.call_args_list[-1].args[2][1:5], ("success", 0, 0, 0))
        conn.commit.assert_not_called()

    def test_missing_ancillary_array_preserves_existing_children_and_marks_partial(self):
        cases = (("order", "approves", "purchase_document_approvals"),
                 ("receipt", "branchs", "purchase_document_branches"),
                 ("return", "branchs", "purchase_document_branches"))
        for kind, missing_field, protected_table in cases:
            with self.subTest(kind=kind):
                entity = {"id": "42", "details": [], "branchs": [], "approves": []}
                del entity[missing_field]
                with patch.object(commerce, "purchase_header"), \
                     patch.object(commerce, "one", return_value={"id": 7}), \
                     patch.object(commerce, "sql") as write:
                    missing = commerce.purchase(object(), kind, entity)
                self.assertEqual(missing, (missing_field,))
                statements = [call.args[1] for call in write.call_args_list]
                self.assertFalse(any(f"DELETE FROM {protected_table}" in s for s in statements))
                params = write.call_args_list[-1].args[2]
                self.assertEqual(params[1], "partial")
                self.assertIsNone(params[3 if missing_field == "branchs" else 4])

    def test_malformed_ancillary_array_fails_before_any_write(self):
        entity = {"id": "42", "details": [], "branchs": "broken"}
        with patch.object(commerce, "purchase_header") as header, patch.object(commerce, "sql") as write:
            with self.assertRaisesRegex(ValueError, "branchs"):
                commerce.purchase(object(), "receipt", entity)
        header.assert_not_called()
        write.assert_not_called()

    def test_incremental_purchase_omitted_ancillary_array_records_day_gap(self):
        row = {"id": "42"}
        conn = MagicMock()
        coverage = {}
        with patch.object(commerce, "scan_day", return_value=({"42": row}, 1)), \
             patch.object(commerce, "call", return_value=[{"id": "42", "details": []}]), \
             patch.object(commerce, "purchase", return_value=("branchs",)), \
             patch.object(commerce, "capture"), \
             patch.object(commerce, "ensure_connection_after_fetch"), \
             patch.object(commerce, "pause"), \
             patch.object(commerce, "captured_key_count", return_value=1), \
             patch.object(commerce, "record_gap") as gap:
            saved = commerce.sync_day(conn, "purchase-receipts", date(2026, 11, 7),
                                      "incremental", 0.5, 2, 0, coverage_info=coverage)
        self.assertEqual(saved, 1)
        self.assertEqual(coverage["partial_documents"], 1)
        self.assertEqual(gap.call_args.args[3], ["branch_allocations"])
        self.assertEqual(commerce.window_coverage("purchase-receipts", "incremental",
                                                   partial_count=1)[0], "partial_detail")

    def test_incremental_day_does_not_complete_after_partial_purchase_detail(self):
        source_row = {"id": "42"}
        incomplete_detail = {"id": "42", "branchs": [], "approves": []}
        conn = MagicMock()
        with patch.object(commerce, "scan_day", return_value=({"42": source_row}, 1)), \
             patch.object(commerce, "call", return_value=[incomplete_detail]), \
             patch.object(commerce, "ensure_connection_after_fetch"), \
             patch.object(commerce, "pause"), \
             patch.object(commerce, "purchase_header") as header:
            with self.assertRaisesRegex(ValueError, "details"):
                commerce.sync_day(conn, "purchase-orders", date(2026, 11, 7),
                                  "incremental", 0.5, 2, 0)
        header.assert_not_called()
        conn.commit.assert_not_called()

    def test_retail_missing_payment_array_preserves_all_existing_rows(self):
        with patch.object(commerce, "retail_order_header") as header, patch.object(commerce, "sql") as write:
            with self.assertRaisesRegex(ValueError, "payFlows"):
                commerce.retail_order(object(), {"id": "8", "orderDetails": []})
        header.assert_not_called()
        write.assert_not_called()

    def test_retail_refresh_keeps_flow_fallback_and_removes_stale_detail_only_rows(self):
        entity = {"id": "8", "orderDetails": [], "payFlows": []}
        with patch.object(commerce, "retail_order_header"), patch.object(commerce, "one", return_value={"id": 5}), \
             patch.object(commerce, "sql") as write:
            commerce.retail_order(object(), entity)
        statements = [call.args[1] for call in write.call_args_list]
        self.assertIn("JOIN retail_sale_flows", statements[0])
        self.assertIn("source_kind='sales_flow'", statements[0])
        self.assertIn("source_kind='detail_api'", statements[1])
        self.assertIn("DELETE FROM retail_payments", statements[2])


class CheckpointSafetyTests(unittest.TestCase):
    def test_explicit_incremental_old_date_is_rejected(self):
        with patch.object(commerce, "one", return_value={"cursor_json": '{"last_complete_day":"2026-11-06"}'}):
            with self.assertRaisesRegex(ValueError, "independent reconcile"):
                commerce.assert_forward_incremental(object(), ("purchase-orders",), date(2026, 11, 6))
            commerce.assert_forward_incremental(object(), ("purchase-orders",), date(2026, 11, 7))

    def test_reconcile_checkpoint_never_moves_back_on_restart(self):
        with patch.object(reconcile, "checkpoint_day", return_value=date(2026, 11, 6)), \
             patch.object(reconcile, "sql") as write:
            reconcile.save_checkpoint(object(), "test", date(2026, 11, 4))
        self.assertIn("2026-11-06", write.call_args.args[2][2])

    def test_reconcile_flags_absence_without_deleting_business_data(self):
        old = {"a": {"source_version": "1"}, "b": {"source_version": "1"}}
        diff, missing = reconcile.compare(old, {"a": {"version": "2"}}, "purchase-orders")
        self.assertEqual((diff["changed"], diff["absent"], missing), (1, 1, {"b"}))

    def test_backfill_retries_stale_version_even_after_attempt_cap(self):
        row = {"status": "success", "attempts": 3, "source_version": "2",
               "detail_version": "1", "attempted_source_version": "1"}
        self.assertTrue(backfill.eligible(row))
        row.update(status="failed", source_version="2", attempted_source_version="2")
        self.assertFalse(backfill.eligible(row))
        self.assertTrue(backfill.eligible(row, retry_failed=True))

    def test_partial_source_arrays_are_not_retried_without_explicit_request(self):
        row = {"status": "partial", "attempts": 1, "source_version": "1",
               "detail_version": "1", "attempted_source_version": "1"}
        self.assertFalse(backfill.eligible(row))
        self.assertTrue(backfill.eligible(row, retry_failed=True))
        row["source_version"] = "2"
        self.assertTrue(backfill.eligible(row))

    def test_backfill_plan_only_reads_local_database(self):
        row = {"status": None, "attempts": 0, "source_version": "1",
               "detail_version": None, "attempted_source_version": None}
        with patch.object(backfill, "state_table_exists", return_value=True), \
             patch.object(backfill, "rows_for_day", return_value=[row]), \
             patch.object(backfill, "one", return_value={"n": 0}), \
             patch.object(backfill, "call") as source_call:
            result = backfill.plan(object(), date(2026, 11, 1), date(2026, 11, 1), ("order",))
        source_call.assert_not_called()
        self.assertEqual(result["kinds"]["order"]["pending"], 1)
        self.assertEqual(result["source_requests"], 0)

    def test_audit_reads_controlled_collection_logs(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "kemai_followup_purchase-orders.log").write_text("old")
            nested = root / "kemai_collection_logs"
            nested.mkdir()
            (nested / "commerce:purchase-orders.log").write_text("new")
            with patch.object(retail_audit, "ROOT", root):
                paths = retail_audit.source_log_paths("purchase-orders")
        self.assertEqual(len(paths), 2)


if __name__ == "__main__":
    unittest.main()
