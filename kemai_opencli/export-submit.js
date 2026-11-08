/** Execute inside the authenticated page; observe the actual promise behind its export button. */
export async function awaitExportSubmission(report,fileName) {
  if(!window.__kemai_req){const key='kemai_export_'+Date.now();window.webpackJsonp.push([[key],{[key]:(m,e,r)=>{window.__kemai_req=r}},[[key]]]);}
  const apis=new Set();
  for(const mod of Object.values(window.__kemai_req.c||{})){
    for(const api of [mod.exports,mod.exports?.a])if(typeof api?.asyncExport==='function')apis.add(api);
  }
  if(!apis.size)throw Error('EXPORT_ADAPTER_UNAVAILABLE: no loaded export API');
  const restore=[];let submitted=null;
  try{
    for(const api of apis){
      const original=api.asyncExport;
      api.asyncExport=function(payload,...rest){
        if(payload?.fileName!==fileName)return original.call(this,payload,...rest);
        if(submitted)throw Error('EXPORT_CONFLICT: multiple submissions for one filename');
        // Match list pagination: no inherited column filters or stale sort criteria.
        const expanded=JSON.parse(payload.expandParam);
        expanded.filters=[];expanded.orders=[];expanded.currentPageOrders=[];
        payload={...payload,expandParam:JSON.stringify(expanded)};
        submitted=original.call(this,payload,...rest);
        return submitted;
      };
      restore.push(()=>{api.asyncExport=original;});
    }
    report.toSyncExport({fileName});
    if(!submitted)throw Error('EXPORT_ADAPTER_UNAVAILABLE: export API was not called');
    const response=await submitted;
    const body=response?.data;
    const signal=[response?.status,response?.response?.status,body?.code,body?.msg,body?.message].join(' ');
    if(/太频繁|操作频繁|请?稍后再试|too many requests|rate[ _-]*limit|\b429\b/i.test(signal))throw Error('RATE_LIMIT 操作太频繁');
    if(body?.code!==0)throw Error('EXPORT_REJECTED: '+String(body?.msg||body?.message||body?.code||'invalid response').slice(0,250));
    return {accepted:true};
  }catch(error){
    const message=String(error?.response?.data?.msg||error?.response?.data?.message||error?.message||error);
    const duplicate=message.match(/导出任务号[：:]\s*([A-Za-z0-9_-]+)/);
    if(/24小时内已经导出/.test(message)&&duplicate)return {accepted:false,duplicate_task_no:duplicate[1],reuse_reason:'server_24h_dedup'};
    if(/太频繁|操作频繁|请?稍后再试|too many requests|rate[ _-]*limit|\b429\b/i.test(message))throw Error('RATE_LIMIT 操作太频繁');
    throw error;
  }finally{for(const undo of restore)undo();}
}
