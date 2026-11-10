-- @lovrabet.sqlCode: 21522951-59797d83
-- @lovrabet.sqlName: dashboardRetailDaily
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql

SELECT
  DATE_FORMAT(f.sold_at, '%Y-%m-%d') AS biz_date,
  f.source_branch_id AS branch_id,
  COALESCE(b.branch_name, '未知门店') AS branch_name,
  ROUND(SUM(f.sale_amount), 2) AS sales_amount,
  ROUND(SUM(f.quantity), 2) AS qty,
  COUNT(*) AS line_count,
  COUNT(DISTINCT f.receipt_no) AS order_count,
  ROUND(SUM(CASE WHEN SIGN(f.sale_amount) = -1 THEN f.sale_amount ELSE 0 END), 2) AS refund_amount,
  ROUND(SUM(CASE WHEN f.member_card <> '' THEN f.sale_amount ELSE 0 END), 2) AS member_sales,
  COUNT(DISTINCT CASE WHEN f.member_card <> '' THEN f.receipt_no END) AS member_orders,
  ROUND(SUM(CASE WHEN c.avg_cost IS NOT NULL THEN f.sale_amount ELSE 0 END), 2) AS sales_with_cost,
  ROUND(SUM(CASE WHEN c.avg_cost IS NOT NULL THEN f.sale_amount - f.quantity * c.avg_cost ELSE 0 END), 2) AS gross_profit
FROM retail_sale_flows f
LEFT JOIN branches b ON b.source_branch_id = f.source_branch_id
LEFT JOIN (
  SELECT l.source_item_id AS item_id, SUM(l.amount) / NULLIF(SUM(l.quantity), 0) AS avg_cost
  FROM purchase_document_lines l
  GROUP BY l.source_item_id
) c ON c.item_id = f.source_item_id
WHERE f.sold_at >= #{startDate}
  AND f.sold_at < DATE_ADD(#{endDate}, INTERVAL 1 DAY)
GROUP BY biz_date, f.source_branch_id, b.branch_name
ORDER BY biz_date ASC, branch_id ASC
