"""Offline safety tests for intraday polling and DB-based catch-up."""

import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import kemai_live_sync as live
import kemai_commerce_sync as commerce
import kemai_retail_lines_from_flows as lines


TZ = ZoneInfo("Asia/Shanghai")


class LivePlanningTests(unittest.TestCase):
    def test_service_start_waits_after_a_recent_manual_snapshot(self):
        now = datetime(2026, 11, 8, 17, 40, tzinfo=TZ)
        cursor = {"last_polled_day": date(2026, 11, 8),
                  "observed_through_utc": datetime(2026, 11, 8, 9, 31)}
        with patch.object(live, "checkpoint_day", return_value=date(2026, 11, 7)), \
             patch.object(live, "live_cursor", return_value=cursor):
            self.assertEqual(live.initial_delay(object(), now, 1800), 1260)

    def test_service_start_does_not_delay_an_unfinished_day(self):
        now = datetime(2026, 11, 8, 17, 40, tzinfo=TZ)
        with patch.object(live, "checkpoint_day", return_value=date(2026, 11, 6)), \
             patch.object(live, "live_cursor") as cursor:
            self.assertEqual(live.initial_delay(object(), now, 1800), 0)
        cursor.assert_not_called()

    def test_02_is_finalization_gate_not_first_poll_gate(self):
        self.assertEqual(live.finalized_until(datetime(2026, 11, 9, 1, 30, tzinfo=TZ)),
                         date(2026, 11, 7))
        self.assertEqual(live.finalized_until(datetime(2026, 11, 9, 2, 0, tzinfo=TZ)),
                         date(2026, 11, 8))

    def test_long_outage_coalesces_to_bounded_daily_catchup(self):
        self.assertEqual(live.pending_days(date(2026, 11, 7), date(2026, 11, 11), 2),
                         [date(2026, 11, 8), date(2026, 11, 9)])
        self.assertEqual(live.pending_days(date(2026, 11, 11), date(2026, 11, 11), 2), [])

    def test_plan_reports_backlog_without_source_calls(self):
        conn = object()
        now = datetime(2026, 11, 10, 3, tzinfo=TZ)
        with patch.object(live, "checkpoint_day", return_value=date(2026, 11, 7)), \
             patch.object(live, "live_cursor", return_value=None), \
             patch.object(live, "scan_day") as source:
            result = live.plan(conn, now, 2)
        source.assert_not_called()
        self.assertEqual(result["streams"]["retail-sales"]["catchup_days_this_cycle"],
                         ["2026-11-08", "2026-11-09"])
        self.assertEqual(result["streams"]["retail-sales"]["pending_finalization_days"], 2)


class LivePageOverlapTests(unittest.TestCase):
    def test_provisional_scan_keeps_unique_ids_from_overlapping_offset_pages(self):
        pages = [[{"id": str(i), "version": 1} for i in range(1000)],
                 [{"id": str(i), "version": 2} for i in range(995, 1995)], []]
        stats = {}
        with patch.object(commerce, "list_window", side_effect=lambda *args: pages[args[3] - 1]) as source:
            found, page_count = commerce.scan_day("retail-sales", date(2026, 11, 8),
                                                   "incremental", .5, 2, 0,
                                                   allow_page_overlap=True, scan_stats=stats)
        self.assertEqual(page_count, 3)
        self.assertEqual(source.call_count, 3)
        self.assertEqual(len(found), 1995)
        self.assertEqual(stats["page_overlap_rows"], 5)
        self.assertEqual(found["995"]["version"], 2)

    def test_finalized_scan_still_rejects_cross_page_duplicate(self):
        pages = [[{"id": str(i)} for i in range(1000)],
                 [{"id": "999"}]]
        with patch.object(commerce, "list_window", side_effect=lambda *args: pages[args[3] - 1]):
            with self.assertRaisesRegex(RuntimeError, "duplicate id"):
                commerce.scan_day("retail-sales", date(2026, 11, 7),
                                  "incremental", .5, 2, 0)

    def test_provisional_scan_rejects_duplicate_within_one_page(self):
        with patch.object(commerce, "list_window", return_value=[{"id": "x"}, {"id": "x"}]):
            with self.assertRaisesRegex(RuntimeError, "duplicate id"):
                commerce.scan_day("retail-orders", date(2026, 11, 8),
                                  "incremental", .5, 2, 0,
                                  allow_page_overlap=True)


class LiveWriteTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.now = datetime(2026, 11, 8, 12, tzinfo=TZ)
        self.day = self.now.date()

    def test_current_day_does_not_advance_final_checkpoint(self):
        with patch.object(live, "stream_lock") as lock, \
             patch.object(live, "start_window", return_value=8), \
             patch.object(live, "honor_cooldown"), \
             patch.object(live, "scan_day", return_value=({"a": {"id": "a"}}, 1)), \
             patch.object(live, "sync_day", return_value=1), \
             patch.object(live, "ensure_connection_after_fetch"), \
             patch.object(live, "save_daily_checkpoint") as daily, \
             patch.object(live, "save_live_cursor") as cursor, \
             patch.object(live, "finish_success"), patch.object(live, "sql"):
            result = live.poll_day(self.conn, "retail-sales", self.day, self.now)
        lock.assert_called_once()
        self.assertFalse(result["final"])
        daily.assert_not_called()
        cursor.assert_called_once()
        self.conn.commit.assert_called_once()

    def test_failed_scan_keeps_both_cursors_unchanged(self):
        with patch.object(live, "stream_lock"), \
             patch.object(live, "start_window", return_value=9), \
             patch.object(live, "honor_cooldown"), \
             patch.object(live, "scan_day", side_effect=RuntimeError("rate limit")), \
             patch.object(live, "save_daily_checkpoint") as daily, \
             patch.object(live, "save_live_cursor") as cursor, \
             patch.object(live, "finish_failure") as failure:
            with self.assertRaisesRegex(RuntimeError, "rate limit"):
                live.poll_day(self.conn, "retail-orders", self.day, self.now)
        daily.assert_not_called()
        cursor.assert_not_called()
        failure.assert_called_once()

    def test_finalized_day_advances_only_after_verified_save(self):
        with patch.object(live, "stream_lock"), \
             patch.object(live, "start_window", return_value=10), \
             patch.object(live, "honor_cooldown"), \
             patch.object(live, "scan_day", return_value=({"a": {"id": "a"}}, 1)), \
             patch.object(live, "sync_day", return_value=1), \
             patch.object(live, "ensure_connection_after_fetch"), \
             patch.object(live, "save_daily_checkpoint") as daily, \
             patch.object(live, "save_live_cursor") as cursor, \
             patch.object(live, "finish_success"):
            live.poll_day(self.conn, "retail-sales", self.day, self.now, final=True)
        daily.assert_called_once_with(self.conn, "retail-sales", self.day)
        cursor.assert_not_called()
        self.conn.commit.assert_called_once()

    def test_provisional_sale_snapshot_avoids_excel_export_task(self):
        with patch.object(commerce, "scan_day", return_value=({"a": {"id": "a"}}, 1)), \
             patch.object(commerce, "wait_export") as export, \
             patch.object(commerce, "ensure_connection_after_fetch"), \
             patch.object(commerce, "retail_flow_batch"), \
             patch.object(commerce, "captured_key_count", return_value=1):
            count = commerce.sync_day(self.conn, "retail-sales", self.day,
                                      "incremental", .5, 2, 0, verify_export=False)
        self.assertEqual(count, 1)
        export.assert_not_called()


class LiveCycleTests(unittest.TestCase):
    def test_today_is_polled_before_backlog_and_each_stream_stops_at_failed_day(self):
        now = datetime(2026, 11, 11, 3, tzinfo=TZ)
        calls = []

        def fake_poll(conn, kind, day, observed_at, final=False, rate=.5):
            calls.append((kind, day.isoformat(), final))
            if kind == "retail-orders" and day == date(2026, 11, 8):
                raise RuntimeError("source error")
            return {"kind": kind, "day": day.isoformat(), "final": final}

        with patch.object(live, "require_live_table"), \
             patch.object(live, "ensure_old_workers_paused"), \
             patch.object(live, "checkpoint_day", return_value=date(2026, 11, 7)), \
             patch.object(live, "mapped_through", return_value=date(2026, 11, 7)), \
             patch.object(live, "poll_day", side_effect=fake_poll), \
             patch.object(live, "map_lines", return_value=1):
            result = live.run_once(object(), now, 2, .5)
        self.assertEqual(calls[:2], [("retail-orders", "2026-11-11", False),
                                     ("retail-sales", "2026-11-11", False)])
        self.assertIn(("retail-orders", "2026-11-08", True), calls)
        self.assertNotIn(("retail-orders", "2026-11-09", True), calls)
        self.assertIn(("retail-sales", "2026-11-09", True), calls)
        self.assertIn("final:retail-orders:2026-11-08", result["errors"])


