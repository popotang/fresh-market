#!/usr/bin/env python3
"""Read Kemai master-data pages through OpenCLI and upsert reference tables."""

import getpass
import argparse
import os
import subprocess
import time
from datetime import datetime

from kemai_sync import TENANT, browser, connect, dump, value
from kemai_sync_log import ensure_tables,start_window,finish_success,finish_failure
from kemai_pipeline.rate_limit import honor_cooldown,is_rate_limited,record_limit
from kemai_pipeline.source_gate import source_lease


ITEM_LIST_URL='https://yunfan.kemai.com.cn/archives/ItemList'


SOURCES = [
    ("branches", "/web/api/user/branch/getListData", "source_branch_id",
     ["source_branch_id","branch_code","branch_name","branch_type","parent_source_branch_id"],
     ["id","code","name","type","pid"]),
    ("departments", "/web/api/product/department/getListData", "source_department_id",
     ["source_department_id","department_code","department_name"],
     ["id","code","name"]),
    ("categories", "/web/api/product/category/getListData", "source_category_id",
     ["source_category_id","category_code","category_name","parent_source_category_id","source_department_id","category_level"],
     ["id","code","name","parentId","departmentId","level"]),
    ("brands", "/web/api/product/brand/getListData", "source_brand_id",
     ["source_brand_id","brand_code","brand_name","parent_source_brand_id"],
     ["id","code","name","parentId"]),
    ("units", "/web/api/product/unit/getListData", "source_unit_id",
     ["source_unit_id","unit_name"],
     ["id","name"]),
]


def page(endpoint, number):
    expr=("(async()=>{if(!window.__kemai_req){let key='master_'+Date.now();"
          "window.webpackJsonp.push([[key],{[key]:(m,e,r)=>{window.__kemai_req=r}},[[key]]])}"
          "let a=await window.__kemai_req('66df').a.request({"
          f"url:{dump(endpoint)},data:{{page:{number},rows:100,params:{{}},filters:[],orders:[]}},"
          "method:'post',hideRuning:true});let d=a?.data?.data,m=a?.data?.message||a?.data?.msg||'',"
          "s=a?.response?.status||a?.status,c=a?.data?.code;return {code:c,message:m,"
          "rateLimited:/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test([s,c,m,document.body?.innerText||''].join(' ')),"
          "page:d?.page,totalRecord:d?.totalRecord,results:d?.results}})()")
    result=browser(expr,"kemai_probe")
    if result.get("code") != 0 or not isinstance(result.get("results"),list):
        raise RuntimeError(f"invalid {endpoint} page {number}: {str(result)[:200]}")
    return result


def prepare_session():
    def ready():
        try:
            state=subprocess.run(['opencli','browser','kemai_probe','state'],
                                 capture_output=True,text=True,timeout=15)
        except subprocess.TimeoutExpired:
            return False
        return state.returncode==0 and f'URL: {ITEM_LIST_URL}' in state.stdout
    if ready():
        return
    for attempt in range(2):
        honor_cooldown()
        try:
            with source_lease('browser:kemai_probe:open'):
                opened=subprocess.run(['opencli','browser','kemai_probe','open',ITEM_LIST_URL],
                                      capture_output=True,text=True,timeout=30)
                if is_rate_limited(opened.stdout+opened.stderr):
                    record_limit('master-page-open',(opened.stdout+opened.stderr)[-500:],wait=False)
        except subprocess.TimeoutExpired:
            if ready():
                return
            raise RuntimeError('master page open timed out before page became ready')
        message=opened.stdout+opened.stderr
        if is_rate_limited(message):
            continue
        if opened.returncode:
            raise RuntimeError(f'master page open failed: {message[-400:]}')
        time.sleep(2)
        return
    raise RuntimeError('master page could not recover from rate limiting')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rate',type=float,default=0.5)
    args=parser.parse_args()
    if not 0<args.rate<=0.5:
        parser.error('--rate must be in (0,0.5]')
    secret_fd=os.environ.pop('KEMAI_PASSWORD_FD',None)
    if secret_fd is None:
        password=getpass.getpass("MySQL password: ")
    else:
        with os.fdopen(int(secret_fd),'r') as stream:
            password=stream.readline().rstrip('\n')
    conn=connect(password)
    try:
        ensure_tables(conn)
        prepare_session()
        last_request_started=None
        with conn.cursor() as cur:
            for table,endpoint,key,columns,fields in SOURCES:
                day=datetime.now().date()
                event_id=start_window(conn,table,'snapshot',day,day)
                current=1
                expected=None
                seen=set()
                try:
                    while True:
                        if last_request_started is not None:
                            time.sleep(max(0,1/args.rate-(time.monotonic()-last_request_started)))
                        started=time.monotonic()
                        data=page(endpoint,current)
                        last_request_started=started
                        results=data["results"]
                        total=int(data["totalRecord"])
                        if expected is None:
                            expected=total
                        elif total!=expected:
                            raise RuntimeError(f"{table} source total changed during scan: {expected} to {total}")
                        values=[]
                        for row in results:
                            vals=[value(row,f) for f in fields]
                            if vals[0] is None:
                                raise RuntimeError(f"{table} missing source id")
                            if vals[0] in seen:
                                raise RuntimeError(f"{table} duplicate source id {vals[0]}")
                            seen.add(vals[0])
                            values.append([TENANT]+vals+[dump(row)])
                        if values:
                            all_columns=["tenant_id"]+columns+["payload"]
                            updates=','.join(f"{c}=VALUES({c})" for c in all_columns[2:])
                            statement=(f"INSERT INTO {table} ({','.join(all_columns)}) VALUES ({','.join(['%s']*len(all_columns))}) "
                                       f"ON DUPLICATE KEY UPDATE {updates}")
                            cur.executemany(statement,values)
                        conn.commit()
                        print(f"{table} page={current} rows={len(results)} total={expected}",flush=True)
                        if len(results)<100:
                            break
                        current+=1
                    if len(seen)!=expected:
                        raise RuntimeError(f"{table} incomplete: unique={len(seen)} expected={expected}")
                    finish_success(conn,event_id,table,day,len(seen),'source_total_and_unique_ids',
                                   'full_catalog','本次主数据分页总数与唯一源 ID 数一致')
                    conn.commit()
                except BaseException as error:
                    conn.rollback()
                    finish_failure(conn,event_id,table,error)
                    raise
    finally:
        conn.close()


if __name__=="__main__":
    main()
