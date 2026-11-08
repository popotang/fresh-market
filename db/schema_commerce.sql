-- Sampled Kemai procurement and retail entities. Source amounts/quantities use 1/10000 units.
-- Keep source IDs as strings. All tables are tenant-scoped.

CREATE TABLE IF NOT EXISTS purchase_documents (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  document_kind VARCHAR(16) NOT NULL,
  source_document_id VARCHAR(32) NOT NULL,
  sheet_no VARCHAR(64) NULL,
  sheet_type VARCHAR(16) NULL,
  source_document_ref_id VARCHAR(32) NULL,
  source_document_ref_no VARCHAR(64) NULL,
  source_supplier_id VARCHAR(32) NULL,
  source_branch_id VARCHAR(32) NULL,
  source_receive_branch_id VARCHAR(32) NULL,
  status_code VARCHAR(32) NULL,
  source_version VARCHAR(64) NULL,
  amount DECIMAL(20,4) NULL,
  source_created_at DATETIME NULL,
  source_modified_at DATETIME NULL,
  source_approved_at DATETIME NULL,
  payload_hash CHAR(64) NOT NULL,
  payload JSON NOT NULL,
  source_list_hash CHAR(64) NULL COMMENT 'Last normalized list response hash; detail payload hash remains separate',
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_purchase_document (tenant_id,document_kind,source_document_id),
  KEY idx_purchase_sheet (tenant_id,sheet_no),
  KEY idx_purchase_ref (tenant_id,source_document_ref_id),
  KEY idx_purchase_supplier (tenant_id,source_supplier_id),
  KEY idx_purchase_branch (tenant_id,source_branch_id),
  KEY idx_purchase_modified (tenant_id,source_modified_at),
  KEY idx_purchase_created (tenant_id,document_kind,source_created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS purchase_document_lines (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  document_id BIGINT UNSIGNED NOT NULL,
  source_line_id VARCHAR(32) NOT NULL,
  source_item_id VARCHAR(32) NULL,
  source_line_ref_id VARCHAR(32) NULL,
  quantity DECIMAL(20,4) NULL,
  unit_price DECIMAL(20,4) NULL,
  amount DECIMAL(20,4) NULL,
  tax_amount DECIMAL(20,4) NULL,
  payload JSON NOT NULL,
  UNIQUE KEY uq_purchase_line (document_id,source_line_id),
  KEY idx_purchase_line_item (source_item_id),
  KEY idx_purchase_line_ref (source_line_ref_id),
  CONSTRAINT fk_purchase_line_document FOREIGN KEY (document_id) REFERENCES purchase_documents(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS purchase_document_branches (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  document_id BIGINT UNSIGNED NOT NULL,
  source_key VARCHAR(64) NOT NULL,
  source_branch_id VARCHAR(32) NULL,
  payload JSON NOT NULL,
  UNIQUE KEY uq_purchase_doc_branch (document_id,source_key),
  KEY idx_purchase_doc_branch_source (source_branch_id),
  CONSTRAINT fk_purchase_doc_branch_document FOREIGN KEY (document_id) REFERENCES purchase_documents(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS purchase_document_approvals (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  document_id BIGINT UNSIGNED NOT NULL,
  source_key VARCHAR(64) NOT NULL,
  source_approver_id VARCHAR(32) NULL,
  status_code VARCHAR(32) NULL,
  approved_at DATETIME NULL,
  payload JSON NOT NULL,
  UNIQUE KEY uq_purchase_doc_approval (document_id,source_key),
  CONSTRAINT fk_purchase_approval_document FOREIGN KEY (document_id) REFERENCES purchase_documents(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- A zero-row detail is complete only when its source arrays were present and
-- validated. The parent ID is the stable queue key for historical backfill.
CREATE TABLE IF NOT EXISTS purchase_document_detail_state (
  document_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  status VARCHAR(16) NOT NULL,
  attempts INT UNSIGNED NOT NULL DEFAULT 0,
  line_count INT UNSIGNED NULL,
  branch_count INT UNSIGNED NULL,
  approval_count INT UNSIGNED NULL,
  source_version VARCHAR(64) NULL,
  attempted_source_version VARCHAR(64) NULL,
  payload_hash CHAR(64) NULL,
  last_error TEXT NULL,
  attempted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  KEY idx_purchase_detail_state_status (status,attempted_at),
  CONSTRAINT fk_purchase_detail_state_document FOREIGN KEY (document_id)
    REFERENCES purchase_documents(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retail_orders (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  source_order_id VARCHAR(32) NOT NULL,
  receipt_no VARCHAR(64) NOT NULL,
  source_receipt_no VARCHAR(64) NULL,
  source_branch_id VARCHAR(32) NULL,
  source_member_id VARCHAR(32) NULL,
  source_type VARCHAR(32) NULL,
  channel_code VARCHAR(32) NULL,
  sale_flag VARCHAR(16) NULL,
  sale_amount DECIMAL(20,4) NULL,
  order_amount DECIMAL(20,4) NULL,
  sold_at DATETIME NULL,
  source_created_at DATETIME NULL,
  payload_hash CHAR(64) NOT NULL,
  payload JSON NOT NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_retail_order (tenant_id,source_order_id),
  KEY idx_retail_receipt (tenant_id,source_branch_id,receipt_no),
  KEY idx_retail_sold (tenant_id,sold_at),
  KEY idx_retail_member (tenant_id,source_member_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retail_order_lines (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL,
  source_line_id VARCHAR(32) NOT NULL,
  source_item_id VARCHAR(32) NULL,
  source_line_ref_id VARCHAR(32) NULL,
  quantity DECIMAL(20,4) NULL,
  sale_price DECIMAL(20,4) NULL,
  sale_amount DECIMAL(20,4) NULL,
  tax_amount DECIMAL(20,4) NULL,
  payload JSON NOT NULL,
  source_kind VARCHAR(24) NOT NULL DEFAULT 'detail_api' COMMENT 'detail_api or sales_flow',
  UNIQUE KEY uq_retail_order_line (order_id,source_line_id),
  KEY idx_retail_line_item (source_item_id),
  CONSTRAINT fk_retail_line_order FOREIGN KEY (order_id) REFERENCES retail_orders(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retail_order_detail_state (
  order_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  status VARCHAR(16) NOT NULL,
  attempts INT UNSIGNED NOT NULL DEFAULT 0,
  line_count INT UNSIGNED NULL,
  payment_count INT UNSIGNED NULL,
  last_error TEXT NULL,
  attempted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  KEY idx_retail_detail_state_status (status, attempted_at),
  CONSTRAINT fk_retail_detail_state_order FOREIGN KEY (order_id) REFERENCES retail_orders(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retail_payments (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL,
  source_payment_id VARCHAR(32) NOT NULL,
  payway_code VARCHAR(32) NULL,
  payway_name VARCHAR(128) NULL,
  amount DECIMAL(20,4) NULL,
  paid_at DATETIME NULL,
  payload JSON NOT NULL,
  UNIQUE KEY uq_retail_payment (order_id,source_payment_id),
  KEY idx_retail_payment_method (payway_code),
  CONSTRAINT fk_retail_payment_order FOREIGN KEY (order_id) REFERENCES retail_orders(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS retail_sale_flows (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  source_flow_id VARCHAR(32) NOT NULL,
  source_order_id VARCHAR(32) NULL,
  receipt_no VARCHAR(64) NULL,
  source_branch_id VARCHAR(32) NULL,
  source_item_id VARCHAR(32) NULL,
  source_supplier_id VARCHAR(32) NULL,
  quantity DECIMAL(20,4) NULL,
  sale_amount DECIMAL(20,4) NULL,
  coupon_share_amount DECIMAL(20,4) NULL,
  sold_at DATETIME NULL,
  source_created_at DATETIME NULL,
  payload_hash CHAR(64) NOT NULL,
  payload JSON NOT NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_retail_flow (tenant_id,source_flow_id),
  KEY idx_retail_flow_order (tenant_id,source_order_id),
  KEY idx_retail_flow_receipt (tenant_id,source_branch_id,receipt_no),
  KEY idx_retail_flow_item (tenant_id,source_item_id),
  KEY idx_retail_flow_sold (tenant_id,sold_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- The archived report repeats the source document ID on every line.
CREATE TABLE IF NOT EXISTS purchase_receipt_history_lines (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  source_document_id VARCHAR(32) NOT NULL,
  source_line_key CHAR(64) NOT NULL,
  sheet_no VARCHAR(64) NOT NULL,
  sheet_type VARCHAR(16) NULL,
  source_supplier_id VARCHAR(32) NULL,
  source_branch_id VARCHAR(32) NULL,
  source_item_id VARCHAR(32) NULL,
  quantity DECIMAL(20,4) NULL,
  amount DECIMAL(20,4) NULL,
  approved_at DATETIME NULL,
  business_date DATE NULL,
  payload_hash CHAR(64) NOT NULL,
  payload JSON NOT NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_receipt_history_line (tenant_id,source_line_key),
  KEY idx_receipt_history_document (tenant_id,source_document_id),
  KEY idx_receipt_history_sheet (tenant_id,sheet_no),
  KEY idx_receipt_history_item (tenant_id,source_item_id),
  KEY idx_receipt_history_date (tenant_id,approved_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Explicitly records fields intentionally deferred by the fast initial load.
CREATE TABLE IF NOT EXISTS sync_coverage_gaps (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  stream VARCHAR(64) NOT NULL,
  scope_key VARCHAR(128) NOT NULL,
  missing_parts JSON NOT NULL,
  source_row_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
  details JSON NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_sync_coverage_gap (tenant_id,stream,scope_key),
  KEY idx_sync_coverage_status (tenant_id,status,stream)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Missing from one complete reconciliation scan is evidence for review, not a
-- deletion instruction. Reappearance resolves the flag on a later scan.
CREATE TABLE IF NOT EXISTS sync_reconcile_missing (
  tenant_id VARCHAR(32) NOT NULL,
  stream VARCHAR(64) NOT NULL,
  source_day DATE NOT NULL,
  source_key VARCHAR(128) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at DATETIME NULL,
  PRIMARY KEY (tenant_id,stream,source_day,source_key),
  KEY idx_reconcile_missing_status (tenant_id,status,stream,source_day)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
