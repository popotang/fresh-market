"""Prepare immutable changed-row writes and hash-only observations of unchanged rows."""
from .artifacts import atomic_json, ArtifactError
from .contracts import digest
from .capabilities import CAPABILITIES

KEYS={'retail-sales':'source_flow_id','retail-orders':'source_order_id',
      'purchase-orders':'source_document_id','purchase-receipts':'source_document_id',
      'purchase-returns':'source_document_id','purchase-receive-history':'source_line_key'}
KINDS={'purchase-orders':'order','purchase-receipts':'receipt','purchase-returns':'return'}


def prepared_rows(store,state):
    if not state.get('ingest_file'):return store.rows(state)
    import json
    if state['ingest_file']!='ingestion.json':raise ArtifactError('Invalid ingestion artifact path')
    value=json.loads((store.path(state['capture_id'])/state['ingest_file']).read_text())
    if digest(value)!=state['ingest_hash']:raise ArtifactError('Ingestion artifact digest mismatch')
    return iter(value['rows'])


def prepare_ingestion(store,state,center):
    if state.get('ingest_file') or state['batches']:return  # Never repartition an existing batch set.
    table=CAPABILITIES[state['stream']].table;key=KEYS[state['stream']]
    source=list(store.rows(state));output=[];observed=0
    for offset in range(0,len(source),500):
        chunk=source[offset:offset+500];keys=[r['source_key'] for r in chunk]
        condition={key:{'$in':keys}}
        if state['stream'] in KINDS:condition['document_kind']={'$eq':KINDS[state['stream']]}
        business,_=center.page(table,where=condition,select=[key,'payload_hash'],size=500)
        raw,_=center.page('source_capture',where={'stream':{'$eq':state['stream']},'source_key':{'$in':keys}},select=['source_key','payload_hash'],size=500)
        business_hash={r[key]:r['payload_hash'] for r in business};raw_hash={r['source_key']:r['payload_hash'] for r in raw}
        for row in chunk:
            expected=row['data']['payload_hash'];source_key=row['source_key']
            if business_hash.get(source_key)==raw_hash.get(source_key)==expected:
                output.append({'source_key':source_key,'expected_hash':expected});observed+=1
            else:output.append(row)
    value={'schema_version':1,'mapping_version':'commerce-v1','source_capture_id':state['capture_id'],'rows':output}
    atomic_json(store.path(state['capture_id'])/'ingestion.json',value)
    state.update(ingest_file='ingestion.json',ingest_hash=digest(value),ingest_policy='observe-unchanged-v1',
                 observed_rows=observed,changed_rows=len(output)-observed,batch_max_rows=200,batch_max_bytes=48000)
    store.save(state)
