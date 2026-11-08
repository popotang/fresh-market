import { cli, Strategy } from '@jackwener/opencli/registry';
import { listArgs, listPage } from './utils.js';
cli({site:'kemai',name:'retail-orders',access:'read',description:'零售小票订单列表分页',domain:'yunfan.kemai.com.cn',strategy:Strategy.COOKIE,browser:true,navigateBefore:false,args:listArgs,columns:['id','receiptNo','branchId','gmtSale','saleAmt','orderAmt','sourceType','saleFlag'],func:(page,args)=>listPage(page,args,'retail-orders')});
