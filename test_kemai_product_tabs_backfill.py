"""Offline tests for the bounded product-tab backfill tool (M4).

No source request and no real database: the section engine and DB helpers are
patched so the tests verify scoping, limits and read-only reporting.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

import kemai_product_tabs_backfill as tabs


class BacklogTests(unittest.TestCase):
    def test_eta_is_a_lower_bound_at_the_rate_cap(self):
        self.assertEqual(tabs.eta_hours(0, 0.5), 0.0)
        self.assertEqual(tabs.eta_hours(1800, 0.5), 1.0)
        self.assertAlmostEqual(tabs.eta_hours(900, 0.5), 0.5)

    def test_summary_splits_retryable_and_blocked(self):
        queues = {"barcode": {"retryable": 10, "blocked_attempts": 2, "pending": 12,
                              "failed": 0, "done": 5, "remaining": 12, "total": 17}}
        with patch.object(tabs, "backlog", return_value=queues):
            summary = tabs.summarize(object(), ["barcode"], 0.5)
        self.assertEqual(summary["retryable_total"], 10)
        self.assertEqual(summary["blocked_total"], 2)
        self.assertEqual(summary["theoretical_hours"], 0.0)  # 10/0.5/3600 rounds to 0.0
        self.assertIn("not a completion promise", summary["note"])

    def test_plan_is_read_only(self):
        with patch.object(tabs, "backlog", return_value={}), \
             patch.object(tabs, "sync_sections") as sync:
            result = tabs.plan(object(), ["barcode"], 0.5)
        sync.assert_not_called()
        self.assertEqual(result["source_requests"], 0)
        self.assertEqual(result["db_writes"], 0)


class SelectionTests(unittest.TestCase):
    def test_select_ids_only_takes_retryable_pending_objects(self):
        cur = MagicMock()
        cur.fetchall.return_value = [{"source_key": "a"}, {"source_key": "b"}]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        ids = tabs.select_ids(conn, "barcode", 5)
        self.assertEqual(ids, ["a", "b"])
        statement, params = cur.execute.call_args.args
        self.assertIn("attempts<3", statement)
        self.assertIn("status IN ('pending','failed')", statement)
        self.assertEqual(params[-1], 5)
        self.assertEqual(params[1], "item_tab_barcode")


class StartTests(unittest.TestCase):
    def setUp(self):
        self.cur = MagicMock()
        self.cur.fetchone.return_value = {"n": 3}
        self.conn = MagicMock()
        self.conn.cursor.return_value.__enter__.return_value = self.cur

    def test_start_scopes_one_section_and_the_selected_ids(self):
        with patch.object(tabs, "ensure_tables_ready"), \
             patch.object(tabs, "one", side_effect=[{"acquired": 1}, {"id": 7}]), \
             patch.object(tabs, "sql"), patch.object(tabs, "start_window", return_value=5), \
             patch.object(tabs, "finish_success"), \
             patch.object(tabs, "select_ids", return_value=["a", "b", "c"]), \
             patch.object(tabs, "sync_sections") as sync, \
             patch.object(tabs, "backlog", return_value={"barcode": {"retryable": 4, "blocked_attempts": 1}}):
            result = tabs.start(self.conn, "barcode", 3, 0.5)
        self.assertEqual(result["requested"], 3)
        self.assertEqual(result["done"], 3)
        kwargs = sync.call_args.kwargs
        self.assertEqual(kwargs["only_ids"], ["a", "b", "c"])
        self.assertEqual(kwargs["sections"], ["barcode"])
        self.assertEqual(kwargs["rate"], 0.5)
        self.assertEqual(result["remaining_retryable"], 4)

    def test_start_reports_empty_queue_without_source(self):
        with patch.object(tabs, "ensure_tables_ready"), \
             patch.object(tabs, "one", side_effect=[{"acquired": 1}, {"id": 7}]), \
             patch.object(tabs, "sql"), patch.object(tabs, "select_ids", return_value=[]), \
             patch.object(tabs, "sync_sections") as sync:
            result = tabs.start(self.conn, "barcode", 3, 0.5)
        sync.assert_not_called()
        self.assertEqual(result["requested"], 0)
        self.assertIn("no retryable objects", result["note"])

    def test_start_refuses_while_live_sync_holds_the_lock(self):
        with patch.object(tabs.fcntl, "flock", side_effect=BlockingIOError()), \
             patch.object(tabs, "ensure_tables_ready") as tables, \
             patch.object(tabs, "sync_sections") as sync:
            with self.assertRaisesRegex(RuntimeError, "retail live sync is running"):
                tabs.start(self.conn, "barcode", 1, .5)
        tables.assert_not_called()
        sync.assert_not_called()

    def test_start_refuses_second_instance(self):
        with patch.object(tabs, "ensure_tables_ready"), \
             patch.object(tabs, "one", return_value={"acquired": 0}):
            with self.assertRaisesRegex(RuntimeError, "already running"):
                tabs.start(self.conn, "barcode", 3, 0.5)


class StatusQueryTests(unittest.TestCase):
    def test_status_uses_the_real_event_columns(self):
        statements = []

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, statement, params=None): statements.append(statement)
            def fetchall(self): return []
            def fetchone(self): return None

        conn = MagicMock()
        conn.cursor.return_value = Cursor()
        with patch.object(tabs, "backlog", return_value={}):
            result = tabs.status(conn, ["barcode"], .5)
        joined = " ".join(statements)
        self.assertIn("source_rows", joined)
        self.assertIn("error_message", joined)
        self.assertNotIn("total_rows", joined)
        self.assertEqual(result["source_requests"], 0)

    def test_failed_run_status_survives_a_rollback(self):
        """A rollback must not discard the failed run marker (real commit order)."""
        class FakeConn:
            def __init__(self):
                self.pending = []
                self.committed = []

            def cursor(self):
                return MagicMock()

            def commit(self):
                self.committed.extend(self.pending)
                self.pending = []

            def rollback(self):
                self.pending = []

        conn = FakeConn()

        def fake_sql(_conn, statement, _params=None):
            conn.pending.append(statement)

        def fake_finish_failure(_conn, _event, _stream, error):
            conn.pending.append(f"EVENT_FAILED:{error}")
            conn.commit()  # kemai_sync_log.finish_failure commits internally

        with patch.object(tabs, "ensure_tables_ready"), \
             patch.object(tabs, "one", side_effect=[{"acquired": 1}, {"id": 9}]), \
             patch.object(tabs, "sql", side_effect=fake_sql), \
             patch.object(tabs, "start_window", return_value=1), \
             patch.object(tabs, "finish_failure", side_effect=fake_finish_failure), \
             patch.object(tabs, "select_ids", return_value=["a"]), \
             patch.object(tabs, "sync_sections", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                tabs.start(conn, "barcode", 1, .5)
        self.assertTrue(any("EVENT_FAILED:boom" in item for item in conn.committed))
        self.assertTrue(any("status='failed'" in item for item in conn.committed))

    def test_backfill_does_not_claim_today_coverage(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"n": 1}
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        with patch.object(tabs, "ensure_tables_ready"), \
             patch.object(tabs, "one", side_effect=[{"acquired": 1}, {"id": 9}]), \
             patch.object(tabs, "sql") as run_sql, patch.object(tabs, "start_window", return_value=1), \
             patch.object(tabs, "finish_success"), patch.object(tabs, "select_ids", return_value=["a"]), \
             patch.object(tabs, "sync_sections"), patch.object(tabs, "backlog",
                        return_value={"barcode": {"retryable": 0, "blocked_attempts": 0}}):
            tabs.start(conn, "barcode", 1, .5)
        statements = [c.args[1] for c in run_sql.call_args_list]
        self.assertTrue(any("covered_through=NULL" in q for q in statements))


class LockInteropTests(unittest.TestCase):
    def test_running_service_does_not_block_the_backfill(self):
        import tempfile
        from pathlib import Path
        import shutil
        import kemai_live_sync as live

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        work = tmp / "work.lock"
        with patch.object(live, "SERVICE_LOCK", tmp / "service.lock"), \
             patch.object(live, "WORK_LOCK", work), patch.object(tabs, "LIVE_LOCK", work):
            with live.exclusive_runner():      # long-lived service lock
                with tabs.live_runner_gate():  # per-cycle work lock is free
                    pass


class CliGuardTests(unittest.TestCase):
    def test_start_requires_a_single_section(self):
        with patch.object(sys, "argv", ["prog", "start", "--section", "all"]):
            with self.assertRaises(SystemExit):
                tabs.main()

    def test_rate_above_cap_is_rejected(self):
        with patch.object(sys, "argv", ["prog", "plan", "--section", "all", "--rate", "0.6"]):
            with self.assertRaises(SystemExit):
                tabs.main()

    def test_limit_is_bounded(self):
        with patch.object(sys, "argv", ["prog", "start", "--section", "barcode", "--limit", "0"]):
            with self.assertRaises(SystemExit):
                tabs.main()


if __name__ == "__main__":
    unittest.main()
