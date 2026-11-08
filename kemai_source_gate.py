#!/usr/bin/env python3
"""Serialize Kemai source operations across Python workers and OpenCLI adapters.

One lease covers one logical source operation. Multi-request browser evaluations
must pace their internal requests while holding the lease. This gate protects
the API workload; it does not count every browser asset loaded by navigation.
"""

import argparse
import fcntl
import json
import math
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DIR = Path.home() / ".opencli" / "kemai-source-gate"
DEFAULT_INTERVAL = 2.0


def gate_dir():
    return Path(os.environ.get("KEMAI_SOURCE_GATE_DIR") or DEFAULT_DIR).expanduser()


def interval_seconds():
    value = float(os.environ.get("KEMAI_SOURCE_GATE_INTERVAL_SECONDS", DEFAULT_INTERVAL))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("KEMAI_SOURCE_GATE_INTERVAL_SECONDS must be positive")
    return max(DEFAULT_INTERVAL, value)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read Kemai gate state {path}: {error}") from error


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def cooldown_until(directory):
    config = read_json(directory / "config.json")
    paths = [directory / "rate-limit-status.json"]
    if config.get("legacy_rate_status"):
        paths.append(Path(config["legacy_rate_status"]))
    return max((float(read_json(path).get("resume_after_epoch") or 0) for path in paths), default=0)


@contextmanager
def source_lease(label):
    """Reserve a global start slot, then hold the exclusive lock through the call."""
    directory = gate_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "source.lock"
    lock_path.touch(mode=0o600, exist_ok=True)
    lock = lock_path.open("r+")
    acquired = False
    lease_state = None
    try:
        while True:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            acquired = True
            now = time.time()
            state = read_json(directory / "source-state.json")
            last_boundary = max(float(state.get("last_started_epoch") or 0),
                                float(state.get("last_finished_epoch") or 0))
            next_start = last_boundary + interval_seconds()
            resume = cooldown_until(directory)
            wait = max(next_start, resume) - now
            if wait <= 0:
                lease_state = {
                    "last_started_epoch": now, "last_label": label, "last_pid": os.getpid(),
                }
                write_json(directory / "source-state.json", lease_state)
                break
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            acquired = False
            time.sleep(min(max(wait, 0.001), 30))
        yield
    finally:
        try:
            if acquired:
                try:
                    if lease_state is not None:
                        lease_state["last_finished_epoch"] = time.time()
                        write_json(directory / "source-state.json", lease_state)
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()


def configure():
    directory = gate_dir()
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "config.json", {
        "script_path": str(Path(__file__).resolve()),
        "legacy_rate_status": str(ROOT / "kemai_rate_limit_status.json"),
    })
    print(directory / "config.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("configure", help="write the non-secret adapter configuration")
    lease = sub.add_parser("lease", help="hold a lease until stdin closes")
    lease.add_argument("label")
    run = sub.add_parser("run", help="run a manual OpenCLI browser operation under the gate")
    run.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "configure":
        configure()
        return
    if args.action == "lease":
        with source_lease(args.label):
            print("READY", flush=True)
            for line in sys.stdin:
                if line.startswith("RATE_LIMIT "):
                    from kemai_rate_limit import record_limit
                    record_limit(args.label, line[len("RATE_LIMIT "):].strip(), wait=False)
        return
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("run requires a command after --")
    if len(command) >= 2 and command[0] == "opencli" and command[1] == "kemai":
        parser.error("opencli kemai already uses this gate; wrapping it would deadlock")
    with source_lease("manual:" + " ".join(command[:3])):
        result = subprocess.run(command, capture_output=True, text=True)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        from kemai_rate_limit import is_rate_limited, record_limit
        if is_rate_limited(result.stdout + result.stderr):
            record_limit("manual:" + " ".join(command[:3]),
                         (result.stdout + result.stderr)[-500:], wait=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
