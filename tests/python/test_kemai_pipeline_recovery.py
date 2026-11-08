import json
from pathlib import Path
import tempfile
import unittest
from kemai_pipeline.artifacts import ArtifactError, CaptureStore
from kemai_pipeline.runner import build_batches, ingest_capture, UnknownWrite


class Center:
    def __init__(self): self.receipts={};self.rows={};self.writes=0;self.commits=0;self.lose_response=False
    def batch_result(self,batch_id):return self.receipts.get(batch_id)
    def batch_upsert(self,batch):
        if batch['batch_id'] in self.receipts:return self.receipts[batch['batch_id']]
        self.writes+=1
        self.rows.update({r['source_key']:r for r in batch['records']})
        receipt={k:batch[k] for k in ('batch_id','capture_id','window_key','payload_hash')}
        receipt.update(state='committed',row_count=len(batch['records']))
        self.receipts[batch['batch_id']]=receipt
        if self.lose_response: raise TimeoutError('committed but response lost')
        return receipt
    def commit_window(self,manifest):
        self.commits+=1
        return {'capture_id':manifest['capture_id'],'state':'committed'}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=CaptureStore(self.temp.name)
        self.state=self.store.create(tenant='205149',stream='retail-sales',window_key='2026-11-07',source_key_version='v1',mode='final')
        self.rows=[{'source_key':str(i),'payload':{'text':'商品'*20}} for i in range(4)]

    def seal(self):
        self.store.append_page(self.state,self.rows)
        self.store.seal(self.state,{'source_rows':4,'verification_method':'fixture_count'})

    def test_retries_use_receipts_even_after_lost_success_response(self):
        self.seal();center=Center();center.lose_response=True
        ingest_capture(self.store,self.state['capture_id'],center,max_rows=2)
        ingest_capture(self.store,self.state['capture_id'],center,max_rows=2)
        self.assertEqual(center.writes,2);self.assertEqual(len(center.rows),4)

    def test_tampered_artifact_prevents_any_write(self):
        self.seal();center=Center()
        path=self.store.path(self.state['capture_id'])/'page-000000.json'
        path.write_text('{"rows":[]}')
        with self.assertRaises(ArtifactError):ingest_capture(self.store,self.state['capture_id'],center)
        self.assertEqual(center.writes,0)

    def test_incomplete_capture_never_resumes_old_offset(self):
        self.store.append_page(self.state,self.rows)
        state=self.store.interrupt_incomplete(self.state['capture_id'])
        self.assertEqual(state['phase'],'interrupted')
        with self.assertRaises(ArtifactError):ingest_capture(self.store,self.state['capture_id'],Center())

    def test_cross_page_duplicates_rejected(self):
        self.store.append_page(self.state,self.rows)
        self.store.append_page(self.state,self.rows[:1])
        with self.assertRaisesRegex(ArtifactError,'Duplicate'):
            self.store.seal(self.state,{'source_rows':5,'verification_method':'count'})

    def test_changed_batch_partition_cannot_reuse_batch_id(self):
        self.seal();center=Center()
        ingest_capture(self.store,self.state['capture_id'],center,max_rows=2)
        with self.assertRaisesRegex(ArtifactError,'partition'):
            ingest_capture(self.store,self.state['capture_id'],center,max_rows=3)

    def test_wrong_server_receipt_blocks_checkpoint(self):
        self.seal();center=Center()
        batch=build_batches(self.state,self.rows)[0]
        center.receipts[batch['batch_id']]={'state':'committed','batch_id':batch['batch_id'],'payload_hash':'wrong'}
        with self.assertRaises(UnknownWrite):ingest_capture(self.store,self.state['capture_id'],center)
        self.assertEqual(center.commits,0)

    def test_empty_day_commits_evidence_without_empty_batch_create(self):
        self.store.seal(self.state,{'source_rows':0,'verification_method':'count'})
        center=Center();ingest_capture(self.store,self.state['capture_id'],center)
        self.assertEqual(center.writes,0);self.assertEqual(center.commits,1)

    def test_capacity_uses_utf8_bytes_and_rejects_single_giant_row(self):
        with self.assertRaises(ArtifactError):
            build_batches(self.state,[{'source_key':'x','text':'汉'*30000}])
        batches=build_batches(self.state,[{**self.rows[0],'source_key':str(i)} for i in range(8)],max_bytes=1024)
        self.assertGreater(len(batches),1)
        self.assertTrue(all(len(json.dumps(b,ensure_ascii=False,separators=(',',':')).encode())<=1024 for b in batches))

    def test_artifact_permission_is_private(self):
        self.assertEqual((self.store.path(self.state['capture_id'])/'manifest.json').stat().st_mode & 0o777,0o600)


if __name__=='__main__':unittest.main()
