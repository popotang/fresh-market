-- Append-only cache generations. Recompute inserts a new generation instead of
-- deleting old rows, so the runtime does not need DELETE permission.

ALTER TABLE metric_values
  ADD COLUMN generation BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '计算世代（epoch 毫秒）' AFTER cache_key;

ALTER TABLE metric_values
  DROP INDEX uq_metric_value_scope,
  ADD UNIQUE KEY uq_metric_value_scope (cache_key, generation, metric_code, dimension_key);
