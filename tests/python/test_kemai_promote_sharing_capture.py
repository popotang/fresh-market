import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'kemai_promote_sharing_capture', ROOT / 'scripts' / 'kemai_promote_sharing_capture.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class PromoteSharingCaptureTests(unittest.TestCase):
    def pages(self, values):
        calls = []

        def run(command, **kwargs):
            page = int(command[command.index('--page') + 1])
            calls.append(page)
            return SimpleNamespace(returncode=0, stdout=json.dumps(values[page]), stderr='')
        return calls, run

    def test_short_final_page_completes_and_hashes_artifacts(self):
        calls, run = self.pages({1: [{'id': 1}, {'id': 2}], 2: [{'id': 3}]})
        with tempfile.TemporaryDirectory() as root:
            result = capture.capture('2026-11-09', '2026-11-09', rows=2,
                                     max_pages=5, root=root, runner=run)
            self.assertEqual(result['phase'], 'complete')
            self.assertEqual(result['source_rows'], 3)
            self.assertEqual(calls, [1, 2])
            manifest = json.loads((Path(root) / result['capture_id'] / 'manifest.json').read_text())
            self.assertEqual(manifest['source_operations'], 2)
            self.assertEqual(len(manifest['pages']), 2)
            self.assertTrue(all(len(p['sha256']) == 64 for p in manifest['pages']))

    def test_empty_first_page_completes_empty_window(self):
        calls, run = self.pages({1: []})
        with tempfile.TemporaryDirectory() as root:
            result = capture.capture('2026-11-09', '2026-11-09', rows=100,
                                     root=root, runner=run)
            self.assertEqual(result['phase'], 'complete')
            self.assertEqual(result['source_rows'], 0)
            self.assertEqual(calls, [1])

    def test_full_page_budget_is_incomplete_not_complete(self):
        calls, run = self.pages({1: [{'id': 1}], 2: [{'id': 2}], 3: [{'id': 3}]})
        with tempfile.TemporaryDirectory() as root:
            result = capture.capture('2026-11-09', '2026-11-09', rows=1,
                                     max_pages=2, root=root, runner=run)
            self.assertEqual(result['phase'], 'incomplete')
            self.assertEqual(result['list_evidence'], 'page_budget_exhausted')
            self.assertEqual(calls, [1, 2])

    def test_failed_page_preserves_prior_artifacts(self):
        def run(command, **kwargs):
            page = int(command[command.index('--page') + 1])
            if page == 2:
                return SimpleNamespace(returncode=1, stdout='', stderr='rate limit')
            return SimpleNamespace(returncode=0, stdout='[{"id":1}]', stderr='')
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(RuntimeError):
                capture.capture('2026-11-09', '2026-11-09', rows=1, root=root, runner=run)
            capture_id = next(Path(root).iterdir()).name
            manifest = json.loads((Path(root) / capture_id / 'manifest.json').read_text())
            self.assertEqual(manifest['phase'], 'failed')
            self.assertEqual(manifest['error'], 'RuntimeError')
            self.assertEqual(len(manifest['pages']), 1)


if __name__ == '__main__':
    unittest.main()
