import {cli,Strategy} from '@jackwener/opencli/registry';
import {ensurePage,TYPES} from './utils.js';
import {withSourceLease} from './source-gate.js';
cli({site:'kemai',name:'query-scope',access:'read',description:'读取列表当前筛选条件与时间类型选项，不读取业务行',
 domain:'yunfan.kemai.com.cn',strategy:Strategy.COOKIE,browser:true,navigateBefore:false,
 args:[{name:'kind',type:'string',required:true}],columns:['kind','query_scope','time_options'],
 func:(page,args)=>withSourceLease('kemai:query-scope',async()=>{
 const c=await ensurePage(page,args.kind);
 return page.evaluate(`(()=>{
  const v=[...document.querySelectorAll('#singlePageDiv *')].map(e=>e.__vue__).filter(Boolean).flatMap(v=>[v,...(v.$children||[])]).find(v=>v.$options?.name===${JSON.stringify(c.component)});
  const candidates=[],seen=new Set();let n=0;
  function walk(o,path,depth){if(!o||typeof o!=='object'||seen.has(o)||depth>6||++n>3000)return;seen.add(o);
   for(const [k,x]of Object.entries(o)){if(/timeType|上传时间|销售时间/i.test(k)||(['label','title','text','name'].includes(k)&&/上传时间|销售时间/.test(String(x)))){
    try{const s=JSON.stringify(o);if(s.length<5000)candidates.push({path,value:JSON.parse(s)});}catch(e){}
   }walk(x,path+'.'+k,depth+1);}}
  walk(v.$data,'component',0);walk(v.$refs.reportPage?.$data,'report',0);
  const render=String(v.$options.render||'');
  if(!window.__kemai_req){const key='kemai_scope_'+Date.now();window.webpackJsonp.push([[key],{[key]:(m,e,r)=>{window.__kemai_req=r}},[[key]]]);}
  const apiModules=[];
  for(const [id,mod]of Object.entries(window.__kemai_req.c||{})){
   for(const value of [mod.exports,mod.exports?.a])if(typeof value?.asyncExport==='function')apiModules.push({id,method:String(value.asyncExport)});
  }
  return [{kind:${JSON.stringify(args.kind)},query_scope:v.QueryParam||v.queryParam,time_options:v.timeType||null,date_options:v.dateType||v.dateTypes||null,computed_keys:Object.keys(v.$options.computed||{}),diagnostic_fields:candidates,
   export_api:apiModules,export_code:v.$refs.reportPage?.syncExportCode,find_times:String(v.$refs.reportPage?.$options?.methods?.findTimes||''),export_method:String(v.$refs.reportPage?.$options?.methods?.toSyncExport||''),
   render_time_fields:render.match(/.{0,250}(?:timeType|dateType).{0,400}/g)||[]}];
 })()`);
 })});
