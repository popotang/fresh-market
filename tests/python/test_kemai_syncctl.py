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

    # ---- unit-2 sync-mode planning contract (list-first on-demand plan) ----
    def test_default_history_plan_declares_list_mode_and_coverage_split(self):
        code, output = invoke(['history', 'plan', '--stream', 'retail-sales',
                               '--from', '2026-11-06', '--to', '2026-11-07'], FakeAdapter())
        self.assertEqual(code, 0)
        result = output['result']
        self.assertEqual(result['sync_mode']['sync_mode'], 'list')
        self.assertEqual(result['detail_demand'], 'none')
        self.assertIn('list evidence only', result['coverage']['detail_coverage'])
        self.assertIn('source_observed_at', result['coverage']['timing_semantics'])
        self.assertEqual(output['source_requests'], 0)
        self.assertEqual(output['db_writes'], 0)

    def test_list_mode_with_detail_field_is_a_contradiction(self):
        code, output = invoke(['history', 'plan', '--stream', 'purchase-orders',
                               '--sync-mode', 'list', '--required-field', 'purchase.lines.quantity',
                               '--from', '2026-11-06', '--to', '2026-11-07'], FakeAdapter())
        self.assertEqual(code, 1)
        self.assertFalse(output['ok'])
        self.assertEqual(output['error']['type'], 'ValueError')

    def test_on_demand_line_field_routes_lines_and_reports_detail_fields(self):
        code, output = invoke(['history', 'plan', '--stream', 'purchase-orders',
                               '--sync-mode', 'on_demand', '--required-field', 'quantity',
                               '--from', '2026-11-06', '--to', '2026-11-07'], FakeAdapter())
        self.assertEqual(code, 0)
        result = output['result']
        self.assertEqual(result['sync_mode']['sync_mode'], 'on_demand')
        self.assertEqual(result['detail_demand'], 'bounded')
        self.assertEqual(result['detail_fields']['needs_detail'], ['purchase.lines.quantity'])
        lines = [t for t in result['tasks'] if t.get('routing', {}).get('action') == 'lines_only']
        self.assertTrue(lines)
        for task in lines:
            self.assertIn('quantity', task['required_fields'])

    def test_on_demand_without_demand_executes_as_list(self):
        with patch('kemai_pipeline.state.cached_days', return_value=set()):
            code, output = invoke(['incremental', 'plan', '--stream', 'retail-sales',
                                   '--sync-mode', 'on_demand'], FakeAdapter())
        self.assertEqual(code, 0)
        self.assertEqual(output['result']['sync_mode']['sync_mode'], 'on_demand')
        self.assertEqual(output['result']['detail_demand'], 'none')
        self.assertIn('executes as list', output['result']['sync_mode']['note'])

    def test_full_on_incremental_is_rejected_without_explicit_range(self):
        code, output = invoke(['incremental', 'plan', '--stream', 'purchase-orders',
                               '--sync-mode', 'full', '--detail-budget', '10'], FakeAdapter())
        self.assertEqual(code, 1)
        self.assertFalse(output['ok'])

    def test_full_history_requires_budget(self):
        argv = ['history', 'plan', '--stream', 'purchase-orders', '--sync-mode', 'full',
                '--from', '2026-11-06', '--to', '2026-11-07']
        code, output = invoke(argv, FakeAdapter())
        self.assertEqual(code, 1)
        argv += ['--detail-budget', '10']
        code, output = invoke(argv, FakeAdapter())
        self.assertEqual(code, 0)
        self.assertEqual(output['result']['detail_demand'], 'bounded')
        self.assertEqual(output['result']['sync_mode']['sync_mode'], 'full')

    def test_include_details_keeps_legacy_full_scope(self):
        with patch('kemai_pipeline.state.cached_days', return_value=set()):
            code, output = invoke(['incremental', 'plan', '--stream', 'purchase-orders',
                                   '--include-details'], FakeAdapter())
        self.assertEqual(code, 0)
        result = output['result']
        self.assertEqual(result['sync_mode']['sync_mode'], 'full')
        self.assertEqual(result['sync_mode']['mode_basis'], 'legacy_include_details')
        self.assertIn('legacy purchase document-detail gaps',
                      str(result['coverage']['detail_scope']))

    def test_paused_payment_field_blocks_in_every_mode(self):
        for mode in ('list', 'on_demand', 'full'):
            argv = ['history', 'plan', '--stream', 'retail-orders', '--sync-mode', mode,
                    '--required-field', 'retail.orders.payments',
                    '--from', '2026-11-06', '--to', '2026-11-07']
            if mode == 'full':
                argv += ['--detail-budget', '5']
            code, output = invoke(argv, FakeAdapter())
            self.assertEqual(code, 2, mode)
            blocked = output['result']['blocked'][-1]
            self.assertEqual(blocked['reason'], 'paused_by_user')
            self.assertEqual(blocked['fields'], ['retail.orders.payments'])

    def test_unknown_field_is_refused_without_guessing(self):
        code, output = invoke(['history', 'plan', '--stream', 'retail-sales',
                               '--required-field', 'wibble',
                               '--from', '2026-11-06', '--to', '2026-11-07'], FakeAdapter())
        self.assertEqual(code, 1)
        self.assertFalse(output['ok'])
        self.assertIn('unknown', output['error']['message'])

    def test_full_plan_declares_planning_only_execution(self):
        code, output = invoke(['history', 'plan', '--stream', 'purchase-orders',
                               '--sync-mode', 'full', '--detail-budget', '10',
                               '--from', '2026-11-06', '--to', '2026-11-07'], FakeAdapter())
        self.assertEqual(code, 0)
        self.assertIn('planning_only_pending_unit3',
                      output['result']['sync_mode']['detail_execution'])
        self.assertIn('detail not scheduled', output['result']['coverage']['detail_coverage'])

    def test_paused_fill_executes_nothing(self):
        with patch('kemai_syncctl.execute_tasks') as execute:
            code, output = invoke(['history', 'fill', '--stream', 'retail-orders',
                                   '--sync-mode', 'on_demand',
                                   '--required-field', 'retail.orders.payments',
                                   '--from', '2026-11-06', '--to', '2026-11-07'], FakeAdapter())
        execute.assert_not_called()
        self.assertEqual(code, 2)
        self.assertFalse(output['result']['complete'])
        self.assertIn('no tasks executed', output['result']['note'])
        self.assertEqual(output['source_requests'], 0)

    def test_full_fill_is_blocked_until_unit3(self):
        with patch('kemai_syncctl.execute_tasks') as execute:
            code, output = invoke(['history', 'fill', '--stream', 'purchase-orders',
                                   '--sync-mode', 'full', '--detail-budget', '10',
                                   '--from', '2026-11-06', '--to', '2026-11-07'], FakeAdapter())
        execute.assert_not_called()
        self.assertEqual(code, 2)
        reasons = [b['reason'] for b in output['result']['blocked']]
        self.assertIn('full_detail_execution_pending_unit3', reasons)

    def test_legacy_full_fill_still_executes(self):
        # The legacy --include-details scope stays executable on incremental run.
        plan = {'status': 'planned', 'tasks': [{'stream': 'purchase-orders', 'day': '2026-11-07'}],
                'blocked': [], 'planning_pending': [], 'deferred_tasks': [],
                'backlog_remaining': 0, 'backlog_by_stream': {}, 'coverage_gaps': [],
                'source_requests': 0, 'db_writes': 0}
        committed = {'complete': True, 'results': [], 'source_operations': 0,
                     'source_requests': None, 'source_requests_basis': 'x'}
        with patch('kemai_pipeline.state.cached_days', return_value=set()), \
             patch('kemai_syncctl.plan_incremental', return_value=plan), \
             patch('kemai_syncctl.execute_tasks', return_value=committed) as execute:
            code, output = invoke(['incremental', 'run', '--stream', 'purchase-orders',
                                   '--include-details'], FakeAdapter())
        execute.assert_called_once()
        self.assertEqual(code, 0)

    def test_incremental_full_run_executes_nothing(self):
        # Explicit full has no date range on incremental and is refused at
        # argument resolution, before any planner or executor call.
        plan = {'status': 'planned', 'tasks': [{'stream': 'purchase-orders', 'day': '2026-11-07'}],
                'blocked': [], 'planning_pending': [], 'deferred_tasks': [],
                'backlog_remaining': 0, 'backlog_by_stream': {}, 'coverage_gaps': [],
                'source_requests': 0, 'db_writes': 0}
        with patch('kemai_syncctl.plan_incremental', return_value=plan), \
             patch('kemai_syncctl.execute_tasks') as execute:
            code, output = invoke(['incremental', 'run', '--stream', 'purchase-orders',
                                   '--sync-mode', 'full', '--detail-budget', '10'], FakeAdapter())
        execute.assert_not_called()
        plan_incremental_not_called = output['ok'] is False
        self.assertTrue(plan_incremental_not_called)
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
