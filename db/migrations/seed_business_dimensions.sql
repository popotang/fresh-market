-- Seed owned dimension tables from facts already present in the owned copy.
-- These are transaction-derived dimensions, not a claim of Kemai master-data
-- synchronization. No member phone or other member contact field is copied.
-- INSERT IGNORE makes the seed repeatable without rewriting verified rows.

INSERT IGNORE INTO members (
  tenant_id, source_member_id, member_card_no, member_grade_id,
  member_grade_name, first_seen_at, last_seen_at
)
SELECT
  tenant_id,
  source_member_id,
  MAX(NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.memberCardNo')), 'null'), '')),
  MAX(NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.memberGradeId')), 'null'), '')),
  MAX(NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.memberGradeName')), 'null'), '')),
  MIN(captured_at),
  MAX(captured_at)
FROM retail_orders
WHERE source_member_id IS NOT NULL AND source_member_id <> ''
GROUP BY tenant_id, source_member_id;

INSERT IGNORE INTO members (
  tenant_id, source_member_id, member_card_no, member_grade_id,
  member_grade_name, first_seen_at, last_seen_at
)
SELECT
  tenant_id,
  NULL,
  member_card_no,
  MAX(NULLIF(member_grade_id, '')),
  MAX(NULLIF(member_grade_name, '')),
  MIN(captured_at),
  MAX(captured_at)
FROM retail_promote_shares
WHERE member_card_no IS NOT NULL AND member_card_no <> ''
GROUP BY tenant_id, member_card_no;

INSERT IGNORE INTO staff_members (
  tenant_id, source_employee_id, employee_code, employee_name,
  cashier_flag, approver_flag, first_seen_at, last_seen_at
)
SELECT
  d.tenant_id,
  a.source_approver_id,
  MAX(NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(a.payload, '$.approverCode')), 'null'), '')),
  MAX(NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(a.payload, '$.approverName')), 'null'), '')),
  0,
  1,
  COALESCE(MIN(a.approved_at), CURRENT_TIMESTAMP),
  COALESCE(MAX(a.approved_at), CURRENT_TIMESTAMP)
FROM purchase_document_approvals a
JOIN purchase_documents d ON d.id = a.document_id
WHERE a.source_approver_id IS NOT NULL AND a.source_approver_id <> ''
GROUP BY d.tenant_id, a.source_approver_id;

INSERT IGNORE INTO staff_members (
  tenant_id, source_employee_id, employee_code, employee_name,
  cashier_flag, approver_flag, first_seen_at, last_seen_at
)
SELECT
  tenant_id,
  JSON_UNQUOTE(JSON_EXTRACT(payload, '$.cashierId')),
  MAX(NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.cashierCode')), 'null'), '')),
  MAX(NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.cashierName')), 'null'), '')),
  1,
  0,
  MIN(captured_at),
  MAX(captured_at)
FROM retail_orders
WHERE JSON_UNQUOTE(JSON_EXTRACT(payload, '$.cashierId')) IS NOT NULL
  AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.cashierId')) <> ''
  AND JSON_UNQUOTE(JSON_EXTRACT(payload, '$.cashierId')) <> 'null'
GROUP BY tenant_id, JSON_UNQUOTE(JSON_EXTRACT(payload, '$.cashierId'));

INSERT IGNORE INTO payways (
  tenant_id, payway_code, payway_name, first_seen_at, last_seen_at
)
SELECT
  o.tenant_id,
  p.payway_code,
  MAX(NULLIF(p.payway_name, '')),
  COALESCE(MIN(p.paid_at), CURRENT_TIMESTAMP),
  COALESCE(MAX(p.paid_at), CURRENT_TIMESTAMP)
FROM retail_payments p
JOIN retail_orders o ON o.id = p.order_id
WHERE p.payway_code IS NOT NULL AND p.payway_code <> ''
GROUP BY o.tenant_id, p.payway_code;

INSERT IGNORE INTO promotions (
  tenant_id, source_promotion_id, promotion_code, activity_code,
  activity_name, marketing_type, first_seen_at, last_seen_at
)
SELECT
  tenant_id,
  NULLIF(promotion_id, ''),
  NULLIF(promotion_code, ''),
  NULLIF(activity_code, ''),
  NULLIF(activity_name, ''),
  NULLIF(marketing_type, ''),
  MIN(captured_at),
  MAX(captured_at)
FROM retail_promote_shares
WHERE COALESCE(
  NULLIF(promotion_id, ''),
  NULLIF(promotion_code, ''),
  NULLIF(activity_code, '')
) IS NOT NULL
GROUP BY
  tenant_id, promotion_id, promotion_code, activity_code,
  activity_name, marketing_type;

-- A source promotion ID and a promotion code are independent lookup identities.
-- Seed any identity skipped by the grouped insert above without duplicating the
-- other unique key. This keeps both relations usable for transaction rows.
INSERT IGNORE INTO promotions (
  tenant_id, source_promotion_id, promotion_code, activity_code,
  activity_name, marketing_type, first_seen_at, last_seen_at
)
SELECT
  p.tenant_id,
  NULL,
  p.promotion_code,
  MAX(NULLIF(p.activity_code, '')),
  MAX(NULLIF(p.activity_name, '')),
  MAX(NULLIF(p.marketing_type, '')),
  MIN(p.captured_at),
  MAX(p.captured_at)
FROM retail_promote_shares p
LEFT JOIN promotions existing
  ON existing.tenant_id = p.tenant_id
 AND existing.promotion_code = p.promotion_code
WHERE p.promotion_code IS NOT NULL AND p.promotion_code <> ''
  AND existing.id IS NULL
GROUP BY p.tenant_id, p.promotion_code;

INSERT IGNORE INTO promotions (
  tenant_id, source_promotion_id, promotion_code, activity_code,
  activity_name, marketing_type, first_seen_at, last_seen_at
)
SELECT
  p.tenant_id,
  p.promotion_id,
  NULL,
  NULL,
  MAX(NULLIF(p.activity_name, '')),
  MAX(NULLIF(p.marketing_type, '')),
  MIN(p.captured_at),
  MAX(p.captured_at)
FROM retail_promote_shares p
LEFT JOIN promotions existing
  ON existing.tenant_id = p.tenant_id
 AND existing.source_promotion_id = p.promotion_id
WHERE p.promotion_id IS NOT NULL AND p.promotion_id <> ''
  AND existing.id IS NULL
GROUP BY p.tenant_id, p.promotion_id;
