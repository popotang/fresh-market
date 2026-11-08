#!/usr/bin/env python3
"""Rate-limited, resumable Kemai item sync through an authenticated OpenCLI browser."""

import argparse
import getpass
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pymysql

from kemai_rate_limit import honor_cooldown,is_rate_limited,mark_ready,rate_limit_signal,record_limit
from kemai_source_gate import source_lease


ROOT = Path(__file__).resolve().parent
TENANT = "205149"
HOST = "rm-bp1zw23n898h6iacwso.mysql.rds.aliyuncs.com"
DB = "fresh-market"
PAGE_SIZE = 100


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def value(obj, key):
    x = obj.get(key)
    return str(x) if x is not None else None


def money(obj, key):
    x = obj.get(key)
    if x is None or x == "":
        return None
    try:
        return Decimal(str(x)) / 10000
    except InvalidOperation:
        return None


def dt(obj, key):
    x = obj.get(key)
    if x is None or x == "":
        return None
    if isinstance(x, (float, int)):
        return datetime.fromtimestamp(x / 1000).strftime("%Y-%m-%d %H:%M:%S")
    return str(x)[:19]


def connect(password):
    # Share one single-host writer lock with the new CLI-backed pipeline.
    # The lock lives for the connection lifetime; existing DB GET_LOCK remains.
    from kemai_pipeline.locking import acquire_writer
    release = acquire_writer()
    try:
        conn = pymysql.connect(host=HOST, port=7707, user="fresh_market", password=password,
                               database=DB, charset="utf8mb4", autocommit=False,
                               cursorclass=pymysql.cursors.DictCursor,
                               connect_timeout=15, read_timeout=60, write_timeout=60)
    except BaseException:
        release()
        raise
    original_close = conn.close
    def close():
        try:
            return original_close()
        finally:
            release()
    conn.close = close
    return conn


def sql(conn, statement, params=()):
    with conn.cursor() as cur:
        cur.execute(statement, params)
        return cur


def one(conn, statement, params=()):
    with conn.cursor() as cur:
        cur.execute(statement, params)
        return cur.fetchone()


def browser(expression, session="kemai_full"):
    for attempt in range(2):
        honor_cooldown()
        with source_lease(f"browser:{session}:eval"):
            p = subprocess.run(["opencli", "browser", session, "eval", expression],
                               capture_output=True, text=True, timeout=110)
            if p.returncode:
                message=(p.stdout+p.stderr)[-500:]
                if is_rate_limited(message):
                    record_limit(f"browser:{session}",message,wait=False)
                    if attempt == 0:
                        continue
                raise RuntimeError(f"OpenCLI browser failed ({p.returncode}): {message}")
            try:
                result=json.loads(p.stdout)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"OpenCLI returned invalid JSON: {p.stdout[:150]}") from e
            signal=rate_limit_signal(result)
            if signal:
                record_limit(f"browser:{session}",signal,wait=False)
                if attempt == 0:
                    continue
                raise RuntimeError(f"Kemai rate limit repeated in browser session {session}: {signal}")
            mark_ready()
            return result
    raise RuntimeError(f"Kemai browser session {session} did not recover from rate limiting")