class LiveMutexTests(unittest.TestCase):
    """M1.2: mutex must trust the real process table, not status JSON."""

    def test_real_worker_process_blocks_start(self):
        tmp = Path(tempfile.mkdtemp())
        rows = [(4242, 1, "/usr/local/bin/python3 kemai_commerce_sync.py incremental --kind retail-sales")]
        with patch.object(live, "_ps_rows", return_value=rows), patch.object(live, "ROOT", tmp):
            self.assertEqual([w["pid"] for w in live.active_source_workers()], [4242])
            with self.assertRaisesRegex(RuntimeError, "source worker is running"):
                live.ensure_old_workers_paused()

    def test_shell_wrapper_grep_and_self_are_not_workers(self):
        rows = [
            (1, 0, "/bin/bash -c python3 kemai_live_sync.py once"),
            (2, 0, "/usr/bin/grep kemai_commerce_sync.py"),
            (3, 0, "/usr/local/bin/python3 kemai_live_sync.py supervise"),
        ]
        with patch.object(live, "_ps_rows", return_value=rows):
            self.assertEqual(live.active_source_workers(), [])

    def test_unreadable_process_table_does_not_claim_conflict(self):
        with patch.object(live, "_ps_rows", return_value=[]):
            self.assertEqual(live.active_source_workers(), [])

    def test_ancestors_are_skipped(self):
        rows = [(10, 0, "/usr/local/bin/python3 kemai_commerce_sync.py"),
                (11, 10, "/usr/local/bin/python3 helper.py"),
                (12, 11, "/usr/local/bin/python3 kemai_live_sync.py once")]
        with patch.object(live, "_ps_rows", return_value=rows), patch.object(live.os, "getpid", return_value=12):
            self.assertEqual(live.active_source_workers(), [])


