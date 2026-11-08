-- Hourly retail snapshots. Times in *_utc columns are UTC; source days are Asia/Shanghai.
CREATE TABLE IF NOT EXISTS sync_live_cursors (
  tenant_id VARCHAR(32) NOT NULL,
  stream VARCHAR(64) NOT NULL,
  last_polled_day DATE NULL,
  observed_through_utc DATETIME(3) NULL,
  committed_at_utc DATETIME(3) NULL,
  last_source_rows BIGINT UNSIGNED NULL,
  last_saved_rows BIGINT UNSIGNED NULL,
  PRIMARY KEY (tenant_id,stream),
  KEY idx_live_cursor_freshness (tenant_id,observed_through_utc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='科迈当日临时采集进度；日终断点仍在 sync_checkpoints';
