"""Pure purchase detail state machine tests (B1 pure layer)."""
import unittest

from kemai_pipeline.detail_state import (MAX_ATTEMPTS, REQUIRED_ARRAYS, DetailVerdict,
                                         classify_entity, eligible, needs_detail,
                                         next_state_row)

NOW = "2026-11-09 12:00:00"


def entity(**overrides):
    base = {"id": "1001", "version": "7", "details": [], "branchs": [], "approves": []}
    base.update(overrides)
    return base


class ClassifyEntityTest(unittest.TestCase):
    def test_missing_branchs_array_is_partial(self):
        verdict = classify_entity(entity(branchs=None))
        self.assertIsInstance(verdict, DetailVerdict)
        self.assertFalse(verdict.complete)
        self.assertIn("branch_allocations", verdict.missing)
        self.assertEqual(verdict.counts["lines"], 0)
        self.assertIsNone(verdict.counts["branches"])
        self.assertEqual(verdict.counts["approvals"], 0)

    def test_all_empty_arrays_complete_with_zero_counts(self):
        verdict = classify_entity(entity())
        self.assertTrue(verdict.complete)
        self.assertEqual(verdict.missing, ())
        self.assertEqual(verdict.counts, {"lines": 0, "branches": 0, "approvals": 0})

    def test_constants(self):
        self.assertEqual(MAX_ATTEMPTS, 3)
        self.assertEqual(REQUIRED_ARRAYS, ("details", "branchs", "approves"))

    def test_payload_hash_and_source_version(self):
        verdict = classify_entity(entity())
        self.assertEqual(len(verdict.payload_hash), 64)
        self.assertEqual(verdict.payload_hash,
                         classify_entity(entity()).payload_hash)
        self.assertEqual(verdict.source_version, "7")
        self.assertIsNone(classify_entity(entity(version=None)).source_version)

    def test_counts_reflect_row_numbers(self):
        verdict = classify_entity(entity(details=[{"id": "1"}, {"id": "2"}],
                                         approves=[{"id": "a"}]))
        self.assertEqual(verdict.counts, {"lines": 2, "branches": 0, "approvals": 1})
        self.assertTrue(verdict.complete)

    def test_invalid_array_raises(self):
        with self.assertRaises(ValueError):
            classify_entity(entity(branchs="not-a-list"))
        with self.assertRaises(ValueError):
            classify_entity(entity(details=[{"id": "1"}, "junk"]))
        with self.assertRaises(ValueError):
            classify_entity("not-an-object")

    def test_missing_details_is_document_lines_gap(self):
        verdict = classify_entity(entity(details=None))
        self.assertFalse(verdict.complete)
        self.assertIn("document_lines", verdict.missing)
        self.assertIsNone(verdict.counts["lines"])


class NeedsDetailTest(unittest.TestCase):
    def test_success_same_version_is_complete(self):
        row = {"status": "success", "source_version": "7", "detail_version": "7"}
        self.assertFalse(needs_detail(row))

    def test_non_success_needs_detail(self):
        for status in ("failed", "partial", None):
            row = {"status": status, "source_version": "7", "detail_version": "7"}
            self.assertTrue(needs_detail(row))

    def test_source_version_drift_needs_detail(self):
        self.assertTrue(needs_detail({"status": "success", "source_version": "8",
                                      "detail_version": "7"}))
        self.assertTrue(needs_detail({"status": "success", "source_version": "8",
                                      "detail_version": None}))
        # Source without a version can never disprove a stored success.
        self.assertFalse(needs_detail({"status": "success", "source_version": None,
                                       "detail_version": "7"}))


class EligibleTest(unittest.TestCase):
    def test_partial_not_eligible_without_retry(self):
        row = {"status": "partial", "source_version": "7", "detail_version": "7",
               "attempted_source_version": "7", "attempts": 1}
        self.assertFalse(eligible(row))
        self.assertFalse(eligible(row, retry_failed=False))
        self.assertTrue(eligible(row, retry_failed=True))

    def test_failed_at_max_attempts_not_eligible(self):
        row = {"status": "failed", "source_version": "7", "detail_version": None,
               "attempted_source_version": "7", "attempts": MAX_ATTEMPTS}
        self.assertTrue(needs_detail(row))
        self.assertFalse(eligible(row))
        self.assertTrue(eligible(row, retry_failed=True))

    def test_failed_below_max_attempts_eligible(self):
        row = {"status": "failed", "source_version": "7", "detail_version": None,
               "attempted_source_version": "7", "attempts": MAX_ATTEMPTS - 1}
        self.assertTrue(eligible(row))

    def test_partial_with_new_source_version_eligible(self):
        row = {"status": "partial", "source_version": "8", "detail_version": "7",
               "attempted_source_version": "7", "attempts": 1}
        self.assertTrue(eligible(row))

    def test_success_same_version_not_eligible(self):
        row = {"status": "success", "source_version": "7", "detail_version": "7",
               "attempted_source_version": "7", "attempts": 1}
        self.assertFalse(eligible(row, retry_failed=True))


