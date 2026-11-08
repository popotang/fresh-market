#!/usr/bin/env python3
"""Reconcile a small old-date commerce window without changing daily checkpoints."""

import argparse
import getpass
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

from kemai_commerce_sync import (PURCHASE, ensure_connection_after_fetch, h,
                                 purchase_partial_count, scan_day, sync_day)
from kemai_purchase_detail_backfill import ensure_state_table
from kemai_pipeline.rate_limit import honor_cooldown
from kemai_sync import TENANT, connect, dump, one, sql
from kemai_sync_log import ensure_tables, finish_failure, finish_success, start_window


ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path(__file__).resolve().parent
DAILY_STATUS = ROOT / '.runtime' / 'state' / 'kemai_daily_status.json'
KINDS = (*PURCHASE, "retail-orders", "retail-sales")
MAX_RATE = 0.5
MAX_DAYS = 7


def checkpoint_name(kind, from_day, to_day):
    return f"commerce_reconcile:{kind}:{from_day}..{to_day}"


def checkpoint_day(conn, name):
    row = one(conn, "SELECT cursor_json FROM sync_checkpoints WHERE tenant_id=%s AND stream=%s",
              (TENANT, name))
    if not row:
        return None
    cursor = row["cursor_json"]
    if isinstance(cursor, str):
        cursor = json.loads(cursor)
    last = cursor.get("last_complete_day") if isinstance(cursor, dict) else None
    return date.fromisoformat(last) if last else None


def save_checkpoint(conn, name, day):
    previous = checkpoint_day(conn, name)
    completed = max(previous, day) if previous else day
    sql(conn, """INSERT INTO sync_checkpoints (tenant_id,stream,cursor_json)
        VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE cursor_json=VALUES(cursor_json)""",
        (TENANT, name, dump({"last_complete_day": completed.isoformat()})))


def ensure_missing_table(conn):
    source = (ROOT / 'db' / 'schema_commerce.sql').read_text()
    statement = next(part.strip() for part in source.split(";")
                     if "CREATE TABLE IF NOT EXISTS sync_reconcile_missing" in part)
    sql(conn, statement)
    conn.commit()


def baseline(conn, kind, day):
    if kind in PURCHASE:
        table, key, when = "purchase_documents", "source_document_id", "source_created_at"
        kind_filter, params = "AND document_kind=%s", (PURCHASE[kind],)
        version_expr, detail_expr = "source_version", "0"
    elif kind == "retail-orders":
        table, key, when = "retail_orders", "source_order_id", "sold_at"
        kind_filter, params = "", ()
        version_expr = "NULL"
        detail_expr = ("CASE WHEN JSON_TYPE(JSON_EXTRACT(payload,'$.orderDetails'))='ARRAY' "
                       "OR JSON_TYPE(JSON_EXTRACT(payload,'$.payFlows'))='ARRAY' "
                       "THEN 1 ELSE 0 END")
    else:
        table, key, when = "retail_sale_flows", "source_flow_id", "sold_at"
        kind_filter, params = "", ()
        version_expr, detail_expr = "NULL", "0"
    with conn.cursor() as cur:
        cur.execute(f"""SELECT {key} AS source_key,{version_expr} AS source_version,
              payload_hash,{detail_expr} AS has_detail_payload
            FROM {table} WHERE tenant_id=%s {kind_filter}
              AND {when}>=%s AND {when}<%s""",
            (TENANT, *params, day, day + timedelta(days=1)))
        return {str(row["source_key"]): row for row in cur.fetchall()}


def compare(baseline_rows, found, kind):
    before, after = set(baseline_rows), set(found)
    changed = 0
    unknown = 0
    for key in before & after:
        old, new = baseline_rows[key], found[key]
        if kind in PURCHASE:
            previous, current = old["source_version"], new.get("version")
            if previous is None or current is None:
                unknown += 1
            elif str(previous) != str(current):
                changed += 1
        elif kind == "retail-orders" and old.get("has_detail_payload"):
            unknown += 1
        elif old["payload_hash"] != h(new):
            changed += 1
    return {"new": len(after - before), "changed": changed,
            "change_unknown": unknown, "absent": len(before - after)}, before - after


def record_absent(conn, kind, day, missing):
    # A complete source scan can only flag possible disappearance. Never delete.
    sql(conn, """UPDATE sync_reconcile_missing SET status='resolved',resolved_at=NOW()
        WHERE tenant_id=%s AND stream=%s AND source_day=%s AND status='pending'""",
        (TENANT, kind, day))
    for key in missing:
        sql(conn, """INSERT INTO sync_reconcile_missing
            (tenant_id,stream,source_day,source_key,status)
            VALUES (%s,%s,%s,%s,'pending')
            ON DUPLICATE KEY UPDATE status='pending',last_seen_at=NOW(),resolved_at=NULL""",
            (TENANT, kind, day, key))


