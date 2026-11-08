import {awaitExportSubmission} from './export-submit.js';
import { cli, Strategy } from '@jackwener/opencli/registry';
import { ensurePage } from './utils.js';
import { withSourceLease } from './source-gate.js';

cli({
  site: 'kemai',
  name: 'purchase-receive-history-export-start',
  access: 'read',
  description: '创建历史已审核采购收货明细异步导出任务',
  domain: 'yunfan.kemai.com.cn',
  strategy: Strategy.COOKIE,
  browser: true,
  navigateBefore: false,
  args: [
    { name: 'from', type: 'string', required: true },
    { name: 'to', type: 'string', required: true },
    {name:'capture-id',type:'string',help:'32位采集版本；限定本轮导出任务，防止复用旧快照'},
  ],
  columns: ['id', 'taskNo', 'fileName', 'sheetType', 'status', 'totalRows', 'sourceUrl', 'createTime'],
  func: (page, kwargs) => withSourceLease('kemai:purchase-receive-history-export-start', async () => {
    await ensurePage(page, 'purchase-receive-history');
    const from=String(kwargs.from),to=String(kwargs.to);
    const captureId=String(kwargs['capture-id']||'');
    if(captureId&&!/^[a-f0-9]{32}$/.test(captureId))throw new Error('invalid capture-id');
    return page.evaluate(`(async()=>{
      const submitExport=${awaitExportSubmission.toString()};
      const v=[...document.querySelectorAll('#singlePageDiv *')].map(e=>e.__vue__).filter(Boolean).flatMap(v=>[v,...(v.$children||[])]).find(v=>v.$options?.name==='pi_zsReport');
      const r=v.$refs.reportPage;
      const fileName='科迈历史收货_'+${JSON.stringify(from)}+(${JSON.stringify(to)}===${JSON.stringify(from)}?'':'_'+${JSON.stringify(to)})+${JSON.stringify(captureId?'_'+captureId:'')};
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
      const params={operDateFrom:${JSON.stringify(from)}+' 00:00',operDateTo:${JSON.stringify(to)}+' 23:59',dateType:'1',queryType:'1',queryRangeType:'2'};
      Object.assign(v.queryParam||{},params);
      Object.assign(r.queryParam||{},params);
      r.queryBody={...(r.queryBody||{}),params:{...(r.queryBody?.params||{}),...params}};
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
      throw Error('导出任务未出现');
    })()`);
  }),
});
