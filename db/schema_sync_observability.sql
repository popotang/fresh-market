-- Per-stream observability for the Kemai ingestion pipeline.
-- A successful event means that the source count was reconciled and the
-- corresponding checkpoint was committed. Historical reconstructions are
-- explicitly marked as such and are not a new SaaS fetch.

CREATE TABLE IF NOT EXISTS sync_stream_events (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  stream VARCHAR(64) NOT NULL,
  mode VARCHAR(32) NOT NULL,
  source_from DATE NULL,
  source_to DATE NULL,
  run_id BIGINT UNSIGNED NULL,
  status VARCHAR(20) NOT NULL,
  source_rows BIGINT UNSIGNED NULL,
  saved_rows BIGINT UNSIGNED NULL,
  verification_method VARCHAR(64) NULL,
  coverage_level VARCHAR(32) NULL,
  coverage_note VARCHAR(512) NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  error_message TEXT NULL,
  details JSON NULL,
  KEY idx_stream_events_latest (tenant_id,stream,started_at),
  KEY idx_stream_events_window (tenant_id,stream,source_to,status),
  KEY idx_stream_events_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='科迈同步日志';

CREATE TABLE IF NOT EXISTS sync_stream_status (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  stream VARCHAR(64) NOT NULL,
  stream_name VARCHAR(128) NOT NULL,
  last_attempt_status VARCHAR(20) NOT NULL DEFAULT 'unknown',
  last_attempt_at DATETIME NULL,
  last_success_at DATETIME NULL,
  covered_through DATE NULL,
  last_source_rows BIGINT UNSIGNED NULL,
  last_saved_rows BIGINT UNSIGNED NULL,
  verification_method VARCHAR(64) NULL,
  coverage_level VARCHAR(32) NOT NULL DEFAULT 'unknown',
  coverage_note VARCHAR(512) NULL,
  last_error TEXT NULL,
  last_event_id BIGINT UNSIGNED NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_stream_status (tenant_id,stream),
  KEY idx_stream_status_freshness (tenant_id,covered_through,last_success_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='科迈数据更新状态';
