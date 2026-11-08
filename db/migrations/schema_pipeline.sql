-- Durable control-plane state. Business tables retain existing unique keys.
-- New checkpoint truth is per window/scope; legacy cursors are NOT advanced.
CREATE TABLE IF NOT EXISTS sync_pipeline_streams (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 tenant_id VARCHAR(32) NOT NULL,
 stream VARCHAR(64) NOT NULL,
 active_capture_id CHAR(32) NULL,
 updated_at DATETIME(3) NOT NULL,
 UNIQUE KEY uq_pipeline_stream (tenant_id,stream)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS sync_pipeline_captures (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 tenant_id VARCHAR(32) NOT NULL,
 stream VARCHAR(64) NOT NULL,
 capture_id CHAR(32) NOT NULL,
 window_key VARCHAR(128) NOT NULL,
 mode VARCHAR(20) NOT NULL,
 source_key_version VARCHAR(64) NOT NULL,
 state VARCHAR(20) NOT NULL,
 evidence JSON NULL,
 started_at DATETIME(3) NOT NULL,
 completed_at DATETIME(3) NULL,
 UNIQUE KEY uq_pipeline_capture (tenant_id,capture_id),
 KEY idx_pipeline_capture_stream (tenant_id,stream,state,started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS sync_pipeline_batches (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 tenant_id VARCHAR(32) NOT NULL,
 stream VARCHAR(64) NOT NULL,
 capture_id CHAR(32) NOT NULL,
 batch_id CHAR(64) NOT NULL,
 window_key VARCHAR(128) NOT NULL,
 payload_hash CHAR(64) NOT NULL,
 source_keys JSON NOT NULL,
 row_count INT UNSIGNED NOT NULL,
 receipt JSON NOT NULL,
 committed_at DATETIME(3) NOT NULL,
 UNIQUE KEY uq_pipeline_batch (tenant_id,batch_id),
 KEY idx_pipeline_batches_capture (tenant_id,capture_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS sync_pipeline_windows (
 id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
 tenant_id VARCHAR(32) NOT NULL,
 stream VARCHAR(64) NOT NULL,
 window_key VARCHAR(128) NOT NULL,
 coverage_scope VARCHAR(32) NOT NULL,
 mode VARCHAR(20) NOT NULL,
 capture_id CHAR(32) NOT NULL,
 row_count BIGINT UNSIGNED NOT NULL,
 evidence JSON NOT NULL,
 completed_at DATETIME(3) NOT NULL,
 UNIQUE KEY uq_pipeline_window (tenant_id,stream,window_key,coverage_scope,mode),
 KEY idx_pipeline_window_day (tenant_id,stream,mode,window_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
