#!/usr/bin/env python3
"""Materialize retail order lines from already captured sale flows, without SaaS calls."""

import argparse
import getpass
import json
import os
from datetime import date, timedelta

from kemai_sync import TENANT, connect, one, sql
from kemai_sync_log import ensure_tables, start_window, finish_success, finish_failure


STREAM = "retail-order-lines"
BATCH = 5000


def ensure_provenance(conn):
    found = one(conn, """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='retail_order_lines'
        AND COLUMN_NAME='source_kind'""")["n"]
    if not found:
        sql(conn, """ALTER TABLE retail_order_lines ADD COLUMN source_kind
            VARCHAR(24) NOT NULL DEFAULT 'detail_api'
            COMMENT 'detail_api:原订单详情; sales_flow:逐商品销售流水映射'""")
        conn.commit()


def materialize(conn, from_day, to_day, *, allow_orphans=False, stats=None):
    end_exclusive = to_day + timedelta(days=1)
    expected = one(conn, """SELECT COUNT(*) AS n FROM retail_sale_flows f
        JOIN retail_orders o ON o.tenant_id=f.tenant_id
        AND o.source_order_id=f.source_order_id
        WHERE f.tenant_id=%s AND f.sold_at>=%s AND f.sold_at<%s""",
        (TENANT, from_day, end_exclusive))["n"]
    orphan = one(conn, """SELECT COUNT(*) AS n FROM retail_sale_flows f
        LEFT JOIN retail_orders o ON o.tenant_id=f.tenant_id
        AND o.source_order_id=f.source_order_id
        WHERE f.tenant_id=%s AND f.sold_at>=%s AND f.sold_at<%s
        AND o.id IS NULL""", (TENANT, from_day, end_exclusive))["n"]
    if stats is not None:
        stats["unmatched_flows"] = int(orphan)
    if orphan and not allow_orphans:
        raise RuntimeError(f"retail sale flows without order: {orphan}")
    cursor = 0
    while True:
        bound = one(conn, """SELECT MAX(id) AS id FROM
            (SELECT id FROM retail_sale_flows WHERE tenant_id=%s AND sold_at>=%s
             AND sold_at<%s AND id>%s ORDER BY id LIMIT %s) AS batch""",
            (TENANT, from_day, end_exclusive, cursor, BATCH))["id"]
        if bound is None:
            break
        sql(conn, """INSERT INTO retail_order_lines
            (order_id,source_line_id,source_item_id,source_line_ref_id,
             quantity,sale_price,sale_amount,tax_amount,payload,source_kind)
            SELECT o.id,f.source_flow_id,f.source_item_id,NULL,
                   f.quantity,
                   CAST(JSON_UNQUOTE(JSON_EXTRACT(f.payload,'$.salePrice')) AS DECIMAL(24,4))/10000,
                   f.sale_amount,
                   CAST(JSON_UNQUOTE(JSON_EXTRACT(f.payload,'$.taxAmt')) AS DECIMAL(24,4))/10000,
                   f.payload,'sales_flow'
            FROM retail_sale_flows f
            JOIN retail_orders o ON o.tenant_id=f.tenant_id
              AND o.source_order_id=f.source_order_id
            WHERE f.tenant_id=%s AND f.sold_at>=%s AND f.sold_at<%s
            AND f.id>%s AND f.id<=%s
            ON DUPLICATE KEY UPDATE
                source_item_id=IF(retail_order_lines.source_kind='sales_flow',VALUES(source_item_id),retail_order_lines.source_item_id),
                quantity=IF(retail_order_lines.source_kind='sales_flow',VALUES(quantity),retail_order_lines.quantity),
                sale_price=IF(retail_order_lines.source_kind='sales_flow',VALUES(sale_price),retail_order_lines.sale_price),
                sale_amount=IF(retail_order_lines.source_kind='sales_flow',VALUES(sale_amount),retail_order_lines.sale_amount),
                tax_amount=IF(retail_order_lines.source_kind='sales_flow',VALUES(tax_amount),retail_order_lines.tax_amount),
                payload=IF(retail_order_lines.source_kind='sales_flow',VALUES(payload),retail_order_lines.payload)""",
            (TENANT, from_day, end_exclusive, cursor, bound))
        conn.commit()
        cursor = bound
        print(f"retail line mapping through flow id {cursor}", flush=True)
    return verify(conn, from_day, to_day)


