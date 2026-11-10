import {cli, Strategy} from '@jackwener/opencli/registry';
import {ensurePage, readPreparedPurchase} from './utils.js';
import {withSourceLease} from './source-gate.js';
import {readPurchaseBatch} from './purchase-batch-reader.js';

cli({site:'kemai', name:'purchase-detail-batch', access:'read',
  description:'采购详情批次：复用页面、逐单限速并持久保存私有源响应',
  domain:'yunfan.kemai.com.cn', strategy:Strategy.COOKIE, browser:true, navigateBefore:false,
  args:[{name:'request-file',type:'string',required:true,help:'私有 JSON 请求绝对路径；最多 10 单'}],
  columns:['schema_version','requested','completed','outcome','command_seconds'],
  func:(page,args)=>readPurchaseBatch(page,args['request-file'],{ensurePage,readPreparedPurchase,withSourceLease})});
