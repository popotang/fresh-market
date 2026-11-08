#!/usr/bin/env python3
"""Plan or run a bounded, resumable historical purchase-detail backfill."""

import argparse
import getpass
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

from kemai_commerce_sync import (PURCHASE, call, capture, ensure_connection_after_fetch,
                                 pause, purchase)
from kemai_pipeline.rate_limit import honor_cooldown, is_rate_limited
from kemai_sync import TENANT, connect, dump, one, sql
from kemai_sync_log import ensure_tables, finish_failure, finish_success, start_window


ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path(__file__).resolve().parent
DAILY_STATUS = ROOT / '.runtime' / 'state' / 'kemai_daily_status.json'
STREAM = "purchase-detail-backfill"
MAX_RATE = 0.5
MAX_ATTEMPTS = 3


def state_table_exists(conn):
    return bool(one(conn, """SELECT COUNT(*) AS n FROM information_schema.TABLES
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='purchase_document_detail_state'""")["n"])


def ensure_state_table(conn):
    source = (ROOT / 'db' / 'schema_commerce.sql').read_text()
    statement = next(part.strip() for part in source.split(";")
                     if "CREATE TABLE IF NOT EXISTS purchase_document_detail_state" in part)
    sql(conn, statement)
    conn.commit()


def rows_for_day(conn, kind, day, has_state=True):
    join = "LEFT JOIN purchase_document_detail_state s ON s.document_id=d.id" if has_state else ""
    state_columns = ("s.status,s.attempts,s.source_version AS detail_version,"
                     "s.attempted_source_version" if has_state else
                     "NULL AS status,0 AS attempts,NULL AS detail_version,"
                     "NULL AS attempted_source_version")
    with conn.cursor() as cur:
        cur.execute(f"""SELECT d.id,d.source_document_id,d.source_version,{state_columns}
            FROM purchase_documents d {join}
            WHERE d.tenant_id=%s AND d.document_kind=%s
              AND d.source_created_at>=%s AND d.source_created_at<%s
            ORDER BY d.id""", (TENANT, kind, day, day + timedelta(days=1)))
        return cur.fetchall()


def needs_detail(row):
    return (row["status"] != "success" or
            (row["source_version"] is not None and
             (row["detail_version"] is None or
              str(row["source_version"]) != str(row["detail_version"]))))


def eligible(row, retry_failed=False):
    if not needs_detail(row):
        return False
    new_source_version = (row["source_version"] is not None and
                          row["attempted_source_version"] is not None and
                          str(row["source_version"]) != str(row["attempted_source_version"]))
    if row["status"] == "partial" and not retry_failed and not new_source_version:
        return False
    return retry_failed or new_source_version or int(row["attempts"] or 0) < MAX_ATTEMPTS


def plan(conn, from_day, to_day, kinds, retry_failed=False):
    has_state = state_table_exists(conn)
    result = {"from": from_day.isoformat(), "to": to_day.isoformat(), "kinds": {},
              "state_table_exists": has_state, "source_requests": 0, "db_writes": 0}
    for kind in kinds:
        counts = {"documents": 0, "complete": 0, "pending": 0,
                  "partial_source_arrays": 0, "blocked_after_retries": 0,
                  "days_with_documents": 0}
        for offset in range((to_day - from_day).days + 1):
            rows = rows_for_day(conn, kind, from_day + timedelta(days=offset), has_state)
            if rows:
                counts["days_with_documents"] += 1
            counts["documents"] += len(rows)
            counts["complete"] += sum(not needs_detail(row) for row in rows)
            counts["pending"] += sum(eligible(row, retry_failed) for row in rows)
            counts["partial_source_arrays"] += sum(row["status"] == "partial" for row in rows)
            counts["blocked_after_retries"] += sum(row["status"] != "partial" and
                                                    needs_detail(row) and not eligible(row, retry_failed)
                                                    for row in rows)
        counts["undated_documents"] = one(conn, """SELECT COUNT(*) AS n FROM purchase_documents
            WHERE tenant_id=%s AND document_kind=%s AND source_created_at IS NULL""",
            (TENANT, kind))["n"]
        stream = next(name for name, value in PURCHASE.items() if value == kind)
        counts["capture_without_document"] = one(conn, """SELECT COUNT(*) AS n
            FROM source_capture c LEFT JOIN purchase_documents d
              ON d.tenant_id=c.tenant_id AND d.document_kind=%s
              AND d.source_document_id=c.source_key
            WHERE c.tenant_id=%s AND c.stream=%s AND d.id IS NULL""",
            (kind, TENANT, stream))["n"]
        result["kinds"][kind] = counts
    return result


