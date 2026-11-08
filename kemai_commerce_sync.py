#!/usr/bin/env python3
"""Sample, full, and overlapping incremental Kemai commerce sync via OpenCLI."""

import argparse
import getpass
import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import quote, urlsplit, urlunsplit
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from kemai_sync import TENANT, connect, dt, dump, money, one, sql
from kemai_rate_limit import honor_cooldown, is_rate_limited, mark_ready, rate_limit_signal, record_limit
from kemai_sync_log import ensure_tables, start_window, finish_success, finish_failure


ROOT = Path(__file__).resolve().parent
HISTORY_KEY_MIGRATION_MARKER = ROOT / "kemai_history_keys_v2.done"
KINDS = ("purchase-orders", "purchase-receipts", "purchase-returns", "retail-orders", "retail-sales", "purchase-receive-history")
PURCHASE = {"purchase-orders": "order", "purchase-receipts": "receipt", "purchase-returns": "return"}
PURCHASE_DETAIL_FIELDS = ("details", "branchs", "approves")
FIRST_DATES = {
    "purchase-orders": date(2025, 1, 1),
    "purchase-receipts": date(2026, 8, 1),
    "purchase-returns": date(2026, 1, 1),
    "retail-orders": date(2026, 8, 1),
    "retail-sales": date(2026, 8, 1),
    "purchase-receive-history": date(2025, 1, 1),
}


def h(obj):
    return hashlib.sha256(dump(obj).encode()).hexdigest()


def val(obj, key):
    x = obj.get(key)
    return str(x) if x is not None else None


def required_arrays(entity, fields, label):
    """Reject partial detail payloads before any header or child write."""
    if not isinstance(entity, dict):
        raise ValueError(f"{label} detail is not an object")
    result = {}
    for field in fields:
        rows = entity.get(field)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"{label} detail lacks a valid {field} array")
        result[field] = rows
    return result


def purchase_arrays(kind, entity):
    """Only item lines are universal; absent ancillary arrays remain unknown."""
    if kind not in PURCHASE.values():
        raise ValueError(f"unsupported purchase document kind: {kind}")
    arrays = required_arrays(entity, ("details",), f"purchase {kind}")
    for field in PURCHASE_DETAIL_FIELDS[1:]:
        rows = entity.get(field)
        if rows is None:
            arrays[field] = None
        elif isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            arrays[field] = rows
        else:
            raise ValueError(f"purchase {kind} detail has invalid {field} array")
    return arrays


def call(*args):
    rate_hits=0
    for attempt in range(3):
        honor_cooldown()
        try:
            proc = subprocess.run(["opencli", "kemai", *args, "-f", "json"], capture_output=True,
                                  text=True, timeout=120)
            if proc.returncode:
                combined=proc.stdout+proc.stderr
                message=combined[-400:]
                if is_rate_limited(combined):
                    record_limit(" ".join(args[:2]),message)
                    rate_hits+=1
                    if rate_hits >= 2:
                        raise RuntimeError(f"Kemai rate limit repeated for {' '.join(args[:2])}")
                    continue
                if "95天" in message or "401" in message or "403" in message or "订单不存在" in message:
                    raise RuntimeError(f"OpenCLI {' '.join(args[:2])} rejected: {message}")
                raise ValueError(f"OpenCLI {' '.join(args[:2])} failed: {message}")
            result=json.loads(proc.stdout)
            signal=rate_limit_signal(result)
            if signal:
                record_limit(" ".join(args[:2]),signal)
                rate_hits+=1
                if rate_hits >= 2:
                    raise RuntimeError(f"Kemai rate limit repeated for {' '.join(args[:2])}")
                continue
            if not isinstance(result,list):
                raise ValueError("OpenCLI response is not an array")
            mark_ready()
            return result
        except (subprocess.TimeoutExpired,ValueError,json.JSONDecodeError):
            if attempt==2:
                raise
            time.sleep(min(60,5*2**attempt))


def pause(started, rate):
    time.sleep(max(0, 1 / rate - (time.monotonic() - started)))


def ensure_connection_after_fetch(conn):
    """Restore an idle MySQL connection before the next write phase."""
    try:
        conn.ping(reconnect=False)
        return
    except Exception:
        conn.ping(reconnect=True)
    lock_name=getattr(conn,"_kemai_lock_name",None)
    if lock_name:
        acquired=one(conn,"SELECT GET_LOCK(%s,0) AS acquired",(lock_name,))["acquired"]
        if acquired!=1:
            raise RuntimeError(f"cannot reacquire {lock_name} after MySQL reconnect")
    print("MySQL idle connection restored after external fetch",flush=True)


def history_key(obj):
    # Stable across API and Excel sources; internal IDs are absent from exports.
    sequence=obj.get("sequence")
    if sequence not in (None,""):
        try:
            number=Decimal(str(sequence))
            if number.is_finite() and number==number.to_integral_value():
                sequence=str(int(number))
            else:
                sequence=str(sequence).strip()
        except (InvalidOperation,ValueError):
            sequence=str(sequence).strip()
    return h([val(obj,"sheetNo"),sequence,val(obj,"itemCode") or val(obj,"itemId")])


