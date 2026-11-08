#!/usr/bin/env python3
"""Refresh detail tabs for products changed in the latest item delta run."""

import getpass
import os

from kemai_sync import TENANT,connect
from kemai_sections import SECTIONS,sync_sections
from kemai_sync_log import ensure_tables,start_window,finish_success,finish_failure


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
            cur.execute("""SELECT id,started_at FROM sync_runs WHERE tenant_id=%s
                AND mode='item_incremental' AND status='success' ORDER BY id DESC LIMIT 1""",(TENANT,))
            run=cur.fetchone()
            if not run:
                raise RuntimeError('no successful item_incremental run')
            cur.execute("""SELECT c.source_key FROM source_capture c JOIN sync_objects o
                ON o.tenant_id=c.tenant_id AND o.stream='item_detail' AND o.source_key=c.source_key
                WHERE c.tenant_id=%s AND c.stream='item_list' AND c.captured_at>= %s
                AND o.status='done' ORDER BY c.source_key""",(TENANT,run['started_at']))
            ids=[r['source_key'] for r in cur.fetchall()]
        scan_day=run['started_at'].date()
        event_id=start_window(conn,'item_tabs_changed','incremental',scan_day,scan_day,run['id'])
        print(f'changed product tabs to refresh={len(ids)}',flush=True)
        if ids:
            sync_sections(conn,run['id'],rate=0.5,batch_size=2,only_ids=ids,session='kemai_delta_probe')
        with conn.cursor() as cur:
            if ids:
                placeholders=','.join(['%s']*len(ids))
                cur.execute("""SELECT COUNT(*) AS n FROM sync_objects WHERE tenant_id=%s
                    AND stream LIKE 'item_tab_%%' AND status<>'done'
                    AND source_key IN ("""+placeholders+")",[TENANT,*ids])
                incomplete=cur.fetchone()['n']
            else:
                incomplete=0
            cur.execute("""SELECT COUNT(*) AS n FROM sync_objects WHERE tenant_id=%s
                AND stream LIKE 'item_tab_%%' AND status IN ('pending','failed')""",(TENANT,))
            initial_pending=cur.fetchone()['n']
        if incomplete:
            raise RuntimeError(f'{incomplete} changed product tab objects remain incomplete')
        finish_success(conn,event_id,'item_tabs_changed',scan_day,len(ids)*len(SECTIONS),
                       'changed_product_tab_objects','changed_only',
                       f'本轮变化商品 {len(ids)} 个的 {len(SECTIONS)} 类页签已刷新；初始待补对象 {initial_pending}',
                       source_rows=len(ids))
        conn.commit()
    except BaseException as error:
        conn.rollback()
        if event_id is not None:
            finish_failure(conn,event_id,'item_tabs_changed',error)
        raise
    finally:
        conn.close()


if __name__=='__main__':
    main()
