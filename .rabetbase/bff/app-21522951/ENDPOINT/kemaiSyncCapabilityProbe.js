/**
 * 科迈同步运行时能力探测，只读，不创建业务记录。
 * @param {object} params 根请求，无可变业务参数。
 * @param {object} context 平台可信上下文。
 * @returns {object} API 可见性、事务内只读查询结果；不证明写入原子性。
 * 依赖数据集：sync_stream_status，dblinkId=10510。
 * 调用 BF：无。执行 SQL：无。副作用：无。
 */
export default async function kemaiSyncCapabilityProbe(params, context) {
  const client = context.client;
  const result = {schema_version: 1, transaction_api: typeof client.db?.transaction,
    batch_write_verified: false, source_requests: 0, db_writes: 0};
  if (typeof client.db?.transaction !== 'function') return result;
  try {
    await client.db.transaction(async (tx) => {
      result.tx_models = typeof tx.models;
      result.tx_sql = typeof tx.sql?.execute;
      const rows = await tx.models.byTable('sync_stream_status', {dblinkId:10510})
        .filter({where:{tenant_id:{$eq:'205149'}}, select:['id'], currentPage:1,pageSize:1});
      result.transaction_read_rows = rows.tableData.length;
    });
    result.transaction_read = 'success';
  } catch (error) {
    result.transaction_read = 'failed';
    result.error = String(error.message || error).slice(0, 300);
  }
  return result;
}
