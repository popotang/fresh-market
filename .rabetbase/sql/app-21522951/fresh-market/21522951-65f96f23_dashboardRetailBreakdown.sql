-- @lovrabet.sqlCode: 21522951-65f96f23
-- @lovrabet.sqlName: dashboardRetailBreakdown
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql

SELECT
  <choose>
    <when test="dimension == 'hour'">
      DATE_FORMAT(f.sold_at, '%H') AS dim_key,
      CONCAT(DATE_FORMAT(f.sold_at, '%H'), ':00') AS dim_label,
    </when>
    <when test="dimension == 'department'">
      f.item_department AS dim_key,
      f.item_department AS dim_label,
    </when>
    <when test="dimension == 'category'">
      f.item_category AS dim_key,
      f.item_category AS dim_label,
    </when>
    <when test="dimension == 'item'">
      COALESCE(NULLIF(f.source_item_id, ''), '未知') AS dim_key,
      f.item_name AS dim_label,
    </when>
    <when test="dimension == 'cashier'">
      f.cashier_name AS dim_key,
      f.cashier_name AS dim_label,
    </when>
    <when test="dimension == 'shift'">
      COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(f.payload, '$.shift')), ''), '未排班') AS dim_key,
      COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(f.payload, '$.shift')), ''), '未排班') AS dim_label,
    </when>
    <when test="dimension == 'member'">
      CASE WHEN f.member_card = '' THEN 'guest' ELSE 'member' END AS dim_key,
      CASE WHEN f.member_card = '' THEN '非会员' ELSE '会员' END AS dim_label,
    </when>
    <otherwise>
      COALESCE(b.branch_name, '未知门店') AS dim_key,
      COALESCE(b.branch_name, '未知门店') AS dim_label,
    </otherwise>
  </choose>
  ROUND(SUM(f.sale_amount), 2) AS sales_amount,
  ROUND(SUM(f.quantity), 2) AS qty,
  COUNT(*) AS line_count,
  COUNT(DISTINCT f.receipt_no) AS order_count,
  ROUND(SUM(f.original_amt_cents) / 10000, 2) AS original_amount,
  ROUND(SUM(CASE WHEN c.avg_cost IS NOT NULL THEN f.sale_amount ELSE 0 END), 2) AS sales_with_cost,
  ROUND(SUM(CASE WHEN c.avg_cost IS NOT NULL THEN f.sale_amount - f.quantity * c.avg_cost ELSE 0 END), 2) AS gross_profit
FROM retail_sale_flows f
LEFT JOIN branches b ON b.source_branch_id = f.source_branch_id
LEFT JOIN (
  SELECT l.source_item_id AS item_id, SUM(l.amount) / NULLIF(SUM(l.quantity), 0) AS avg_cost
  FROM purchase_document_lines l
  GROUP BY l.source_item_id
) c ON c.item_id = f.source_item_id
WHERE f.sold_at &gt;= #{startDate}
  AND f.sold_at &lt; DATE_ADD(#{endDate}, INTERVAL 1 DAY)
  <if test="branchId != null and branchId != ''">
    AND f.source_branch_id = #{branchId, jdbcType=VARCHAR}
  </if>
GROUP BY dim_key, dim_label
ORDER BY
  <choose>
    <when test="dimension == 'hour'">dim_key ASC</when>
    <otherwise>sales_amount DESC</otherwise>
  </choose>
LIMIT 300
