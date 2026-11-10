import copy
import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from kemai_pipeline.artifacts import CaptureStore
from kemai_pipeline.capabilities import capture_method
from kemai_pipeline.export_mappings import HISTORY_EXPORT_FIELDS, MAPPING_VERSION
from kemai_pipeline.mapping_verification import canonical_artifact_digest
from kemai_pipeline.normalize import dump
from kemai_pipeline.operator import _execute_tasks, history_plan, plan_incremental
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter
from kemai_pipeline.source import SourceCooling, SourceError
from kemai_pipeline.workflow import WindowWorkflow, resolve_capture_method


CAPTURE_ID = 'a' * 32


def task(stream='retail-sales', day='2026-11-08', mode='final'):
    return {'stream': stream, 'day': day, 'mode': mode,
            'method': capture_method(stream, provisional=mode == 'provisional')}


class FakeCenter:
    config = {'tenant_id': 't'}

    def __init__(self, active=None):
        self.active = active or []
        self.reads = []
        self.begun = []
        self.bound = []
        self.failed = []

    def read_all(self, table, *, where, select=None):
        self.reads.append((table, where))
        assert table == 'sync_pipeline_captures'
        rows = copy.deepcopy(self.active)
        if select:
            fields = dict.fromkeys(['id', 'tenant_id', *select])
            rows = [{key: row[key] for key in fields if key in row} for row in rows]
        return rows

    def gateway(self, *args, **kwargs):
        raise AssertionError('active recovery must use stream-wide standard API reads')

    def begin_capture(self, state):
        self.begun.append(state['capture_id'])

    def bind_capture(self, state):
        self.bound.append(state['capture_id'])

    def fail_capture(self, state, reason):
        self.failed.append((state['capture_id'], reason))


class StrictCaptureCenter(FakeCenter):
    def fail_capture(self, state, reason):
        scope = LovrabetAdapter.capture_scope(state)
        self.failed.append((scope, reason))


class FakeWorkflow:
    def __init__(self, store, source, center, *, resume_result=None, begin_results=None):
        self.resume_result = resume_result or {'state': 'committed', 'capture_id': CAPTURE_ID}
        self.begin_results = list(begin_results or [])
        self.resumed = []
        self.begun = []
        self.source = source

    def resume(self, capture_id):
        self.resumed.append(capture_id)
        return dict(self.resume_result)

    def begin(self, stream, day, *, mode='final', method=None):
        self.begun.append((stream, day, mode, method))
        result = self.begin_results.pop(0) if self.begin_results else {
            'state': 'committed', 'capture_id': CAPTURE_ID}
        if isinstance(result, Exception):
            raise result
        return dict(result)


