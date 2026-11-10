import {cli,Strategy} from '@jackwener/opencli/registry';
import {CliError} from '@jackwener/opencli/errors';
import {withSourceLease} from './source-gate.js';
import {ensurePage} from './utils.js';
import {open,mkdir,unlink} from 'node:fs/promises';
import {recoverArtifact,publishArtifact} from './artifact-files.js';
import {resolve,dirname} from 'node:path';
import {createHash} from 'node:crypto';
import {trustedExportUrl,fetchWithRedactedErrors} from './source-validation.js';

cli({site:'kemai',name:'export-download',access:'read',description:'下载指定导出任务为私有 XLSX 工件，校验类型与摘要，不写数据中心',
  domain:'yunfan.kemai.com.cn',strategy:Strategy.COOKIE,browser:true,navigateBefore:false,
  args:[{name:'task-id',type:'string',required:true},{name:'output',type:'string',required:true}],
  columns:['task_id','path','rows','bytes','sha256'],func:(page,args)=>withSourceLease('kemai:export-download',async()=>{
    const taskId=String(args['task-id']);if(!/^\d{1,32}$/.test(taskId))throw new CliError('INVALID_ARGUMENT','Invalid export task ID');
    const output=resolve(args.output);if(!output.endsWith('.xlsx'))throw new CliError('INVALID_ARGUMENT','Output must end in .xlsx');
    const previous=await recoverArtifact(output,taskId);if(previous)return [previous];
    await ensurePage(page,'retail-sales');
    const task=await page.evaluate(`(async()=>{
      const v=[...document.querySelectorAll('#singlePageDiv *')].map(e=>e.__vue__).filter(Boolean).flatMap(v=>[v,...(v.$children||[])]).find(v=>v.$options?.name==='SalesDetails');
      const a=await v.$refs.reportPage.getDataApi({page:1,rows:100,params:{},filters:[],orders:[],currentPageOrders:[]},'/web/api/user/exportTask/',true);
      const signal=[a?.response?.status,a?.status,a?.data?.code,a?.data?.message,a?.data?.msg,document.body?.innerText||''].join(' ');
      if(/太频繁|操作频繁|请?稍后再试|too many requests|rate[ _-]*limit|\\b429\\b/i.test(signal))throw Error('RATE_LIMIT 操作太频繁');
      if(a?.data?.code!==0)throw Error('Export task query failed');
      return a.data.data.results.find(r=>String(r.id)===${JSON.stringify(taskId)})||null;
    })()`);
    if(!task||String(task.status)!=='2'||!task.sourceUrl)throw new CliError('EXPORT_NOT_READY','Exact task is not ready or outside the bounded task list');
    const url=trustedExportUrl(task.sourceUrl);
    const rows=Number(task.totalRows);if(task.totalRows==null||!Number.isSafeInteger(rows)||rows<0)throw new CliError('API_ERROR','Export row total missing');
    await new Promise(r=>setTimeout(r,2000));
    const response=await fetchWithRedactedErrors(url,{redirect:'error',signal:AbortSignal.timeout(120000)});
    if(response.status===429)throw new CliError('RATE_LIMIT','Export download HTTP 429');
    if(!response.ok)throw new CliError('EXPORT_FAILED','Export download status '+response.status);
    await mkdir(dirname(output),{recursive:true,mode:0o700});
    const part=output+'.'+process.pid+'.part';const file=await open(part,'wx',0o600);
    const hash=createHash('sha256');let size=0,head=Buffer.alloc(0);
    try{
      for await(const chunk of response.body){
        const data=Buffer.from(chunk);size+=data.length;if(size>512*1024*1024)throw new CliError('ARTIFACT_LIMIT','Export exceeds 512 MiB');
        if(head.length<2048)head=Buffer.concat([head,data.subarray(0,2048-head.length)]);
        hash.update(data);await file.writeFile(data);
      }
      await file.sync();await file.close();
      if(head[0]!==0x50||head[1]!==0x4b){
        if(/太频繁|稍后再试|too many requests|rate.limit/i.test(head.toString()))throw new CliError('RATE_LIMIT','Export returned rate-limit content');
        throw new CliError('INVALID_ARTIFACT','Export is not an XLSX ZIP');
      }
      const result={schema_version:1,task_id:taskId,file_name:task.fileName,path:output,rows,bytes:size,
        sha256:hash.digest('hex'),observed_at:new Date().toISOString()};
      await publishArtifact(part,output,result);
      return [result];
    }catch(error){await file.close().catch(()=>{});await unlink(part).catch(()=>{});throw error;}
  })});
