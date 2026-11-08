#!/usr/bin/env python3
"""Read-only daily checkpoint and stored-count audit for purchase headers."""

import argparse
import getpass
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

from kemai_retail_daily_audit import ROOT, source_evidence, source_log_paths
from kemai_sync import TENANT, connect


# The receipt API was only available for the 90-day window starting Aug 8
# during the initial Nov 5 import. Earlier approved lines are audited via
# the separate history export reconciliation.
KINDS = {
    "purchase-orders": ("order", date(2025, 1, 1)),
    "purchase-receipts": ("receipt", date(2026, 8, 8)),
    "purchase-returns": ("return", date(2026, 1, 1)),
}


def logged_counts(paths, kind, marker):
    pattern = re.compile(rf"{re.escape(kind)} (\d{{4}}-\d{{2}}-\d{{2}}) {marker}=(\d+)")
    counts = {}
    for path in paths:
        for line in path.read_text(errors="replace").splitlines():
            if match := pattern.search(line):
                counts.setdefault(match[1], set()).add(int(match[2]))
    return counts


def audit(conn, paths, kind, include_incremental=False):
    document_kind, baseline = KINDS[kind]
    with conn.cursor() as cur:
        cur.execute("""SELECT cursor_json FROM sync_checkpoints
                       WHERE tenant_id=%s AND stream=%s""", (TENANT, f"commerce_full:{kind}"))
        checkpoint = cur.fetchone()
        if not checkpoint:
            raise RuntimeError(f"{kind} full checkpoint not found")
        last = date.fromisoformat(json.loads(checkpoint["cursor_json"])["last_complete_day"])
        if include_incremental:
            cur.execute("""SELECT cursor_json FROM sync_checkpoints
                           WHERE tenant_id=%s AND stream=%s""", (TENANT, f"commerce_incremental:{kind}"))
            incremental = cur.fetchone()
            if not incremental:
                raise RuntimeError(f"{kind} incremental checkpoint not found")
            last = max(last, date.fromisoformat(json.loads(incremental["cursor_json"])["last_complete_day"]))
        cur.execute("""SELECT DATE(source_created_at) AS day,COUNT(*) AS n
                       FROM purchase_documents WHERE tenant_id=%s AND document_kind=%s
                       AND source_created_at >= %s AND source_created_at < %s
                       GROUP BY DATE(source_created_at)""",
                    (TENANT, document_kind, baseline.isoformat(),
                     (last + timedelta(days=1)).isoformat()))
        stored = {str(row["day"]): int(row["n"]) for row in cur.fetchall()}
        cur.execute("""SELECT DATE(d.source_created_at) AS day,COUNT(*) AS n
                       FROM purchase_documents d JOIN source_capture c
                         ON c.tenant_id=d.tenant_id AND c.stream=%s
                         AND c.source_key=d.source_document_id
                       WHERE d.tenant_id=%s AND d.document_kind=%s
                         AND d.source_created_at >= %s AND d.source_created_at < %s
                       GROUP BY DATE(d.source_created_at)""",
                    (kind, TENANT, document_kind, baseline.isoformat(),
                     (last + timedelta(days=1)).isoformat()))
        database_captures = {str(row["day"]): int(row["n"]) for row in cur.fetchall()}
    completed, exports = source_evidence(paths, kind)
    captured = logged_counts(paths, kind, "verified captured_total")
    scanned = logged_counts(paths, kind, "single_scan_total")
    issues = []
    scan_only = []
    captured_confirmed = 0
    database_capture_confirmed = 0
    day = baseline
    while day <= last:
        key = day.isoformat()
        values = completed.get(key)
        if not values:
            issues.append({"day": key, "reason": "missing_completed_source_window"})
        elif len(values) != 1:
            issues.append({"day": key, "reason": "conflicting_completed_counts",
                           "counts": sorted(values)})
        else:
            saved = next(iter(values))
            if stored.get(key, 0) != saved:
                issues.append({"day": key, "reason": "database_count_mismatch",
                               "source_saved": saved, "database": stored.get(key, 0)})
            if database_captures.get(key, 0) != saved:
                issues.append({"day": key, "reason": "database_capture_count_mismatch",
                               "source_saved": saved, "database_capture": database_captures.get(key, 0)})
            else:
                database_capture_confirmed += 1
            for label, totals in (("captured_total", captured.get(key)),
                                  ("single_scan_total", scanned.get(key)),
                                  ("export_total", exports.get(key))):
                if totals and (len(totals) != 1 or next(iter(totals)) != saved):
                    issues.append({"day": key, "reason": f"{label}_mismatch",
                                   "source_saved": saved, "totals": sorted(totals)})
            if captured.get(key) and captured[key] == {saved}:
                captured_confirmed += 1
            if saved and not exports.get(key):
                scan_only.append(key)
        day += timedelta(days=1)
    return {"stream": kind, "checkpoint_scope": "full_and_incremental" if include_incremental else "full",
            "checkpoint_day": last.isoformat(),
            "audited_from": baseline.isoformat(),
            "completed_source_days": len([d for d in completed
                                          if baseline.isoformat() <= d <= last.isoformat()]),
            "captured_confirmed_days": captured_confirmed,
            "database_capture_confirmed_days": database_capture_confirmed,
            "scan_only_nonempty_days": scan_only,
            "independent_export_total_days": len([d for d in exports
                                                  if baseline.isoformat() <= d <= last.isoformat()]),
            "issues": issues, "source_logs": [str(path) for path in paths]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=tuple(KINDS), required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--include-incremental", action="store_true",
                        help="audit through the latest committed incremental day too")
    parser.add_argument("--output", type=Path, help="write the complete audit as JSON")
    parser.add_argument("--summary", action="store_true", help="print counts without day lists")
    args = parser.parse_args()
    paths = source_log_paths(args.kind)
    if not paths:
        raise SystemExit(f"no {args.kind} source logs found")
    secret_fd = os.environ.pop("KEMAI_PASSWORD_FD", None)
    if secret_fd is None:
        password = getpass.getpass("MySQL password: ")
    else:
        with os.fdopen(int(secret_fd), "r") as stream:
            password = stream.readline().rstrip("\n")
    conn = connect(password)
    try:
        report = audit(conn, paths, args.kind, args.include_incremental)
    finally:
        conn.close()
    if args.output:
        temp = args.output.with_suffix(args.output.suffix + ".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        os.chmod(temp, 0o600)
        temp.replace(args.output)
    display = ({**report, "scan_only_nonempty_days": len(report["scan_only_nonempty_days"]),
                "source_logs": len(report["source_logs"])} if args.summary else report)
    print(json.dumps(display, ensure_ascii=False, indent=2))
    if args.strict and report["issues"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
