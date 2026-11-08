#!/usr/bin/env python3
"""Explicit, bounded integration acceptance in isolated test tables via Lovrabet CLI.

No SaaS requests. Run --execute only after publishing the standard gateway.
Every case persists its response immediately; reusing --report reuses test IDs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent


def call(params):
    started = time.monotonic()
    p = subprocess.run(['lovrabet','bff','exec','--name','kemaiPipelineProbeGateway',
        '--params',json.dumps(params,ensure_ascii=False),'--appcode','app-21522951','--format','compress'],
        cwd=ROOT,capture_output=True,text=True,timeout=90)
    try:
        value = json.loads(p.stdout)
    except ValueError:
        tags=('BATCH_CONFLICT','DIGEST_MISMATCH','PROBE_AFTER_WRITE_FAILURE','INCOMPLETE_CAPTURE','PROBE_WINDOW_FAILURE','BATCH_ROW_LIMIT','BATCH_BYTE_LIMIT')
        safe=next((tag for tag in tags if tag in p.stderr),'UNCLASSIFIED_CLI_ERROR')
        value={'ok':False,'error':{'code':safe}}
    return {'ok':value.get('ok') is True and p.returncode==0,
            'result':value.get('data',{}).get('result'),
            'error':value.get('error'), 'elapsed_seconds':round(time.monotonic()-started,3)}


def batch(prefix, suffix, size=2, value=1, *, sources=None):
    rows=[{'source_key':k,'value':value,'text':'商品数据测试'} for k in
          (sources or [f'{prefix}_{suffix}_r{i}' for i in range(size)])]
    payload=json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    return {'op':'batch','batch_id':f'{prefix}_{suffix}','capture_id':f'{prefix}_c_{suffix}',
            'window_key':f'{prefix}_w_{suffix}','records_json':payload,
            'payload_hash':hashlib.sha256(payload.encode()).hexdigest()}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--report',type=Path,required=True)
    args=ap.parse_args()
    if not args.execute:
        print(json.dumps({'scope':'isolated_test','source_requests':0,'cases':[
            'first_and_retry','same_id_changed_payload','invalid_digest','concurrent_same_batch',
            'write_rollback','update_existing','incomplete_manifest','window_rollback',
            'window_commit_retry','capacity_10_50_100_200','reject_201','reject_64000_bytes']}))
        return
    report=json.loads(args.report.read_text()) if args.report.exists() else {
        'schema_version':1,'scope':'isolated_test','prefix':'test_'+uuid.uuid4().hex[:16],
        'source_requests':0,'cases':[]}
    prefix=report['prefix']
    def save(name, responses, passed):
        report['cases'].append({'name':name,'passed':bool(passed),'responses':responses})
        args.report.parent.mkdir(parents=True,exist_ok=True)
        temp=args.report.with_suffix('.tmp')
        temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');temp.replace(args.report)
        print(json.dumps({'case':name,'passed':bool(passed)},ensure_ascii=False),flush=True)
        if not passed: raise RuntimeError(f'Acceptance failed: {name}; inspect {args.report}')
    p=batch(prefix,'first')
    a=call(p);b=call(p)
    save('first_and_retry',[a,b],a['ok'] and b['ok'] and a['result']==b['result'])
    bad=call(batch(prefix,'first',value=2));save('same_id_changed_payload',[bad],not bad['ok'] and bad['error']['code']=='BATCH_CONFLICT')
    badp=batch(prefix,'hash');badp['payload_hash']='0'*64
    bad=call(badp);save('invalid_digest',[bad],not bad['ok'] and bad['error']['code']=='DIGEST_MISMATCH')
    cp=batch(prefix,'concurrent')
    with ThreadPoolExecutor(max_workers=2) as pool: replies=list(pool.map(call,[cp,cp]))
    count=call({'op':'count','batch_id':cp['batch_id']})
    save('concurrent_same_batch',replies+[count],all(r['ok'] for r in replies) and
         replies[0]['result']==replies[1]['result'] and count['result']['rows'][0]['row_count']==2)
    ca=batch(prefix,'conflicting',value=1);cb=batch(prefix,'conflicting',value=9)
    with ThreadPoolExecutor(max_workers=2) as pool: conflict=list(pool.map(call,[ca,cb]))
    count=call({'op':'count','batch_id':ca['batch_id']})
    save('concurrent_conflicting_payload',conflict+[count],sum(r['ok'] for r in conflict)==1 and
         all(r['ok'] or r['error']['code']=='BATCH_CONFLICT' for r in conflict) and count['result']['rows'][0]['row_count']==2)
    rollback=batch(prefix,'rollback');rollback['fail_after_write']=True
    r=call(rollback);receipt=call({'op':'result','batch_id':rollback['batch_id']})
    count=call({'op':'count','batch_id':rollback['batch_id']})
    save('write_rollback',[r,receipt,count],not r['ok'] and receipt['result']['rows']==[] and count['result']['rows'][0]['row_count']==0)
    update=batch(prefix,'update',value=2,sources=[f'{prefix}_first_r{i}' for i in range(2)])
    r=call(update);save('update_existing',[r],r['ok'] and r['result']['updated']==2 and r['result']['inserted']==0)
    win={'op':'commit','capture_id':update['capture_id'],'window_key':update['window_key'],
         'batch_ids':[update['batch_id']],'source_rows':2}
    r=call({**win,'batch_ids':[update['batch_id'],f'{prefix}_missing']})
    save('incomplete_manifest',[r],not r['ok'])
    r=call({**win,'fail_after_write':True});check=call({'op':'window','window_key':win['window_key']})
    save('window_rollback',[r,check],not r['ok'] and check['result']['rows']==[])
    a=call(win);b=call(win);save('window_commit_retry',[a,b],a['ok'] and b['ok'] and a['result']==b['result'])
    for size in (10,50,100,200):
        p=batch(prefix,f'capacity{size}',size=size);r=call(p)
        count=call({'op':'count','batch_id':p['batch_id']})
        save(f'capacity_{size}',[r,count],r['ok'] and count['result']['rows'][0]['row_count']==size)
    for size in (50,200):
        p=batch(prefix,f'update{size}',size=size,value=3,
                sources=[f'{prefix}_capacity{size}_r{i}' for i in range(size)])
        r=call(p);count=call({'op':'count','batch_id':p['batch_id']})
        save(f'update_capacity_{size}',[r,count],r['ok'] and r['result']['updated']==size and
             count['result']['rows'][0]['row_count']==size)
    r=call(batch(prefix,'too_many',size=201));save('reject_201',[r],not r['ok'])
    p=batch(prefix,'too_big',size=1);rows=json.loads(p['records_json']);rows[0]['text']='汉'*22000
    p['records_json']=json.dumps(rows,ensure_ascii=False);p['payload_hash']=hashlib.sha256(p['records_json'].encode()).hexdigest()
    r=call(p);save('reject_64000_bytes',[r],not r['ok'])
    report['complete']=True
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
