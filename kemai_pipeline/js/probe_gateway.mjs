/** Standard model API isolation harness. No SaaS or business-table writes. */
export default async function kemaiPipelineProbeGateway(params, context) {
  const id = (value, size=64) => {
    if(typeof value!=='string'||!/^test_[a-zA-Z0-9_-]+$/.test(value)||value.length>size)
      throw new Error('INVALID_TEST_ID');
    return value;
  };
  const model=(client,name)=>client.models.byTable('kemai_pipeline_probe_'+name,{dblinkId:10510});
  const decode=value=>typeof value==='string'?JSON.parse(value):value;
  const canonical=value=>JSON.stringify(value, function(key,item){
    if(item&&typeof item==='object'&&!Array.isArray(item)) return Object.keys(item).sort().reduce((out,k)=>{out[k]=item[k];return out;},{});
    return item;
  });
  const read=async(client,table,where,limit=200)=>{
    for(let attempt=0;attempt<3;attempt++){
      const r=await model(client,table).filter({where,currentPage:1,pageSize:limit});
      if(Array.isArray(r.tableData)&&r.paging&&Number(r.paging.totalCount)===r.tableData.length) return r.tableData;
      if(r.paging&&Number(r.paging.totalCount)>limit) break;
    }
    throw new Error('INCOMPLETE_MODEL_READ');
  };
  const receipt=async batch_id=>{
    const rows=await read(context.client,'batches',{batch_id:{$eq:batch_id}},2);
    return rows;
  };
  if(params.op==='result') return {rows:await receipt(id(params.batch_id))};
  if(params.op==='window') return {rows:await read(context.client,'windows',{window_key:{$eq:id(params.window_key,128)}},2)};
  if(params.op==='count') {
    const r=await model(context.client,'records').aggregate({where:{last_batch_id:{$eq:id(params.batch_id)}},
      aggregate:[{column:'source_key',type:'COUNT',alias:'row_count'}]});
    return {rows:r.tableData};
  }
  if(params.op==='batch') {
    const batch_id=id(params.batch_id),capture_id=id(params.capture_id),window_key=id(params.window_key,128);
    if(typeof params.records_json!=='string'||unescape(encodeURIComponent(params.records_json)).length>64000)
      throw new Error('BATCH_BYTE_LIMIT');
    const records=JSON.parse(params.records_json);
    if(!Array.isArray(records)||records.length<1||records.length>200) throw new Error('BATCH_ROW_LIMIT');
    const keys=records.map(r=>id(r.source_key,128));
    if(new Set(keys).size!==keys.length) throw new Error('DUPLICATE_SOURCE_KEY');
    const payload_hash=sha256(params.records_json);
    if(payload_hash!==params.payload_hash) throw new Error('DIGEST_MISMATCH');
    const validate=rows=>{
      if(rows.length!==1) return null;
      const old=rows[0];
      if(old.payload_hash!==payload_hash||old.capture_id!==capture_id||old.window_key!==window_key)
        throw new Error('BATCH_CONFLICT');
      if(old.state!=='committed'||!old.receipt) throw new Error('BATCH_NOT_COMMITTED');
      return decode(old.receipt);
    };
    const existing=validate(await receipt(batch_id));
    if(existing) return existing;
    try {
      return await context.client.db.transaction(async tx=>{
        // Unique primary key is the reservation. Competing insert waits or fails;
        // recovery reads the winning receipt outside the rolled-back transaction.
        await model(tx,'batches').create({batch_id,capture_id,window_key,payload_hash,
          state:'writing',row_count:records.length,created_at:Date.now()});
        const previous=await read(tx,'records',{source_key:{$in:keys}});
        const byKey=new Map(previous.map(r=>[r.source_key,r]));
        const added=[], changed=[], unchanged=[];
        for(const row of records){
          const old=byKey.get(row.source_key);
          const value={source_key:row.source_key,payload:JSON.stringify(row),last_batch_id:batch_id};
          if(!old) added.push(value);
          else if(canonical(decode(old.payload))===canonical(row)) unchanged.push({old,value});
          else changed.push({old,value});
        }
        if(added.length) {
          const ids=await model(tx,'records').batchCreate(added);
          if(!Array.isArray(ids)||ids.length!==added.length) throw new Error('INCOMPLETE_BATCH_CREATE');
        }
        // No batchUpdate API exists; differing values require bounded updates.
        // Physical PK handling is checked in live acceptance, never guessed complete.
        for(const {old,value} of [...changed,...unchanged])
          await model(tx,'records').update({id:old.source_key,...value});
        if(params.fail_after_write===true) throw new Error('PROBE_AFTER_WRITE_FAILURE');
        const result={batch_id,capture_id,window_key,payload_hash,row_count:records.length,
          inserted:added.length,updated:changed.length,unchanged:unchanged.length,state:'committed'};
        await model(tx,'batches').update({id:batch_id,state:'committed',receipt:JSON.stringify(result)});
        return result;
      });
    } catch(error) {
      // Includes lost response after successful commit. Never blindly reinsert.
      const winner=validate(await receipt(batch_id));
      if(winner) return winner;
      throw error;
    }
  }
  if(params.op==='commit') {
    const capture_id=id(params.capture_id),window_key=id(params.window_key,128);
    if(!Array.isArray(params.batch_ids)||params.batch_ids.length>100||params.batch_ids.length<1||
        new Set(params.batch_ids).size!==params.batch_ids.length||!Number.isSafeInteger(params.source_rows)||params.source_rows<0)
      throw new Error('INVALID_MANIFEST');
    params.batch_ids.forEach(b=>id(b));
    const evidence={capture_id,window_key,source_rows:params.source_rows,batch_ids:params.batch_ids.slice().sort(),scope:'isolated_test'};
    const before=await read(context.client,'windows',{window_key:{$eq:window_key}},2);
    if(before.length){
      if(canonical(decode(before[0].evidence))!==canonical(evidence)) throw new Error('WINDOW_CONFLICT');
      return evidence;
    }
    return await context.client.db.transaction(async tx=>{
      const batches=await read(tx,'batches',{capture_id:{$eq:capture_id},window_key:{$eq:window_key}},100);
      if(batches.length!==params.batch_ids.length||batches.some(b=>b.state!=='committed'||!params.batch_ids.includes(b.batch_id)))
        throw new Error('INCOMPLETE_CAPTURE');
      const sum=batches.reduce((n,b)=>n+Number(b.row_count),0);
      const counts=await model(tx,'records').aggregate({where:{last_batch_id:{$in:params.batch_ids}},
        aggregate:[{column:'source_key',type:'COUNT',alias:'row_count'}]});
      if(sum!==params.source_rows||Number(counts.tableData[0].row_count)!==sum) throw new Error('COUNT_MISMATCH');
      await model(tx,'windows').create({window_key,capture_id,row_count:sum,evidence:JSON.stringify(evidence)});
      if(params.fail_after_write===true) throw new Error('PROBE_WINDOW_FAILURE');
      return evidence;
    });
  }
  throw new Error('UNSUPPORTED_OP');
}
