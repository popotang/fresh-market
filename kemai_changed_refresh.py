#!/usr/bin/env python3
"""Refresh details for changed items found by the latest item list delta run."""

import getpass
import os

from kemai_sync import TENANT,connect,detail_batch,digest,dt,dump,product_statement,value
from kemai_sync_log import ensure_tables,start_window,finish_success,finish_failure

RATE=0.5


def main():
    secret_fd=os.environ.pop('KEMAI_PASSWORD_FD',None)
    if secret_fd is None:
        password=getpass.getpass('MySQL password: ')
    else:
        with os.fdopen(int(secret_fd),'r') as stream:
            password=stream.readline().rstrip('\n')
    conn=connect(password)
    event_id=None
    try:
        ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute("""SELECT id,started_at FROM sync_runs WHERE tenant_id=%s AND mode='item_incremental'
                AND status='success' ORDER BY id DESC LIMIT 1""",(TENANT,))
            run=cur.fetchone()
            if not run:
                raise RuntimeError('no successful item_incremental run')
            cur.execute("""UPDATE sync_objects o JOIN source_capture c
                ON c.tenant_id=o.tenant_id AND c.stream='item_list' AND c.source_key=o.source_key
                SET o.status='pending' WHERE o.tenant_id=%s AND o.stream='item_detail'
                AND o.status='refreshing' AND c.captured_at>= %s""",(TENANT,run['started_at']))
            cur.execute("""SELECT c.source_key FROM source_capture c JOIN sync_objects o
                ON o.tenant_id=c.tenant_id AND o.stream='item_detail' AND o.source_key=c.source_key
                WHERE c.tenant_id=%s AND c.stream='item_list' AND c.captured_at>= %s
                AND o.status='pending' ORDER BY c.source_key""",(TENANT,run['started_at']))
            ids=[r['source_key'] for r in cur.fetchall()]
        conn.commit()
        scan_day=run['started_at'].date()
        event_id=start_window(conn,'item_detail','incremental',scan_day,scan_day,run['id'])
        print(f'changed items to refresh={len(ids)}',flush=True)
        for offset in range(0,len(ids),2):
            batch=ids[offset:offset+2]
            try:
                with conn.cursor() as cur:
                    for item_id in batch:
                        cur.execute("""UPDATE sync_objects SET status='refreshing' WHERE tenant_id=%s
                            AND stream='item_detail' AND source_key=%s AND status='pending'""",
                            (TENANT,item_id))
                conn.commit()
                results=detail_batch(batch,'kemai_delta',interval_seconds=1/RATE)
                with conn.cursor() as cur:
                    for result in results:
                        item_id=str(result.get('id'))
                        entity=result.get('entity')
                        if result.get('code')!=0 or not isinstance(entity,dict) or str(entity.get('id'))!=item_id:
                            raise RuntimeError(f'bad detail response for {item_id}')
                        if value(entity,'shopAdminId') not in (None,TENANT):
                            raise RuntimeError(f'tenant mismatch for {item_id}')
                        statement,fields=product_statement(entity,'item_detail')
                        cur.execute(statement,fields)
                        cur.execute("SELECT id FROM products WHERE tenant_id=%s AND source_item_id=%s",(TENANT,item_id))
                        product_id=cur.fetchone()['id']
                        cur.execute("""INSERT INTO source_capture
                            (tenant_id,stream,source_key,source_version,source_modified_at,payload_hash,payload)
                            VALUES (%s,'item_detail',%s,%s,%s,%s,%s)
                            ON DUPLICATE KEY UPDATE source_version=VALUES(source_version),
                            source_modified_at=VALUES(source_modified_at),payload_hash=VALUES(payload_hash),
                            payload=VALUES(payload),captured_at=NOW()""",
                            (TENANT,item_id,value(entity,'version'),dt(entity,'gmtModified'),digest(entity),dump(entity)))
                        cur.execute("""INSERT INTO product_profile (product_id,payload,payload_hash)
                            VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE payload=VALUES(payload),
                            payload_hash=VALUES(payload_hash)""",(product_id,dump(entity),digest(entity)))
                        cur.execute("""UPDATE sync_objects SET status='done',attempts=attempts+1,
                            payload_hash=%s,last_success_at=NOW(),last_error=NULL
                            WHERE tenant_id=%s AND stream='item_detail' AND source_key=%s""",
                            (digest(entity),TENANT,item_id))
                conn.commit()
                print(f'refreshed details {offset+len(batch)}/{len(ids)}',flush=True)
            except BaseException:
                conn.rollback()
                with conn.cursor() as cur:
                    for item_id in batch:
                        cur.execute("""UPDATE sync_objects SET status='pending' WHERE tenant_id=%s
                            AND stream='item_detail' AND source_key=%s AND status='refreshing'""",(TENANT,item_id))
                conn.commit()
                raise
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM products WHERE tenant_id=%s",(TENANT,))
            product_count=cur.fetchone()['n']
            cur.execute("""SELECT COUNT(*) AS n FROM product_profile p
                JOIN products i ON i.id=p.product_id WHERE i.tenant_id=%s""",(TENANT,))
            profile_count=cur.fetchone()['n']
        full=product_count==profile_count
        finish_success(conn,event_id,'item_detail',scan_day,len(ids),'detail_capture_and_profile_count',
                       'full_catalog' if full else 'changed_only',
                       f'本轮刷新 {len(ids)} 个变化商品；基础详情覆盖 {profile_count}/{product_count}')
        conn.commit()
    except BaseException as error:
        conn.rollback()
        if event_id is not None:
            finish_failure(conn,event_id,'item_detail',error)
        raise
    finally:
        conn.close()


if __name__=='__main__':
    main()
