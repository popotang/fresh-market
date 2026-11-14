-- @lovrabet.sqlCode: 21522951-f1358599
-- @lovrabet.sqlName: metricDataCoverage
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- @lovrabet.syncedAt: 2026-11-14T07:29:56.393Z
-- 只读：返回数据窗口、成本覆盖、库存快照覆盖、会员识别、促销关联和支付事实可用性。
-- 未传日期时以最近营业日为结束日、回看 7 天；结果只描述自有副本。
WITH win AS (
  SELECT
    <choose>
      <when test="startDate != null and startDate != ''">#{startDate}</when>
      <otherwise>
        DATE_SUB(
          <choose>
            <when test="endDate != null and endDate != ''">#{endDate}</when>
            <otherwise>(SELECT MAX(DATE(sold_at)) FROM retail_sale_flows WHERE tenant_id = #{tenantId})</otherwise>
          </choose>,
          INTERVAL 6 DAY
        )
      </otherwise>
    </choose> AS start_date,
    <choose>
      <when test="endDate != null and endDate != ''">#{endDate}</when>
      <otherwise>(SELECT MAX(DATE(sold_at)) FROM retail_sale_flows WHERE tenant_id = #{tenantId})</otherwise>
    </choose> AS end_date
),
cost AS (
  SELECT
    d.tenant_id,
    l.source_item_id AS item_id,
    SUM(CASE WHEN d.document_kind = 'receipt' THEN l.amount
             WHEN d.document_kind = 'return' THEN -l.amount ELSE 0 END)
      / NULLIF(SUM(CASE WHEN d.document_kind = 'receipt' THEN l.quantity
                       WHEN d.document_kind = 'return' THEN -l.quantity ELSE 0 END), 0) AS avg_cost
  FROM purchase_document_lines l
  JOIN purchase_documents d ON d.id = l.document_id
  CROSS JOIN win w
  WHERE d.document_kind IN ('receipt', 'return')
    AND d.source_created_at &lt; DATE_ADD(w.end_date, INTERVAL 1 DAY)
  GROUP BY d.tenant_id, l.source_item_id
  HAVING SUM(CASE WHEN d.document_kind = 'receipt' THEN l.quantity
                  WHEN d.document_kind = 'return' THEN -l.quantity ELSE 0 END) &gt; 0
),
sales_calc AS (
  SELECT
    COUNT(*) AS sales_row_count,
    COALESCE(SUM(CASE WHEN f.sale_amount &gt; 0 THEN f.sale_amount ELSE 0 END), 0) AS gross_sales_amount,
    COALESCE(SUM(f.sale_amount), 0) AS net_sales_amount,
    COALESCE(SUM(CASE WHEN f.member_card &lt;&gt; '' AND f.sale_amount &gt; 0 THEN f.sale_amount ELSE 0 END), 0) AS member_gross_sales_amount,
    COALESCE(SUM(CASE WHEN f.member_card &lt;&gt; '' AND f.sale_amount &gt; 0 THEN f.sale_amount ELSE 0 END), 0) AS member_identified_sales_amount,
    COALESCE(SUM(CASE WHEN c.avg_cost IS NOT NULL THEN f.sale_amount ELSE 0 END), 0) AS sales_with_cost
  FROM retail_sale_flows f FORCE INDEX (idx_retail_flow_sold)
  CROSS JOIN win w
  LEFT JOIN cost c ON c.tenant_id = f.tenant_id AND c.item_id = f.source_item_id
  WHERE f.tenant_id = #{tenantId}
    AND f.sold_at &gt;= w.start_date
    AND f.sold_at &lt; DATE_ADD(w.end_date, INTERVAL 1 DAY)
    <if test="branchId != null and branchId != ''">
      AND f.source_branch_id = #{branchId, jdbcType=VARCHAR}
    </if>
),
snapshot_ranked AS (
  SELECT
    s.*,
    ROW_NUMBER() OVER (
      PARTITION BY s.source_branch_id, s.product_id
      ORDER BY s.captured_at DESC, s.id DESC
    ) AS rn
  FROM product_branch_stocks s
  WHERE 1 = 1
    <if test="branchId != null and branchId != ''">
      AND s.source_branch_id = #{branchId, jdbcType=VARCHAR}
    </if>
),
snapshot_latest AS (
  SELECT * FROM snapshot_ranked WHERE rn = 1
),
sold7 AS (
  SELECT
    f.source_item_id AS item_id,
    f.source_branch_id AS branch_id,
    SUM(f.quantity) AS sold_qty,
    SUM(f.sale_amount) AS sales_amount
  FROM retail_sale_flows f FORCE INDEX (idx_retail_flow_sold)
  CROSS JOIN win w
  WHERE f.tenant_id = #{tenantId}
    AND f.sold_at &gt;= DATE_SUB(w.end_date, INTERVAL 6 DAY)
    AND f.sold_at &lt; DATE_ADD(w.end_date, INTERVAL 1 DAY)
    <if test="branchId != null and branchId != ''">
      AND f.source_branch_id = #{branchId, jdbcType=VARCHAR}
    </if>
  GROUP BY f.source_item_id, f.source_branch_id
),
snapshot_products AS (
  SELECT
    sl.source_branch_id AS branch_id,
    p.source_item_id AS item_id
  FROM snapshot_latest sl
  JOIN products p ON p.id = sl.product_id AND p.tenant_id = #{tenantId}
),
sold7_covered AS (
  SELECT d.branch_id, d.item_id, d.sales_amount
  FROM sold7 d
  WHERE EXISTS (
    SELECT 1 FROM snapshot_products sp
    WHERE sp.branch_id = d.branch_id AND sp.item_id = d.item_id
  )
),
promo AS (
  SELECT
    SUM(CASE WHEN COALESCE(NULLIF(p.promotion_id, ''), NULLIF(p.promotion_code, '')) IS NOT NULL THEN 1 ELSE 0 END) AS promotion_rows,
    SUM(CASE WHEN COALESCE(NULLIF(p.promotion_id, ''), NULLIF(p.promotion_code, '')) IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM promotions pr
                WHERE pr.tenant_id = p.tenant_id
                  AND (
                    (p.promotion_id &lt;&gt; '' AND pr.source_promotion_id = p.promotion_id)
                    OR (p.promotion_code &lt;&gt; '' AND pr.promotion_code = p.promotion_code)
                  )
              ) THEN 1 ELSE 0 END) AS promotion_linked_rows
  FROM retail_promote_shares p
  CROSS JOIN win w
  WHERE p.tenant_id = #{tenantId}
    AND p.sold_at &gt;= w.start_date
    AND p.sold_at &lt; DATE_ADD(w.end_date, INTERVAL 1 DAY)
    <if test="branchId != null and branchId != ''">
      AND p.branch_code = (
        SELECT b.branch_code FROM branches b
        WHERE b.tenant_id = #{tenantId} AND b.source_branch_id = #{branchId, jdbcType=VARCHAR}
        LIMIT 1
      )
    </if>
)
SELECT
  (SELECT MAX(DATE(sold_at)) FROM retail_sale_flows WHERE tenant_id = #{tenantId}) AS latest_biz_date,
  w.start_date AS window_start_date,
  w.end_date AS window_end_date,
  s.sales_row_count,
  ROUND(s.gross_sales_amount, 2) AS gross_sales_amount,
  ROUND(s.net_sales_amount, 2) AS net_sales_amount,
  ROUND(s.member_gross_sales_amount, 2) AS member_gross_sales_amount,
  ROUND(s.member_identified_sales_amount, 2) AS member_identified_sales_amount,
  ROUND(s.sales_with_cost, 2) AS sales_with_cost,
  (SELECT COUNT(*) FROM snapshot_latest) AS snapshot_sku_count,
  (SELECT COUNT(DISTINCT item_id, branch_id) FROM sold7 WHERE COALESCE(item_id, '') &lt;&gt; '') AS sold_sku_count,
  (SELECT COUNT(*) FROM sold7_covered) AS covered_sold_sku_count,
  ROUND((SELECT COALESCE(SUM(sales_amount), 0) FROM sold7), 2) AS sold_sales_amount,
  ROUND((SELECT COALESCE(SUM(sales_amount), 0) FROM sold7_covered), 2) AS covered_sales_amount,
  (SELECT MAX(captured_at) FROM product_branch_stocks
    WHERE 1 = 1
    <if test="branchId != null and branchId != ''">
      AND source_branch_id = #{branchId, jdbcType=VARCHAR}
    </if>) AS latest_snapshot_at,
  (SELECT MIN(captured_at) FROM product_branch_stocks
    WHERE 1 = 1
    <if test="branchId != null and branchId != ''">
      AND source_branch_id = #{branchId, jdbcType=VARCHAR}
    </if>) AS earliest_snapshot_at,
  TIMESTAMPDIFF(
    HOUR,
    (SELECT MAX(captured_at) FROM product_branch_stocks
      WHERE 1 = 1
      <if test="branchId != null and branchId != ''">
        AND source_branch_id = #{branchId, jdbcType=VARCHAR}
      </if>),
    NOW()
  ) AS snapshot_age_hours,
  COALESCE(p.promotion_rows, 0) AS promotion_rows,
  COALESCE(p.promotion_linked_rows, 0) AS promotion_linked_rows,
  (
    SELECT COUNT(*)
    FROM retail_payments pay
    JOIN retail_orders o ON o.id = pay.order_id
    CROSS JOIN win w3
    WHERE o.tenant_id = #{tenantId}
      AND o.sold_at &gt;= w3.start_date
      AND o.sold_at &lt; DATE_ADD(w3.end_date, INTERVAL 1 DAY)
      <if test="branchId != null and branchId != ''">
        AND o.source_branch_id = #{branchId, jdbcType=VARCHAR}
      </if>
  ) AS payment_fact_count
FROM win w
CROSS JOIN sales_calc s
LEFT JOIN promo p ON 1 = 1
