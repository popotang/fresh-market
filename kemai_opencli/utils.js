import { CliError } from '@jackwener/opencli/errors';
import { withSourceLease } from './source-gate.js';
import { validateDate, assertSourceIdentity } from './source-validation.js';

const RATE_LIMIT_RE = /(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\b429\b/i;

export async function assertNoRateLimit(page, context='page') {
  const signal=await page.evaluate(`(()=>{
    const text=document.body?.innerText||'';
    const match=text.match(/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i);
    return match?.[0]||'';
  })()`);
  if (signal) throw new CliError('RATE_LIMIT', `科迈限流（${context}）：${signal}`);
}

export const TYPES = {
  'purchase-orders': { route: '/purchase/PurchaseOrderList', component: 'ListPage', action: '/web/api/purchase/purchaseOrder/', group: 'purchase' },
  'purchase-receipts': { route: '/purchase/PurchaseReceiveList', component: 'ListPage', action: '/web/api/stock/purchaseIn/', group: 'purchase' },
  'purchase-returns': { route: '/purchase/PurchaseOutList', component: 'ListPage', action: '/web/api/stock/purchaseOut/', group: 'purchase' },
  'retail-orders': { route: '/retail/smallTicketList', component: 'smallTicketList', action: '/web/api/order/retail/', group: 'retail' },
  'retail-sales': { route: '/retail/salesDetails', component: 'SalesDetails', action: '/web/api/order/orderItemFlow/', group: 'retail' },
  'retail-promote-sharing': { route: '/retail/PromoteSharingList', component: 'smallTicketList', action: '/web/api/order/item/discount/', group: 'retail' },
  'purchase-receive-history': { route: '/purchase/ReceiveAndReturnDetailReport', component: 'pi_zsReport', action: '/web/api/stock/purchaseCommon/', group: 'history' },
};

export const listArgs = [
  { name: 'from', type: 'string', required: true, help: '开始日期 YYYY-MM-DD' },
  { name: 'to', type: 'string', required: true, help: '结束日期 YYYY-MM-DD' },
  { name: 'page', type: 'int', default: 1, help: '页码，从 1 开始' },
  { name: 'rows', type: 'int', default: 20, help: '每页 1-1000 行（历史收货用 1000 减少分页）' },
  { name: 'date-type', type: 'string', default: '0', help: '采购日期类型，默认制单时间；零售不用' },
];

export async function ensurePage(page, type) {
  if (!page) throw new CliError('INTERNAL_ERROR', 'OpenCLI browser bridge 不可用');
  const config = TYPES[type];
  if (!config) throw new CliError('INVALID_ARGUMENT', `未知类型 ${type}`);
  const target = `https://yunfan.kemai.com.cn${config.route}`;
  const current = await page.evaluate('location.href');
  if (!String(current).startsWith(target)) {
    await page.goto(target);
    await page.wait({ time: 2 });
  }
  for (let i = 0; i < 20; i++) {
    await assertNoRateLimit(page,`ensurePage:${type}`);
    const ready = await page.evaluate(`!![...document.querySelectorAll('#singlePageDiv *')].map(e=>e.__vue__).filter(Boolean).flatMap(v=>[v,...(v.$children||[])]).find(v=>v.$options?.name===${JSON.stringify(config.component)})`);
    if (ready) return config;
    await page.wait({ time: 0.3 });
  }
  throw new CliError('AUTH_REQUIRED', `页面未加载：${target}；请先在浏览器登录科迈云帆`);
}

export async function listPageResult(page, kwargs, type) {
  return withSourceLease(`kemai:${type}:list`, () => unguardedListPageResult(page, kwargs, type));
}

async function unguardedListPageResult(page, kwargs, type) {
  const from = validateDate(kwargs.from), to = validateDate(kwargs.to);
  const pageNo = Number(kwargs.page ?? 1), rows = Number(kwargs.rows ?? 20);
  if (!Number.isInteger(pageNo) || pageNo < 1 || !Number.isInteger(rows) || rows < 1 || rows > 1000) {
    throw new CliError('INVALID_ARGUMENT', '页码须大于 0，每页须为 1-1000');
  }
  const c = await ensurePage(page, type);
  if (from > to) throw new CliError('INVALID_ARGUMENT', '--from 晚于 --to');
  const until=kwargs.until==null?null:String(kwargs.until);
  if(until&&(!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(until)||!until.startsWith(to+' ')||c.group!=='retail'||until.slice(11)>'23:59:59'))throw new CliError('INVALID_ARGUMENT','Invalid retail cutoff');
  const timeType=kwargs['time-type']==null?null:String(kwargs['time-type']);
  if(timeType!==null&&!['1','2'].includes(timeType))throw new CliError('INVALID_ARGUMENT','Invalid retail time type');
  const data = { c, from, to, pageNo, rows, until, timeType, dateType: String(kwargs['date-type'] ?? '0') };
  const result = await page.evaluate(`(async()=>{
    const x=${JSON.stringify(data)};
    const v=[...document.querySelectorAll('#singlePageDiv *')].map(e=>e.__vue__).filter(Boolean).flatMap(v=>[v,...(v.$children||[])]).find(v=>v.$options?.name===x.c.component);
    if(!v) throw Error('页面组件未就绪');
    const r=x.c.group==='purchase'?v:v.$refs.reportPage;
    const params={...(x.c.group==='purchase'?v.QueryParam:v.queryParam)};
    if(x.c.group==='purchase'){
      params.operDateFrom=x.from;params.operDateTo=x.to;params.dateType=x.dateType;
    }else if(x.c.group==='retail'){
      if(x.timeType!==null){
        // Versioned capture commands query the whole configured branch, independent of UI filters.
        for(const key of ['keyword','receiptNo','memberPhone','memberGradeId','itemId','itemSubno','consumeWay','saleFlag','orderType','cashierId','salesClerkCode1','salesClerkId1','salesClerkName1'])if(key in params)params[key]='';
        for(const key of ['sourceTypeList','statusList','salesClerkCodeList'])if(key in params)params[key]=[];
        if(x.c.component==='smallTicketList'){params.dateType=x.timeType==='1'?'0':'2';delete params.timeType;}
        else params.timeType=x.timeType;
      }
      params.saleTimeFrom=x.from+' 00:00:00';params.saleTimeTo=x.until||x.to+' 23:59:59';
      params.saleTimeRange=[x.from+' 00:00:00',x.until||x.to+' 23:59:59'];
    }else{
      params.operDateFrom=x.from+' 00:00';params.operDateTo=x.to+' 23:59';
      params.dateType='1';params.queryType='1';params.queryRangeType='2';
    }
    const a=await r.getDataApi({page:x.pageNo,rows:x.rows,params,filters:[],orders:[],currentPageOrders:[]},x.c.action,true);
    const message=a?.data?.message||a?.data?.msg||'';
    const bodyText=document.body?.innerText||'';
    const signal=[a?.response?.status,a?.status,a?.data?.code,message,bodyText].join(' ');
    const rateLimited=/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test(signal);
    return {httpStatus:a?.response?.status||a?.status||200,code:a?.data?.code,message,rateLimited,
      rows:a?.data?.data?.results,totalRecord:a?.data?.data?.totalRecord,queryScope:params};
  })()`);
  if (result?.rateLimited || RATE_LIMIT_RE.test(result?.message||'')) throw new CliError('RATE_LIMIT', `科迈限流：HTTP ${result?.httpStatus||200}，业务码 ${result?.code}，${result?.message||'操作太频繁，请稍后再试'}`);
  if (result?.code !== 0 || !Array.isArray(result.rows)) throw new CliError('API_ERROR', `科迈列表请求失败：${type}；${result?.message||'未知错误'}`);
  return result;
}

export async function listPage(page, kwargs, type) {
  return (await listPageResult(page, kwargs, type)).rows;
}

export async function detail(page, kwargs, type) {
  return withSourceLease(`kemai:${type}:detail`, () => unguardedDetail(page, kwargs, type));
}

async function unguardedDetail(page, kwargs, type) {
  const c=await ensurePage(page,type);
  const id=String(kwargs.id||'');
  if(!/^\d{1,32}$/.test(id)) throw new CliError('INVALID_ARGUMENT','--id 必须是源系统单据 ID');
  if(c.group==='purchase') {
    return [(await readPreparedPurchase(page,c,id)).entity];
  }
  const saleTime=String(kwargs['sale-time']||'');
  if(!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(saleTime)) throw new CliError('INVALID_ARGUMENT','零售详情需 --sale-time "YYYY-MM-DD HH:mm:ss"');
  const result=await page.evaluate(`(async()=>{
    const v=[...document.querySelectorAll('#singlePageDiv *')].find(e=>e.__vue__?.$options?.name==='smallTicketList')?.__vue__;
    if(!v) throw Error('零售订单组件未就绪');
    v.entity=null;
    v.OnEditEntityDetails({id:${JSON.stringify(id)},gmtSale:${JSON.stringify(saleTime)}});
    for(let i=0;i<100;i++){
      if(String(v.entity?.id)===${JSON.stringify(id)}) return v.entity;
      if(v.entity&&Object.keys(v.entity).length===0){
        const message=document.querySelector('.ivu-modal-confirm-body')?.innerText||'详情接口未返回订单';
        throw Error('零售详情不可用：'+message);
      }
      await new Promise(r=>setTimeout(r,100));
    }
    const text=document.body?.innerText||'';
    if(text.includes('操作太频繁')||text.includes('稍后再试')) throw Error('科迈限流：您的操作太频繁，请稍后再试');
    throw Error('零售详情等待超时');
  })()`);
  return [result];
}

// Called after one shared page preparation; each caller must hold a per-document source lease.
export async function readPreparedPurchase(page,c,id) {
    const result=await page.evaluate(`(async()=>{
      const v=[...document.querySelectorAll('#singlePageDiv *')].find(e=>e.__vue__?.$options?.name==='ListPage')?.__vue__;
      const info=v?.$parent?.$refs?.info?.$refs?.infopage;
      if(!info) throw Error('单据详情组件未就绪');
      if(!location.href.startsWith('https://yunfan.kemai.com.cn'+${JSON.stringify(c.route)})) throw Error('PAGE_CHANGED');
      if(/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test(document.body?.innerText||'')) throw Error('RATE_LIMIT');
      const apiStarted=performance.now();
      const a=await info.loadEntityApi({param:{},id:${JSON.stringify(id)}},${JSON.stringify(c.action)});
      const message=a?.data?.message||a?.data?.msg||'';
      const bodyText=document.body?.innerText||'';
      const signal=[a?.response?.status,a?.status,a?.data?.code,message,bodyText].join(' ');
      return {apiMethodMs:performance.now()-apiStarted,httpStatus:a?.response?.status||a?.status||200,code:a?.data?.code,message,rateLimited:/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test(signal),entity:a?.data?.data?.entity};
    })()`);
    if(result?.rateLimited||RATE_LIMIT_RE.test(result?.message||'')) throw new CliError('RATE_LIMIT',`科迈限流：HTTP ${result?.httpStatus||200}，业务码 ${result?.code}，${result?.message||'操作太频繁，请稍后再试'}`);
    if(result?.code!==0) throw new CliError('API_ERROR',`采购单详情请求失败；${result?.message||'未知错误'}`);
    return {entity:assertSourceIdentity(result.entity,id), api_method_seconds:result.apiMethodMs/1000};
}
