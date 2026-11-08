import { cli, Strategy } from '@jackwener/opencli/registry';
import { detail } from './utils.js';
cli({site:'kemai',name:'retail-order-detail',access:'read',description:'零售小票单头、商品明细与支付流水',domain:'yunfan.kemai.com.cn',strategy:Strategy.COOKIE,browser:true,navigateBefore:false,args:[{name:'id',type:'string',required:true,help:'源订单 ID'},{name:'sale-time',type:'string',required:true,help:'销售时间 YYYY-MM-DD HH:mm:ss'}],columns:['id','receiptNo','branchId','saleAmt','gmtSale'],func:(page,args)=>detail(page,args,'retail-orders')});
