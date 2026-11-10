import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,copyFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

async function fixture(fn){
  const root=await mkdtemp(join(tmpdir(),'kemai-product-reader-'));
  try{
    await writeFile(join(root,'package.json'),JSON.stringify({type:'module'}));
    const pkg=join(root,'node_modules/@jackwener/opencli');await mkdir(pkg,{recursive:true});
    await writeFile(join(pkg,'package.json'),JSON.stringify({type:'module',exports:{'./errors':'./errors.js'}}));
    await writeFile(join(pkg,'errors.js'),'export class CliError extends Error{constructor(code,message){super(message);this.code=code}}');
    for(const name of ['product-reader.js','product-endpoints.js'])await copyFile(new URL('../../kemai_opencli/'+name,import.meta.url),join(root,name));
    await writeFile(join(root,'utils.js'),'export async function assertNoRateLimit(){}');
    await writeFile(join(root,'source-gate.js'),'export async function withSourceLease(name,fn){return fn()}');
    const {productRead}=await import(pathToFileURL(join(root,'product-reader.js')));
    const scripts=[];let response={code:0,data:{results:[],totalRecord:0}};
    const page={evaluate:async script=>{scripts.push(script);return script==='location.href'?'https://yunfan.kemai.com.cn/archives/ItemList':response}};
    await fn({productRead,page,scripts,setResponse:v=>{response=v}});
  }finally{await rm(root,{recursive:true,force:true})}
}

test('product list uses explicit empty query instead of active UI filters',()=>fixture(async({productRead,page,scripts})=>{
  const rows=await productRead(page,'list',{page:1,rows:100});
  assert.deepEqual(rows[0].query_scope,{});
  assert.ok(scripts[1].includes('params:{},filters:[],orders:[]'));
  assert.ok(!scripts[1].includes('v.QueryParam'));
}));
test('HTTP-200 business rate limit fails before returning a product envelope',()=>fixture(async({productRead,page,setResponse})=>{
  setResponse({code:1,message:'您的操作太频繁，请稍后再试',rateLimited:true});
  await assert.rejects(productRead(page,'list',{}),e=>e.code==='RATE_LIMIT');
}));
test('detail ID mismatch is rejected',()=>fixture(async({productRead,page,setResponse})=>{
  setResponse({code:0,data:{entity:{id:'2'}}});
  await assert.rejects(productRead(page,'detail',{id:'1'}),e=>e.code==='API_ERROR');
}));
test('invalid ID and unpaginated section offset reject before browser access',()=>fixture(async({productRead,page,scripts})=>{
  await assert.rejects(productRead(page,'detail',{id:'bad'}),e=>e.code==='INVALID_ARGUMENT');
  await assert.rejects(productRead(page,'section',{id:'1',section:'barcode',page:2}),e=>e.code==='INVALID_ARGUMENT');
  assert.equal(scripts.length,0);
}));
test('missing list results is not treated as an empty list',()=>fixture(async({productRead,page,setResponse})=>{
  setResponse({code:0,data:{}});
  await assert.rejects(productRead(page,'list',{}),e=>e.code==='API_ERROR');
}));
