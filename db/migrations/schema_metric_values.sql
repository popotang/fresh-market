-- Ten-minute read-through cache for computed metric values and query rows.
-- This is mutable cache, not an append-only business snapshot or audit ledger.

CREATE TABLE IF NOT EXISTS metric_values (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  cache_key CHAR(64) NOT NULL,
  generation BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '计算世代（epoch 毫秒）',
  definition_version VARCHAR(64) NOT NULL,
  group_code VARCHAR(64) NOT NULL,
  metric_code VARCHAR(64) NOT NULL,
  dimension VARCHAR(32) NOT NULL,
  dimension_key VARCHAR(128) NOT NULL,
  dimension_label VARCHAR(255) NULL,
  branch_key VARCHAR(32) NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  value_decimal DECIMAL(28,8) NULL,
  value_text VARCHAR(255) NULL,
  attributes_json JSON NULL,
  meta_json JSON NULL,
  row_index INT UNSIGNED NOT NULL DEFAULT 0,
  calculated_at BIGINT UNSIGNED NOT NULL COMMENT '计算时间（epoch 毫秒）',
  expires_at BIGINT UNSIGNED NOT NULL COMMENT '过期时间（epoch 毫秒）',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_metric_value_scope (cache_key, generation, metric_code, dimension_key),
  KEY idx_metric_values_expiry (cache_key, expires_at),
  KEY idx_metric_values_business (definition_version, group_code, metric_code, dimension, branch_key, start_date, end_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='指标值缓存（10分钟）';
