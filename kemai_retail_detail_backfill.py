#!/usr/bin/env python3
"""Resume retail order detail and payment capture, at most 0.5 SaaS calls/second."""

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from kemai_commerce_sync import capture, ensure_connection_after_fetch, pause, retail_order
from kemai_rate_limit import honor_cooldown, is_rate_limited, record_limit
from kemai_source_gate import source_lease
from kemai_sync import TENANT, connect, one, sql
from kemai_sync_log import ensure_tables, finish_failure, finish_success, start_window


ROOT = Path(__file__).resolve().parent
STATE = ROOT / "kemai_retail_detail_status.json"
LOG = ROOT / "kemai_retail_detail_backfill.log"
DAILY = ROOT / "kemai_daily_status.json"
STREAM = "retail-payments"
MAX_RATE = 0.5
SESSION = "kemai_retail_detail"
SOURCE_PAGE = "https://yunfan.kemai.com.cn/retail/smallTicketList"


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save(state):
    state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    temporary = STATE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(STATE)


def ensure_state_table(conn):
    sql(conn, """CREATE TABLE IF NOT EXISTS retail_order_detail_state (
        order_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
        status VARCHAR(16) NOT NULL,
        attempts INT UNSIGNED NOT NULL DEFAULT 0,
        line_count INT UNSIGNED NULL,
        payment_count INT UNSIGNED NULL,
        last_error TEXT NULL,
        attempted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at DATETIME NULL,
        KEY idx_retail_detail_state_status (status, attempted_at),
        CONSTRAINT fk_retail_detail_state_order FOREIGN KEY (order_id)
          REFERENCES retail_orders(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    conn.commit()


def open_source_session():
    with source_lease("browser:retail-detail:open"):
        result = subprocess.run(["opencli", "browser", SESSION, "open", SOURCE_PAGE,
                                 "--window", "background"],
                                capture_output=True, text=True, timeout=45)
        if result.returncode:
            if is_rate_limited(result.stdout + result.stderr):
                record_limit("retail-detail-open", result.stdout + result.stderr, wait=False)
            raise RuntimeError(f"OpenCLI retail session open failed: {(result.stdout + result.stderr)[-500:]}")


def fetch_detail(source_id, sold_at):
    expression = """(async()=>{
      const id=__ID__,saleTime=__TIME__;
      const v=[...document.querySelectorAll('#singlePageDiv *')]
        .find(e=>e.__vue__?.$options?.name==='smallTicketList')?.__vue__;
      if(!v) return {ok:false,error:'零售订单页面组件未就绪'};
      v.entity=null;
      v.OnEditEntityDetails({id,gmtSale:saleTime});
      for(let i=0;i<150;i++){
        if(String(v.entity?.id)===id) return {ok:true,entity:v.entity};
        if(v.entity&&Object.keys(v.entity).length===0){
          return {ok:false,error:document.querySelector('.ivu-modal-confirm-body')?.innerText||'详情接口未返回订单'};
        }
        const text=document.body?.innerText||'';
        if(text.includes('操作太频繁')||text.includes('稍后再试')) return {ok:false,error:'您的操作太频繁，请稍后再试'};
        await new Promise(resolve=>setTimeout(resolve,100));
      }
      return {ok:false,error:'零售详情等待超时'};
    })()""".replace("__ID__", json.dumps(str(source_id))).replace(
        "__TIME__", json.dumps(sold_at.strftime("%Y-%m-%d %H:%M:%S")))
    with source_lease("browser:retail-detail:eval"):
        result = subprocess.run(["opencli", "browser", SESSION, "eval", expression],
                                capture_output=True, text=True, timeout=35)
        if result.returncode:
            message = (result.stdout + result.stderr)[-700:]
            if is_rate_limited(message):
                record_limit("retail-detail", message, wait=False)
            raise RuntimeError(f"OpenCLI retail detail failed: {message}")
        try:
            response = json.loads(result.stdout)
        except ValueError as error:
            raise RuntimeError("OpenCLI retail detail returned invalid JSON") from error
        if not response.get("ok"):
            message = str(response.get("error") or "unknown source error")
            if is_rate_limited(message):
                record_limit("retail-detail", message, wait=False)
            raise RuntimeError(f"Kemai retail detail unavailable: {message}")
        return response["entity"]


def wait_for_daily():
    while read_json(DAILY).get("status") == "running":
        print("daily sync is running; retail detail backfill is waiting", flush=True)
        time.sleep(30)


def source_days(conn, from_day, to_day):
    with conn.cursor() as cur:
        cur.execute("""SELECT DATE(sold_at) AS day FROM retail_orders
            WHERE tenant_id=%s AND sold_at>=%s AND sold_at<%s
            GROUP BY DATE(sold_at) ORDER BY day""",
            (TENANT, from_day, to_day + timedelta(days=1)))
        return [row["day"] for row in cur.fetchall()]


def pending_orders(conn, day):
    with conn.cursor() as cur:
        cur.execute("""SELECT o.id,o.source_order_id,o.sold_at,COALESCE(s.attempts,0) AS attempts
            FROM retail_orders o
            LEFT JOIN retail_order_detail_state s ON s.order_id=o.id AND s.status='success'
            WHERE o.tenant_id=%s AND o.sold_at>=%s AND o.sold_at<%s
            AND s.order_id IS NULL ORDER BY o.id""",
            (TENANT, day, day + timedelta(days=1)))
        return cur.fetchall()


def process_order(conn, order, rate):
    started = time.monotonic()
    try:
        detail = fetch_detail(order["source_order_id"], order["sold_at"])
    finally:
        pause(started, rate)
    if str(detail.get("id")) != str(order["source_order_id"]):
        raise RuntimeError("retail detail returned a different order ID")
    if not all(isinstance(detail.get(field), list) for field in ("orderDetails", "payFlows")):
        raise RuntimeError("retail detail lacks item/payment arrays")
    ensure_connection_after_fetch(conn)
    retail_order(conn, detail)
    capture(conn, "retail-order-detail", detail)
    sql(conn, """INSERT INTO retail_order_detail_state
        (order_id,status,attempts,line_count,payment_count,last_error,attempted_at,completed_at)
        VALUES (%s,'success',1,%s,%s,NULL,NOW(),NOW())
        ON DUPLICATE KEY UPDATE status='success',attempts=attempts+1,
        line_count=VALUES(line_count),payment_count=VALUES(payment_count),
        last_error=NULL,attempted_at=NOW(),completed_at=NOW()""",
        (order["id"], len(detail["orderDetails"]), len(detail["payFlows"])))
    conn.commit()
    return len(detail["payFlows"])


def mark_error(conn, order, error):
    sql(conn, """INSERT INTO retail_order_detail_state
        (order_id,status,attempts,last_error,attempted_at)
        VALUES (%s,'failed',1,%s,NOW())
        ON DUPLICATE KEY UPDATE status='failed',attempts=attempts+1,
        last_error=VALUES(last_error),attempted_at=NOW()""",
        (order["id"], str(error)[:4000]))
    conn.commit()


def plan(conn, from_day, to_day, rate):
    with conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) AS orders,
            SUM(CASE WHEN s.status='success' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN s.status='failed' THEN 1 ELSE 0 END) AS failed,
            COUNT(DISTINCT DATE(o.sold_at)) AS source_days
            FROM retail_orders o LEFT JOIN retail_order_detail_state s ON s.order_id=o.id
            WHERE o.tenant_id=%s AND o.sold_at>=%s AND o.sold_at<%s""",
            (TENANT, from_day, to_day + timedelta(days=1)))
        result = cur.fetchone()
    remaining = result["orders"] - int(result["completed"] or 0)
    return {"from": from_day.isoformat(), "to": to_day.isoformat(),
            "orders": result["orders"], "completed": int(result["completed"] or 0),
            "failed": int(result["failed"] or 0), "remaining": remaining,
            "source_days": result["source_days"], "rate_cap_per_second": rate,
            "minimum_hours_at_rate_cap": round(remaining / rate / 3600, 1),
            "source_requests_in_plan": 0}


