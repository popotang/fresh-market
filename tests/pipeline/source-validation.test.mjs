import test from 'node:test';
import assert from 'node:assert/strict';
import {validateDate,validateTaskRows,assertSourceIdentity,trustedExportUrl,fetchWithRedactedErrors} from '../../kemai_opencli/source-validation.js';

test('date validation rejects normalized impossible calendar dates',()=>{
  assert.equal(validateDate('2028-02-29'),'2028-02-29');
  for(const value of ['2026-02-29','2026-11-31','2026-13-01','2026-1-01','not-a-date']){
    assert.throws(()=>validateDate(value),error=>error.code==='INVALID_ARGUMENT',value);
  }
});

test('bounded export task rows accepts only integers from 1 through 100',()=>{
  assert.equal(validateTaskRows(1),1);
  assert.equal(validateTaskRows('100'),100);
  for(const value of [0,101,1.5,NaN,null,'20x'])assert.throws(()=>validateTaskRows(value),error=>error.code==='INVALID_ARGUMENT');
});

test('purchase identity compares canonical strings and fails closed for missing or different IDs',()=>{
  const entity={id:42,details:[]};
  assert.equal(assertSourceIdentity(entity,'42'),entity);
  for(const [value,requested] of [[{},'42'],[{id:41},'42'],[null,'42']]){
    assert.throws(()=>assertSourceIdentity(value,requested),error=>error.code==='SOURCE_IDENTITY_MISMATCH');
  }
});

test('trusted export origin rejects credentials, ports, other hosts, and non-HTTPS URLs',()=>{
  const signed='https://yunfan-store.kemai.com.cn/file.xlsx?signature=secret';
  assert.equal(trustedExportUrl(signed).href,signed);
  for(const value of [signed.replace('https:','http:'),signed.replace('yunfan-store.kemai.com.cn','evil.example'),signed.replace('.com.cn/','.com.cn:443/'),signed.replace('.com.cn/','.com.cn@evil.example/'),'not a url signature=secret']){
    assert.throws(()=>trustedExportUrl(value),error=>error.code==='UNTRUSTED_EXPORT'&&!String(error).includes('secret'),value);
  }
});

test('fetch errors never retain a signed URL in message, stack, or cause',async()=>{
  const signed='https://yunfan-store.kemai.com.cn/file.xlsx?signature=secret';
  const response={ok:true,status:200};
  assert.equal(await fetchWithRedactedErrors(signed,{},async()=>response),response);
  await assert.rejects(fetchWithRedactedErrors(signed,{},async()=>{throw new Error(`fetch failed ${signed}`);}),error=>{
    assert.equal(error.code,'EXPORT_FAILED');
    assert.ok(!String(error).includes('secret'));
    assert.ok(!String(error.stack).includes('secret'));
    assert.equal(error.cause,undefined);
    return true;
  });
});