def wait_for_daily():
    while True:
        try:
            state = json.loads(DAILY_STATUS.read_text())
        except (OSError, ValueError):
            state = {}
        if state.get("status") != "running":
            return
        print("daily sync running; purchase detail backfill waits", flush=True)
        time.sleep(30)


def mark_failure(conn, document_id, source_version, error):
    sql(conn, """INSERT INTO purchase_document_detail_state
        (document_id,status,attempts,attempted_source_version,last_error,attempted_at)
        VALUES (%s,'failed',1,%s,%s,NOW())
        ON DUPLICATE KEY UPDATE status='failed',
        attempts=IF(attempted_source_version IS NOT NULL AND
                    attempted_source_version<>VALUES(attempted_source_version),1,attempts+1),
        attempted_source_version=VALUES(attempted_source_version),
        last_error=VALUES(last_error),attempted_at=NOW()""",
        (document_id, source_version, str(error)[:4000]))
    conn.commit()


def fetch_and_save(conn, kind, row, rate):
    started = time.monotonic()
    try:
        result = call("purchase-detail", "--kind", kind, "--id", row["source_document_id"])
    finally:
        pause(started, rate)
    if len(result) != 1 or not isinstance(result[0], dict):
        raise ValueError("purchase detail did not return exactly one entity")
    entity = result[0]
    if str(entity.get("id")) != str(row["source_document_id"]):
        raise ValueError("purchase detail returned a different document ID")
    ensure_connection_after_fetch(conn)
    missing = purchase(conn, kind, entity)
    capture(conn, f"purchase-detail:{kind}", entity)
    conn.commit()
    return missing


def child_count(conn, table, kind, day):
    return one(conn, f"""SELECT COUNT(*) AS n FROM {table} child
        JOIN purchase_documents d ON d.id=child.document_id
        WHERE d.tenant_id=%s AND d.document_kind=%s
          AND d.source_created_at>=%s AND d.source_created_at<%s""",
        (TENANT, kind, day, day + timedelta(days=1)))["n"]


