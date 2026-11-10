import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kemai_pipeline.artifacts import CaptureStore, atomic_json, ArtifactError
from kemai_pipeline.lines_only_workflow import LinesOnlyWorkflow
from kemai_pipeline.lovrabet_adapter import ContractError
from kemai_pipeline.source import OpenCLISource, SourceCooling, SourceError
from tests.python.test_kemai_pipeline_detail_workflow import FakeDetailCenter, make_doc, make_entity, LockProbe


class BatchSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.docs=[make_doc(101+i,str(5001+i)) for i in range(3)]
        self.calls=[];self.fail=None
        for name in ['kemai_pipeline.batch_source.cooldown_until']:
            p=patch(name,return_value=0);p.start();self.addCleanup(p.stop)
        p=patch('kemai_pipeline.batch_source.record_limit');self.record=p.start();self.addCleanup(p.stop)
        p=patch('kemai_pipeline.detail_workflow.writer_lock',LockProbe());p.start();self.addCleanup(p.stop)
        self.source=OpenCLISource(executor=self.execute,max_operations=100)

    def execute(self, args, **kwargs):
        self.assertEqual(args[2],'purchase-detail-batch')
        request=Path(args[args.index('--request-file')+1])
        data=json.loads(request.read_text());self.calls.append(data['items'])
        for i,item in enumerate(data['items']):
            if self.fail and i==1:
                if self.fail=='timeout':raise subprocess.TimeoutExpired(args,kwargs['timeout'])
                return SimpleNamespace(returncode=1,stdout='',stderr='RATE_LIMIT: 操作太频繁')
            entity=make_entity(item['id'])
            body=json.dumps(entity)
            atomic_json(request.parent/(item['key']+'.json'),{
                'schema_version':1,'kind':data['kind'],'id':item['id'],'key':item['key'],
                'entity_json':body,'sha256':hashlib.sha256(body.encode()).hexdigest()})
        return SimpleNamespace(returncode=0,stdout='[]',stderr='')

    def read(self):
        return self.source.document_details('order',self.docs,self.root/'journal')

    def test_one_process_for_three_and_zero_process_on_journal_resume(self):
        entities,metrics,error=self.read()
        self.assertIsNone(error);self.assertEqual(len(entities),3)
        self.assertEqual(metrics['cli_calls'],1);self.assertEqual(self.source.operations,3)
        self.assertEqual(self.read()[1]['journal_hits'],3)
        self.assertEqual(len(self.calls),1)

    def test_cooling_preserves_partial_and_resume_requests_only_missing(self):
        self.fail='cooling'
        entities,metrics,error=self.read()
        self.assertIsInstance(error,SourceCooling);self.assertEqual(len(entities),1)
        self.record.assert_called_once()
        self.fail=None
        self.assertIsNone(self.read()[2]);self.assertEqual(len(self.calls[-1]),2)

    def test_timeout_preserves_artifact_and_is_resumable(self):
        self.fail='timeout'
        entities,_,error=self.read()
        self.assertEqual(len(entities),1);self.assertIsInstance(error,RuntimeError)
        self.fail=None;self.assertIsNone(self.read()[2])
        self.assertEqual(len(self.calls[-1]),2)

    def test_corrupt_artifact_fails_before_source(self):
        self.read()
        path=next((self.root/'journal').glob('[0-9a-f]'*64+'.json'))
        value=json.loads(path.read_text());value['sha256']='bad';atomic_json(path,value)
        with self.assertRaises(ArtifactError):self.read()
        self.assertEqual(len(self.calls),1)

    def test_budget_and_duplicates_rejected_before_source(self):
        self.source.max_operations=2
        with self.assertRaises(SourceError):self.read()
        self.assertEqual(self.calls,[])
        self.docs.append(self.docs[0])
        with self.assertRaises(ValueError):self.read()

    def test_unknown_version_not_reused(self):
        self.docs[0]['source_version']=None
        self.read();self.read()
        self.assertEqual(len(self.calls[-1]),1)

    def test_changed_version_only_refetches_changed_document(self):
        self.read();self.docs[0]['source_version']='8'
        self.read();self.assertEqual(len(self.calls[-1]),1)

    def test_existing_cooldown_issues_no_cli_call(self):
        with patch('kemai_pipeline.batch_source.cooldown_until',return_value=10**12):
            _,metrics,error=self.read()
        self.assertIsInstance(error,SourceCooling)
        self.assertEqual(metrics['cli_calls'],0);self.assertEqual(self.calls,[])

    def test_missing_cli_is_resumable(self):
        self.source.executor=lambda *a,**k: (_ for _ in ()).throw(FileNotFoundError())
        _,_,error=self.read();self.assertIsInstance(error,RuntimeError)

    def test_single_mode_remains_available(self):
        self.source.document_detail=lambda kind,sid:make_entity(sid)
        center=FakeDetailCenter(documents=self.docs)
        workflow=LinesOnlyWorkflow(CaptureStore(self.root/'captures'),self.source,center,batch_source=False)
        self.assertTrue(workflow.begin('purchase-orders','2026-11-08',max_documents=3)['verified'])
        self.assertEqual(self.calls,[])

    def test_workflow_batches_and_keeps_receipts_and_resume(self):
        center=FakeDetailCenter(documents=self.docs)
        workflow=LinesOnlyWorkflow(CaptureStore(self.root/'captures'),self.source,center)
        result=workflow.begin('purchase-orders','2026-11-08',max_documents=3)
        self.assertTrue(result['verified']);self.assertEqual(len(self.calls),1)
        summary=json.loads(next((self.root/'captures'/result['capture_id']/'timings').glob('summary-*')).read_text())
        self.assertEqual(summary['source_operations'],3)
        self.assertEqual(summary['source_batches'][0]['cli_calls'],1)
        self.assertEqual(workflow.resume(result['capture_id'])['processed'],0)

    def test_workflow_cooling_saves_success_before_error_no_db_writes(self):
        center=FakeDetailCenter(documents=self.docs)
        store=CaptureStore(self.root/'captures');workflow=LinesOnlyWorkflow(store,self.source,center)
        self.fail='cooling'
        with self.assertRaises(SourceCooling):workflow.begin('purchase-orders','2026-11-08',max_documents=3)
        self.assertIsNotNone(workflow.evidence.load('order',self.docs[0]))
        self.assertEqual(center.tables['purchase_document_lines'],[])
        capture=next(p.name for p in store.root.iterdir() if len(p.name)==32)
        self.assertEqual(store.load(capture)['last_error'],'cooling_30_minutes')
        self.fail=None
        self.assertTrue(workflow.resume(capture)['verified'])
        self.assertEqual(len(self.calls[-1]),2)

    def test_unknown_db_write_recovers_without_source_replay(self):
        center=FakeDetailCenter(documents=self.docs);center.fail_upsert_once='purchase_document_lines'
        store=CaptureStore(self.root/'captures');workflow=LinesOnlyWorkflow(store,self.source,center)
        with self.assertRaises(ContractError):workflow.begin('purchase-orders','2026-11-08',max_documents=3)
        capture=next(p.name for p in store.root.iterdir() if len(p.name)==32)
        self.assertTrue(workflow.resume(capture)['verified']);self.assertEqual(len(self.calls),1)

    def test_more_than_ten_split_and_max_documents_respected(self):
        docs=[make_doc(101+i,str(5001+i)) for i in range(14)]
        center=FakeDetailCenter(documents=docs)
        workflow=LinesOnlyWorkflow(CaptureStore(self.root/'captures'),self.source,center)
        result=workflow.begin('purchase-orders','2026-11-08',max_documents=12)
        self.assertEqual(result['processed'],12);self.assertEqual(result['remainder'],2)
        self.assertEqual([len(x) for x in self.calls],[10,2])
