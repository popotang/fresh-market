import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, writeFile, readFile, readdir, rm, stat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import vm from 'node:vm';
import {readPurchaseBatch} from '../../kemai_opencli/purchase-batch-reader.js';

async function fixture(run, count=3) {
  const dir=await mkdtemp(join(tmpdir(),'kemai-batch-'));
  const items=Array.from({length:count},(_,i)=>({id:String(i+1),key:String(i+1).padStart(64,'0')}));
  const request=join(dir,'request.json');
  await writeFile(request,JSON.stringify({schema_version:1,kind:'order',items}),{mode:0o600});
  let prepares=0,leases=0,active=false;
  const seen=[];
  const deps={
    ensurePage:async()=>{assert.ok(active);prepares++;return {route:'test'};},
    withSourceLease:async(_label,fn,timing)=>{assert.ok(!active);leases++;active=true;timing.gate_wait_seconds=0.01;try{return await fn();}finally{active=false;}},
    readPreparedPurchase:async(_page,_config,id)=>{assert.ok(active);seen.push(id);return {entity:{id,details:[],version:'7'},api_method_seconds:0.001};},
  };
  try {await run({dir,request,items,deps,seen,counts:()=>({prepares,leases})});}
  finally {await rm(dir,{recursive:true,force:true});}
}

test('one preparation, one lease per document, private durable full responses and stage timing',()=>fixture(async f=>{
  const [result]=await readPurchaseBatch({},f.request,f.deps);
  assert.deepEqual(f.counts(),{prepares:1,leases:3});
  assert.equal(result.completed,3);assert.equal(result.outcome,'complete');
  assert.equal(result.records.filter(r=>'page_prepare_seconds' in r).length,1);
  for(const item of f.items){
    const path=join(f.dir,item.key+'.json');
    const data=JSON.parse(await readFile(path));
    assert.equal(data.sha256,createHash('sha256').update(data.entity_json).digest('hex'));
    assert.equal(JSON.parse(data.entity_json).id,item.id);
    assert.equal((await stat(path)).mode&0o077,0);
  }
}));

test('HTTP 200 business rate limit stops next documents and retains prior response',()=>fixture(async f=>{
  const read=f.deps.readPreparedPurchase;
  f.deps.readPreparedPurchase=async(...args)=>{
    if(args[2]==='2')throw Error('RATE_LIMIT: HTTP 200 code 1 您的操作太频繁，请稍后再试');
    return read(...args);
  };
  await assert.rejects(readPurchaseBatch({},f.request,f.deps),/RATE_LIMIT/);
  const files=await readdir(f.dir);
  assert.ok(files.includes(f.items[0].key+'.json'));
  assert.ok(!files.includes(f.items[1].key+'.json'));
  assert.equal(f.counts().leases,2);
  const timing=JSON.parse(await readFile(join(f.dir,files.find(x=>x.startsWith('batch-timing')))));
  assert.equal(timing.completed,1);assert.equal(timing.outcome,'cooling');
}));

test('existing artifacts and invalid batches fail before source access',()=>fixture(async f=>{
  await writeFile(join(f.dir,f.items[2].key+'.json'),'{}',{mode:0o600});
  await assert.rejects(readPurchaseBatch({},f.request,f.deps),/ARTIFACT_EXISTS/);
  assert.equal(f.counts().leases,0);
}));

test('limit 10 enforced before source access',()=>fixture(async f=>{
  await assert.rejects(readPurchaseBatch({},f.request,f.deps),/batch size/);
  assert.equal(f.counts().leases,0);
},11));

test('identity mismatch stops batch without publishing wrong response',()=>fixture(async f=>{
  f.deps.readPreparedPurchase=async()=>({entity:{id:'999'},api_method_seconds:0});
  await assert.rejects(readPurchaseBatch({},f.request,f.deps),/SOURCE_IDENTITY_MISMATCH/);
  assert.equal(f.counts().leases,1);
  assert.ok(!(await readdir(f.dir)).includes(f.items[0].key+'.json'));
}));

// Execute the actual injected script against a fake Vue/API surface, not a browser.
async function preparedReader() {
  let source=await readFile(new URL('../../kemai_opencli/utils.js',import.meta.url),'utf8');
  source=source.replace("import { CliError } from '@jackwener/opencli/errors';",
    'class CliError extends Error { constructor(code,message){super(message);this.code=code;} }');
  for(const name of ['source-gate.js','source-validation.js'])
    source=source.replace(`'./${name}'`,JSON.stringify(new URL('../../kemai_opencli/'+name,import.meta.url).href));
  return (await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))).readPreparedPurchase;
}

test('prepared reader detects real HTTP 200 business failures, HTTP 429, modal and changed page',async()=>{
  const read=await preparedReader();
  let response={status:200,data:{code:0,data:{entity:{id:'1'}}}}, text='', calls=0;
  const location={href:'https://yunfan.kemai.com.cn/purchase/PurchaseOrderList'};
  const info={loadEntityApi:async()=>{calls++;return response;}};
  const document={body:{get innerText(){return text;}},querySelectorAll:()=>[
    {__vue__:{$options:{name:'ListPage'},$parent:{$refs:{info:{$refs:{infopage:info}}}}}}]};
  const page={evaluate:script=>vm.runInNewContext(script,{document,location,performance})};
  const config={route:'/purchase/PurchaseOrderList',action:'/test/'};
  assert.equal((await read(page,config,'1')).entity.id,'1');
  response={status:200,data:{code:1,msg:'您的操作太频繁，请稍后再试'}};
  await assert.rejects(read(page,config,'1'),e=>e.code==='RATE_LIMIT');
  response={status:429,data:{}};
  await assert.rejects(read(page,config,'1'),e=>e.code==='RATE_LIMIT');
  text='您的操作太频繁，请稍后再试';const before=calls;
  await assert.rejects(read(page,config,'1'),/RATE_LIMIT/);assert.equal(calls,before);
  text='';location.href='https://yunfan.kemai.com.cn/login';
  await assert.rejects(read(page,config,'1'),/PAGE_CHANGED/);assert.equal(calls,before);
});
