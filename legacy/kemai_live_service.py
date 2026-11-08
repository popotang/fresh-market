#!/usr/bin/env python3
"""Supervise the retail live poller as a detached, single-instance service.

`start` authenticates once in the foreground, then launches
`kemai_live_sync.py supervise` in its own session with the password handed over
through an anonymous pipe. `stop`/`status`/`restart` never touch credentials
unless `--db` is requested.

The service is a thin wrapper: scheduling logic lives in kemai_live_sync.py and
durable progress lives in the database. The PID file only answers "is this
process alive"; it is never used as a business checkpoint.
"""

import argparse
import getpass
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path(__file__).resolve().parent
STATE = ROOT / '.runtime' / 'state' / 'kemai_live_service.json'
LOG = ROOT / '.runtime' / 'logs' / 'kemai_live_supervise.log'
PY = sys.executable
DEFAULT_INTERVAL = 30
DEFAULT_MAX_CATCHUP_DAYS = 2
DEFAULT_RATE = 0.5


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    temporary = STATE.with_name(f"{STATE.name}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(STATE)


def is_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def iso_or_none(value):
    return value.isoformat() if value is not None else None


def process_command(pid):
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.strip()


def is_our_supervisor(pid):
    """A reused PID must never be killed just because the number matches.

    The argument list must contain the exact script path we launch and the
    `supervise` subcommand; a loose substring match is not enough.
    """
    command = process_command(pid)
    if not command:
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = command.split()
    script = str(LEGACY / 'kemai_live_sync.py')
    return script in argv and "supervise" in argv


def command(interval, max_catchup_days, rate):
    return [PY, str(LEGACY / 'kemai_live_sync.py'), "supervise",
            "--interval-minutes", str(interval),
            "--max-catchup-days", str(max_catchup_days),
            "--rate", str(rate)]


def preflight():
    """Refuse to start while another worker may issue source requests."""
    import kemai_live_sync as live
    live.ensure_old_workers_paused()
    return live.cooldown_state()


def password_from_terminal():
    secret_fd = os.environ.pop("KEMAI_PASSWORD_FD", None)
    if secret_fd is None:
        return getpass.getpass("MySQL password: ")
    with os.fdopen(int(secret_fd), "r") as stream:
        return stream.readline().rstrip("\n")


def start(args):
    state = read_state()
    if is_alive(state.get("pid")):
        return {"ok": False, "error": "live service is already running", "pid": state["pid"]}, 2
    try:
        cooling = preflight()
    except Exception as error:
        return {"ok": False, "error": f"preflight refused: {error}"}, 3
    password = password_from_terminal()
    if not password:
        return {"ok": False, "error": "empty password"}, 2
    read_fd, write_fd = os.pipe()
    os.write(write_fd, (password + "\n").encode())
    os.close(write_fd)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log_handle = LOG.open("ab")
    argv = command(args.interval_minutes, args.max_catchup_days, args.rate)
    try:
        process = subprocess.Popen(
            argv, cwd=ROOT,
            env={**os.environ, "KEMAI_PASSWORD_FD": str(read_fd)},
            pass_fds=(read_fd,), stdout=log_handle, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception as error:
        os.close(read_fd)
        return {"ok": False, "error": f"failed to launch: {error}"}, 2
    finally:
        log_handle.close()
    os.close(read_fd)
    time.sleep(1.0)
    alive = is_alive(process.pid)
    record = {"pid": process.pid,
              "status": "running" if alive else "exited_immediately",
              "started_at": now(), "argv": argv, "log": str(LOG),
              "interval_minutes": args.interval_minutes,
              "max_catchup_days": args.max_catchup_days, "rate": args.rate,
              "cooling_at_start": cooling}
    save_state(record)
    result = {"ok": alive, "service": record}
    if not alive:
        result["error"] = "supervisor exited within 1s; check the log"
        result["log_tail"] = tail(20)
        return result, 2
    return result, 0


def tail(lines):
    try:
        content = LOG.read_text(errors="replace").splitlines()
    except OSError:
        return []
    return content[-lines:]


def stop(args):
    state = read_state()
    pid = state.get("pid")
    if not pid:
        return {"ok": True, "status": "not_running"}, 0
    if not is_alive(pid):
        state.update({"status": "stale_removed", "pid": None, "stopped_at": now()})
        save_state(state)
        return {"ok": True, "status": "stale_removed", "pid": pid}, 0
    if not is_our_supervisor(pid):
        state.update({"status": "identity_mismatch", "stopped_at": now()})
        save_state(state)
        return ({"ok": False, "status": "identity_mismatch", "pid": pid,
                 "error": "pid is alive but is not this live_sync supervisor; refusing to signal"},
                2)
    os.kill(int(pid), signal.SIGTERM)
    deadline = time.monotonic() + (args.timeout_seconds or 20)
    while time.monotonic() < deadline:
        if not is_alive(pid):
            break
        time.sleep(0.5)
    forced = False
    if is_alive(pid):
        os.kill(int(pid), signal.SIGKILL)
        forced = True
        time.sleep(0.5)
    alive = is_alive(pid)
    state.update({"status": "stopped" if not alive else "stop_failed",
                  "stopped_at": now(), "forced_kill": forced})
    if not alive:
        state["pid"] = None
    save_state(state)
    return {"ok": not alive, "status": state["status"], "pid": pid,
            "forced_kill": forced}, 0 if not alive else 2


def status(args):
    import kemai_live_sync as live
    state = read_state()
    pid = state.get("pid")
    result = {"service": state, "alive": is_alive(pid), "log": str(LOG),
              "log_tail": tail(5)}
    if args.db:
        from kemai_sync import connect
        password = password_from_terminal()
        conn = connect(password)
        try:
            cursors = {}
            for kind in live.KINDS:
                row = live.live_cursor(conn, kind)
                cursors[kind] = {k: (str(v) if v is not None else None) for k, v in row.items()} if row else None
            result["live_cursors_utc"] = cursors
            result["daily_checkpoints"] = {
                kind: iso_or_none(live.checkpoint_day(conn, f"commerce_incremental:{kind}"))
                for kind in live.KINDS}
            result["cooldown"] = live.cooldown_state()
            result["active_source_workers"] = live.active_source_workers()
        finally:
            conn.close()
    return {"ok": True, **result}, 0


def restart(args):
    _, stop_code = stop(args)
    if stop_code not in (0,):
        return {"ok": False, "error": "stop did not complete; not starting a second instance"}, 2
    return start(args)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("start", "stop", "status", "restart"))
    parser.add_argument("--interval-minutes", type=int, choices=(30, 60), default=DEFAULT_INTERVAL)
    parser.add_argument("--max-catchup-days", type=int, default=DEFAULT_MAX_CATCHUP_DAYS)
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--db", action="store_true",
                        help="status: also read live cursors/checkpoints (prompts for the DB password)")
    args = parser.parse_args()
    if args.max_catchup_days < 1 or not 0 < args.rate <= DEFAULT_RATE:
        parser.error("max-catchup-days must be positive and rate <= 0.5")
    handler = {"start": start, "stop": stop, "status": status, "restart": restart}[args.action]
    result, code = handler(args)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
