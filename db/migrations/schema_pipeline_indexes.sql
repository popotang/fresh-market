-- Retail source timeType=1 filters upload time (gmtCreate), not sale time.
-- Add once after checking information_schema.STATISTICS; online additive migration.
ALTER TABLE retail_sale_flows ADD INDEX idx_retail_flow_upload (tenant_id,source_created_at), ALGORITHM=INPLACE, LOCK=NONE;
ALTER TABLE retail_orders ADD INDEX idx_retail_order_upload (tenant_id,source_created_at), ALGORITHM=INPLACE, LOCK=NONE;
