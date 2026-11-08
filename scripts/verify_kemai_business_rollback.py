"""Verify existing-row field writes roll back. Does not collect SaaS data."""
import json
from pathlib import Path
import subprocess
import uuid
from kemai_pipeline.contracts import Batch
from kemai_pipeline.normalize import normalize

root=Path(__file__).resolve().parents[1]
config=json.loads((root/'kemai_pipeline/datasets.json').read_text())
results=[]
for stream,table in [('retail-sales','retail_sale_flows'),('retail-orders','retail_orders')]:
    fixture=json.loads((root/'.local-audits'/f'{stream}-existing-fixture.json').read_text())
    raw=json.loads(fixture['row']['payload']) if isinstance(fixture['row']['payload'],str) else fixture['row']['payload']
    raw['_pipelineValidation']=True
    record=normalize(stream,raw)
    batch=Batch('205149',stream,'2026-08-01',uuid.uuid4().hex,0,'source-id-v1',(record,)).envelope()
    params={**batch,'op':'validate','mode':'final','records_json':json.dumps(batch['records'],ensure_ascii=False,sort_keys=True,separators=(',',':'))}
    params.pop('records')
    p=subprocess.run(['lovrabet','bff','exec','--name','kemaiSyncGateway','--params',json.dumps(params,ensure_ascii=False),
      '--format','compress','--appcode','app-21522951'],capture_output=True,text=True,cwd=root)
    if p.returncode:
        print(p.stderr[-1600:]);raise RuntimeError('Rollback validation failed')
    result=json.loads(p.stdout)['data']['result']
    assert result['rolled_back'] and result['updated']==1
    p=subprocess.run(['lovrabet','data','getOne','--code',config['datasets'][table],
      '--params',json.dumps({'id':fixture['row']['id']}),'--format','compress'],capture_output=True,text=True,cwd=root)
    after=json.loads(p.stdout)['data']['result']
    assert after==fixture['row'],'Row changed despite forced rollback'
    results.append({'stream':stream,'result':result,'business_row_unchanged':True})
(root/'.local-audits/business-rollback-2026-11-08.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,ensure_ascii=False))