def list_page(page, session="kemai_full"):
    expr = ("(async()=>{let v=[...document.querySelectorAll('#singlePageDiv *')]"
            ".find(e=>e.__vue__?.$options?.name==='ListPage')?.__vue__;"
            "if(!v)return {error:'item list unavailable'};"
            f"try{{let a=await v.getDataApi({{page:{page},rows:{PAGE_SIZE},params:v.QueryParam,filters:[],orders:[]}},v.Action,true);"
            "let d=a?.data?.data,m=a?.data?.message||a?.data?.msg||'',body=document.body?.innerText||'';"
            "let signal=[a?.response?.status,a?.status,a?.data?.code,m,body].join(' ');"
            "return {code:a?.data?.code,message:m,rateLimited:/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test(signal),"
            "page:d?.page,rows:d?.rows,totalRecord:d?.totalRecord,results:d?.results}}"
            "catch(e){let m=e?.response?.data?.message||e?.response?.data?.msg||e?.message||String(e),"
            "s=e?.response?.status||e?.status,c=e?.response?.data?.code;return {error:m,status:s,code:c,"
            "rateLimited:/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test([s,c,m,document.body?.innerText||''].join(' '))}}})()")
    for attempt in range(2):
        result = browser(expr,session)
        if isinstance(result,dict) and (result.get("rateLimited") or is_rate_limited(result.get("error") or result.get("message") or "")):
            record_limit(f"item-list-page:{page}",result)
            if attempt == 0:
                continue
            raise RuntimeError(f"rate limit repeated for list page {page}")
        if isinstance(result, dict) and result.get("code") == 0 and isinstance(result.get("results"), list):
            return result
        raise RuntimeError(f"bad list page {page}: {str(result)[:250]}")


def detail_batch(ids, session="kemai_full", interval_seconds=0):
    interval_ms=max(0,int(float(interval_seconds)*1000))
    expr = ("(async()=>{let v=document.querySelector('#itemInfo')?.__vue__?.$refs?.infopage;"
            "if(!v)return {error:'item detail unavailable'};let out=[];"
            f"let ids={dump(ids)};for(let i=0;i<ids.length;i++){{let id=ids[i];"
            f"if(i&&{interval_ms})await new Promise(r=>setTimeout(r,{interval_ms}));"
            "try{let a=await v.loadEntityApi({param:{},id},v.Action);"
            "let m=a?.data?.message||a?.data?.msg||'',s=a?.response?.status||a?.status,c=a?.data?.code;"
            "out.push({id,code:c,message:m,rateLimited:/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test([s,c,m,document.body?.innerText||''].join(' ')),entity:a?.data?.data?.entity})}"
            "catch(e){let m=e?.response?.data?.message||e?.response?.data?.msg||e?.message||String(e),"
            "s=e?.response?.status||e?.status,c=e?.response?.data?.code;out.push({id,error:m,status:s,code:c,"
            "rateLimited:/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test([s,c,m,document.body?.innerText||''].join(' '))})}}return out})()")
    for attempt in range(2):
        result = browser(expr,session)
        if not isinstance(result, list) or len(result) != len(ids):
            raise RuntimeError(f"bad detail batch: {str(result)[:250]}")
        limited=next((x for x in result if x.get("rateLimited") or is_rate_limited(x.get("error") or x.get("message") or "")),None)
        if not limited:
            return result
        record_limit(f"item-detail-batch:{ids[0]}",limited)
        if attempt == 1:
            raise RuntimeError(f"rate limit repeated for detail batch starting {ids[0]}")


def migrate(conn):
    source = (ROOT / "schema_v2.sql").read_text()
    statements = [s.strip() for s in source.split(";") if "CREATE TABLE" in s]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()
    print(f"schema ready: {len(statements)} CREATE TABLE statements", flush=True)


def run_start(conn, phase, rate):
    with conn.cursor() as cur:
        cur.execute("UPDATE sync_runs SET status='interrupted',finished_at=NOW(),"
                    "last_error='process ended before completion' WHERE tenant_id=%s AND mode=%s AND status='running'",
                    (TENANT,phase))
        cur.execute("INSERT INTO sync_runs (tenant_id,mode,status,parameters) VALUES (%s,%s,'running',%s)",
                    (TENANT, phase, dump({"rate_per_second": rate, "browser": "kemai_full"})))
        run_id = cur.lastrowid
    conn.commit()
    return run_id


def run_end(conn, run_id, status, error=None):
    sql(conn, "UPDATE sync_runs SET status=%s,finished_at=NOW(),last_error=%s WHERE id=%s",
        (status, str(error)[:4000] if error else None, run_id))
    conn.commit()


def checkpoint(conn, stream, state, run_id):
    sql(conn, "INSERT INTO sync_checkpoints (tenant_id,stream,cursor_json,last_run_id) VALUES (%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE cursor_json=VALUES(cursor_json),last_run_id=VALUES(last_run_id)",
        (TENANT, stream, dump(state), run_id))


