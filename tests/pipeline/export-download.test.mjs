import test from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {mkdtemp,rm,readFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const commands=[];
const leaseEvents=[];
registerHooks({resolve(specifier,context,nextResolve){
  if(specifier==='@jackwener/opencli/registry'){
    const source=`export const Strategy={COOKIE:'cookie'};export function cli(config){globalThis.__downloadCommands.push(config);}`;
    return {url:'data:text/javascript,'+encodeURIComponent(source),shortCircuit:true};
  }
  if(specifier==='@jackwener/opencli/errors'){
    const source=`export class CliError extends Error{constructor(code,message){super(message);this.code=code;this.name='CliError';}}`;
    return {url:'data:text/javascript,'+encodeURIComponent(source),shortCircuit:true};
  }
  if(specifier==='./source-gate.js'&&context.parentURL?.endsWith('/export-download.js')){
    const source=`export async function withSourceLease(name,fn){globalThis.__leaseDepth++;globalThis.__leaseEvents.push(['enter',name,globalThis.__leaseDepth]);try{return await fn();}finally{if(globalThis.__onLeaseExit)globalThis.__leaseExitEvidence=globalThis.__onLeaseExit();globalThis.__leaseEvents.push(['exit',name,globalThis.__leaseDepth]);globalThis.__leaseDepth--;}}`;
    return {url:'data:text/javascript,'+encodeURIComponent(source),shortCircuit:true};
  }
  if(specifier==='./utils.js'&&context.parentURL?.endsWith('/export-download.js')){
    const source=`export async function ensurePage(){}`;
    return {url:'data:text/javascript,'+encodeURIComponent(source),shortCircuit:true};
  }
  return nextResolve(specifier,context);
}});
globalThis.__downloadCommands=commands;
globalThis.__leaseEvents=leaseEvents;
globalThis.__leaseDepth=0;
await import('../../kemai_opencli/export-download.js');
const command=commands.find(item=>item.name==='export-download');
const signed='https://yunfan-store.kemai.com.cn/private/history.xlsx?signature=secret';

function pageFor(task={id:'17',status:2,totalRows:1,fileName:'history.xlsx',sourceUrl:signed}){
  return {async evaluate(){leaseEvents.push(['task-query','kemai:export-download',globalThis.__leaseDepth]);return task;}};
}
function response(bytes){return {ok:true,status:200,body:(async function*(){yield bytes;})()};}
async function fixture(fn){
  const directory=await mkdtemp(join(tmpdir(),'kemai-download-test-'));
  const originalFetch=globalThis.fetch;
  leaseEvents.length=0;globalThis.__onLeaseExit=null;globalThis.__leaseExitEvidence=null;
  try{await fn(directory);}finally{globalThis.fetch=originalFetch;globalThis.__onLeaseExit=null;await rm(directory,{recursive:true,force:true});}
}

test('task query, fetch, and artifact publication stay inside the source lease',()=>fixture(async directory=>{
  const output=join(directory,'private','history.xlsx');
  globalThis.fetch=async url=>{assert.equal(url.href,signed);leaseEvents.push(['fetch','kemai:export-download',globalThis.__leaseDepth]);return response(Buffer.from('PK\x03\x04offline-xlsx'));};
  globalThis.__onLeaseExit=()=>({file:existsSync(output),manifest:existsSync(output+'.meta.json')});
  const rows=await command.func(pageFor(),{'task-id':'17',output});
  assert.equal(rows.length,1);assert.equal(rows[0].task_id,'17');assert.equal(rows[0].rows,1);
  assert.equal(await readFile(output,'utf8'),'PK\x03\x04offline-xlsx');
  assert.deepEqual(globalThis.__leaseExitEvidence,{file:true,manifest:true});
  assert.deepEqual(leaseEvents.map(event=>event[0]),['enter','task-query','fetch','exit']);
  assert.ok(leaseEvents.every(event=>event[2]===1));
  assert.ok(!JSON.stringify(rows).includes('secret'));
}));

test('network fetch failure is redacted and publishes no artifact',()=>fixture(async directory=>{
  const output=join(directory,'history.xlsx');
  globalThis.fetch=async()=>{throw new Error(`connection failed ${signed}`);};
  await assert.rejects(command.func(pageFor(),{'task-id':'17',output}),error=>{
    assert.equal(error.code,'EXPORT_FAILED');
    assert.ok(!String(error).includes('secret'));
    assert.ok(!String(error.stack).includes('signature=secret'));
    assert.equal(error.cause,undefined);
    return true;
  });
  assert.equal(existsSync(output),false);
  assert.equal(existsSync(output+'.meta.json'),false);
}));
