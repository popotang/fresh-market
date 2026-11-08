import { cli, Strategy } from '@jackwener/opencli/registry';
import { ensurePage } from './utils.js';
import { withSourceLease } from './source-gate.js';

cli({
  site:'kemai',
  name:'export-task-list',
  access:'read',
  description:'查询科迈导出中心最近任务',
  domain:'yunfan.kemai.com.cn',
  strategy:Strategy.COOKIE,
  browser:true,
  navigateBefore:false,
  args:[{name:'rows',type:'int',default:20,help:'返回最近任务数'}],
  columns:['id','fileName','status','sourceUrl','gmtCreate','gmtModified'],
  func:(page,args)=>withSourceLease('kemai:export-task-list',async()=>{
    await ensurePage(page,'retail-sales');
    const rows=Number(args.rows||20);
    return page.evaluate(`(async()=>{
      const v=[...document.querySelectorAll('#singlePageDiv *')].map(e=>e.__vue__).filter(Boolean).flatMap(v=>[v,...(v.$children||[])]).find(v=>v.$options?.name==='SalesDetails');
      const r=v.$refs.reportPage;
      const a=await r.getDataApi({page:1,rows:${rows},params:{status:'',operDateFrom:'',operDateTo:''},filters:[],orders:[],currentPageOrders:[]},'/web/api/user/exportTask/',true);
      const signal=[a?.response?.status,a?.status,a?.data?.code,a?.data?.message,a?.data?.msg,document.body?.innerText||''].join(' ');
      if(/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test(signal)) throw Error('科迈限流：HTTP '+(a?.response?.status||a?.status||200)+'，业务码 '+a?.data?.code+'，'+(a?.data?.message||a?.data?.msg||'您的操作太频繁，请稍后再试'));
      if(a?.data?.code!==0) throw Error('科迈导出中心请求失败：'+(a?.data?.message||a?.data?.msg||'未知错误'));
      return a?.data?.data?.results||[];
    })()`);
  }),
});
