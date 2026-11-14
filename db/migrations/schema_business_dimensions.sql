-- Business fact/dimension physical tables for the owned fresh-market copy.
-- No statement in this file targets the Kemai SaaS source.
-- retail_promote_shares mirrors the existing METADATA fields so its rows can be
-- migrated without changing their meaning. Dimension rows are derived from the
-- owned copy until read-only source master-data collectors are available.

CREATE TABLE IF NOT EXISTS retail_promote_shares (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  tenant_id VARCHAR(32) NOT NULL,
  source_row_hash CHAR(64) NOT NULL,
  occurrence_no INT UNSIGNED NOT NULL,
  source_order_id VARCHAR(32) NULL,
  sheet_no VARCHAR(64) NULL,
  marketing_type VARCHAR(32) NULL,
  promotion_id VARCHAR(64) NULL,
  promotion_code VARCHAR(64) NULL,
  activity_code VARCHAR(64) NULL,
  activity_name VARCHAR(255) NULL,
  amount DECIMAL(20,4) NULL,
  original_amount DECIMAL(20,4) NULL,
  sale_amount DECIMAL(20,4) NULL,
  original_price DECIMAL(20,4) NULL,
  quantity DECIMAL(20,4) NULL,
  unit_quantity DECIMAL(20,4) NULL,
  unit_name VARCHAR(128) NULL,
  sold_at DATETIME NULL,
  source_created_at DATETIME NULL,
  source_operation_at DATETIME NULL,
  branch_code VARCHAR(64) NULL,
  branch_name VARCHAR(255) NULL,
  item_code VARCHAR(64) NULL,
  item_subno VARCHAR(64) NULL,
  item_name VARCHAR(255) NULL,
  item_size VARCHAR(128) NULL,
  item_category_id VARCHAR(32) NULL,
  item_category_code VARCHAR(64) NULL,
  item_category_name VARCHAR(128) NULL,
  item_brand_id VARCHAR(32) NULL,
  item_brand_code VARCHAR(64) NULL,
  item_brand_name VARCHAR(128) NULL,
  member_card_no VARCHAR(64) NULL,
  member_grade_id VARCHAR(32) NULL,
  member_grade_name VARCHAR(64) NULL,
  channel VARCHAR(64) NULL,
  capture_id VARCHAR(64) NOT NULL,
  page_no INT UNSIGNED NOT NULL,
  row_no INT UNSIGNED NOT NULL,
  payload JSON NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_retail_promote_share_row (tenant_id,source_row_hash,occurrence_no),
  KEY idx_promote_share_order (tenant_id,source_order_id),
  KEY idx_promote_share_sheet (tenant_id,sheet_no),
  KEY idx_promote_share_item (tenant_id,item_code),
  KEY idx_promote_share_branch (tenant_id,branch_code),
  KEY idx_promote_share_category (tenant_id,item_category_id),
  KEY idx_promote_share_brand (tenant_id,item_brand_id),
  KEY idx_promote_share_promotion (tenant_id,promotion_code),
  KEY idx_promote_share_member (tenant_id,member_card_no),
  KEY idx_promote_share_sold (tenant_id,sold_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='零售让利明细';

CREATE TABLE IF NOT EXISTS members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  tenant_id VARCHAR(32) NOT NULL,
  source_member_id VARCHAR(32) NULL COMMENT '来源会员ID；不包含会员手机号',
  member_card_no VARCHAR(64) NULL,
  member_grade_id VARCHAR(32) NULL,
  member_grade_name VARCHAR(64) NULL,
  status_code VARCHAR(32) NULL,
  source_created_at DATETIME NULL,
  source_modified_at DATETIME NULL,
  payload JSON NULL COMMENT '未来会员主数据；不得写入 memberPhone',
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_members_source (tenant_id,source_member_id),
  UNIQUE KEY uq_members_card (tenant_id,member_card_no),
  KEY idx_members_grade (tenant_id,member_grade_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='会员';

CREATE TABLE IF NOT EXISTS staff_members (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  tenant_id VARCHAR(32) NOT NULL,
  source_employee_id VARCHAR(32) NULL,
  employee_code VARCHAR(64) NULL,
  employee_name VARCHAR(255) NULL,
  cashier_flag TINYINT(1) NOT NULL DEFAULT 0,
  approver_flag TINYINT(1) NOT NULL DEFAULT 0,
  status_code VARCHAR(32) NULL,
  payload JSON NULL,
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_staff_source (tenant_id,source_employee_id),
  UNIQUE KEY uq_staff_code (tenant_id,employee_code),
  KEY idx_staff_name (tenant_id,employee_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='人员';

CREATE TABLE IF NOT EXISTS payways (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  tenant_id VARCHAR(32) NOT NULL,
  payway_code VARCHAR(32) NOT NULL,
  payway_name VARCHAR(128) NULL,
  status_code VARCHAR(32) NULL,
  payload JSON NULL,
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_payways_code (tenant_id,payway_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='付款方式';

CREATE TABLE IF NOT EXISTS promotions (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  tenant_id VARCHAR(32) NOT NULL,
  source_promotion_id VARCHAR(64) NULL,
  promotion_code VARCHAR(64) NULL,
  activity_code VARCHAR(64) NULL,
  activity_name VARCHAR(255) NULL,
  marketing_type VARCHAR(32) NULL,
  start_at DATETIME NULL,
  end_at DATETIME NULL,
  status_code VARCHAR(32) NULL,
  payload JSON NULL,
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_promotions_source (tenant_id,source_promotion_id),
  UNIQUE KEY uq_promotions_code (tenant_id,promotion_code),
  KEY idx_promotions_activity (tenant_id,activity_code),
  KEY idx_promotions_period (tenant_id,start_at,end_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='促销活动';
