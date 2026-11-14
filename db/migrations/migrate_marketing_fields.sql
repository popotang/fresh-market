-- Optional materialization of marketing/member fields.
-- Status: DRAFT ONLY. Do not execute automatically. Apply through the approved
-- Lovrabet dataset/schema change process, then enable normalize.py mappings and
-- a bounded payload backfill. The read-only retailMarketing* views need no DDL.

ALTER TABLE retail_sale_flows
  ADD COLUMN promote_code VARCHAR(64) NULL COMMENT '促销活动单号（源 promoteCodes）',
  ADD COLUMN member_card_no VARCHAR(32) NULL COMMENT '会员卡号（源 memberCardNo）',
  ADD COLUMN member_grade_name VARCHAR(64) NULL COMMENT '会员等级名（源 memberGradeName）',
  ADD COLUMN discount_amount DECIMAL(20,4) NULL COMMENT '折扣金额（源 discountAmt/10000）';

ALTER TABLE retail_orders
  ADD COLUMN member_card_no VARCHAR(32) NULL COMMENT '会员卡号（源 memberCardNo）',
  ADD COLUMN member_grade_id VARCHAR(32) NULL COMMENT '会员等级ID（源 memberGradeId）',
  ADD COLUMN member_grade_name VARCHAR(64) NULL COMMENT '会员等级名（源 memberGradeName）',
  ADD COLUMN discount_amount DECIMAL(20,4) NULL COMMENT '折扣金额（源 discountAmt/10000）';

-- Optional only if promotion-level analysis needs the index:
-- ALTER TABLE retail_sale_flows ADD KEY idx_retail_flow_promote (tenant_id, promote_code);
