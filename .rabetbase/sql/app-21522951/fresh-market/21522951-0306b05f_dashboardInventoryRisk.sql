-- @lovrabet.sqlCode: 21522951-0306b05f
-- @lovrabet.sqlName: dashboardInventoryRisk
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql

SELECT
  s.source_branch_id AS branch_id,
  COALESCE(b.branch_name, '未知门店') AS branch_name,
  p.item_id AS item_id,
  COALESCE(p.item_name, '未知商品') AS item_name,
  COALESCE(p.unit_name, '') AS unit_name,
  ROUND(MAX(s.stock_qty), 2) AS stock_qty,
  ROUND(MAX(s.cost_price), 2) AS cost_price,
  ROUND(MAX(s.stock_qty) * MAX(s.cost_price), 2) AS stock_value,
  MAX(s.captured_at) AS snapshot_at,
  ROUND(COALESCE(SUM(x.sold_qty), 0), 2) AS sold_qty,
  ROUND(COALESCE(SUM(x.sales_amount), 0), 2) AS sales_amount,
  x.last_sold_at AS last_sold_at,
  CASE
    WHEN COALESCE(SUM(x.sold_qty), 0) &gt; 0 AND MAX(s.stock_qty) &lt;= 0 THEN 'stockout'
    WHEN COALESCE(SUM(x.sold_qty), 0) = 0 THEN 'slow'
    WHEN MAX(s.stock_qty) / (SUM(x.sold_qty) / (DATEDIFF(#{endDate}, #{startDate}) + 1)) &lt; 3 THEN 'low'
    WHEN MAX(s.stock_qty) / (SUM(x.sold_qty) / (DATEDIFF(#{endDate}, #{startDate}) + 1)) &gt; 30 THEN 'overstock'
    ELSE 'healthy'
  END AS risk_level,
  CASE
    WHEN COALESCE(SUM(x.sold_qty), 0) &gt; 0
      THEN ROUND(MAX(s.stock_qty) / (SUM(x.sold_qty) / (DATEDIFF(#{endDate}, #{startDate}) + 1)), 1)
    ELSE NULL
  END AS days_cover
FROM product_branch_stocks s
LEFT JOIN (
  SELECT
    JSON_UNQUOTE(JSON_EXTRACT(pp.payload, '$.id')) AS item_id,
    JSON_UNQUOTE(JSON_EXTRACT(pp.payload, '$.name')) AS item_name,
    JSON_UNQUOTE(JSON_EXTRACT(pp.payload, '$.unitName')) AS unit_name,
    pp.product_id AS product_id
  FROM product_profile pp
) p ON p.product_id = s.product_id
LEFT JOIN branches b ON b.source_branch_id = s.source_branch_id
LEFT JOIN (
  SELECT
    f.source_item_id AS item_id,
    f.source_branch_id AS branch_id,
    SUM(f.quantity) AS sold_qty,
    SUM(f.sale_amount) AS sales_amount,
    MAX(f.sold_at) AS last_sold_at
  FROM retail_sale_flows f
  WHERE f.sold_at &gt;= #{startDate}
    AND f.sold_at &lt; DATE_ADD(#{endDate}, INTERVAL 1 DAY)
  GROUP BY item_id, f.source_branch_id
) x ON x.item_id = p.item_id AND x.branch_id = s.source_branch_id
WHERE 1 = 1
  <if test="branchId != null and branchId != ''">
    AND s.source_branch_id = #{branchId, jdbcType=VARCHAR}
  </if>
GROUP BY s.source_branch_id, b.branch_name, p.item_id, p.item_name, p.unit_name, x.last_sold_at
ORDER BY
  CASE
    WHEN COALESCE(SUM(x.sold_qty), 0) &gt; 0 AND MAX(s.stock_qty) &lt;= 0 THEN 0
    WHEN COALESCE(SUM(x.sold_qty), 0) &gt; 0 AND MAX(s.stock_qty) / (SUM(x.sold_qty) / (DATEDIFF(#{endDate}, #{startDate}) + 1)) &lt; 3 THEN 1
    WHEN COALESCE(SUM(x.sold_qty), 0) = 0 THEN 3
    ELSE 2
  END ASC,
  COALESCE(SUM(x.sales_amount), 0) DESC
LIMIT 300
