"""Offline concurrency checks for the shared Kemai source gate."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "kemai_source_gate.py"


class SourceGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.env = {**os.environ,
                    "KEMAI_SOURCE_GATE_DIR": str(self.directory),
                    "KEMAI_SOURCE_GATE_INTERVAL_SECONDS": "0.08",
                    "KEMAI_SOURCE_GATE_SCRIPT": str(SCRIPT),
                    "KEMAI_RATE_LIMIT_STATE": str(self.directory / "legacy-rate.json"),
                    "KEMAI_RATE_LIMIT_LOG": str(self.directory / "rate-limit.log")}
        (self.directory / "config.json").write_text(json.dumps({
            "legacy_rate_status": str(self.directory / "legacy-rate.json"),
        }))

    def test_processes_are_serial_and_spaced(self):
        program = ("import json,time; from kemai_source_gate import source_lease; "
                   "\nwith source_lease('offline-test'):"
                   "\n start=time.time(); time.sleep(0.04); end=time.time();"
                   " print(json.dumps([start,end]),flush=True)")
        workers = [subprocess.Popen([sys.executable, "-c", program], cwd=ROOT,
                                    env=self.env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
                   for _ in range(3)]
        periods = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=10)
            self.assertEqual(worker.returncode, 0, stderr)
            periods.append(json.loads(stdout))
        periods.sort()
        for previous, current in zip(periods, periods[1:]):
            self.assertGreaterEqual(current[0] - previous[0], 0.065)
            self.assertGreaterEqual(current[0] - previous[1], 0.065)

    def test_existing_cooldown_delays_next_lease(self):
        resume = time.time() + 0.15
        (self.directory / "legacy-rate.json").write_text(json.dumps({
            "resume_after_epoch": resume,
        }))
        started = time.time()
        result = subprocess.run([sys.executable, str(SCRIPT), "lease", "offline-cooldown"],
                                input="", capture_output=True, text=True,
                                env=self.env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("READY", result.stdout)
        self.assertGreaterEqual(time.time() - started, 0.12)

    def test_manual_wrapper_rejects_nested_adapter(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "run", "--",
                                 "opencli", "kemai", "retail-sales"],
                                capture_output=True, text=True, env=self.env, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already uses this gate", result.stderr)

    def test_node_adapter_shares_gate_and_records_business_limit(self):
        program = ("import {withSourceLease} from './kemai_opencli/source-gate.js';"
                   "await withSourceLease('offline-node', async()=>{"
                   "console.log('LEASED'); throw new Error('您的操作太频繁，请稍后再试');});")
        result = subprocess.run(["node", "--input-type=module", "-e", program], cwd=ROOT,
                                capture_output=True, text=True, env=self.env, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LEASED", result.stdout)
        state = json.loads((self.directory / "legacy-rate.json").read_text())
        self.assertEqual(state["state"], "cooling")
        self.assertEqual(state["cooldown_seconds"], 1800)

    def test_python_and_node_cannot_overlap(self):
        python_program = ("import json,time; from kemai_source_gate import source_lease;"
                          "\nwith source_lease('offline-python'):"
                          "\n start=time.time(); time.sleep(0.05);"
                          " print(json.dumps([start,time.time()]),flush=True)")
        node_program = ("import {withSourceLease} from './kemai_opencli/source-gate.js';"
                        "await withSourceLease('offline-node',async()=>{"
                        "const start=Date.now()/1000;"
                        "await new Promise(resolve=>setTimeout(resolve,50));"
                        "console.log(JSON.stringify([start,Date.now()/1000]));});")
        workers = [subprocess.Popen([sys.executable, "-c", python_program], cwd=ROOT,
                                    env=self.env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True),
                   subprocess.Popen(["node", "--input-type=module", "-e", node_program],
                                    cwd=ROOT, env=self.env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)]
        periods = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=10)
            self.assertEqual(worker.returncode, 0, stderr)
            periods.append(json.loads(stdout))
        periods.sort()
        self.assertGreaterEqual(periods[1][0] - periods[0][0], 0.065)
        self.assertGreaterEqual(periods[1][0] - periods[0][1], 0.065)


if __name__ == "__main__":
    unittest.main()
