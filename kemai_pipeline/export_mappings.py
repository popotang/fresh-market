"""Pure data: Kemai purchase-receive-history XLSX export column mapping.

HISTORY_EXPORT_FIELDS was initially ported verbatim from
legacy/kemai_commerce_sync.py (L475-487); the P3 real-sample verification on
2026-11-09 (sample: 科迈历史收货_2026-11-08 export, 352 rows) proved the live
export differs from the legacy mapping: it carries a leading 序号 column and
places 金额 right after 供应商名称 instead of after 数量. The mapping below is
the live-export truth verified by scripts/verify_kemai_history_export_mapping.py
against that sample; MAPPING_VERSION was bumped v1 -> v2 for the correction.
Semantics unchanged: Chinese header name -> English field name.
The strict parser (xlsx_import.parse_history_xlsx) compares the file header
row against list(HISTORY_EXPORT_FIELDS) in name and order, so insertion
order here is part of the contract.

Stable line keys come from kemai_pipeline.normalize.history_key, which depends
on sheetNo + normalized sequence + (itemCode or itemId). The Kemai history
export always carries 商品编码 (itemCode) and no itemId column, so the required
columns below are the export-side spelling of that dependency; normalization
itself stays in normalize.py and is only referenced, never copied.
"""

HISTORY_EXPORT_FIELDS={
    "序号":"rowNo","单据编号":"sheetNo","供应商编码":"supplierCode","供应商名称":"supplierName",
    "金额":"amt","机构编码":"branchCode","机构名称":"branchName","仓库编码":"storeCode","仓库名称":"storeName",
    "审核时间":"approveTime","商品编码":"itemCode","商品名称":"itemName","国际条码":"itemSubno",
    "规格":"itemSize","采购单位":"unitName","采购规格":"purchaseUnitQty","采购件数":"purchaseQty",
    "明细序号":"sequence","保质天数":"validDays","生产日期":"madeDate","保质日期":"validDate",
    "单价":"price","数量":"qty","税率":"taxRate","税额":"taxAmt","未税金额":"notaxAmt",
    "零售价":"retailPrice","零售金额":"retailAmt","库存单位数量":"basicQty","库存单位":"stockUnitName",
    "采购费用分摊金额":"purchaseFeeShareAmt","费用分摊库存单价":"purchaseFeeShareBasicPrice",
    "行费":"purchaseExtFeeAmt","行费分摊库存单价":"purchaseExtFeeShareBasicPrice",
    "分摊后入库单价":"sharedBasicPrice","分摊后入库金额":"sharedAmt","业务日期":"warehousingDate",
    "单据备注":"sheetMemo","明细备注":"itemMemo",
}

MAPPING_VERSION='history-export-mapping-v2'

# Columns kemai_pipeline.normalize.history_key needs from this export; itemId is
# its documented fallback for itemCode but never appears in the XLSX export.
REQUIRED_KEY_COLUMNS=('sheetNo','sequence','itemCode')
