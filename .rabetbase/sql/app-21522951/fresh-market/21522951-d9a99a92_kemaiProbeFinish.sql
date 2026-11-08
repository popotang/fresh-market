-- @lovrabet.sqlCode: 21522951-d9a99a92
-- @lovrabet.sqlName: kemaiProbeFinish
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- @lovrabet.syncedAt: 2026-11-08T10:44:06.842Z

UPDATE kemai_pipeline_probe_batches SET state='committed',receipt=CAST(#{receipt_json} AS JSON)
WHERE batch_id=#{batch_id} AND state='writing' 
