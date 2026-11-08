"""Offline tests for the live-sync service wrapper (M2).

No real process is spawned and no database is touched: Popen, aliveness and
preflight are patched so the tests stay hermetic.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import kemai_live_service as service


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.patches = [patch.object(service, "STATE", self.tmp / "state.json"),
                        patch.object(service, "LOG", self.tmp / "svc.log")]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_state_roundtrip_is_private(self):
        service.save_state({"pid": 1, "status": "running"})
        self.assertEqual(service.read_state()["pid"], 1)
        self.assertEqual(oct(os.stat(self.tmp / "state.json").st_mode & 0o777), "0o600")

    def test_missing_state_reads_empty(self):
        self.assertEqual(service.read_state(), {})

    def test_aliveness_checks(self):
        self.assertTrue(service.is_alive(os.getpid()))
        self.assertFalse(service.is_alive(0))
        self.assertFalse(service.is_alive("not-a-pid"))
        self.assertFalse(service.is_alive(None))

    def test_identity_requires_exact_script_path_and_subcommand(self):
        script = str(service.LEGACY / "kemai_live_sync.py")
        with patch.object(service, "process_command",
                          return_value=f"/usr/bin/python3 {script} supervise --interval-minutes 30"):
            self.assertTrue(service.is_our_supervisor(1))
        with patch.object(service, "process_command",
                          return_value="/usr/bin/python3 /tmp/kemai_live_sync.py.bak supervise"):
            self.assertFalse(service.is_our_supervisor(1))
        with patch.object(service, "process_command",
                          return_value=f"/usr/bin/python3 {script} once"):
            self.assertFalse(service.is_our_supervisor(1))

    def test_missing_values_serialize_as_null(self):
        self.assertIsNone(service.iso_or_none(None))
        self.assertEqual(service.iso_or_none(__import__("datetime").date(2026, 11, 7)), "2026-11-07")

    def test_command_carries_bounded_schedule(self):
        argv = service.command(30, 2, 0.5)
        self.assertIn("kemai_live_sync.py", argv[1])
        self.assertEqual(argv[2], "supervise")
        self.assertIn("30", argv)
        self.assertIn("0.5", argv)


class StartStopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        for name, value in (("STATE", self.tmp / "state.json"), ("LOG", self.tmp / "svc.log")):
            patcher = patch.object(service, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.args = SimpleNamespace(interval_minutes=30, max_catchup_days=2,
                                    rate=0.5, timeout_seconds=1)

    def test_start_refuses_when_service_already_alive(self):
        service.save_state({"pid": 123, "status": "running"})
        with patch.object(service, "is_alive", return_value=True):
            result, code = service.start(self.args)
        self.assertFalse(result["ok"])
        self.assertEqual(code, 2)

    def test_start_refuses_when_another_worker_exists(self):
        with patch.object(service, "is_alive", return_value=False), \
             patch.object(service, "preflight", side_effect=RuntimeError("source worker is running")):
            result, code = service.start(self.args)
        self.assertFalse(result["ok"])
        self.assertIn("preflight refused", result["error"])
        self.assertEqual(code, 3)

    def test_start_spawns_detached_supervisor_and_records_pid(self):
        process = MagicMock()
        process.pid = 4321
        with patch.object(service, "is_alive", side_effect=lambda pid: pid == 4321), \
             patch.object(service, "preflight", return_value={"cooling": False}), \
             patch.object(service, "password_from_terminal", return_value="secret"), \
             patch.object(service.subprocess, "Popen", return_value=process) as popen, \
             patch.object(service.time, "sleep"):
            result, code = service.start(self.args)
        self.assertTrue(result["ok"])
        self.assertEqual(code, 0)
        self.assertEqual(service.read_state()["pid"], 4321)
        kwargs = popen.call_args.kwargs
        self.assertTrue(kwargs["start_new_session"])
        self.assertIn("KEMAI_PASSWORD_FD", kwargs["env"])

    def test_stop_without_pid_is_a_noop(self):
        result, code = service.stop(self.args)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "not_running")
        self.assertEqual(code, 0)

    def test_stop_clears_a_stale_pid(self):
        service.save_state({"pid": 999, "status": "running"})
        with patch.object(service, "is_alive", return_value=False):
            result, code = service.stop(self.args)
        self.assertEqual(result["status"], "stale_removed")
        self.assertIsNone(service.read_state()["pid"])

    def test_stop_refuses_to_signal_a_reused_pid(self):
        service.save_state({"pid": 555, "status": "running"})
        with patch.object(service, "is_alive", return_value=True), \
             patch.object(service, "is_our_supervisor", return_value=False), \
             patch.object(service.os, "kill") as kill:
            result, code = service.stop(self.args)
        kill.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "identity_mismatch")
        self.assertEqual(service.read_state()["status"], "identity_mismatch")

    def test_stop_sends_sigterm_and_waits(self):
        service.save_state({"pid": 777, "status": "running"})
        alive = {"value": True}

        def fake_alive(pid):
            return alive["value"]

        def fake_kill(pid, sig):
            alive["value"] = False

        with patch.object(service, "is_alive", side_effect=fake_alive), \
             patch.object(service, "is_our_supervisor", return_value=True), \
             patch.object(service.os, "kill", side_effect=fake_kill) as kill, \
             patch.object(service.time, "sleep"):
            result, code = service.stop(self.args)
        self.assertTrue(result["ok"])
        self.assertEqual(kill.call_args.args[1], service.signal.SIGTERM)
        self.assertEqual(service.read_state()["status"], "stopped")

    def test_restart_does_not_start_when_stop_fails(self):
        service.save_state({"pid": 888, "status": "running"})
        with patch.object(service, "stop", return_value=({"ok": False}, 2)), \
             patch.object(service, "start") as start:
            result, code = service.restart(self.args)
        start.assert_not_called()
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