def wait_for_daily():
    while True:
        try:
            state = json.loads(DAILY_STATUS.read_text())
        except (OSError, ValueError):
            state = {}
        if state.get("status") != "running":
            return
        print("daily sync running; reconciliation waits", flush=True)
        time.sleep(30)


def run(conn, kind, from_day, to_day, rate, restart=False):
    ensure_missing_table(conn)
    if kind in PURCHASE:
        ensure_state_table(conn)
    ensure_tables(conn)
    name = checkpoint_name(kind, from_day, to_day)
    last = checkpoint_day(conn, name)
    start = from_day if restart or not last else max(from_day, last + timedelta(days=1))
    if start > to_day:
        return {"kind": kind, "days_scanned": 0, "checkpoint": last.isoformat(),
                "note": "window complete; use --restart to scan again"}
    lock = f"kemai_reconcile:{TENANT}:{kind}"
    if one(conn, "SELECT GET_LOCK(%s,0) AS acquired", (lock,))["acquired"] != 1:
        raise RuntimeError(f"another {kind} sync is running")
    conn._kemai_lock_name = lock
    totals = {"kind": kind, "days_scanned": 0, "source_rows": 0,
              "new": 0, "changed": 0, "change_unknown": 0, "absent": 0}
    try:
        for offset in range((to_day - start).days + 1):
            day = start + timedelta(days=offset)
            wait_for_daily()
            honor_cooldown()
            before = baseline(conn, kind, day)
            event_id = start_window(conn, f"reconcile:{kind}", "reconcile", day, day)
            try:
                scanned = scan_day(kind, day, "reconcile", rate, 2, 0)
                found, pages = scanned
                diff, missing = compare(before, found, kind)
                ensure_connection_after_fetch(conn)
                coverage_info = {}
                saved = sync_day(conn, kind, day, "reconcile", rate, 2, 0,
                                 scan_result=(found, pages),coverage_info=coverage_info)
                partial_count = coverage_info.get("partial_documents",0)
                if kind in PURCHASE:
                    partial_count = max(partial_count,purchase_partial_count(conn, kind, day))
                record_absent(conn, kind, day, missing)
                save_checkpoint(conn, name, day)
                note = (f"旧日期 {day}: 新增 {diff['new']}、变化 {diff['changed']}、"
                        f"无法比较版本 {diff['change_unknown']}、源列表未见 {diff['absent']}；"
                        "未见项只标待核")
                if partial_count:
                    note += f"；{partial_count} 张采购单附属数组未返回"
                finish_success(conn, event_id, f"reconcile:{kind}", day, saved,
                               "single_scan_and_capture",
                               "partial_detail" if partial_count else "reconciled_old_day",
                               note, len(found))
                sql(conn, "UPDATE sync_stream_events SET details=%s WHERE id=%s",
                    (dump({**diff, "pages": pages, "checkpoint": name}), event_id))
                conn.commit()
                totals["days_scanned"] += 1
                totals["source_rows"] += len(found)
                for key in diff:
                    totals[key] += diff[key]
                print(dump({"kind": kind, "day": day.isoformat(), "source_rows": len(found), **diff}),
                      flush=True)
            except BaseException as error:
                conn.rollback()
                finish_failure(conn, event_id, f"reconcile:{kind}", error)
                raise
        return totals
    finally:
        try:
            sql(conn, "SELECT RELEASE_LOCK(%s)", (lock,))
        except Exception:
            pass
        conn._kemai_lock_name = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "start"))
    parser.add_argument("--from", dest="from_day", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="to_day", type=date.fromisoformat, required=True)
    parser.add_argument("--kind", choices=KINDS, required=True)
    parser.add_argument("--rate", type=float, default=MAX_RATE)
    parser.add_argument("--restart", action="store_true",
                        help="revisit this window; its independent checkpoint remains monotonic")
    args = parser.parse_args()
    days = (args.to_day - args.from_day).days + 1
    if days < 1 or days > MAX_DAYS or not 0 < args.rate <= MAX_RATE:
        parser.error("date window must be 1-7 days and rate <= 0.5/s")
    secret_fd = os.environ.pop("KEMAI_PASSWORD_FD", None)
    if secret_fd is None:
        password = getpass.getpass("MySQL password: ")
    else:
        with os.fdopen(int(secret_fd), "r") as secret_stream:
            password = secret_stream.readline().rstrip("\n")
    conn = connect(password)
    try:
        name = checkpoint_name(args.kind, args.from_day, args.to_day)
        if args.mode == "plan":
            last = checkpoint_day(conn, name)
            result = {"kind": args.kind, "from": args.from_day.isoformat(),
                      "to": args.to_day.isoformat(), "checkpoint": last.isoformat() if last else None,
                      "restart": args.restart, "source_requests": 0, "db_writes": 0}
        else:
            result = run(conn, args.kind, args.from_day, args.to_day, args.rate, args.restart)
        print(dump(result), flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
