import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash,randomUUID} from 'node:crypto';
const text=readFileSync('.rabetbase/bff/app-21522951/ENDPOINT/kemaiSyncGateway.js','utf8').replace('const WRITES_ENABLED=false','const WRITES_ENABLED=true');
const gateway=(await import('data:text/javascript;base64,'+Buffer.from(text).toString('base64'))).default;
const hash=v=>createHash('sha256').update(typeof v==='string'?v:JSON.stringify(v)).digest('hex');
function fixture(){
  let tables={},next=1,tail=Promise.resolve();
  const matches=(row,where)=>Object.entries(where).every(([key,filters])=>Object.entries(filters).every(([op,value])=>
    op==='$eq'?row[key]===value:op==='$in'?value.includes(row[key]):op==='$gte'?row[key]>=value:op==='$lte'?row[key]<=value:false));
  const models=state=>({byTable(name){return {
    async filter({where}){const rows=(state()[name]||[]).filter(r=>matches(r,where));return {tableData:structuredClone(rows),paging:{totalCount:rows.length}};},
    async aggregate({where,aggregate}){return {tableData:[{[aggregate[0].alias]:(state()[name]||[]).filter(r=>matches(r,where)).length}]};},
    async create(row){const all=state()[name]??=[];const created={...structuredClone(row),id:next++};all.push(created);return created.id;},
    async batchCreate(rows){const ids=[];for(const row of rows)ids.push(await this.create(row));return ids;},
    async update(row){const old=(state()[name]||[]).find(r=>r.id===row.id);assert.ok(old,'update target exists');Object.assign(old,structuredClone(row));}
  };}});
  const client={models:models(()=>tables),db:{transaction:async action=>{
    const previous=tail;let release;tail=new Promise(r=>release=r);await previous;
    let working=structuredClone(tables);
    try{const result=await action({models:models(()=>working)});tables=working;return result;}finally{release();}
  }}};
  return {call:p=>gateway(p,{client}),tables:()=>tables};
}
function scope(capture_id=randomUUID().replaceAll('-','')){return {tenant:'205149',stream:'retail-sales',capture_id,window_key:'2026-11-07',mode:'final',source_key_version:'source-id-v1'};}
function makeBatch(s,value=1,sequence=0){
  const raw=JSON.stringify({id:'123',gmtSale:'2026-11-07 12:00:00',value});
  const records=[{source_key:'123',data:{source_flow_id:'123',sold_at:'2026-11-07 12:00:00',source_created_at:'2026-11-07 12:00:00',payload:raw,payload_hash:hash(raw)}}];
  const records_json=JSON.stringify(records);
  return {...s,op:'batch.upsert',sequence,batch_id:hash(['205149',s.stream,s.capture_id,sequence]),records_json,payload_hash:hash(records_json)};
}
function commit(s,b){return {...s,op:'window.commit',batch_ids:[b.batch_id],evidence:{source_rows:1,export_total:1,export_task_id:'task',verification_method:'export_total_and_capture'}};}

