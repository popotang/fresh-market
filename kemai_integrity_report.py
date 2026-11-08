#!/usr/bin/env python3
"""Read-only duplicate, orphan, checkpoint, and coverage report for Kemai sync."""

import argparse
import getpass
import json
import os
from pathlib import Path

from kemai_sync import TENANT,connect


def rows(conn,statement,params=(TENANT,)):
    with conn.cursor() as cur:
        cur.execute(statement,params)
        return cur.fetchall()


def scalar(conn,statement,params=(TENANT,)):
    result=rows(conn,statement,params)
    return int(result[0]["n"])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--strict",action="store_true")
    parser.add_argument("--output",type=Path,help="save the complete report as JSON")
    parser.add_argument("--summary",action="store_true",help="print counts without full gap details")
    args=parser.parse_args()
    secret_fd=os.environ.pop("KEMAI_PASSWORD_FD",None)
    if secret_fd is None:
        password=getpass.getpass("MySQL password: ")
    else:
        with os.fdopen(int(secret_fd),"r") as stream:
            password=stream.readline().rstrip("\n")
    conn=connect(password)
    try:
        count_queries={
            "products":"SELECT COUNT(*) AS n FROM products WHERE tenant_id=%s",
            "product_profile":"SELECT COUNT(*) AS n FROM product_profile x JOIN products p ON p.id=x.product_id WHERE p.tenant_id=%s",
            "product_child_records":"SELECT COUNT(*) AS n FROM product_child_records x JOIN products p ON p.id=x.product_id WHERE p.tenant_id=%s",
            "product_supply_qualifications":"SELECT COUNT(*) AS n FROM product_supply_qualifications x JOIN products p ON p.id=x.product_id WHERE p.tenant_id=%s",
            "product_branch_scopes":"SELECT COUNT(*) AS n FROM product_branch_scopes x JOIN products p ON p.id=x.product_id WHERE p.tenant_id=%s",
            "product_branch_prices":"SELECT COUNT(*) AS n FROM product_branch_prices x JOIN products p ON p.id=x.product_id WHERE p.tenant_id=%s",
            "product_branch_stocks":"SELECT COUNT(*) AS n FROM product_branch_stocks x JOIN products p ON p.id=x.product_id WHERE p.tenant_id=%s",
            "purchase_documents":"SELECT COUNT(*) AS n FROM purchase_documents WHERE tenant_id=%s",
            "purchase_document_lines":"SELECT COUNT(*) AS n FROM purchase_document_lines x JOIN purchase_documents p ON p.id=x.document_id WHERE p.tenant_id=%s",
            "purchase_document_branches":"SELECT COUNT(*) AS n FROM purchase_document_branches x JOIN purchase_documents p ON p.id=x.document_id WHERE p.tenant_id=%s",
            "purchase_document_approvals":"SELECT COUNT(*) AS n FROM purchase_document_approvals x JOIN purchase_documents p ON p.id=x.document_id WHERE p.tenant_id=%s",
            "purchase_receipt_history_lines":"SELECT COUNT(*) AS n FROM purchase_receipt_history_lines WHERE tenant_id=%s",
            "retail_orders":"SELECT COUNT(*) AS n FROM retail_orders WHERE tenant_id=%s",
            "retail_order_lines":"SELECT COUNT(*) AS n FROM retail_order_lines x JOIN retail_orders p ON p.id=x.order_id WHERE p.tenant_id=%s",
            "retail_payments":"SELECT COUNT(*) AS n FROM retail_payments x JOIN retail_orders p ON p.id=x.order_id WHERE p.tenant_id=%s",
            "retail_sale_flows":"SELECT COUNT(*) AS n FROM retail_sale_flows WHERE tenant_id=%s",
            "source_capture":"SELECT COUNT(*) AS n FROM source_capture WHERE tenant_id=%s",
        }
        table_counts={table:scalar(conn,query) for table,query in count_queries.items()}
        duplicate_checks={
            "products":scalar(conn,"SELECT COUNT(*) AS n FROM (SELECT source_item_id FROM products WHERE tenant_id=%s GROUP BY source_item_id HAVING COUNT(*)>1) x"),
            "product_children":scalar(conn,"SELECT COUNT(*) AS n FROM (SELECT c.product_id,c.section,c.source_key FROM product_child_records c JOIN products p ON p.id=c.product_id WHERE p.tenant_id=%s GROUP BY c.product_id,c.section,c.source_key HAVING COUNT(*)>1) x"),
            "source_capture":scalar(conn,"SELECT COUNT(*) AS n FROM (SELECT stream,source_key FROM source_capture WHERE tenant_id=%s GROUP BY stream,source_key HAVING COUNT(*)>1) x"),
            "purchase_documents":scalar(conn,"SELECT COUNT(*) AS n FROM (SELECT document_kind,source_document_id FROM purchase_documents WHERE tenant_id=%s GROUP BY document_kind,source_document_id HAVING COUNT(*)>1) x"),
            "purchase_lines":scalar(conn,"SELECT COUNT(*) AS n FROM (SELECT x.document_id,x.source_line_id FROM purchase_document_lines x JOIN purchase_documents p ON p.id=x.document_id WHERE p.tenant_id=%s GROUP BY x.document_id,x.source_line_id HAVING COUNT(*)>1) d"),
            "history_lines":scalar(conn,"SELECT COUNT(*) AS n FROM (SELECT source_line_key FROM purchase_receipt_history_lines WHERE tenant_id=%s GROUP BY source_line_key HAVING COUNT(*)>1) x"),
            "retail_orders":scalar(conn,"SELECT COUNT(*) AS n FROM (SELECT source_order_id FROM retail_orders WHERE tenant_id=%s GROUP BY source_order_id HAVING COUNT(*)>1) x"),
            "retail_order_lines":scalar(conn,"SELECT COUNT(*) AS n FROM (SELECT x.order_id,x.source_line_id FROM retail_order_lines x JOIN retail_orders p ON p.id=x.order_id WHERE p.tenant_id=%s GROUP BY x.order_id,x.source_line_id HAVING COUNT(*)>1) d"),
            "retail_payments":scalar(conn,"SELECT COUNT(*) AS n FROM (SELECT x.order_id,x.source_payment_id FROM retail_payments x JOIN retail_orders p ON p.id=x.order_id WHERE p.tenant_id=%s GROUP BY x.order_id,x.source_payment_id HAVING COUNT(*)>1) d"),
            "retail_flows":scalar(conn,"SELECT COUNT(*) AS n FROM (SELECT source_flow_id FROM retail_sale_flows WHERE tenant_id=%s GROUP BY source_flow_id HAVING COUNT(*)>1) x"),
        }
        orphan_checks={
            "product_profiles":scalar(conn,"SELECT COUNT(*) AS n FROM product_profile x LEFT JOIN products p ON p.id=x.product_id WHERE p.id IS NULL",()),
            "product_children":scalar(conn,"SELECT COUNT(*) AS n FROM product_child_records x LEFT JOIN products p ON p.id=x.product_id WHERE p.id IS NULL",()),
            "product_supply":scalar(conn,"SELECT COUNT(*) AS n FROM product_supply_qualifications x LEFT JOIN products p ON p.id=x.product_id WHERE p.id IS NULL",()),
            "product_scopes":scalar(conn,"SELECT COUNT(*) AS n FROM product_branch_scopes x LEFT JOIN products p ON p.id=x.product_id WHERE p.id IS NULL",()),
            "product_prices":scalar(conn,"SELECT COUNT(*) AS n FROM product_branch_prices x LEFT JOIN products p ON p.id=x.product_id WHERE p.id IS NULL",()),
            "product_stocks":scalar(conn,"SELECT COUNT(*) AS n FROM product_branch_stocks x LEFT JOIN products p ON p.id=x.product_id WHERE p.id IS NULL",()),
            "purchase_lines":scalar(conn,"SELECT COUNT(*) AS n FROM purchase_document_lines x LEFT JOIN purchase_documents p ON p.id=x.document_id WHERE p.id IS NULL",()),
            "purchase_branches":scalar(conn,"SELECT COUNT(*) AS n FROM purchase_document_branches x LEFT JOIN purchase_documents p ON p.id=x.document_id WHERE p.id IS NULL",()),
            "purchase_approvals":scalar(conn,"SELECT COUNT(*) AS n FROM purchase_document_approvals x LEFT JOIN purchase_documents p ON p.id=x.document_id WHERE p.id IS NULL",()),
            "retail_lines":scalar(conn,"SELECT COUNT(*) AS n FROM retail_order_lines x LEFT JOIN retail_orders p ON p.id=x.order_id WHERE p.id IS NULL",()),
            "retail_payments":scalar(conn,"SELECT COUNT(*) AS n FROM retail_payments x LEFT JOIN retail_orders p ON p.id=x.order_id WHERE p.id IS NULL",()),
        }
        source_counts=rows(conn,"SELECT stream,COUNT(*) AS n FROM source_capture WHERE tenant_id=%s GROUP BY stream ORDER BY stream")
        object_status=rows(conn,"SELECT stream,status,COUNT(*) AS n FROM sync_objects WHERE tenant_id=%s GROUP BY stream,status ORDER BY stream,status")
        checkpoints=rows(conn,"SELECT stream,cursor_json,updated_at FROM sync_checkpoints WHERE tenant_id=%s ORDER BY stream")
        gaps=rows(conn,"SELECT stream,scope_key,missing_parts,source_row_count,status,last_seen_at FROM sync_coverage_gaps WHERE tenant_id=%s ORDER BY stream,scope_key")
        latest_runs=rows(conn,"""SELECT r.mode,r.status,r.started_at,r.finished_at,r.last_error FROM sync_runs r
            JOIN (SELECT mode,MAX(id) id FROM sync_runs WHERE tenant_id=%s GROUP BY mode) x ON x.id=r.id ORDER BY r.mode""")
        failures={"duplicates":{k:v for k,v in duplicate_checks.items() if v},
                  "orphans":{k:v for k,v in orphan_checks.items() if v}}
        report={"tenant_id":TENANT,"table_counts":table_counts,"duplicates":duplicate_checks,
                "orphans":orphan_checks,"source_counts":source_counts,"sync_objects":object_status,
                "checkpoints":checkpoints,"coverage_gaps":gaps,"latest_runs":latest_runs,
                "strict_failures":failures}
        if args.output:
            temp=args.output.with_suffix(args.output.suffix+".tmp")
            temp.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n")
            os.chmod(temp,0o600)
            temp.replace(args.output)
        display=({"tenant_id":TENANT,"table_counts":table_counts,
                  "duplicates":duplicate_checks,"orphans":orphan_checks,
                  "source_streams":len(source_counts),"object_status_groups":len(object_status),
                  "checkpoints":len(checkpoints),"coverage_gaps":len(gaps),
                  "latest_runs":{row["mode"]:row["status"] for row in latest_runs},
                  "strict_failures":failures} if args.summary else report)
        print(json.dumps(display,ensure_ascii=False,indent=2,default=str))
        if args.strict and any(failures.values()):
            raise SystemExit(2)
    finally:
        conn.close()


if __name__=="__main__":
    main()