def raw_decimal(value):
    if value in (None,""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def record_gap(conn,stream,scope_key,missing_parts,source_row_count,details=None):
    sql(conn,"""INSERT INTO sync_coverage_gaps
      (tenant_id,stream,scope_key,missing_parts,source_row_count,details,status)
      VALUES (%s,%s,%s,%s,%s,%s,'pending')
      ON DUPLICATE KEY UPDATE missing_parts=VALUES(missing_parts),
      source_row_count=VALUES(source_row_count),details=VALUES(details),
      status='pending',last_seen_at=NOW()""",
      (TENANT,stream,scope_key,dump(missing_parts),source_row_count,dump(details or {})))


def capture(conn, stream, obj, source_key=None):
    key = source_key or val(obj, "id")
    if not key:
        raise ValueError(f"{stream} row missing source id")
    sql(conn, """INSERT INTO source_capture
        (tenant_id,stream,source_key,source_version,source_modified_at,payload_hash,payload)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE source_version=VALUES(source_version),
        source_modified_at=VALUES(source_modified_at),payload_hash=VALUES(payload_hash),
        payload=VALUES(payload),captured_at=NOW()""",
        (TENANT, stream, key, val(obj, "version"), dt(obj, "gmtModified"), h(obj), dump(obj)))


def purchase_header(conn, kind, entity):
    source_id = val(entity, "id")
    if not source_id:
        raise ValueError("purchase entity missing id")
    sql(conn, """INSERT INTO purchase_documents
      (tenant_id,document_kind,source_document_id,sheet_no,sheet_type,source_document_ref_id,
       source_document_ref_no,source_supplier_id,source_branch_id,source_receive_branch_id,
       status_code,source_version,amount,source_created_at,source_modified_at,source_approved_at,
       payload_hash,payload)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON DUPLICATE KEY UPDATE sheet_no=VALUES(sheet_no),sheet_type=VALUES(sheet_type),
       source_document_ref_id=VALUES(source_document_ref_id),source_document_ref_no=VALUES(source_document_ref_no),
       source_supplier_id=VALUES(source_supplier_id),source_branch_id=VALUES(source_branch_id),
       source_receive_branch_id=VALUES(source_receive_branch_id),status_code=VALUES(status_code),
       source_version=VALUES(source_version),amount=VALUES(amount),source_created_at=VALUES(source_created_at),
       source_modified_at=VALUES(source_modified_at),source_approved_at=VALUES(source_approved_at),
       payload_hash=VALUES(payload_hash),payload=VALUES(payload),captured_at=NOW()""",
        (TENANT, kind, source_id, val(entity,"sheetNo"), val(entity,"sheetType"),
         val(entity,"sourceId"),val(entity,"sourceNo"),val(entity,"supplierId"),
         val(entity,"branchId"),val(entity,"receiveBranchId"),val(entity,"status"),
         val(entity,"version"),money(entity,"sumAmt"),dt(entity,"gmtCreate"),
         dt(entity,"gmtModified"),dt(entity,"approveTime"),h(entity),dump(entity)))


def purchase(conn, kind, entity):
    arrays = purchase_arrays(kind, entity)
    missing = tuple(field for field in PURCHASE_DETAIL_FIELDS[1:] if arrays[field] is None)
    purchase_header(conn,kind,entity)
    source_id=val(entity,"id")
    doc_id = one(conn, "SELECT id FROM purchase_documents WHERE tenant_id=%s AND document_kind=%s AND source_document_id=%s",
                 (TENANT,kind,source_id))["id"]
    for field,table in (("details","purchase_document_lines"),
                        ("branchs","purchase_document_branches"),
                        ("approves","purchase_document_approvals")):
        if arrays[field] is not None:
            sql(conn, f"DELETE FROM {table} WHERE document_id=%s", (doc_id,))
    for i, row in enumerate(arrays["details"]):
        sql(conn,"""INSERT INTO purchase_document_lines
            (document_id,source_line_id,source_item_id,source_line_ref_id,quantity,unit_price,amount,tax_amount,payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (doc_id,val(row,"id") or f"ordinal:{i}",val(row,"itemId"),val(row,"sourceDetailId"),
             money(row,"qty"),money(row,"price") or money(row,"purchasePrice"),
             money(row,"amt") or money(row,"amount"),money(row,"taxAmt"),dump(row)))
    for i, row in enumerate(arrays["branchs"] or []):
        sql(conn,"INSERT INTO purchase_document_branches (document_id,source_key,source_branch_id,payload) VALUES (%s,%s,%s,%s)",
            (doc_id,val(row,"id") or val(row,"branchId") or f"ordinal:{i}",val(row,"branchId"),dump(row)))
    for i, row in enumerate(arrays["approves"] or []):
        sql(conn,"""INSERT INTO purchase_document_approvals
            (document_id,source_key,source_approver_id,status_code,approved_at,payload)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (doc_id,val(row,"id") or f"ordinal:{i}",val(row,"approverId"),
             val(row,"status"),dt(row,"approveTime"),dump(row)))
    sql(conn,"""INSERT INTO purchase_document_detail_state
        (document_id,status,attempts,line_count,branch_count,approval_count,
         source_version,attempted_source_version,payload_hash,last_error,attempted_at,completed_at)
        VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
        ON DUPLICATE KEY UPDATE status=VALUES(status),
        attempts=IF(attempted_source_version IS NOT NULL AND
                    attempted_source_version<>VALUES(attempted_source_version),1,attempts+1),
        line_count=VALUES(line_count),branch_count=VALUES(branch_count),
        approval_count=VALUES(approval_count),source_version=VALUES(source_version),
        attempted_source_version=VALUES(attempted_source_version),
        payload_hash=VALUES(payload_hash),last_error=VALUES(last_error),
        attempted_at=NOW(),completed_at=VALUES(completed_at)""",
        (doc_id,"partial" if missing else "success",len(arrays["details"]),
         len(arrays["branchs"]) if arrays["branchs"] is not None else None,
         len(arrays["approves"]) if arrays["approves"] is not None else None,
         val(entity,"version"),val(entity,"version"),h(entity),
         f"source omitted ancillary arrays: {','.join(missing)}" if missing else None,
         datetime.now() if not missing else None))
    return missing


def retail_order_header(conn, entity):
    source_id=val(entity,"id")
    if not source_id:
        raise ValueError("retail order missing id")
    sql(conn,"""INSERT INTO retail_orders
      (tenant_id,source_order_id,receipt_no,source_receipt_no,source_branch_id,source_member_id,
       source_type,channel_code,sale_flag,sale_amount,order_amount,sold_at,source_created_at,payload_hash,payload)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON DUPLICATE KEY UPDATE receipt_no=VALUES(receipt_no),source_receipt_no=VALUES(source_receipt_no),
       source_branch_id=VALUES(source_branch_id),source_member_id=VALUES(source_member_id),
       source_type=VALUES(source_type),channel_code=VALUES(channel_code),sale_flag=VALUES(sale_flag),
       sale_amount=VALUES(sale_amount),order_amount=VALUES(order_amount),sold_at=VALUES(sold_at),
       source_created_at=VALUES(source_created_at),payload_hash=VALUES(payload_hash),
       payload=VALUES(payload),captured_at=NOW()""",
        (TENANT,source_id,val(entity,"receiptNo") or source_id,val(entity,"sourceReceiptNo"),
         val(entity,"branchId"),val(entity,"memberId"),val(entity,"sourceType"),
         val(entity,"channel"),val(entity,"saleFlag"),money(entity,"saleAmt"),
         money(entity,"orderAmt"),dt(entity,"gmtSale"),dt(entity,"gmtCreate"),h(entity),dump(entity)))


def retail_order(conn, entity):
    arrays = required_arrays(entity, ("orderDetails", "payFlows"), "retail order")
    retail_order_header(conn,entity)
    source_id=val(entity,"id")
    order_id=one(conn,"SELECT id FROM retail_orders WHERE tenant_id=%s AND source_order_id=%s",(TENANT,source_id))["id"]
    # Keep sale-flow coverage when a refreshed detail response removes a line.
    # Matching rows fall back to their flow payload; detail-only stale rows go.
    sql(conn,"""UPDATE retail_order_lines l
        JOIN retail_sale_flows f ON f.tenant_id=%s AND f.source_order_id=%s
          AND f.source_flow_id=l.source_line_id
        SET l.source_item_id=f.source_item_id,l.source_line_ref_id=NULL,
            l.quantity=f.quantity,
            l.sale_price=CAST(JSON_UNQUOTE(JSON_EXTRACT(f.payload,'$.salePrice')) AS DECIMAL(24,4))/10000,
            l.sale_amount=f.sale_amount,
            l.tax_amount=CAST(JSON_UNQUOTE(JSON_EXTRACT(f.payload,'$.taxAmt')) AS DECIMAL(24,4))/10000,
            l.payload=f.payload,l.source_kind='sales_flow'
        WHERE l.order_id=%s AND l.source_kind='detail_api'""",
        (TENANT,source_id,order_id))
    sql(conn,"DELETE FROM retail_order_lines WHERE order_id=%s AND source_kind='detail_api'",(order_id,))
    sql(conn,"DELETE FROM retail_payments WHERE order_id=%s",(order_id,))
    for i,row in enumerate(arrays["orderDetails"]):
        sql(conn,"""INSERT INTO retail_order_lines
            (order_id,source_line_id,source_item_id,source_line_ref_id,quantity,sale_price,sale_amount,tax_amount,payload,source_kind)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'detail_api')
            ON DUPLICATE KEY UPDATE source_item_id=VALUES(source_item_id),
            source_line_ref_id=VALUES(source_line_ref_id),quantity=VALUES(quantity),
            sale_price=VALUES(sale_price),sale_amount=VALUES(sale_amount),
            tax_amount=VALUES(tax_amount),payload=VALUES(payload),source_kind='detail_api'""",
            (order_id,val(row,"id") or f"ordinal:{i}",val(row,"itemId"),val(row,"sourceDetailId"),
             money(row,"qty"),money(row,"salePrice"),money(row,"saleAmt"),money(row,"taxAmt"),dump(row)))
    for i,row in enumerate(arrays["payFlows"]):
        sql(conn,"""INSERT INTO retail_payments
            (order_id,source_payment_id,payway_code,payway_name,amount,paid_at,payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (order_id,val(row,"id") or f"ordinal:{i}",val(row,"paywayCode"),val(row,"paywayName"),
             money(row,"amt"),dt(row,"thirdPayTime"),dump(row)))


def retail_flow(conn, row):
    source_id=val(row,"id")
    if not source_id:
        raise ValueError("retail sale flow missing id")
    sql(conn,"""INSERT INTO retail_sale_flows
      (tenant_id,source_flow_id,source_order_id,receipt_no,source_branch_id,source_item_id,
       source_supplier_id,quantity,sale_amount,coupon_share_amount,sold_at,source_created_at,payload_hash,payload)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON DUPLICATE KEY UPDATE source_order_id=VALUES(source_order_id),receipt_no=VALUES(receipt_no),
       source_branch_id=VALUES(source_branch_id),source_item_id=VALUES(source_item_id),
       source_supplier_id=VALUES(source_supplier_id),quantity=VALUES(quantity),sale_amount=VALUES(sale_amount),
       coupon_share_amount=VALUES(coupon_share_amount),sold_at=VALUES(sold_at),
       source_created_at=VALUES(source_created_at),payload_hash=VALUES(payload_hash),
       payload=VALUES(payload),captured_at=NOW()""",
        (TENANT,source_id,val(row,"sheetId"),val(row,"receiptNo"),val(row,"branchId"),
         val(row,"itemId"),val(row,"supplierId"),money(row,"qty"),money(row,"saleAmt"),
         money(row,"couponShareAmt"),dt(row,"gmtSale"),dt(row,"gmtCreate"),h(row),dump(row)))


def retail_flow_batch(conn, rows):
    flow_values=[]
    capture_values=[]
    for row in rows:
        source_id=val(row,"id")
        if not source_id:
            raise ValueError("retail sale flow missing id")
        payload=dump(row)
        payload_hash=h(row)
        flow_values.append((TENANT,source_id,val(row,"sheetId"),val(row,"receiptNo"),val(row,"branchId"),
            val(row,"itemId"),val(row,"supplierId"),money(row,"qty"),money(row,"saleAmt"),
            money(row,"couponShareAmt"),dt(row,"gmtSale"),dt(row,"gmtCreate"),payload_hash,payload))
        capture_values.append((TENANT,"retail-sales",source_id,val(row,"version"),
            dt(row,"gmtModified"),payload_hash,payload))
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO retail_sale_flows
          (tenant_id,source_flow_id,source_order_id,receipt_no,source_branch_id,source_item_id,
           source_supplier_id,quantity,sale_amount,coupon_share_amount,sold_at,source_created_at,payload_hash,payload)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON DUPLICATE KEY UPDATE source_order_id=VALUES(source_order_id),receipt_no=VALUES(receipt_no),
           source_branch_id=VALUES(source_branch_id),source_item_id=VALUES(source_item_id),
           source_supplier_id=VALUES(source_supplier_id),quantity=VALUES(quantity),sale_amount=VALUES(sale_amount),
           coupon_share_amount=VALUES(coupon_share_amount),sold_at=VALUES(sold_at),
           source_created_at=VALUES(source_created_at),payload_hash=VALUES(payload_hash),
           payload=VALUES(payload),captured_at=NOW()""",flow_values)
        cur.executemany("""INSERT INTO source_capture
          (tenant_id,stream,source_key,source_version,source_modified_at,payload_hash,payload)
          VALUES (%s,%s,%s,%s,%s,%s,%s)
          ON DUPLICATE KEY UPDATE source_version=VALUES(source_version),
          source_modified_at=VALUES(source_modified_at),payload_hash=VALUES(payload_hash),
          payload=VALUES(payload),captured_at=NOW()""",capture_values)


def receipt_history(conn, row):
    source_id=val(row,"id")
    if not source_id or not val(row,"sheetNo"):
        raise ValueError("historical receipt row missing id or sheetNo")
    sql(conn,"""INSERT INTO purchase_receipt_history_lines
      (tenant_id,source_document_id,source_line_key,sheet_no,sheet_type,source_supplier_id,source_branch_id,
       source_item_id,quantity,amount,approved_at,business_date,payload_hash,payload)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON DUPLICATE KEY UPDATE sheet_no=VALUES(sheet_no),sheet_type=VALUES(sheet_type),
       source_supplier_id=VALUES(source_supplier_id),source_branch_id=VALUES(source_branch_id),
       source_item_id=VALUES(source_item_id),quantity=VALUES(quantity),amount=VALUES(amount),
       approved_at=VALUES(approved_at),business_date=VALUES(business_date),
       payload_hash=VALUES(payload_hash),payload=VALUES(payload),captured_at=NOW()""",
        (TENANT,source_id,history_key(row),val(row,"sheetNo"),val(row,"sheetType"),val(row,"supplierId"),
         val(row,"branchId"),val(row,"itemId"),money(row,"qty"),money(row,"amt"),
         dt(row,"approveTime"),val(row,"warehousingDate"),h(row),dump(row)))


def migrate(conn):
    source=(ROOT/"schema_commerce.sql").read_text()
    statements=[s.strip() for s in source.split(";") if "CREATE TABLE" in s]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()
    ensure_tables(conn)
    refresh_product_gap(conn)
    print(f"commerce schema ready: {len(statements)} tables",flush=True)


def refresh_product_gap(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT stream,status,COUNT(*) AS n FROM sync_objects
                     WHERE tenant_id=%s AND stream LIKE 'item_tab_%%'
                     GROUP BY stream,status ORDER BY stream,status""",(TENANT,))
        rows=cur.fetchall()
        cur.execute("SELECT COUNT(*) AS n FROM products WHERE tenant_id=%s",(TENANT,))
        product_count=cur.fetchone()["n"]
    details={}
    for row in rows:
        details.setdefault(row["stream"],{})[row["status"]]=row["n"]
    missing=[stream for stream,counts in details.items() if counts.get("pending",0)+counts.get("failed",0)>0]
    if missing:
        record_gap(conn,"product-tabs","initial-full",missing,product_count,
                   {"load_mode":"deferred_after_fast_initial","sections":details})
    else:
        sql(conn,"""UPDATE sync_coverage_gaps SET status='resolved',last_seen_at=NOW()
                  WHERE tenant_id=%s AND stream='product-tabs' AND scope_key='initial-full'""",(TENANT,))
    conn.commit()


def daterange(start,end):
    day=start
    while day<=end:
        yield day
        day+=timedelta(days=1)


def list_window(kind,start,end,page,rows,rate):
    started=time.monotonic()
    result=call(kind,"--from",start.isoformat(),"--to",end.isoformat(),"--page",str(page),"--rows",str(rows))
    pause(started,rate)
    return result


def scan_day(kind,day,mode,rate,sample_limit,max_pages,*,allow_page_overlap=False,scan_stats=None):
    # The list APIs accept 1,000 rows per request. Their UI defaults are much
    # smaller, but 100-row pages multiply browser startup cost substantially.
    size=sample_limit if mode=="sample" else 1000
    found={}
    seen_page={}
    if scan_stats is not None:
        scan_stats["page_overlap_rows"]=0
    page=1
    while True:
        rows=list_window(kind,day,day,page,size,rate)
        if not rows:
            break
        for row in rows:
            source_id=history_key(row) if kind=="purchase-receive-history" else val(row,"id")
            if not source_id:
                raise RuntimeError(f"{kind} {day} page={page} missing id")
            if source_id in found:
                # Live, same-day lists are ordered while new transactions arrive.
                # Offset pages can overlap at their boundary. Keep the later
                # payload for a provisional snapshot, but preserve strict
                # behavior for finalized days and duplicate IDs within a page.
                if not allow_page_overlap or seen_page[source_id]==page:
                    raise RuntimeError(f"{kind} {day} duplicate id on page {page}: {source_id}")
                if scan_stats is not None:
                    scan_stats["page_overlap_rows"]+=1
            found[source_id]=row
            seen_page[source_id]=page
        if mode=="sample" or len(rows)<size or (max_pages and page>=max_pages):
            break
        page+=1
        if page>10000:
            raise RuntimeError(f"{kind} {day} exceeded 10000 pages")
    return found,page


def captured_key_count(conn,stream,keys):
    keys=list(keys)
    total=0
    for start in range(0,len(keys),500):
        chunk=keys[start:start+500]
        placeholders=",".join(["%s"]*len(chunk))
        row=one(conn,f"""SELECT COUNT(*) AS n FROM source_capture
                         WHERE tenant_id=%s AND stream=%s AND source_key IN ({placeholders})""",
                (TENANT,stream,*chunk))
        total+=row["n"]
    return total


HISTORY_EXPORT_FIELDS={
    "单据编号":"sheetNo","供应商编码":"supplierCode","供应商名称":"supplierName",
    "机构编码":"branchCode","机构名称":"branchName","仓库编码":"storeCode","仓库名称":"storeName",
    "审核时间":"approveTime","商品编码":"itemCode","商品名称":"itemName","国际条码":"itemSubno",
    "规格":"itemSize","采购单位":"unitName","采购规格":"purchaseUnitQty","采购件数":"purchaseQty",
    "明细序号":"sequence","保质天数":"validDays","生产日期":"madeDate","保质日期":"validDate",
    "单价":"price","数量":"qty","金额":"amt","税率":"taxRate","税额":"taxAmt","未税金额":"notaxAmt",
    "零售价":"retailPrice","零售金额":"retailAmt","库存单位数量":"basicQty","库存单位":"stockUnitName",
    "采购费用分摊金额":"purchaseFeeShareAmt","费用分摊库存单价":"purchaseFeeShareBasicPrice",
    "行费":"purchaseExtFeeAmt","行费分摊库存单价":"purchaseExtFeeShareBasicPrice",
    "分摊后入库单价":"sharedBasicPrice","分摊后入库金额":"sharedAmt","业务日期":"warehousingDate",
    "单据备注":"sheetMemo","明细备注":"itemMemo",
}


def wait_export(command,start,end,rate):
    started=time.monotonic()
    task=call(command,"--from",start.isoformat(),"--to",end.isoformat())[0]
    pause(started,rate)
    task_id=str(task["id"])
    for _ in range(30):
        if task.get("sourceUrl") and str(task.get("status"))=="2" and task.get("totalRows") is not None:
            return task
        if task.get("failReason") or str(task.get("status"))=="3":
            raise RuntimeError(f"export task failed: {task.get('failReason')}")
        time.sleep(10)
        tasks=call("export-task-list","--rows","50")
        latest=next((row for row in tasks if str(row.get("id"))==task_id),None)
        if latest:
            task={**task,**{key:value for key,value in latest.items() if value is not None}}
    raise RuntimeError(f"export task {task_id} did not provide a completed source URL and row total")


def normalize_history_keys(conn):
    if HISTORY_KEY_MIGRATION_MARKER.exists():
        print("history stable-key normalization already complete",flush=True)
        return

    def batches(values, size=200):
        for offset in range(0,len(values),size):
            yield values[offset:offset+size]

    def update_keys(cur, table, column, values):
        # Both identifiers are fixed at the call sites below. Updating many keys
        # per statement avoids one remote MySQL round trip for every row.
        for offset in range(0,len(values),200):
            chunk=values[offset:offset+200]
            cases=" ".join("WHEN %s THEN %s" for _ in chunk)
            placeholders=",".join("%s" for _ in chunk)
            params=[value for row_id,key in chunk for value in (row_id,key)]
            params.extend(row_id for row_id,_ in chunk)
            cur.execute(f"UPDATE {table} SET {column}=CASE id {cases} END "
                        f"WHERE id IN ({placeholders})",params)
            if (offset+len(chunk))%10000<200 or offset+len(chunk)==len(values):
                print(f"history rekey {table} updated={offset+len(chunk)}/{len(values)}",flush=True)

    def rekey_groups(cur, table, column, groups):
        extras=[]
        changed=[]
        for key,items in groups.items():
            keep,current_key=min(items)
            extras.extend(row_id for row_id,_ in items if row_id!=keep)
            if current_key!=key:
                changed.append((keep,key))
        for chunk in batches(extras):
            placeholders=",".join("%s" for _ in chunk)
            cur.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})",chunk)
        # Deleting duplicate old keys first frees the final unique key. Rows
        # already at the canonical key need no update or temporary key.
        update_keys(cur,table,column,changed)
        print(f"history rekey {table}: unique={len(groups)} duplicates={len(extras)} changed={len(changed)}",flush=True)

    with conn.cursor() as cur:
        cur.execute("SELECT id,source_line_key,payload FROM purchase_receipt_history_lines WHERE tenant_id=%s",(TENANT,))
        rows=cur.fetchall()
    groups={}
    for row in rows:
        payload=row["payload"]
        if isinstance(payload,str):
            payload=json.loads(payload)
        groups.setdefault(history_key(payload),[]).append((row["id"],row["source_line_key"]))
    with conn.cursor() as cur:
        rekey_groups(cur,"purchase_receipt_history_lines","source_line_key",groups)
        cur.execute("""SELECT id,source_key,payload FROM source_capture
                     WHERE tenant_id=%s AND stream='purchase-receive-history'""",(TENANT,))
        captures=cur.fetchall()
    capture_groups={}
    for row in captures:
        payload=row["payload"]
        if isinstance(payload,str):
            payload=json.loads(payload)
        capture_groups.setdefault(history_key(payload),[]).append((row["id"],row["source_key"]))
    with conn.cursor() as cur:
        rekey_groups(cur,"source_capture","source_key",capture_groups)
    conn.commit()
    HISTORY_KEY_MIGRATION_MARKER.write_text(
        json.dumps({"completed_at":datetime.now().astimezone().isoformat(timespec="seconds"),
                    "history_rows_before":len(rows),"history_rows_after":len(groups),
                    "capture_rows_before":len(captures),"capture_rows_after":len(capture_groups),
                    "deduplicated_rows":len(rows)-len(groups)},ensure_ascii=False)+"\n"
    )
    os.chmod(HISTORY_KEY_MIGRATION_MARKER,0o600)
    print(f"history stable-key normalization complete rows={len(rows)} captures={len(captures)}",flush=True)


def encoded_export_url(url):
    if not url.startswith("https://yunfan-store.kemai.com.cn/"):
        raise RuntimeError("unexpected history export URL")
    parts=urlsplit(url)
    # Export names can contain Chinese characters. Keep existing percent escapes
    # and the signed query bytes intact while encoding only non-ASCII characters.
    return urlunsplit((parts.scheme,parts.netloc,quote(parts.path,safe="/%"),
                      quote(parts.query,safe="=&%/?+,:;@!$'()*[]-._~"),parts.fragment))


def history_export_rows(task):
    url=encoded_export_url(str(task.get("sourceUrl") or ""))
    from openpyxl import load_workbook
    with tempfile.TemporaryDirectory(prefix="kemai-history-") as temp_dir:
        path=Path(temp_dir)/"history.xlsx"
        urllib.request.urlretrieve(url,path)
        ws=load_workbook(path,read_only=True,data_only=True).active
        headers=[str(value or "") for value in next(ws.iter_rows(min_row=3,max_row=3,values_only=True))]
        fields=[HISTORY_EXPORT_FIELDS.get(header) for header in headers]
        if "sheetNo" not in fields or "itemCode" not in fields or "sequence" not in fields:
            raise RuntimeError("history export required columns missing")
        rows=[]
        for values in ws.iter_rows(min_row=4,values_only=True):
            row={field:value for field,value in zip(fields,values) if field}
            if not row.get("sheetNo"):
                continue
            row["sheetType"]=str(row["sheetNo"])[:2]
            row["_loadMode"]="excel_fast_initial"
            rows.append(row)
        return rows


def sync_history_export_range(conn,start,end,rate):
    task=wait_export("purchase-receive-history-export-start",start,end,rate)
    rows=history_export_rows(task)
    expected=int(task.get("totalRows") or 0)
    if len(rows)!=expected:
        raise RuntimeError(f"history export {start}..{end} rows={len(rows)} task_total={expected}")
    export_keys=[history_key(row) for row in rows]
    if len(set(export_keys))!=len(export_keys):
        raise RuntimeError(f"history export {start}..{end} contains duplicate stable line keys")
    ensure_connection_after_fetch(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT item_code,source_item_id FROM products WHERE tenant_id=%s",(TENANT,))
        item_map={str(row["item_code"]):str(row["source_item_id"]) for row in cur.fetchall()}
        cur.execute("SELECT supplier_code,source_supplier_id FROM suppliers WHERE tenant_id=%s",(TENANT,))
        supplier_map={str(row["supplier_code"]):str(row["source_supplier_id"]) for row in cur.fetchall() if row["supplier_code"]}
        cur.execute("SELECT branch_code,source_branch_id FROM branches WHERE tenant_id=%s",(TENANT,))
        branch_map={str(row["branch_code"]):str(row["source_branch_id"]) for row in cur.fetchall() if row["branch_code"]}
        cur.execute("SELECT sheet_no,source_document_id FROM purchase_documents WHERE tenant_id=%s AND document_kind='receipt'",(TENANT,))
        document_map={str(row["sheet_no"]):str(row["source_document_id"]) for row in cur.fetchall() if row["sheet_no"]}
    keys=[]
    missing_items=missing_suppliers=missing_branches=placeholder_documents=0
    for offset in range(0,len(rows),1000):
        line_values=[]; capture_values=[]
        for row in rows[offset:offset+1000]:
            sheet=str(row["sheetNo"]); item_code=str(row.get("itemCode") or "")
            supplier_code=str(row.get("supplierCode") or ""); branch_code=str(row.get("branchCode") or "")
            source_item=item_map.get(item_code); source_supplier=supplier_map.get(supplier_code); source_branch=branch_map.get(branch_code)
            source_document=document_map.get(sheet) or sheet
            missing_items+=source_item is None; missing_suppliers+=source_supplier is None
            missing_branches+=source_branch is None; placeholder_documents+=sheet not in document_map
            key=history_key(row); keys.append(key); payload=dump(row); payload_hash=h(row)
            line_values.append((TENANT,source_document,key,sheet,row.get("sheetType"),source_supplier,source_branch,
                source_item,raw_decimal(row.get("qty")),raw_decimal(row.get("amt")),dt(row,"approveTime"),
                val(row,"warehousingDate"),payload_hash,payload))
            capture_values.append((TENANT,"purchase-receive-history",key,payload_hash,payload))
        with conn.cursor() as cur:
            cur.executemany("""INSERT INTO purchase_receipt_history_lines
              (tenant_id,source_document_id,source_line_key,sheet_no,sheet_type,source_supplier_id,source_branch_id,
               source_item_id,quantity,amount,approved_at,business_date,payload_hash,payload)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON DUPLICATE KEY UPDATE source_document_id=VALUES(source_document_id),sheet_no=VALUES(sheet_no),
               sheet_type=VALUES(sheet_type),source_supplier_id=VALUES(source_supplier_id),source_branch_id=VALUES(source_branch_id),
               source_item_id=VALUES(source_item_id),quantity=VALUES(quantity),amount=VALUES(amount),approved_at=VALUES(approved_at),
               business_date=VALUES(business_date),payload_hash=VALUES(payload_hash),payload=VALUES(payload),captured_at=NOW()""",line_values)
            cur.executemany("""INSERT INTO source_capture (tenant_id,stream,source_key,payload_hash,payload)
              VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE payload_hash=VALUES(payload_hash),
              payload=VALUES(payload),captured_at=NOW()""",capture_values)
        conn.commit()
        print(f"purchase-receive-history {start}..{end} excel_saved={min(offset+1000,len(rows))}/{len(rows)}",flush=True)
    stored=captured_key_count(conn,"purchase-receive-history",keys)
    if stored!=len(set(keys)):
        raise RuntimeError(f"history export captured={stored} unique={len(set(keys))}")
    record_gap(conn,"purchase-receive-history",f"{start}..{end}",
               ["internal_source_document_id","full_receipt_header","full_receipt_detail"],len(rows),
               {"load_mode":"excel_fast_initial","placeholder_documents":placeholder_documents,
                "unmapped_items":missing_items,"unmapped_suppliers":missing_suppliers,"unmapped_branches":missing_branches})
    conn.commit()
    print(f"purchase-receive-history {start}..{end} verified export_total={expected}; details deferred",flush=True)
    return len(rows)


def sync_day(conn,kind,day,mode,rate,sample_limit,max_pages,retail_order_details=False,
             scan_result=None,coverage_info=None,verify_export=True):
    found,pages=scan_result if scan_result is not None else scan_day(kind,day,mode,rate,sample_limit,max_pages)
    if mode!="sample" and max_pages==0:
        if kind in ("retail-sales","retail-orders") and verify_export:
            export_command="retail-sales-export-start" if kind=="retail-sales" else "retail-orders-export-start"
            task=wait_export(export_command,day,day,rate)
            expected=int(task["totalRows"])
            if len(found)!=expected:
                raise RuntimeError(f"{kind} {day} single scan count={len(found)} export total={expected}")
            print(f"{kind} {day} verified export_total={expected}",flush=True)
        elif kind!="purchase-receive-history":
            # These list APIs report totalRecord=0 even when rows exist. The
            # traversal's unique-key count is therefore the source-day total;
            # it is reconciled against captured keys after the upserts.
            print(f"{kind} {day} single_scan_total={len(found)}",flush=True)
    ensure_connection_after_fetch(conn)
    if kind=="retail-sales":
        rows=list(found.values())
        for start in range(0,len(rows),1000):
            retail_flow_batch(conn,rows[start:start+1000])
            conn.commit()
            print(f"{kind} {day} saved={min(start+1000,len(rows))}/{len(rows)}",flush=True)
        if mode!="sample" and max_pages==0:
            stored=captured_key_count(conn,kind,found.keys())
            if stored!=len(found):
                raise RuntimeError(f"{kind} {day} captured total={stored} scan total={len(found)}")
            print(f"{kind} {day} verified captured_total={stored}",flush=True)
        print(f"{kind} {day} pages={pages} saved={len(rows)}",flush=True)
        return len(rows)
    # Initial backfill and catch-up use the exported order list as the source
    # of truth. Per-order details are recorded as a coverage gap for a later
    # bounded pass; fetching thousands of details during daily catch-up would
    # keep the whole serial pipeline occupied for hours.
    if kind=="retail-orders" and (mode in ("full","reconcile") or
                                  (mode=="incremental" and not retail_order_details)):
        rows=list(found.values())
        for start in range(0,len(rows),1000):
            chunk=rows[start:start+1000]
            for row in chunk:
                retail_order_header(conn,row)
                capture(conn,kind,row)
            conn.commit()
            print(f"{kind} {day} saved_headers={min(start+1000,len(rows))}/{len(rows)}",flush=True)
        stored=captured_key_count(conn,kind,found.keys())
        if stored!=len(found):
            raise RuntimeError(f"{kind} {day} captured total={stored} scan total={len(found)}")
        if rows or mode!="reconcile":
            record_gap(conn,kind,day.isoformat(),["order_details","payment_flows"],len(rows),
                       {"item_lines_covered_by":"retail-sales",
                        "load_mode":{"full":"fast_initial","reconcile":"reconcile_headers"}.get(mode,"fast_incremental")})
            conn.commit()
        print(f"{kind} {day} verified captured_total={stored}; details deferred",flush=True)
        print(f"{kind} {day} pages={pages} saved={len(rows)}",flush=True)
        return len(rows)
    n=0
    processed_headers=set()
    partial_documents={}
    for row in found.values():
        if kind in PURCHASE:
            if mode=="full":
                purchase_header(conn,PURCHASE[kind],row)
                capture(conn,kind,row)
            else:
                started=time.monotonic()
                detail=call("purchase-detail","--kind",PURCHASE[kind],"--id",val(row,"id"))[0]
                pause(started,rate)
                if not isinstance(detail,dict) or val(detail,"id") != val(row,"id"):
                    raise ValueError(f"{kind} detail returned a different document ID")
                missing=purchase(conn,PURCHASE[kind],detail)
                if missing:
                    partial_documents[val(detail,"id")]=missing
                capture(conn,kind,detail)
        elif kind=="retail-orders":
            started=time.monotonic()
            detail=call("retail-order-detail","--id",val(row,"id"),"--sale-time",dt(row,"gmtSale"))[0]
            pause(started,rate)
            if not isinstance(detail,dict) or val(detail,"id") != val(row,"id"):
                raise ValueError("retail detail returned a different order ID")
            if retail_order_details and not all(isinstance(detail.get(field),list)
                                                for field in ("orderDetails","payFlows")):
                raise RuntimeError(f"retail order {val(row,'id')} detail lacks item/payment arrays")
            retail_order(conn,detail)
            capture(conn,kind,detail)
        elif kind=="purchase-receive-history":
            document_id=val(row,"id")
            if document_id not in processed_headers:
                started=time.monotonic()
                detail=call("purchase-detail","--kind","receipt","--id",document_id)[0]
                pause(started,rate)
                if not isinstance(detail,dict) or val(detail,"id") != document_id:
                    raise ValueError("receipt history detail returned a different document ID")
                purchase(conn,"receipt",detail)
                capture(conn,"purchase-receipts",detail)
                processed_headers.add(document_id)
            receipt_history(conn,row)
            capture(conn,kind,row,history_key(row))
        else:
            retail_flow(conn,row)
            capture(conn,kind,row)
        n+=1
        commit_interval=10 if kind in PURCHASE else 100
        if n%commit_interval==0:
            conn.commit()
            print(f"{kind} {day} saved={n}/{len(found)}",flush=True)
    conn.commit()
    if kind=="retail-orders" and retail_order_details and mode=="incremental" and max_pages==0:
        stored=captured_key_count(conn,kind,found.keys())
        if stored!=len(found):
            raise RuntimeError(f"{kind} {day} captured total={stored} scan total={len(found)}")
        sql(conn,"""UPDATE sync_coverage_gaps SET status='resolved',last_seen_at=NOW()
              WHERE tenant_id=%s AND stream=%s AND scope_key=%s""",
            (TENANT,kind,day.isoformat()))
        conn.commit()
        print(f"{kind} {day} verified captured_total={stored}; details complete",flush=True)
    if kind in PURCHASE and found and mode=="full" and max_pages==0:
        missing=["document_lines","branch_allocations","approval_history"]
        details={"load_mode":"fast_initial"}
        if kind=="purchase-receipts":
            details["item_lines_covered_by"]="purchase-receive-history"
        record_gap(conn,kind,day.isoformat(),missing,len(found),details)
        conn.commit()
    if kind not in ("purchase-receive-history","retail-sales","retail-orders") and mode!="sample" and max_pages==0:
        stored=captured_key_count(conn,kind,found.keys())
        if stored!=len(found):
            raise RuntimeError(f"{kind} {day} captured total={stored} scan total={len(found)}")
        print(f"{kind} {day} verified captured_total={stored}",flush=True)
    if kind in PURCHASE and mode in ("incremental","reconcile") and partial_documents:
        part_names={"branchs":"branch_allocations","approves":"approval_history"}
        missing_parts=sorted({part_names[field] for fields in partial_documents.values() for field in fields})
        record_gap(conn,kind,day.isoformat(),missing_parts,len(found),
                   {"load_mode":"partial_detail","partial_documents":len(partial_documents),
                    "sample_source_ids":list(partial_documents)[:20]})
        conn.commit()
    if coverage_info is not None:
        coverage_info["partial_documents"]=len(partial_documents)
    if kind=="purchase-receive-history" and mode!="sample" and max_pages==0:
        stored=one(conn,"""SELECT COUNT(*) AS n FROM purchase_receipt_history_lines
                         WHERE tenant_id=%s AND DATE(approved_at)=%s""",(TENANT,day.isoformat()))["n"]
        if stored!=len(found):
            raise RuntimeError(f"{kind} {day} db total={stored} source scan total={len(found)}")
        print(f"{kind} {day} verified db_total={stored}",flush=True)
    print(f"{kind} {day} pages={pages} saved={n}",flush=True)
    return n


def month_ranges(start,end):
    day=start
    while day<=end:
        next_month=(day.replace(day=28)+timedelta(days=4)).replace(day=1)
        last=min(end,next_month-timedelta(days=1))
        yield day,last
        day=last+timedelta(days=1)


def purchase_partial_count(conn, kind, day):
    return one(conn,"""SELECT COUNT(*) AS n
        FROM purchase_documents d JOIN purchase_document_detail_state s ON s.document_id=d.id
        WHERE d.tenant_id=%s AND d.document_kind=%s
          AND d.source_created_at>=%s AND d.source_created_at<%s
          AND s.status='partial'""",
        (TENANT,PURCHASE[kind],day,day+timedelta(days=1)))["n"]


def window_coverage(kind, mode, retail_order_details=False, partial_count=0):
    if kind=="retail-orders" and not retail_order_details:
        return "headers_only", "订单头已核对；商品行与支付流水待补"
    if kind in PURCHASE and mode=="full":
        return "headers_only", "单据头已核对；历史商品行、适用机构及审批待补"
    if kind in PURCHASE and partial_count:
        return "partial_detail", f"列表和商品行已核对；{partial_count} 张单据的附属数组未返回，缺口待补"
    if kind=="purchase-receive-history":
        return "approved_rows_only", "已审核商品行；部分附属数组与历史未审核记录未覆盖"
    if kind=="retail-sales":
        return "full_list", "逐商品销售流水已按源总数核对"
    return "full_detail", "本窗口列表及可访问单据详情已核对"


def window_verification(kind, mode, empty=False):
    if empty:
        return "empty_month_scan"
    if kind=="purchase-receive-history" and mode=="full":
        return "excel_total_and_capture"
    if kind in ("retail-orders", "retail-sales"):
        return "export_total_and_capture"
    return "single_scan_and_capture"


def assert_forward_incremental(conn, kinds, start):
    """Incremental windows must not rewind the daily supervisor's checkpoints."""
    for kind in kinds:
        row = one(conn,"SELECT cursor_json FROM sync_checkpoints WHERE tenant_id=%s AND stream=%s",
                  (TENANT,f"commerce_incremental:{kind}"))
        if row:
            cursor = row["cursor_json"]
            if isinstance(cursor,str):
                cursor = json.loads(cursor)
            last = cursor.get("last_complete_day") if isinstance(cursor,dict) else None
            if last and start <= date.fromisoformat(last):
                raise ValueError(f"incremental {kind} starts at {start} before committed {last}; "
                                 "use the independent reconcile command for old dates")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=["migrate","sample","full","incremental"])
    parser.add_argument("--from",dest="from_day",type=date.fromisoformat)
    parser.add_argument("--to",dest="to_day",type=date.fromisoformat,default=date.today())
    parser.add_argument("--kind",choices=["all",*KINDS],default="all")
    parser.add_argument("--sample-limit",type=int,default=2)
    parser.add_argument("--max-pages",type=int,default=0,help="0 means no page cap")
    parser.add_argument("--lookback-days",type=int,default=30)
    parser.add_argument("--restart",action="store_true",help="retired: use independent reconcile for old dates")
    parser.add_argument("--rate",type=float,default=0.5)
    parser.add_argument("--retail-order-details",action="store_true",
                        help="deprecated; use kemai_retail_detail_backfill.py plan/start for bounded detail capture")
    args=parser.parse_args()
    if not 0<args.rate<=0.5 or not 1<=args.sample_limit<=20 or args.lookback_days<1 or args.max_pages<0:
        parser.error("rate must be <=0.5; sample-limit 1-20; lookback-days positive; max-pages >=0")
    if args.mode=="full" and not args.from_day:
        parser.error("full requires --from")
    if args.restart:
        parser.error("--restart is retired: it rewinds full checkpoints; use kemai_commerce_reconcile.py")
    if args.retail_order_details:
        parser.error("--retail-order-details is retired: use kemai_retail_detail_backfill.py plan/start; the old path would rescan list windows and move incremental checkpoints")
    start=args.from_day or (args.to_day if args.mode=="sample" else args.to_day-timedelta(days=args.lookback_days-1))
    if start>args.to_day:
        parser.error("--from later than --to")
    secret_fd=os.environ.pop("KEMAI_PASSWORD_FD",None)
    if secret_fd is None:
        password=getpass.getpass("MySQL password: ")
    else:
        with os.fdopen(int(secret_fd),"r") as secret_stream:
            password=secret_stream.readline().rstrip("\n")
    conn=connect(password)
    try:
        migrate(conn)
        if args.mode=="migrate":
            return
        kinds=KINDS if args.kind=="all" else (args.kind,)
        if args.mode=="incremental":
            assert_forward_incremental(conn,kinds,start)
        if len(kinds)==1:
            lock_name=f"kemai_commerce:{TENANT}:{kinds[0]}"
            acquired=one(conn,"SELECT GET_LOCK(%s,0) AS acquired",(lock_name,))["acquired"]
            if acquired!=1:
                raise RuntimeError(f"another {kinds[0]} sync is already running")
            conn._kemai_lock_name=lock_name
        # A run is intentionally separate from the long-running item job.
        sql(conn,"INSERT INTO sync_runs (tenant_id,mode,status,parameters) VALUES (%s,%s,'running',%s)",
            (TENANT,f"commerce_{args.mode}",dump({"from":start.isoformat(),"to":args.to_day.isoformat(),"kinds":kinds,"rate":args.rate})))
        run_id=one(conn,"SELECT LAST_INSERT_ID() AS id")["id"]
        conn.commit()
        try:
            counts={}
            for kind in kinds:
                counts[kind]=0
                kind_start=max(start,FIRST_DATES[kind]) if args.mode=="full" else start
                kind_end=args.to_day
                recent_boundary=date.today()-timedelta(days=90)
                if kind=="purchase-receipts" and args.mode!="sample":
                    kind_start=max(kind_start,recent_boundary)
                checkpoint_stream=f"commerce_{args.mode}:{kind}"
                if args.mode=="full" and not args.restart and args.max_pages==0:
                    checkpoint_row=one(conn,"SELECT cursor_json FROM sync_checkpoints WHERE tenant_id=%s AND stream=%s",(TENANT,checkpoint_stream))
                    if checkpoint_row:
                        state=json.loads(checkpoint_row["cursor_json"])
                        last=state.get("last_complete_day")
                        if last and date.fromisoformat(last)>=kind_start:
                            kind_start=date.fromisoformat(last)+timedelta(days=1)
                if kind=="purchase-receive-history" and args.mode=="full" and args.max_pages==0:
                    normalize_history_keys(conn)
                for month_start,month_end in month_ranges(kind_start,kind_end):
                    if kind=="purchase-receive-history" and args.mode=="full" and args.max_pages==0:
                        event_id=start_window(conn,kind,args.mode,month_start,month_end,run_id)
                        try:
                            window_rows=sync_history_export_range(conn,month_start,month_end,args.rate)
                            sql(conn,"""INSERT INTO sync_checkpoints (tenant_id,stream,cursor_json,last_run_id)
                                VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE cursor_json=VALUES(cursor_json),
                                last_run_id=VALUES(last_run_id)""",
                                (TENANT,checkpoint_stream,dump({"last_complete_day":month_end.isoformat()}),run_id))
                            coverage,note=window_coverage(kind,args.mode)
                            finish_success(conn,event_id,kind,month_end,window_rows,
                                           window_verification(kind,args.mode),coverage,note)
                            conn.commit()
                            counts[kind]+=window_rows
                        except BaseException as error:
                            conn.rollback()
                            finish_failure(conn,event_id,kind,error)
                            raise
                        continue
                    if args.mode!="sample" and args.max_pages==0 and month_start<month_end:
                        try:
                            first=list_window(kind,month_start,month_end,1,1,args.rate)
                        except BaseException as error:
                            event_id=start_window(conn,kind,args.mode,month_start,month_end,run_id)
                            finish_failure(conn,event_id,kind,error)
                            raise
                        if not first:
                            print(f"{kind} {month_start}..{month_end} empty month",flush=True)
                            event_id=start_window(conn,kind,args.mode,month_start,month_end,run_id)
                            sql(conn,"""INSERT INTO sync_checkpoints (tenant_id,stream,cursor_json,last_run_id)
                                VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE cursor_json=VALUES(cursor_json),
                                last_run_id=VALUES(last_run_id)""",
                                (TENANT,checkpoint_stream,dump({"last_complete_day":month_end.isoformat()}),run_id))
                            coverage,note=window_coverage(kind,args.mode,args.retail_order_details)
                            finish_success(conn,event_id,kind,month_end,0,
                                           window_verification(kind,args.mode,empty=True),coverage,note)
                            conn.commit()
                            continue
                    for day in daterange(month_start,month_end):
                        log_window=args.mode!="sample" and args.max_pages==0
                        event_id=start_window(conn,kind,args.mode,day,day,run_id) if log_window else None
                        try:
                            coverage_info={}
                            window_rows=sync_day(conn,kind,day,args.mode,args.rate,args.sample_limit,
                                                 args.max_pages,args.retail_order_details,
                                                 coverage_info=coverage_info)
                            if log_window:
                                partial_count=coverage_info.get("partial_documents",0)
                                if kind in PURCHASE and args.mode=="incremental":
                                    partial_count=max(partial_count,purchase_partial_count(conn,kind,day))
                                sql(conn,"""INSERT INTO sync_checkpoints (tenant_id,stream,cursor_json,last_run_id)
                                    VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE cursor_json=VALUES(cursor_json),
                                    last_run_id=VALUES(last_run_id)""",
                                    (TENANT,checkpoint_stream,dump({"last_complete_day":day.isoformat()}),run_id))
                                coverage,note=window_coverage(kind,args.mode,args.retail_order_details,
                                                               partial_count)
                                finish_success(conn,event_id,kind,day,window_rows,
                                               window_verification(kind,args.mode),coverage,note)
                                conn.commit()
                            counts[kind]+=window_rows
                        except BaseException as error:
                            conn.rollback()
                            if event_id is not None:
                                finish_failure(conn,event_id,kind,error)
                            raise
            run_status="partial" if args.max_pages else "success"
            sql(conn,"UPDATE sync_runs SET status=%s,stats=%s,finished_at=NOW() WHERE id=%s",(run_status,dump(counts),run_id))
            conn.commit()
            print(dump({"run_id":run_id,"counts":counts}),flush=True)
        except BaseException as error:
            try:
                conn.rollback()
            except Exception:
                try:
                    conn.ping(reconnect=True)
                except Exception:
                    pass
            try:
                sql(conn,"UPDATE sync_runs SET status='failed',last_error=%s,finished_at=NOW() WHERE id=%s",(str(error)[:4000],run_id))
                conn.commit()
            except Exception as log_error:
                print(f"sync run {run_id} failure could not be recorded: {log_error}",flush=True)
            raise
    finally:
        conn.close()


if __name__=="__main__":
    main()
