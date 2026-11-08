-- @lovrabet.sqlCode: 21522951-33b20f3d
-- @lovrabet.sqlName: kemaiProbeRows
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- @lovrabet.syncedAt: 2026-11-08T10:44:04.774Z

INSERT INTO kemai_pipeline_probe_records(source_key,payload,last_batch_id)
SELECT j.source_key,j.payload,#{batch_id}
FROM JSON_TABLE(#{records_json}, '$[*]' COLUMNS (
 source_key VARCHAR(128) PATH '$.source_key' ERROR ON ERROR,
 payload JSON PATH '$' ERROR ON ERROR
)) AS j
ON DUPLICATE KEY UPDATE payload=VALUES(payload),last_batch_id=VALUES(last_batch_id)