def capture(conn, stream, source_key, payload, source_version=None, modified=None):
    sql(conn, "INSERT INTO source_capture (tenant_id,stream,source_key,source_version,source_modified_at,payload_hash,payload) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE source_version=VALUES(source_version),"
        "source_modified_at=VALUES(source_modified_at),payload_hash=VALUES(payload_hash),"
        "payload=VALUES(payload),captured_at=NOW()",
        (TENANT, stream, source_key, source_version, modified, digest(payload), dump(payload)))


def product_statement(item, stream):
    item_id = value(item, "id")
    code = value(item, "code")
    name = value(item, "name")
    if not item_id or not code or not name:
        raise ValueError(f"item missing id/code/name: {str(item)[:150]}")
    columns = ["tenant_id", "source_item_id", "item_code", "barcode", "item_name", "sub_name",
               "item_size", "source_category_id", "source_brand_id", "source_department_id",
               "source_supplier_id", "source_unit_id", "status_code", "lifetime_code",
               "retail_price", "purchase_price", "source_version", "source_created_at",
               "source_modified_at", "source_approved_at", "list_hash", "detail_hash"]
    fields = [TENANT, item_id, code, value(item,"barcode"), name, value(item,"subName"),
              value(item,"size"), value(item,"itemClsId"), value(item,"itemBrandId"),
              value(item,"itemDepartmentId"), value(item,"supplierId"), value(item,"unitId"),
              value(item,"status"), value(item,"lifetime"), money(item,"retailPrice"),
              money(item,"purchasePrice"), value(item,"version"), dt(item,"gmtCreate"),
              dt(item,"gmtModified"), dt(item,"approveTime"),
              digest(item) if stream == "item_list" else None,
              digest(item) if stream == "item_detail" else None]
    update = [f"{c}=COALESCE(VALUES({c}),{c})" for c in columns[2:] if c not in ("list_hash","detail_hash")]
    update.append(f"{stream.replace('item_', '')}_hash=VALUES({stream.replace('item_', '')}_hash)")
    update.append("last_seen_at=NOW()")
    statement = (f"INSERT INTO products ({','.join(columns)}) VALUES ({','.join(['%s']*len(columns))}) "
                 f"ON DUPLICATE KEY UPDATE {','.join(update)}")
    return statement, fields


def upsert_product(conn, item, stream):
    statement, fields = product_statement(item, stream)
    sql(conn, statement, fields)
    item_id = value(item, "id")
    row = one(conn, "SELECT id FROM products WHERE tenant_id=%s AND source_item_id=%s", (TENANT,item_id))
    return row["id"]


def full_list(conn, run_id, rate):
    previous = one(conn, "SELECT cursor_json FROM sync_checkpoints WHERE tenant_id=%s AND stream='item_list'", (TENANT,))
    state = json.loads(previous["cursor_json"]) if previous else {}
    page = int(state.get("next_page", 1))
    expected = state.get("source_total")
    while True:
        started = time.monotonic()
        result = list_page(page)
        rows = result["results"]
        total = int(result["totalRecord"])
        if page == 1:
            expected = total
        if expected is not None and abs(total - expected) > PAGE_SIZE:
            raise RuntimeError(f"source total changed too much: {expected} -> {total}")
        if not rows and (page - 1) * PAGE_SIZE < total:
            raise RuntimeError(f"unexpected empty page {page}/{total}")
        capture_values = []
        product_values = []
        queue_values = []
        statement = None
        for item in rows:
            item_id = value(item, "id")
            capture_values.append((TENANT,"item_list",item_id,value(item,"version"),
                                   dt(item,"gmtModified"),digest(item),dump(item)))
            statement, fields = product_statement(item,"item_list")
            product_values.append(fields)
            queue_values.append((TENANT,item_id))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO source_capture (tenant_id,stream,source_key,source_version,source_modified_at,payload_hash,payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE source_version=VALUES(source_version),"
                "source_modified_at=VALUES(source_modified_at),payload_hash=VALUES(payload_hash),payload=VALUES(payload),captured_at=NOW()",
                capture_values)
            cur.executemany(statement,product_values)
            cur.executemany("INSERT INTO sync_objects (tenant_id,stream,source_key,status) VALUES (%s,'item_detail',%s,'pending') "
                "ON DUPLICATE KEY UPDATE source_key=VALUES(source_key)",queue_values)
        checkpoint(conn, "item_list", {"next_page":page+1,"source_total":total,"rows_on_last_page":len(rows)}, run_id)
        conn.commit()
        if page == 1 or page % 10 == 0 or len(rows) < PAGE_SIZE:
            print(f"list page={page} rows={len(rows)} source_total={total}", flush=True)
        if len(rows) < PAGE_SIZE:
            return total
        page += 1
        time.sleep(max(0, 1/rate - (time.monotonic()-started)))


