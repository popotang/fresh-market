"""Durable per-stream sync events and customer-visible freshness state."""

from pathlib import Path

from kemai_sync import TENANT, sql


ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path(__file__).resolve().parent
STREAM_NAMES = {
    "branches": "分支机构",
    "departments": "部门",
    "categories": "商品类别",
    "brands": "品牌",
    "units": "计量单位",
    "suppliers": "供应商",
    "item_list": "商品清单",
    "item_detail": "商品基础详情",
    "item_tabs_changed": "变化商品页签",
    "purchase-orders": "采购订单",
    "purchase-receipts": "采购收货单",
    "purchase-returns": "采购退货单",
    "purchase-receive-history": "历史已审核收货明细",
    "retail-orders": "零售订单",
    "retail-sales": "销售流水",
    "retail-order-lines": "零售订单商品行（销售流水映射）",
    "live:retail-orders": "零售订单·当日快照",
    "live:retail-sales": "销售流水·当日快照",
    "live:retail-order-lines": "零售商品行·当日映射",
    "retail-payments": "零售支付流水",
}


def ensure_tables(conn):
    source = (ROOT / 'db' / 'schema_sync_observability.sql').read_text()
    statements = [part.strip() for part in source.split(";") if "CREATE TABLE" in part]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()


def start_window(conn, stream, mode, source_from, source_to, run_id=None):
    with conn.cursor() as cur:
        cur.execute("""UPDATE sync_stream_events SET status='interrupted',finished_at=NOW(),
            error_message='前次任务在本次启动前中断；实际中断时间未知'
            WHERE tenant_id=%s AND stream=%s AND status='running'""", (TENANT, stream))
        cur.execute("""INSERT INTO sync_stream_events
            (tenant_id,stream,mode,source_from,source_to,run_id,status)
            VALUES (%s,%s,%s,%s,%s,%s,'running')""",
            (TENANT, stream, mode, source_from, source_to, run_id))
        event_id = cur.lastrowid
        cur.execute("""INSERT INTO sync_stream_status
            (tenant_id,stream,stream_name,last_attempt_status,last_attempt_at,last_event_id)
            VALUES (%s,%s,%s,'running',NOW(),%s)
            ON DUPLICATE KEY UPDATE stream_name=VALUES(stream_name),
            last_attempt_status='running',last_attempt_at=NOW(),last_event_id=VALUES(last_event_id)""",
            (TENANT, stream, STREAM_NAMES.get(stream, stream), event_id))
    conn.commit()
    return event_id


def finish_success(conn, event_id, stream, source_to, rows, verification, coverage, note,
                   source_rows=None):
    """Stage success in the caller's checkpoint transaction; caller commits."""
    if rows < 0 or (source_rows is not None and source_rows < 0):
        raise ValueError("sync counts cannot be negative")
    source_rows = rows if source_rows is None else source_rows
    sql(conn, """UPDATE sync_stream_events SET status='success',source_rows=%s,saved_rows=%s,
        verification_method=%s,coverage_level=%s,coverage_note=%s,finished_at=NOW(),error_message=NULL
        WHERE id=%s AND tenant_id=%s AND stream=%s AND status='running'""",
        (source_rows, rows, verification, coverage, note, event_id, TENANT, stream))
    sql(conn, """UPDATE sync_stream_status SET last_attempt_status='success',
        last_success_at=NOW(),covered_through=IF(covered_through IS NULL,%s,
        GREATEST(covered_through,%s)),last_source_rows=%s,last_saved_rows=%s,
        verification_method=%s,coverage_level=%s,coverage_note=%s,last_error=NULL
        WHERE tenant_id=%s AND stream=%s AND last_event_id=%s""",
        (source_to, source_to, source_rows, rows, verification, coverage, note,
         TENANT, stream, event_id))


def finish_failure(conn, event_id, stream, error):
    """Preserve the last success timestamp when a later attempt fails."""
    message = str(error)[:4000]
    sql(conn, """UPDATE sync_stream_events SET status='failed',finished_at=NOW(),error_message=%s
        WHERE id=%s AND tenant_id=%s AND stream=%s AND status='running'""",
        (message, event_id, TENANT, stream))
    sql(conn, """UPDATE sync_stream_status SET last_attempt_status='failed',last_error=%s
        WHERE tenant_id=%s AND stream=%s AND last_event_id=%s""",
        (message, TENANT, stream, event_id))
    conn.commit()
