"""Resumable product detail-tab extraction for Kemai Yunfan."""

import subprocess
import time
from decimal import Decimal, InvalidOperation

from kemai_sync import TENANT, browser, checkpoint, digest, dump, money, one, value
from kemai_rate_limit import honor_cooldown, is_rate_limited, record_limit
from kemai_source_gate import source_lease


SECTIONS = {
    "supply": ("/web/api/product/price/purchasePrice", {"querySource":"1"}),
    "retail_price": ("/web/api/product/price/retailPrice", {}),
    "delivery_price": ("/web/api/product/price/deliveryPrice", {}),
    "stock": ("/web/api/product/itemStock/query", {"surroundBranchStockQueryFlag":True}),
    "promotion": ("/web/api/product/item/promote", {}),
    "barcode": ("/web/api/product/barcode/attach", {}),
    "grading": ("/web/api/product/pack/freshGrading", {}),
    "pack": ("/web/api/product/pack/queryItemPack", {}),
    "history_price": ("/web/api/product/queryItemHistoryReceivePrice", None),
    "scope": ("/web/api/product/item/queryMustInBusinessScope", {}),
    "combination": ("/web/api/item/relation/combinationDetails", {}),
    "mix": ("/web/api/product/item/mix/component/getListData", {}),
    "split": ("/web/api/item/relation/splitDetails", {"type":"5"}),
    "cut": ("/web/api/item/relation/splitDetails", {"type":"2"}),
}


def session_request(expression, session):
    try:
        return browser(expression,session)
    except RuntimeError as error:
        message=str(error)
        if not any(token in message for token in ("webpackJsonp", "undefined", "Detached", "item list unavailable")):
            raise
        for attempt in range(2):
            honor_cooldown()
            with source_lease(f"browser:{session}:restore"):
                opened=subprocess.run(["opencli","browser",session,"open",
                                       "https://yunfan.kemai.com.cn/archives/ItemList"],
                                      capture_output=True,text=True,timeout=45)
                if is_rate_limited(opened.stdout+opened.stderr):
                    record_limit(f"item-tab-session-restore:{session}",
                                 (opened.stdout+opened.stderr)[-500:],wait=False)
            if is_rate_limited(opened.stdout+opened.stderr):
                continue
            if opened.returncode:
                raise RuntimeError(f"cannot restore {session}: {opened.stderr[-250:]}") from error
            time.sleep(2)
            return browser(expression,session)
        raise RuntimeError(f"rate limit repeated while restoring {session}") from error


def section_batch(section, ids, session="kemai_probe", interval_seconds=0):
    endpoint,params=SECTIONS[section]
    interval_ms=max(0,int(float(interval_seconds)*1000))
    setup=("if(!window.__kemai_req){let key='codex_sync_'+Date.now();"
           "window.webpackJsonp.push([[key],{[key]:(m,e,r)=>{window.__kemai_req=r}},[[key]]])}")
    payload=("{page:1,rows:10,params:{itemId:id,sourceSheetTypes:['PI','SX'],"
             "queryByAuthBranch:true,querySource:'1'}}" if section=="history_price"
             else "Object.assign({itemId:id},"+dump(params)+")")
    expr=("(async()=>{"+setup+"let out=[],ids="+dump(ids)+";for(let i=0;i<ids.length;i++)"
          "{let id=ids[i];if(i&&"+str(interval_ms)+")await new Promise(r=>setTimeout(r,"+str(interval_ms)+"));"
          "try{let a=await window.__kemai_req('66df').a.request({url:"+dump(endpoint)+
          ",data:"+payload+",method:'post',hideRuning:true});"
          "let m=a?.data?.message||a?.data?.msg||'',s=a?.response?.status||a?.status,c=a?.data?.code;"
          "out.push({id,code:c,message:m,rateLimited:/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test([s,c,m,document.body?.innerText||''].join(' ')),data:a?.data?.data})}"
          "catch(e){let m=e?.response?.data?.message||e?.response?.data?.msg||e?.message||String(e),"
          "s=e?.response?.status||e?.status,c=e?.response?.data?.code;out.push({id,error:m,status:s,code:c,"
          "rateLimited:/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test([s,c,m,document.body?.innerText||''].join(' '))})}}return out})()")
    for attempt in range(2):
        result=session_request(expr,session)
        if not isinstance(result,list) or len(result)!=len(ids):
            raise RuntimeError(f"invalid {section} batch: {str(result)[:180]}")
        limited=next((x for x in result if x.get("rateLimited") or is_rate_limited(x.get("error") or x.get("message") or "")),None)
        if not limited:
            return result
        record_limit(f"item-tab-{section}:{ids[0]}",limited)
        if attempt == 1:
            raise RuntimeError(f"rate limit repeated for {section} batch starting {ids[0]}")