test('business/raw/receipt and final event/window commit together; same batch is idempotent',async()=>{
  const f=fixture(),s=scope(),b=makeBatch(s);
  await f.call({...s,op:'capture.begin'});
  const receipt=await f.call(b);assert.equal(receipt.inserted,1);assert.deepEqual(await f.call(b),receipt);
  assert.equal(f.tables().retail_sale_flows.length,1);assert.equal(f.tables().source_capture.length,1);
  assert.equal((await f.call(commit(s,b))).state,'committed');
  assert.equal(f.tables().sync_pipeline_windows.length,1);
  assert.equal(f.tables().sync_stream_events.filter(e=>e.status==='success').length,1);
  await f.call(commit(s,b));assert.equal(f.tables().sync_stream_events.length,2);
});
test('conflicting batch and incomplete manifest cannot advance window',async()=>{
  const f=fixture(),s=scope(),b=makeBatch(s);await f.call({...s,op:'capture.begin'});await f.call(b);
  await assert.rejects(f.call(makeBatch(s,2)),/BATCH_CONFLICT/);
  await assert.rejects(f.call({...commit(s,b),batch_ids:[]}),/INCOMPLETE_CAPTURE/);
  assert.equal(f.tables().sync_pipeline_windows,undefined);
});
test('same source key across batches cannot inflate complete coverage',async()=>{
  const f=fixture(),s=scope(),a=makeBatch(s),b=makeBatch(s,2,1);await f.call({...s,op:'capture.begin'});await f.call(a);await f.call(b);
  await assert.rejects(f.call({...commit(s,a),batch_ids:[a.batch_id,b.batch_id],evidence:{...commit(s,a).evidence,source_rows:2,export_total:2}}),/COUNT_MISMATCH/);
});
test('source outside scope rejected before any business mutation',async()=>{
  const f=fixture(),s=scope();await f.call({...s,op:'capture.begin'});
  const b=makeBatch(s),rows=JSON.parse(b.records_json);rows[0].data.source_created_at='2026-11-06 12:00:00';
  b.records_json=JSON.stringify(rows);b.payload_hash=hash(b.records_json);
  await assert.rejects(f.call(b),/ROW_OUTSIDE_WINDOW/);assert.equal(f.tables().retail_sale_flows,undefined);
});
test('new capture cannot supersede a running capture; failed capture must not write',async()=>{
  const f=fixture(),s=scope(),other=scope();await f.call({...s,op:'capture.begin'});
  await assert.rejects(f.call({...other,op:'capture.begin'}),/STREAM_BUSY/);
  await f.call({...s,op:'capture.fail',reason:'source error'});await f.call({...other,op:'capture.begin'});
  await assert.rejects(f.call(makeBatch(s)),/CAPTURE_NOT_ACTIVE/);
});
test('concurrent same batch resolves under stream lock',async()=>{
  const f=fixture(),s=scope(),b=makeBatch(s);await f.call({...s,op:'capture.begin'});
  const [a,c]=await Promise.all([f.call(b),f.call(b)]);assert.deepEqual(a,c);assert.equal(f.tables().retail_sale_flows.length,1);
});
test('empty final day commits without batchCreate([])',async()=>{
  const f=fixture(),s=scope();await f.call({...s,op:'capture.begin'});
  const result=await f.call({...s,op:'window.commit',batch_ids:[],evidence:{source_rows:0,export_total:0,export_task_id:'empty',verification_method:'export_total_and_capture'}});
  assert.equal(result.state,'committed');assert.equal(f.tables().sync_pipeline_windows[0].row_count,0);
});
test('DB extra rows fail closed; no fabricated final coverage',async()=>{
  const f=fixture(),s=scope(),b=makeBatch(s);await f.call({...s,op:'capture.begin'});await f.call(b);
  f.tables().retail_sale_flows.push({id:999,tenant_id:'205149',source_flow_id:'unexpected',sold_at:'2026-11-07 13:00:00',source_created_at:'2026-11-07 13:00:00'});
  await assert.rejects(f.call(commit(s,b)),/DATABASE_WINDOW_COUNT_MISMATCH/);assert.equal(f.tables().sync_pipeline_windows,undefined);
});
test('hash observations verify both business and raw capture hashes without rewriting unchanged rows',async()=>{
  const f=fixture(),s=scope(),b=makeBatch(s);await f.call({...s,op:'capture.begin'});await f.call(b);await f.call(commit(s,b));
  const next=scope();await f.call({...next,op:'capture.begin'});
  const rawHash=JSON.parse(b.records_json)[0].data.payload_hash;
  const records_json=JSON.stringify([{source_key:'123',expected_hash:rawHash}]);
  const observed={...next,op:'batch.upsert',sequence:0,batch_id:hash(['205149',next.stream,next.capture_id,0]),records_json,payload_hash:hash(records_json)};
  const receipt=await f.call(observed);assert.equal(receipt.unchanged,1);assert.equal(receipt.inserted,0);assert.equal(receipt.updated,0);
  assert.equal(f.tables().retail_sale_flows.length,1);
});
test('changed DB row invalidates prepared observation',async()=>{
  const f=fixture(),s=scope(),b=makeBatch(s);await f.call({...s,op:'capture.begin'});await f.call(b);await f.call(commit(s,b));
  const next=scope();await f.call({...next,op:'capture.begin'});
  const records_json=JSON.stringify([{source_key:'123',expected_hash:'0'.repeat(64)}]);
  await assert.rejects(f.call({...next,op:'batch.upsert',sequence:0,batch_id:hash(['205149',next.stream,next.capture_id,0]),records_json,payload_hash:hash(records_json)}),/OBSERVED_ROW_CHANGED/);
});
test('same capture with changed completion evidence is rejected',async()=>{
  const f=fixture(),s=scope(),b=makeBatch(s);await f.call({...s,op:'capture.begin'});await f.call(b);await f.call(commit(s,b));
  const changed=commit(s,b);changed.evidence.export_task_id='other';await assert.rejects(f.call(changed),/WINDOW_MANIFEST_CONFLICT/);
});
test('late-uploaded sale belongs to upload window, not sale window',async()=>{
 const f=fixture(),s=scope(),b=makeBatch(s);await f.call({...s,op:'capture.begin'});
 const rows=JSON.parse(b.records_json);rows[0].data.sold_at='2026-11-05 23:00:00';
 b.records_json=JSON.stringify(rows);b.payload_hash=hash(b.records_json);
 await f.call(b);assert.equal((await f.call(commit(s,b))).state,'committed');
});
test('order query metadata alone does not rewrite business/raw rows; business changes still update',async()=>{
 const f=fixture();const s={...scope(),stream:'retail-orders'};
 const make=(scope,operation,value=1)=>{
  const payload=JSON.stringify({id:'123',gmtOperation:operation,gmtCreate:'2026-11-07 12:00:00',saleAmt:value});
  const records_json=JSON.stringify([{source_key:'123',data:{source_order_id:'123',receipt_no:'r1',source_created_at:'2026-11-07 12:00:00',payload,payload_hash:hash(payload)}}]);
  return {...scope,op:'batch.upsert',sequence:0,batch_id:hash(['205149',scope.stream,scope.capture_id,0]),records_json,payload_hash:hash(records_json)};
 };
 await f.call({...s,op:'capture.begin'});const a=make(s,1);await f.call(a);await f.call(commit(s,a));
 const next={...scope(),stream:'retail-orders'};await f.call({...next,op:'capture.begin'});const b=make(next,2);
 const receipt=await f.call(b);assert.equal(receipt.updated,0);assert.equal(receipt.metadata_only_unchanged,1);
 assert.equal(JSON.parse(f.tables().retail_orders[0].payload).gmtOperation,1);await f.call(commit(next,b));
 const last={...scope(),stream:'retail-orders'};await f.call({...last,op:'capture.begin'});
 const changed=await f.call(make(last,3,2));assert.equal(changed.updated,1);assert.equal(changed.metadata_only_unchanged,0);
});

