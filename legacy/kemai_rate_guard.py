#!/usr/bin/env python3
"""Freeze one legacy Kemai worker group whenever the shared limiter is cooling."""

import argparse
import json
import os
import signal
import time
from datetime import datetime
from pathlib import Path

from kemai_pipeline.rate_limit import STATE, extend_cooldown


ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path(__file__).resolve().parent
STATUS=ROOT / '.runtime' / 'state' / 'kemai_rate_guard_status.json'
LOG=ROOT / '.runtime' / 'logs' / 'kemai_rate_guard.log'


def write_json(path,value):
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n")
    os.chmod(temp,0o600)
    temp.replace(path)


def append(event):
    with LOG.open("a") as stream:
        stream.write(json.dumps(event,ensure_ascii=False)+"\n")
    os.chmod(LOG,0o600)


def group_exists(pgid):
    try:
        os.killpg(pgid,0)
        return True
    except ProcessLookupError:
        return False


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--pgid",type=int,required=True)
    parser.add_argument("--label",required=True)
    args=parser.parse_args()
    managed_stop=False
    last_event=None
    write_json(STATUS,{"pid":os.getpid(),"pgid":args.pgid,"label":args.label,
                       "state":"watching","started_at":now_iso()})
    while group_exists(args.pgid):
        limiter={}
        try:
            limiter=json.loads(STATE.read_text()) if STATE.exists() else {}
        except (OSError,ValueError,json.JSONDecodeError):
            pass
        resume=float(limiter.get("resume_after_epoch") or 0)
        cooling=limiter.get("state")=="cooling" and time.time()<resume
        event_key=(limiter.get("last_event_epoch"),limiter.get("extended_at"),resume)
        if cooling:
            if int(limiter.get("cooldown_seconds") or 0)<1800:
                limiter=extend_cooldown(1800,"rate_guard_enforced_30_minutes")
                resume=float(limiter["resume_after_epoch"])
                event_key=(limiter.get("last_event_epoch"),limiter.get("extended_at"),resume)
            if not managed_stop:
                os.killpg(args.pgid,signal.SIGSTOP)
                managed_stop=True
                event={"event":"worker_frozen","at":now_iso(),"pgid":args.pgid,
                       "label":args.label,"resume_after":limiter.get("resume_after")}
                append(event)
                write_json(STATUS,{"pid":os.getpid(),"pgid":args.pgid,"label":args.label,
                                   "state":"cooling","resume_after":limiter.get("resume_after"),
                                   "updated_at":now_iso()})
            last_event=event_key
        elif managed_stop and time.time()>=resume:
            os.killpg(args.pgid,signal.SIGCONT)
            managed_stop=False
            event={"event":"worker_resumed","at":now_iso(),"pgid":args.pgid,"label":args.label}
            append(event)
            write_json(STATUS,{"pid":os.getpid(),"pgid":args.pgid,"label":args.label,
                               "state":"running","updated_at":now_iso(),"last_limiter_event":last_event})
        time.sleep(1)
    event={"event":"worker_group_finished","at":now_iso(),"pgid":args.pgid,"label":args.label}
    append(event)
    write_json(STATUS,{"pid":os.getpid(),"pgid":args.pgid,"label":args.label,
                       "state":"finished","finished_at":now_iso()})


if __name__=="__main__":
    main()
