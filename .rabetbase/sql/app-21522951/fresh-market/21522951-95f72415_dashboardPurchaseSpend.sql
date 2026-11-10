-- @lovrabet.sqlCode: 21522951-95f72415
-- @lovrabet.sqlName: dashboardPurchaseSpend
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql

SELECT
  COALESCE(NULLIF(d.source_supplier_id, ''), '未知供应商') AS supplier_id,
  COALESCE(NULLIF(MAX(s.supplier_name), ''), CONCAT('供应商 ', d.source_supplier_id)) AS supplier_name,
  ROUND(SUM(CASE WHEN d.document_kind = 'order' THEN d.amount ELSE 0 END), 2) AS order_amount,
  ROUND(SUM(CASE WHEN d.document_kind = 'receipt' THEN d.amount ELSE 0 END), 2) AS receipt_amount,
  ROUND(SUM(CASE WHEN d.document_kind = 'return' THEN d.amount ELSE 0 END), 2) AS return_amount,
  ROUND(SUM(CASE WHEN d.document_kind = 'receipt' THEN d.amount
                 WHEN d.document_kind = 'return' THEN -d.amount
                 ELSE 0 END), 2) AS net_amount,
  COUNT(*) AS document_count,
  MAX(DATE_FORMAT(d.source_created_at, '%Y-%m-%d')) AS last_document_date
FROM purchase_documents d
LEFT JOIN suppliers s ON s.source_supplier_id = d.source_supplier_id
WHERE d.source_created_at &gt;= #{startDate}
  AND d.source_created_at &lt; DATE_ADD(#{endDate}, INTERVAL 1 DAY)
  <if test="branchId != null and branchId != ''">
    AND d.source_branch_id = #{branchId, jdbcType=VARCHAR}
  </if>
GROUP BY d.source_supplier_id
ORDER BY net_amount DESC
LIMIT 120
