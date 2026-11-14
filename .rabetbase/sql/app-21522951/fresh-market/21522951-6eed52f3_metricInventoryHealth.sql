-- @lovrabet.sqlCode: 21522951-6eed52f3
-- @lovrabet.sqlName: metricInventoryHealth
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- @lovrabet.syncedAt: 2026-11-14T07:29:57.049Z
-- 只读：每个门店+商品的最新库存快照，以及固定近7天销量和末次销售时间。
-- 风险分级、SKU 计数和覆盖率由 BFF 统一计算；这里不输出“全店周转天数”。
WITH snapshot_ranked AS (
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
    SUM(f.quantity) AS sold_qty_7d,
    SUM(f.sale_amount) AS sales_amount_7d,
    MAX(f.sold_at) AS last_sold_at
  FROM retail_sale_flows f FORCE INDEX (idx_retail_flow_sold)
  WHERE f.tenant_id = #{tenantId}
    AND f.sold_at &gt;= DATE_SUB(#{endDate}, INTERVAL 6 DAY)
    AND f.sold_at &lt; DATE_ADD(#{endDate}, INTERVAL 1 DAY)
    <if test="branchId != null and branchId != ''">
      AND f.source_branch_id = #{branchId, jdbcType=VARCHAR}
    </if>
  GROUP BY f.source_item_id, f.source_branch_id
)
SELECT
  <choose>
    <when test="dimension == 'branch'">
      s.source_branch_id AS branch_id,
      COALESCE(b.branch_name, '未知门店') AS branch_name,
      s.source_branch_id AS dim_key,
      COALESCE(b.branch_name, '未知门店') AS dim_label,
    </when>
    <when test="dimension == 'category'">
      COALESCE(c.category_name, '未分类') AS dim_key,
      COALESCE(c.category_name, '未分类') AS dim_label,
    </when>
    <otherwise>
      COALESCE(p.source_item_id, '未知') AS item_id,
      COALESCE(p.item_name, '未知商品') AS item_name,
      COALESCE(p.source_item_id, '未知') AS dim_key,
      COALESCE(p.item_name, '未知商品') AS dim_label,
    </otherwise>
  </choose>
  COALESCE(u.unit_name, '') AS unit_name,
  ROUND(COALESCE(s.stock_qty, 0), 2) AS stock_qty,
  ROUND(s.cost_price, 4) AS cost_price,
  ROUND(COALESCE(s.stock_qty, 0) * s.cost_price, 2) AS stock_value,
  s.captured_at AS snapshot_at,
  ROUND(COALESCE(x.sold_qty_7d, 0), 2) AS sold_qty_7d,
  ROUND(COALESCE(x.sales_amount_7d, 0), 2) AS sales_amount_7d,
  x.last_sold_at AS last_sold_at
FROM snapshot_latest s
LEFT JOIN products p
  ON p.id = s.product_id
 AND p.tenant_id = #{tenantId}
LEFT JOIN categories c
  ON c.tenant_id = #{tenantId}
 AND c.source_category_id = p.source_category_id
LEFT JOIN units u
  ON u.tenant_id = #{tenantId}
 AND u.source_unit_id = p.source_unit_id
LEFT JOIN branches b
  ON b.tenant_id = #{tenantId}
 AND b.source_branch_id = s.source_branch_id
LEFT JOIN sold7 x
  ON x.branch_id = s.source_branch_id
 AND x.item_id = p.source_item_id
WHERE 1 = 1
  <if test="branchId != null and branchId != ''">
    AND s.source_branch_id = #{branchId, jdbcType=VARCHAR}
  </if>
ORDER BY stock_value DESC, dim_key ASC
LIMIT 5000
