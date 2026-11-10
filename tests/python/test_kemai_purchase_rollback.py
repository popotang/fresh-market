import copy
import json
import unittest

from scripts.verify_kemai_purchase_rollback import (
    assert_validation_rollback,
    snapshot_validation_state,
)


DAY = '2026-11-07'
STREAM = 'purchase-orders'
CAPTURE_ID = 'c' * 32
BATCH_ID = 'b' * 64


def snapshot():
    return {
        'business': {'id': 1, 'document_kind': 'order', 'source_document_id': '88',
                     'payload': 'private-business'},
        'raw_capture': [{'id': 2, 'stream': STREAM, 'source_key': '88',
                         'payload': 'private-raw'}],
        'captures': [],
        'batches': [],
        'windows': [],
        'controller': [{'id': 3, 'stream': STREAM, 'active_capture_id': 'old'}],
        'status': [{'id': 4, 'stream': STREAM, 'last_attempt_status': 'success'}],
        'events': [{'id': 5, 'stream': STREAM, 'source_from': DAY, 'source_to': DAY,
                    'status': 'success'}],
    }


class FakeCenter:
    def __init__(self, rows):
        self.rows = rows
        self.reads = []

    @staticmethod
    def _matches(row, where):
        return all(all(op == '$eq' and row.get(key) == value for op, value in filters.items())
                   for key, filters in where.items())

    def _read(self, table, where):
        self.reads.append((table, where))
        return [copy.deepcopy(row) for row in self.rows.get(table, [])
                if self._matches(row, where)]

    def page(self, table, *, where, size=200):
        rows = self._read(table, where)
        return rows[:size], len(rows)

    def read_all(self, table, *, where, select=None):
        return self._read(table, where)

    def invoke(self, *args, **kwargs):
        raise AssertionError('snapshot must not invoke BFF or Kemai')


class RollbackSnapshotTests(unittest.TestCase):
    def test_validate_rollback_accepts_unchanged_business_raw_and_control_state(self):
        before = snapshot()
        checks = assert_validation_rollback(before, copy.deepcopy(before))
        self.assertTrue(all(checks.values()))
        self.assertNotIn('private-business', json.dumps(checks))
        self.assertNotIn('private-raw', json.dumps(checks))

    def test_source_capture_created_by_failed_validation_fails_audit(self):
        before = snapshot()
        before['raw_capture'] = []
        after = copy.deepcopy(before)
        after['raw_capture'] = [{'id': 99, 'payload': 'must-rollback'}]
        with self.assertRaisesRegex(RuntimeError, 'raw_capture_unchanged'):
            assert_validation_rollback(before, after)

    def test_random_batch_receipt_left_behind_fails_audit(self):
        before = snapshot()
        after = copy.deepcopy(before)
        after['batches'] = [{'batch_id': BATCH_ID}]
        with self.assertRaisesRegex(RuntimeError, 'random_batch_absent'):
            assert_validation_rollback(before, after)

    def test_stream_status_or_event_change_fails_audit(self):
        for field in ('status', 'events', 'controller'):
            with self.subTest(field=field):
                before = snapshot()
                after = copy.deepcopy(before)
                after[field][0]['changed'] = True
                with self.assertRaises(RuntimeError):
                    assert_validation_rollback(before, after)

    def test_snapshot_uses_standard_adapter_reads_and_never_kemai(self):
        center = FakeCenter({
            'purchase_documents': [snapshot()['business']],
            'source_capture': snapshot()['raw_capture'],
            'sync_pipeline_streams': snapshot()['controller'],
            'sync_stream_status': snapshot()['status'],
            'sync_stream_events': snapshot()['events'],
        })
        value = snapshot_validation_state(
            center, stream=STREAM, day=DAY, source_key='88',
            capture_id=CAPTURE_ID, batch_id=BATCH_ID)
        self.assertEqual(value['business']['source_document_id'], '88')
        self.assertEqual(value['raw_capture'][0]['source_key'], '88')
        self.assertEqual(value['captures'], [])
        self.assertEqual(value['batches'], [])
        self.assertEqual(value['windows'], [])
        tables = {table for table, _where in center.reads}
        self.assertEqual(tables, {
            'purchase_documents', 'source_capture', 'sync_pipeline_captures',
            'sync_pipeline_batches', 'sync_pipeline_windows',
            'sync_pipeline_streams', 'sync_stream_status', 'sync_stream_events',
        })


if __name__ == '__main__':
    unittest.main()
