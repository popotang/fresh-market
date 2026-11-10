import json
import tempfile
import unittest
from pathlib import Path

from kemai_pipeline.artifacts import CaptureStore
from kemai_pipeline.normalize import normalize

from scripts.kemai_capture_hash_audit import audit_capture, main

DAY = '2026-11-07'
SECRET_TEXT = 'private-supplier-name-保密'


def source_row(source_id):
    return {'id': source_id, 'sheetNo': 'CG' + source_id, 'supplierId': 'sup-' + source_id,
            'supplierName': SECRET_TEXT, 'gmtCreate': DAY + ' 10:00:00', 'sumAmt': 12345}


class CaptureHashAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CaptureStore(self.root)

    def make_capture(self, rows, stream='purchase-orders'):
        state = self.store.create(tenant='205149', stream=stream, window_key=DAY,
                                  source_key_version='purchase-id-v1', mode='final')
        self.store.append_page(state, rows)
        self.store.seal(state, {'source_rows': len(rows), 'verification_method': 'test'})
        return state

    def test_all_match_produces_clean_verdict(self):
        rows = [normalize('purchase-orders', source_row(str(i))) for i in (88, 99)]
        state = self.make_capture(rows)
        report_path = self.root / 'report.json'
        report = audit_capture(self.root, state['capture_id'])
        self.assertTrue(report['passed'])
        self.assertEqual(report['rows'], 2)
        self.assertEqual(report['mismatched_rows'], 0)
        self.assertEqual(report['mismatches'], [])
        self.assertEqual(report['stream'], 'purchase-orders')
        exit_code = main(['--capture-id', state['capture_id'], '--artifact-root', str(self.root),
                          '--report', str(report_path)])
        self.assertEqual(exit_code, 0)
        written = json.loads(report_path.read_text())
        self.assertTrue(written['passed'])

    def test_recomputed_hash_mismatch_lists_keys_without_payload_dump(self):
        good = normalize('purchase-orders', source_row('88'))
        tampered = normalize('purchase-orders', source_row('99'))
        tampered['data']['payload_hash'] = '0' * 64
        tampered['data']['sheet_no'] = 'FORGED'
        state = self.make_capture([good, tampered])
        report = audit_capture(self.root, state['capture_id'])
        self.assertFalse(report['passed'])
        self.assertEqual(report['rows'], 2)
        self.assertEqual(report['mismatched_rows'], 1)
        self.assertEqual([m['source_key'] for m in report['mismatches']], ['99'])
        fields = report['mismatches'][0]['fields']
        self.assertIn('payload_hash', fields)
        self.assertIn('sheet_no', fields)
        blob = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(SECRET_TEXT, blob)
        self.assertNotIn('FORGED', blob)
        self.assertNotIn('payload', report['mismatches'][0])

    def test_malformed_capture_dir_fails_closed(self):
        missing = audit_capture(self.root, 'f' * 32)
        self.assertFalse(missing['passed'])
        self.assertEqual(missing['error'], 'malformed_capture_dir')
        state = self.make_capture([normalize('purchase-orders', source_row('88'))])
        page = self.store.path(state['capture_id']) / state['pages'][0]['file']
        page.write_text(json.dumps({'rows': []}))
        corrupted = audit_capture(self.root, state['capture_id'])
        self.assertFalse(corrupted['passed'])
        self.assertEqual(corrupted['error'], 'malformed_capture_dir')
        exit_code = main(['--capture-id', state['capture_id'], '--artifact-root', str(self.root),
                          '--report', str(self.root / 'bad.json')])
        self.assertEqual(exit_code, 2)


if __name__ == '__main__':
    unittest.main()
