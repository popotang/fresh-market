import { cli, Strategy } from '@jackwener/opencli/registry';
import { listArgs, listPage } from './utils.js';
cli({site:'kemai',name:'purchase-orders',access:'read',description:'采购订单列表分页',domain:'yunfan.kemai.com.cn',strategy:Strategy.COOKIE,browser:true,navigateBefore:false,args:listArgs,columns:['id','sheetNo','supplierId','receiveBranchId','status','version','gmtModified'],func:(page,args)=>listPage(page,args,'purchase-orders')});
