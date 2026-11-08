"""Offline checks for payment detail response handling and pacing."""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kemai_retail_detail_backfill as worker


class RetailDetailTests(unittest.TestCase):
    def setUp(self):
        self.when = datetime(2026, 11, 6, 12, 13, 51)
        # fetch_detail takes the shared source lease, so tests must never touch the
        # production gate directory or the project rate-limit files.
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        (self.directory / "config.json").write_text(json.dumps({
            "legacy_rate_status": str(self.directory / "legacy-rate.json"),
        }))
        isolation = patch.dict(os.environ, {
            "KEMAI_SOURCE_GATE_DIR": str(self.directory),
            "KEMAI_RATE_LIMIT_STATE": str(self.directory / "legacy-rate.json"),
            "KEMAI_RATE_LIMIT_LOG": str(self.directory / "rate-limit.log"),
        })
        isolation.start()
        self.addCleanup(isolation.stop)

    @patch.object(worker.subprocess, "run")
    def test_detail_success(self, invoke):
        invoke.return_value = SimpleNamespace(returncode=0, stdout=json.dumps({
            "ok": True, "entity": {"id": "123", "orderDetails": [], "payFlows": []}
        }), stderr="")
        self.assertEqual(worker.fetch_detail("123", self.when)["id"], "123")
        self.assertEqual(invoke.call_args.args[0][:4],
                         ["opencli", "browser", worker.SESSION, "eval"])

    @patch.object(worker, "record_limit")
    @patch.object(worker.subprocess, "run")
    def test_business_rate_limit_on_http_200(self, invoke, record_limit):
        invoke.return_value = SimpleNamespace(returncode=0, stdout=json.dumps({
            "ok": False, "error": "您的操作太频繁，请稍后再试"
        }), stderr="")
        with self.assertRaisesRegex(RuntimeError, "操作太频繁"):
            worker.fetch_detail("123", self.when)
        record_limit.assert_called_once()

    @patch.object(worker, "record_limit")
    @patch.object(worker.subprocess, "run")
    def test_unavailable_order_is_distinct_from_rate_limit(self, invoke, record_limit):
        invoke.return_value = SimpleNamespace(returncode=0, stdout=json.dumps({
            "ok": False, "error": "订单不存在"
        }), stderr="")
        with self.assertRaisesRegex(RuntimeError, "订单不存在"):
            worker.fetch_detail("123", self.when)
        record_limit.assert_not_called()

    @patch.object(worker, "pause")
    @patch.object(worker, "fetch_detail", side_effect=RuntimeError("source failed"))
    def test_failed_request_still_paces(self, fetch, pause):
        with self.assertRaisesRegex(RuntimeError, "source failed"):
            worker.process_order(None, {"source_order_id": "123", "sold_at": self.when}, 0.5)
        pause.assert_called_once()


if __name__ == "__main__":
    unittest.main()
