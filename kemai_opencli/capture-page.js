import { cli, Strategy } from '@jackwener/opencli/registry';
import { listArgs, listPageResult, TYPES } from './utils.js';
import { CliError } from '@jackwener/opencli/errors';

cli({site:'kemai',name:'capture-page',access:'read',
  description:'版本化分页捕获：返回原始行、页码、总数与观测时间，不写数据中心',
  domain:'yunfan.kemai.com.cn',strategy:Strategy.COOKIE,browser:true,navigateBefore:false,
  args:[{name:'kind',type:'string',required:true,help:'六类采购/零售列表之一'},{name:'until',type:'string',help:'零售临时快照截止时间 YYYY-MM-DD HH:mm:ss'},...listArgs],
  columns:['schema_version','stream','page','page_size','total_rows','observed_at'],
  func:async(page,args)=>{
    if(!Object.hasOwn(TYPES,args.kind)) throw new CliError('INVALID_ARGUMENT','Unsupported capture stream');
    // Retail windows use upload time (gmtCreate); preserve sale time separately for analytics.
    const result=await listPageResult(page,{...args,'time-type':'1'},args.kind);
    const total=result.totalRecord == null ? null : Number(result.totalRecord);
    if(total!==null&&(!Number.isSafeInteger(total)||total<0)) throw new CliError('API_ERROR','Invalid totalRecord');
    return [{schema_version:1,stream:args.kind,from:args.from,to:args.to,until:args.until||null,
      page:Number(args.page??1),page_size:Number(args.rows??20),total_rows:total===0?null:total,reported_total_rows:total,
      observed_at:new Date().toISOString(),rows:result.rows,query_scope:result.queryScope,
      request_count:null,request_count_basis:'not_measured_at_http_layer'}];
  }});