def history_page(item_id, number, session="kemai_probe"):
    setup=("if(!window.__kemai_req){let key='codex_sync_'+Date.now();"
           "window.webpackJsonp.push([[key],{[key]:(m,e,r)=>{window.__kemai_req=r}},[[key]]])}")
    expr=("(async()=>{"+setup+"try{let a=await window.__kemai_req('66df').a.request({"
          "url:'/web/api/product/queryItemHistoryReceivePrice',"
          "data:{page:"+str(number)+",rows:10,params:{itemId:"+dump(item_id)+
          ",sourceSheetTypes:['PI','SX'],queryByAuthBranch:true,querySource:'1'}},"
          "method:'post',hideRuning:true});let m=a?.data?.message||a?.data?.msg||'',"
          "s=a?.response?.status||a?.status,c=a?.data?.code;return {code:c,message:m,"
          "rateLimited:/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test([s,c,m,document.body?.innerText||''].join(' ')),data:a?.data?.data}}"
          "catch(e){let m=e?.response?.data?.message||e?.response?.data?.msg||e?.message||String(e),"
          "s=e?.response?.status||e?.status,c=e?.response?.data?.code;return {error:m,status:s,code:c,"
          "rateLimited:/(?:您的)?操作太频繁|请稍后再试|too many requests|rate[ -]?limit|\\b429\\b/i.test([s,c,m,document.body?.innerText||''].join(' '))}}})()")
    for attempt in range(2):
        result=session_request(expr,session)
        if result.get("rateLimited") or is_rate_limited(result.get("error") or result.get("message") or ""):
            record_limit(f"item-tab-history-price:{item_id}:page:{number}",result)
            if attempt == 0:
                continue
            raise RuntimeError(f"rate limit repeated for history page {number} of {item_id}")
        if result.get("code")==0 and isinstance(result.get("data"),dict):
            return result["data"]
        raise RuntimeError(f"history page {number} failed for {item_id}: {str(result)[:180]}")


def rows_for(section, data):
    if isinstance(data,list):
        return data
    if isinstance(data,dict):
        if section in ("combination","split","cut"):
            return data.get("details") or []
        if isinstance(data.get("results"),list):
            return data["results"]
    return []


def quantity(row, field):
    raw=row.get(field)
    if raw in (None,""):
        return None
    try:
        return Decimal(str(raw))/10000
    except InvalidOperation:
        return None