class OperatorRecoveryTests(unittest.TestCase):
    def run_tasks(self, center, tasks, workflow, artifact_root=None):
        source = Mock(operations=0)
        workflow.source = source
        with patch('kemai_pipeline.operator.WindowWorkflow',
                   return_value=workflow):
            result = _execute_tasks(center, tasks, artifact_root=artifact_root or tempfile.mkdtemp(), source=source)
        return result

    def test_active_older_capture_resumes_before_new_work_without_completing_request(self):
        center = FakeCenter([{'capture_id': CAPTURE_ID, 'window_key': '2026-11-06', 'state': 'running'}])
        with tempfile.TemporaryDirectory() as root:
            store = CaptureStore(root)
            store.create(tenant='t', stream='retail-sales', window_key='2026-11-06',
                         source_key_version='source-id-v1', mode='final')
            # Use the actual active ID for the local manifest.
            active_id = next(Path(root).iterdir()).name
            center.active[0]['capture_id'] = active_id
            workflow = FakeWorkflow(None, None, center,
                                    resume_result={'state': 'committed', 'capture_id': active_id})
            result = self.run_tasks(center, [task(day='2026-11-08')], workflow, artifact_root=root)
            self.assertEqual(workflow.resumed, [active_id])
            self.assertEqual(workflow.begun, [])
            row = result['results'][0]
            self.assertFalse(row['requested_day_committed'])
            self.assertTrue(row['deferred'])
            self.assertFalse(result['complete'])
        self.assertEqual(center.reads[0][1], {
            'stream': {'$eq': 'retail-sales'}, 'state': {'$eq': 'running'}})

    def test_missing_manifest_releases_active_capture_with_actual_adapter_scope(self):
        capture = {'capture_id': CAPTURE_ID, 'window_key': '2026-11-06', 'state': 'running',
                   'stream': 'retail-sales', 'mode': 'final', 'source_key_version': 'source-id-v1'}
        center = StrictCaptureCenter([capture])
        workflow = FakeWorkflow(None, None, center)
        with tempfile.TemporaryDirectory() as empty_root:
            result = self.run_tasks(center, [task(day='2026-11-08')], workflow,
                                    artifact_root=empty_root)
        self.assertEqual(result['results'][0]['state'], 'interrupted')
        self.assertEqual(center.failed, [({
            'stream': 'retail-sales', 'capture_id': CAPTURE_ID,
            'window_key': '2026-11-06', 'mode': 'final',
            'source_key_version': 'source-id-v1'},
            'artifact_missing_restart_with_new_capture')])
        self.assertEqual(workflow.resumed, [])

    def test_resume_same_requested_day_can_complete(self):
        center = FakeCenter([{'capture_id': CAPTURE_ID, 'window_key': '2026-11-08', 'state': 'running'}])
        with tempfile.TemporaryDirectory() as root:
            store = CaptureStore(root)
            state = store.create(tenant='t', stream='retail-sales', window_key='2026-11-08',
                                 source_key_version='source-id-v1', mode='final')
            center.active[0]['capture_id'] = state['capture_id']
            workflow = FakeWorkflow(None, None, center, resume_result={
                'state': 'committed', 'capture_id': state['capture_id']})
            result = self.run_tasks(center, [task()], workflow, artifact_root=root)
            self.assertEqual(workflow.resumed, [state['capture_id']])
            self.assertEqual(workflow.begun, [])
        self.assertTrue(result['results'][0]['requested_day_committed'])
        self.assertFalse(result['results'][0]['deferred'])
        self.assertTrue(result['complete'])

    def test_deferred_results_cover_every_task_skipped_after_stream_interruption(self):
        center = FakeCenter()
        workflow = FakeWorkflow(None, None, center, begin_results=[
            {'state': 'interrupted', 'capture_id': CAPTURE_ID}])
        result = self.run_tasks(center, [task(day='2026-11-06'), task(day='2026-11-07')], workflow)
        self.assertEqual(len(result['results']), 2)
        self.assertEqual(result['results'][1]['requested_day'], '2026-11-07')
        self.assertTrue(result['results'][1]['deferred'])
        self.assertEqual(result['results'][1]['state'], 'deferred')
        self.assertFalse(result['complete'])

    def test_one_stream_failure_does_not_block_another(self):
        center = FakeCenter()
        workflow = FakeWorkflow(None, None, center, begin_results=[
            SourceError('bad source'), {'state': 'committed', 'capture_id': CAPTURE_ID}])
        result = self.run_tasks(center, [task(), task('retail-orders')], workflow)
        self.assertEqual(result['results'][0]['state'], 'failed')
        self.assertTrue(result['results'][1]['requested_day_committed'])

    def test_cooldown_defers_all_remaining_streams(self):
        center = FakeCenter()
        workflow = FakeWorkflow(None, None, center, begin_results=[SourceCooling('cooling')])
        result = self.run_tasks(center, [task(), task('retail-orders')], workflow)
        self.assertEqual(len(result['results']), 2)
        self.assertEqual(result['results'][0]['state'], 'cooling')
        self.assertTrue(all(row['deferred'] for row in result['results']))
        self.assertEqual(result['results'][1]['state'], 'deferred')


class PlanningGapReadTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 18, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.state = {'baseline_verified': True, 'baseline_from': '2026-08-01',
                      'last_contiguous_verified_day': '2026-11-16',
                      'covered_days': [], 'holes': []}

    def plan(self, center):
        with patch('kemai_pipeline.operator.stream_state', return_value=self.state), \
             patch('kemai_pipeline.operator.cooldown_until', return_value=0):
            return plan_incremental(center, ['retail-sales'], max_days=1, now=self.now)

    def test_gap_reads_are_stream_scoped_and_count_is_separate_from_details(self):
        center = Mock()
        center.count.return_value = 1
        center.read_all.return_value = [{'stream': 'retail-sales', 'scope_key': 'detail:1',
                                        'missing_parts': ['detail'], 'status': 'pending'}]
        result = self.plan(center)
        expected_where = {'stream': {'$eq': 'retail-sales'}, 'status': {'$eq': 'pending'}}
        center.count.assert_called_once_with('sync_coverage_gaps', where=expected_where)
        center.read_all.assert_called_once()
        self.assertEqual(center.read_all.call_args.kwargs['where'], expected_where)
        self.assertEqual(result['coverage_gap_counts'], {'retail-sales': 1})
        self.assertTrue(result['coverage_gaps_complete'])
        self.assertEqual(result['coverage_gaps_omitted'], 0)

    def test_oversized_gap_details_are_omitted_explicitly_without_full_table_read(self):
        center = Mock()
        center.count.return_value = 10001
        result = self.plan(center)
        center.read_all.assert_not_called()
        self.assertEqual(result['coverage_gap_counts'], {'retail-sales': 10001})
        self.assertEqual(result['coverage_gaps'], [])
        self.assertFalse(result['coverage_gaps_complete'])
        self.assertEqual(result['coverage_gaps_omitted'], 10001)
        self.assertIn({'stream': 'retail-sales', 'reason': 'coverage_gap_details_over_budget',
                       'count': 10001, 'detail_limit': 1000}, result['blocked'])


