#!/usr/bin/env python3
"""Bounded retail snapshots every 30/60 minutes, with DB-driven day catch-up.

The source adapters currently filter by *date*, so a live poll is a full
snapshot of the current source day. It never advances the daily checkpoint.
Only a post-02:00 verified pass can finalize a past day.
"""

import argparse
import fcntl
import getpass
import json
import math
import os
import subprocess
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from kemai_commerce_sync import (ensure_connection_after_fetch, scan_day, sync_day)
from kemai_rate_limit import STATE as RATE_STATE, honor_cooldown
from kemai_retail_lines_from_flows import ensure_provenance, materialize, verify
from kemai_sync import TENANT, connect, dump, one, sql
from kemai_sync_log import ensure_tables, finish_failure, finish_success, start_window


ROOT = Path(__file__).resolve().parent
DDL = ROOT / "schema_live_sync.sql"
# Two locks with different lifetimes.
#   SERVICE_LOCK: held by a `supervise` process for its whole life -> one service.
#   WORK_LOCK:    held only around one run_once -> mutually exclusive with the
#                 bounded history backfill, and released between cycles so the
#                 backfill can run while the service is idle.
SERVICE_LOCK = ROOT / ".kemai-live.service.lock"
WORK_LOCK = ROOT / ".kemai-live.lock"
SHANGHAI = ZoneInfo("Asia/Shanghai")
KINDS = ("retail-orders", "retail-sales")
BASELINE = date(2026, 8, 1)
FINALIZE_HOUR = 2
MAX_RATE = 0.5
# Any process that may issue Kemai source requests. Checked against the real
# process table, not against stale status JSON.
SOURCE_SCRIPTS = (
    "kemai_daily_launch.py",
    "kemai_collection_launch.py",
    "kemai_commerce_sync.py",
    "kemai_purchase_detail_backfill.py",
    "kemai_commerce_reconcile.py",
    "kemai_retail_detail_backfill.py",
    "kemai_product_incremental.py",
    "kemai_changed_refresh.py",
    "kemai_changed_tabs.py",
    "kemai_masters.py",
    "kemai_derive_suppliers.py",
    "kemai_retail_lines_from_flows.py",
    "kemai_sync.py",
    "kemai_product_tabs_backfill.py",
    "kemai_launch.py",
    "kemai_followup_launch.py",
    "kemai_catchup_launch.py",
    "kemai_priority_launch.py",
    "kemai_product_export_probe.py",
)


def local_now():
    return datetime.now(SHANGHAI)


def utc_naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def checkpoint_day(conn, stream):
    row = one(conn, "SELECT cursor_json FROM sync_checkpoints WHERE tenant_id=%s AND stream=%s",
              (TENANT, stream))
    if not row or not row["cursor_json"]:
        return None
    cursor = row["cursor_json"]
    if isinstance(cursor, str):
        cursor = json.loads(cursor)
    day = cursor.get("last_complete_day") if isinstance(cursor, dict) else None
    return date.fromisoformat(day) if day else None


def live_cursor(conn, kind):
    return one(conn, """SELECT last_polled_day,observed_through_utc,committed_at_utc,
        last_source_rows,last_saved_rows FROM sync_live_cursors
        WHERE tenant_id=%s AND stream=%s""", (TENANT, kind))


def finalized_until(today, hour=FINALIZE_HOUR):
    return today.date() - timedelta(days=1 if today.hour >= hour else 2)


def pending_days(last_day, cutoff, max_days):
    if max_days < 1:
        raise ValueError("max_days must be positive")
    first = (last_day + timedelta(days=1)) if last_day else BASELINE
    if first > cutoff:
        return []
    return [first + timedelta(days=n) for n in range(min((cutoff - first).days + 1, max_days))]


def ensure_live_table(conn):
    sql(conn, DDL.read_text())
    conn.commit()
    ensure_tables(conn)
    ensure_provenance(conn)


def require_live_table(conn):
    present = one(conn, """SELECT COUNT(*) AS n FROM information_schema.TABLES
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='sync_live_cursors'""")["n"]
    if not present:
        raise RuntimeError("sync_live_cursors is missing; run kemai_live_sync.py migrate first")


