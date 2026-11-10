import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import kemai_syncctl
from kemai_pipeline.lovrabet_adapter import ContractError


class FakeAdapter:
    def __init__(self):
        self.config = {'appcode': 'app-test', 'tenant_id': 't'}
        self.requests = 0
        self.durable_windows = [{
            'stream': 'retail-sales', 'window_key': '2026-08-01', 'row_count': 0,
            'evidence': {'window_time_field': 'source_created_at', 'source_rows': 0,
                         'verification_method': 'empty_page_and_db'}}]

    def gateway(self, operation, **kwargs):
        self.requests += 1
        return {'writes_enabled': True, 'accepted_streams': ['retail-sales']}

    def count(self, table, *, where):
        self.requests += 1
        return 0

    def read_all(self, table, *, where=None, select=None):
        self.requests += 1
        if table == 'sync_pipeline_windows' and where and 'coverage_scope' in where:
            return self.durable_windows
        if table == 'sync_stream_status':
            return [{'stream': 'retail-sales', 'covered_through': '2026-11-07',
                     'last_success_at': '2026-11-08 01:00:00'}]
        return []


def invoke(argv, adapter):
    output = io.StringIO()
    with redirect_stdout(output):
        code = kemai_syncctl.main(argv, adapter=adapter)
    return code, json.loads(output.getvalue())


class SyncctlTests(unittest.TestCase):
    def test_capabilities_lists_abandon_without_claiming_source_acceptance(self):
        code, output = invoke(['capabilities'], FakeAdapter())
        self.assertEqual(code, 0)
        self.assertIn('abandon', output['result']['enabled_commands'])
        self.assertEqual(output['result']['accepted_streams'], ['retail-sales'])
        self.assertEqual(output['source_requests'], 0)

    def test_status_uses_durable_contiguous_cursor_and_retains_display_warning(self):
        with patch('kemai_pipeline.state.cached_days', return_value=set()):
            code, output = invoke(['status', '--stream', 'retail-sales'], FakeAdapter())
        self.assertEqual(code, 0)
        result = output['result']
        self.assertEqual(result['last_contiguous_verified_day'], '2026-08-01')
        self.assertIn('display maximum', result['note'])
        self.assertEqual(output['source_requests'], 0)
        self.assertEqual(output['db_writes'], 0)

    def test_planning_pending_makes_run_incomplete_and_is_propagated(self):
        plan = {'status': 'planned', 'tasks': [], 'blocked': [],
                'planning_pending': [{'stream': 'retail-sales', 'day': '2026-11-07'}],
                'backlog_remaining': 1, 'backlog_by_stream': {'retail-sales': 1},
                'coverage_gaps': [], 'source_requests': 0, 'db_writes': 0}
        with patch('kemai_syncctl.plan_incremental', return_value=plan):
            code, output = invoke(['incremental', 'run', '--stream', 'retail-sales'], FakeAdapter())
        self.assertEqual(code, 2)
        self.assertFalse(output['result']['complete'])
        self.assertEqual(output['result']['planning_pending'], plan['planning_pending'])
        self.assertEqual(output['result']['planning_backlog'], 1)
        self.assertEqual(output['result']['coverage_gaps'], [])

    def test_history_and_incremental_plans_have_zero_source_and_db_writes(self):
        for argv in (['history', 'plan', '--stream', 'retail-sales',
                      '--from', '2026-11-06', '--to', '2026-11-07'],
                     ['incremental', 'plan', '--stream', 'retail-sales']):
            with self.subTest(argv=argv), patch('kemai_pipeline.state.cached_days', return_value=set()):
                code, output = invoke(argv, FakeAdapter())
                self.assertEqual(code, 0)
                self.assertEqual(output['source_requests'], 0)
                self.assertEqual(output['db_writes'], 0)
                self.assertEqual(output['result']['source_requests'], 0)
                self.assertEqual(output['result']['db_writes'], 0)

    def test_cooling_run_propagates_deferred_tasks_without_executing(self):
        deferred = [
            {'stream': 'retail-sales', 'day': '2026-11-18', 'mode': 'provisional',
             'reason': 'provisional_snapshot'},
            {'stream': 'retail-sales', 'day': '2026-11-17', 'mode': 'final',
             'reason': 'late_arrival_revisit'}]
        plan = {'status': 'cooling', 'tasks': [], 'deferred_tasks': deferred,
                'blocked': [], 'planning_pending': [], 'backlog_remaining': 0,
                'backlog_by_stream': {}, 'coverage_gaps': [], 'source_requests': 0, 'db_writes': 0}
        with patch('kemai_syncctl.plan_incremental', return_value=plan), \
             patch('kemai_syncctl.execute_tasks') as execute:
            code, output = invoke(['incremental', 'run', '--stream', 'retail-sales'], FakeAdapter())
        execute.assert_not_called()
        self.assertEqual(code, 2)
        self.assertEqual(output['result']['state'], 'cooling')
        self.assertEqual(output['result']['deferred_tasks'], deferred)
        self.assertEqual(output['source_requests'], 0)

    def test_errors_remain_in_versioned_outer_envelope(self):
        adapter = FakeAdapter()
        adapter.read_all = lambda *args, **kwargs: (_ for _ in ()).throw(ContractError('bad contract'))
        code, output = invoke(['status', '--stream', 'retail-sales'], adapter)
        self.assertEqual(code, 1)
        self.assertEqual(output['schema_version'], 1)
        self.assertFalse(output['ok'])
        self.assertEqual(output['error']['type'], 'ContractError')


if __name__ == '__main__':
    unittest.main()