test('purchase streams are blocked until the acceptance registry promotes them',async()=>{
  const f=fixture();
  const purchase={tenant:'205149',stream:'purchase-orders',capture_id:randomUUID().replaceAll('-',''),
    window_key:'2026-11-07',mode:'final',source_key_version:'purchase-id-v1'};
  await assert.rejects(()=>f.call({...purchase,op:'capture.begin'}),/STREAM_ACCEPTANCE_REQUIRED/);
  const caps=await f.call({op:'capabilities'});
  assert.deepEqual(caps.accepted_streams,['retail-orders','retail-sales']);
});

test('promotion is data-driven: the accepted list is injected, not hardcoded',async()=>{
  const path='.rabetbase/bff/app-21522951/ENDPOINT/kemaiSyncGateway.js';
  const original=readFileSync(path,'utf8');
  const injected=original.replace('__KEMAI_ACCEPTED_STREAMS__=["retail-orders", "retail-sales"]',
    '__KEMAI_ACCEPTED_STREAMS__=["purchase-orders","retail-orders","retail-sales"]');
  assert.notEqual(injected,original,'injected registry constant not found');
  const promoted=(await import('data:text/javascript;base64,'+Buffer.from(injected).toString('base64'))).default;
  const caps=await promoted({op:'capabilities'},{});
  assert.deepEqual(caps.accepted_streams,['purchase-orders','retail-orders','retail-sales']);
});
