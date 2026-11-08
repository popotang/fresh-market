import {cli,Strategy} from '@jackwener/opencli/registry';
import {productRead} from './product-reader.js';
cli({site:'kemai',name:'product-section',access:'read',description:'版本化商品/档案原始捕获；不写数据中心',domain:'yunfan.kemai.com.cn',strategy:Strategy.COOKIE,browser:true,navigateBefore:false,args:[{name:'id',type:'string',required:true},{name:'section',type:'string',required:true},{name:'page',type:'int',default:1}],columns:['schema_version','kind','object_key','section','observed_at'],func:(page,args)=>productRead(page,'section',args)});
