"""Read-only standard API key/hash comparison for a completed capture; no SaaS calls."""
import argparse
from pathlib import Path
from kemai_pipeline.artifacts import CaptureStore,atomic_json
from kemai_pipeline.capabilities import CAPABILITIES
from kemai_pipeline.contracts import digest
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter
from kemai_pipeline.preparation import KEYS,KINDS
from kemai_pipeline.locking import writer_lock
from datetime import date,timedelta


def verify(capture_id, artifact_root=None):
 root=Path(__file__).resolve().parents[1];store=CaptureStore(artifact_root or root/'.kemai-artifacts');state=store.load(capture_id)
 if state['phase']!='committed' or state['mode']!='final':raise ValueError('A committed closed-day capture is required')
 cap=CAPABILITIES[state['stream']];key=KEYS[state['stream']];day=date.fromisoformat(state['window_key'])
 wanted={r['source_key']:r['data']['payload_hash'] for r in store.rows(state)}
 center=LovrabetAdapter();actual={};page=1;expected=None;metadata_only=0;raw_actual={}
 hash_field='source_list_hash' if state['stream'] in KINDS else 'payload_hash'
 source_rows={r['source_key']:r for r in store.rows(state)}
 where={cap.date_field:{'$gte':str(day),'$lt':str(day+timedelta(days=1))}}
 if state['stream'] in KINDS:where['document_kind']={'$eq':KINDS[state['stream']]}
 with writer_lock():
  expected=center.count(cap.table,where=where)
  keys=list(wanted)
  for offset in range(0,len(keys),500):
   chunk=keys[offset:offset+500]
   rows,total=center.page(cap.table,where={**where,key:{'$in':chunk}},select=[key,hash_field],size=500)
   if total>len(chunk):raise ValueError('Unexpected duplicate business identities')
   for row in rows:
    if row[key] in actual:raise ValueError('Duplicate business key')
    actual[row[key]]=row.get(hash_field)
   if state['stream'] in KINDS:
    raw_rows,_=center.page('source_capture',where={'stream':{'$eq':state['stream']},
      'source_key':{'$in':chunk}},select=['source_key','payload_hash'],size=500)
    for raw in raw_rows:raw_actual[raw['source_key']]=raw['payload_hash']
   mismatches=[r[key] for r in rows if r.get(hash_field)!=wanted[r[key]]]
   if state['stream']=='retail-orders' and mismatches:
    payloads,_=center.page(cap.table,where={**where,key:{'$in':mismatches}},select=[key,'payload'],size=500)
    import json
    for row in payloads:
     original=json.loads(source_rows[row[key]]['data']['payload']);stored=row['payload']
     if isinstance(stored,str):stored=json.loads(stored)
     original.pop('gmtOperation',None);stored.pop('gmtOperation',None)
     if original==stored:metadata_only+=1
 report={'schema_version':1,'capture_id':capture_id,'stream':state['stream'],'day':str(day),
   'source_rows':len(wanted),'db_rows':expected,'missing':len(wanted.keys()-actual.keys()),
   'extra':expected-len(actual),'changed_hashes':sum(actual.get(k)!=v for k,v in wanted.items() if k in actual),
   'source_keyset_digest':digest(sorted(wanted)),'db_keyset_digest':digest(sorted(actual)),
   'source_requests':0,'business_writes':0,'data_center_requests':center.requests}
 report['metadata_only_differences']=metadata_only
 if state['stream'] in KINDS:
  report['raw_missing']=len(wanted.keys()-raw_actual.keys())
  report['raw_changed_hashes']=sum(raw_actual.get(k)!=v for k,v in wanted.items() if k in raw_actual)
  report['hash_field']=hash_field
 report['business_differences']=report['changed_hashes']-metadata_only
 report['comparison_policy']='retail-order-query-metadata-v1' if state['stream']=='retail-orders' else 'exact_payload_hash'
 report['passed']=report['missing']==report['extra']==report['business_differences']==0
 if state['stream'] in KINDS:report['passed'] &= report['raw_missing']==report['raw_changed_hashes']==0
 return report

if __name__=='__main__':
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--capture-id',required=True);ap.add_argument('--artifact-root',type=Path);ap.add_argument('--report',type=Path,required=True);args=ap.parse_args()
 result=verify(args.capture_id,args.artifact_root);atomic_json(args.report,result)
 import json
 print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['passed'] else 2)
