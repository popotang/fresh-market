#!/usr/bin/env python3
"""Reconcile the live item list and queue changed/new details for the existing worker."""

import getpass
import json
import os
import subprocess
import time
from datetime import datetime

from kemai_sync import TENANT, connect, digest, dump, dt, list_page, one, product_statement
from kemai_sections import SECTIONS
from kemai_rate_limit import honor_cooldown, is_rate_limited, record_limit
from kemai_source_gate import source_lease
from kemai_sync_log import ensure_tables, start_window, finish_success, finish_failure


RATE = 0.5
VOLATILE = {"gmtOperation"}
ITEM_LIST_URL = "https://yunfan.kemai.com.cn/archives/ItemList"


def prepare_item_page():
    for attempt in range(2):
        honor_cooldown()
        with source_lease('browser:kemai_delta:open'):
            result=subprocess.run(["opencli","browser","kemai_delta","open",ITEM_LIST_URL],
                                  capture_output=True,text=True,timeout=90)
            if is_rate_limited(result.stdout+result.stderr):
                record_limit("item-list-open",(result.stdout+result.stderr)[-500:],wait=False)
        message=result.stdout+result.stderr
        if is_rate_limited(message):
            continue
        if result.returncode:
            raise RuntimeError(f"item list page open failed: {message[-400:]}")
        time.sleep(2)
        return
    raise RuntimeError("item list page open hit repeated rate limits")


def stable_hash(row):
    return digest({k:v for k,v in row.items() if k not in VOLATILE})


def scan():
    page=1
    found={}
    expected=None
    while True:
        started=time.monotonic()
        result=list_page(page,"kemai_delta")
        rows=result["results"]
        total=int(result["totalRecord"])
        if expected is None:
            expected=total
        for row in rows:
            source_id=str(row.get("id") or "")
            if not source_id or source_id in found:
                raise RuntimeError(f"item list duplicate/missing id at page {page}: {source_id}")
            found[source_id]=row
        if page==1 or page%20==0 or len(rows)<100:
            print(f"item incremental scan page={page} unique={len(found)} source_total={total}",flush=True)
        if len(rows)<100:
            break
        page+=1
        time.sleep(max(0,1/RATE-(time.monotonic()-started)))
    if len(found)!=expected:
        raise RuntimeError(f"item list count mismatch: unique={len(found)} source_total={expected}")
    return found


def queue_changes(conn,found):
    new=changed=unchanged=0
    ids=list(found)
    for offset in range(0,len(ids),100):
        selected=ids[offset:offset+100]
        placeholders=','.join(['%s']*len(selected))
        with conn.cursor() as cur:
            cur.execute(f"SELECT source_key,payload FROM source_capture WHERE tenant_id=%s AND stream='item_list' AND source_key IN ({placeholders})",
                        [TENANT,*selected])
            previous={r['source_key']:json.loads(r['payload']) if isinstance(r['payload'],str) else r['payload'] for r in cur.fetchall()}
            for source_id in selected:
                row=found[source_id]
                prior=previous.get(source_id)
                if prior is not None and stable_hash(prior)==stable_hash(row):
                    unchanged+=1
                    continue
                if prior is None:
                    new+=1
                else:
                    changed+=1
                statement,values=product_statement(row,'item_list')
                cur.execute(statement,values)
                cur.execute("""INSERT INTO source_capture
                    (tenant_id,stream,source_key,source_version,source_modified_at,payload_hash,payload)
                    VALUES (%s,'item_list',%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE source_version=VALUES(source_version),
                    source_modified_at=VALUES(source_modified_at),payload_hash=VALUES(payload_hash),
                    payload=VALUES(payload),captured_at=NOW()""",
                    (TENANT,source_id,str(row.get('version')) if row.get('version') is not None else None,
                     dt(row,'gmtModified'),digest(row),dump(row)))
                cur.execute("""INSERT INTO sync_objects (tenant_id,stream,source_key,status,attempts)
                    VALUES (%s,'item_detail',%s,'pending',0)
                    ON DUPLICATE KEY UPDATE status='pending',attempts=0,last_error=NULL""",(TENANT,source_id))
                for section in SECTIONS:
                    stream='item_tab_'+section
                    cur.execute("""INSERT INTO sync_objects
                        (tenant_id,stream,source_key,status,attempts,last_error)
                        VALUES (%s,%s,%s,'pending',0,NULL)
                        ON DUPLICATE KEY UPDATE status='pending',attempts=0,last_error=NULL""",
                        (TENANT,stream,source_id))
        conn.commit()
        if offset%2000==0:
            print(f"item incremental compare {min(offset+100,len(ids))}/{len(ids)} new={new} changed={changed}",flush=True)
    return {'new':new,'changed':changed,'unchanged':unchanged,'source_total':len(found)}


def main():
    secret_fd=os.environ.pop('KEMAI_PASSWORD_FD',None)
    if secret_fd is None:
        password=getpass.getpass('MySQL password: ')
    else:
        with os.fdopen(int(secret_fd),'r') as secret_stream:
            password=secret_stream.readline().rstrip('\n')
    conn=connect(password)
    try:
        ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sync_runs (tenant_id,mode,status,parameters) VALUES (%s,'item_incremental','running',%s)",
                        (TENANT,dump({'rate':RATE,'session':'kemai_delta'})))
            run_id=cur.lastrowid
        conn.commit()
        scan_day=datetime.now().date()
        event_id=start_window(conn,'item_list','incremental',scan_day,scan_day,run_id)
        try:
            prepare_item_page()
            found=scan()
            # One source scan only; scan() already verifies unique IDs against
            # totalRecord, so a second pass only doubles load without adding a
            # stronger reconciliation signal.
            stats=queue_changes(conn,found)
            with conn.cursor() as cur:
                cur.execute("UPDATE sync_runs SET status='success',stats=%s,finished_at=NOW() WHERE id=%s",(dump(stats),run_id))
            finish_success(conn,event_id,'item_list',scan_day,
                           stats['new']+stats['changed'],'source_total_and_unique_ids',
                           'full_catalog','商品清单全量扫描；写入数为新增与变化数',
                           source_rows=stats['source_total'])
            conn.commit()
            print(dump({'run_id':run_id,**stats}),flush=True)
        except BaseException as exc:
            conn.rollback()
            finish_failure(conn,event_id,'item_list',exc)
            with conn.cursor() as cur:
                cur.execute("UPDATE sync_runs SET status='failed',last_error=%s,finished_at=NOW() WHERE id=%s",
                            (str(exc)[:4000],run_id))
            conn.commit()
            raise
    finally:
        conn.close()


if __name__=='__main__':
    main()
