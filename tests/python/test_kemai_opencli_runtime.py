import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts import kemai_opencli_runtime as runtime


class OpenCLIRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kemai-opencli-runtime-")
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.installed = self.root / "installed"
        self.gate = self.root / "gate"
        self.backups = self.root / "backups"
        self.source.mkdir()
        self.gate.mkdir()
        self.manifest = {
            "schema_version": 1,
            "adapter": "kemai",
            "adapter_version": "1.0.0",
            "runtime_requirements": {
                "node_minimum_major": 20,
                "opencli_version": "1.8.8",
            },
            "commands": [],
        }
        self.write_source("manifest.json", json.dumps(self.manifest))
        self.write_source("adapter.js", "export const value = 'project';\n")
        self.write_source("capabilities.js", "export const staticOnly = true;\n")
        self.gate_config = {
            "script_path": str(self.root / "private-gate.py"),
            "legacy_rate_status": str(self.gate / "legacy-rate.json"),
        }
        self.write(self.gate / "config.json", json.dumps(self.gate_config))
        self.write(self.gate / "source-state.json", json.dumps({
            "last_label": "offline-test",
            "last_pid": 123,
            "last_started_epoch": 10,
            "last_finished_epoch": 11,
        }))
        self.write(self.gate / "rate-limit-status.json", json.dumps({
            "resume_after_epoch": 4102444800,
            "resume_after": "2100-01-01T00:00:00+08:00",
        }))
        self.write(self.gate / "legacy-rate.json", "{}")
        self.node = self.executable("node", "v20.11.1\\n")
        self.opencli = self.executable("opencli", "1.8.8\\n")

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def write_source(self, name, content):
        self.write(self.source / name, content)

    def executable(self, name, output):
        path = self.root / name
        self.write(path, f"#!/bin/sh\nprintf '{output}'\n")
        path.chmod(0o700)
        return str(path)

    def check(self):
        return runtime.check_runtime(
            source_dir=self.source,
            runtime_dir=self.installed,
            gate_dir=self.gate,
            node_bin=self.node,
            opencli_bin=self.opencli,
        )

    def install(self):
        return runtime.install_runtime(
            source_dir=self.source,
            runtime_dir=self.installed,
            gate_dir=self.gate,
            backup_root=self.backups,
            node_bin=self.node,
            opencli_bin=self.opencli,
        )

    def install_consistent_files(self):
        shutil.copytree(self.source, self.installed)

    def test_consistent_files_report_versions_and_unchecked_source_acceptance(self):
        self.install_consistent_files()
        report = self.check()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["files"]["missing"], [])
        self.assertEqual(report["files"]["extra"], [])
        self.assertEqual(report["files"]["changed"], [])
        self.assertEqual(report["node"]["version"], "v20.11.1")
        self.assertEqual(report["opencli"]["version"], "1.8.8")
        self.assertEqual(report["gate"]["cooldown"]["state"], "cooling")
        self.assertEqual(report["gate"]["cooldown"]["resume_after_epoch"], 4102444800.0)
        self.assertEqual(report["gate"]["source_state"]["last_label"], "offline-test")
        self.assertIsNone(report["authentication"])
        self.assertEqual(report["source_acceptance"], "not_checked")
        self.assertEqual(report["source_requests"], 0)

    def test_missing_file_reports_drift(self):
        self.install_consistent_files()
        (self.installed / "adapter.js").unlink()
        report = self.check()
        self.assertFalse(report["ok"])
        self.assertEqual(report["files"]["missing"], ["adapter.js"])

    def test_unknown_extra_file_reports_drift(self):
        self.install_consistent_files()
        self.write(self.installed / "unknown.js", "unknown\n")
        report = self.check()
        self.assertFalse(report["ok"])
        self.assertEqual(report["files"]["extra"], ["unknown.js"])

    def test_changed_file_reports_expected_and_actual_sha256(self):
        self.install_consistent_files()
        changed = b"export const value = 'runtime-drift';\n"
        (self.installed / "adapter.js").write_bytes(changed)
        report = self.check()
        self.assertFalse(report["ok"])
        self.assertEqual(report["files"]["changed"], [{
            "path": "adapter.js",
            "expected_sha256": hashlib.sha256((self.source / "adapter.js").read_bytes()).hexdigest(),
            "actual_sha256": hashlib.sha256(changed).hexdigest(),
        }])

    def test_install_refuses_unknown_extra_without_modifying_runtime_or_gate(self):
        self.install_consistent_files()
        self.write(self.installed / "unknown.txt", "keep\n")
        before = (self.installed / "adapter.js").read_bytes()
        gate_before = (self.gate / "config.json").read_bytes()
        with self.assertRaisesRegex(runtime.RuntimeInstallError, "unknown runtime extra"):
            self.install()
        self.assertEqual((self.installed / "adapter.js").read_bytes(), before)
        self.assertEqual((self.installed / "unknown.txt").read_text(), "keep\n")
        self.assertEqual((self.gate / "config.json").read_bytes(), gate_before)
        self.assertFalse(self.backups.exists())

    def test_install_refuses_same_source_and_runtime_path(self):
        with self.assertRaisesRegex(runtime.RuntimeInstallError, "same path"):
            runtime.install_runtime(
                source_dir=self.source,
                runtime_dir=self.source,
                gate_dir=self.gate,
                backup_root=self.backups,
                node_bin=self.node,
                opencli_bin=self.opencli,
            )

    def test_install_refuses_unreadable_runtime_digest(self):
        self.install_consistent_files()
        (self.installed / "adapter.js").unlink()
        (self.installed / "adapter.js").mkdir()
        gate_before = (self.gate / "config.json").read_bytes()
        with self.assertRaisesRegex(runtime.RuntimeInstallError, "digest"):
            self.install()
        self.assertTrue((self.installed / "adapter.js").is_dir())
        self.assertEqual((self.gate / "config.json").read_bytes(), gate_before)

    def test_install_backs_up_changed_files_and_becomes_consistent(self):
        self.install_consistent_files()
        old = b"export const value = 'old-runtime';\n"
        (self.installed / "adapter.js").write_bytes(old)
        (self.installed / "capabilities.js").unlink()
        gate_before = (self.gate / "config.json").read_bytes()

        report = self.install()

        self.assertTrue(report["ok"], report)
        self.assertEqual((self.installed / "adapter.js").read_bytes(), (self.source / "adapter.js").read_bytes())
        self.assertEqual((self.installed / "capabilities.js").read_bytes(), (self.source / "capabilities.js").read_bytes())
        backup = Path(report["backup"]["path"])
        self.assertEqual((backup / "adapter.js").read_bytes(), old)
        self.assertFalse((backup / "capabilities.js").exists())
        evidence = json.loads((backup / "backup-manifest.json").read_text())
        adapter_evidence = next(row for row in evidence["files"] if row["path"] == "adapter.js")
        self.assertEqual(adapter_evidence["sha256"], hashlib.sha256(old).hexdigest())
        self.assertEqual((self.gate / "config.json").read_bytes(), gate_before)
        self.assertTrue(self.check()["ok"])

    def test_install_rejects_non_javascript_or_json_source_entries(self):
        self.write_source("notes.txt", "not installable\n")
        self.install_consistent_files()
        self.write(self.installed / "notes.txt", "old\n")
        with self.assertRaisesRegex(runtime.RuntimeInstallError, "unknown runtime extra"):
            self.install()
        self.assertEqual((self.installed / "notes.txt").read_text(), "old\n")


if __name__ == "__main__":
    unittest.main()
