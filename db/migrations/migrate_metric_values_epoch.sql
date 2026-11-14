-- Move cache timestamps to epoch milliseconds to eliminate timezone ambiguity.
-- Only the newly introduced cache rows are cleared; business facts are untouched.

DELETE FROM metric_values;

ALTER TABLE metric_values
  MODIFY calculated_at BIGINT UNSIGNED NOT NULL COMMENT '计算时间（epoch 毫秒）',
  MODIFY expires_at BIGINT UNSIGNED NOT NULL COMMENT '过期时间（epoch 毫秒）';
