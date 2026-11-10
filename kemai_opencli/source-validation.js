function failure(code,message){
  const error=new Error(`${code}: ${message}`);
  error.code=code;
  return error;
}

export function validateDate(value){
  const text=String(value);
  const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if(!match)throw failure('INVALID_ARGUMENT','日期必须是 YYYY-MM-DD');
  const [year,month,day]=match.slice(1).map(Number);
  const date=new Date(Date.UTC(year,month-1,day));
  if(year===0||date.getUTCFullYear()!==year||date.getUTCMonth()!==month-1||date.getUTCDate()!==day){
    throw failure('INVALID_ARGUMENT','日期必须是真实日历日期 YYYY-MM-DD');
  }
  return text;
}

export function validateTaskRows(value){
  const rows=Number(value);
  if(value==null||value===''||!Number.isInteger(rows)||rows<1||rows>100){
    throw failure('INVALID_ARGUMENT','导出任务查询行数须为 1-100');
  }
  return rows;
}

export function assertSourceIdentity(entity,requestedId){
  if(!entity||typeof entity!=='object'||entity.id==null||String(entity.id)!==String(requestedId)){
    throw failure('SOURCE_IDENTITY_MISMATCH','采购详情源单据 ID 与请求 ID 不一致');
  }
  return entity;
}

export function trustedExportUrl(value){
  let url;
  try{url=new URL(value);}catch{throw failure('UNTRUSTED_EXPORT','Unexpected export origin');}
  const authority=/^[a-z][a-z\d+.-]*:\/\/([^/?#]*)/i.exec(String(value))?.[1]||'';
  if(url.protocol!=='https:'||url.hostname!=='yunfan-store.kemai.com.cn'||authority.toLowerCase()!==url.hostname||url.port||url.username||url.password){
    throw failure('UNTRUSTED_EXPORT','Unexpected export origin');
  }
  return url;
}

export async function fetchWithRedactedErrors(url,options={},fetchImpl=globalThis.fetch){
  try{return await fetchImpl(url,options);}
  catch{throw failure('EXPORT_FAILED','Export download request failed');}
}
