"""Offline fail-closed and private-artifact checks for the product export probe."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

import kemai_product_export_probe as probe


class ProductArtifactTests(unittest.TestCase):
    def test_csv_is_rejected_instead_of_being_inspected_as_a_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "products.csv"
            path.write_text("商品编码,商品名称\n001,测试\n", encoding="utf-8-sig")
            with self.assertRaisesRegex(RuntimeError, "strict XLSX"):
                probe.inspect_file(path)

    def test_probe_uses_export_download_and_separates_artifact_from_report(self):
        task = {"id": 17, "sheetType": "itemArchives", "status": 2,
                "totalRows": 1, "download_ready": True}

        def download(public_task, output):
            self.assertEqual(public_task, task)
            output.parent.mkdir(parents=True, exist_ok=True)
            book = Workbook()
            sheet = book.active
            sheet.title = "商品档案"
            sheet.append(["商品档案"])
            sheet.append(["商品编码", "商品名称"])
            sheet.append(["001", "测试商品"])
            book.save(output)
            return {"task_id": "17", "path": str(output), "rows": 1}

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(probe, "ROOT", Path(directory)), \
             patch.object(probe, "ensure_source_idle"), \
             patch.object(probe, "await_export", return_value=task), \
             patch.object(probe, "download_export", side_effect=download, create=True) as fetched, \
             patch("urllib.request.urlretrieve", side_effect=AssertionError("URL fallback used")):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                with patch.object(sys, "argv", ["probe", "--snapshot", "2026-11-08", "--expected-total", "1"]):
                    probe.main()
            artifact = Path(directory) / ".kemai-artifacts" / "kemai_item_archives_2026-11-08.xlsx"
            report_path = Path(directory) / ".local-audits" / "kemai_item_archives_export_probe_2026-11-08.json"
            self.assertEqual(artifact, Path(json.loads(stdout.getvalue())["file"]))
            self.assertTrue(artifact.is_file())
            self.assertTrue(report_path.is_file())
            self.assertEqual(json.loads(report_path.read_text())["format"], "xlsx")
            fetched.assert_called_once()


if __name__ == "__main__":
    unittest.main()
