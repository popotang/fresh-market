import test from 'node:test';
import assert from 'node:assert/strict';
import {awaitExportSubmission} from '../../kemai_opencli/export-submit.js';
function fixture(body){
 let payload;const original=async p=>{payload=p;return {status:200,data:body};};const api={asyncExport:original};
 globalThis.window={__kemai_req:{c:{m:{exports:{a:api}}}}};
 return {api,original,payload:()=>payload,report:{toSyncExport({fileName}){api.asyncExport({fileName,expandParam:JSON.stringify({filters:[{old:true}],orders:[],params:{dateType:'0'}})});}}};
}
test('await accepted export and remove inherited column filters',async()=>{
 const f=fixture({code:0});assert.deepEqual(await awaitExportSubmission(f.report,'capture'),{accepted:true});
 assert.equal(f.api.asyncExport,f.original);assert.deepEqual(JSON.parse(f.payload().expandParam).filters,[]);
});
test('HTTP 200 export rejection cannot become a pending success',async()=>{
 const f=fixture({code:1,msg:'导出列不存在'});await assert.rejects(awaitExportSubmission(f.report,'capture'),/EXPORT_REJECTED/);assert.equal(f.api.asyncExport,f.original);
});
test('HTTP 200 rate limit is detected without relying on a modal',async()=>{
 const f=fixture({code:1,msg:'您的操作太频繁，请稍后再试'});await assert.rejects(awaitExportSubmission(f.report,'capture'),/RATE_LIMIT/);assert.equal(f.api.asyncExport,f.original);
});
test('source 24-hour dedup returns only the exact source-issued task number',async()=>{
 const f=fixture({code:0});f.api.asyncExport=async()=>{throw Error('当前用户24小时内已经导出过此数据了，可以前往导出中心下载，导出任务号：DC0001202611080001');};
 const original=f.api.asyncExport;
 const result=await awaitExportSubmission(f.report,'capture');
 assert.equal(result.duplicate_task_no,'DC0001202611080001');assert.equal(result.reuse_reason,'server_24h_dedup');assert.equal(f.api.asyncExport,original);
});
