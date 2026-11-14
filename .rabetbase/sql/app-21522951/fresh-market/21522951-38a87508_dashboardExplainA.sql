-- @lovrabet.sqlCode: 21522951-38a87508
-- @lovrabet.sqlName: dashboardExplainA
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql

SELECT
  DATE_FORMAT(f.sold_at, '%Y-%m-%d') AS biz_date,
  f.source_branch_id AS branch_id,
  COALESCE(b.branch_name, '未知门店') AS branch_name,
  ROUND(SUM(f.sale_amount), 2) AS sales_amount,
  COUNT(DISTINCT f.receipt_no) AS order_count,
  ROUND(SUM(CASE WHEN f.member_card <> '' THEN f.sale_amount ELSE 0 END), 2) AS member_sales,
  COUNT(DISTINCT CASE WHEN f.member_card <> '' THEN f.receipt_no END) AS member_orders
FROM retail_sale_flows f FORCE INDEX (idx_bi_dept)
LEFT JOIN branches b ON b.source_branch_id = f.source_branch_id
WHERE f.sold_at >= '2026-10-11' AND f.sold_at < '2026-11-10'
GROUP BY biz_date, f.source_branch_id, b.branch_name
