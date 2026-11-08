#!/usr/bin/env python3
"""Run one bounded-rate Kemai incremental pass for each completed day."""

import getpass
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from kemai_catchup_launch import STATUS as CATCHUP_STATE, KINDS, resume_start
from kemai_followup_launch import ROOT, LEGACY, STATE as FULL_STATE, spawn

STATE=ROOT / '.runtime' / 'state' / 'kemai_daily_status.json'
LOG=ROOT / '.runtime' / 'logs' / 'kemai_daily_supervisor.log'
PRODUCT_JOBS=(('master_snapshot','kemai_masters.py'),
              ('item_incremental','kemai_product_incremental.py'),
              ('supplier_derive','kemai_derive_suppliers.py'),
              ('item_detail_refresh','kemai_changed_refresh.py'),
              ('item_tabs_refresh','kemai_changed_tabs.py'))


def read(path):
    try:
        return json.loads(path.read_text())
    except (OSError,ValueError):
        return {}


def save(state):
    temp=STATE.with_suffix('.json.tmp')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    os.chmod(temp,0o600)
    temp.replace(STATE)


def run_job(state,day,name,script,password):
    jobs=state.setdefault('days',{}).setdefault(day,{})
    if jobs.get(name,{}).get('status')=='success':
        return True
    prior=jobs.get(name,{})
    if prior.get('status')=='running' and prior.get('pid'):
        try:
            os.kill(prior['pid'],0)
        except ProcessLookupError:
            pass
        else:
            raise RuntimeError(f'previous {name} worker pid={prior["pid"]} is still running')
    process,log=spawn('daily_'+name,script,password)
    jobs[name]={'pid':process.pid,'status':'running','log':str(log),
                'started_at':datetime.now().astimezone().isoformat(timespec='seconds')}
    save(state)
    print(f'daily {day} {name} started pid={process.pid}',flush=True)
    code=process.wait()
    jobs[name]['status']='success' if code==0 else 'failed'
    jobs[name]['exit_code']=code
    jobs[name]['finished_at']=datetime.now().astimezone().isoformat(timespec='seconds')
    save(state)
    print(f'daily {day} {name} finished exit={code}',flush=True)
    return code==0


def run_cycle(state,password,full,catchup):
    baseline=(date.fromisoformat(full['to'])+timedelta(days=1)).isoformat()
    target=(date.today()-timedelta(days=1)).isoformat()
    if target<baseline:
        return False
    state['status']='running'
    state['target_complete_day']=target
    save(state)

    product_ok=True
    if target>str(catchup.get('complete_through') or ''):
        for name,script in PRODUCT_JOBS:
            if not product_ok:
                break
            product_ok=run_job(state,target,name,[str(ROOT/script)],password)
    for kind in KINDS:
        if full.get('jobs',{}).get(kind,{}).get('status')!='success':
            state.setdefault('days',{}).setdefault(target,{})[kind]={'status':'full_incomplete'}
            save(state)
            continue
        start=resume_start(password,kind,baseline)
        if start>target:
            jobs=state.setdefault('days',{}).setdefault(target,{})
            if jobs.get(kind,{}).get('status')!='success':
                jobs[kind]={'status':'success','from':start,'rows':0}
                save(state)
            continue
        command=[str(LEGACY / 'kemai_commerce_sync.py'),'incremental','--from',start,
                 '--to',target,'--kind',kind,'--rate','0.5']
        run_job(state,target,kind,command,password)

    jobs=state['days'][target]
    if jobs.get('retail-orders',{}).get('status')=='success' and jobs.get('retail-sales',{}).get('status')=='success':
        previous_complete=state.get('last_complete_day')
        line_start=(date.fromisoformat(previous_complete)+timedelta(days=1)).isoformat() if previous_complete else baseline
        if line_start<=target:
            run_job(state,target,'retail_order_lines',
                    [str(LEGACY / 'kemai_retail_lines_from_flows.py'),'--from',line_start,'--to',target],password)

    product_done=(target<=str(catchup.get('complete_through') or '') or
                  all(jobs.get(name,{}).get('status')=='success' for name,_ in PRODUCT_JOBS))
    streams_done=all(jobs.get(kind,{}).get('status')=='success' for kind in KINDS)
    lines_done=(target<baseline or jobs.get('retail_order_lines',{}).get('status')=='success'
                or (state.get('last_complete_day') and target<=state['last_complete_day']))
    state['status']='watching' if product_done and streams_done and lines_done else 'retry_wait'
    if state['status']=='watching':
        state['last_complete_day']=target
    save(state)
    print(f'daily {target} status={state["status"]}',flush=True)
    return True


def supervise(password):
    # Prevent the retired daily loop from running alongside live or batch jobs.
    runner_fd=os.open(ROOT / '.runtime' / 'locks' / '.kemai-live.lock',os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(runner_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(runner_fd)
        raise RuntimeError('another Kemai live or batch collector is running') from error
    state=read(STATE)
    state.pop('paused_at',None)
    state.pop('paused_by',None)
    state.update({'supervisor_pid':os.getpid(),'status':'waiting_for_catchup'})
    save(state)
    while True:
        full=read(FULL_STATE)
        catchup=read(CATCHUP_STATE)
        if full.get('status') not in ('success','failed') or catchup.get('status')!='success':
            state['status']='waiting_for_catchup'
            save(state)
            time.sleep(60)
            continue
        if datetime.now().hour<2:
            time.sleep(900)
            continue
        try:
            ran=run_cycle(state,password,full,catchup)
        except Exception as error:
            state['status']='retry_wait'
            state['last_error']=str(error)[:1000]
            save(state)
            print(f'daily cycle failed: {error}',flush=True)
            time.sleep(1800)
            continue
        time.sleep(3600 if ran else 900)


def launch():
    old=read(STATE)
    pid=old.get('supervisor_pid')
    if pid and old.get('status') in ('waiting_for_catchup','running','watching','retry_wait'):
        try:
            os.kill(pid,0)
            raise SystemExit(f'daily supervisor already running pid={pid}')
        except ProcessLookupError:
            pass
    password=getpass.getpass('MySQL password: ')
    read_fd,write_fd=os.pipe()
    env=os.environ.copy()
    env['KEMAI_PASSWORD_FD']=str(read_fd)
    log_fd=os.open(LOG,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--supervise'],
                                 cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log_fd,stderr=log_fd,
                                 pass_fds=(read_fd,),start_new_session=True)
        os.close(read_fd)
        with os.fdopen(write_fd,'w') as stream:
            stream.write(password+'\n')
        print(f'started daily supervisor pid={process.pid}; status={STATE}')
    finally:
        os.close(log_fd)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--supervise':
        fd=int(os.environ.pop('KEMAI_PASSWORD_FD'))
        with os.fdopen(fd,'r') as stream:
            supervise(stream.readline().rstrip('\n'))
    else:
        launch()
