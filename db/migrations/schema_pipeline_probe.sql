-- Isolated P0-1 contract acceptance tables; no business-table foreign keys.
CREATE TABLE IF NOT EXISTS kemai_pipeline_probe_batches (
  batch_id VARCHAR(64) PRIMARY KEY,
  capture_id VARCHAR(64) NOT NULL,
  window_key VARCHAR(128) NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  state VARCHAR(16) NOT NULL,
  row_count INT UNSIGNED NOT NULL,
  receipt JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  KEY idx_probe_capture(capture_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS kemai_pipeline_probe_records (
  source_key VARCHAR(128) PRIMARY KEY,
  payload JSON NOT NULL,
  last_batch_id VARCHAR(64) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS kemai_pipeline_probe_windows (
  window_key VARCHAR(128) PRIMARY KEY,
  capture_id VARCHAR(64) NOT NULL,
  row_count INT UNSIGNED NOT NULL,
  evidence JSON NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
