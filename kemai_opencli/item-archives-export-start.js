import { cli, Strategy } from '@jackwener/opencli/registry';
import { CliError } from '@jackwener/opencli/errors';
import { assertNoRateLimit } from './utils.js';
import { withSourceLease } from './source-gate.js';
import { publicExportTask } from './export-task-projection.js';
import { validateDate } from './source-validation.js';

cli({
  site: 'kemai',
  name: 'item-archives-export-start',
  access: 'read',
  description: '创建商品档案全量异步导出任务，用于核验可批量取得的字段',
  domain: 'yunfan.kemai.com.cn',
  strategy: Strategy.COOKIE,
  browser: true,
  navigateBefore: false,
  args: [
    { name: 'snapshot', type: 'string', required: true, help: '导出任务创建日期 YYYY-MM-DD，用于复用当天任务' },
    { name: 'expected-total', type: 'int', help: '商品清单总数；只复用行数相同的已完成任务' },
  ],
  columns: ['id', 'taskNo', 'fileName', 'sheetType', 'status', 'totalRows', 'download_ready', 'createTime'],
  func: (page, kwargs) => withSourceLease('kemai:item-archives-export-start', async () => {
    const snapshot = validateDate(kwargs.snapshot);
    const expectedTotal = kwargs['expected-total'] == null ? null : Number(kwargs['expected-total']);
    if (expectedTotal !== null && (!Number.isInteger(expectedTotal) || expectedTotal < 0)) {
      throw new CliError('INVALID_ARGUMENT', '--expected-total 必须是非负整数');
    }
    const target = 'https://yunfan.kemai.com.cn/archives/ItemList';
    const current = await page.evaluate('location.href');
    if (!String(current).startsWith(target)) {
      await page.goto(target);
      await page.wait({ time: 2 });
    }
    for (let attempt = 0; attempt < 20; attempt++) {
      await assertNoRateLimit(page, 'item-archives-export-page');
      const ready = await page.evaluate(`!![...document.querySelectorAll('#singlePageDiv *')]
        .map(e=>e.__vue__).filter(Boolean).flatMap(v=>[v,...(v.$children||[])])
        .find(v=>v.$options?.name==='ListPage' && v.syncExportCode==='itemArchives')`);
      if (ready) break;
      if (attempt === 19) throw new CliError('AUTH_REQUIRED', '商品档案页面未加载；请确认已登录科迈云帆');
      await page.wait({ time: 0.3 });
    }
    const result = await page.evaluate(`(async()=>{
      const v=[...document.querySelectorAll('#singlePageDiv *')]
        .map(e=>e.__vue__).filter(Boolean).flatMap(v=>[v,...(v.$children||[])])
        .find(v=>v.$options?.name==='ListPage'&&v.syncExportCode==='itemArchives');
      if(!v) throw Error('商品档案组件未就绪');
      const fileName='科迈商品档案_'+${JSON.stringify(snapshot)};
      const expectedTotal=${JSON.stringify(expectedTotal)};
      const assertNoLimit=(message='')=>{
        const signal=[message,document.body?.innerText||''].join(' ');
        const match=signal.match(/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i);
        if(match) throw Error('科迈限流（业务响应/页面）：'+match[0]);
      };
      const list=async()=>{
        assertNoLimit();
        const a=await v.getDataApi({page:1,rows:100,params:{status:'',operDateFrom:'',operDateTo:''},filters:[],orders:[],currentPageOrders:[]},'/web/api/user/exportTask/',true);
        assertNoLimit([a?.response?.status,a?.status,a?.data?.code,a?.data?.message,a?.data?.msg].join(' '));
        if(a?.data?.code!==0) throw Error('导出任务列表异常：'+(a?.data?.message||a?.data?.msg||a?.data?.code));
        return a?.data?.data?.results||[];
      };
      const beforeRows=await list();
      // Reuse an in-progress task too. A retry must not create a second export
      // just because the first task has not populated totalRows yet.
      // The UI names every item export "商品档案" regardless of fileName.
      // Match the source task's creation day and, when known, its row count.
      const reusable=beforeRows.find(x=>x.sheetType==='itemArchives'&&
        String(x.createTime||x.gmtCreate||'').startsWith(${JSON.stringify(snapshot)})&&
        !x.failReason&&String(x.status)!=='3'&&
        (expectedTotal===null||x.totalRows==null||Number(x.totalRows)===expectedTotal));
      if(reusable) return [reusable];
      const before=new Set(beforeRows.map(x=>String(x.id)));
      await new Promise(resolve=>setTimeout(resolve,2000));
      v.toSyncExport({fileName});
      for(let i=0;i<10;i++){
        await new Promise(resolve=>setTimeout(resolve,2000));
        const after=await list();
        const created=after.filter(x=>!before.has(String(x.id))&&x.sheetType==='itemArchives');
        if(created.length) return created;
      }
      throw Error('商品档案导出任务创建后未在导出中心出现');
    })()`);
    return result.map(publicExportTask);
  }),
});
