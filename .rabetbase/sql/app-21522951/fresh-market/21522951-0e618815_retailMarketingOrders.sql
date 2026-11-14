-- @lovrabet.sqlCode: 21522951-0e618815
-- @lovrabet.sqlName: retailMarketingOrders
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- 只读：订单头营销/会员字段（payload JSON 提取）；discountAmt 与 sale_amount 同缩放（/10000）。
SELECT
  o.source_order_id AS order_id,
  o.receipt_no AS receipt_no,
  o.sold_at AS sold_time,
  o.source_branch_id AS branch_id,
  o.sale_amount AS sale_amount,
  o.order_amount AS order_amount,
  JSON_UNQUOTE(JSON_EXTRACT(o.payload, '$.memberCardNo')) AS member_card_no,
  JSON_UNQUOTE(JSON_EXTRACT(o.payload, '$.memberGradeId')) AS member_grade_id,
  JSON_UNQUOTE(JSON_EXTRACT(o.payload, '$.memberGradeName')) AS member_grade_name,
  ROUND(CAST(JSON_UNQUOTE(JSON_EXTRACT(o.payload, '$.discountAmt')) AS DECIMAL(20,4)) / 10000, 4) AS discount_amount
FROM retail_orders o
WHERE o.tenant_id = '205149'
  AND o.sold_at >= #{startDate}
  AND o.sold_at < DATE_ADD(#{endDate}, INTERVAL 1 DAY)
ORDER BY o.sold_at DESC
LIMIT #{limit}
