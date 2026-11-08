import { cli, Strategy } from '@jackwener/opencli/registry';
import { listArgs, listPage } from './utils.js';
cli({site:'kemai',name:'retail-sales',access:'read',description:'零售逐商品销售流水分页',domain:'yunfan.kemai.com.cn',strategy:Strategy.COOKIE,browser:true,navigateBefore:false,args:listArgs,columns:['id','sheetId','receiptNo','itemId','qty','saleAmt','gmtSale'],func:(page,args)=>listPage(page,args,'retail-sales')});
