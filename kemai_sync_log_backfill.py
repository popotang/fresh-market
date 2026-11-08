#!/usr/bin/env python3
"""Reconstruct the latest verified stream state without calling Kemai."""

import argparse
import getpass
import json
from datetime import date
from pathlib import Path

from kemai_sync import TENANT, connect
from kemai_sync_log import STREAM_NAMES, ensure_tables


ROOT = Path(__file__).resolve().parent
COMMERCE = (
    "purchase-orders", "purchase-receipts", "purchase-returns",
    "retail-orders", "retail-sales", "purchase-receive-history",
)
MASTERS = {
    "branches": "分支机构", "departments": "部门", "categories": "商品类别",
    "brands": "品牌", "units": "计量单位", "suppliers": "供应商",
}


def read_report(name):
    return json.loads((ROOT / name).read_text())


def newest_product_capture(conn, prefix):
    with conn.cursor() as cur:
        if prefix == "item_detail":
            cur.execute("""SELECT MAX(captured_at) AS finished_at FROM source_capture
                WHERE tenant_id=%s AND stream='item_detail'""", (TENANT,))
        else:
            cur.execute("""SELECT MAX(captured_at) AS finished_at FROM source_capture
                WHERE tenant_id=%s AND stream LIKE 'item_tab_%%'""", (TENANT,))
        return cur.fetchone()["finished_at"]


def records(conn):
    result = []
    with conn.cursor() as cur:
        for stream in COMMERCE:
            cur.execute("""SELECT cursor_json,last_run_id FROM sync_checkpoints
                WHERE tenant_id=%s AND stream=%s""", (TENANT, "commerce_incremental:" + stream))
            checkpoint = cur.fetchone()
            if not checkpoint:
                raise RuntimeError(f"missing incremental checkpoint for {stream}")
            day = date.fromisoformat(json.loads(checkpoint["cursor_json"])["last_complete_day"])
            cur.execute("""SELECT id,status,parameters,stats,started_at,finished_at FROM sync_runs
                WHERE id=%s AND tenant_id=%s""", (checkpoint["last_run_id"], TENANT))
            run = cur.fetchone()
            if not run or run["status"] != "success" or not run["finished_at"]:
                raise RuntimeError(f"checkpoint for {stream} does not point to a successful run")
            stats = json.loads(run["stats"])
            params = json.loads(run["parameters"])
            if params.get("kinds") != [stream]:
                raise RuntimeError(f"run {run['id']} does not identify {stream} uniquely")
            count = stats[stream]
            audit = read_report(f"kemai_{stream.replace('-', '_')}_audit_2026-11-07.json") if stream != "purchase-receive-history" else read_report("kemai_history_reconciliation_2026-11-07.json")
            if stream == "purchase-receive-history":
                verified = not audit["issues"] and audit["incremental_day"] == day.isoformat() and audit["incremental_source_rows"] == count
            else:
                verified = not audit["issues"] and audit["checkpoint_day"] == day.isoformat()
            if not verified:
                raise RuntimeError(f"audit mismatch for {stream}")
            if stream == "retail-orders":
                coverage, note, method = "headers_only", "订单头已核对；商品行与支付流水待补", "export_total_and_capture"
            elif stream == "purchase-receive-history":
                coverage, note, method = "approved_rows_only", "已审核收货商品行；早期未审核单据未覆盖", "source_window_and_capture"
            elif stream == "retail-sales":
                coverage, note, method = "full_list", "逐商品销售流水已核对；历史 2026-10-11 为单轮计数", "export_total_and_capture"
            else:
                coverage, note, method = "full_detail", "本日列表与可访问单据详情已核对", "single_scan_and_capture"
            result.append(dict(stream=stream, mode="reconstructed", source_from=day, source_to=day,
                               run_id=run["id"], source_rows=count, saved_rows=count,
                               started_at=run["started_at"], finished_at=run["finished_at"],
                               verification=method, coverage=coverage, note=note,
                               provenance="incremental checkpoint + completed run + daily audit"))
        cur.execute("""SELECT id,stats,started_at,finished_at FROM sync_runs
            WHERE tenant_id=%s AND mode='item_incremental' AND status='success'
            ORDER BY id DESC LIMIT 1""", (TENANT,))
        product_run = cur.fetchone()
    product_audit = read_report("kemai_product_coverage_audit_2026-11-07.json")
    if not product_run or product_run["id"] != product_audit["latest_successful_incremental_run"] or product_audit["issues"]:
        raise RuntimeError("latest product run and audit differ")
    stats = json.loads(product_run["stats"])
    scan_day = product_run["started_at"].date()
    result.append(dict(stream="item_list", mode="reconstructed", source_from=scan_day,
                       source_to=scan_day, run_id=product_run["id"],
                       source_rows=stats["source_total"], saved_rows=stats["new"] + stats["changed"],
                       started_at=product_run["started_at"], finished_at=product_run["finished_at"],
                       verification="source_total_and_unique_ids", coverage="full_catalog",
                       note=f"商品清单 {stats['source_total']} 条；本轮新增 {stats['new']}、变化 {stats['changed']}",
                       provenance="successful product run + product coverage audit"))
    detail_finish = newest_product_capture(conn, "item_detail")
    tabs_finish = newest_product_capture(conn, "item_tabs")
    if not detail_finish or not tabs_finish:
        raise RuntimeError("missing product detail or tab captures")
    for stream, finished, source_rows, saved_rows, verification, coverage, note in (
        ("item_detail", detail_finish, stats["changed"] + stats["new"],
         stats["changed"] + stats["new"], "profile_count_and_capture", "full_catalog",
         f"基础详情 {product_audit['totals']['profiles']}/{stats['source_total']}；变化商品已刷新"),
        ("item_tabs_changed", tabs_finish, stats["changed"] + stats["new"],
         (stats["changed"] + stats["new"]) * 14, "changed_tab_status_audit", "changed_only",
         f"变化商品 14 类页签已刷新；初始待补对象 {product_audit['initial_tab_pending_or_failed']}")
    ):
        if finished < product_run["started_at"]:
            raise RuntimeError(f"{stream} capture predates product run")
        result.append(dict(stream=stream, mode="reconstructed", source_from=scan_day,
                           source_to=scan_day, run_id=product_run["id"], source_rows=source_rows,
                           saved_rows=saved_rows, started_at=product_run["started_at"],
                           finished_at=finished, verification=verification, coverage=coverage,
                           note=note, provenance="product capture timestamp + product coverage audit"))
    return result


