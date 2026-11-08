#!/usr/bin/env python3
"""One controlled Kemai collection run.

The only process allowed to touch the Kemai source while the daily supervisor is
paused. It authenticates once, then hands the password to each child through an
anonymous pipe (``KEMAI_PASSWORD_FD``), never through argv, files or logs.

Stages
  phase0    read-only: migrate, table check, mapping verify, integrity, coverage
  batch1    2026-11-07 increment (earliest 2026-11-08 02:00 CST)
  batch2    2026-11-08 increment (earliest 2026-11-09 02:00 CST)
  backfill  bounded historical purchase-detail window (needs explicit --from/--to)
  plan      print the exact command sequence; no password, no database, no source
"""

import argparse
import fcntl
import getpass
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUDITS = ROOT / ".local-audits"
LOGS = ROOT / "kemai_collection_logs"
STATUS = ROOT / "kemai_collection_status.json"
PY = sys.executable

KINDS = ("purchase-orders", "purchase-receipts", "purchase-returns",
         "retail-orders", "retail-sales", "purchase-receive-history")
PRODUCT_CHAIN = (
    ("masters", "kemai_masters.py", ["--rate", "0.5"]),
    ("item_incremental", "kemai_product_incremental.py", []),
    ("suppliers", "kemai_derive_suppliers.py", []),
    ("changed_refresh", "kemai_changed_refresh.py", []),
    ("changed_tabs", "kemai_changed_tabs.py", []),
)
# Jobs that must not run when an earlier product job failed.
PRODUCT_DEPENDENTS = {
    "item_incremental": {"suppliers", "changed_refresh", "changed_tabs"},
}
BATCH_DAY = {"batch1": date(2026, 11, 7), "batch2": date(2026, 11, 8)}
THRESHOLD = {"batch1": datetime(2026, 11, 8, 2, 0),
             "batch2": datetime(2026, 11, 9, 2, 0)}


def log(message):
    print(f"{datetime.now().isoformat(timespec='seconds')} {message}", flush=True)


def load_status():
    try:
        return json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return {"stage": None, "jobs": {}, "checkpoints": {}}


