#!/usr/bin/env python3
"""Optional 30/60-minute scheduler using only kemai_syncctl; never starts itself."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from zoneinfo import ZoneInfo
from kemai_pipeline.artifacts import atomic_json
from kemai_pipeline.locking import writer_lock

ROOT=Path(__file__).resolve().parent


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stream',action='append',choices=['retail-sales','retail-orders'],required=True)
    ap.add_argument('--interval-minutes',type=int,choices=[30,60],default=30)
    ap.add_argument('--max-catchup-days',type=int,default=2)
    ap.add_argument('--once',action='store_true')
    args=ap.parse_args(argv)
    if not 1<=args.max_catchup_days<=31:ap.error('max-catchup-days must be 1..31')
    stopping=False;child=None
    def stop(signum,frame):
        nonlocal stopping
        stopping=True
        if child and child.poll() is None:
            try:os.killpg(child.pid,signal.SIGINT)
            except ProcessLookupError:pass
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    target=ROOT/'.kemai-artifacts/service-status.json'
    # Same file as legacy live supervisor: cannot run old/new services simultaneously.
    with writer_lock(ROOT/'.kemai-live.service.lock'):
        while not stopping:
            started=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
            argv=[sys.executable,str(ROOT/'kemai_syncctl.py'),'incremental','run',
                  '--max-catchup-days',str(args.max_catchup_days)]
            for stream in dict.fromkeys(args.stream):argv+=['--stream',stream]
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            log=target.parent/'last-service-cycle.json'
            with log.open('w') as handle:
                os.chmod(log,0o600)
                child=subprocess.Popen(argv,cwd=ROOT,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True)
                atomic_json(target,{'state':'running','pid':os.getpid(),'child_pid':child.pid,'started_at':started})
                returncode=child.wait()
            atomic_json(target,{'state':'stopped' if stopping or args.once else 'waiting','pid':os.getpid(),
                'last_started_at':started,'last_finished_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                'last_exit_code':returncode,'result_file':str(log),'interval_minutes':args.interval_minutes})
            if stopping or args.once:return returncode
            deadline=time.monotonic()+60*args.interval_minutes
            while not stopping and time.monotonic()<deadline:time.sleep(min(1,deadline-time.monotonic()))
    return 0


if __name__=='__main__':raise SystemExit(main())
