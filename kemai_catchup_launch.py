#!/usr/bin/env python3
"""One-time catch-up pass after each background commerce full stream succeeds."""

import getpass
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from kemai_followup_launch import ROOT,STATE as FULL_STATE,spawn
from kemai_sync import TENANT,connect


STATUS=ROOT/'kemai_catchup_status.json'
LOG=ROOT/'kemai_catchup_supervisor.log'
KINDS=('purchase-orders','purchase-receipts','purchase-returns',
       'retail-orders','retail-sales','purchase-receive-history')


def save(state):
    tmp=STATUS.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n')
    os.chmod(tmp,0o600)
    tmp.replace(STATUS)


def resume_start(password,kind,baseline):
    """Start after the last committed incremental day for this stream."""
    conn=connect(password)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT cursor_json FROM sync_checkpoints WHERE tenant_id=%s AND stream=%s",
                        (TENANT,'commerce_incremental:'+kind))
            row=cur.fetchone()
        if row:
            cursor=json.loads(row['cursor_json'])
            last=cursor.get('last_complete_day')
            if last:
                return max(baseline,(date.fromisoformat(last)+timedelta(days=1)).isoformat())
        return baseline
    finally:
        conn.close()


def supervise(password):
    previous=json.loads(STATUS.read_text()) if STATUS.exists() else {}
    state={'supervisor_pid':os.getpid(),'status':'running','streams':{},'products':{}}
    save(state)
    # Do not overlap catch-up requests with the initial full supervisor. Wait
    # for all full streams to reach a terminal state, then catch up serially.
    while True:
        full=json.loads(FULL_STATE.read_text()) if FULL_STATE.exists() else {}
        if full.get('status') not in ('success','failed'):
            time.sleep(60)
            continue
        cutoff=date.fromisoformat(full['to'])
        start=(cutoff+timedelta(days=1)).isoformat()
        complete_through=(date.today()-timedelta(days=1)).isoformat()
        if start>complete_through:
            print(f'catch-up waiting for first complete day from {start}',flush=True)
            time.sleep(600)
            continue
        if previous.get('baseline_to')==full['to'] and previous.get('complete_through')==complete_through:
            state['streams']={key:value for key,value in previous.get('streams',{}).items()
                              if value.get('status')=='success'}
            state['products']={key:value for key,value in previous.get('products',{}).items()
                              if value.get('status')=='success'}
        previous={}
        state['baseline_to']=full['to']
        state['complete_through']=complete_through
        save(state)
        jobs=full.get('jobs',{})
        product_ok=True
        for name,script in (('item_incremental','kemai_product_incremental.py'),
                            ('item_detail_refresh','kemai_changed_refresh.py'),
                            ('item_tabs_refresh','kemai_changed_tabs.py')):
            if state['products'].get(name,{}).get('status')=='success':
                continue
            if not product_ok:
                state['products'][name]={'status':'dependency_failed'}
                save(state)
                continue
            # A successful baseline product scan may have happened before the
            # catch-up day ended. It cannot prove that day's final catalog
            # state, so run one fresh catalog pass after the complete day.
            process,log=spawn('catchup_'+name,[str(ROOT/script)],password)
            state['products'][name]={'pid':process.pid,'status':'running','log':str(log)}
            save(state)
            print(f'catch-up {name} started pid={process.pid}',flush=True)
            code=process.wait()
            product_ok=code==0
            state['products'][name]['status']='success' if product_ok else 'failed'
            state['products'][name]['exit_code']=code
            save(state)
            print(f'catch-up {name} finished exit={code}',flush=True)
        for kind in KINDS:
            if state['streams'].get(kind,{}).get('status')=='success':
                continue
            full_status=jobs.get(kind,{}).get('status')
            if full_status=='success':
                rate='0.5'
                kind_start=resume_start(password,kind,start)
                if kind_start>complete_through:
                    state['streams'][kind]={'status':'success','from':kind_start,'through':complete_through,'rows':0}
                    save(state)
                    continue
                command=[str(ROOT/'kemai_commerce_sync.py'),'incremental','--from',kind_start,
                         '--to',complete_through,'--kind',kind,'--rate',rate]
                process,log=spawn('catchup_'+kind,command,password)
                state['streams'][kind]={'pid':process.pid,'status':'running','log':str(log),
                                        'from':kind_start,'through':complete_through}
                save(state)
                print(f'catch-up {kind} started pid={process.pid}',flush=True)
                code=process.wait()
                state['streams'][kind]['status']='success' if code==0 else 'failed'
                state['streams'][kind]['exit_code']=code
                save(state)
                print(f'catch-up {kind} finished exit={code}',flush=True)
            elif full_status=='failed':
                state['streams'][kind]={'status':'full_failed'}
                save(state)
            else:
                state['streams'][kind]={'status':'full_incomplete'}
                save(state)
        if len(state['streams'])==len(KINDS):
            all_ok=all(v['status']=='success' for v in state['streams'].values())
            all_ok=all_ok and all(v['status']=='success' for v in state['products'].values())
            state['status']='success' if all_ok else 'failed'
            state['finished_at']=date.today().isoformat()
            save(state)
            print(f'catch-up complete status={state["status"]}',flush=True)
            return
        time.sleep(60)


def launch():
    if STATUS.exists():
        old=json.loads(STATUS.read_text())
        pid=old.get('supervisor_pid')
        if pid and old.get('status')=='running':
            try:
                os.kill(pid,0)
                raise SystemExit(f'catch-up supervisor already running pid={pid}')
            except ProcessLookupError:
                pass
    password=getpass.getpass('MySQL password: ')
    read_fd,write_fd=os.pipe()
    env=os.environ.copy()
    env['KEMAI_PASSWORD_FD']=str(read_fd)
    log_fd=os.open(LOG,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:
        process=subprocess.Popen([sys.executable,str(ROOT/'kemai_catchup_launch.py'),'--supervise'],
                                 cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log_fd,stderr=log_fd,
                                 pass_fds=(read_fd,),start_new_session=True)
        os.close(read_fd)
        with os.fdopen(write_fd,'w') as stream:
            stream.write(password+'\n')
        print(f'started catch-up supervisor pid={process.pid}; status={STATUS}')
    finally:
        os.close(log_fd)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--supervise':
        fd=int(os.environ.pop('KEMAI_PASSWORD_FD'))
        with os.fdopen(fd,'r') as stream:
            supervise(stream.readline().rstrip('\n'))
    else:
        launch()
