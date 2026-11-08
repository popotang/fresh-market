import { cli, Strategy } from '@jackwener/opencli/registry';
import { listArgs, listPage } from './utils.js';
cli({site:'kemai',name:'purchase-receive-history',access:'read',description:'历史已审核采购收货商品行',domain:'yunfan.kemai.com.cn',strategy:Strategy.COOKIE,browser:true,navigateBefore:false,args:listArgs,columns:['id','sheetNo','sheetType','approveTime','itemId','qty','amt'],func:(page,args)=>listPage(page,args,'purchase-receive-history')});