def full_details(conn, run_id, rate, batch_size):
    done = 0
    failed = 0
    while True:
        rows = []
        with conn.cursor() as cur:
            cur.execute("SELECT source_key FROM sync_objects WHERE tenant_id=%s AND stream='item_detail' "
                        "AND status IN ('pending','failed') AND attempts < 3 ORDER BY source_key LIMIT %s",
                        (TENANT,batch_size))
            rows = cur.fetchall()
        if not rows:
            return done, failed
        ids = [r["source_key"] for r in rows]
        started = time.monotonic()
        try:
            results = detail_batch(ids,interval_seconds=1/rate)
        except Exception as e:
            conn.rollback()
            raise RuntimeError(f"detail batch starting {ids[0]} failed: {e}") from e
        good = []
        bad = []
        for result in results:
            item_id = str(result.get("id"))
            entity = result.get("entity")
            if result.get("code") == 0 and isinstance(entity,dict) and str(entity.get("id")) == item_id:
                if value(entity,"shopAdminId") not in (None,TENANT):
                    raise RuntimeError(f"tenant mismatch for item {item_id}")
                good.append((item_id,entity))
            else:
                error = str(result.get("error") or f"response code {result.get('code')}")[:1000]
                if "401" in error or "403" in error:
                    raise RuntimeError(f"authentication/permission failure on {item_id}: {error}")
                bad.append((error,TENANT,item_id))
        if good:
            product_values = []
            captures = []
            statuses = []
            statement = None
            for item_id,entity in good:
                statement,fields=product_statement(entity,"item_detail")
                product_values.append(fields)
                captures.append((TENANT,"item_detail",item_id,value(entity,"version"),
                                 dt(entity,"gmtModified"),digest(entity),dump(entity)))
                statuses.append((digest(entity),TENANT,item_id))
            with conn.cursor() as cur:
                cur.executemany(statement,product_values)
                placeholders=','.join(['%s']*len(good))
                cur.execute(f"SELECT id,source_item_id FROM products WHERE tenant_id=%s AND source_item_id IN ({placeholders})",
                            [TENANT]+[x[0] for x in good])
                product_ids={r["source_item_id"]:r["id"] for r in cur.fetchall()}
                profiles=[(product_ids[item_id],dump(entity),digest(entity)) for item_id,entity in good]
                cur.executemany("INSERT INTO source_capture (tenant_id,stream,source_key,source_version,source_modified_at,payload_hash,payload) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE source_version=VALUES(source_version),"
                    "source_modified_at=VALUES(source_modified_at),payload_hash=VALUES(payload_hash),payload=VALUES(payload),captured_at=NOW()",
                    captures)
                cur.executemany("INSERT INTO product_profile (product_id,payload,payload_hash) VALUES (%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE payload=VALUES(payload),payload_hash=VALUES(payload_hash)",profiles)
                cur.executemany("UPDATE sync_objects SET status='done',attempts=attempts+1,payload_hash=%s,"
                    "last_success_at=NOW(),last_error=NULL WHERE tenant_id=%s AND stream='item_detail' AND source_key=%s",statuses)
            done += len(good)
        if bad:
            with conn.cursor() as cur:
                cur.executemany("UPDATE sync_objects SET status='failed',attempts=attempts+1,last_error=%s "
                    "WHERE tenant_id=%s AND stream='item_detail' AND source_key=%s",bad)
            failed += len(bad)
        checkpoint(conn,"item_detail",{"last_id":ids[-1],"done_this_run":done,"failed_this_run":failed},run_id)
        conn.commit()
        if done % 100 < batch_size or failed:
            print(f"details done={done} failed={failed} last={ids[-1]}",flush=True)
        # Keep the boundary between detail batches at the same source rate
        # as requests within a batch.
        time.sleep(max(1/rate, len(ids)/rate-(time.monotonic()-started)))


