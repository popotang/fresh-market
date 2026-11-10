import test from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {publicExportTask} from '../../kemai_opencli/export-task-projection.js';

const commands=[];
registerHooks({resolve(specifier,context,nextResolve){
  if(specifier==='@jackwener/opencli/registry'){
    const source=`export const Strategy={COOKIE:'cookie'};export function cli(config){globalThis.__taskProjectionCommands.push(config);}`;
    return {url:'data:text/javascript,'+encodeURIComponent(source),shortCircuit:true};
  }
  if(specifier==='@jackwener/opencli/errors'){
    const source=`export class CliError extends Error{constructor(code,message){super(message);this.code=code;}}`;
    return {url:'data:text/javascript,'+encodeURIComponent(source),shortCircuit:true};
  }
  if(specifier==='./source-gate.js'){
    const source=`export async function withSourceLease(name,fn){return fn();}`;
    return {url:'data:text/javascript,'+encodeURIComponent(source),shortCircuit:true};
  }
  return nextResolve(specifier,context);
}});
globalThis.__taskProjectionCommands=commands;
for(const name of ['export-task-list','item-archives-export-start','purchase-receive-history-export-start','retail-orders-export-start','retail-sales-export-start']){
  await import(`../../kemai_opencli/${name}.js`);
}

test('public export task allowlist removes signed URL and preserves capture and dedup evidence',()=>{
  const task={
    id:17,taskNo:'DC17',fileName:'capture_2026-11-08_abc',sheetType:'retailSales',status:2,totalRows:0,
    sourceUrl:'https://yunfan-store.kemai.com.cn/export.xlsx?signature=secret',createTime:'2026-11-08 10:00:00',
    gmtCreate:'2026-11-08 10:00:00',gmtModified:'2026-11-08 10:01:00',failReason:null,
    reuse_reason:'server_24h_dedup',reused_task_no:'DC16',requested_file_name:'capture_2026-11-08_abc',
    internalToken:'must-not-leak',nested:{sourceUrl:'must-not-leak'},
  };
  assert.deepEqual(publicExportTask(task),{
    id:17,taskNo:'DC17',fileName:'capture_2026-11-08_abc',sheetType:'retailSales',status:2,totalRows:0,
    createTime:'2026-11-08 10:00:00',gmtCreate:'2026-11-08 10:00:00',gmtModified:'2026-11-08 10:01:00',failReason:null,
    reuse_reason:'server_24h_dedup',reused_task_no:'DC16',requested_file_name:'capture_2026-11-08_abc',download_ready:true,
  });
  assert.equal(task.sourceUrl,'https://yunfan-store.kemai.com.cn/export.xlsx?signature=secret');
  assert.ok(!JSON.stringify(publicExportTask(task)).includes('secret'));
});

test('download readiness requires a completed task, row total, and private source URL',()=>{
  assert.equal(publicExportTask({id:1,status:2,totalRows:3}).download_ready,false);
  assert.equal(publicExportTask({id:1,status:1,totalRows:3,sourceUrl:'https://private'}).download_ready,false);
  assert.equal(publicExportTask({id:1,status:'2',totalRows:'3',sourceUrl:'https://private'}).download_ready,true);
  assert.equal(publicExportTask({id:1,status:2,totalRows:null,sourceUrl:'https://private'}).download_ready,false);
});

test('all five public export commands expose download_ready columns without sourceUrl',()=>{
  assert.equal(commands.length,5);
  for(const command of commands){
    assert.ok(command.columns.includes('download_ready'),command.name);
    assert.ok(!command.columns.includes('sourceUrl'),command.name);
  }
});

test('public commands reject invalid dates and rows before touching the source page',async()=>{
  const page={async evaluate(){throw new Error('source page must not be touched');}};
  for(const command of commands){
    const args=command.name==='export-task-list'?{rows:101}:
      command.name==='item-archives-export-start'?{snapshot:'2026-02-29'}:
      {from:'2026-02-29',to:'2026-11-08'};
    await assert.rejects(command.func(page,args),error=>error.code==='INVALID_ARGUMENT',command.name);
  }
});

test('every public command projects signed raw tasks out of its returned records',async()=>{
  const routes={
    'export-task-list':'https://yunfan.kemai.com.cn/retail/salesDetails',
    'item-archives-export-start':'https://yunfan.kemai.com.cn/archives/ItemList',
    'purchase-receive-history-export-start':'https://yunfan.kemai.com.cn/purchase/ReceiveAndReturnDetailReport',
    'retail-orders-export-start':'https://yunfan.kemai.com.cn/retail/smallTicketList',
    'retail-sales-export-start':'https://yunfan.kemai.com.cn/retail/salesDetails',
  };
  for(const command of commands){
    const raw={id:17,taskNo:'DC17',fileName:'capture',sheetType:'capture',status:2,totalRows:1,
      sourceUrl:'https://yunfan-store.kemai.com.cn/private.xlsx?signature=secret',createTime:'2026-11-08 10:00:00'};
    const page={async evaluate(script){
      if(script==='location.href')return routes[command.name];
      if(script.includes('const text=document.body?.innerText'))return '';
      if(script.startsWith('!!'))return true;
      if(script.startsWith('(async()=>'))return [raw];
      throw new Error('unexpected page evaluation');
    }};
    const args=command.name==='export-task-list'?{rows:20}:
      command.name==='item-archives-export-start'?{snapshot:'2026-11-08','expected-total':1}:
      {from:'2026-11-08',to:'2026-11-08'};
    const rows=await command.func(page,args);
    assert.equal(rows.length,1,command.name);
    assert.equal(rows[0].id,17,command.name);
    assert.equal(rows[0].download_ready,true,command.name);
    assert.ok(!Object.hasOwn(rows[0],'sourceUrl'),command.name);
    assert.ok(!JSON.stringify(rows).includes('secret'),command.name);
  }
});
