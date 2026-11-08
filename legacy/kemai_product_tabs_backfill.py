#!/usr/bin/env python3
"""Bounded, resumable backfill for the initial product-tab queue.

The initial 14-section queue is far too large to drain in one run (about
206k objects at 0.5 req/s). This tool works one tab class at a time:

    plan    read-only counts, backlog and theoretical ETA (no source, no writes)
    status  plan plus the latest run/event evidence
    start   fetch at most --limit pending objects of exactly one section

Progress is durable in `sync_objects`; the tool adds no second cursor. Each run
is bounded, holds a DB lock, paces through the shared source gate, and stops
cleanly on authentication or rate-limit errors.
"""

import argparse
import fcntl
import getpass
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from kemai_sections import SECTIONS, sync_sections
from kemai_sync import TENANT, connect, dump, one, sql
from kemai_sync_log import ensure_tables, finish_failure, finish_success, start_window

ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path(__file__).resolve().parent
STREAM_PREFIX = "item_tab_"
EVENT_STREAM = "product-tabs-backfill"
MAX_RATE = 0.5
MAX_LIMIT = 200
LOCK = f"kemai_product_tabs:{TENANT}"
LIVE_LOCK = ROOT / '.runtime' / 'locks' / '.kemai-live.lock'


def stream_for(section):
    return STREAM_PREFIX + section


def positive_limit(value):
    number = int(value)
    if not 1 <= number <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"limit must be 1..{MAX_LIMIT}")
    return number


def ensure_tables_ready(conn):
    ensure_tables(conn)


def backlog(conn, sections):
    """Per-section queue state; read-only."""
    result = {}
    with conn.cursor() as cur:
        for section in sections:
            stream = stream_for(section)
            cur.execute("""SELECT status, COUNT(*) AS n FROM sync_objects
                WHERE tenant_id=%s AND stream=%s GROUP BY status""", (TENANT, stream))
            counts = {row["status"]: int(row["n"]) for row in cur.fetchall()}
            cur.execute("""SELECT COUNT(*) AS n FROM sync_objects
                WHERE tenant_id=%s AND stream=%s AND attempts>=3 AND status<>'done'""",
                (TENANT, stream))
            blocked = int(cur.fetchone()["n"])
            done = counts.get("done", 0)
            retryable = sum(counts.get(k, 0) for k in ("pending", "failed")) - blocked
            pending = counts.get("pending", 0)
            failed = counts.get("failed", 0)
            result[section] = {"stream": stream, "done": done,
                               "pending": pending, "failed": failed,
                               "blocked_attempts": blocked,
                               "retryable": max(0, retryable),
                               "remaining": pending + failed,
                               "total": sum(counts.values())}
    return result


def eta_hours(retryable, rate):
    if retryable <= 0:
        return 0.0
    return round(retryable / rate / 3600, 1)


def summarize(conn, sections, rate):
    queues = backlog(conn, sections)
    total_retryable = sum(item["retryable"] for item in queues.values())
    return {"sections": queues, "rate_cap": rate,
            "retryable_total": total_retryable,
            "blocked_total": sum(item["blocked_attempts"] for item in queues.values()),
            "theoretical_hours": eta_hours(total_retryable, rate),
            "note": "theoretical lower bound at the rate cap; real time includes browser start-up, "
                    "database writes and rate-limit cooldowns, so it is not a completion promise"}


def plan(conn, sections, rate):
    result = summarize(conn, sections, rate)
    result.update({"source_requests": 0, "db_writes": 0,
                   "as_of": datetime.now().astimezone().isoformat(timespec="seconds")})
    return result


def status(conn, sections, rate):
    result = plan(conn, sections, rate)
    with conn.cursor() as cur:
        cur.execute("""SELECT id,mode,status,started_at,finished_at,last_error
            FROM sync_runs WHERE tenant_id=%s AND mode='product_tabs_backfill'
            ORDER BY id DESC LIMIT 3""", (TENANT,))
        result["recent_runs"] = cur.fetchall()
        cur.execute("""SELECT stream,status,source_from,source_to,saved_rows,source_rows,
            coverage_level,coverage_note,finished_at,error_message
            FROM sync_stream_events WHERE tenant_id=%s AND stream=%s
            ORDER BY id DESC LIMIT 5""", (TENANT, EVENT_STREAM))
        result["recent_events"] = cur.fetchall()
        cur.execute("""SELECT stream,covered_through,last_success_at,last_attempt_status,last_error
            FROM sync_stream_status WHERE tenant_id=%s AND stream=%s""", (TENANT, EVENT_STREAM))
        result["stream_status"] = cur.fetchone()
    return result


def select_ids(conn, section, limit):
    stream = stream_for(section)
    with conn.cursor() as cur:
        cur.execute("""SELECT source_key FROM sync_objects
            WHERE tenant_id=%s AND stream=%s AND status IN ('pending','failed') AND attempts<3
            ORDER BY source_key LIMIT %s""", (TENANT, stream, limit))
        return [row["source_key"] for row in cur.fetchall()]


