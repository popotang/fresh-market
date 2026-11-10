"""Offline compatibility checks for non-leaking public export task records."""

import json
import unittest
from unittest.mock import Mock

from kemai_pipeline.source import OpenCLISource


def process(rows):
    return type("Proc", (), {"returncode": 0, "stdout": json.dumps(rows), "stderr": ""})()


class PublicExportSourceTests(unittest.TestCase):
    def setUp(self):
        self.task = {"task_id": "17", "stream": "retail-sales", "day": "2026-11-08",
                     "file_name": "科迈零售流水_2026-11-08_abc"}

    def test_status_and_matching_use_public_fields_without_source_url(self):
        source = OpenCLISource(executor=Mock(return_value=process([
            {"id": 17, "fileName": self.task["file_name"], "status": 2,
             "totalRows": 3, "download_ready": True},
        ])))
        self.assertEqual(source.export_status(self.task),
                         {"state": "complete", "task_id": "17", "source_rows": 3})

    def test_recovery_preserves_task_identity_without_source_url(self):
        source = OpenCLISource(executor=Mock(return_value=process([
            {"id": 17, "fileName": self.task["file_name"], "status": 1,
             "totalRows": None, "download_ready": False},
        ])))
        self.assertEqual(source.recover_export("retail-sales", "2026-11-08", "abc"), self.task)


if __name__ == "__main__":
    unittest.main()
