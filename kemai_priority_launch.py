#!/usr/bin/env python3
"""Launch one detached, lock-protected Kemai commerce stream with an inherited secret FD."""

import argparse
import getpass
import os
import subprocess
import sys
from datetime import date
from pathlib import Path


ROOT=Path(__file__).resolve().parent
KINDS=("purchase-orders","purchase-receipts","purchase-returns","retail-orders","retail-sales","purchase-receive-history")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind",choices=KINDS,required=True)
    parser.add_argument("--from",dest="from_day",required=True)
    parser.add_argument("--to",dest="to_day",default=date.today().isoformat())
    parser.add_argument("--rate",type=float,default=0.5)
    args=parser.parse_args()
    pid_path=ROOT/f"kemai_priority_{args.kind}.pid"
    if pid_path.exists():
        try:
            old_pid=int(pid_path.read_text().strip())
            os.kill(old_pid,0)
            raise SystemExit(f"{args.kind} priority process already running pid={old_pid}")
        except (ValueError,ProcessLookupError):
            pass
    password=getpass.getpass("MySQL password: ")
    read_fd,write_fd=os.pipe()
    env=os.environ.copy()
    env["KEMAI_PASSWORD_FD"]=str(read_fd)
    log_path=ROOT/f"kemai_followup_{args.kind}-priority.log"
    log_fd=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    command=[sys.executable,str(ROOT/"kemai_commerce_sync.py"),"full","--from",args.from_day,
             "--to",args.to_day,"--kind",args.kind,"--rate",str(args.rate)]
    try:
        process=subprocess.Popen(command,cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                                 stdout=log_fd,stderr=log_fd,pass_fds=(read_fd,),start_new_session=True)
        os.close(read_fd)
        with os.fdopen(write_fd,"w") as secret_stream:
            secret_stream.write(password+"\n")
        pid_path.write_text(f"{process.pid}\n")
        os.chmod(pid_path,0o600)
        print(f"started {args.kind} priority process pid={process.pid}; log={log_path}")
    finally:
        os.close(log_fd)


if __name__=="__main__":
    main()
