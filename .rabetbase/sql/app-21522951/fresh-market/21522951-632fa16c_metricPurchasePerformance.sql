-- @lovrabet.sqlCode: 21522951-632fa16c
-- @lovrabet.sqlName: metricPurchasePerformance
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- @lovrabet.syncedAt: 2026-11-14T07:29:57.602Z
-- 只读：采购下单、收货、退货和净收货额；不做交期与满足率推断。
SELECT
  <choose>
    <when test="dimension == 'day'">
      DATE_FORMAT(d.source_created_at, '%Y-%m-%d') AS biz_date,
      DATE_FORMAT(d.source_created_at, '%Y-%m-%d') AS dim_key,
      DATE_FORMAT(d.source_created_at, '%Y-%m-%d') AS dim_label,
    </when>
    <when test="dimension == 'branch'">
      d.source_branch_id AS branch_id,
      COALESCE(b.branch_name, '未知门店') AS branch_name,
      d.source_branch_id AS dim_key,
      COALESCE(b.branch_name, '未知门店') AS dim_label,
    </when>
    <otherwise>
      COALESCE(NULLIF(d.source_supplier_id, ''), '未知供应商') AS supplier_id,
      COALESCE(NULLIF(MAX(s.supplier_name), ''), CONCAT('供应商 ', COALESCE(NULLIF(d.source_supplier_id, ''), '未知'))) AS supplier_name,
      COALESCE(NULLIF(d.source_supplier_id, ''), '未知供应商') AS dim_key,
      COALESCE(NULLIF(MAX(s.supplier_name), ''), CONCAT('供应商 ', COALESCE(NULLIF(d.source_supplier_id, ''), '未知'))) AS dim_label,
    </otherwise>
  </choose>
  COUNT(*) AS document_count,
  ROUND(SUM(CASE WHEN d.document_kind = 'order' THEN d.amount ELSE 0 END), 2) AS purchase_order_amount,
  ROUND(SUM(CASE WHEN d.document_kind = 'receipt' THEN d.amount ELSE 0 END), 2) AS receipt_amount,
  ROUND(SUM(CASE WHEN d.document_kind = 'return' THEN d.amount ELSE 0 END), 2) AS purchase_return_amount,
  ROUND(SUM(CASE WHEN d.document_kind = 'receipt' THEN d.amount
                 WHEN d.document_kind = 'return' THEN -d.amount
                 ELSE 0 END), 2) AS net_receipt_amount
FROM purchase_documents d
LEFT JOIN branches b
  ON b.tenant_id = d.tenant_id
 AND b.source_branch_id = d.source_branch_id
LEFT JOIN suppliers s
  ON s.tenant_id = d.tenant_id
 AND s.source_supplier_id = d.source_supplier_id
WHERE d.tenant_id = #{tenantId}
  AND d.source_created_at &gt;= #{startDate}
  AND d.source_created_at &lt; DATE_ADD(#{endDate}, INTERVAL 1 DAY)
  <if test="branchId != null and branchId != ''">
    AND d.source_branch_id = #{branchId, jdbcType=VARCHAR}
  </if>
GROUP BY
  <choose>
    <when test="dimension == 'day'">biz_date, dim_key, dim_label</when>
    <when test="dimension == 'branch'">branch_id, branch_name, dim_key, dim_label</when>
    <otherwise>d.source_supplier_id</otherwise>
  </choose>
ORDER BY dim_key ASC
LIMIT 5000
