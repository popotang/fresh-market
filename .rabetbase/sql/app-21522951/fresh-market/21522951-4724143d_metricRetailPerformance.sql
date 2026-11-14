-- @lovrabet.sqlCode: 21522951-4724143d
-- @lovrabet.sqlName: metricRetailPerformance
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- @lovrabet.syncedAt: 2026-11-14T07:29:55.709Z
-- 只读：销售、订单、会员、综合让利和估算毛利的基础度量；比率由 BFF 统一派生。
-- 加权采购成本只取实际收货与采购退货，且不使用查询结束日之后的单据。
SELECT
  <choose>
    <when test="dimension == 'day'">
      DATE_FORMAT(f.sold_at, '%Y-%m-%d') AS biz_date,
      DATE_FORMAT(f.sold_at, '%Y-%m-%d') AS dim_key,
      DATE_FORMAT(f.sold_at, '%Y-%m-%d') AS dim_label,
    </when>
    <when test="dimension == 'branch'">
      f.source_branch_id AS branch_id,
      COALESCE(b.branch_name, '未知门店') AS branch_name,
      f.source_branch_id AS dim_key,
      COALESCE(b.branch_name, '未知门店') AS dim_label,
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
      COALESCE(NULLIF(f.source_item_id, ''), '未知') AS item_id,
      f.item_name AS item_name,
      COALESCE(NULLIF(f.source_item_id, ''), '未知') AS dim_key,
      f.item_name AS dim_label,
    </when>
    <when test="dimension == 'cashier'">
      f.cashier_name AS dim_key,
      f.cashier_name AS dim_label,
    </when>
    <otherwise>
      DATE_FORMAT(f.sold_at, '%H') AS dim_key,
      CONCAT(DATE_FORMAT(f.sold_at, '%H'), ':00') AS dim_label,
    </otherwise>
  </choose>
  ROUND(COALESCE(SUM(CASE WHEN f.sale_amount &gt; 0 THEN f.sale_amount ELSE 0 END), 0), 2) AS gross_sales_amount,
  ROUND(COALESCE(-SUM(CASE WHEN f.sale_amount &lt; 0 THEN f.sale_amount ELSE 0 END), 0), 2) AS refund_amount,
  ROUND(COALESCE(SUM(f.sale_amount), 0), 2) AS net_sales_amount,
  ROUND(COALESCE(SUM(CASE WHEN f.sale_amount &gt; 0 THEN f.quantity ELSE 0 END), 0), 2) AS gross_quantity,
  ROUND(COALESCE(SUM(f.quantity), 0), 2) AS net_quantity,
  COUNT(DISTINCT f.source_order_id) AS order_count,
  COUNT(DISTINCT CASE WHEN f.sale_amount &gt; 0 THEN f.source_order_id END) AS sales_order_count,
  ROUND(COALESCE(SUM(CASE WHEN f.sale_amount &gt; 0 THEN f.original_amt_cents / 10000 ELSE 0 END), 0), 2) AS original_sales_amount,
  ROUND(COALESCE(SUM(CASE WHEN f.member_card &lt;&gt; '' THEN f.sale_amount ELSE 0 END), 0), 2) AS member_net_sales_amount,
  ROUND(COALESCE(SUM(CASE WHEN f.member_card &lt;&gt; '' AND f.sale_amount &gt; 0 THEN f.sale_amount ELSE 0 END), 0), 2) AS member_gross_sales_amount,
  COUNT(DISTINCT CASE WHEN f.member_card &lt;&gt; '' AND f.sale_amount &gt; 0 THEN f.source_order_id END) AS member_sales_order_count,
  ROUND(COALESCE(SUM(CASE WHEN c.avg_cost IS NOT NULL THEN f.sale_amount ELSE 0 END), 0), 2) AS sales_with_cost,
  ROUND(COALESCE(SUM(CASE WHEN c.avg_cost IS NOT NULL
    THEN f.sale_amount - f.quantity * c.avg_cost ELSE 0 END), 0), 2) AS estimated_gross_profit
FROM retail_sale_flows f FORCE INDEX (idx_retail_flow_sold)
LEFT JOIN branches b
  ON b.tenant_id = f.tenant_id
 AND b.source_branch_id = f.source_branch_id
LEFT JOIN (
  SELECT
    d.tenant_id,
    l.source_item_id AS item_id,
    SUM(CASE WHEN d.document_kind = 'receipt' THEN l.amount
             WHEN d.document_kind = 'return' THEN -l.amount ELSE 0 END)
      / NULLIF(SUM(CASE WHEN d.document_kind = 'receipt' THEN l.quantity
                       WHEN d.document_kind = 'return' THEN -l.quantity ELSE 0 END), 0) AS avg_cost
  FROM purchase_document_lines l
  JOIN purchase_documents d ON d.id = l.document_id
  WHERE d.document_kind IN ('receipt', 'return')
    AND d.source_created_at &lt; DATE_ADD(#{endDate}, INTERVAL 1 DAY)
  GROUP BY d.tenant_id, l.source_item_id
  HAVING SUM(CASE WHEN d.document_kind = 'receipt' THEN l.quantity
                  WHEN d.document_kind = 'return' THEN -l.quantity ELSE 0 END) &gt; 0
) c ON c.tenant_id = f.tenant_id AND c.item_id = f.source_item_id
WHERE f.tenant_id = #{tenantId}
  AND f.sold_at &gt;= #{startDate}
  AND f.sold_at &lt; DATE_ADD(#{endDate}, INTERVAL 1 DAY)
  <if test="branchId != null and branchId != ''">
    AND f.source_branch_id = #{branchId, jdbcType=VARCHAR}
  </if>
GROUP BY
  <choose>
    <when test="dimension == 'day'">biz_date, dim_key, dim_label</when>
    <when test="dimension == 'branch'">branch_id, branch_name, dim_key, dim_label</when>
    <when test="dimension == 'item'">item_id, item_name, dim_key, dim_label</when>
    <otherwise>dim_key, dim_label</otherwise>
  </choose>
ORDER BY dim_key ASC
LIMIT 5000
