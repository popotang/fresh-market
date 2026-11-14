-- @lovrabet.sqlCode: 21522951-adc95c26
-- @lovrabet.sqlName: retailMarketingFlows
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- 只读：从已存 payload(JSON) 暴露营销/会员字段；不修改任何表结构与业务数据。
-- promoteCodes 为促销活动单号（SP…），与零售让利明细 promotionCode 关联。
SELECT
  f.source_flow_id AS flow_id,
  f.receipt_no AS receipt_no,
  f.sold_at AS sold_time,
  f.source_branch_id AS branch_id,
  f.source_item_id AS item_id,
  f.quantity AS quantity,
  f.sale_amount AS sale_amount,
  f.coupon_share_amount AS coupon_share_amount,
  JSON_UNQUOTE(JSON_EXTRACT(f.payload, '$.promoteCodes')) AS promote_code,
  JSON_UNQUOTE(JSON_EXTRACT(f.payload, '$.memberCardNo')) AS member_card_no,
  JSON_UNQUOTE(JSON_EXTRACT(f.payload, '$.memberGradeName')) AS member_grade_name,
  JSON_UNQUOTE(JSON_EXTRACT(f.payload, '$.discountType')) AS discount_type,
  ROUND(CAST(JSON_UNQUOTE(JSON_EXTRACT(f.payload, '$.discountAmt')) AS DECIMAL(20,4)) / 10000, 4) AS discount_amount
FROM retail_sale_flows f
WHERE f.tenant_id = '205149'
  AND f.sold_at >= #{startDate}
  AND f.sold_at < DATE_ADD(#{endDate}, INTERVAL 1 DAY)
ORDER BY f.sold_at DESC
LIMIT #{limit}
