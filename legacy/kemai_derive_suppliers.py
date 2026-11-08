#!/usr/bin/env python3
"""Populate supplier references observed in the full item-list capture."""

import getpass
import os

from kemai_sync import TENANT, connect
from kemai_sync_log import ensure_tables,start_window,finish_success,finish_failure


def main():
    secret_fd=os.environ.pop('KEMAI_PASSWORD_FD',None)
    if secret_fd is None:
        password=getpass.getpass("MySQL password: ")
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
                raise RuntimeError('no successful product-list scan for supplier derivation')
            day=run['started_at'].date()
            event_id=start_window(conn,'suppliers','derived',day,day,run['id'])
            cur.execute("SELECT JSON_UNQUOTE(JSON_EXTRACT(payload,'$.supplierId')) AS source_id,"
                        "MAX(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.supplierCode'))) AS code,"
                        "MAX(JSON_UNQUOTE(JSON_EXTRACT(payload,'$.supplierName'))) AS name "
                        "FROM source_capture WHERE tenant_id=%s AND stream='item_list' "
                        "AND JSON_EXTRACT(payload,'$.supplierId') IS NOT NULL "
                        "GROUP BY source_id",(TENANT,))
            rows=[(TENANT,r["source_id"],r["code"],r["name"]) for r in cur.fetchall() if r["source_id"] not in (None,"","null")]
            cur.executemany("INSERT INTO suppliers (tenant_id,source_supplier_id,supplier_code,supplier_name) "
                            "VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE supplier_code=VALUES(supplier_code),"
                            "supplier_name=VALUES(supplier_name)",rows)
            finish_success(conn,event_id,'suppliers',day,len(rows),'derived_from_product_capture',
                           'observed_only','仅包含商品清单引用的供应商，未覆盖完整供应商名录')
            conn.commit()
            print(f"suppliers observed in product list: {len(rows)}",flush=True)
    except BaseException as error:
        conn.rollback()
        if event_id is not None:
            finish_failure(conn,event_id,'suppliers',error)
        raise
    finally:
        conn.close()


if __name__=="__main__":
    main()
