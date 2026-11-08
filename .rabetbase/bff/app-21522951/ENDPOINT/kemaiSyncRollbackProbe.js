/**
 * 受控验证当前 dblink 的事务回滚能力。
 * @param {object} params 根请求。
 * @param {string} params.action 必须为 rollback_probe。
 * @param {object} context 平台可信上下文。
 * @returns {object} 本次诊断标记、回滚结果、残留数量与清理结果。
 * @throws {Error} 参数不匹配、真实数据库错误或清理失败。
 * 依赖数据集：sync_stream_events，dblinkId=10510。
 * 调用 BF/SQL：无。
 * 副作用：事务内创建两条固定租户的诊断事件并主动回滚；若回滚失效，仅清理本次精确标记的残留行。
 */
export default async function kemaiSyncRollbackProbe(params, context) {
  if (params.action !== 'rollback_probe') throw new Error('action must be rollback_probe');
  const marker = 'probe_' + Date.now() + '_' + Math.random().toString(36).slice(2,10);
  const model = (client) => client.models.byTable('sync_stream_events', {dblinkId:10510});
  let inserted = 0;
  try {
    await context.client.db.transaction(async (tx) => {
      const rows = [0, 1].map(() => ({tenant_id:'205149',stream:marker,mode:'capability_probe',
        status:'success',started_at:Date.now(),coverage_note:'Temporary batchCreate rollback diagnostic; not source coverage'}));
      const ids = await model(tx).batchCreate(rows);
      if (!Array.isArray(ids) || ids.length !== rows.length || new Set(ids).size !== rows.length)
        throw new Error('Unexpected batchCreate ID result');
      inserted = ids.length;
      throw new Error('KEMAI_PROBE_FORCE_ROLLBACK');
    });
  } catch (error) {
    if (!String(error.message || error).includes('KEMAI_PROBE_FORCE_ROLLBACK')) throw error;
  }
  const after = await model(context.client).filter({where:{tenant_id:{$eq:'205149'},stream:{$eq:marker}},
    select:['id','stream','tenant_id'],currentPage:1,pageSize:10});
  const residual = after.tableData;
  if (residual.length > 2 || residual.some(r => r.stream !== marker || String(r.tenant_id) !== '205149'))
    throw new Error('Unexpected probe readback; no cleanup attempted');
  for (const row of residual) await model(context.client).delete({id:row.id});
  return {schema_version:1, marker, inserted, residual_after_rollback:residual.length,
    rollback_verified:inserted===2 && residual.length===0, cleaned_rows:residual.length,
    source_requests:0, write_api:'tx.models.batchCreate', scope:'same-dblink batchCreate rollback; does not prove batch idempotency or window commit'};
}
