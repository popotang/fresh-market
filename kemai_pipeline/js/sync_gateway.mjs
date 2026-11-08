/** Standard APIs only. Generated with portable SHA-256 and verified field allowlists. */
// Injected by scripts/build_kemai_gateway.py from kemai_pipeline/acceptance.json.
const ACCEPTED_STREAMS=(typeof __KEMAI_ACCEPTED_STREAMS__!=='undefined')
  ?__KEMAI_ACCEPTED_STREAMS__:['retail-sales','retail-orders'];
export default async function kemaiSyncGateway(p, context) {
  const TENANT='205149';
  const WRITES_ENABLED=true; // Promote only after production-table rollback and end-to-end acceptance.
  const specs={
    'retail-sales':['retail_sale_flows','source_flow_id',null,'source-id-v1'],
    'retail-orders':['retail_orders','source_order_id',null,'source-id-v1'],
    'purchase-orders':['purchase_documents','source_document_id','order','purchase-id-v1'],
    'purchase-receipts':['purchase_documents','source_document_id','receipt','purchase-id-v1'],
    'purchase-returns':['purchase_documents','source_document_id','return','purchase-id-v1'],
    'purchase-receive-history':['purchase_receipt_history_lines','source_line_key',null,'history-decimal-sequence-v2']
  };
  if(p.op==='capabilities')return {schema_version:1,writes_enabled:WRITES_ENABLED,streams:Object.keys(specs),accepted_streams:ACCEPTED_STREAMS,max_rows:200,max_bytes:64000,default_rows:50,source_requests:0};
  if(p.tenant!==TENANT||!Object.hasOwn(specs,p.stream))throw new Error('INVALID_SCOPE');
  const [table,key,kind,keyVersion]=specs[p.stream];
  const m=(client,name)=>client.models.byTable(name,{dblinkId:10510});
  const decode=v=>typeof v==='string'?JSON.parse(v):v;
  const canonical=v=>JSON.stringify(v,function(k,x){if(x&&typeof x==='object'&&!Array.isArray(x))return Object.keys(x).sort().reduce((o,key)=>{o[key]=x[key];return o;},{});return x;});
  const scope={tenant_id:{$eq:TENANT},stream:{$eq:p.stream}};
  const read=async(client,name,where,limit=200,select=undefined)=>{
    for(let i=0;i<3;i++){
      const r=await m(client,name).filter({where,currentPage:1,pageSize:limit,...(select?{select}:{})});
      if(Array.isArray(r.tableData)&&r.paging&&Number(r.paging.totalCount)===r.tableData.length)return r.tableData;
      if(Number(r.paging?.totalCount)>limit)break;
    }
    throw new Error('INCOMPLETE_MODEL_READ');
  };
  const allBatches=async(client,where)=>{
    const rows=[];let total=null;
    for(let page=1;page<=25;page++){
      const response=await m(client,'sync_pipeline_batches').filter({where,currentPage:page,pageSize:200,
        orderBy:[{id:'asc'}],select:['id','batch_id','source_keys','row_count']});
      const n=Number(response.paging?.totalCount);
      if(!Number.isSafeInteger(n)||n<0||n>5000||!Array.isArray(response.tableData)||
          response.tableData.length!==Math.min(200,Math.max(0,n-(page-1)*200))||(total!==null&&total!==n))throw new Error('INCOMPLETE_BATCH_MANIFEST_READ');
      total=n;rows.push(...response.tableData);
      if(rows.length===total){if(new Set(rows.map(r=>r.batch_id)).size!==rows.length)throw new Error('DUPLICATE_BATCH_MANIFEST');return rows;}
    }
    throw new Error('BATCH_MANIFEST_BUDGET');
  };
  const one=async(client,name,where)=>(await read(client,name,where,2))[0]||null;
  if(p.op==='coverage.import'){
    if(!Array.isArray(p.days)||!p.days.length||p.days.length>31||!/^[a-f0-9]{64}$/.test(p.evidence_hash||''))throw new Error('INVALID_LEGACY_EVIDENCE');
    const result=[];
    for(const day of p.days){
      if(!/^\d{4}-\d{2}-\d{2}$/.test(day.day)||!Number.isSafeInteger(day.rows)||day.rows<0||
          !['legacy_export_and_db','legacy_single_scan_and_db'].includes(day.verification_method))throw new Error('INVALID_LEGACY_DAY');
      const dateField=p.stream.startsWith('retail-')?'source_created_at':p.stream==='purchase-receive-history'?'approved_at':'source_created_at';
      const outcome=await context.client.db.transaction(async tx=>{
        const where={tenant_id:{$eq:TENANT},[dateField]:{$gte:day.day+' 00:00:00',$lte:day.day+' 23:59:59'}};if(kind)where.document_kind={$eq:kind};
        const count=await m(tx,table).aggregate({where,aggregate:[{column:'id',type:'COUNT',alias:'n'}]});
        if(Number(count.tableData?.[0]?.n)!==day.rows)throw new Error('LEGACY_DB_COUNT_CHANGED');
        const windowWhere={...scope,window_key:{$eq:day.day},coverage_scope:{$eq:'list'},mode:{$eq:'final'}};
        const existing=await one(tx,'sync_pipeline_windows',windowWhere);
        if(existing&&decode(existing.evidence)?.window_time_field===dateField)return {day:day.day,state:'existing'};
        const evidence={source_rows:day.rows,verification_method:day.verification_method,source_evidence_hash:p.evidence_hash,
          origin:'legacy_log_and_current_db_count',window_time_field:dateField,source_observed_at:null,detail_coverage:'not_checked'};
        const value={tenant_id:TENANT,stream:p.stream,window_key:day.day,
          coverage_scope:'list',mode:'final',capture_id:sha256(p.stream+day.day+p.evidence_hash).slice(0,32),
          row_count:day.rows,evidence:JSON.stringify(evidence),completed_at:Date.now()};
        // Keep stronger source-capture evidence; this import only migrates legacy count evidence.
        if(existing&&decode(existing.evidence)?.origin!=='legacy_log_and_current_db_count'){
          if(existing.row_count!==day.rows)throw new Error('LEGACY_NATIVE_COUNT_CONFLICT');
          await m(tx,'sync_pipeline_windows').update({id:existing.id,evidence:JSON.stringify({...decode(existing.evidence),
            window_time_field:dateField,time_basis_reverified_from:p.evidence_hash})});
          return {day:day.day,state:'native_time_basis_reverified'};
        }
        if(existing)await m(tx,'sync_pipeline_windows').update({id:existing.id,...value});else await m(tx,'sync_pipeline_windows').create(value);
        await m(tx,'sync_stream_events').create({tenant_id:TENANT,stream:p.stream,mode:'reconstructed',source_from:day.day,
          source_to:day.day,status:'success',source_rows:day.rows,saved_rows:day.rows,
          verification_method:'legacy_log_and_db',coverage_level:'list_counts',
          coverage_note:'Imported historical evidence; this is not a new source capture',
          started_at:Date.now(),finished_at:Date.now(),details:JSON.stringify(evidence)});
        return {day:day.day,state:'imported'};
      });
      result.push(outcome);
    }
    return {days:result,source_requests:0,source_freshness_advanced:false};
  }
  const captureWhere={...scope,capture_id:{$eq:p.capture_id}};
  if(p.op==='state'&&(!/^\d{4}-\d{2}-\d{2}$/.test(p.from||'')||!/^\d{4}-\d{2}-\d{2}$/.test(p.to||'')||p.from>p.to))throw new Error('INVALID_STATE_RANGE');
  if(p.op==='state')return {captures:await read(context.client,'sync_pipeline_captures',{
    ...scope,state:{$eq:'running'}},100),
    windows:await read(context.client,'sync_pipeline_windows',{
      ...scope,window_key:{$gte:p.from,$lte:p.to}},1000),
    baseline_verified:false,note:'Caller must verify explicit baseline and contiguous final list windows'};
  const hex=(v,n)=>typeof v==='string'&&v.length===n&&/^[a-f0-9]+$/.test(v);
  if(p.op==='batch.result'){
    if(!hex(p.batch_id,64))throw new Error('INVALID_BATCH_ID');
    const row=await one(context.client,'sync_pipeline_batches',{...scope,batch_id:{$eq:p.batch_id}});
    return row?decode(row.receipt):null;
  }
  if(!hex(p.capture_id,32)||!/^\d{4}-\d{2}-\d{2}$/.test(p.window_key||'')||
      !['final','provisional'].includes(p.mode)||p.source_key_version!==keyVersion)throw new Error('INVALID_CAPTURE_CONTRACT');
  const dayField=p.stream.startsWith('retail-')?'source_created_at':p.stream==='purchase-receive-history'?'approved_at':'source_created_at';
  const chinaToday=new Date(Date.now()+8*3600000).toISOString().slice(0,10);
  if(p.mode==='final'&&p.window_key>=chinaToday)throw new Error('FINAL_DAY_NOT_CLOSED');
  if(p.op!=='validate'&&!ACCEPTED_STREAMS.includes(p.stream))throw new Error('STREAM_ACCEPTANCE_REQUIRED');
  if(!WRITES_ENABLED&&p.op!=='validate')throw new Error('WRITE_ACCEPTANCE_REQUIRED');
  const existingReceipt=async()=>one(context.client,'sync_pipeline_batches',{...scope,batch_id:{$eq:p.batch_id}});
  const batchInput=()=>{
    if(!Number.isSafeInteger(p.sequence)||p.sequence<0||!hex(p.batch_id,64)||
        p.batch_id!==sha256(JSON.stringify([TENANT,p.stream,p.capture_id,p.sequence])))throw new Error('INVALID_BATCH_ID');
    if(typeof p.records_json!=='string'||unescape(encodeURIComponent(p.records_json)).length>64000)throw new Error('BATCH_BYTE_LIMIT');
    if(sha256(p.records_json)!==p.payload_hash)throw new Error('DIGEST_MISMATCH');
    const rows=JSON.parse(p.records_json);
    if(!Array.isArray(rows)||!rows.length||rows.length>200)throw new Error('BATCH_ROW_LIMIT');
    const keys=new Set();
    for(const row of rows){
      if(typeof row.source_key!=='string'||!row.source_key||row.source_key.length>128||keys.has(row.source_key))throw new Error('INVALID_SOURCE_KEY');
      keys.add(row.source_key);
      if(row.data==null){if(!/^[a-f0-9]{64}$/.test(row.expected_hash||''))throw new Error('INVALID_OBSERVATION_HASH');continue;}
      const data=row.data;
      if(!data||data[key]!==row.source_key||(kind&&data.document_kind!==kind))throw new Error('SOURCE_ID_MISMATCH');
      if(Object.keys(data).some(k=>!MODEL_FIELDS[table].includes(k)||['id','tenant_id','captured_at'].includes(k)))throw new Error('UNSUPPORTED_FIELD');
      if(typeof data.payload!=='string'||sha256(data.payload)!==data.payload_hash)throw new Error('RAW_PAYLOAD_MISMATCH');
      if(p.op!=='validate'&&String(data[dayField]||'').slice(0,10)!==p.window_key)throw new Error('ROW_OUTSIDE_WINDOW');
      const raw=JSON.parse(data.payload);
      if(key!=='source_line_key'&&String(raw.id)!==row.source_key)throw new Error('RAW_SOURCE_ID_MISMATCH');
    }
    return rows;
  };
  const writeRows=async(tx,rows)=>{
    const keys=rows.map(r=>r.source_key),where={tenant_id:{$eq:TENANT},[key]:{$in:keys}};
    if(kind)where.document_kind={$eq:kind};
    const purchase=!!kind;
    const current=await read(tx,table,where,200,['id',key,'payload_hash',dayField,...(purchase?['source_list_hash']:[]),...(p.stream==='retail-orders'?['payload']:[])]);
    const rawCurrent=await read(tx,'source_capture',{...scope,source_key:{$in:keys}},200,['id','source_key','payload_hash']);
    const byKey=new Map(current.map(r=>[String(r[key]),r]));
    const rawByKey=new Map(rawCurrent.map(r=>[String(r.source_key),r]));
    const adds=[],rawAdds=[],updates=[],rawUpdates=[];
    let unchanged=0,metadataOnly=0;
    for(const row of rows){
      const old=byKey.get(row.source_key),oldRaw=rawByKey.get(row.source_key);
      if(row.data==null){
        if(!old||!oldRaw||(purchase?old.source_list_hash:old.payload_hash)!==row.expected_hash||oldRaw.payload_hash!==row.expected_hash)throw new Error('OBSERVED_ROW_CHANGED');
        const sourceDay=typeof old[dayField]==='number'?new Date(old[dayField]+8*3600000).toISOString().slice(0,10):String(old[dayField]||'').slice(0,10);
        if(sourceDay!==p.window_key)throw new Error('ROW_OUTSIDE_WINDOW');
        unchanged++;continue;
      }
      const data={...row.data,tenant_id:TENANT,captured_at:Date.now()};
      const raw=JSON.parse(data.payload);
      // Source gmtOperation is generated per query page (observed in the real 5,228-row capture).
      // Full source snapshots remain immutable on disk. Only this top-level query timestamp
      // may differ; all business fields and the raw/business DB hashes must still agree.
      if(p.stream==='retail-orders'&&old&&oldRaw&&old.payload_hash===oldRaw.payload_hash&&old.payload_hash!==data.payload_hash){
        const previous={...decode(old.payload)},incoming={...raw};delete previous.gmtOperation;delete incoming.gmtOperation;
        if(canonical(previous)===canonical(incoming)){unchanged++;metadataOnly++;continue;}
      }
      const capture={tenant_id:TENANT,stream:p.stream,source_key:row.source_key,payload:data.payload,
        payload_hash:data.payload_hash,source_version:raw.version==null?null:String(raw.version),captured_at:Date.now()};
      if(!old)adds.push(purchase?{...data,source_list_hash:data.payload_hash}:data);
      else if(purchase&&old.source_list_hash!==data.payload_hash){
        // Legacy parent payload may contain full detail. List refresh updates
        // mapped header fields and its own hash without destroying that detail.
        const {payload,payload_hash,...header}=data;
        updates.push({id:old.id,...header,source_list_hash:data.payload_hash});
      }
      else if(!purchase&&old.payload_hash!==data.payload_hash)updates.push({id:old.id,...data});
      else unchanged++;
      if(!oldRaw)rawAdds.push(capture);
      else if(oldRaw.payload_hash!==capture.payload_hash)rawUpdates.push({id:oldRaw.id,...capture});
    }
    const bulk=async(name,values)=>{if(values.length){const ids=await m(tx,name).batchCreate(values);
      if(!Array.isArray(ids)||ids.length!==values.length)throw new Error('INCOMPLETE_BATCH_CREATE');}};
    await bulk(table,adds);await bulk('source_capture',rawAdds);
    for(const row of updates)await m(tx,table).update(row);
    for(const row of rawUpdates)await m(tx,'source_capture').update(row);
    if(purchase){
      // The model API can acknowledge an update while silently dropping a newly
      // added, not-yet-analyzed column. Confirm persistence before the receipt
      // transaction commits; otherwise a false complete window would result.
      const saved=await read(tx,table,where,200,[key,'source_list_hash']);
      const hashes=new Map(saved.map(r=>[String(r[key]),r.source_list_hash]));
      for(const row of rows){
        const wanted=row.data==null?row.expected_hash:row.data.payload_hash;
        if(hashes.get(row.source_key)!==wanted)throw new Error('PERSISTED_LIST_HASH_MISMATCH');
      }
    }
    return {inserted:adds.length,updated:updates.length,unchanged,metadata_only_unchanged:metadataOnly};
  };
  if(p.op==='validate'){
    const rows=batchInput();let result;
    // Restrict dry validation to already-existing source identities: no synthetic business entities.
    const where={tenant_id:{$eq:TENANT},[key]:{$in:rows.map(r=>r.source_key)}};
    if(kind)where.document_kind={$eq:kind};
    const found=await read(context.client,table,where);
    if(found.length!==rows.length)throw new Error('VALIDATION_REQUIRES_EXISTING_SOURCE_ROWS');
    try{await context.client.db.transaction(async tx=>{result=await writeRows(tx,rows);throw new Error('VALIDATION_ROLLBACK');});}
    catch(error){if(!String(error.message||error).includes('VALIDATION_ROLLBACK'))throw error;}
    return {validated:true,rolled_back:true,...result,source_requests:0};
  }
  let controller=await one(context.client,'sync_pipeline_streams',scope);
  if(!controller&&p.op==='capture.begin'){
    try{await m(context.client,'sync_pipeline_streams').create({tenant_id:TENANT,stream:p.stream,updated_at:Date.now()});}
    catch(error){if(!await one(context.client,'sync_pipeline_streams',scope))throw error;}
    controller=await one(context.client,'sync_pipeline_streams',scope);
  }
  if(!controller)throw new Error('CAPTURE_NOT_STARTED');
  const serial=async fn=>context.client.db.transaction(async tx=>{
    // An UPDATE acquires a database row lock using the standard model API.
    // It precedes all transactional reads, so no old repeatable-read snapshot is reused.
    await m(tx,'sync_pipeline_streams').update({id:controller.id,stream:p.stream});
    const control=await one(tx,'sync_pipeline_streams',scope);
    return fn(tx,control);
  });
  if(p.op==='capture.begin')return serial(async(tx,control)=>{
    const old=await one(tx,'sync_pipeline_captures',captureWhere);
    if(old){if(old.window_key!==p.window_key||old.mode!==p.mode||old.source_key_version!==keyVersion)throw new Error('CAPTURE_CONFLICT');
      if(control.active_capture_id!==p.capture_id&&old.state!=='committed')throw new Error('CAPTURE_SUPERSEDED');return {capture_id:p.capture_id,state:old.state};}
    if(control.active_capture_id){
      const active=await one(tx,'sync_pipeline_captures',{...scope,capture_id:{$eq:control.active_capture_id}});
      if(active&&active.state==='running')throw new Error('STREAM_BUSY_RESUME_ACTIVE_CAPTURE');
    }
    await m(tx,'sync_pipeline_captures').create({tenant_id:TENANT,stream:p.stream,capture_id:p.capture_id,
      window_key:p.window_key,mode:p.mode,source_key_version:keyVersion,state:'running',started_at:Date.now()});
    await m(tx,'sync_pipeline_streams').update({id:controller.id,active_capture_id:p.capture_id,updated_at:Date.now()});
    await m(tx,'sync_stream_events').create({tenant_id:TENANT,stream:p.stream,mode:p.mode,
      source_from:p.window_key,source_to:p.window_key,status:'running',started_at:Date.now(),details:JSON.stringify({capture_id:p.capture_id})});
    return {capture_id:p.capture_id,state:'running'};
  });
  if(p.op==='batch.upsert'){
    const rows=batchInput();
    const validateReceipt=row=>{
      if(!row)return null;
      if(row.payload_hash!==p.payload_hash||row.capture_id!==p.capture_id||row.window_key!==p.window_key)throw new Error('BATCH_CONFLICT');
      return decode(row.receipt);
    };
    const old=validateReceipt(await existingReceipt());if(old)return old;
    return serial(async(tx,control)=>{
      const prior=validateReceipt(await one(tx,'sync_pipeline_batches',{...scope,batch_id:{$eq:p.batch_id}}));if(prior)return prior;
      const capture=await one(tx,'sync_pipeline_captures',captureWhere);
      if(control.active_capture_id!==p.capture_id||!capture||capture.state!=='running'||capture.window_key!==p.window_key||capture.mode!==p.mode)throw new Error('CAPTURE_NOT_ACTIVE');
      const counts=await writeRows(tx,rows);
      const receipt={batch_id:p.batch_id,capture_id:p.capture_id,window_key:p.window_key,payload_hash:p.payload_hash,
        row_count:rows.length,state:'committed',...counts};
      await m(tx,'sync_pipeline_batches').create({tenant_id:TENANT,stream:p.stream,capture_id:p.capture_id,
        batch_id:p.batch_id,window_key:p.window_key,payload_hash:p.payload_hash,source_keys:JSON.stringify(rows.map(r=>r.source_key)),
        row_count:rows.length,receipt:JSON.stringify(receipt),committed_at:Date.now()});
      return receipt;
    });
  }
  if(p.op==='capture.note')return serial(async(tx,control)=>{
    if(control.active_capture_id!==p.capture_id)throw new Error('CAPTURE_SUPERSEDED');
    if(!['cooling','failed','awaiting_export'].includes(p.status))throw new Error('INVALID_ATTEMPT_STATUS');
    const eventId=await m(tx,'sync_stream_events').create({tenant_id:TENANT,stream:p.stream,mode:p.mode,
      source_from:p.window_key,source_to:p.window_key,status:p.status,started_at:Date.now(),finished_at:Date.now(),
      error_message:String(p.reason||p.status).slice(0,128),details:JSON.stringify({capture_id:p.capture_id})});
    const status=await one(tx,'sync_stream_status',scope);
    if(status)await m(tx,'sync_stream_status').update({id:status.id,last_attempt_status:p.status,last_attempt_at:Date.now(),
      last_error:String(p.reason||p.status).slice(0,128),last_event_id:eventId,updated_at:Date.now()});
    return {capture_id:p.capture_id,state:p.status};
  });
  if(p.op==='capture.fail')return serial(async(tx,control)=>{
    const capture=await one(tx,'sync_pipeline_captures',captureWhere);
    if(!capture||capture.state==='committed')throw new Error('CAPTURE_CANNOT_FAIL');
    if(control.active_capture_id!==p.capture_id)throw new Error('CAPTURE_SUPERSEDED');
    await m(tx,'sync_pipeline_captures').update({id:capture.id,state:'interrupted'});
    const eventId=await m(tx,'sync_stream_events').create({tenant_id:TENANT,stream:p.stream,mode:p.mode,source_from:p.window_key,
      source_to:p.window_key,status:'failed',started_at:Date.now(),finished_at:Date.now(),
      error_message:String(p.reason||'capture interrupted').slice(0,128),details:JSON.stringify({capture_id:p.capture_id})});
    const status=await one(tx,'sync_stream_status',scope);
    if(status)await m(tx,'sync_stream_status').update({id:status.id,last_attempt_status:'failed',last_attempt_at:Date.now(),
      last_error:String(p.reason||'capture interrupted').slice(0,128),last_event_id:eventId,updated_at:Date.now()});
    return {capture_id:p.capture_id,state:'interrupted'};
  });
  if(p.op==='window.commit')return serial(async(tx,control)=>{
    const capture=await one(tx,'sync_pipeline_captures',captureWhere);
    if(!capture||capture.window_key!==p.window_key||capture.mode!==p.mode)throw new Error('CAPTURE_CONFLICT');
    if(capture.state==='committed'){
      const saved=decode(capture.evidence);
      const wanted=sha256(canonical({batch_ids:(p.batch_ids||[]).slice().sort(),evidence:p.evidence}));
      if(saved.manifest_digest&&saved.manifest_digest!==wanted)throw new Error('WINDOW_MANIFEST_CONFLICT');
      if(!saved.manifest_digest&&canonical(saved)!==canonical(p.evidence))throw new Error('WINDOW_MANIFEST_CONFLICT');
      return {capture_id:p.capture_id,state:'committed',evidence:saved};
    }
    if(control.active_capture_id!==p.capture_id||capture.state!=='running')throw new Error('CAPTURE_NOT_ACTIVE');
    const e={...p.evidence},ids=p.batch_ids;
    if(!e||!Number.isSafeInteger(e.source_rows)||e.source_rows<0||!Array.isArray(ids)||ids.length>5000||new Set(ids).size!==ids.length)throw new Error('INVALID_MANIFEST');
    if(p.mode==='final'&&!['export_total_and_capture','reused_export_total_and_capture','single_scan_count','empty_page_and_db'].includes(e.verification_method))throw new Error('INVALID_FINAL_EVIDENCE');
    if(e.verification_method==='empty_page_and_db'&&e.source_rows!==0)throw new Error('INVALID_EMPTY_EVIDENCE');
    if(p.mode==='final'&&p.stream.startsWith('retail-')&&e.source_rows>0&&!['export_total_and_capture','reused_export_total_and_capture'].includes(e.verification_method))throw new Error('RETAIL_EXPORT_TOTAL_REQUIRED');
    if(['export_total_and_capture','reused_export_total_and_capture'].includes(e.verification_method)&&(e.export_total!==e.source_rows||!e.export_task_id))throw new Error('INVALID_EXPORT_EVIDENCE');
    if(e.verification_method==='reused_export_total_and_capture'&&(e.export_reuse_reason!=='server_24h_dedup'||!e.source_task_no))throw new Error('INVALID_EXPORT_REUSE_EVIDENCE');
    const batches=await allBatches(tx,captureWhere);
    if(batches.length!==ids.length||batches.some(b=>!ids.includes(b.batch_id)))throw new Error('INCOMPLETE_CAPTURE');
    const keys=batches.flatMap(b=>decode(b.source_keys));
    if(keys.length>100000)throw new Error('WINDOW_ROW_BUDGET');
    if(keys.length!==e.source_rows||new Set(keys).size!==keys.length)throw new Error('COUNT_MISMATCH');
    for(let i=0;i<keys.length;i+=200){
      const where={tenant_id:{$eq:TENANT},[key]:{$in:keys.slice(i,i+200)}};if(kind)where.document_kind={$eq:kind};
      const result=await m(tx,table).aggregate({where,aggregate:[{column:'id',type:'COUNT',alias:'n'}]});
      if(Number(result.tableData?.[0]?.n)!==keys.slice(i,i+200).length)throw new Error('BUSINESS_ROWS_MISSING');
    }
    if(p.mode==='final'){
      const where={tenant_id:{$eq:TENANT},[dayField]:{$gte:p.window_key+' 00:00:00',$lte:p.window_key+' 23:59:59'}};
      if(kind)where.document_kind={$eq:kind};
      const count=await m(tx,table).aggregate({where,aggregate:[{column:'id',type:'COUNT',alias:'n'}]});
      if(Number(count.tableData?.[0]?.n)!==e.source_rows)throw new Error('DATABASE_WINDOW_COUNT_MISMATCH');
    }
    e.manifest_digest=sha256(canonical({batch_ids:ids.slice().sort(),evidence:e}));
    const coverage_scope='list',windowWhere={...scope,window_key:{$eq:p.window_key},coverage_scope:{$eq:coverage_scope},mode:{$eq:p.mode}};
    const old=await one(tx,'sync_pipeline_windows',windowWhere);
    const value={tenant_id:TENANT,stream:p.stream,window_key:p.window_key,coverage_scope,mode:p.mode,
      capture_id:p.capture_id,row_count:keys.length,evidence:JSON.stringify(e),completed_at:Date.now()};
    if(old)await m(tx,'sync_pipeline_windows').update({id:old.id,...value});else await m(tx,'sync_pipeline_windows').create(value);
    await m(tx,'sync_pipeline_captures').update({id:capture.id,state:'committed',evidence:JSON.stringify(e),completed_at:Date.now()});
    const eventId=await m(tx,'sync_stream_events').create({tenant_id:TENANT,stream:p.stream,mode:p.mode,
      source_from:p.window_key,source_to:p.window_key,status:'success',source_rows:keys.length,saved_rows:keys.length,
      verification_method:e.verification_method,coverage_level:p.mode==='final'?'list_only':'provisional',
      coverage_note:'List coverage only; detail coverage tracked separately',started_at:capture.started_at,finished_at:Date.now(),
      details:JSON.stringify({capture_id:p.capture_id,coverage_scope:'list',source_observed_at:e.source_observed_at||null,
        snapshot_cutoff:e.snapshot_cutoff||null,window_time_field:dayField,manifest_digest:e.manifest_digest})});
    const status=await one(tx,'sync_stream_status',scope);
    const statusValue={tenant_id:TENANT,stream:p.stream,stream_name:status?.stream_name||p.stream,
      last_attempt_status:'success',last_attempt_at:Date.now(),last_success_at:Date.now(),last_source_rows:keys.length,
      last_saved_rows:keys.length,verification_method:e.verification_method,coverage_level:p.mode==='final'?'list_only':'provisional',
      coverage_note:'List coverage only; detail coverage tracked separately',last_event_id:eventId,last_error:null,updated_at:Date.now()};
    if(status)await m(tx,'sync_stream_status').update({id:status.id,...statusValue});else await m(tx,'sync_stream_status').create(statusValue);
    return {capture_id:p.capture_id,state:'committed',evidence:e};
  });
  throw new Error('UNSUPPORTED_OP');
}
