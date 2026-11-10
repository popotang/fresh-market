import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kemai_pipeline.artifacts import CaptureStore, ArtifactError
from kemai_pipeline.lines_only_workflow import LinesOnlyWorkflow
from kemai_pipeline.lovrabet_adapter import ContractError
from kemai_pipeline.source import SourceCooling, SourceError
from tests.python.test_kemai_pipeline_detail_workflow import (
    FakeDetailCenter, FakeDetailSource, LockProbe, make_doc, make_entity,
    pending_gap, success_state)

DAY = '2026-11-08'


class LinesOnlyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = patch('kemai_pipeline.detail_workflow.writer_lock', LockProbe())
        self.lock.start()
        self.addCleanup(self.lock.stop)
        self.center = FakeDetailCenter(documents=[make_doc()], gaps=[pending_gap()])
        self.source = FakeDetailSource({'5001': make_entity('5001')})

    def workflow(self):
        return LinesOnlyWorkflow(CaptureStore(self.root), self.source, self.center)

    def run_one(self):
        return self.workflow().begin('purchase-orders', DAY, retry_failed=True)

    def test_partial_existing_rows_do_not_prove_completion(self):
        self.center.tables['purchase_document_lines'] = [{'id': 90000, 'document_id': 101,
                                                          'source_line_id': '5001-L0'}]
        state = success_state(lines=10)
        state['status'] = 'failed'
        self.center.tables['purchase_document_detail_state'] = [state]
        result = self.run_one()
        self.assertEqual(result['processed'], 1)
        self.assertEqual(len(self.center.tables['purchase_document_lines']), 2)
        self.assertEqual(self.source.fetched, [('order', '5001')])
        self.assertTrue(result['verified'])
        self.assertFalse(result['gap_closed'])
        self.assertEqual(self.center.tables['sync_coverage_gaps'][0]['status'], 'pending')
        self.assertEqual(self.center.tables['purchase_document_branches'], [])
        self.assertEqual(self.center.tables['purchase_document_approvals'], [])

    def test_unproven_old_success_is_rechecked(self):
        self.center.tables['purchase_document_detail_state'] = [success_state()]
        plan = self.workflow().plan('purchase-orders', DAY)
        self.assertEqual(plan['eligible'], 1)
        self.assertEqual(plan['complete'], 0)
        self.assertEqual(self.run_one()['processed'], 1)

    def test_valid_receipt_skips_and_zero_lines_are_provable(self):
        self.source = FakeDetailSource({'5001': make_entity('5001', lines=0)})
        self.assertTrue(self.run_one()['verified'])
        self.assertEqual(self.run_one()['processed'], 0)
        self.assertEqual(len(self.source.fetched), 1)

    def test_changed_field_and_missing_row_are_repaired_from_snapshot(self):
        self.run_one()
        self.center.tables['purchase_document_lines'][0]['quantity'] = 999
        self.center.tables['purchase_document_lines'].pop()
        self.assertEqual(self.run_one()['processed'], 1)
        self.assertEqual(len(self.source.fetched), 1)
        self.assertEqual(len(self.center.tables['purchase_document_lines']), 2)
        self.assertNotEqual(self.center.tables['purchase_document_lines'][0]['quantity'], 999)
        self.assertEqual(self.run_one()['processed'], 0)

    def test_snapshot_is_saved_before_unknown_write_and_resume_needs_no_source(self):
        self.center.fail_upsert_once = 'purchase_document_lines'
        with self.assertRaises(ContractError):
            self.run_one()
        w = self.workflow()
        sha, snapshot = w.evidence.load('order', make_doc())
        self.assertEqual(snapshot['entity'], make_entity('5001'))
        self.assertIn('branchs', snapshot['entity'])
        self.assertIn('approves', snapshot['entity'])
        self.assertIsNone(w.evidence.receipt('order', make_doc()))
        capture_id = next(p.name for p in self.root.iterdir() if len(p.name) == 32)
        result = w.resume(capture_id)
        self.assertTrue(result['verified'])
        self.assertEqual(len(self.source.fetched), 1)
        files = list((self.root / capture_id / 'timings').glob('*.json'))
        metrics = [json.loads(p.read_text()) for p in files]
        self.assertTrue(any(m.get('outcome') == 'interrupted' for m in metrics))
        self.assertTrue(any(m.get('cache_hit') is True for m in metrics))
        self.assertTrue(all('entity' not in m for m in metrics))
        self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in self.root.rglob('*.json')))

    def test_changed_version_fetches_new_snapshot(self):
        self.run_one()
        self.center.tables['purchase_documents'][0]['source_version'] = '8'
        self.source = FakeDetailSource({'5001': make_entity('5001', version='8')})
        self.assertTrue(self.run_one()['verified'])
        self.assertEqual(len(self.source.fetched), 1)
        self.assertEqual(len(list(self.workflow().evidence.directory('order', make_doc()).glob('*.json'))), 4)

    def test_unknown_version_is_never_skipped_or_reused(self):
        self.center.tables['purchase_documents'][0]['source_version'] = None
        self.source = FakeDetailSource({'5001': make_entity('5001', version=None)})
        self.run_one()
        self.run_one()
        self.assertEqual(len(self.source.fetched), 2)

    def test_corrupted_snapshot_stops_before_source_or_writes(self):
        self.run_one()
        w = self.workflow()
        sha, _ = w.evidence.load('order', make_doc())
        (w.evidence.directory('order', make_doc()) / (sha + '.json')).write_text('{}')
        with self.assertRaises(ArtifactError):
            self.run_one()
        self.assertEqual(len(self.source.fetched), 1)

    def test_extra_old_rows_remain_unverified(self):
        self.run_one()
        self.center.tables['purchase_document_lines'].append({'id': 99999, 'document_id': 101, 'source_line_id': 'extra'})
        result = self.run_one()
        self.assertFalse(result['verified'])
        self.assertEqual(result['state'], 'incomplete')
        self.assertEqual(len(self.center.tables['purchase_document_lines']), 3)

    def test_missing_details_cannot_get_receipt(self):
        entity = make_entity('5001')
        entity.pop('details')
        self.source = FakeDetailSource({'5001': entity})
        self.assertFalse(self.run_one()['verified'])
        self.assertIsNone(self.workflow().evidence.receipt('order', make_doc()))

    def test_source_version_mismatch_keeps_snapshot_but_does_not_write(self):
        self.source = FakeDetailSource({'5001': make_entity('5001', version='8')})
        with self.assertRaises(SourceError):
            self.run_one()
        self.assertEqual(self.center.tables['purchase_document_lines'], [])
        self.assertTrue(list((self.root / 'detail-evidence').rglob('latest.json')))

    def test_snapshot_disk_failure_prevents_business_and_state_writes(self):
        with patch('kemai_pipeline.line_evidence.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(ContractError):
                self.run_one()
        self.assertEqual(self.center.tables['purchase_document_lines'], [])
        self.assertEqual(self.center.tables['purchase_document_detail_state'], [])

    def test_state_write_failure_requires_resume_before_receipt(self):
        self.center.fail_upsert_once = 'purchase_document_detail_state'
        with self.assertRaises(ContractError):
            self.run_one()
        self.assertIsNone(self.workflow().evidence.receipt('order', make_doc()))
        self.assertTrue(self.run_one()['verified'])
        self.assertEqual(len(self.source.fetched), 1)

    def test_cooling_is_resumable_and_has_timing_evidence(self):
        self.source = FakeDetailSource(errors={'5001': SourceCooling('cooling')})
        with self.assertRaises(SourceCooling):
            self.run_one()
        manifest = next(self.root.glob('*/manifest.json'))
        state = json.loads(manifest.read_text())
        self.assertEqual(state['phase'], 'capturing')
        self.assertEqual(state['last_error'], 'cooling_30_minutes')
        self.source = FakeDetailSource({'5001': make_entity('5001')})
        self.assertTrue(self.workflow().resume(state['capture_id'])['verified'])

    def test_timing_records_phases_without_business_payload(self):
        result = self.run_one()
        summaries = list((self.root / result['capture_id'] / 'timings').glob('summary-*.json'))
        summary = json.loads(summaries[0].read_text())
        self.assertEqual(summary['source_operations'], 1)
        self.assertIsNone(summary['source_http_requests'])
        self.assertGreater(summary['seconds'], 0)
        self.assertIn('source_read', summary['phases'])
        self.assertIn('children_pre_read', summary['phases'])
        self.assertIn('children_batch_create', summary['phases'])
        self.assertIn('state_write_verify', summary['phases'])
        self.assertIn('lines_verify', summary['phases'])


if __name__ == '__main__':
    unittest.main()
