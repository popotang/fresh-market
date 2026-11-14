import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from kemai_pipeline.lovrabet_adapter import ContractError
from kemai_pipeline.promote_sharing import row_hash
from tests.python.test_kemai_product_workflow import MemoryDB

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'kemai_promote_sharing_backfill', ROOT / 'scripts' / 'kemai_promote_sharing_backfill.py')
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def make_capture(root, rows):
    root = Path(root)
    capture_id = 'a' * 32
    directory = root / capture_id
    directory.mkdir()
    pages = []
    for index, chunk in enumerate((rows[:2], rows[2:]), 1):
        if not chunk:
            continue
        envelope = {'schema_version': 1, 'stream': 'retail-promote-sharing',
                    'page': index, 'observed_at': '2026-11-10T00:00:00Z', 'rows': chunk}
        name = f'page-{index:06}.json'
        (directory / name).write_text(json.dumps(envelope))
        pages.append({'file': name, 'rows': len(chunk), 'sha256': row_hash(envelope)})
    manifest = {'schema_version': 1, 'capture_id': capture_id, 'stream': 'retail-promote-sharing',
                'tenant': '205149', 'phase': 'complete', 'pages': pages}
    (directory / 'manifest.json').write_text(json.dumps(manifest))
    return directory


def source_row(suffix, **changes):
    return {'sheetId': 'order-1', 'sheetNo': 'receipt-1', 'itemCode': 'item-1',
            'type': '5', 'amt': '100', 'qty': '10000', 'saleAmt': '20000',
            'gmtSale': '2026-11-09 00:00:00', 'gmtCreate': '2026-11-09 00:00:01',
            'gmtOperation': 1794298305427, 'promotionCode': suffix, **changes}


class PromoteSharingBackfillTests(unittest.TestCase):
    def test_duplicates_are_preserved_and_replay_has_zero_requests(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_capture(root, [source_row('A'), source_row('A'),
                                            source_row('B'), source_row('C')])
            db = MemoryDB()
            result = backfill.backfill(db, directory, batch_size=2)
            self.assertEqual(result['phase'], 'complete')
            self.assertEqual(result['created_rows'], 4)
            rows = db.tables['retail_promote_shares']
            self.assertEqual(len(rows), 4)
            self.assertIsInstance(rows[0]['sold_at'], int)
            self.assertIsInstance(rows[0]['captured_at'], int)
            self.assertEqual(sorted(r['occurrence_no'] for r in rows), [1, 1, 1, 2])
            requests = db.requests
            replay = backfill.backfill(db, directory)
            self.assertEqual(replay['note'], 'completed backfill replays without any standard-API request')
            self.assertEqual(db.requests, requests)

    def test_resume_after_failure_does_not_duplicate_verified_batches(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_capture(root, [source_row('A'), source_row('B'), source_row('C')])
            db = MemoryDB()
            result = backfill.backfill(db, directory, batch_size=2, max_rows=2)
            self.assertEqual(result['phase'], 'running')
            self.assertEqual(len(db.tables['retail_promote_shares']), 2)
            result = backfill.backfill(db, directory, batch_size=2)
            self.assertEqual(result['phase'], 'complete')
            self.assertEqual(len(db.tables['retail_promote_shares']), 3)
            self.assertEqual(db.tables['retail_promote_shares'][-1]['occurrence_no'], 1)

    def test_capture_hash_mismatch_fails_before_db_access(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_capture(root, [source_row('A')])
            page = directory / 'page-000001.json'
            envelope = json.loads(page.read_text())
            envelope['rows'][0]['amt'] = '999'
            page.write_text(json.dumps(envelope))
            db = MemoryDB()
            with self.assertRaisesRegex(ValueError, 'hash or row count mismatch'):
                backfill.backfill(db, directory)
            self.assertEqual(db.requests, 0)


if __name__ == '__main__':
    unittest.main()