def status(conn):
    for label, query in [
        ("products", "SELECT COUNT(*) AS n FROM products WHERE tenant_id=%s"),
        ("details_done", "SELECT COUNT(*) AS n FROM sync_objects WHERE tenant_id=%s AND stream='item_detail' AND status='done'"),
        ("details_pending", "SELECT COUNT(*) AS n FROM sync_objects WHERE tenant_id=%s AND stream='item_detail' AND status='pending'"),
        ("details_failed", "SELECT COUNT(*) AS n FROM sync_objects WHERE tenant_id=%s AND stream='item_detail' AND status='failed'")]:
        print(label,one(conn,query,(TENANT,))["n"])
    print("checkpoints",dump(one(conn,"SELECT stream,cursor_json,updated_at FROM sync_checkpoints WHERE tenant_id=%s ORDER BY updated_at DESC LIMIT 1",(TENANT,))))


def main():
    p=argparse.ArgumentParser()
    p.add_argument("command",choices=["migrate","full","status"])
    p.add_argument("--rate",type=float,default=0.5,help="maximum average browser API requests per second")
    p.add_argument("--batch-size",type=int,default=2)
    args=p.parse_args()
    if args.rate <= 0 or args.rate > 3 or args.batch_size < 1 or args.batch_size > 5:
        p.error("rate must be in (0,3] and batch-size in [1,5]")
    password_fd=os.environ.pop("KEMAI_PASSWORD_FD",None)
    if password_fd is None:
        password=getpass.getpass("MySQL password: ")
    else:
        fd=int(password_fd)
        with os.fdopen(fd,"r") as secret_stream:
            password=secret_stream.readline().rstrip("\n")
    conn=connect(password)
    try:
        if args.command == "migrate":
            migrate(conn)
        elif args.command == "status":
            status(conn)
        else:
            migrate(conn)
            run_id=run_start(conn,"full",args.rate)
            try:
                total=full_list(conn,run_id,args.rate)
                unique=one(conn,"SELECT COUNT(*) AS n FROM source_capture WHERE tenant_id=%s AND stream='item_list'",(TENANT,))["n"]
                if unique < total:
                    raise RuntimeError(f"list incomplete without rescan: captured={unique} source={total}")
                done,failed=full_details(conn,run_id,args.rate,args.batch_size)
                count=one(conn,"SELECT COUNT(*) AS n FROM sync_objects WHERE tenant_id=%s AND stream='item_detail' AND status='done'",(TENANT,))["n"]
                missing=one(conn,"SELECT COUNT(*) AS n FROM sync_objects WHERE tenant_id=%s AND stream='item_detail' AND status<>'done'",(TENANT,))["n"]
                if count < total or missing:
                    raise RuntimeError(f"reconciliation incomplete: source={total}, details_done={count}, missing={missing}")
                from kemai_sections import sync_sections
                sync_sections(conn,run_id,args.rate,args.batch_size)
                run_end(conn,run_id,"success")
                print(f"full item and tab sync success: {count} products",flush=True)
            except BaseException as e:
                try:
                    conn.rollback()
                    run_end(conn,run_id,"failed",e)
                except pymysql.Error:
                    recovery=connect(password)
                    try:
                        run_end(recovery,run_id,"failed",e)
                    finally:
                        recovery.close()
                raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
