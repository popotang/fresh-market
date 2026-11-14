-- Versioned business metric definitions for the owned fresh-market application.
-- This table stores semantic metadata only; business facts stay in existing tables.

CREATE TABLE IF NOT EXISTS metric_definitions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  definition_version VARCHAR(64) NOT NULL,
  metric_code VARCHAR(64) NOT NULL,
  name VARCHAR(128) NOT NULL,
  group_code VARCHAR(64) NOT NULL,
  unit VARCHAR(32) NOT NULL,
  formula VARCHAR(512) NOT NULL,
  source_name VARCHAR(128) NOT NULL,
  nullable TINYINT(1) NOT NULL DEFAULT 1,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  sort_order INT UNSIGNED NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_metric_definition (definition_version, metric_code),
  KEY idx_metric_group (definition_version, group_code, enabled, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='指标定义';
