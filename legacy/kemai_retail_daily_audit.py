#!/usr/bin/env python3
"""Read-only reconciliation of completed retail-flow/order days against source logs."""

import argparse
import getpass
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

from kemai_sync import TENANT, connect


ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path(__file__).resolve().parent
BASELINE = date(2026, 8, 1)
TABLES = {"retail-sales": "retail_sale_flows", "retail-orders": "retail_orders"}


def source_log_paths(kind):
    """Read retained runtime evidence while accepting pre-cleanup test layouts."""
    paths = set(ROOT.glob(f"kemai*{kind}*.log"))
    runtime_logs = ROOT / ".runtime/logs"
    if runtime_logs.exists():
        paths.update(runtime_logs.rglob(f"kemai*{kind}*.log"))
    for collection_logs in (ROOT / "kemai_collection_logs", runtime_logs / "collection"):
        if collection_logs.exists():
            paths.update(collection_logs.rglob(f"commerce:{kind}*.log"))
    return sorted(paths)


def source_evidence(paths, kind):
    prefix = re.escape(kind)
    export = re.compile(rf"{prefix} (\d{{4}}-\d{{2}}-\d{{2}}) verified export_total=(\d+)")
    complete = re.compile(rf"{prefix} (\d{{4}}-\d{{2}}-\d{{2}}) pages=\d+ saved=(\d+)")
    # A resumed stream can skip only the remaining part of an empty month.
    empty_month = re.compile(rf"{prefix} (\d{{4}}-\d{{2}}-\d{{2}})\.\.(\d{{4}}-\d{{2}}-\d{{2}}) empty month")
    completed = {}
    exports = {}
    empty_months = []
    for path in paths:
        for line in path.read_text(errors="replace").splitlines():
            if match := complete.search(line):
                completed.setdefault(match[1], set()).add(int(match[2]))
            if match := export.search(line):
                exports.setdefault(match[1], set()).add(int(match[2]))
            if match := empty_month.search(line):
                empty_months.append((date.fromisoformat(match[1]), date.fromisoformat(match[2])))
    for start, end in empty_months:
        day = start
        while day <= end:
            completed.setdefault(day.isoformat(), set()).add(0)
            day += timedelta(days=1)
    return completed, exports


def audit(conn, paths, kind="retail-sales", include_incremental=False):
    table = TABLES[kind]
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
        cur.execute(f"""SELECT DATE(sold_at) AS day,COUNT(*) AS n FROM {table}
                       WHERE tenant_id=%s AND sold_at >= %s AND sold_at < %s
                       GROUP BY DATE(sold_at)""",
                    (TENANT, BASELINE.isoformat(), (last + timedelta(days=1)).isoformat()))
        stored = {str(row["day"]): int(row["n"]) for row in cur.fetchall()}
    completed, exports = source_evidence(paths, kind)
    issues = []
    scan_only = []
    verified_days = 0
    day = BASELINE
    while day <= last:
        key = day.isoformat()
        saved_values = completed.get(key)
        export_values = exports.get(key)
        if not saved_values:
            issues.append({"day": key, "reason": "missing_completed_source_window"})
        elif len(saved_values) != 1:
            issues.append({"day": key, "reason": "conflicting_completed_counts", "counts": sorted(saved_values)})
        else:
            saved = next(iter(saved_values))
            if stored.get(key, 0) != saved:
                issues.append({"day": key, "reason": "database_count_mismatch",
                               "source_saved": saved, "database": stored.get(key, 0)})
            elif export_values:
                if len(export_values) != 1 or next(iter(export_values)) != saved:
                    issues.append({"day": key, "reason": "export_count_mismatch",
                                   "source_saved": saved, "export_totals": sorted(export_values)})
                else:
                    verified_days += 1
            elif saved:
                scan_only.append(key)
            else:
                verified_days += 1
        day += timedelta(days=1)
    return {"stream": kind, "checkpoint_scope": "full_and_incremental" if include_incremental else "full",
            "checkpoint_day": last.isoformat(),
            "completed_source_days": len([d for d in completed if d <= last.isoformat()]),
            "export_verified_or_empty_days": verified_days,
            "scan_only_days": scan_only, "issues": issues,
            "source_logs": [str(path) for path in paths]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=tuple(TABLES), default="retail-sales")
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
    display = ({**report, "scan_only_days": len(report["scan_only_days"]),
                "source_logs": len(report["source_logs"])} if args.summary else report)
    print(json.dumps(display, ensure_ascii=False, indent=2))
    if args.strict and report["issues"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
