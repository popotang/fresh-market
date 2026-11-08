#!/usr/bin/env python3
"""Read-only reconciliation of the latest Kemai catalog with stored products."""

import argparse
import getpass
import json
import os
from pathlib import Path

from kemai_sync import TENANT, connect
from kemai_sections import SECTIONS


def count(conn, query, params):
    with conn.cursor() as cur:
        cur.execute(query, params)
        return int(cur.fetchone()["n"])


def audit(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT id,started_at,finished_at,stats FROM sync_runs
                       WHERE tenant_id=%s AND mode='item_incremental'
                         AND status='success' ORDER BY id DESC LIMIT 1""", (TENANT,))
        run = cur.fetchone()
    if not run:
        raise RuntimeError("no successful item_incremental run")
    stats = run["stats"]
    if isinstance(stats, str):
        stats = json.loads(stats)
    source_total = int(stats["source_total"])

    totals = {
        "products": count(conn, "SELECT COUNT(*) AS n FROM products WHERE tenant_id=%s", (TENANT,)),
        "list_captures": count(conn, "SELECT COUNT(*) AS n FROM source_capture WHERE tenant_id=%s AND stream='item_list'", (TENANT,)),
        "profiles": count(conn, """SELECT COUNT(*) AS n FROM product_profile d JOIN products p
                          ON p.id=d.product_id WHERE p.tenant_id=%s""", (TENANT,)),
        "detail_captures": count(conn, "SELECT COUNT(*) AS n FROM source_capture WHERE tenant_id=%s AND stream='item_detail'", (TENANT,)),
        "detail_objects_done": count(conn, """SELECT COUNT(*) AS n FROM sync_objects
                           WHERE tenant_id=%s AND stream='item_detail' AND status='done'""", (TENANT,)),
    }
    missing = {
        "product_without_list_capture": count(conn, """SELECT COUNT(*) AS n FROM products p
            LEFT JOIN source_capture c ON c.tenant_id=p.tenant_id AND c.stream='item_list'
              AND c.source_key=p.source_item_id
            WHERE p.tenant_id=%s AND c.id IS NULL""", (TENANT,)),
        "list_capture_without_product": count(conn, """SELECT COUNT(*) AS n FROM source_capture c
            LEFT JOIN products p ON p.tenant_id=c.tenant_id AND p.source_item_id=c.source_key
            WHERE c.tenant_id=%s AND c.stream='item_list' AND p.id IS NULL""", (TENANT,)),
        "product_without_profile": count(conn, """SELECT COUNT(*) AS n FROM products p
            LEFT JOIN product_profile d ON d.product_id=p.id
            WHERE p.tenant_id=%s AND d.product_id IS NULL""", (TENANT,)),
        "product_without_detail_capture": count(conn, """SELECT COUNT(*) AS n FROM products p
            LEFT JOIN source_capture c ON c.tenant_id=p.tenant_id AND c.stream='item_detail'
              AND c.source_key=p.source_item_id
            WHERE p.tenant_id=%s AND c.id IS NULL""", (TENANT,)),
        "product_without_done_detail_object": count(conn, """SELECT COUNT(*) AS n FROM products p
            LEFT JOIN sync_objects o ON o.tenant_id=p.tenant_id AND o.stream='item_detail'
              AND o.source_key=p.source_item_id AND o.status='done'
            WHERE p.tenant_id=%s AND o.source_key IS NULL""", (TENANT,)),
    }
    with conn.cursor() as cur:
        cur.execute("""SELECT o.stream,o.status,COUNT(*) AS n FROM sync_objects o
                       WHERE o.tenant_id=%s AND o.stream LIKE 'item_tab_%%'
                       GROUP BY o.stream,o.status ORDER BY o.stream,o.status""", (TENANT,))
        tab_status = {}
        for row in cur.fetchall():
            tab_status.setdefault(row["stream"], {})[row["status"]] = int(row["n"])
        cur.execute("""SELECT COUNT(*) AS n FROM source_capture c
                       WHERE c.tenant_id=%s AND c.stream='item_list'
                         AND c.captured_at >= %s""", (TENANT, run["started_at"]))
        changed_products = int(cur.fetchone()["n"])
        cur.execute("""SELECT o.stream,o.status,COUNT(*) AS n FROM sync_objects o
                       JOIN source_capture c ON c.tenant_id=o.tenant_id
                         AND c.stream='item_list' AND c.source_key=o.source_key
                       WHERE o.tenant_id=%s AND o.stream LIKE 'item_tab_%%'
                         AND c.captured_at >= %s
                       GROUP BY o.stream,o.status ORDER BY o.stream,o.status""",
                    (TENANT, run["started_at"]))
        changed_tab_status = {}
        for row in cur.fetchall():
            changed_tab_status.setdefault(row["stream"], {})[row["status"]] = int(row["n"])

    issues = []
    for name, value in totals.items():
        if value != source_total:
            issues.append({"reason": "catalog_count_mismatch", "table": name,
                           "source_total": source_total, "stored": value})
    issues.extend({"reason": name, "count": value} for name, value in missing.items() if value)
    for section in SECTIONS:
        stream = "item_tab_" + section
        status = changed_tab_status.get(stream, {})
        if status.get("done", 0) != changed_products or any(
                value for key, value in status.items() if key != "done"):
            issues.append({"reason": "latest_changed_tabs_incomplete", "stream": stream,
                           "changed_products": changed_products, "status": status})
    pending = sum(counts.get("pending", 0) + counts.get("failed", 0)
                  for counts in tab_status.values())
    return {
        "latest_successful_incremental_run": run["id"],
        "latest_run_started_at": str(run["started_at"]),
        "latest_run_finished_at": str(run["finished_at"]),
        "latest_run_stats": stats,
        "source_total": source_total,
        "totals": totals,
        "missing": missing,
        "latest_changed_products": changed_products,
        "latest_changed_tab_status": changed_tab_status,
        "initial_tab_status": tab_status,
        "initial_tab_pending_or_failed": pending,
        "initial_tab_gap_is_deferred": pending > 0,
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    secret_fd = os.environ.pop("KEMAI_PASSWORD_FD", None)
    if secret_fd is None:
        password = getpass.getpass("MySQL password: ")
    else:
        with os.fdopen(int(secret_fd), "r") as stream:
            password = stream.readline().rstrip("\n")
    conn = connect(password)
    try:
        report = audit(conn)
    finally:
        conn.close()
    if args.output:
        temp = args.output.with_suffix(args.output.suffix + ".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        os.chmod(temp, 0o600)
        temp.replace(args.output)
    display = ({key: value for key, value in report.items()
                if key not in ("latest_changed_tab_status", "initial_tab_status")}
               if args.summary else report)
    print(json.dumps(display, ensure_ascii=False, indent=2))
    if args.strict and report["issues"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
