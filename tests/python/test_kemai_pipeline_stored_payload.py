import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import kemai_syncctl
from kemai_pipeline.artifacts import ArtifactError, CaptureStore
from kemai_pipeline.detail_state import classify_entity
from kemai_pipeline.detail_workflow import DETAIL_METHOD, DetailWorkflow
from kemai_pipeline.source import SourceError
from kemai_pipeline.stored_payload_source import StoredPayloadSource
from tests.python.test_kemai_pipeline_detail_workflow import (FakeDetailCenter, LockProbe,
                                                              make_doc, make_entity, pending_gap)

DAY = '2026-11-08'


def stored_doc(entity, **overrides):
    doc = make_doc(**overrides)
    doc.update(payload=json.dumps(entity), payload_hash=classify_entity(entity).payload_hash)
    return doc


def stored_workflow(center, root):
    return DetailWorkflow(CaptureStore(root), StoredPayloadSource(center), center,
                          resolve_gaps=False, method='stored_payload', write_partial=True,
                          verification_scope='stored_payload', capture_control=False)


class StoredPayloadSourceTests(unittest.TestCase):
    def test_reads_only_stored_parent_payload(self):
        entity = make_entity('5001')
        center = FakeDetailCenter(documents=[stored_doc(entity)])
        source = StoredPayloadSource(center)
        self.assertEqual(source.document_detail('order', '5001'), entity)
        self.assertEqual(source.operations, 0)

    def test_identity_version_and_hash_mismatch_fail_closed(self):
        entity = make_entity('5001')
        for label, mutate in (
                ('identity', lambda doc, value: doc.update(payload=json.dumps({**value, 'id': 'other'}))),
                ('version', lambda doc, value: doc.update(payload=json.dumps({**value, 'version': '8'}))),
                ('hash', lambda doc, value: doc.update(payload_hash='0' * 64))):
            with self.subTest(label=label):
                doc = stored_doc(entity)
                mutate(doc, entity)
                center = FakeDetailCenter(documents=[doc])
                with self.assertRaises(SourceError):
                    StoredPayloadSource(center).document_detail('order', '5001')


