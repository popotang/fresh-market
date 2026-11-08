-- Kemai Yunfan full-sync foundation. Existing product_items remains untouched.
-- Source identifiers and codes are strings to preserve leading zeros and 19-digit IDs.

CREATE TABLE IF NOT EXISTS sync_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  mode VARCHAR(32) NOT NULL,
  status VARCHAR(20) NOT NULL,
  parameters JSON NULL,
  stats JSON NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  last_error TEXT NULL,
  KEY idx_sync_runs_tenant_started (tenant_id, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sync_checkpoints (
  tenant_id VARCHAR(32) NOT NULL,
  stream VARCHAR(64) NOT NULL,
  cursor_json JSON NULL,
  last_run_id BIGINT UNSIGNED NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (tenant_id, stream)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sync_objects (
  tenant_id VARCHAR(32) NOT NULL,
  stream VARCHAR(64) NOT NULL,
  source_key VARCHAR(128) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  attempts INT UNSIGNED NOT NULL DEFAULT 0,
  payload_hash CHAR(64) NULL,
  last_success_at DATETIME NULL,
  last_error TEXT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (tenant_id, stream, source_key),
  KEY idx_sync_objects_status (tenant_id, stream, status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS source_capture (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  stream VARCHAR(64) NOT NULL,
  source_key VARCHAR(128) NOT NULL,
  source_version VARCHAR(64) NULL,
  source_modified_at DATETIME NULL,
  payload_hash CHAR(64) NOT NULL,
  payload JSON NOT NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_source_capture (tenant_id, stream, source_key),
  KEY idx_source_capture_stream (tenant_id, stream, captured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS branches (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  source_branch_id VARCHAR(32) NOT NULL,
  branch_code VARCHAR(64) NULL,
  branch_name VARCHAR(255) NULL,
  branch_type VARCHAR(32) NULL,
  parent_source_branch_id VARCHAR(32) NULL,
  payload JSON NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_branches_source (tenant_id, source_branch_id),
  KEY idx_branches_code (tenant_id, branch_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS departments (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  source_department_id VARCHAR(32) NOT NULL,
  department_code VARCHAR(64) NULL,
  department_name VARCHAR(255) NULL,
  payload JSON NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_departments_source (tenant_id, source_department_id),
  KEY idx_departments_code (tenant_id, department_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS categories (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  source_category_id VARCHAR(32) NOT NULL,
  category_code VARCHAR(64) NULL,
  category_name VARCHAR(255) NULL,
  parent_source_category_id VARCHAR(32) NULL,
  source_department_id VARCHAR(32) NULL,
  category_level SMALLINT NULL,
  payload JSON NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_categories_source (tenant_id, source_category_id),
  KEY idx_categories_parent (tenant_id, parent_source_category_id),
  KEY idx_categories_code (tenant_id, category_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS brands (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  source_brand_id VARCHAR(32) NOT NULL,
  brand_code VARCHAR(64) NULL,
  brand_name VARCHAR(255) NULL,
  parent_source_brand_id VARCHAR(32) NULL,
  payload JSON NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_brands_source (tenant_id, source_brand_id),
  KEY idx_brands_code (tenant_id, brand_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS units (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  source_unit_id VARCHAR(32) NOT NULL,
  unit_name VARCHAR(128) NULL,
  payload JSON NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_units_source (tenant_id, source_unit_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS suppliers (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  source_supplier_id VARCHAR(32) NOT NULL,
  supplier_code VARCHAR(64) NULL,
  supplier_name VARCHAR(255) NULL,
  payload JSON NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_suppliers_source (tenant_id, source_supplier_id),
  KEY idx_suppliers_code (tenant_id, supplier_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS products (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  tenant_id VARCHAR(32) NOT NULL,
  source_item_id VARCHAR(32) NOT NULL,
  item_code VARCHAR(64) NOT NULL,
  barcode VARCHAR(64) NULL,
  item_name VARCHAR(255) NOT NULL,
  sub_name VARCHAR(255) NULL,
  item_size VARCHAR(128) NULL,
  source_category_id VARCHAR(32) NULL,
  source_brand_id VARCHAR(32) NULL,
  source_department_id VARCHAR(32) NULL,
  source_supplier_id VARCHAR(32) NULL,
  source_unit_id VARCHAR(32) NULL,
  status_code VARCHAR(32) NULL,
  lifetime_code VARCHAR(32) NULL,
  retail_price DECIMAL(18,4) NULL,
  purchase_price DECIMAL(18,4) NULL,
  source_version VARCHAR(64) NULL,
  source_created_at DATETIME NULL,
  source_modified_at DATETIME NULL,
  source_approved_at DATETIME NULL,
  list_hash CHAR(64) NULL,
  detail_hash CHAR(64) NULL,
  first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at DATETIME NULL,
  UNIQUE KEY uq_products_source (tenant_id, source_item_id),
  KEY idx_products_code (tenant_id, item_code),
  KEY idx_products_barcode (tenant_id, barcode),
  KEY idx_products_category (tenant_id, source_category_id),
  KEY idx_products_modified (tenant_id, source_modified_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_profile (
  product_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  payload JSON NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_product_profile_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_child_records (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  product_id BIGINT UNSIGNED NOT NULL,
  section VARCHAR(64) NOT NULL,
  source_key VARCHAR(128) NOT NULL,
  payload JSON NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_product_child_record (product_id, section, source_key),
  KEY idx_product_child_section (section, product_id),
  CONSTRAINT fk_product_child_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_supply_qualifications (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  product_id BIGINT UNSIGNED NOT NULL,
  source_key CHAR(64) NOT NULL,
  source_branch_id VARCHAR(32) NULL,
  source_supplier_id VARCHAR(32) NULL,
  barcode VARCHAR(64) NULL,
  unit_name VARCHAR(128) NULL,
  purchase_price DECIMAL(18,4) NULL,
  primary_flag TINYINT NULL,
  payload JSON NOT NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_supply_qualification (product_id, source_key),
  KEY idx_supply_branch (source_branch_id, source_supplier_id),
  CONSTRAINT fk_supply_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_branch_scopes (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  product_id BIGINT UNSIGNED NOT NULL,
  source_key CHAR(64) NOT NULL,
  source_branch_id VARCHAR(32) NULL,
  lifetime_code VARCHAR(32) NULL,
  logistics_mode VARCHAR(32) NULL,
  payload JSON NOT NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_branch_scope (product_id, source_key),
  KEY idx_scope_branch (source_branch_id, product_id),
  CONSTRAINT fk_scope_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_branch_prices (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  product_id BIGINT UNSIGNED NOT NULL,
  price_kind VARCHAR(32) NOT NULL,
  source_key CHAR(64) NOT NULL,
  source_branch_id VARCHAR(32) NULL,
  barcode VARCHAR(64) NULL,
  unit_name VARCHAR(128) NULL,
  retail_price DECIMAL(18,4) NULL,
  delivery_price DECIMAL(18,4) NULL,
  sales_price DECIMAL(18,4) NULL,
  member_price DECIMAL(18,4) NULL,
  payload JSON NOT NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_branch_price (product_id, price_kind, source_key),
  KEY idx_price_branch (source_branch_id, product_id),
  CONSTRAINT fk_price_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_branch_stocks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  product_id BIGINT UNSIGNED NOT NULL,
  source_key CHAR(64) NOT NULL,
  source_branch_id VARCHAR(32) NULL,
  stock_qty DECIMAL(18,3) NULL,
  cost_price DECIMAL(18,4) NULL,
  payload JSON NOT NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_branch_stock (product_id, source_key),
  KEY idx_stock_branch (source_branch_id, product_id),
  CONSTRAINT fk_stock_product FOREIGN KEY (product_id) REFERENCES products(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
