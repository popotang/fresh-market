"""Pure purchase detail child-row normalization tests (B1 pure layer)."""
import json
import unittest
from decimal import Decimal

from kemai_pipeline.normalize_detail import (normalize_purchase_approvals,
                                             normalize_purchase_branches,
                                             normalize_purchase_lines)


def entity(**overrides):
    base = {"id": "1001", "version": "7", "details": [], "branchs": [], "approves": []}
    base.update(overrides)
    return base


class LinesTest(unittest.TestCase):
    def test_ordinal_fallback_key(self):
        rows = normalize_purchase_lines(9, entity(details=[{"qty": "1"}, {"qty": "2"}]))
        self.assertEqual([row["source_line_id"] for row in rows],
                         ["ordinal:0", "ordinal:1"])
        self.assertTrue(all(row["document_id"] == 9 for row in rows))

    def test_source_id_preferred_over_ordinal(self):
        rows = normalize_purchase_lines(9, entity(details=[{"id": 555, "qty": "1"}]))
        self.assertEqual(rows[0]["source_line_id"], "555")

    def test_money_scaling_matches_legacy(self):
        rows = normalize_purchase_lines(9, entity(details=[
            {"id": "1", "qty": "250000", "price": "123456", "amt": "10000",
             "taxAmt": "500", "itemId": 77, "sourceDetailId": "ref-1"}]))
        row = rows[0]
        self.assertEqual(row["quantity"], Decimal("25"))
        self.assertEqual(row["unit_price"], Decimal("12.3456"))
        self.assertEqual(row["amount"], Decimal("1"))
        self.assertEqual(row["tax_amount"], Decimal("0.05"))
        self.assertEqual(row["source_item_id"], "77")
        self.assertEqual(row["source_line_ref_id"], "ref-1")

    def test_empty_and_invalid_money_become_none(self):
        rows = normalize_purchase_lines(9, entity(details=[
            {"id": "1", "qty": "", "price": None, "amt": "abc", "taxAmt": None}]))
        row = rows[0]
        self.assertIsNone(row["quantity"])
        self.assertIsNone(row["unit_price"])
        self.assertIsNone(row["amount"])
        self.assertIsNone(row["tax_amount"])

    def test_falsy_price_falls_back_to_purchase_price(self):
        # Legacy `money(row,"price") or money(row,"purchasePrice")`: Decimal 0 is falsy.
        rows = normalize_purchase_lines(9, entity(details=[
            {"id": "1", "price": 0, "purchasePrice": "20000"}]))
        self.assertEqual(rows[0]["unit_price"], Decimal("2"))

    def test_amount_falls_back_to_amount_key(self):
        rows = normalize_purchase_lines(9, entity(details=[
            {"id": "1", "amt": None, "amount": "30000"}]))
        self.assertEqual(rows[0]["amount"], Decimal("3"))

    def test_missing_details_array_raises(self):
        with self.assertRaises(ValueError):
            normalize_purchase_lines(9, {"id": "1001"})
        with self.assertRaises(ValueError):
            normalize_purchase_lines(9, entity(details=[{"id": "1"}, "junk"]))

    def test_output_keys_stable(self):
        rows = normalize_purchase_lines(9, entity(details=[{"id": "1"}]))
        self.assertEqual(list(rows[0]),
                         ["document_id", "source_line_id", "source_item_id",
                          "source_line_ref_id", "quantity", "unit_price", "amount",
                          "tax_amount", "payload"])


class BranchesTest(unittest.TestCase):
    def test_ordinal_and_branch_id_fallback(self):
        rows = normalize_purchase_branches(9, entity(branchs=[
            {"branchId": "b1"}, {"id": "x2", "branchId": "b2"}, {}]))
        self.assertEqual([row["source_key"] for row in rows], ["b1", "x2", "ordinal:2"])
        self.assertEqual(rows[0]["source_branch_id"], "b1")
        self.assertIsNone(rows[2]["source_branch_id"])

    def test_absent_array_yields_no_rows(self):
        self.assertEqual(normalize_purchase_branches(9, entity(branchs=None)), [])
        self.assertEqual(normalize_purchase_branches(9, entity()), [])


class ApprovalsTest(unittest.TestCase):
    def test_ordinal_fallback_and_fields(self):
        rows = normalize_purchase_approvals(9, entity(approves=[
            {"approverId": 88, "status": 2, "approveTime": "2026-01-02 03:04:05.123"},
            {"id": "a9", "approverId": "u9", "status": "1",
             "approveTime": "2026-01-03 04:05:06"}]))
        self.assertEqual(rows[0]["source_key"], "ordinal:0")
        self.assertEqual(rows[0]["source_approver_id"], "88")
        self.assertEqual(rows[0]["status_code"], "2")
        self.assertEqual(rows[0]["approved_at"], "2026-01-02 03:04:05")
        self.assertEqual(rows[1]["source_key"], "a9")
        self.assertEqual(rows[1]["approved_at"], "2026-01-03 04:05:06")
        self.assertTrue(all(row["document_id"] == 9 for row in rows))

    def test_absent_array_yields_no_rows(self):
        self.assertEqual(normalize_purchase_approvals(9, entity(approves=None)), [])


class DeterminismTest(unittest.TestCase):
    def test_same_input_byte_identical_output(self):
        source = entity(
            details=[{"id": "1", "qty": "250000", "price": "10000", "amt": "10000",
                      "taxAmt": "0", "itemId": "5", "sourceDetailId": "r1"},
                     {"qty": "1", "amount": "20000"}],
            branchs=[{"branchId": "b1"}, {"id": "x", "branchId": "b2"}],
            approves=[{"id": "a1", "approverId": "u", "status": "2",
                       "approveTime": "2026-01-02 03:04:05"}])
        for normalize in (normalize_purchase_lines, normalize_purchase_branches,
                          normalize_purchase_approvals):
            first = normalize(9, source)
            second = normalize(9, source)
            self.assertEqual(first, second)
            self.assertEqual(json.dumps(first, sort_keys=True, default=str),
                             json.dumps(second, sort_keys=True, default=str))

    def test_payload_is_canonical_json(self):
        rows = normalize_purchase_lines(9, entity(details=[{"b": 1, "a": "中"}]))
        self.assertEqual(rows[0]["payload"], '{"a":"中","b":1}')


if __name__ == "__main__":
    unittest.main()
