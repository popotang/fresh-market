#!/usr/bin/env python3
"""Start the long Kemai full sync detached; keep DB credentials in process memory."""

import getpass
import os
import subprocess
import sys
from pathlib import Path


ROOT=Path(__file__).resolve().parent
PID_FILE=ROOT/"kemai_full_run.pid"
LOG_FILE=ROOT/"kemai_full_run.log"


def running(pid):
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False


def main():
    if PID_FILE.exists():
        try:
            pid=int(PID_FILE.read_text().strip())
            if running(pid):
                raise SystemExit(f"already running pid={pid}")
        except ValueError:
            pass
    password=getpass.getpass("MySQL password: ")
    read_fd,write_fd=os.pipe()
    env=os.environ.copy()
    env["KEMAI_PASSWORD_FD"]=str(read_fd)
    log_fd=os.open(LOG_FILE,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:
        process=subprocess.Popen([sys.executable,str(ROOT/"kemai_sync.py"),"full","--rate","0.5","--batch-size","5"],
                                 cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log_fd,stderr=log_fd,
                                 pass_fds=(read_fd,),start_new_session=True)
        os.close(read_fd)
        with os.fdopen(write_fd,"w") as secret_stream:
            secret_stream.write(password+"\n")
        PID_FILE.write_text(str(process.pid)+"\n")
        print(f"started pid={process.pid}; log={LOG_FILE}")
    finally:
        os.close(log_fd)


if __name__=="__main__":
    main()
