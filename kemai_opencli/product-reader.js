import {CliError} from '@jackwener/opencli/errors';
import {assertNoRateLimit} from './utils.js';
import {withSourceLease} from './source-gate.js';
import {SECTIONS,MASTERS} from './product-endpoints.js';
const route='https://yunfan.kemai.com.cn/archives/ItemList';
export async function productRead(page,kind,args){
  const id=String(args.id||'');
  if(['detail','section'].includes(kind)&&!/^\d{1,32}$/.test(id))throw new CliError('INVALID_ARGUMENT','Invalid item ID');
  const number=Number(args.page??1),size=Number(args.rows??100);
  if(!Number.isInteger(number)||number<1||!Number.isInteger(size)||size<1||size>100)throw new CliError('INVALID_ARGUMENT','page>=1; rows 1..100');
  if(kind==='section'&&!Object.hasOwn(SECTIONS,args.section))throw new CliError('INVALID_ARGUMENT','Unknown item section');
  if(kind==='section'&&args.section!=='history_price'&&number!==1)throw new CliError('INVALID_ARGUMENT','This section is not paginated');
  if(kind==='master'&&!Object.hasOwn(MASTERS,args.kind))throw new CliError('INVALID_ARGUMENT','Unknown master kind');
  return withSourceLease('kemai:product:'+kind,async()=>{
    if(!String(await page.evaluate('location.href')).startsWith(route)){await page.goto(route);await page.wait({time:2});}
    await assertNoRateLimit(page,'product:'+kind);
    const request={kind,id,page:number,rows:size,section:args.section,
      endpoint:kind==='master'?MASTERS[args.kind]:kind==='section'?SECTIONS[args.section][0]:null,
      extra:kind==='section'?SECTIONS[args.section][1]:null};
    const result=await page.evaluate(`(async()=>{
      const x=${JSON.stringify(request)};
      let response;
      if(x.kind==='list'){
        const v=[...document.querySelectorAll('#singlePageDiv *')].find(e=>e.__vue__?.$options?.name==='ListPage')?.__vue__;
        if(!v)throw Error('AUTH_REQUIRED: item list unavailable');
        response=await v.getDataApi({page:x.page,rows:x.rows,params:v.QueryParam,filters:[],orders:[]},v.Action,true);
      }else if(x.kind==='detail'){
        const v=document.querySelector('#itemInfo')?.__vue__?.$refs?.infopage;
        if(!v)throw Error('AUTH_REQUIRED: item detail unavailable');
        response=await v.loadEntityApi({param:{},id:x.id},v.Action);
      }else{
        if(!window.__kemai_req){const key='kemai_reader_'+Date.now();window.webpackJsonp.push([[key],{[key]:(m,e,r)=>{window.__kemai_req=r}},[[key]]]);}
        const data=x.kind==='master'?{page:x.page,rows:x.rows,params:{},filters:[],orders:[]}:
          x.section==='history_price'?{page:x.page,rows:10,params:{itemId:x.id,sourceSheetTypes:['PI','SX'],queryByAuthBranch:true,querySource:'1'}}:
          Object.assign({itemId:x.id},x.extra||{});
        response=await window.__kemai_req('66df').a.request({url:x.endpoint,data,method:'post',hideRuning:true});
      }
      const code=response?.data?.code,message=response?.data?.msg||response?.data?.message||'';
      const signal=[response?.response?.status,response?.status,code,message,document.body?.innerText||''].join(' ');
      return {code,message,rateLimited:/(?:您的)?操作(?:过于)?频繁|太频繁|请?稍后再试|too many requests|rate[ _-]*limit|\\b429\\b/i.test(signal),data:response?.data?.data};
    })()`);
    if(result?.rateLimited)throw new CliError('RATE_LIMIT','科迈操作太频繁，请稍后再试');
    if(result?.code!==0||result.data==null)throw new CliError('API_ERROR','Product source response invalid');
    if((kind==='list'||kind==='master')&&!Array.isArray(result.data.results))throw new CliError('API_ERROR','List results array missing');
    if(kind==='detail'&&String(result.data.entity?.id)!==id)throw new CliError('API_ERROR','Item identity mismatch');
    return [{schema_version:1,kind,object_key:id||null,section:args.section||null,
      page:number,page_size:kind==='section'&&args.section==='history_price'?10:size,
      observed_at:new Date().toISOString(),data:result.data}];
  });
}