def verify_day(conn, kind, day):
    with conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS documents,
            SUM(CASE WHEN s.status='success' AND
                (d.source_version IS NULL OR
                 (s.source_version IS NOT NULL AND d.source_version=s.source_version))
                THEN 1 ELSE 0 END) AS complete,
            COALESCE(SUM(CASE WHEN s.status='success' THEN s.line_count ELSE 0 END),0) AS lines,
            COALESCE(SUM(CASE WHEN s.status='success' THEN s.branch_count ELSE 0 END),0) AS branches,
            COALESCE(SUM(CASE WHEN s.status='success' THEN s.approval_count ELSE 0 END),0) AS approvals
            FROM purchase_documents d LEFT JOIN purchase_document_detail_state s ON s.document_id=d.id
            WHERE d.tenant_id=%s AND d.document_kind=%s
              AND d.source_created_at>=%s AND d.source_created_at<%s""",
            (TENANT, kind, day, day + timedelta(days=1)))
        counts = cur.fetchone()
    actual = {
        "lines": child_count(conn, "purchase_document_lines", kind, day),
        "branches": child_count(conn, "purchase_document_branches", kind, day),
        "approvals": child_count(conn, "purchase_document_approvals", kind, day),
    }
    stream = next(name for name, value in PURCHASE.items() if value == kind)
    gap = one(conn, """SELECT source_row_count FROM sync_coverage_gaps
        WHERE tenant_id=%s AND stream=%s AND scope_key=%s AND status='pending'""",
        (TENANT, stream, day.isoformat()))
    complete = (int(counts["complete"] or 0) == counts["documents"] and
                all(int(counts[name]) == actual[name] for name in actual) and
                (gap is None or int(gap["source_row_count"]) == counts["documents"]))
    return {"documents": counts["documents"], "complete": int(counts["complete"] or 0),
            "expected_children": {name: int(counts[name]) for name in actual},
            "actual_children": actual, "gap_count_matches": gap is None or
            int(gap["source_row_count"]) == counts["documents"], "verified": complete}


def finish_partial(conn, event_id, day, result):
    note = (f"{result['complete']}/{result['documents']} 单据详情完成；"
            f"子表行数/历史列表总数尚待核对")
    sql(conn, """UPDATE sync_stream_events SET status='partial',source_rows=%s,
        saved_rows=%s,verification_method='per_document_state_and_child_counts',
        coverage_level='partial',coverage_note=%s,finished_at=NOW()
        WHERE id=%s AND status='running'""",
        (result["documents"], result["complete"], note, event_id))
    sql(conn, """UPDATE sync_stream_status SET last_attempt_status='partial',
        coverage_note=%s WHERE tenant_id=%s AND stream=%s AND last_event_id=%s""",
        (note, TENANT, STREAM, event_id))
    conn.commit()


def run(conn, from_day, to_day, kinds, limit, rate, retry_failed=False):
    ensure_state_table(conn)
    ensure_tables(conn)
    lock = f"kemai_purchase_detail:{TENANT}"
    if one(conn, "SELECT GET_LOCK(%s,0) AS acquired", (lock,))["acquired"] != 1:
        raise RuntimeError("another purchase detail backfill is running")
    conn._kemai_lock_name = lock
    saved = failed = partial = 0
    try:
        for kind in kinds:
            for offset in range((to_day - from_day).days + 1):
                day = from_day + timedelta(days=offset)
                rows = rows_for_day(conn, kind, day)
                candidates = [row for row in rows if eligible(row, retry_failed)]
                if not rows and not candidates:
                    continue
                if saved + failed >= limit:
                    return {"saved": saved, "partial": partial, "failed": failed,
                            "limit_reached": True}
                event_id = start_window(conn, STREAM, "detail_backfill", day, day)
                try:
                    for row in candidates:
                        if saved + failed >= limit:
                            break
                        wait_for_daily()
                        honor_cooldown()
                        wait_for_daily()
                        try:
                            missing = fetch_and_save(conn, kind, row, rate)
                            saved += 1
                            partial += bool(missing)
                        except Exception as error:
                            conn.rollback()
                            ensure_connection_after_fetch(conn)
                            mark_failure(conn, row["id"], row["source_version"], error)
                            failed += 1
                            print(f"purchase detail {kind} {day} document={row['id']} failed: {error}", flush=True)
                            if is_rate_limited(str(error)) or failed >= 20:
                                raise RuntimeError("rate limited or too many detail errors; stop this run") from error
                    result = verify_day(conn, kind, day)
                    if result["verified"]:
                        stream = next(name for name, value in PURCHASE.items() if value == kind)
                        sql(conn, """UPDATE sync_coverage_gaps SET status='resolved',last_seen_at=NOW()
                            WHERE tenant_id=%s AND stream=%s AND scope_key=%s
                              AND status='pending'""", (TENANT, stream, day.isoformat()))
                        finish_success(conn, event_id, STREAM, day, result["complete"],
                                       "per_document_state_and_child_counts", "full_detail",
                                       f"{kind} {day}: {result['complete']} 单据详情和子表行数已核对",
                                       result["documents"])
                        conn.commit()
                    else:
                        finish_partial(conn, event_id, day, result)
                    print(dump({"kind": kind, "day": day.isoformat(), **result}), flush=True)
                except BaseException as error:
                    conn.rollback()
                    finish_failure(conn, event_id, STREAM, error)
                    raise
        return {"saved": saved, "partial": partial, "failed": failed,
                "limit_reached": False}
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
    parser.add_argument("--kind", choices=("all", *PURCHASE.values()), default="all")
    parser.add_argument("--limit", type=int, help="required for start; maximum source documents this run")
    parser.add_argument("--rate", type=float, default=MAX_RATE)
    parser.add_argument("--retry-failed", action="store_true", help="retry documents with >=3 failed attempts")
    args = parser.parse_args()
    if args.from_day > args.to_day or not 0 < args.rate <= MAX_RATE:
        parser.error("invalid date range or rate above 0.5/s")
    if args.mode == "start" and (args.limit is None or args.limit < 1):
        parser.error("start requires --limit >= 1")
    kinds = tuple(PURCHASE.values()) if args.kind == "all" else (args.kind,)
    secret_fd = os.environ.pop("KEMAI_PASSWORD_FD", None)
    if secret_fd is None:
        password = getpass.getpass("MySQL password: ")
    else:
        with os.fdopen(int(secret_fd), "r") as secret_stream:
            password = secret_stream.readline().rstrip("\n")
    conn = connect(password)
    try:
        result = (plan(conn, args.from_day, args.to_day, kinds, args.retry_failed)
                  if args.mode == "plan" else
                  run(conn, args.from_day, args.to_day, kinds, args.limit, args.rate,
                      args.retry_failed))
        print(dump(result), flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