def save_live_cursor(conn, kind, source_day, observed_at, source_rows, saved_rows):
    sql(conn, """INSERT INTO sync_live_cursors
        (tenant_id,stream,last_polled_day,observed_through_utc,committed_at_utc,
         last_source_rows,last_saved_rows)
        VALUES (%s,%s,%s,%s,UTC_TIMESTAMP(3),%s,%s)
        ON DUPLICATE KEY UPDATE last_polled_day=VALUES(last_polled_day),
        observed_through_utc=VALUES(observed_through_utc),
        committed_at_utc=UTC_TIMESTAMP(3),last_source_rows=VALUES(last_source_rows),
        last_saved_rows=VALUES(last_saved_rows)""",
        (TENANT, kind, source_day, utc_naive(observed_at), source_rows, saved_rows))


def save_daily_checkpoint(conn, kind, day):
    name = f"commerce_incremental:{kind}"
    old = checkpoint_day(conn, name)
    if old and day != old + timedelta(days=1):
        raise RuntimeError(f"{kind} daily checkpoint is {old}, cannot skip to {day}")
    sql(conn, """INSERT INTO sync_checkpoints (tenant_id,stream,cursor_json)
        VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE cursor_json=VALUES(cursor_json)""",
        (TENANT, name, dump({"last_complete_day": day.isoformat()})))


