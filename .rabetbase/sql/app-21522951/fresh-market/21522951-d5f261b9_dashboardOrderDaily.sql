-- @lovrabet.sqlCode: 21522951-d5f261b9
-- @lovrabet.sqlName: dashboardOrderDaily
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql

SELECT
  DATE_FORMAT(o.sold_at, '%Y-%m-%d') AS biz_date,
  o.source_branch_id AS branch_id,
  COUNT(*) AS order_count,
  ROUND(SUM(o.sale_amount), 2) AS order_sales,
  ROUND(SUM(CASE WHEN o.source_member_id IS NOT NULL AND o.source_member_id != ''
                 THEN o.sale_amount ELSE 0 END), 2) AS member_sales,
  COUNT(CASE WHEN o.source_member_id IS NOT NULL AND o.source_member_id != ''
             THEN 1 END) AS member_orders
FROM retail_orders o
WHERE o.sold_at >= #{startDate}
  AND o.sold_at &lt; DATE_ADD(#{endDate}, INTERVAL 1 DAY)
  <if test="branchId != null and branchId != ''">
    AND o.source_branch_id = #{branchId, jdbcType=VARCHAR}
  </if>
GROUP BY biz_date, o.source_branch_id
ORDER BY biz_date ASC, branch_id ASC