def save_status(state):
    temporary = STATUS.with_name(f"{STATUS.name}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(STATUS)


def run_job(state, name, argv, password, timeout=6 * 3600, stdout_path=None):
    """Run one child with a fresh password pipe. Never echoes the password."""
    LOGS.mkdir(exist_ok=True)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, (password + "\n").encode())
    os.close(write_fd)
    env = {**os.environ, "KEMAI_PASSWORD_FD": str(read_fd)}
    # Keep each source attempt for later audits, including retries of the same day.
    batch_job = (name.startswith("commerce:") or name in {item[0] for item in PRODUCT_CHAIN}
                 or name.startswith("retail_order_lines"))
    started = datetime.now()
    day_suffix = (f".{state['target_day']}.{started:%Y%m%dT%H%M%S%f}"
                  if batch_job and state.get("target_day") else "")
    log_path = LOGS / f"{name}{day_suffix}.log"
    record = {"status": "running", "argv": argv, "log": str(log_path),
              "started_at": started.isoformat(timespec="seconds")}
    state["jobs"][name] = record
    save_status(state)
    log(f"start {name}")
    try:
        if stdout_path:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("wb") as err_handle, stdout_path.open("wb") as out_handle:
                code = subprocess.run(argv, cwd=ROOT, env=env, pass_fds=(read_fd,),
                                      stdout=out_handle, stderr=err_handle,
                                      timeout=timeout).returncode
        else:
            with log_path.open("wb") as handle:
                code = subprocess.run(argv, cwd=ROOT, env=env, pass_fds=(read_fd,),
                                      stdout=handle, stderr=subprocess.STDOUT,
                                      timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        code = -1
    finally:
        os.close(read_fd)
    record.update({"status": "success" if code == 0 else "failed", "exit_code": code,
                   "finished_at": datetime.now().isoformat(timespec="seconds"),
                   "seconds": round((datetime.now() - started).total_seconds())})
    save_status(state)
    log(f"{'ok  ' if code == 0 else 'FAIL'} {name} exit={code}")
    return code == 0


def connect(password):
    from kemai_sync import connect as connect_db
    return connect_db(password)


def read_checkpoints(conn):
    """Return {kind: last_complete_day or None} for the six commerce increments."""
    from kemai_sync import TENANT
    result = {}
    with conn.cursor() as cur:
        for kind in KINDS:
            cur.execute("SELECT cursor_json FROM sync_checkpoints WHERE tenant_id=%s AND stream=%s",
                        (TENANT, f"commerce_incremental:{kind}"))
            row = cur.fetchone()
            day = None
            if row:
                cursor = row["cursor_json"]
                if isinstance(cursor, str):
                    cursor = json.loads(cursor)
                if isinstance(cursor, dict):
                    day = cursor.get("last_complete_day")
            result[kind] = day
    return result


def table_exists(conn, name):
    with conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s""", (name,))
        return bool(cur.fetchone()["n"])


def preflight(state, password):
    """Read-only gates the handoff requires before any source request."""
    ok = True
    ok &= run_job(state, "migrate", [PY, "kemai_commerce_sync.py", "migrate"], password)
    conn = connect(password)
    try:
        tables = {name: table_exists(conn, name) for name in
                  ("purchase_document_detail_state", "sync_reconcile_missing")}
        state["tables"] = tables
        state["checkpoints"]["before"] = read_checkpoints(conn)
        save_status(state)
        if not all(tables.values()):
            log(f"FAIL missing tables: {tables}")
            ok = False
    finally:
        conn.close()
    AUDITS.mkdir(mode=0o700, exist_ok=True)
    ok &= run_job(state, "retail_lines_verify",
                  [PY, "kemai_retail_lines_from_flows.py", "--from", "2026-08-01",
                   "--to", "2026-11-06", "--verify-only"], password,
                  stdout_path=AUDITS / "retail-lines-verify.json")
    ok &= run_job(state, "integrity", [PY, "kemai_integrity_report.py", "--strict",
                                       "--summary", "--output", str(AUDITS / "integrity.json")], password)
    ok &= run_job(state, "product_coverage", [PY, "kemai_product_coverage_audit.py", "--strict",
                                              "--summary", "--output",
                                              str(AUDITS / "product-coverage.json")], password)
    return ok


def product_chain(state, password):
    failed = set()
    for name, script, extra in PRODUCT_CHAIN:
        if any(name in dependents for parent, dependents in PRODUCT_DEPENDENTS.items()
               if parent in failed):
            log(f"skip {name}: dependency failed")
            state["jobs"][name] = {"status": "skipped_dependency"}
            save_status(state)
            continue
        if not run_job(state, name, [PY, script, *extra], password):
            failed.add(name)
    return failed


def batch(state, password, stage, wait=True, skip_product=False, skip_commerce=False):
    target = BATCH_DAY[stage]
    threshold = THRESHOLD[stage]
    now = datetime.now()
    if wait and now < threshold:
        wait = (threshold - now).total_seconds()
        log(f"{stage} waits {wait:.0f}s until {threshold}")
        time.sleep(wait)
    state["stage"] = stage
    state["target_day"] = target.isoformat()
    save_status(state)

    conn = connect(password)
    try:
        checkpoints = read_checkpoints(conn)
    finally:
        conn.close()
    state["checkpoints"]["before"] = checkpoints
    save_status(state)

    if skip_product:
        log("skip product chain by request")
    else:
        product_failed = product_chain(state, password)

    streams_ok = {}
    for kind in (() if skip_commerce else KINDS):
        last = checkpoints.get(kind)
        start = (date.fromisoformat(last) + timedelta(days=1)) if last else target
        if start > target:
            log(f"skip {kind}: checkpoint {last} already covers {target}")
            state["jobs"][f"commerce:{kind}"] = {"status": "already_complete",
                                                 "checkpoint": last}
            save_status(state)
            streams_ok[kind] = True
            continue
        streams_ok[kind] = run_job(
            state, f"commerce:{kind}",
            [PY, "kemai_commerce_sync.py", "incremental", "--from", start.isoformat(),
             "--to", target.isoformat(), "--kind", kind, "--rate", "0.5"], password)

    if streams_ok.get("retail-orders") and streams_ok.get("retail-sales"):
        previous = state["checkpoints"]["before"].get("retail-orders") or "2026-11-06"
        line_start = max(date.fromisoformat(previous) + timedelta(days=1),
                         target - timedelta(days=1))
        if line_start > target:
            log(f"skip retail_order_lines: already mapped through {target}")
            state["jobs"]["retail_order_lines"] = {"status": "already_complete"}
            save_status(state)
        else:
            run_job(state, "retail_order_lines",
                    [PY, "kemai_retail_lines_from_flows.py", "--from", line_start.isoformat(),
                     "--to", target.isoformat()], password)
            run_job(state, "retail_order_lines_verify",
                    [PY, "kemai_retail_lines_from_flows.py", "--from", line_start.isoformat(),
                     "--to", target.isoformat(), "--verify-only"], password,
                    stdout_path=AUDITS / f"retail-lines-{target.isoformat()}.json")
    else:
        log("skip retail_order_lines: a retail stream did not succeed")
        state["jobs"]["retail_order_lines"] = {"status": "skipped_dependency"}
        save_status(state)

    audits(state, password, target)
    conn = connect(password)
    try:
        state["checkpoints"]["after"] = read_checkpoints(conn)
    finally:
        conn.close()
    save_status(state)
    return all(streams_ok.values())


def audits(state, password, target):
    AUDITS.mkdir(mode=0o700, exist_ok=True)
    suffix = target.isoformat()
    run_job(state, f"audit_retail_orders_{suffix}",
            [PY, "kemai_retail_daily_audit.py", "--kind", "retail-orders",
             "--include-incremental", "--strict", "--summary",
             "--output", str(AUDITS / f"retail-orders-{suffix}.json")], password)
    run_job(state, f"audit_retail_sales_{suffix}",
            [PY, "kemai_retail_daily_audit.py", "--include-incremental", "--strict",
             "--summary", "--output", str(AUDITS / f"retail-sales-{suffix}.json")], password)
    for kind in ("purchase-orders", "purchase-receipts", "purchase-returns"):
        run_job(state, f"audit_{kind}_{suffix}",
                [PY, "kemai_purchase_daily_audit.py", "--kind", kind,
                 "--include-incremental", "--strict", "--summary",
                 "--output", str(AUDITS / f"{kind}-{suffix}.json")], password)
    run_job(state, f"integrity_{suffix}",
            [PY, "kemai_integrity_report.py", "--strict", "--summary",
             "--output", str(AUDITS / f"integrity-{suffix}.json")], password)
    run_job(state, f"product_coverage_{suffix}",
            [PY, "kemai_product_coverage_audit.py", "--strict", "--summary",
             "--output", str(AUDITS / f"product-coverage-{suffix}.json")], password)


def plan(args):
    """Print the command sequence without touching password, DB or source."""
    lines = ["# stage plan (no password, no database, no source)"]
    lines.append("# phase0")
    lines.append(f"{PY} kemai_commerce_sync.py migrate")
    lines.append(f"{PY} kemai_retail_lines_from_flows.py --from 2026-08-01 --to 2026-11-06 --verify-only")
    lines.append(f"{PY} kemai_integrity_report.py --strict --summary --output .local-audits/integrity.json")
    lines.append(f"{PY} kemai_product_coverage_audit.py --strict --summary --output .local-audits/product-coverage.json")
    for stage in ("batch1", "batch2"):
        target = BATCH_DAY[stage]
        lines.append(f"# {stage}: target {target} (not before {THRESHOLD[stage]})")
        for name, script, extra in PRODUCT_CHAIN:
            lines.append(" ".join([PY, script, *extra]))
        for kind in KINDS:
            lines.append(f"{PY} kemai_commerce_sync.py incremental --from <checkpoint+1> "
                         f"--to {target} --kind {kind} --rate 0.5")
        lines.append(f"{PY} kemai_retail_lines_from_flows.py --from <last_complete+1> --to {target}")
        lines.append(f"{PY} kemai_retail_lines_from_flows.py --from <last_complete+1> --to {target} --verify-only")
    lines.append("# backfill: explicit bounded window, e.g.")
    lines.append(f"{PY} kemai_purchase_detail_backfill.py start --from DATE --to DATE --kind receipt --limit 5 --rate 0.5")
    print("\n".join(lines))
    return 0


def execute_stage(args, password):
    """The caller holds the shared live/batch process lock."""
    state = {"stage": args.stage, "jobs": {}, "checkpoints": {},
             "started_at": datetime.now().isoformat(timespec="seconds"),
             "pid": os.getpid()}
    save_status(state)

    if args.stage == "phase0":
        ok = preflight(state, password)
    elif args.stage in ("batch1", "batch2"):
        ok = preflight(state, password) and batch(
            state, password, args.stage, wait=not args.now,
            skip_product=args.skip_product, skip_commerce=args.skip_commerce)
    else:
        from kemai_sync import TENANT  # noqa: F401  (kept for parity with other stages)
        ok = True
        for kind in (KINDS if args.kind == "all" else (args.kind,)):
            if kind not in ("purchase-orders", "purchase-receipts", "purchase-returns"):
                raise ValueError("backfill only supports purchase kinds")
            ok &= run_job(state, f"backfill:{kind}",
                          [PY, "kemai_purchase_detail_backfill.py", "start",
                           "--from", args.from_day.isoformat(), "--to", args.to_day.isoformat(),
                           "--kind", {"purchase-orders": "order", "purchase-receipts": "receipt",
                                      "purchase-returns": "return"}[kind],
                           "--limit", str(args.limit), "--rate", "0.5"], password)
    state["finished_at"] = datetime.now().isoformat(timespec="seconds")
    state["result"] = "success" if ok else "failed"
    save_status(state)
    log(f"stage {args.stage} finished: {'success' if ok else 'FAILED'}")
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", required=True,
                        choices=("plan", "phase0", "batch1", "batch2", "backfill"))
    parser.add_argument("--skip-product", action="store_true",
                        help="do not run the five product jobs")
    parser.add_argument("--skip-commerce", action="store_true",
                        help="do not run the six commerce streams")
    parser.add_argument("--now", action="store_true",
                        help="ignore the 02:00 source-lag threshold and start immediately")
    parser.add_argument("--detach", action="store_true",
                        help="fork a detached daemon after the password prompt")
    parser.add_argument("--from", dest="from_day", type=date.fromisoformat)
    parser.add_argument("--to", dest="to_day", type=date.fromisoformat)
    parser.add_argument("--kind", choices=("all", *KINDS), default="all")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    if args.stage == "plan":
        return plan(args)
    if args.stage == "backfill" and not (args.from_day and args.to_day):
        parser.error("backfill requires --from and --to")
    AUDITS.mkdir(mode=0o700, exist_ok=True)
    secret_fd = os.environ.pop("KEMAI_PASSWORD_FD", None)
    if secret_fd is not None:
        with os.fdopen(int(secret_fd), "r") as stream:
            password = stream.readline().rstrip("\n")
    else:
        password = getpass.getpass("MySQL password: ")
    if not password:
        parser.error("empty password")

    if args.detach:
        if os.fork() > 0:
            print("detached collection daemon started; "
                  f"follow {LOGS} and {STATUS.name}", flush=True)
            return 0
        os.setsid()
        if os.fork() > 0:
            os._exit(0)
        launch_log = (ROOT / "kemai_collection_launch.log").open("ab")
        os.dup2(launch_log.fileno(), 1)
        os.dup2(launch_log.fileno(), 2)

    lock_fd = os.open(ROOT / ".kemai-live.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another Kemai live or batch collection is running") from error
        return execute_stage(args, password)
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
