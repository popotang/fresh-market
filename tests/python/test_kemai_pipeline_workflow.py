import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from datetime import datetime
from zoneinfo import ZoneInfo
from kemai_pipeline.artifacts import CaptureStore
from kemai_pipeline.source import OpenCLISource,SourceError,SourceCooling
from kemai_pipeline.workflow import WindowWorkflow
from kemai_pipeline.normalize import normalize,history_key
from kemai_pipeline.locking import writer_lock,WriterBusy
from kemai_pipeline.planner import incremental_plan
from tests.python.test_kemai_pipeline_recovery import Center


class WorkflowCenter(Center):
    config={'tenant_id':'205149'}
    def begin_capture(self,state):pass
    def bind_capture(self,state):pass
    def fail_capture(self,state,reason):pass
    def note_capture(self,state,status,reason):pass


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        lock_patch=patch('kemai_pipeline.workflow.writer_lock',lambda:writer_lock(Path(self.temp.name)/'workflow.lock'))
        lock_patch.start();self.addCleanup(lock_patch.stop)
        self.store=CaptureStore(self.temp.name)
        self.source=Mock();self.center=WorkflowCenter()
        self.source.pages.return_value=[{'rows':[{'id':'1','gmtSale':'2026-11-07 12:00:00','gmtCreate':'2026-11-07 12:00:01','qty':12345}],
                                       'total_rows':1,'observed_at':'2026-11-08T10:00:00Z'}]
        self.source.export_start.return_value={'task_id':'task1'}
        self.source.export_status.side_effect=[{'state':'pending'},{'state':'complete','source_rows':1,'task_id':'task1'}]
        self.workflow=WindowWorkflow(self.store,self.source,self.center)

    def test_export_pending_resumes_same_task_and_capture_without_second_source_scan(self):
        result=self.workflow.begin('retail-sales','2026-11-07')
        self.assertEqual(result['state'],'awaiting_export');self.assertEqual(self.center.writes,0)
        result=self.workflow.resume(result['capture_id'])
        self.assertEqual(result['state'],'committed')
        self.assertEqual(self.source.pages.call_count,1);self.assertEqual(self.source.export_start.call_count,1)

    def test_export_count_mismatch_never_writes(self):
        self.source.export_status.side_effect=None;self.source.export_status.return_value={'state':'complete','source_rows':2,'task_id':'task1'}
        with self.assertRaises(SourceError):self.workflow.begin('retail-sales','2026-11-07')
        self.assertEqual(self.center.writes,0)

    def test_partial_pages_do_not_resume_old_page_offset(self):
        state=self.store.create(tenant='205149',stream='retail-sales',window_key='2026-11-07',source_key_version='source-id-v1',mode='final')
        state['server_started']=True;self.store.append_page(state,[normalize('retail-sales',{'id':'1'})])
        result=self.workflow.resume(state['capture_id'])
        self.assertEqual(result['state'],'interrupted');self.source.pages.assert_not_called()

    def test_purchase_window_uses_create_time_scope_and_no_export(self):
        scope={'dateType':'0','operDateFrom':'2026-11-07','operDateTo':'2026-11-07',
               'keyword':'','itemPackId':None,'status':''}
        self.source.pages.return_value=[{'rows':[{'id':'PO1','sheetNo':'PO-1','gmtCreate':'2026-11-07 10:00:00','sumAmt':12345,'status':2}],
            'total_rows':None,'observed_at':'2026-11-08T10:00:00Z','query_scope':scope}]
        result=self.workflow.begin('purchase-orders','2026-11-07')
        self.assertEqual(result['state'],'committed')
        self.source.export_start.assert_not_called()
        state=self.store.load(result['capture_id'])
        self.assertEqual(state['evidence']['window_time_field'],'source_created_at')
        self.assertFalse(state['evidence']['source_total_reliable'])
        self.assertEqual(state['evidence']['late_modification_policy'],
                         'create_day_window_with_previous_closed_day_revisit')

    def test_closed_page_total_mismatch_never_writes(self):
        self.source.pages.return_value[0]['total_rows']=3
        with self.assertRaises(SourceError):self.workflow.begin('retail-sales','2026-11-07')
        self.assertEqual(self.center.writes,0)

    def test_normalization_preserves_decimal_precision_and_stable_excel_key(self):
        row=normalize('retail-sales',{'id':'9223372036854775807','qty':12345,'saleAmt':123456789012345})
        self.assertEqual(row['source_key'],'9223372036854775807')
        self.assertEqual(row['data']['quantity'],'1.2345')
        self.assertEqual(row['data']['sale_amount'],'12345678901.2345')
        a={'sheetNo':'P1','sequence':'1.0','itemCode':'001'}
        self.assertEqual(history_key(a),history_key({**a,'sequence':1}))

    def test_http200_business_rate_limit_records_cooldown_without_retry(self):
        executor=Mock(return_value=Mock(returncode=0,stderr='',stdout=json.dumps([{'code':1,'msg':'您的操作太频繁，请稍后再试'}])))
        with patch('kemai_pipeline.source.cooldown_until',return_value=0),patch('kemai_pipeline.source.record_limit') as log:
            with self.assertRaises(SourceCooling):OpenCLISource(executor).call('capture-page')
            log.assert_called_once();self.assertEqual(executor.call_count,1)

    def test_existing_cooldown_makes_no_source_call(self):
        executor=Mock()
        with patch('kemai_pipeline.source.cooldown_until',return_value=99999999999):
            with self.assertRaises(SourceCooling):OpenCLISource(executor).call('capture-page')
        executor.assert_not_called()

    def test_planner_skips_verified_islands_and_uses_shanghai_day(self):
        state={'baseline_verified':True,'baseline_from':'2026-11-01','last_contiguous_verified_day':'2026-11-01',
               'covered_days':['2026-11-03','2026-11-04']}
        tasks=incremental_plan({'retail-sales':state},datetime(2026,11,8,17,tzinfo=ZoneInfo('UTC')))['tasks']
        self.assertEqual(tasks[0]['day'],'2026-11-09')
        self.assertEqual([r['day'] for r in tasks[1:]],['2026-11-02','2026-11-05'])

    def test_zero_source_budget_prevents_all_requests(self):
        executor=Mock()
        with patch('kemai_pipeline.source.cooldown_until',return_value=0):
            with self.assertRaisesRegex(SourceError,'budget exhausted'):OpenCLISource(executor,max_operations=0).call('capture-page')
        executor.assert_not_called()

    def test_query_scope_change_never_combines_snapshots(self):
        first=self.source.pages.return_value[0];first['query_scope']={'branchIdList':['1']}
        self.source.pages.return_value=[first,{**first,'query_scope':{'branchIdList':['2']}}]
        with self.assertRaisesRegex(SourceError,'scope changed'):self.workflow.begin('retail-sales','2026-11-07')
        self.assertEqual(self.center.writes,0)

    def test_error_logging_does_not_overwrite_newer_batch_receipts(self):
        self.source.export_status.side_effect=None
        self.source.export_status.return_value={'state':'complete','source_rows':1,'task_id':'task1'}
        def interrupted(store,capture,center,**kwargs):
            state=store.load(capture);state['phase']='writing';state['batches']=[{'id':'saved'}]
            state['receipts']={'saved':{'state':'committed'}};store.save(state)
            raise ValueError('simulated transport failure')
        with patch('kemai_pipeline.workflow.ingest_capture',side_effect=interrupted):
            with self.assertRaises(ValueError):self.workflow.begin('retail-sales','2026-11-07')
        states=list(Path(self.temp.name).glob('*/manifest.json'))
        value=json.loads(states[0].read_text());self.assertEqual(value['batches'],[{'id':'saved'}])
        self.assertEqual(value['receipts']['saved']['state'],'committed')

    def test_writer_lock_is_reentrant_but_excludes_other_processes(self):
        import subprocess,sys
        path=Path(self.temp.name)/'lock'
        with writer_lock(path),writer_lock(path):
            proc=subprocess.run([sys.executable,'-c',
              'from kemai_pipeline.locking import writer_lock;import sys\nwith writer_lock(sys.argv[1]): pass',str(path)],capture_output=True,text=True)
            self.assertNotEqual(proc.returncode,0);self.assertIn('WriterBusy',proc.stderr)
        with writer_lock(path):pass


if __name__=='__main__':unittest.main()
