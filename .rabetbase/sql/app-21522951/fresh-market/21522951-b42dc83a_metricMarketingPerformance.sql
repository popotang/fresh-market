-- @lovrabet.sqlCode: 21522951-b42dc83a
-- @lovrabet.sqlName: metricMarketingPerformance
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- @lovrabet.syncedAt: 2026-11-14T07:29:58.194Z
-- 只读：营销优惠金额与促销销售覆盖；与综合让利分开，不计算增量 ROI。
SELECT
  <choose>
    <when test="dimension == 'day'">
      DATE_FORMAT(p.sold_at, '%Y-%m-%d') AS biz_date,
      DATE_FORMAT(p.sold_at, '%Y-%m-%d') AS dim_key,
      DATE_FORMAT(p.sold_at, '%Y-%m-%d') AS dim_label,
    </when>
    <when test="dimension == 'branch'">
      p.branch_code AS dim_key,
      COALESCE(p.branch_name, '未知门店') AS dim_label,
    </when>
    <when test="dimension == 'promotion'">
      COALESCE(NULLIF(p.promotion_code, ''), NULLIF(p.promotion_id, ''), '未关联促销') AS dim_key,
      COALESCE(NULLIF(p.activity_name, ''), NULLIF(p.promotion_code, ''), NULLIF(p.promotion_id, ''), '未关联促销') AS dim_label,
    </when>
    <when test="dimension == 'category'">
      COALESCE(NULLIF(p.item_category_name, ''), '未分类') AS dim_key,
      COALESCE(NULLIF(p.item_category_name, ''), '未分类') AS dim_label,
    </when>
    <otherwise>
      COALESCE(NULLIF(p.item_code, ''), '未知商品') AS item_id,
      COALESCE(NULLIF(p.item_name, ''), '未知商品') AS item_name,
      COALESCE(NULLIF(p.item_code, ''), '未知商品') AS dim_key,
      COALESCE(NULLIF(p.item_name, ''), '未知商品') AS dim_label,
    </otherwise>
  </choose>
  ROUND(SUM(CASE WHEN COALESCE(NULLIF(p.promotion_id, ''), NULLIF(p.promotion_code, '')) IS NOT NULL
                 THEN p.amount ELSE 0 END), 2) AS marketing_discount_amount,
  ROUND(SUM(CASE WHEN COALESCE(NULLIF(p.promotion_id, ''), NULLIF(p.promotion_code, '')) IS NOT NULL
                 THEN p.sale_amount ELSE 0 END), 2) AS marketing_sales_amount,
  ROUND(SUM(CASE WHEN COALESCE(NULLIF(p.promotion_id, ''), NULLIF(p.promotion_code, '')) IS NOT NULL
                 THEN p.original_amount ELSE 0 END), 2) AS marketing_original_amount,
  COUNT(DISTINCT CASE WHEN COALESCE(NULLIF(p.promotion_id, ''), NULLIF(p.promotion_code, '')) IS NOT NULL
                      THEN p.source_order_id END) AS marketing_order_count,
  COALESCE(n.net_sales_amount, 0) AS net_sales_amount,
  COUNT(CASE WHEN COALESCE(NULLIF(p.promotion_id, ''), NULLIF(p.promotion_code, '')) IS NOT NULL
             THEN 1 END) AS promotion_rows,
  COUNT(CASE WHEN COALESCE(NULLIF(p.promotion_id, ''), NULLIF(p.promotion_code, '')) IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM promotions pr
                WHERE pr.tenant_id = p.tenant_id
                  AND (
                    (p.promotion_id &lt;&gt; '' AND pr.source_promotion_id = p.promotion_id)
                    OR (p.promotion_code &lt;&gt; '' AND pr.promotion_code = p.promotion_code)
                  )
              ) THEN 1 END) AS promotion_linked_rows
FROM retail_promote_shares p
CROSS JOIN (
  SELECT COALESCE(SUM(f.sale_amount), 0) AS net_sales_amount
  FROM retail_sale_flows f FORCE INDEX (idx_retail_flow_sold)
  WHERE f.tenant_id = #{tenantId}
    AND f.sold_at &gt;= #{startDate}
    AND f.sold_at &lt; DATE_ADD(#{endDate}, INTERVAL 1 DAY)
    <if test="branchId != null and branchId != ''">
      AND f.source_branch_id = #{branchId, jdbcType=VARCHAR}
    </if>
) n
WHERE p.tenant_id = #{tenantId}
  AND p.sold_at &gt;= #{startDate}
  AND p.sold_at &lt; DATE_ADD(#{endDate}, INTERVAL 1 DAY)
  <if test="branchId != null and branchId != ''">
    AND p.branch_code = (
      SELECT b.branch_code FROM branches b
      WHERE b.tenant_id = #{tenantId}
        AND b.source_branch_id = #{branchId, jdbcType=VARCHAR}
      LIMIT 1
    )
  </if>
GROUP BY
  <choose>
    <when test="dimension == 'day'">biz_date, dim_key, dim_label</when>
    <when test="dimension == 'item'">item_id, item_name, dim_key, dim_label</when>
    <otherwise>dim_key, dim_label</otherwise>
  </choose>,
  n.net_sales_amount
ORDER BY dim_key ASC
LIMIT 5000
