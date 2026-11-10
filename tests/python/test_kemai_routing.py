import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from datetime import datetime
from zoneinfo import ZoneInfo

from kemai_pipeline.routing import choose_route, route_task, annotate_plan
from kemai_pipeline.operator import _execute_tasks, plan_incremental
from kemai_pipeline.workflow import WindowWorkflow, resolve_capture_method
from kemai_pipeline.source import SourceError, SourceCooling
from kemai_pipeline.artifacts import CaptureStore
from tests.python.test_kemai_pipeline_export_data_workflow import (
    write_verification_artifact, make_xlsx, sample_rows, FakeHistorySource, FakeCenter)
from tests.python.test_kemai_pipeline_operator import FakeCenter as RecoveryCenter, FakeWorkflow

HISTORY='purchase-receive-history'


class RoutingTests(unittest.TestCase):
    def history(self, **kwargs):
        return choose_route(HISTORY,mapping_verified=True,**kwargs)

    def test_user_pins_procurement_lists_to_api_even_for_large_backlog(self):
        for stream in ('purchase-orders','purchase-receipts','purchase-returns'):
            r=choose_route(stream,estimated_rows=1000000,backlog_days=30,mapping_verified=True)
            self.assertEqual(r['method'],'api_only');self.assertEqual(r['reason'],'user_policy_api_only')
            self.assertEqual(choose_route(stream,preference='excel')['status'],'blocked')

    def test_small_large_and_unknown(self):
        self.assertEqual(self.history(estimated_rows=20)['method'],'api_only')
        self.assertEqual(self.history(estimated_rows=6000)['method'],'export_for_data')
        self.assertEqual(self.history()['method'],'api_only')
        self.assertEqual(self.history(backlog_days=9)['method'],'export_for_data')
        self.assertEqual(self.history(estimated_rows=20,backlog_days=9)['method'],'api_only')

    def test_missing_small_does_not_hide_large_window_or_skip_equal_counts(self):
        self.assertEqual(self.history(estimated_rows=10000,missing_rows=1)['method'],'export_for_data')
        self.assertEqual(self.history(estimated_rows=20,missing_rows=0)['action'],'capture_window')

    def test_unverified_mapping_and_count_only_exports_never_selected_for_data(self):
        r=choose_route(HISTORY,estimated_rows=50000)
        self.assertEqual(r['method'],'api_only')
        self.assertEqual(r['rejected_alternatives'][0]['reason'],'mapping_not_verified')
        r=choose_route('retail-sales',estimated_rows=50000)
        self.assertEqual(r['method'],'api_for_data+export_for_count')
        self.assertEqual(choose_route('retail-sales',preference='excel')['status'],'blocked')
        self.assertEqual(choose_route('purchase-orders',preference='excel')['status'],'blocked')

    def test_cooling_prevents_switch_and_recovery_is_pinned(self):
        active={'capture_id':'a'*32,'method':'api_only'}
        self.assertEqual(self.history(active_capture=active,estimated_rows=50000)['method'],'api_only')
        self.assertEqual(self.history(active_capture=active,cooling=True)['status'],'cooling')

    def test_pause_and_acceptance(self):
        self.assertEqual(choose_route('retail-payments')['reason'],'paused_by_user')
        r=self.history(estimated_rows=6000,accepted=False)
        self.assertEqual(r['status'],'blocked');self.assertEqual(r['method'],'export_for_data')
        self.assertEqual(r['candidate_reason'],'large_scan_window')

    def test_provisional_and_required_fields(self):
        self.assertEqual(self.history(mode='provisional',preference='excel')['status'],'blocked')
        self.assertEqual(choose_route('purchase-orders',required_fields=['quantity'])['action'],'lines_only')
        self.assertEqual(choose_route('purchase-orders',required_fields=['item_name'])['status'],'blocked')
        self.assertEqual(choose_route('purchase-orders',required_fields=['quantity'],accepted=False)['status'],'blocked')
        r=self.history(required_fields=['source_document_id'],estimated_rows=50000)
        self.assertEqual(r['method'],'api_only')
        self.assertEqual(r['rejected_alternatives'][0]['reason'],'required_fields_not_in_export')
        self.assertEqual(self.history(required_fields=['source_item_id'],preference='excel')['status'],'blocked')

    def test_estimates_are_validated(self):
        for k,v in [('estimated_rows',-1),('missing_rows',True),('backlog_days',-1),('required_fields','quantity')]:
            with self.subTest(k=k),self.assertRaises(ValueError):self.history(**{k:v})

    def test_recent_same_scope_costs_can_override_threshold(self):
        costs={'stream':HISTORY,'scope':'same_window','samples':5,'age_hours':1,
               'api_page_seconds':10,'export_fixed_seconds':1000,'export_row_seconds':0}
        self.assertEqual(self.history(estimated_rows=6000,costs=costs)['method'],'api_only')
        costs['export_fixed_seconds']=1
        self.assertEqual(self.history(estimated_rows=20,costs=costs)['method'],'export_for_data')
        for field,value in [('age_hours',999),('samples',0),('stream','other'),('api_page_seconds',float('nan'))]:
            r=self.history(estimated_rows=20,costs={**costs,field:value})
            self.assertEqual(r['method'],'api_only')

    def test_api_allowed_after_mapping_is_verified_and_local_acceptance_still_required(self):
        with tempfile.TemporaryDirectory() as root:
            write_verification_artifact(root)
            self.assertEqual(resolve_capture_method(HISTORY,'final','api_only',root=root),'api_only')
            decision=route_task({'stream':HISTORY,'estimated_rows':9999,'accepted':True},root)
            self.assertEqual(decision['reason'],'stream_acceptance_required')

    def test_annotated_plan_honors_existing_estimate_and_records_reasons(self):
        task={'stream':'retail-sales','day':'2026-11-06','mode':'final','estimated_rows':7000}
        with tempfile.TemporaryDirectory() as root:
            p=annotate_plan({'tasks':[task]},root)
        self.assertEqual(p['tasks'][0]['routing']['estimated_rows'],7000)
        self.assertTrue(p['tasks'][0]['routing']['rejected_alternatives'])

    def test_executor_rechecks_contract_before_any_capture_or_source(self):
        center=RecoveryCenter();source=Mock(operations=0)
        workflow=FakeWorkflow(None,source,center)
        task={'stream':'retail-sales','day':'2026-11-06','mode':'final','strategy':'excel',
              'routing':{'status':'selected','method':'export_for_data'}}
        with tempfile.TemporaryDirectory() as root,patch('kemai_pipeline.operator.WindowWorkflow',return_value=workflow):
            result=_execute_tasks(center,[task],artifact_root=root,source=source)
        self.assertEqual(result['results'][0]['state'],'blocked')
        self.assertEqual(workflow.begun,[])

    def test_requested_lines_run_only_after_base_window_commits(self):
        center=RecoveryCenter();source=Mock(operations=0)
        workflow=FakeWorkflow(None,source,center)
        task={'stream':'purchase-orders','day':'2026-11-06','mode':'final','required_fields':['quantity']}
        with tempfile.TemporaryDirectory() as root,patch('kemai_pipeline.operator.WindowWorkflow',return_value=workflow),\
                patch('kemai_pipeline.operator._run_lines_task',return_value={'state':'committed','capture_id':'b'*32}) as lines:
            result=_execute_tasks(center,[task],artifact_root=root,source=source)
        self.assertEqual(len(workflow.begun),1);lines.assert_called_once()
        self.assertTrue(result['results'][0]['base_window_committed'])
        self.assertEqual(result['results'][0]['requested_scope'],'lines_only')

    def test_pending_base_does_not_start_details(self):
        center=RecoveryCenter();source=Mock(operations=0)
        workflow=FakeWorkflow(None,source,center,begin_results=[{'state':'awaiting_export'}])
        task={'stream':'purchase-orders','day':'2026-11-06','mode':'final','required_fields':['quantity']}
        with tempfile.TemporaryDirectory() as root,patch('kemai_pipeline.operator.WindowWorkflow',return_value=workflow),\
                patch('kemai_pipeline.operator._run_lines_task') as lines:
            _execute_tasks(center,[task],artifact_root=root,source=source)
        lines.assert_not_called()

    def test_default_incremental_leaves_detail_gaps_on_demand(self):
        client=Mock();client.count.return_value=1
        client.read_all.return_value=[{'stream':'purchase-orders','scope_key':'2026-11-06',
                                      'missing_parts':['document_lines'],'source_row_count':3}]
        state={'baseline_verified':True,'baseline_from':'2026-11-06','last_contiguous_verified_day':'2026-11-06',
               'covered_days':['2026-11-06'],'holes':[],'estimated_scan_rows':8}
        with patch('kemai_pipeline.operator.stream_state',return_value=state),patch('kemai_pipeline.operator.cooldown_until',return_value=0):
            result=plan_incremental(client,['purchase-orders'],now=datetime(2026,11,7,12,tzinfo=ZoneInfo('Asia/Shanghai')))
        self.assertFalse(any(t.get('kind')=='detail_pass' for t in result['tasks']))
        self.assertEqual(result['coverage_gaps'][0]['scheduling'],'on_demand')
        self.assertEqual(result['tasks'][0]['estimated_rows'],8)


class ExportRecoveryRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);write_verification_artifact(self.root)
        path=self.root/'input.xlsx';make_xlsx(path,sample_rows())
        self.source=FakeHistorySource(path.read_bytes(),2)
        self.center=FakeCenter()
        self.store=CaptureStore(self.root)
        self.workflow=WindowWorkflow(self.store,self.source,self.center)

    def test_unknown_export_submission_is_recovered_without_second_start(self):
        started=self.source.export_start
        def lost(*args,**kwargs):
            started(*args,**kwargs)
            raise SourceError('export result unknown')
        self.source.export_start=lost
        with self.assertRaises(SourceError):self.workflow.begin(HISTORY,'2026-11-06')
        capture=next(p.parent.name for p in self.root.glob('*/manifest.json'))
        state=self.store.load(capture)
        self.assertEqual(state['phase'],'capturing');self.assertEqual(state['export_submission'],'unknown')
        self.source.recover_export=lambda *args:{'task_id':'9001','stream':HISTORY,'day':'2026-11-06'}
        with patch('kemai_pipeline.workflow.ingest_capture',return_value={'state':'committed'}):
            self.workflow.resume(capture)
        self.assertEqual(len(self.source.starts),1)

    def test_cooling_before_first_source_operation_is_not_unknown_submission(self):
        original=self.source.export_start
        self.source.export_start=lambda *a,**kw: (_ for _ in ()).throw(SourceCooling('already cooling'))
        with self.assertRaises(SourceCooling):self.workflow.begin(HISTORY,'2026-11-06')
        capture=next(p.parent.name for p in self.root.glob('*/manifest.json'))
        self.assertEqual(self.store.load(capture)['export_submission'],'not_started')
        self.source.export_start=original
        with patch('kemai_pipeline.workflow.ingest_capture',return_value={'state':'committed'}):
            self.workflow.resume(capture)
        self.assertEqual(len(self.source.starts),1)

    def test_cached_xlsx_skips_task_poll_and_download_and_persists_route(self):
        self.workflow.routing_decision={'method':'export_for_data','reason':'large_scan_window'}
        with patch('kemai_pipeline.workflow.ingest_capture',side_effect=RuntimeError('unknown DB result')):
            with self.assertRaises(RuntimeError):self.workflow.begin(HISTORY,'2026-11-06')
        capture=next(p.parent.name for p in self.root.glob('*/manifest.json'))
        state=self.store.load(capture)
        self.assertEqual(state['routing']['reason'],'large_scan_window')
        # Simulate interruption after the durable file/receipt, before parse/seal completion.
        state.update(phase='capturing',pages=[],pages_complete=False)
        self.store.save(state)
        before=self.source.operations
        with patch('kemai_pipeline.workflow.ingest_capture',return_value={'state':'committed'}):
            self.workflow.resume(capture)
        self.assertEqual(self.source.operations,before)
        evidence=self.store.load(capture)['evidence']
        self.assertEqual(evidence['window_time_field'],'approved_at')
        self.assertEqual(evidence['detail_coverage'],'not_checked')