class LockDesignTests(unittest.TestCase):
    """Service lock (lifetime) and work lock (one cycle) must be separate files."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        for name, value in (("SERVICE_LOCK", self.tmp / "service.lock"),
                            ("WORK_LOCK", self.tmp / "work.lock")):
            patcher = patch.object(live, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.now = datetime(2026, 11, 8, 12, tzinfo=TZ)

    def test_locks_use_different_files(self):
        self.assertNotEqual(live.SERVICE_LOCK, live.WORK_LOCK)

    def test_service_lock_does_not_block_the_work_lock(self):
        with live.exclusive_runner():
            with live.work_runner():
                pass

    def test_work_lock_is_released_after_a_cycle(self):
        with patch.object(live, "run_once", return_value={"snapshots": []}) as once:
            live.run_cycle_with_work_lock(object(), self.now, 2, .5)
        once.assert_called_once()
        with live.work_runner():
            pass  # reacquirable: the cycle released it

    def test_busy_work_lock_skips_instead_of_failing(self):
        with live.work_runner(), patch.object(live, "run_once") as once:
            result = live.run_cycle_with_work_lock(object(), self.now, 2, .5)
        once.assert_not_called()
        self.assertIn("skipped_busy", result)
        self.assertEqual(live.once_exit_code(result), 4)

    def test_second_service_instance_is_rejected(self):
        with live.exclusive_runner():
            with self.assertRaisesRegex(live.LockBusy, "already running"):
                with live.exclusive_runner():
                    pass


class OnceEntryTests(unittest.TestCase):
    """The real main() entry path, not just the helper exit codes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        for name, value in (("SERVICE_LOCK", self.tmp / "service.lock"),
                            ("WORK_LOCK", self.tmp / "work.lock")):
            patcher = patch.object(live, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.common = [
            patch.object(sys, "argv", ["kemai_live_sync.py", "once"]),
            patch.object(live, "password_from_terminal", return_value="secret"),
            patch.object(live, "connect", return_value=MagicMock()),
            patch.object(live, "require_live_table"),
            patch.object(live, "local_now", return_value=datetime(2026, 11, 8, 12, tzinfo=TZ)),
        ]
        for item in self.common:
            item.start()
            self.addCleanup(item.stop)

    def test_once_still_runs_while_a_supervisor_holds_the_service_lock(self):
        with live.exclusive_runner(), \
             patch.object(live, "run_once", return_value={"errors": {}, "cooling": None}) as once:
            with self.assertRaises(SystemExit) as exit_ctx:
                live.main()
        self.assertEqual(exit_ctx.exception.code, 0)
        once.assert_called_once()

    def test_once_returns_4_when_the_work_lock_is_busy(self):
        with live.work_runner(), patch.object(live, "run_once") as once:
            with self.assertRaises(SystemExit) as exit_ctx:
                live.main()
        once.assert_not_called()
        self.assertEqual(exit_ctx.exception.code, 4)


class WorkerListTests(unittest.TestCase):
    # Frozen expectation: every entrypoint that can issue a Kemai source request.
    # If a new worker is added, this list must grow with it.
    EXPECTED = (
        "kemai_daily_launch.py", "kemai_collection_launch.py",
        "kemai_commerce_sync.py", "kemai_purchase_detail_backfill.py",
        "kemai_commerce_reconcile.py", "kemai_retail_detail_backfill.py",
        "kemai_product_incremental.py", "kemai_changed_refresh.py",
        "kemai_changed_tabs.py", "kemai_masters.py",
        "kemai_derive_suppliers.py", "kemai_retail_lines_from_flows.py",
        "kemai_sync.py", "kemai_product_tabs_backfill.py",
        "kemai_launch.py", "kemai_followup_launch.py",
        "kemai_catchup_launch.py", "kemai_priority_launch.py",
        "kemai_product_export_probe.py",
    )

    def test_worker_list_covers_every_source_entrypoint(self):
        missing = [name for name in self.EXPECTED if name not in live.SOURCE_SCRIPTS]
        self.assertEqual(missing, [])

    def test_short_lived_launcher_is_not_a_worker(self):
        self.assertNotIn("kemai_live_service.py", live.SOURCE_SCRIPTS)


class LiveCooldownTests(unittest.TestCase):
    def test_cooldown_address_is_read_only(self):
        state = {"resume_after_epoch": 2_000_000_000, "message": "too frequent"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rate.json"
            path.write_text(__import__("json").dumps(state))
            with patch.object(live, "RATE_STATE", path):
                cooling = live.cooldown_state(datetime(2030, 1, 1, tzinfo=TZ))
        self.assertTrue(cooling["cooling"])
        self.assertIn("resume_after", cooling)

    def test_cooling_returns_without_any_source_request(self):
        now = datetime(2026, 11, 8, 12, tzinfo=TZ)
        with patch.object(live, "require_live_table"), \
             patch.object(live, "ensure_old_workers_paused"), \
             patch.object(live, "checkpoint_day", return_value=date(2026, 11, 6)), \
             patch.object(live, "cooldown_state",
                          return_value={"cooling": True, "seconds_remaining": 900, "resume_after": "2026-11-08T20:00:00"}), \
             patch.object(live, "poll_day") as poll, patch.object(live, "map_lines") as mapper:
            result = live.run_once(object(), now, 2, .5)
        poll.assert_not_called()
        mapper.assert_not_called()
        self.assertEqual(result["source_requests_estimate"], 0)
        self.assertEqual(result["pending_finalization_days"]["retail-orders"], 1)
        self.assertEqual(live.once_exit_code(result), 3)


class LiveBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.now = datetime(2026, 11, 8, 12, tzinfo=TZ)

    def test_provisional_snapshot_keeps_live_covered_through_empty(self):
        statements = []
        with patch.object(live, "stream_lock"), patch.object(live, "start_window", return_value=1), \
             patch.object(live, "honor_cooldown"), \
             patch.object(live, "scan_day", return_value=({"a": {"id": "a"}}, 1)), \
             patch.object(live, "sync_day", return_value=1), \
             patch.object(live, "ensure_connection_after_fetch"), \
             patch.object(live, "save_live_cursor"), patch.object(live, "finish_success"), \
             patch.object(live, "sql", side_effect=lambda *a, **k: statements.append(a[1])):
            live.poll_day(self.conn, "retail-orders", self.now.date(), self.now)
        self.assertTrue(any("covered_through=NULL" in q for q in statements))

    def test_duplicate_page_key_aborts_without_cursor_advance(self):
        with patch.object(live, "stream_lock"), patch.object(live, "start_window", return_value=1), \
             patch.object(live, "honor_cooldown"), \
             patch.object(live, "scan_day", side_effect=RuntimeError("duplicate id across pages")), \
             patch.object(live, "save_live_cursor") as cursor, \
             patch.object(live, "save_daily_checkpoint") as daily, \
             patch.object(live, "finish_failure"):
            with self.assertRaisesRegex(RuntimeError, "duplicate"):
                live.poll_day(self.conn, "retail-sales", self.now.date(), self.now)
        cursor.assert_not_called()
        daily.assert_not_called()

    def test_two_same_day_snapshots_stay_provisional(self):
        with patch.object(live, "stream_lock"), patch.object(live, "start_window", return_value=1), \
             patch.object(live, "honor_cooldown"), \
             patch.object(live, "scan_day", return_value=({"a": {"id": "a"}}, 1)), \
             patch.object(live, "sync_day", return_value=1), \
             patch.object(live, "ensure_connection_after_fetch"), \
             patch.object(live, "save_live_cursor") as cursor, \
             patch.object(live, "save_daily_checkpoint") as daily, \
             patch.object(live, "finish_success"), patch.object(live, "sql"):
            for _ in range(2):
                live.poll_day(self.conn, "retail-orders", self.now.date(), self.now)
        daily.assert_not_called()
        self.assertEqual(cursor.call_count, 2)

    def test_request_estimate_counts_pages_plus_export_tasks(self):
        now = datetime(2026, 11, 8, 12, tzinfo=TZ)

        def fake_poll(conn, kind, day, observed_at, final=False, rate=.5):
            return {"kind": kind, "day": day.isoformat(), "final": final,
                    "pages": 3 if not final else 4}

        with patch.object(live, "require_live_table"), \
             patch.object(live, "ensure_old_workers_paused"), \
             patch.object(live, "checkpoint_day", return_value=date(2026, 11, 6)), \
             patch.object(live, "cooldown_state", return_value={"cooling": False}), \
             patch.object(live, "poll_day", side_effect=fake_poll), \
             patch.object(live, "map_lines", return_value=0), \
             patch.object(live, "mapped_through", return_value=date(2026, 11, 6)):
            result = live.run_once(object(), now, 2, .5)
        self.assertEqual(result["source_requests_estimate"], 3 * 2 + 4 * 2 + 2)
        self.assertIn("excludes export-task-list polling", result["estimate_basis"])

    def test_exit_codes_are_explicit(self):
        self.assertEqual(live.once_exit_code({"errors": {}, "cooling": None}), 0)
        self.assertEqual(live.once_exit_code({"errors": {"x": "boom"}}), 2)
        self.assertEqual(live.once_exit_code({"errors": {}, "cooling": {"cooling": True}}), 3)
        self.assertEqual(live.once_exit_code({"skipped_busy": "work lock held"}), 4)

    def test_next_delay_merges_overrun_and_backs_off(self):
        self.assertEqual(live.next_delay({"errors": {}, "cooling": {"cooling": True,
                                                                    "seconds_remaining": 120}}, 3, 1800), 120)
        self.assertEqual(live.next_delay({"errors": {"a": "b"}}, 2, 1800), 120)
        self.assertEqual(live.next_delay({"errors": {}, "cooling": None}, 0, 1800), 1800)

    def test_plan_exposes_cooldown_and_real_workers(self):
        with patch.object(live, "checkpoint_day", return_value=date(2026, 11, 7)), \
             patch.object(live, "live_cursor", return_value=None), \
             patch.object(live, "active_source_workers", return_value=[{"pid": 9, "command": "x"}]), \
             patch.object(live, "cooldown_state", return_value={"cooling": False}):
            result = live.plan(object(), self.now, 2)
        self.assertEqual(result["active_source_workers"], [{"pid": 9, "command": "x"}])
        self.assertFalse(result["cooldown"]["cooling"])


class LineMappingInvariantTests(unittest.TestCase):
    def test_live_mapping_keeps_parent_lag_visible_as_partial_coverage(self):
        conn = MagicMock()
        stats = {}

        def mapped(_conn, _start, _end, *, allow_orphans, stats):
            self.assertTrue(allow_orphans)
            stats["unmatched_flows"] = 2
            return 5

        with patch.object(live, "start_window", return_value=17), \
             patch.object(live, "materialize", side_effect=mapped), \
             patch.object(live, "finish_success") as finished, \
             patch.object(live, "sql"):
            count = live.map_lines(conn, date(2026, 11, 8), False, stats)
        self.assertEqual(count, 5)
        self.assertEqual(stats["unmatched_flows"], 2)
        self.assertEqual(finished.call_args.args[6], "partial")
        self.assertEqual(finished.call_args.args[8], 7)
        conn.commit.assert_called_once()

    def test_final_mapping_still_rejects_unmatched_flow(self):
        with patch.object(lines, "one", side_effect=[{"n": 5}, {"n": 2}]), \
             patch.object(lines, "sql") as write:
            with self.assertRaisesRegex(RuntimeError, "without order: 2"):
                lines.materialize(MagicMock(), date(2026, 11, 7), date(2026, 11, 7))
        write.assert_not_called()

    def test_flow_mapping_never_overwrites_detail_api_rows(self):
        statements = []
        seq = [{"n": 1}, {"n": 0}, {"id": 10}, {"id": None}]
        with patch.object(lines, "one", side_effect=seq), \
             patch.object(lines, "sql", side_effect=lambda *a, **k: statements.append(a[1])), \
             patch.object(lines, "verify", return_value=0):
            lines.materialize(MagicMock(), date(2026, 11, 7), date(2026, 11, 7))
        upserts = [q for q in statements if "ON DUPLICATE KEY UPDATE" in q]
        self.assertTrue(upserts)
        self.assertIn("source_kind='sales_flow'", upserts[0])

class LegacyExportDownloadTests(unittest.TestCase):
    def test_wait_export_accepts_public_download_readiness_without_source_url(self):
        task = {"id": 17, "status": 2, "totalRows": 1, "download_ready": True}
        with patch.object(commerce, "call", return_value=[task]), \
             patch.object(commerce, "pause"), patch.object(commerce.time, "sleep"):
            self.assertEqual(commerce.wait_export("retail-sales-export-start",
                                                  date(2026, 11, 8), date(2026, 11, 8), .5), task)

    def test_history_rows_come_from_export_download_receipt(self):
        task = {"id": 17, "status": 2, "totalRows": 1, "download_ready": True}

        def fetch(*args):
            self.assertEqual(args[:3], ("export-download", "--task-id", "17"))
            output = Path(args[4])
            from openpyxl import Workbook
            book = Workbook()
            sheet = book.active
            sheet.append(["历史收货"])
            sheet.append(["导出明细"])
            sheet.append(["单据编号", "商品编码", "明细序号"])
            sheet.append(["CG001", "ITEM1", 1])
            book.save(output)
            return [{"task_id": "17", "path": str(output), "rows": 1}]

        with patch.object(commerce, "call", side_effect=fetch), \
             patch("urllib.request.urlretrieve", side_effect=AssertionError("URL fallback used")):
            rows = commerce.history_export_rows(task)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sheetNo"], "CG001")
        self.assertEqual(rows[0]["itemCode"], "ITEM1")

    def test_history_receipt_rejects_task_identity_and_row_count_mismatch(self):
        from openpyxl import Workbook
        for receipt, message in (({"task_id": "18", "rows": 1}, "identity"),
                                 ({"task_id": "17", "rows": 2}, "row count")):
            with self.subTest(receipt=receipt):
                def fetch(*args):
                    output = Path(args[4])
                    book = Workbook()
                    sheet = book.active
                    sheet.append(["历史收货"])
                    sheet.append(["导出明细"])
                    sheet.append(["单据编号", "商品编码", "明细序号"])
                    sheet.append(["CG001", "ITEM1", 1])
                    book.save(output)
                    return [dict(receipt, path=str(output))]

                with patch.object(commerce, "call", side_effect=fetch):
                    with self.assertRaisesRegex(RuntimeError, message):
                        commerce.history_export_rows({"id": 17, "totalRows": 1, "download_ready": True})



if __name__ == "__main__":
    unittest.main()