def verify(conn, from_day, to_day):
    end_exclusive = to_day + timedelta(days=1)
    expected = one(conn, """SELECT COUNT(*) AS n FROM retail_sale_flows f
        JOIN retail_orders o ON o.tenant_id=f.tenant_id
        AND o.source_order_id=f.source_order_id
        WHERE f.tenant_id=%s AND f.sold_at>=%s AND f.sold_at<%s""",
        (TENANT, from_day, end_exclusive))["n"]
    actual = one(conn, """SELECT COUNT(*) AS n FROM retail_sale_flows f
        JOIN retail_orders o ON o.tenant_id=f.tenant_id
          AND o.source_order_id=f.source_order_id
        JOIN retail_order_lines l ON l.order_id=o.id AND l.source_line_id=f.source_flow_id
        WHERE f.tenant_id=%s AND f.sold_at>=%s AND f.sold_at<%s""",
        (TENANT, from_day, end_exclusive))["n"]
    if actual != expected:
        raise RuntimeError(f"mapped retail lines {actual} != matched sale flows {expected}")
    unmatched = one(conn, """SELECT COUNT(*) AS n FROM retail_sale_flows f
        JOIN retail_orders o ON o.tenant_id=f.tenant_id AND o.source_order_id=f.source_order_id
        LEFT JOIN retail_order_lines l ON l.order_id=o.id AND l.source_line_id=f.source_flow_id
        WHERE f.tenant_id=%s AND f.sold_at>=%s AND f.sold_at<%s
        AND l.id IS NULL""", (TENANT, from_day, end_exclusive))["n"]
    if unmatched:
        raise RuntimeError(f"sale flows without mapped line: {unmatched}")
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_day", type=date.fromisoformat,
                        default=date(2026, 8, 1))
    parser.add_argument("--to", dest="to_day", type=date.fromisoformat,
                        default=date.today() - timedelta(days=1))
    parser.add_argument("--verify-only", action="store_true",
                        help="read-only mapping count check; no DB or SaaS writes")
    args = parser.parse_args()
    if args.from_day > args.to_day:
        parser.error("--from later than --to")
    secret_fd = os.environ.pop("KEMAI_PASSWORD_FD", None)
    if secret_fd is None:
        password = getpass.getpass("MySQL password: ")
    else:
        with os.fdopen(int(secret_fd), "r") as secret_stream:
            password = secret_stream.readline().rstrip("\n")
    conn = connect(password)
    event_id = None
    try:
        if args.verify_only:
            count = verify(conn, args.from_day, args.to_day)
            print(json.dumps({"stream": STREAM, "verified_rows": count,
                              "source_requests": 0, "db_writes": 0}, ensure_ascii=False))
            return
        ensure_tables(conn)
        ensure_provenance(conn)
        event_id = start_window(conn, STREAM, "derived_backfill",
                                args.from_day, args.to_day)
        count = materialize(conn, args.from_day, args.to_day)
        finish_success(conn, event_id, STREAM, args.to_day, count,
                       "source_flow_id_join_and_total", "flow_mapped",
                       "全部已采销售流水映射至订单商品行；原订单详情的额外字段仍待逐单采集", count)
        conn.commit()
        print(json.dumps({"stream": STREAM, "rows": count, "source_requests": 0},
                         ensure_ascii=False), flush=True)
    except BaseException as error:
        conn.rollback()
        if event_id is not None:
            finish_failure(conn, event_id, STREAM, error)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