def apply(conn, items):
    ensure_tables(conn)
    with conn.cursor() as cur:
        for item in items:
            cur.execute("""SELECT id FROM sync_stream_events WHERE tenant_id=%s AND stream=%s
                AND mode='reconstructed' AND run_id=%s AND source_to=%s LIMIT 1""",
                (TENANT, item["stream"], item["run_id"], item["source_to"]))
            found = cur.fetchone()
            if found:
                event_id = found["id"]
            else:
                cur.execute("""INSERT INTO sync_stream_events
                    (tenant_id,stream,mode,source_from,source_to,run_id,status,source_rows,
                     saved_rows,verification_method,coverage_level,coverage_note,started_at,
                     finished_at,details)
                    VALUES (%s,%s,%s,%s,%s,%s,'success',%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (TENANT,item["stream"],item["mode"],item["source_from"],item["source_to"],
                     item["run_id"],item["source_rows"],item["saved_rows"],item["verification"],
                     item["coverage"],item["note"],item["started_at"],item["finished_at"],
                     json.dumps({"provenance":item["provenance"]},ensure_ascii=False)))
                event_id = cur.lastrowid
            cur.execute("""INSERT INTO sync_stream_status
                (tenant_id,stream,stream_name,last_attempt_status,last_attempt_at,last_success_at,
                 covered_through,last_source_rows,last_saved_rows,verification_method,coverage_level,
                 coverage_note,last_event_id)
                VALUES (%s,%s,%s,'success',%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                 stream_name=VALUES(stream_name),
                 last_attempt_status=IF(last_success_at IS NULL OR last_success_at<=VALUES(last_success_at),
                                        VALUES(last_attempt_status),last_attempt_status),
                 last_attempt_at=IF(last_success_at IS NULL OR last_success_at<=VALUES(last_success_at),
                                    VALUES(last_attempt_at),last_attempt_at),
                 covered_through=IF(covered_through IS NULL,VALUES(covered_through),
                                    GREATEST(covered_through,VALUES(covered_through))),
                 last_source_rows=IF(last_success_at IS NULL OR last_success_at<=VALUES(last_success_at),
                                     VALUES(last_source_rows),last_source_rows),
                 last_saved_rows=IF(last_success_at IS NULL OR last_success_at<=VALUES(last_success_at),
                                    VALUES(last_saved_rows),last_saved_rows),
                 verification_method=IF(last_success_at IS NULL OR last_success_at<=VALUES(last_success_at),
                                        VALUES(verification_method),verification_method),
                 coverage_level=IF(last_success_at IS NULL OR last_success_at<=VALUES(last_success_at),
                                   VALUES(coverage_level),coverage_level),
                 coverage_note=IF(last_success_at IS NULL OR last_success_at<=VALUES(last_success_at),
                                  VALUES(coverage_note),coverage_note),
                 last_event_id=IF(last_success_at IS NULL OR last_success_at<=VALUES(last_success_at),
                                  VALUES(last_event_id),last_event_id),
                 last_success_at=GREATEST(COALESCE(last_success_at,VALUES(last_success_at)),
                                          VALUES(last_success_at))""",
                (TENANT,item["stream"],STREAM_NAMES[item["stream"]],item["started_at"],
                 item["finished_at"],item["source_to"],item["source_rows"],item["saved_rows"],
                 item["verification"],item["coverage"],item["note"],event_id))
        for stream, name in MASTERS.items():
            cur.execute("""INSERT INTO sync_stream_status
                (tenant_id,stream,stream_name,last_attempt_status,coverage_level,coverage_note)
                VALUES (%s,%s,%s,'not_scheduled','unknown','尚未接入每日增量；不能视为最新')
                ON DUPLICATE KEY UPDATE stream_name=VALUES(stream_name)""",(TENANT,stream,name))
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write reconstructed rows to MySQL")
    args = parser.parse_args()
    conn = connect(getpass.getpass("MySQL password: "))
    try:
        items = records(conn)
        for item in items:
            print(f"{item['stream']:26s} through={item['source_to']} "
                  f"source={item['source_rows']} written={item['saved_rows']} "
                  f"finished={item['finished_at']} coverage={item['coverage']}")
        print(f"master stream status rows checked: {len(MASTERS)}")
        if args.apply:
            apply(conn, items)
            print("reconstructed status applied; no Kemai requests sent")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