@contextmanager
def live_runner_gate():
    """A bounded history backfill must not interleave with the retail live cycle.

    The live runner holds an exclusive flock on the same file; acquiring it here
    makes the two mutually exclusive in both directions.
    """
    fd = os.open(LIVE_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "retail live sync is running; schedule this backfill when live is stopped "
                "or wait for a maintenance window") from error
        yield
    finally:
        os.close(fd)


def start(conn, section, limit, rate):
    with live_runner_gate():
        return _start_locked(conn, section, limit, rate)


def _start_locked(conn, section, limit, rate):
    ensure_tables_ready(conn)
    if one(conn, "SELECT GET_LOCK(%s,0) AS acquired", (LOCK,))["acquired"] != 1:
        raise RuntimeError("another product-tab backfill is already running")
    conn._kemai_lock_name = LOCK
    day = date.today()
    event_id = None
    run_id = None
    try:
        sql(conn, """INSERT INTO sync_runs (tenant_id,mode,status,parameters)
            VALUES (%s,'product_tabs_backfill','running',%s)""",
            (TENANT, dump({"section": section, "limit": limit, "rate": rate})))
        run_id = one(conn, "SELECT LAST_INSERT_ID() AS id")["id"]
        conn.commit()
        ids = select_ids(conn, section, limit)
        if not ids:
            sql(conn, "UPDATE sync_runs SET status='success',finished_at=NOW() WHERE id=%s", (run_id,))
            conn.commit()
            return {"section": section, "requested": 0, "done": 0,
                    "note": "no retryable objects left in this section"}
        event_id = start_window(conn, EVENT_STREAM, "tabs_backfill", day, day)
        started = time.monotonic()
        sync_sections(conn, run_id, rate=rate, batch_size=2,
                      only_ids=ids, session="kemai_probe", sections=[section])
        elapsed = round(time.monotonic() - started, 1)
        with conn.cursor() as cur:
            placeholders = ",".join(["%s"] * len(ids))
            cur.execute(f"""SELECT COUNT(*) AS n FROM sync_objects
                WHERE tenant_id=%s AND stream=%s AND status='done'
                AND source_key IN ({placeholders})""",
                [TENANT, stream_for(section), *ids])
            done = int(cur.fetchone()["n"])
        finish_success(conn, event_id, EVENT_STREAM, day, done,
                       "sync_objects_done_count", "initial_tabs_bounded",
                       f"{section} 有界补采 {done}/{len(ids)}；初始页签队列仍按批续跑", len(ids))
        # This is a historical queue backfill, not proof that the synthetic
        # stream was captured up to today. Keep its date coverage empty.
        sql(conn, """UPDATE sync_stream_status SET covered_through=NULL
            WHERE tenant_id=%s AND stream=%s""", (TENANT, EVENT_STREAM))
        sql(conn, "UPDATE sync_runs SET status='success',finished_at=NOW(),stats=%s WHERE id=%s",
            (dump({"section": section, "requested": len(ids), "done": done,
                   "seconds": elapsed, "rate": rate}), run_id))
        conn.commit()
        remaining = backlog(conn, [section])[section]
        return {"section": section, "requested": len(ids), "done": done,
                "seconds": elapsed,
                "remaining_retryable": remaining["retryable"],
                "blocked_attempts": remaining["blocked_attempts"],
                "next_eta_hours": eta_hours(remaining["retryable"], rate)}
    except BaseException as error:
        conn.rollback()
        if event_id:
            try:
                finish_failure(conn, event_id, EVENT_STREAM, error)
            except Exception:
                pass
        if run_id:
            try:
                sql(conn, "UPDATE sync_runs SET status='failed',finished_at=NOW(),last_error=%s WHERE id=%s",
                    (str(error)[:1000], run_id))
                conn.commit()
            except Exception:
                pass
        raise
    finally:
        try:
            sql(conn, "SELECT RELEASE_LOCK(%s)", (LOCK,))
        except Exception:
            pass
        conn._kemai_lock_name = None


def password_from_terminal():
    secret_fd = os.environ.pop("KEMAI_PASSWORD_FD", None)
    if secret_fd is None:
        return getpass.getpass("MySQL password: ")
    with os.fdopen(int(secret_fd), "r") as stream:
        return stream.readline().rstrip("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("plan", "start", "status"))
    parser.add_argument("--section", required=True, choices=("all", *SECTIONS),
                        help="one tab class for start; all is plan/status only")
    parser.add_argument("--limit", type=positive_limit, default=10,
                        help=f"max objects this run, 1..{MAX_LIMIT} (start only)")
    parser.add_argument("--rate", type=float, default=MAX_RATE)
    args = parser.parse_args()
    if not 0 < args.rate <= MAX_RATE:
        parser.error("rate must be <= 0.5 source requests/second")
    if args.action == "start" and args.section == "all":
        parser.error("start needs exactly one --section so the run stays bounded")
    sections = list(SECTIONS) if args.section == "all" else [args.section]
    password = password_from_terminal()
    conn = connect(password)
    try:
        if args.action == "plan":
            print(dump(plan(conn, sections, args.rate)), flush=True)
        elif args.action == "status":
            print(dump(status(conn, sections, args.rate)), flush=True)
        else:
            print(dump(start(conn, args.section, args.limit, args.rate)), flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
