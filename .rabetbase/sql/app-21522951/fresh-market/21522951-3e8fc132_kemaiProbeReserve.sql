-- @lovrabet.sqlCode: 21522951-3e8fc132
-- @lovrabet.sqlName: kemaiProbeReserve
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- @lovrabet.syncedAt: 2026-11-08T10:44:03.048Z

INSERT INTO kemai_pipeline_probe_batches
(batch_id,capture_id,window_key,payload_hash,state,row_count)
VALUES (#{batch_id},#{capture_id},#{window_key},SHA2(#{records_json},256),#{state},#{row_count})
ON DUPLICATE KEY UPDATE batch_id=VALUES(batch_id)