def _ps_rows():
    try:
        output = subprocess.run(["ps", "-axo", "pid=,ppid=,command="],
                                capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        rows.append((int(parts[0]), int(parts[1]) if parts[1].isdigit() else 0, parts[2]))
    return rows


def _ancestors(rows, pid):
    parent = {child: ppid for child, ppid, _command in rows}
    seen = set()
    while pid in parent and parent[pid] not in seen:
        pid = parent[pid]
        seen.add(pid)
    return seen


def active_source_workers():
    """Real Python workers that could issue Kemai source requests."""
    rows = _ps_rows()
    if not rows:
        return []
    skipped = _ancestors(rows, os.getpid()) | {os.getpid()}
    found = []
    for pid, _ppid, command in rows:
        if pid in skipped:
            continue
        executable = command.split(None, 1)[0].rsplit("/", 1)[-1]
        if "python" not in executable:
            continue  # shells, grep, editors must not be counted
        if "kemai_live_sync.py" in command:
            continue  # another live instance is prevented by the flock below
        if any(script in command for script in SOURCE_SCRIPTS):
            found.append({"pid": pid, "command": command.strip()[:200]})
    return found


def cooldown_state(now=None):
    """Read-only view of the shared 30-minute rate-limit cooldown."""
    try:
        state = json.loads(RATE_STATE.read_text())
    except (OSError, ValueError):
        return {"cooling": False}
    resume = float(state.get("resume_after_epoch") or 0)
    current = (now or local_now()).timestamp()
    if resume > current:
        return {"cooling": True,
                "resume_after": datetime.fromtimestamp(resume, SHANGHAI).isoformat(timespec="seconds"),
                "seconds_remaining": int(resume - current),
                "reason": state.get("message") or state.get("command")}
    return {"cooling": False}


def ensure_old_workers_paused():
    path = ROOT / "kemai_daily_status.json"
    if path.exists():
        state = json.loads(path.read_text())
        if state.get("status") != "paused_by_user":
            raise RuntimeError("daily supervisor must remain paused before live sync starts")
    path = ROOT / "kemai_collection_status.json"
    if path.exists():
        state = json.loads(path.read_text())
        pid = state.get("pid")
        if pid and not state.get("finished_at"):
            try:
                os.kill(int(pid), 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError(f"collection launcher pid={pid} is still running")
    workers = active_source_workers()
    if workers:
        raise RuntimeError(
            "another Kemai source worker is running (status files are not enough): "
            + json.dumps(workers[:3], ensure_ascii=False))


class LockBusy(RuntimeError):
    """A non-blocking lock is held by another process."""


@contextmanager
def _exclusive(path, message):
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LockBusy(message) from error
        yield
    finally:
        os.close(fd)


@contextmanager
def exclusive_runner():
    """Single live service for the whole process lifetime."""
    with _exclusive(SERVICE_LOCK, "another Kemai live sync is already running"):
        yield


@contextmanager
def work_runner():
    """One collection cycle; shared with the bounded history backfill."""
    with _exclusive(WORK_LOCK,
                    "another Kemai source task holds the work lock (live cycle or backfill)"):
        yield


def run_cycle_with_work_lock(conn, now, max_catchup_days, rate):
    """Run one bounded cycle; never raise just because the work lock is busy."""
    try:
        with work_runner():
            return run_once(conn, now, max_catchup_days, rate)
    except LockBusy as error:
        return {"as_of": now.isoformat(timespec="seconds"),
                "skipped_busy": str(error),
                "source_requests_estimate": 0}


@contextmanager
def stream_lock(conn, kind):
    name = f"kemai_commerce:{TENANT}:{kind}"
    if one(conn, "SELECT GET_LOCK(%s,0) AS acquired", (name,))["acquired"] != 1:
        raise RuntimeError(f"another {kind} sync is already running")
    conn._kemai_lock_name = name
    try:
        yield
    finally:
        try:
            sql(conn, "SELECT RELEASE_LOCK(%s)", (name,))
        except Exception:
            # A lost DB connection also releases its advisory locks.
            pass
        finally:
            conn._kemai_lock_name = None


def poll_day(conn, kind, day, observed_at, final=False, rate=MAX_RATE):
    """Scan once, verify, upsert, then commit either a provisional or daily cursor."""
    stream = kind if final else f"live:{kind}"
    with stream_lock(conn, kind):
        event_id = start_window(conn, stream, "live_finalize" if final else "live_snapshot", day, day)
        try:
            honor_cooldown()
            scan_stats = {}
            found, pages = scan_day(kind, day, "incremental", rate, 2, 0,
                                    allow_page_overlap=not final, scan_stats=scan_stats)
            coverage = {}
            saved = sync_day(conn, kind, day, "incremental", rate, 2, 0,
                             scan_result=(found, pages), coverage_info=coverage,
                             verify_export=final)
            if saved != len(found):
                raise RuntimeError(f"{kind} {day} saved={saved} source={len(found)}")
            ensure_connection_after_fetch(conn)
            if final:
                save_daily_checkpoint(conn, kind, day)
                level = "headers_only" if kind == "retail-orders" else "full_list"
                note = "日终源总数与唯一键已核对；订单原详情及支付仍有缺口" if kind == "retail-orders" else "日终源总数与唯一键已核对"
            else:
                save_live_cursor(conn, kind, day, observed_at, len(found), saved)
                level = "provisional"
                note = (f"{day} 当日临时快照；开始于 {observed_at.strftime('%Y-%m-%d %H:%M:%S')} 北京时间；"
                        "源仅按日期筛选，原详情及支付另计" if kind == "retail-orders" else
                        f"{day} 当日临时快照；开始于 {observed_at.strftime('%Y-%m-%d %H:%M:%S')} 北京时间；源仅按日期筛选")
            finish_success(conn, event_id, stream, day, saved,
                           "export_total_and_capture" if final else "single_scan_and_capture",
                           level, note, len(found))
            if not final:
                # covered_through means a finalized source date, not today's snapshot.
                sql(conn, """UPDATE sync_stream_status SET covered_through=NULL
                    WHERE tenant_id=%s AND stream=%s AND last_event_id=%s""",
                    (TENANT, stream, event_id))
            conn.commit()
            return {"kind": kind, "day": day.isoformat(), "rows": saved,
                    "source_unique": len(found), "pages": pages, "final": final,
                    "page_overlap_rows": scan_stats.get("page_overlap_rows", 0),
                    "observed_at": observed_at.isoformat(timespec="seconds")}
        except BaseException as error:
            conn.rollback()
            finish_failure(conn, event_id, stream, error)
            raise


def mapped_through(conn):
    row = one(conn, """SELECT covered_through FROM sync_stream_status
        WHERE tenant_id=%s AND stream='retail-order-lines'""", (TENANT,))
    value = row["covered_through"] if row else None
    return date.fromisoformat(value) if isinstance(value, str) else value


def map_lines(conn, day, final, stats=None):
    stream = "retail-order-lines" if final else "live:retail-order-lines"
    event_id = start_window(conn, stream, "live_finalize" if final else "live_snapshot", day, day)
    try:
        mapping = {}
        count = materialize(conn, day, day, allow_orphans=not final, stats=mapping)
        orphan = mapping.get("unmatched_flows", 0)
        if stats is not None:
            stats.update(mapping)
        note = (f"当日临时映射 {count} 条；另有 {orphan} 条流水尚无订单档案，等待下轮补齐；原详情及支付仍待补"
                if orphan else "销售流水已映射商品行；原订单详情及支付仍待补")
        finish_success(conn, event_id, stream, day, count,
                       "source_flow_id_join_and_total",
                       "flow_mapped" if final else ("partial" if orphan else "provisional"),
                       note, count + orphan)
        if not final:
            sql(conn, """UPDATE sync_stream_status SET covered_through=NULL
                WHERE tenant_id=%s AND stream=%s AND last_event_id=%s""",
                (TENANT, stream, event_id))
        conn.commit()
        return count
    except BaseException as error:
        conn.rollback()
        finish_failure(conn, event_id, stream, error)
        raise


def plan(conn, now, max_catchup_days):
    cutoff = finalized_until(now)
    streams = {}
    for kind in KINDS:
        final_day = checkpoint_day(conn, f"commerce_incremental:{kind}")
        current = live_cursor(conn, kind)
        streams[kind] = {
            "daily_checkpoint": final_day.isoformat() if final_day else None,
            "catchup_days_this_cycle": [d.isoformat() for d in pending_days(final_day, cutoff, max_catchup_days)],
            "pending_finalization_days": max(0, (cutoff - final_day).days) if final_day else None,
            "today_snapshot": now.date().isoformat(),
            "last_observed_through_utc": str(current["observed_through_utc"]) if current and current["observed_through_utc"] else None,
        }
    try:
        workers = active_source_workers()
    except Exception:  # a ps failure must not break the read-only plan
        workers = []
    return {"as_of": now.isoformat(timespec="seconds"), "finalize_through": cutoff.isoformat(),
            "rate_cap": MAX_RATE, "source_requests": 0, "db_writes": 0,
            "cooldown": cooldown_state(now), "active_source_workers": workers,
            "streams": streams}


def run_once(conn, now, max_catchup_days, rate):
    require_live_table(conn)
    ensure_old_workers_paused()
    for kind in KINDS:
        if checkpoint_day(conn, f"commerce_incremental:{kind}") is None:
            raise RuntimeError(f"missing commerce_incremental:{kind} baseline checkpoint")
    cutoff = finalized_until(now)
    outcome = {"as_of": now.isoformat(timespec="seconds"),
               "finalize_through": cutoff.isoformat(),
               "snapshot_day": now.date().isoformat(),
               "source_requests_estimate": 0,
               "snapshots": [], "finalized": [], "mapped": [],
               "pending_finalization_days": {}, "errors": {}, "cooling": None}
    for kind in KINDS:
        last = checkpoint_day(conn, f"commerce_incremental:{kind}")
        outcome["pending_finalization_days"][kind] = (
            max(0, (cutoff - last).days) if last else None)
    cooling = cooldown_state(now)
    if cooling["cooling"]:
        # Do not block this cycle (and never add a second executor) while cooling.
        outcome["cooling"] = cooling
        outcome["skipped"] = "global rate-limit cooldown; no source request issued"
        return outcome
    hot = {}
    # Freshness first; long catch-up is bounded so it cannot starve today's data.
    for kind in KINDS:
        try:
            result = poll_day(conn, kind, now.date(), now, rate=rate)
            hot[kind] = result
            outcome["snapshots"].append(result)
        except Exception as error:
            outcome["errors"][f"live:{kind}"] = str(error)[:500]
    if all(kind in hot for kind in KINDS):
        try:
            mapping_stats = {}
            rows = map_lines(conn, now.date(), False, mapping_stats)
            outcome["mapped"].append({"day": now.date().isoformat(),
                                      "rows": rows, "final": False,
                                      "unmatched_flows": mapping_stats.get("unmatched_flows", 0)})
        except Exception as error:
            outcome["errors"]["live:retail-order-lines"] = str(error)[:500]
    for kind in KINDS:
        last = checkpoint_day(conn, f"commerce_incremental:{kind}")
        for day in pending_days(last, cutoff, max_catchup_days):
            try:
                outcome["finalized"].append(
                    poll_day(conn, kind, day, now, final=True, rate=rate))
            except Exception as error:
                outcome["errors"][f"final:{kind}:{day}"] = str(error)[:500]
                break
    # Mapping has its own durable covered_through. A crash after flow upserts
    # cannot skip the mapping: the next cycle starts after that status date.
    orders = checkpoint_day(conn, "commerce_incremental:retail-orders")
    sales = checkpoint_day(conn, "commerce_incremental:retail-sales")
    end = min(orders, sales) if orders and sales else None
    start = mapped_through(conn)
    if end and start:
        for day in pending_days(start, end, max_catchup_days):
            try:
                outcome["mapped"].append({"day": day.isoformat(),
                                          "rows": map_lines(conn, day, True), "final": True})
            except Exception as error:
                outcome["errors"][f"final:retail-order-lines:{day}"] = str(error)[:500]
                break
    elif end:
        outcome["errors"]["final:retail-order-lines"] = (
            "retail-order-lines baseline is missing; verify and backfill the historical line mapping first")
    # A lower bound: list pages plus one export task per finalized day. The
    # export-task-list polling (up to 30 calls per finalized day) is excluded,
    # so this is an estimate and never a billing-grade request count.
    pages = sum(int(item.get("pages") or 0)
                for item in outcome["snapshots"] + outcome["finalized"])
    export_tasks = sum(1 for item in outcome["finalized"])
    outcome["source_requests_estimate"] = pages + export_tasks
    outcome["estimate_basis"] = ("list_pages + finalized_export_tasks; "
                                 "excludes export-task-list polling")
    return outcome


def once_exit_code(result):
    if result.get("skipped_busy"):
        return 4
    if result.get("errors"):
        return 2
    if result.get("cooling"):
        return 3
    return 0


def next_delay(result, failures, schedule):
    """Seconds to wait before the next cycle; overruns merge, never duplicate."""
    if result.get("cooling"):
        return min(int(result["cooling"]["seconds_remaining"]), schedule)
    if result.get("errors"):
        return min(60 * max(1, failures), 900)
    return schedule


def initial_delay(conn, now, schedule):
    """Honor a recent manual snapshot when starting the detached supervisor."""
    cutoff = finalized_until(now)
    observed = []
    for kind in KINDS:
        if checkpoint_day(conn, f"commerce_incremental:{kind}") != cutoff:
            return 0
        cursor = live_cursor(conn, kind)
        if not cursor or cursor["last_polled_day"] != now.date():
            return 0
        value = cursor["observed_through_utc"]
        if not value:
            return 0
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        observed.append(value)
    elapsed = (utc_naive(now) - min(observed)).total_seconds()
    return max(0, min(schedule, math.ceil(schedule - elapsed)))


def password_from_terminal():
    secret_fd = os.environ.pop("KEMAI_PASSWORD_FD", None)
    if secret_fd is None:
        return getpass.getpass("MySQL password: ")
    with os.fdopen(int(secret_fd), "r") as secret_stream:
        return secret_stream.readline().rstrip("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("migrate", "plan", "once", "supervise"))
    parser.add_argument("--interval-minutes", type=int, choices=(30, 60), default=30)
    parser.add_argument("--max-catchup-days", type=int, default=2)
    parser.add_argument("--rate", type=float, default=MAX_RATE)
    args = parser.parse_args()
    if args.max_catchup_days < 1 or not 0 < args.rate <= MAX_RATE:
        parser.error("max-catchup-days must be positive and rate <= 0.5")
    password = password_from_terminal()
    conn = connect(password)
    try:
        if args.action == "migrate":
            ensure_live_table(conn)
            print(dump({"migration": "ready", "table": "sync_live_cursors"}), flush=True)
            return
        require_live_table(conn)
        if args.action == "plan":
            print(dump(plan(conn, local_now(), args.max_catchup_days)), flush=True)
            return
        if args.action == "once":
            # A manual pass only needs the per-cycle work lock. It must stay
            # possible while a supervisor owns the lifetime service lock.
            result = run_cycle_with_work_lock(conn, local_now(),
                                              args.max_catchup_days, args.rate)
            print(dump(result), flush=True)
            raise SystemExit(once_exit_code(result))
        with exclusive_runner():
            schedule = args.interval_minutes * 60
            wait = initial_delay(conn, local_now(), schedule)
            if wait:
                print(dump({"initial_wait_seconds": wait,
                            "reason": "recent successful live snapshots"}), flush=True)
                time.sleep(wait)
            failures = 0
            while True:
                started = time.monotonic()
                try:
                    conn.ping(reconnect=True)
                    result = run_cycle_with_work_lock(conn, local_now(),
                                                      args.max_catchup_days, args.rate)
                    print(dump(result), flush=True)
                    failures = failures + 1 if result.get("errors") else 0
                    delay = schedule if result.get("skipped_busy") else next_delay(result, failures, schedule)
                except Exception as error:
                    failures += 1
                    delay = next_delay({}, failures, schedule)
                    print(dump({"supervisor_error": str(error)[:500],
                                "next_delay_seconds": delay}), flush=True)
                elapsed = time.monotonic() - started
                remaining = delay - elapsed
                if remaining <= 0:
                    print(dump({"cycle_overrun_seconds": round(-remaining),
                                "note": "next trigger merged; one source executor only"}), flush=True)
                time.sleep(max(0, remaining))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
