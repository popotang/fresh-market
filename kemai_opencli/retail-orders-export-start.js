import {awaitExportSubmission} from './export-submit.js';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { ensurePage } from './utils.js';
import { withSourceLease } from './source-gate.js';
import { publicExportTask } from './export-task-projection.js';
import { validateDate } from './source-validation.js';

const args = [
  { name: 'from', type: 'string', required: true, help: '开始日期 YYYY-MM-DD' },
  { name: 'to', type: 'string', required: true, help: '结束日期 YYYY-MM-DD' },
    {name:'time-type',type:'string',help:'1=上传时间，2=当班日期；缺省保留源页面设置'},
    {name:'scope-json',type:'string',help:'与采集页一致的查询条件 JSON'},
    {name:'capture-id',type:'string',help:'32位采集版本；限定本轮导出任务，防止复用旧快照'},
];

cli({
  site: 'kemai',
  name: 'retail-orders-export-start',
  access: 'read',
  description: '创建零售订单明细异步导出任务并返回新任务或可复用任务',
  domain: 'yunfan.kemai.com.cn',
  strategy: Strategy.COOKIE,
  browser: true,
  navigateBefore: false,
  args,
  columns: ['id', 'taskNo', 'fileName', 'sheetType', 'status', 'totalRows', 'download_ready', 'createTime'],
  func: (page, kwargs) => withSourceLease('kemai:retail-orders-export-start', async () => {
    const from=validateDate(kwargs.from),to=validateDate(kwargs.to);
    await ensurePage(page, 'retail-orders');
    const captureId=String(kwargs['capture-id']||'');
    const timeType=kwargs['time-type']==null?null:String(kwargs['time-type']);
    if(timeType!==null&&!['1','2'].includes(timeType))throw new Error('invalid time-type');
    const queryScope=kwargs['scope-json']?JSON.parse(kwargs['scope-json']):{};
    if(!queryScope||Array.isArray(queryScope)||typeof queryScope!=='object')throw new Error('invalid scope-json');
    if(captureId&&!/^[a-f0-9]{32}$/.test(captureId))throw new Error('invalid capture-id');
    const result=await page.evaluate(`(async()=>{
      const submitExport=${awaitExportSubmission.toString()};
      const v=[...document.querySelectorAll('#singlePageDiv *')].map(e=>e.__vue__).filter(Boolean).flatMap(v=>[v,...(v.$children||[])]).find(v=>v.$options?.name==='smallTicketList');
      const r=v.$refs.reportPage;
      const fileName='科迈零售订单_'+${JSON.stringify(from)}+(${JSON.stringify(to)}===${JSON.stringify(from)}?'':'_'+${JSON.stringify(to)})+${JSON.stringify(captureId?'_'+captureId:'')};
      const assertNoLimit=(message='')=>{const signal=[message,document.body?.innerText||''].join(' '),match=signal.match(/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i);if(match)throw Error('科迈限流（业务响应/页面）：'+match[0])};
      const list=async()=>{
        assertNoLimit();
        const a=await r.getDataApi({page:1,rows:100,params:{status:'',operDateFrom:'',operDateTo:''},filters:[],orders:[],currentPageOrders:[]},'/web/api/user/exportTask/',true);
        assertNoLimit([a?.response?.status,a?.status,a?.data?.code,a?.data?.message,a?.data?.msg].join(' '));
        return a?.data?.data?.results||[];
      };
      const beforeRows=await list();
      const reusable=beforeRows.find(x=>x.fileName===fileName&&!x.failReason&&String(x.status)!=='3');
      if(reusable) return [reusable];
      const before=new Set(beforeRows.map(x=>String(x.id)));
      const params={...${JSON.stringify(queryScope)},...${JSON.stringify(timeType===null?{}:{dateType:timeType==='1'?'0':'2'})},saleTimeFrom:${JSON.stringify(from)}+' 00:00:00',saleTimeTo:${JSON.stringify(to)}+' 23:59:59',saleTimeRange:[${JSON.stringify(from)}+' 00:00:00',${JSON.stringify(to)}+' 23:59:59']};
      Object.assign(v.queryParam||{},params);
      Object.assign(r.queryParam||{},params);
      r.queryBody={...(r.queryBody||{}),filters:[],orders:[],params:{...(r.queryBody?.params||{}),...params}};
      await new Promise(resolve=>setTimeout(resolve,2000));
      const submission=await submitExport(r,fileName);
      if(submission.duplicate_task_no){
        await new Promise(resolve=>setTimeout(resolve,2000));
        const matches=(await list()).filter(x=>String(x.taskNo)===submission.duplicate_task_no);
        if(matches.length!==1)throw Error('SOURCE_DEDUP_TASK_UNAVAILABLE: exact source-returned task number not in recent tasks');
        return [{...matches[0],reuse_reason:submission.reuse_reason,reused_task_no:submission.duplicate_task_no,requested_file_name:fileName}];
      }
      for(let i=0;i<10;i++){
        await new Promise(resolve=>setTimeout(resolve,2000));
        const after=await list();
        const created=after.filter(x=>!before.has(String(x.id))&&x.fileName===fileName);
        if(created.length) return created;
      }
      assertNoLimit();
      await new Promise(resolve=>setTimeout(resolve,2000));
      const latest=(await list()).find(x=>x.fileName===fileName&&!x.failReason&&String(x.status)!=='3');
      if(latest) return [latest];
      throw Error('导出任务创建后未在导出中心出现且无可复用任务');
    })()`);
    return result.map(publicExportTask);
  }),
});