class CaptureMethodTests(unittest.TestCase):
    def test_history_plan_emits_method_reason_and_zero_io(self):
        result = history_plan('retail-sales', date(2026, 11, 6), date(2026, 11, 7))
        self.assertTrue(all(row['method'] == 'api_for_data+export_for_count' for row in result['tasks']))
        self.assertTrue(all(row['reason'] == 'explicit_history_backfill' for row in result['tasks']))
        self.assertEqual(result['source_requests'], 0)
        self.assertEqual(result['db_writes'], 0)

    def test_latest_closed_day_revisit_is_removed_from_outage_backlog(self):
        center = Mock()
        center.count.return_value = 0
        center.read_all.return_value = []
        state = {'baseline_verified': True, 'baseline_from': '2026-08-01',
                 'last_contiguous_verified_day': '2026-11-07', 'covered_days': [], 'holes': []}
        now = datetime(2026, 11, 18, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        with patch('kemai_pipeline.operator.stream_state', return_value=state), \
             patch('kemai_pipeline.operator.cooldown_until', return_value=0):
            result = plan_incremental(center, ['retail-sales'], max_days=1, now=now)
        revisit = [row for row in result['tasks'] if row.get('reason') == 'late_arrival_revisit']
        self.assertEqual([row['day'] for row in revisit], ['2026-11-17'])
        self.assertNotIn('2026-11-17', [row['day'] for row in result['planning_pending']])
        self.assertEqual(result['backlog_remaining'], len(result['planning_pending']))

    def test_cooling_defers_latest_closed_day_revisit_without_outage_backlog(self):
        center = Mock()
        center.count.return_value = 0
        state = {'baseline_verified': True, 'baseline_from': '2026-08-01',
                 'last_contiguous_verified_day': '2026-11-17',
                 'covered_days': ['2026-11-17'], 'holes': []}
        now = datetime(2026, 11, 18, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        cooling = now + timedelta(minutes=30)
        with patch('kemai_pipeline.operator.stream_state', return_value=state), \
             patch('kemai_pipeline.operator.cooldown_until', return_value=cooling.timestamp()):
            result = plan_incremental(center, ['retail-sales'], max_days=1, now=now)
        self.assertEqual(result['status'], 'cooling')
        self.assertEqual(result['tasks'], [])
        revisit = [row for row in result['deferred_tasks']
                   if row.get('reason') == 'late_arrival_revisit']
        self.assertEqual([row['day'] for row in revisit], ['2026-11-17'])
        self.assertEqual(result['planning_pending'], [])
        self.assertEqual(result['backlog_remaining'], 0)

    def test_export_for_data_fails_closed_before_capture_creation_or_page_fallback(self):
        with self.assertRaisesRegex(SourceError, 'EXECUTION_METHOD_NOT_IMPLEMENTED'):
            resolve_capture_method('purchase-receive-history', 'final', 'export_for_data')

    def test_final_retail_method_cannot_drop_export_count(self):
        for method in ('api_only', 'api_only_provisional'):
            with self.subTest(method=method):
                with self.assertRaisesRegex(SourceError, 'CAPTURE_METHOD_CONFLICT'):
                    resolve_capture_method('retail-sales', 'final', method)

    def test_begin_and_resume_validate_method_before_data_center_or_source(self):
        with tempfile.TemporaryDirectory() as root:
            store = CaptureStore(root)
            source = Mock()
            center = FakeCenter()
            workflow = WindowWorkflow(store, source, center)
            with self.assertRaisesRegex(SourceError, 'EXECUTION_METHOD_NOT_IMPLEMENTED'):
                workflow.begin('purchase-receive-history', '2026-11-06', method='export_for_data')
            self.assertEqual(list(Path(root).iterdir()), [])
            self.assertEqual(center.begun, [])
            state = store.create(tenant='t', stream='purchase-receive-history', window_key='2026-11-06',
                                 source_key_version='history-decimal-sequence-v2', mode='final',
                                 method='export_for_data')
            with self.assertRaisesRegex(SourceError, 'EXECUTION_METHOD_NOT_IMPLEMENTED'):
                workflow.resume(state['capture_id'])
            self.assertEqual(center.begun, [])
            source.pages.assert_not_called()

    def test_new_manifest_persists_method_and_legacy_manifest_uses_default_without_rewrite(self):
        with tempfile.TemporaryDirectory() as root:
            store = CaptureStore(root)
            state = store.create(tenant='t', stream='retail-sales', window_key='2026-11-06',
                                 source_key_version='source-id-v1', mode='final',
                                 method='api_for_data+export_for_count')
            self.assertEqual(store.load(state['capture_id'])['method'], 'api_for_data+export_for_count')
            legacy = store.create(tenant='t', stream='retail-orders', window_key='2026-11-06',
                                  source_key_version='source-id-v1', mode='final')
            path = store.path(legacy['capture_id']) / 'manifest.json'
            value = json.loads(path.read_text())
            value.pop('method', None)
            original = json.dumps(value, sort_keys=True)
            path.write_text(original)
            self.assertEqual(resolve_capture_method('retail-orders', 'final', value.get('method')),
                             'api_for_data+export_for_count')
            self.assertEqual(path.read_text(), original)

    def test_caller_supplied_true_is_ignored(self):
        # 调用方显式传 export_mapping_verified=True 不再有任何作用：无工件仍 fail-closed。
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(SourceError, 'EXECUTION_METHOD_NOT_IMPLEMENTED'):
                resolve_capture_method('purchase-receive-history', 'final', 'export_for_data',
                                       root=root, export_mapping_verified=True)
            # 传 True 也不能把默认方法解锁为 export_for_data（回落到既有 api_only 默认）。
            self.assertEqual(resolve_capture_method('purchase-receive-history', 'final',
                                                    root=root, export_mapping_verified=True),
                             'api_only')

    def test_valid_artifact_enables_export_for_data(self):
        with tempfile.TemporaryDirectory() as root:
            artifact = {'schema_version': 1, 'stream': 'purchase-receive-history',
                        'mapping_version': MAPPING_VERSION,
                        'header_sha256': hashlib.sha256(
                            dump(list(HISTORY_EXPORT_FIELDS.keys())).encode()).hexdigest(),
                        'sample_rows': 2, 'key_coverage': 1.0,
                        'verifier': 'scripts/verify_kemai_history_export_mapping.py',
                        'verified_at': '2026-11-09T10:00:00+08:00'}
            artifact['artifact_sha256'] = canonical_artifact_digest(artifact)
            directory = Path(root) / 'export-mapping'
            directory.mkdir()
            (directory / 'purchase-receive-history.json').write_text(
                json.dumps(artifact, ensure_ascii=False))
            self.assertEqual(resolve_capture_method('purchase-receive-history', 'final', root=root),
                             'export_for_data')
            self.assertEqual(resolve_capture_method('purchase-receive-history', 'final',
                                                    'export_for_data', root=root),
                             'export_for_data')
            # stale 工件（mapping_version 漂移）立即回到 fail-closed
            artifact['mapping_version'] = 'history-export-mapping-v0'
            artifact['artifact_sha256'] = canonical_artifact_digest(artifact)
            (directory / 'purchase-receive-history.json').write_text(
                json.dumps(artifact, ensure_ascii=False))
            with self.assertRaisesRegex(SourceError, 'EXECUTION_METHOD_NOT_IMPLEMENTED'):
                resolve_capture_method('purchase-receive-history', 'final', 'export_for_data', root=root)


if __name__ == '__main__':
    unittest.main()
