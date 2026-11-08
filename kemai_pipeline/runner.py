"""Deterministic write/resume engine. Source and data center are separate ports."""
import json
from .contracts import Batch
from .artifacts import ArtifactError
from .preparation import prepared_rows


class UnknownWrite(RuntimeError):
    pass


def build_batches(state, rows, *, max_rows=100, max_bytes=48000):
    if not 1<=max_rows<=200 or not 1024<=max_bytes<=64000:
        raise ValueError('Unverified batch capacity')
    result=[];pending=[];row_count=0
    def envelope(values):
        return Batch(state['tenant'],state['stream'],state['window_key'],state['capture_id'],
                     len(result),state['source_key_version'],tuple(values)).envelope()
    def size(value):
        # Include envelope and JSON escaping overhead, not just source row bytes.
        return len(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode())
    for row in rows:
        row_count+=1
        if row_count>100000:raise ArtifactError('Window exceeds verified 100,000-row budget; no batches were sent')
        candidate=pending+[row]
        if pending and (len(candidate)>max_rows or size(envelope(candidate))>max_bytes):
            result.append(envelope(pending));pending=[]
            if len(result)>=5000:raise ArtifactError('Window exceeds 5,000-batch budget; no batches were sent')
        pending.append(row)
        if size(envelope(pending))>max_bytes:
            raise ArtifactError('Single record exceeds verified transport capacity')
    if pending:result.append(envelope(pending))
    return result


def check_receipt(batch, receipt):
    if not isinstance(receipt,dict) or receipt.get('state')!='committed':
        raise UnknownWrite('No committed server receipt')
    for key in ('batch_id','capture_id','window_key','payload_hash'):
        if receipt.get(key)!=batch[key]: raise UnknownWrite('Receipt identity/digest mismatch')
    if receipt.get('row_count')!=len(batch['records']): raise UnknownWrite('Receipt row count mismatch')
    return receipt


def ingest_capture(store, capture_id, center, *, max_rows=100, max_bytes=48000):
    state=store.load(capture_id)
    if state['phase'] not in ('captured','writing','committed'):
        raise ArtifactError('Incomplete capture must be recaptured; never resume unstable page offset')
    batches=build_batches(state,prepared_rows(store,state),
        max_rows=state.get('batch_max_rows',max_rows),max_bytes=state.get('batch_max_bytes',max_bytes))
    ids=[b['batch_id'] for b in batches]
    specs=[{'id':b['batch_id'],'hash':b['payload_hash']} for b in batches]
    if state['batches'] and state['batches']!=specs:
        raise ArtifactError('Batch partition changed; resume with original capacity settings')
    state['batches']=specs
    # A bulk server read avoids one network round trip for every already committed batch.
    lookup=getattr(center,'batch_results',None)
    known=lookup(capture_id) if callable(lookup) else {}
    if not isinstance(known,dict):raise UnknownWrite('Invalid batch receipt collection')
    for batch in batches:
        receipt=known.get(batch['batch_id'])
        if receipt is None:
            state['phase']='writing';store.save(state)
            try:
                receipt=center.batch_upsert(batch)
            except Exception as error:
                try: receipt=center.batch_result(batch['batch_id'])
                except Exception: raise UnknownWrite('Write outcome unknown; resume same batch') from error
                if receipt is None: raise
        state['receipts'][batch['batch_id']]=check_receipt(batch,receipt)
        store.save(state)
    manifest={k:state[k] for k in ('tenant','stream','window_key','capture_id','source_key_version','mode')}
    manifest.update(batch_ids=ids,evidence=state['evidence'])
    result=center.commit_window(manifest)
    if not isinstance(result,dict) or result.get('capture_id')!=capture_id or result.get('state')!='committed':
        raise UnknownWrite('Window commit outcome unknown; resume and verify')
    state.update(phase='committed',commit=result);store.save(state)
    return result
