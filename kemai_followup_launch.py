#!/usr/bin/env python3
"""Launch and supervise the bounded-rate commerce backfill and item delta scan."""

import getpass
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'kemai_followup_status.json'
SUPERVISOR_LOG=ROOT/'kemai_followup_supervisor.log'
JOBS=[
    ('purchase-receive-history','2025-01-01'),
    ('purchase-receipts','2026-08-01'),
    ('retail-sales','2026-08-01'),
    ('purchase-orders','2025-01-01'),
    ('purchase-returns','2026-01-01'),
    ('retail-orders','2026-08-01'),
]


def save(state):
    tmp=STATE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    os.chmod(tmp,0o600)
    tmp.replace(STATE)


def spawn(name,command,password):
    read_fd,write_fd=os.pipe()
    env=os.environ.copy()
    env['KEMAI_PASSWORD_FD']=str(read_fd)
    log_path=ROOT/f'kemai_followup_{name}.log'
    log_fd=os.open(log_path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:
        process=subprocess.Popen([sys.executable,*command],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,
                                 stdout=log_fd,stderr=log_fd,pass_fds=(read_fd,),start_new_session=True)
        os.close(read_fd)
        with os.fdopen(write_fd,'w') as secret_stream:
            secret_stream.write(password+'\n')
        return process,log_path
    finally:
        os.close(log_fd)


def supervise(password,end_day,rate,wait_pid=None):
    previous=json.loads(STATE.read_text()) if STATE.exists() else {}
    completed={}
    if previous.get('to')==end_day:
        completed={name:job for name,job in previous.get('jobs',{}).items()
                   if job.get('status')=='success'}
    state={'started_at':date.today().isoformat(),'supervisor_pid':os.getpid(),
           'to':end_day,'status':'running','jobs':completed}
    save(state)
    failed=[]

    if wait_pid:
        print(f'waiting for active baseline worker pid={wait_pid}',flush=True)
        while True:
            try:
                os.kill(wait_pid,0)
            except ProcessLookupError:
                break
            time.sleep(30)
        print(f'baseline worker pid={wait_pid} exited; continuing serially',flush=True)

    def run_serial(name,command):
        if state['jobs'].get(name,{}).get('status')=='success':
            print(f'skipped completed {name}',flush=True)
            return True
        for attempt in range(1,4):
            process,log_path=spawn(name,command,password)
            state['jobs'][name]={'pid':process.pid,'status':'running','log':str(log_path),'attempts':attempt}
            save(state)
            print(f'started {name} pid={process.pid} attempt={attempt}',flush=True)
            code=process.wait()
            state['jobs'][name]['exit_code']=code
            state['jobs'][name]['status']='success' if code==0 else 'failed'
            save(state)
            print(f'finished {name} exit={code}',flush=True)
            if code==0:
                return True
            if attempt<3:
                state['jobs'][name]['status']='retry_queued'
                save(state)
                time.sleep(15)
        failed.append(name)
        return False

    # Strictly one Kemai-facing process at a time. Detail/tab refreshes depend
    # on the item delta succeeding, so do not run them after a failed scan.
    item_ok=run_serial('item_incremental',[str(ROOT/'kemai_product_incremental.py')])
    if item_ok:
        detail_ok=run_serial('item_detail_refresh',[str(ROOT/'kemai_changed_refresh.py')])
        if detail_ok:
            run_serial('item_tabs_refresh',[str(ROOT/'kemai_changed_tabs.py')])

    for kind,start in JOBS:
        command=[str(ROOT/'kemai_commerce_sync.py'),'full','--from',start,'--to',end_day,
                 '--kind',kind,'--rate',str(rate)]
        run_serial(kind,command)
    state['finished_at']=date.today().isoformat()
    state['status']='success' if not failed else 'failed'
    state['failed_jobs']=failed
    save(state)
    print(f'followup complete status={state["status"]} failed={failed}',flush=True)


def launch(end_day,rate,wait_pid=None):
    if STATE.exists():
        old=json.loads(STATE.read_text())
        pid=old.get('supervisor_pid')
        if pid and old.get('status') not in ('success','failed'):
            try:
                os.kill(pid,0)
                raise SystemExit(f'followup supervisor already running pid={pid}')
            except ProcessLookupError:
                pass
    password=getpass.getpass('MySQL password: ')
    read_fd,write_fd=os.pipe()
    env=os.environ.copy()
    env['KEMAI_PASSWORD_FD']=str(read_fd)
    log_fd=os.open(SUPERVISOR_LOG,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:
        command=[sys.executable,str(ROOT/'kemai_followup_launch.py'),'--supervise',
                 '--to',end_day,'--rate',str(rate)]
        if wait_pid:
            command.extend(['--wait-pid',str(wait_pid)])
        process=subprocess.Popen(command,
                                 cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log_fd,stderr=log_fd,
                                 pass_fds=(read_fd,),start_new_session=True)
        os.close(read_fd)
        with os.fdopen(write_fd,'w') as secret_stream:
            secret_stream.write(password+'\n')
        print(f'started supervisor pid={process.pid}; status={STATE}')
    finally:
        os.close(log_fd)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--supervise',action='store_true')
    parser.add_argument('--to',dest='to_day',type=date.fromisoformat,
                        default=date.today()-timedelta(days=1))
    parser.add_argument('--rate',type=float,default=0.5)
    parser.add_argument('--wait-pid',type=int)
    args=parser.parse_args()
    if not 0<args.rate<=1:
        parser.error('--rate must be in (0,1]')
    if args.supervise:
        fd=int(os.environ.pop('KEMAI_PASSWORD_FD'))
        with os.fdopen(fd,'r') as stream:
            supervise(stream.readline().rstrip('\n'),args.to_day.isoformat(),args.rate,args.wait_pid)
    else:
        launch(args.to_day.isoformat(),args.rate,args.wait_pid)