class StoredPayloadWorkflowTests(unittest.TestCase):
    def test_partial_payload_writes_available_lines_but_never_claims_complete_or_closes_gap(self):
        entity = make_entity('5001', branches=None, approvals=0)
        del entity['approves']
        center = FakeDetailCenter(documents=[stored_doc(entity)], gaps=[pending_gap()])
        with tempfile.TemporaryDirectory() as root, patch(
                'kemai_pipeline.detail_workflow.writer_lock', LockProbe()):
            result = stored_workflow(center, root).begin('purchase-orders', DAY)
        self.assertEqual(result['state'], 'incomplete')
        self.assertFalse(result['gap_closed'])
        self.assertTrue(result['coverage_gaps_untouched'])
        self.assertEqual(result['verification_scope'], 'stored_payload')
        self.assertEqual(len(center.tables['purchase_document_lines']), 2)
        self.assertEqual(center.tables['purchase_document_branches'], [])
        self.assertEqual(center.tables['purchase_document_approvals'], [])
        state = center.tables['purchase_document_detail_state'][0]
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['line_count'], 2)
        self.assertIsNone(state['branch_count'])
        self.assertIsNone(state['approval_count'])
        self.assertIn('stored payload', state['last_error'])
        self.assertEqual(center.tables['sync_coverage_gaps'][0]['status'], 'pending')
        self.assertFalse(any(op == ('upsert', 'sync_coverage_gaps') for op in center.ops))

    def test_complete_payload_can_commit_capture_without_resolving_gap(self):
        entity = make_entity('5001')
        center = FakeDetailCenter(documents=[stored_doc(entity)], gaps=[pending_gap()])
        with tempfile.TemporaryDirectory() as root, patch(
                'kemai_pipeline.detail_workflow.writer_lock', LockProbe()):
            result = stored_workflow(center, root).begin('purchase-orders', DAY)
        self.assertEqual(result['state'], 'committed')
        self.assertFalse(result['gap_closed'])
        self.assertTrue(result['coverage_gaps_untouched'])
        self.assertEqual(center.tables['sync_coverage_gaps'][0]['status'], 'pending')
        self.assertEqual(center.tables['purchase_document_detail_state'][0]['status'], 'success')
        self.assertFalse(any(op == ('upsert', 'sync_coverage_gaps') for op in center.ops))
        self.assertFalse(any(op[0] in ('begin_capture', 'note_capture', 'fail_capture')
                             for op in center.ops))

    def test_version_mismatch_skips_stale_children_but_continues_with_current_documents(self):
        stale_entity = make_entity('5001', version='8')
        stale_doc = stored_doc(stale_entity)  # parent list version is 7; stored detail is 8
        current_entity = make_entity('5002')
        current_doc = stored_doc(current_entity, doc_id=102, source_id='5002')
        center = FakeDetailCenter(documents=[stale_doc, current_doc], gaps=[pending_gap(documents=2)])
        with tempfile.TemporaryDirectory() as root, patch(
                'kemai_pipeline.detail_workflow.writer_lock', LockProbe()):
            result = stored_workflow(center, root).begin('purchase-orders', DAY)
        self.assertEqual(result['processed'], 2)
        states = {row['document_id']: row for row in center.tables['purchase_document_detail_state']}
        self.assertEqual(states[101]['status'], 'partial')
        self.assertEqual(states[101]['last_error'], 'stored_payload_version_mismatch')
        self.assertEqual(states[101]['source_version'], '8')
        self.assertEqual(states[102]['status'], 'success')
        self.assertEqual(len(center.tables['purchase_document_lines']), 2)
        self.assertTrue(all(row['document_id'] == 102
                            for row in center.tables['purchase_document_lines']))

    def test_stored_and_live_captures_cannot_cross_resume(self):
        center = FakeDetailCenter()
        with tempfile.TemporaryDirectory() as root:
            store = CaptureStore(root)
            live = store.create(tenant='t', stream='purchase-detail:order', window_key=DAY,
                                source_key_version='purchase-id-v1', mode='final',
                                method=DETAIL_METHOD)
            stored = store.create(tenant='t', stream='purchase-detail:order', window_key=DAY,
                                  source_key_version='purchase-id-v1', mode='final',
                                  method='stored_payload')
            with patch('kemai_pipeline.detail_workflow.writer_lock', LockProbe()):
                with self.assertRaises(ArtifactError):
                    stored_workflow(center, root).resume(live['capture_id'])
                live_workflow = DetailWorkflow(store, StoredPayloadSource(center), center)
                with self.assertRaises(ArtifactError):
                    live_workflow.resume(stored['capture_id'])
        self.assertEqual(center.ops, [])


class StoredPayloadCliTests(unittest.TestCase):
    class Adapter(FakeDetailCenter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.requests = 0

    def invoke(self, argv, adapter):
        output = StringIO()
        with redirect_stdout(output):
            code = kemai_syncctl.main(argv, adapter=adapter)
        return code, json.loads(output.getvalue())

    def test_run_uses_zero_source_operations_and_never_constructs_opencli_source(self):
        entity = make_entity('5001')
        adapter = self.Adapter(documents=[stored_doc(entity)], gaps=[pending_gap()])
        with patch('kemai_pipeline.detail_workflow.writer_lock', LockProbe()), patch(
                'kemai_syncctl.OpenCLISource', side_effect=AssertionError('OpenCLI must not be used')):
            code, output = self.invoke(['stored-detail', 'run', '--stream', 'purchase-orders',
                                        '--day', DAY], adapter)
        self.assertEqual(code, 0, output)
        self.assertEqual(output['source_requests'], 0)
        self.assertEqual(output['source_operations'], 0)
        self.assertIsNone(output['db_writes'])
        self.assertEqual(output['result']['state'], 'committed')
        self.assertTrue(output['result']['coverage_gaps_untouched'])

    def test_plan_and_verify_are_read_only(self):
        entity = make_entity('5001')
        adapter = self.Adapter(documents=[stored_doc(entity)])
        for action in ('plan', 'verify'):
            with self.subTest(action=action):
                code, output = self.invoke(['stored-detail', action, '--stream', 'purchase-orders',
                                            '--day', DAY], adapter)
                self.assertEqual(code, 2 if action == 'verify' else 0, output)
                self.assertTrue(output['ok'])
                self.assertEqual(output['source_requests'], 0)
                self.assertEqual(output['source_operations'], 0)
                self.assertEqual(output['db_writes'], 0)


if __name__ == '__main__':
    unittest.main()
