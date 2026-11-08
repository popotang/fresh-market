-- @lovrabet.sqlCode: 21522951-13a55065
-- @lovrabet.sqlName: kemaiProbeCommit
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- @lovrabet.syncedAt: 2026-11-08T10:44:08.242Z

INSERT INTO kemai_pipeline_probe_windows(window_key,capture_id,row_count,evidence)
VALUES(#{window_key},#{capture_id},#{row_count},CAST(#{evidence_json} AS JSON))
ON DUPLICATE KEY UPDATE capture_id=VALUES(capture_id),row_count=VALUES(row_count),evidence=VALUES(evidence)
