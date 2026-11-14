import { cli, Strategy } from '@jackwener/opencli/registry';
import { listArgs, listPage } from './utils.js';
cli({site:'kemai',name:'retail-promote-sharing',access:'read',description:'零售让利明细分页（营销优惠金额，非优惠券核销）',domain:'yunfan.kemai.com.cn',strategy:Strategy.COOKIE,browser:true,navigateBefore:false,args:listArgs,columns:['id','sheetId','sheetNo','type','promotionCode','amt','gmtSale'],func:(page,args)=>listPage(page,args,'retail-promote-sharing')});