class NextStateRowTest(unittest.TestCase):
    def test_first_attempt_is_one(self):
        row = next_state_row(None, classify_entity(entity()), now=NOW)
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["completed_at"], NOW)
        self.assertEqual(row["attempted_at"], NOW)
        self.assertIsNone(row["last_error"])
        self.assertIsNone(row["document_id"])

    def test_attempts_reset_on_source_version_drift(self):
        prev = {"document_id": 42, "status": "failed", "attempts": 3,
                "attempted_source_version": "6"}
        row = next_state_row(prev, classify_entity(entity(version="7")), now=NOW)
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["document_id"], 42)

    def test_same_version_increments_attempts(self):
        prev = {"status": "failed", "attempts": 3, "attempted_source_version": "7"}
        row = next_state_row(prev, classify_entity(entity()), now=NOW,
                             error=ValueError("boom"))
        self.assertEqual(row["attempts"], 4)
        self.assertEqual(row["status"], "failed")

    def test_partial_records_missing_parts_note(self):
        verdict = classify_entity(entity(branchs=None, approves=None))
        row = next_state_row(None, verdict, now=NOW)
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["last_error"],
                         "source omitted ancillary arrays: branch_allocations,approval_history")
        self.assertIsNone(row["completed_at"])
        self.assertEqual(row["line_count"], 0)
        self.assertIsNone(row["branch_count"])
        self.assertIsNone(row["approval_count"])

    def test_last_error_never_contains_source_text(self):
        secret = "SECRET-ENTITY-原文-供应商张三-1001"
        error = ValueError(f"fetch failed for payload {secret}")
        row = next_state_row(None, classify_entity(entity()), now=NOW, error=error)
        self.assertEqual(row["status"], "failed")
        self.assertNotIn(secret, row["last_error"])
        self.assertNotIn("1001", row["last_error"])
        self.assertEqual(row["last_error"], "ValueError")

    def test_failed_after_success_preserves_completed_at_counts_and_hash(self):
        prev = {"document_id": 7, "status": "success", "attempts": 1,
                "attempted_source_version": "6",
                "completed_at": "2026-11-01 00:00:00",
                "line_count": 5, "branch_count": 2, "approval_count": 1,
                "payload_hash": "oldhash"}
        verdict = classify_entity(entity(version="7", details=[{"id": "1"}]))
        row = next_state_row(prev, verdict, now=NOW, error=ValueError("boom"))
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], 1)  # version drift 6 -> 7 resets
        self.assertEqual(row["attempted_source_version"], "7")
        self.assertEqual(row["attempted_at"], NOW)
        self.assertEqual(row["last_error"], "ValueError")
        # mark_failure semantics: only the five failure columns move.
        self.assertEqual(row["completed_at"], "2026-11-01 00:00:00")
        self.assertEqual(row["line_count"], 5)
        self.assertEqual(row["branch_count"], 2)
        self.assertEqual(row["approval_count"], 1)
        self.assertEqual(row["payload_hash"], "oldhash")
        self.assertEqual(row["document_id"], 7)

    def test_failed_with_no_prev_starts_fresh(self):
        verdict = classify_entity(entity(details=[{"id": "1"}]))
        row = next_state_row(None, verdict, now=NOW, error=ValueError("boom"))
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], 1)
        self.assertIsNone(row["completed_at"])
        self.assertEqual(row["line_count"], 1)
        self.assertEqual(row["branch_count"], 0)
        self.assertEqual(row["approval_count"], 0)
        self.assertEqual(row["payload_hash"], verdict.payload_hash)

    def test_partial_still_overwrites_counts(self):
        prev = {"status": "success", "attempts": 1, "attempted_source_version": "7",
                "completed_at": "2026-11-01 00:00:00",
                "line_count": 5, "branch_count": 2, "approval_count": 1,
                "payload_hash": "oldhash"}
        verdict = classify_entity(entity(branchs=None, details=[{"id": "1"}, {"id": "2"}]))
        row = next_state_row(prev, verdict, now=NOW)
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["attempts"], 2)
        self.assertEqual(row["line_count"], 2)
        self.assertIsNone(row["branch_count"])
        self.assertEqual(row["approval_count"], 0)
        self.assertEqual(row["payload_hash"], verdict.payload_hash)
        self.assertIsNone(row["completed_at"])

    def test_output_columns_align_with_state_table(self):
        row = next_state_row(None, classify_entity(entity(details=[{"id": "1"}])), now=NOW)
        self.assertEqual(set(row), {
            "document_id", "status", "attempts", "line_count", "branch_count",
            "approval_count", "source_version", "attempted_source_version",
            "payload_hash", "last_error", "attempted_at", "completed_at"})
        self.assertEqual(row["line_count"], 1)
        self.assertEqual(row["branch_count"], 0)
        self.assertEqual(row["approval_count"], 0)
        self.assertEqual(row["source_version"], "7")
        self.assertEqual(row["attempted_source_version"], "7")


if __name__ == "__main__":
    unittest.main()
