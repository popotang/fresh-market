import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from kemai_pipeline.artifacts import CaptureStore,ArtifactError
from kemai_pipeline.contracts import digest
from kemai_pipeline.preparation import prepare_ingestion,prepared_rows
from kemai_pipeline.normalize import normalize
from kemai_pipeline.runner import build_batches

class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=CaptureStore(self.temp.name)
        self.state=self.store.create(tenant='205149',stream='retail-sales',window_key='2026-11-07',source_key_version='source-id-v1',mode='final')
        self.rows=[normalize('retail-sales',{'id':str(i),'gmtSale':'2026-11-07 12:00:00','description':'长字段'*200}) for i in range(201)]
        self.store.append_page(self.state,self.rows);self.store.seal(self.state,{'source_rows':201,'verification_method':'test'})
        self.center=Mock()
        self.center.page.side_effect=lambda table,**kw: ([{'source_flow_id':r['source_key'],'source_key':r['source_key'],'payload_hash':r['data']['payload_hash']} for r in self.rows],len(self.rows))

    def test_unchanged_capture_uses_compact_observations_and_retains_full_source(self):
        prepare_ingestion(self.store,self.state,self.center)
        rows=list(prepared_rows(self.store,self.state))
        self.assertEqual(self.state['observed_rows'],201)
        self.assertTrue(all(set(r)=={'source_key','expected_hash'} for r in rows))
        self.assertEqual(list(self.store.rows(self.state)),self.rows)
        self.assertEqual([len(b['records']) for b in build_batches(self.state,rows,max_rows=200)],[200,1])

    def test_missing_raw_copy_requires_full_row_even_when_business_matches(self):
        self.center.page.side_effect=[([{'source_flow_id':r['source_key'],'payload_hash':r['data']['payload_hash']} for r in self.rows],201),([],0)]
        prepare_ingestion(self.store,self.state,self.center)
        self.assertEqual(self.state['observed_rows'],0)
        self.assertEqual(list(prepared_rows(self.store,self.state)),self.rows)

    def test_retry_never_repartitions_started_batches(self):
        self.state['batches']=[{'id':'old','hash':'old'}]
        prepare_ingestion(self.store,self.state,self.center)
        self.center.page.assert_not_called();self.assertNotIn('ingest_file',self.state)

    def test_changed_artifact_rejected_before_writes(self):
        prepare_ingestion(self.store,self.state,self.center)
        path=self.store.path(self.state['capture_id'])/'ingestion.json'
        value=json.loads(path.read_text());value['rows'][0]['expected_hash']='0'*64;path.write_text(json.dumps(value))
        with self.assertRaises(ArtifactError):list(prepared_rows(self.store,self.state))

    def test_preparation_resume_does_not_requery_database(self):
        prepare_ingestion(self.store,self.state,self.center)
        calls=self.center.page.call_count
        prepare_ingestion(self.store,self.store.load(self.state['capture_id']),self.center)
        self.assertEqual(self.center.page.call_count,calls)

if __name__=='__main__':unittest.main()