def run(conn, from_day, to_day, rate):
    ensure_tables(conn)
    ensure_state_table(conn)
    lock = f"kemai_retail_detail:{TENANT}"
    if one(conn, "SELECT GET_LOCK(%s,0) AS acquired", (lock,))["acquired"] != 1:
        raise RuntimeError("another retail detail worker is running")
    conn._kemai_lock_name = lock
    wait_for_daily()
    honor_cooldown()
    open_source_session()
    state = {"pid": os.getpid(), "status": "running", "rate": rate,
             "from": from_day.isoformat(), "to": to_day.isoformat(),
             "started_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    save(state)
    total_errors = 0
    try:
        for day in source_days(conn, from_day, to_day):
            ensure_connection_after_fetch(conn)
            orders = pending_orders(conn, day)
            if not orders:
                continue
            source_total = one(conn, """SELECT COUNT(*) AS n FROM retail_orders
                WHERE tenant_id=%s AND sold_at>=%s AND sold_at<%s""",
                (TENANT, day, day + timedelta(days=1)))["n"]
            state.update({"current_day": day.isoformat(), "day_orders": source_total,
                          "day_pending": len(orders), "last_error": None})
            save(state)
            event_id = start_window(conn, STREAM, "detail_backfill", day, day)
            try:
                day_errors = 0
                for index, order in enumerate(orders, 1):
                    wait_for_daily()
                    honor_cooldown()
                    wait_for_daily()
                    try:
                        process_order(conn, order, rate)
                    except Exception as error:
                        conn.rollback()
                        ensure_connection_after_fetch(conn)
                        mark_error(conn, order, error)
                        day_errors += 1
                        total_errors += 1
                        state.update({"last_error": str(error)[:1000],
                                      "failed_order_id": order["id"]})
                        save(state)
                        print(f"retail detail {day} order {order['id']} unavailable: {error}", flush=True)
                        if is_rate_limited(str(error)) or total_errors >= 20:
                            raise RuntimeError("rate limited or too many detail errors; stop this run") from error
                    if index % 20 == 0 or index == len(orders):
                        state.update({"day_done_this_run": index,
                                      "last_order_id": order["id"]})
                        save(state)
                        print(f"retail detail {day} {index}/{len(orders)}", flush=True)
                completed = one(conn, """SELECT COUNT(*) AS orders,
                    COALESCE(SUM(s.payment_count),0) AS payments FROM retail_orders o
                    JOIN retail_order_detail_state s ON s.order_id=o.id AND s.status='success'
                    WHERE o.tenant_id=%s AND o.sold_at>=%s AND o.sold_at<%s""",
                    (TENANT, day, day + timedelta(days=1)))
                if completed["orders"] == source_total and day_errors == 0:
                    sql(conn, """UPDATE sync_coverage_gaps SET status='resolved',last_seen_at=NOW()
                        WHERE tenant_id=%s AND stream='retail-orders' AND scope_key=%s""",
                        (TENANT, day.isoformat()))
                    finish_success(conn, event_id, STREAM, day, completed["payments"],
                                   "all_order_details_checked", "day_complete_historical_pending",
                                   f"{source_total} 笔订单详情、{completed['payments']} 条支付；该日期已完整，其他日期按日志核查", source_total)
                    conn.commit()
                    state.update({"last_complete_day": day.isoformat(),
                                  "day_payment_rows": completed["payments"],
                                  "day_done_this_run": len(orders)})
                    save(state)
                    print(f"retail detail {day} complete orders={source_total} payments={completed['payments']}", flush=True)
                else:
                    sql(conn, """UPDATE sync_stream_events SET source_rows=%s,saved_rows=%s,
                        coverage_level='partial',coverage_note=%s WHERE id=%s""",
                        (source_total, completed["payments"],
                         f"{completed['orders']}/{source_total} 笔订单详情完成；{day_errors} 笔本轮不可访问", event_id))
                    finish_failure(conn, event_id, STREAM,
                                   f"{day.isoformat()} 详情只完成 {completed['orders']}/{source_total} 笔")
                    print(f"retail detail {day} partial {completed['orders']}/{source_total}", flush=True)
            except BaseException as error:
                conn.rollback()
                finish_failure(conn, event_id, STREAM, error)
                state.update({"status": "stopped_on_error", "last_error": str(error)[:1000]})
                save(state)
                print(f"retail detail {day} failed: {error}", flush=True)
                return
        remaining = plan(conn, from_day, to_day, rate)["remaining"]
        state.update({"status": "complete" if remaining == 0 else "partial",
                      "remaining_orders": remaining, "total_errors": total_errors})
        save(state)
    finally:
        sql(conn, "SELECT RELEASE_LOCK(%s)", (lock,))
        conn.close()


def launch(from_day, to_day, rate):
    previous = read_json(STATE)
    pid = previous.get("pid")
    if pid and previous.get("status") == "running":
        try:
            os.kill(pid, 0)
            raise SystemExit(f"retail detail worker already running pid={pid}")
        except ProcessLookupError:
            pass
    password = getpass.getpass("MySQL password: ")
    read_fd, write_fd = os.pipe()
    environment = os.environ.copy()
    environment["KEMAI_PASSWORD_FD"] = str(read_fd)
    log_fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "work",
                                    "--from", from_day.isoformat(), "--to", to_day.isoformat(),
                                    "--rate", str(rate)],
                                   cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log_fd, stderr=log_fd,
                                   pass_fds=(read_fd,), start_new_session=True)
        os.close(read_fd)
        with os.fdopen(write_fd, "w") as stream:
            stream.write(password + "\n")
        print(f"started retail detail worker pid={process.pid}; status={STATE}")
    finally:
        os.close(log_fd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "start", "work"])
    parser.add_argument("--from", dest="from_day", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="to_day", type=date.fromisoformat, required=True)
    parser.add_argument("--rate", type=float, default=MAX_RATE)
    args = parser.parse_args()
    if args.from_day > args.to_day or args.to_day >= date.today():
        parser.error("date window must end before today")
    if not 0 < args.rate <= MAX_RATE:
        parser.error("--rate must be in (0, 0.5]")
    if args.action == "work":
        fd = int(os.environ.pop("KEMAI_PASSWORD_FD"))
        with os.fdopen(fd, "r") as stream:
            run(connect(stream.readline().rstrip("\n")), args.from_day, args.to_day, args.rate)
    elif args.action == "start":
        launch(args.from_day, args.to_day, args.rate)
    else:
        conn = connect(getpass.getpass("MySQL password: "))
        try:
            print(json.dumps(plan(conn, args.from_day, args.to_day, args.rate), ensure_ascii=False))
        finally:
            conn.close()