def write_typed(cur, section, values):
    if not values:
        return
    if section=="supply":
        cur.executemany("INSERT INTO product_supply_qualifications "
            "(product_id,source_key,source_branch_id,source_supplier_id,barcode,unit_name,purchase_price,primary_flag,payload) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "purchase_price=VALUES(purchase_price),primary_flag=VALUES(primary_flag),payload=VALUES(payload),captured_at=NOW()",values)
    elif section=="scope":
        cur.executemany("INSERT INTO product_branch_scopes "
            "(product_id,source_key,source_branch_id,lifetime_code,logistics_mode,payload) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "lifetime_code=VALUES(lifetime_code),logistics_mode=VALUES(logistics_mode),payload=VALUES(payload),captured_at=NOW()",values)
    elif section in ("retail_price","delivery_price"):
        cur.executemany("INSERT INTO product_branch_prices "
            "(product_id,price_kind,source_key,source_branch_id,barcode,unit_name,retail_price,delivery_price,sales_price,member_price,payload) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "retail_price=VALUES(retail_price),delivery_price=VALUES(delivery_price),sales_price=VALUES(sales_price),"
            "member_price=VALUES(member_price),payload=VALUES(payload),captured_at=NOW()",values)
    elif section=="stock":
        cur.executemany("INSERT INTO product_branch_stocks "
            "(product_id,source_key,source_branch_id,stock_qty,cost_price,payload) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE stock_qty=VALUES(stock_qty),"
            "cost_price=VALUES(cost_price),payload=VALUES(payload),captured_at=NOW()",values)


def prepare_queues(conn, only_ids=None, sections=None):
    with conn.cursor() as cur:
        for section in (sections or SECTIONS):
            stream="item_tab_"+section
            if section in ("combination","mix"):
                types=("'1','4'" if section=="combination" else "'3'")
                query=("INSERT INTO sync_objects (tenant_id,stream,source_key,status) "
                       "SELECT p.tenant_id,%s,p.source_item_id,'pending' FROM products p "
                       "JOIN source_capture c ON c.tenant_id=p.tenant_id AND c.source_key=p.source_item_id "
                       "AND c.stream='item_list' WHERE p.tenant_id=%s "
                       f"AND JSON_UNQUOTE(JSON_EXTRACT(c.payload,'$.type')) IN ({types}) "
                       "ON DUPLICATE KEY UPDATE source_key=VALUES(source_key)")
            else:
                query=("INSERT INTO sync_objects (tenant_id,stream,source_key,status) "
                       "SELECT tenant_id,%s,source_item_id,'pending' FROM products WHERE tenant_id=%s "
                       "ON DUPLICATE KEY UPDATE source_key=VALUES(source_key)")
            params=[stream,TENANT]
            if only_ids:
                source_column="p.source_item_id" if section in ("combination","mix") else "source_item_id"
                placeholders=','.join(['%s']*len(only_ids))
                query=query.replace(" ON DUPLICATE KEY UPDATE",f" AND {source_column} IN ({placeholders}) ON DUPLICATE KEY UPDATE")
                params.extend(only_ids)
            cur.execute(query,params)
            conn.commit()
            print(f"queued {stream}",flush=True)


def sync_sections(conn, run_id, rate=0.5, batch_size=2, only_ids=None, session="kemai_probe", sections=None):
    prepare_queues(conn,only_ids,sections)
    for section in (sections or SECTIONS):
        stream="item_tab_"+section
        done=0
        while True:
            with conn.cursor() as cur:
                scoped=(" AND source_key IN ("+','.join(['%s']*len(only_ids))+")" if only_ids else "")
                cur.execute("SELECT source_key FROM sync_objects WHERE tenant_id=%s AND stream=%s "
                            "AND status IN ('pending','failed') AND attempts<3"+scoped+" ORDER BY source_key LIMIT %s",
                            [TENANT,stream,*(only_ids or []),batch_size])
                selected=cur.fetchall()
            if not selected:
                break
            ids=[r["source_key"] for r in selected]
            started=time.monotonic()
            results=section_batch(section,ids,session,interval_seconds=1/rate)
            good=[]
            bad=[]
            for result in results:
                item_id=str(result.get("id"))
                if result.get("code")==0:
                    data=result.get("data")
                    if section=="history_price" and isinstance(data,dict):
                        pages=int(data.get("totalPage") or 1)
                        if pages>1000:
                            raise RuntimeError(f"unexpected history_price pages={pages} for {item_id}")
                        if pages>1:
                            all_rows=list(data.get("results") or [])
                            for number in range(2,pages+1):
                                time.sleep(1/rate)
                                page_data=history_page(item_id,number,session)
                                all_rows.extend(page_data.get("results") or [])
                            data={**data,"results":all_rows}
                    good.append((item_id,data))
                else:
                    error=str(result.get("error") or f"response code {result.get('code')}")[:1000]
                    if "401" in error or "403" in error:
                        raise RuntimeError(f"authentication/permission failure for {stream} {item_id}: {error}")
                    bad.append((error,TENANT,stream,item_id))
            if good:
                placeholders=','.join(['%s']*len(good))
                with conn.cursor() as cur:
                    cur.execute(f"SELECT id,source_item_id FROM products WHERE tenant_id=%s AND source_item_id IN ({placeholders})",
                                [TENANT]+[x[0] for x in good])
                    product_ids={r["source_item_id"]:r["id"] for r in cur.fetchall()}
                    captures=[]
                    statuses=[]
                    children=[]
                    typed=[]
                    for item_id,data in good:
                        captures.append((TENANT,stream,item_id,digest(data),dump(data)))
                        statuses.append((digest(data),TENANT,stream,item_id))
                        product_id=product_ids[item_id]
                        for index,row in enumerate(rows_for(section,data)):
                            if not isinstance(row,dict):
                                row={"value":row}
                            identity=row.get("id") or row.get("branchId") or row.get("barcode") or index
                            source_key=digest([identity,row.get("supplierId"),row.get("unitName"),index])
                            children.append((product_id,section,source_key,dump(row),digest(row)))
                            if section=="supply":
                                flag=value(row,"primaryFlag")
                                typed.append((product_id,source_key,value(row,"branchId"),value(row,"supplierId"),
                                              value(row,"barcode"),value(row,"unitName"),money(row,"purchasePrice"),
                                              int(flag) if flag in ("0","1") else None,dump(row)))
                            elif section=="scope":
                                typed.append((product_id,source_key,value(row,"branchId"),value(row,"lifetime"),
                                              value(row,"logisticsMode"),dump(row)))
                            elif section in ("retail_price","delivery_price"):
                                typed.append((product_id,section,source_key,value(row,"branchId"),value(row,"barcode"),
                                              value(row,"unitName"),money(row,"retailPrice"),money(row,"deliveryPrice"),
                                              money(row,"salesPrice"),money(row,"memberPrice"),dump(row)))
                            elif section=="stock":
                                typed.append((product_id,source_key,value(row,"branchId"),quantity(row,"stockQty"),
                                              money(row,"price"),dump(row)))
                    cur.executemany("INSERT INTO source_capture (tenant_id,stream,source_key,payload_hash,payload) "
                        "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE payload_hash=VALUES(payload_hash),"
                        "payload=VALUES(payload),captured_at=NOW()",captures)
                    if children:
                        cur.executemany("INSERT INTO product_child_records (product_id,section,source_key,payload,payload_hash) "
                            "VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE payload=VALUES(payload),"
                            "payload_hash=VALUES(payload_hash),captured_at=NOW()",children)
                    write_typed(cur,section,typed)
                    cur.executemany("UPDATE sync_objects SET status='done',attempts=attempts+1,payload_hash=%s,"
                        "last_success_at=NOW(),last_error=NULL WHERE tenant_id=%s AND stream=%s AND source_key=%s",statuses)
                done+=len(good)
            if bad:
                with conn.cursor() as cur:
                    cur.executemany("UPDATE sync_objects SET status='failed',attempts=attempts+1,last_error=%s "
                                    "WHERE tenant_id=%s AND stream=%s AND source_key=%s",bad)
            checkpoint(conn,stream,{"last_id":ids[-1],"done_this_run":done},run_id)
            conn.commit()
            if done and done%100<batch_size:
                print(f"{stream} done={done} last={ids[-1]}",flush=True)
            # Requests inside a batch are paced, but the final request can
            # finish close to the batch boundary. Leave one full interval
            # before starting the next batch as well.
            time.sleep(max(1/rate,len(ids)/rate-(time.monotonic()-started)))
        scoped=(" AND source_key IN ("+','.join(['%s']*len(only_ids))+")" if only_ids else "")
        remaining=one(conn,"SELECT COUNT(*) AS n FROM sync_objects WHERE tenant_id=%s AND stream=%s AND status<>'done'"+scoped,
                      [TENANT,stream,*(only_ids or [])])["n"]
        if remaining:
            raise RuntimeError(f"{stream} incomplete: {remaining} items")
        print(f"{stream} complete",flush=True)
