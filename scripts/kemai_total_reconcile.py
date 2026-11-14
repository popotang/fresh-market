#!/usr/bin/env python3
"""List-scope coverage: Kemai source totals vs data-center totals, three modes.

Measured facts this design is built on (2026-11-10, read-only):
  * every list stream returns `totalRecord=0` even when rows exist, so one
    request cannot yield a source total;
  * one `capture-page` call costs about 10 seconds regardless of `--rows`.

Modes
  local (default)  Zero source operations. Reads the data center only: per-stream
                   table totals, coverage dates, status and (optionally) per-day
                   counts, plus a local-only detail/object inventory. This is the
                   global view.
  probe            One to three source reads for one stream and one day. Starts
                   at the page just past the data-center row count, so a short
                   final page yields the exact source total and a full page proves
                   the source has more rows than we store.
  exact            Pages from 1 to the short final page for one stream and one
                   window. Exact, bounded by --max-pages.

Neither system is written, no export task is created and no detail is fetched.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kemai_pipeline.lovrabet_adapter import ContractError, LovrabetAdapter  # noqa: E402
from kemai_pipeline.source_gate import cooldown_until, gate_dir  # noqa: E402
from kemai_pipeline.direct_read import (DirectReadClient, DirectReadError,  # noqa: E402
                                        session_auth, report_params)

TZ = ZoneInfo('Asia/Shanghai')
PAGE_SIZE = 1000          # hard source cap: page size must be 1..1000
MAX_PAGES = 100
SOURCE_OPERATION_CEILING = 1000

CONFIG = {
    # Source timeType=1 is upload time; never substitute the sale date.
    'retail-sales': {'table': 'retail_sale_flows', 'field': 'source_created_at', 'kind': None,
                     'branch_id': '69028303',
                     'start': '2026-08-01'},
    'retail-orders': {'table': 'retail_orders', 'field': 'source_created_at', 'kind': None,
                      'branch_id': '69028303',
                      'start': '2026-08-01'},
    'purchase-orders': {'table': 'purchase_documents', 'field': 'source_created_at', 'kind': 'order',
                        'start': '2025-01-01'},
    'purchase-receipts': {'table': 'purchase_documents', 'field': 'source_created_at', 'kind': 'receipt',
                          'start': '2026-08-08'},
    'purchase-returns': {'table': 'purchase_documents', 'field': 'source_created_at', 'kind': 'return',
                         'start': '2026-01-01'},
    'purchase-receive-history': {'table': 'purchase_receipt_history_lines', 'field': 'approved_at',
                                 'kind': None, 'start': '2025-01-01'},
    'retail-promote-sharing': {'table': 'retail_promote_shares', 'field': 'sold_at', 'kind': None,
                               'datetime': 'epoch_ms', 'start': '2026-10-01'},
}

API_ROWS = 1000

OBJECT_SPECS = (
    ('商品详情 profile', 'product_profile', 'products', True),
    ('商品页签子记录', 'product_child_records', 'products', False),
    ('采购商品行', 'purchase_document_lines', 'purchase_documents', False),
    ('采购分支', 'purchase_document_branches', 'purchase_documents', False),
    ('采购审批', 'purchase_document_approvals', 'purchase_documents', False),
    ('库存快照', 'product_branch_stocks', 'products', False),
)


class _NoLease:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


NO_LEASE = _NoLease()


def no_lease(label):
    return NO_LEASE


def today():
    return datetime.now(TZ).strftime('%Y-%m-%d')


def window_where(config, from_day, to_day):
    end = (datetime.fromisoformat(to_day) + timedelta(days=1)).strftime('%Y-%m-%d 00:00:00')
    if config.get('datetime') == 'epoch_ms':
        start_ms = int(datetime.fromisoformat(from_day + 'T00:00:00').replace(tzinfo=TZ).timestamp() * 1000)
        end_ms = int((datetime.fromisoformat(to_day + 'T00:00:00') + timedelta(days=1)).replace(
            tzinfo=TZ).timestamp() * 1000)
        where = {config['field']: {'$gte': start_ms, '$lt': end_ms}}
    else:
        where = {config['field']: {'$gte': from_day + ' 00:00:00', '$lt': end}}
    if config['kind']:
        where['document_kind'] = {'$eq': config['kind']}
    if config.get('branch_id'):
        where['source_branch_id'] = {'$eq': config['branch_id']}
    return where


class SourceReader:
    """Bounded, rate-limited, read-only list page reader. Counts every call."""

    def __init__(self, runner=subprocess.run, lease=None, timeout=240):
        self.runner = runner
        self.lease = lease or no_lease
        self.timeout = timeout
        self.operations = 0

    def page(self, stream, from_day, to_day, page, rows=PAGE_SIZE):
        if self.operations >= SOURCE_OPERATION_CEILING:
            raise RuntimeError('Source operation ceiling reached; stop and report the unknown')
        if cooldown_until(gate_dir()) > time.time():
            raise RuntimeError('RATE_LIMIT: shared source cooldown active; no reconciliation issued')
        with self.lease(f'reconcile:{stream}'):
            self.operations += 1
            result = self.runner(['opencli', 'kemai', 'capture-page', '--kind', stream,
                                  '--from', from_day, '--to', to_day, '--page', str(page),
                                  '--rows', str(rows), '-f', 'json'],
                                 capture_output=True, text=True, timeout=self.timeout, cwd=ROOT)
        if result.returncode:
            raise RuntimeError('Source read failed; inspect authentication/rate limit')
        envelope = json.loads(result.stdout)
        rows_out = envelope.get('data') if isinstance(envelope, dict) else None
        if not isinstance(rows_out, list) or len(rows_out) != 1:
            raise ValueError('Invalid capture-page envelope')
        evidence = rows_out[0]
        if evidence.get('stream') != stream:
            raise ValueError('Source stream mismatch')
        return evidence


def dc_window_count(adapter, stream, from_day, to_day):
    """One data-center read: pageSize=1 and read totalCount (no wide payload select)."""
    table = CONFIG[stream]['table']
    params = {'where': adapter._scoped_where(table, window_where(CONFIG[stream], from_day, to_day)),
              'currentPage': 1, 'pageSize': 1}
    result = adapter.invoke('data', 'filter', '--code', adapter.dataset_code(table),
                            '--params', json.dumps(params, ensure_ascii=False))
    paging = result.get('paging') if isinstance(result, dict) else None
    total = (paging or {}).get('totalCount')
    if type(total) is not int or total < 0:
        raise RuntimeError('Invalid data-center total')
    return total


def list_params(stream, from_day, to_day):
    return report_params(stream, from_day, to_day)


def browser_auth(session='kemai', runner=subprocess.run):
    return session_auth(session, runner=runner)


def api_page(stream, from_day, to_day, page, auth, *, rows=API_ROWS, opener=None):
    """One gated direct read; compatibility wrapper for offline callers."""
    started = time.time()
    result, total = DirectReadClient(auth, opener=opener).list_page(
        stream, from_day, to_day, page, rows=rows)
    return result, total, round(time.time() - started, 2)


def api_stats_total(stream, from_day, to_day, auth, *, opener=None):
    """One gated direct statistical query; missing total is an error."""
    return DirectReadClient(auth, opener=opener).statistical_total(stream, from_day, to_day)


def api_window_total(stream, from_day, to_day, auth, *, max_pages=20, worker=None, interval=0.0,
                     stats_worker=None):
    """Exact source total: summary request, then totalRecord, then bounded paging."""
    worker = worker or api_page
    stats = stats_worker if stats_worker is not None else api_stats_total
    summary_total = stats(stream, from_day, to_day, auth)
    if isinstance(summary_total, int) and summary_total >= 0:
        return {'source_total': summary_total, 'pages': 0, 'operations': 1,
                'basis': 'source_statistical_total', 'complete': True}
    _, total, _ = worker(stream, from_day, to_day, 1, auth, rows=1)
    if isinstance(total, int) and total > 0:
        return {'source_total': total, 'pages': 1, 'operations': 1,
                'basis': 'source_totalRecord', 'complete': True}
    counted, pages = 0, 0
    while pages < max_pages:
        if pages and interval:
            time.sleep(interval)
        pages += 1
        rows, _, _ = worker(stream, from_day, to_day, pages, auth)
        counted += len(rows)
        if len(rows) < API_ROWS:
            return {'source_total': counted, 'pages': pages, 'operations': pages + 1,
                    'basis': 'page_count_short_final_page', 'complete': True}
    return {'source_total': counted, 'pages': pages, 'operations': pages + 1,
            'basis': 'page_budget_exhausted', 'complete': False}


def api_view(*, to_day, days=3, streams=None, adapter=None, auth=None, max_pages=20,
             worker=None, interval=0.0, stats_worker=None):
    """Fast list comparison: direct read-only API over the last `days` days."""
    adapter = adapter or LovrabetAdapter()
    auth = auth or browser_auth()
    streams = list(streams or CONFIG)
    if days < 1 or max_pages < 1 or max_pages > 100:
        raise ValueError('Invalid reconciliation budget')
    direct = None
    if worker is None and stats_worker is None:
        direct = DirectReadClient(auth, max_operations=min(1000, len(streams)*days*(max_pages+2)))
        worker = lambda stream, start, end, page, _auth, rows=API_ROWS: (
            *direct.list_page(stream, start, end, page, rows=rows), 0)
        stats_worker = lambda stream, start, end, _auth: direct.statistical_total(stream, start, end)
    results, operations, center_attempts = [], 0, 0
    started = time.time()
    for stream in streams:
        for offset in range(days):
            day = (datetime.fromisoformat(to_day) - timedelta(days=offset)).strftime('%Y-%m-%d')
            try:
                total = api_window_total(stream, day, day, auth, max_pages=max_pages,
                                         worker=worker, interval=interval,
                                         stats_worker=stats_worker)
                # History and promotion still lack a proven source/DB time basis.
                comparable = stream in ('retail-sales', 'retail-orders', 'purchase-orders',
                                        'purchase-receipts', 'purchase-returns')
            except (RuntimeError, ValueError, DirectReadError) as error:
                results.append({'stream': stream, 'day': day, 'status': 'error',
                                'note': type(error).__name__, 'source_total': None,
                                'data_center_total': None, 'match': None})
                continue
            center, db_unavailable = None, False
            if comparable:
                center_attempts += 1
                try:
                    center = dc_window_count(adapter, stream, day, day)
                except (RuntimeError, ValueError):
                    db_unavailable = True
            operations += total['operations']
            matched = total['source_total'] == center if center is not None else None
            row = {'stream': stream, 'day': day, 'source_total': total['source_total'],
                   'data_center_total': center,
                   'delta': total['source_total'] - center if center is not None else None,
                   'pages': total['pages'], 'total_basis': total['basis'], 'match': matched,
                   'status': ('match' if matched else 'mismatch') if total['complete']
                             else 'source_total_unavailable'}
            if not total['complete']:
                row.update(status='source_total_unavailable',match=None)
            elif center is None:
                row.update(delta=None, status='db_window_unavailable' if db_unavailable
                           else 'scope_unverified', match=None)
            elif total['source_total']==0:
                row.update(status='params_unverified',match=None)
            if day == today():
                row['provisional'] = True
                row['note'] = 'Current day is still open; a delta is expected, not a failure'
            if not total['complete']:
                row['note'] = 'Page budget exhausted; source total is a lower bound'
            results.append(row)
    return {'mode': 'api', 'to': to_day, 'days': days,
            'source_operations': direct.operations if direct else operations,
            'data_center_operations': center_attempts,
            'elapsed_seconds': round(time.time() - started, 1),
            'streams': results,
            'scope': 'list_total_only; direct read-only API; no detail, no export task'}


def probe_day(stream, day, *, adapter, reader):
    """Boundary probe: read the page just past our row count, then the tail if full."""
    if stream not in CONFIG:
        raise ValueError('Unsupported reconciliation stream')
    center = dc_window_count(adapter, stream, day, day)
    page = center // PAGE_SIZE + 1
    evidence = reader.page(stream, day, day, page)
    first = evidence.get('rows') or []
    result = {'stream': stream, 'day': day, 'data_center_total': center,
              'probe_page': page, 'source_operations': reader.operations,
              'scope': 'list_total_only; single-day boundary probe'}
    if not first:
        result.update(status='consistent', source_total=None, match=True,
                      note='No rows beyond the stored range; source total does not exceed the data center')
        return result
    total = (page - 1) * PAGE_SIZE + len(first)
    while len(first) == PAGE_SIZE and reader.operations < MAX_PAGES:
        page += 1
        evidence = reader.page(stream, day, day, page)
        first = evidence.get('rows') or []
        total = (page - 1) * PAGE_SIZE + len(first)
        if not first:
            total = (page - 1) * PAGE_SIZE
    result.update(source_total=total, delta=total - center, match=total == center,
                  status='match' if total == center else 'mismatch',
                  source_operations=reader.operations,
                  note='Exact source total from a short final page' if len(first) < PAGE_SIZE
                       else 'Page budget exhausted; source total is a lower bound')
    return result


def exact_total(stream, from_day, to_day, *, adapter, reader, max_pages=MAX_PAGES):
    if stream not in CONFIG:
        raise ValueError('Unsupported reconciliation stream')
    total = 0
    for page in range(1, max_pages + 1):
        rows = reader.page(stream, from_day, to_day, page).get('rows') or []
        total += len(rows)
        if len(rows) < PAGE_SIZE:
            break
    else:
        return {'stream': stream, 'from': from_day, 'to': to_day, 'source_total': None,
                'status': 'source_total_unavailable', 'match': None,
                'source_operations': reader.operations,
                'note': 'Page budget exhausted; no completeness claim'}
    center = dc_window_count(adapter, stream, from_day, to_day)
    return {'stream': stream, 'from': from_day, 'to': to_day, 'source_total': total,
            'data_center_total': center, 'delta': total - center,
            'match': total == center, 'status': 'match' if total == center else 'mismatch',
            'source_operations': reader.operations,
            'scope': 'list_total_only; exact page count'}


def local_view(*, to_day, days=7, adapter=None, daily=False):
    """Zero source operations. Data-center facts plus local-only object inventory."""
    adapter = adapter or LovrabetAdapter()
    cache = {}
    requests = 0

    def total(table, kind=None):
        nonlocal requests
        key = (table, kind)
        if key not in cache:
            try:
                where = {'document_kind': {'$eq': kind}} if kind else None
                _, count = adapter.page(table, where=where, size=1)
                cache[key] = count
            except ContractError as error:
                cache[key] = {'error': str(error)}
            requests += 1
        return cache[key]

    status_rows = []
    try:
        status_rows, _ = adapter.page('sync_stream_status', size=100)
        requests += 1
    except (ContractError, ValueError):
        pass
    by_stream = {row.get('stream'): row for row in status_rows}

    streams = []
    for stream, config in CONFIG.items():
        status = by_stream.get(stream, {})
        row = {'stream': stream, 'table': config['table'], 'kind': config['kind'],
               'rows_in_table': total(config['table'], config['kind']),
               'configured_from': config['start'],
               'covered_through': status.get('covered_through'),
               'last_success_at': status.get('last_success_at'),
               'last_attempt_status': status.get('last_attempt_status') or 'not_registered'}
        if daily:
            per_day = []
            for offset in range(days):
                day = (datetime.fromisoformat(to_day) - timedelta(days=offset)).strftime('%Y-%m-%d')
                per_day.append({'day': day, 'rows': dc_window_count(adapter, stream, day, day)})
                requests += 1
            row['daily'] = per_day
        streams.append(row)

    objects = []
    for label, table, base, one_to_one in OBJECT_SPECS:
        count = total(table)
        base_count = total(base) if base else None
        entry = {'object': label, 'table': table,
                 'scope': 'data_center_only; zero source operations'}
        if isinstance(count, dict):
            entry.update(rows=None, status='no_dataset_mapping', completion=None, per_base=None)
        else:
            entry['rows'] = count
            if isinstance(base_count, int) and base_count:
                entry['base'] = {'table': base, 'rows': base_count}
                entry['per_base'] = round(count / base_count, 2)
                entry['completion'] = round(count / base_count * 100, 1) if one_to_one else None
                entry['status'] = 'measured'
            else:
                entry.update(completion=None, per_base=None, status='no_base')
        objects.append(entry)

    gaps = total('sync_coverage_gaps')
    return {'mode': 'local', 'to': to_day, 'source_operations': 0,
            'data_center_operations': requests, 'streams': streams, 'objects': objects,
            'coverage_gaps_pending': gaps if isinstance(gaps, int) else None}


def summarize(payload, elapsed=None):
    summary = {'mode': payload.get('mode'),
               'source_operations': payload.get('source_operations'),
               'data_center_operations': payload.get('data_center_operations'),
               'detail_source_operations': 0,
               'elapsed_seconds': round(elapsed, 1) if elapsed is not None else None}
    if payload.get('mode') == 'local':
        summary['streams_reported'] = len(payload['streams'])
        summary['objects_measured'] = sum(1 for row in payload.get('objects', []) if row.get('status') == 'measured')
        summary['objects_without_mapping'] = sum(1 for row in payload.get('objects', [])
                                                 if row.get('status') == 'no_dataset_mapping')
    elif payload.get('mode') == 'api':
        rows = payload.get('streams', [])
        summary['days'] = payload.get('days')
        summary['rows_reported'] = len(rows)
        summary['comparisons'] = sum(row.get('status') in ('match', 'mismatch') for row in rows)
        summary['match'] = sum(1 for row in rows if row.get('status') == 'match')
        summary['mismatch'] = sum(1 for row in rows
                                  if row.get('status') == 'mismatch' and not row.get('provisional'))
        summary['provisional'] = sum(1 for row in rows if row.get('provisional'))
        summary['source_total_unavailable'] = sum(1 for row in rows
                                                  if row.get('status') == 'source_total_unavailable')
        summary['errors'] = sum(1 for row in rows if row.get('status') == 'error')
        summary['unverified'] = sum(1 for row in rows if row.get('status') in
                                    ('params_unverified', 'scope_unverified', 'db_window_unavailable'))
    else:
        summary['status'] = payload.get('status')
        summary['match'] = payload.get('match')
    return summary


def _fmt_time(value):
    if isinstance(value, (int, float)) and value:
        moment = datetime.fromtimestamp(value / 1000, TZ)
        return moment.strftime('%Y-%m-%d') if moment.hour == moment.minute == 0 else moment.strftime('%Y-%m-%d %H:%M')
    return '—'


def _fmt_count(value):
    return f'{value:,}' if isinstance(value, int) else '未知'


def baseline_markdown(payload, generated_at):
    lines = ['# 科迈 → 数据中心：覆盖基线', '',
             f'生成时间：{generated_at}。模式：`{payload.get("mode")}`；'
             f'截止日：{payload.get("to")}；源操作 **{payload.get("source_operations", 0)}** 次。', '']
    if payload.get('mode') == 'local':
        lines += ['## 一、列表流（数据中心口径，未访问源）', '',
                  '| 流 | 表 | 库内行数 | 配置起始 | 连续核验到 | 最后成功 | 最近结果 |',
                  '|---|---|---:|---|---|---|---|']
        for row in payload['streams']:
            lines.append(f'| `{row["stream"]}` | `{row["table"]}` | {row["rows_in_table"]:,} | '
                         f'{row["configured_from"]} | {_fmt_time(row["covered_through"])} | '
                         f'{_fmt_time(row["last_success_at"])} | {row["last_attempt_status"]} |')
        lines += ['', '## 二、详情/对象覆盖（仅库内计数）', '',
                  '| 对象 | 表 | 已入库 | 基数 | 1:1 完成度 | 每基数行数 | 状态 |',
                  '|---|---|---:|---:|---:|---:|---|']
        for row in payload['objects']:
            base = row.get('base', {}).get('rows')
            completion = f'{row["completion"]}%' if row.get('completion') is not None else '—'
            per_base = row.get('per_base') if row.get('per_base') is not None else '—'
            rows_value = '未知' if row.get('rows') is None else f'{row["rows"]:,}'
            lines.append(f'| {row["object"]} | `{row["table"]}` | {rows_value} | '
                         f'{_fmt_count(base)} | {completion} | {per_base} | {row["status"]} |')
        gaps = payload.get('coverage_gaps_pending')
        lines += ['', f'待复核覆盖缺口台账：**{gaps if gaps is not None else "未知"}** 条 pending。', '']
    elif payload.get('mode') == 'api':
        lines += [f'## 一、近 {payload.get("days")} 天列表总量对比（直连只读 API）', '',
                  '| 流 | 日期 | 源端 | 数据中心 | 差额 | 结果 | 依据 | 页数 |',
                  '|---|---|---:|---:|---:|---|---|---:|']
        for row in payload['streams']:
            source = '未知' if row.get('source_total') is None else f'{row["source_total"]:,}'
            center = '未知' if row.get('data_center_total') is None else f'{row["data_center_total"]:,}'
            delta = '—' if row.get('delta') is None else f'{row["delta"]:+,}'
            lines.append(f'| `{row["stream"]}` | {row["day"]} | {source} | {center} | {delta} | '
                         f'{row["status"]} | {row.get("total_basis", "")} | {row.get("pages", "—")} |')
        lines.append('')
    else:
        lines += ['## 单点对比结果', '',
                  '| 流 | 窗口 | 源端 | 数据中心 | 差额 | 结果 | 依据 | 源操作 |',
                  '|---|---|---:|---:|---:|---|---|---:|']
        row = payload
        window = row.get('day') or f'{row.get("from")} ~ {row.get("to")}'
        lines.append(f'| `{row["stream"]}` | {window} | {row.get("source_total")} | '
                     f'{row.get("data_center_total")} | {row.get("delta")} | {row.get("status")} | '
                     f'{row.get("note", "")} | {row.get("source_operations")} |')
        lines.append('')
    lines += ['## 三、边界', '',
              '- 统计接口提供源端总数；每个请求仍经共享 0.5 次/秒门禁与 30 分钟限流冷却。',
              '- 零售流水按上传时间查询，数据中心也按 `source_created_at` 与同一分支计数；查询失败时标 `db_window_unavailable`。',
              '- 收货历史与让利报表的源端时间口径尚未与库内字段验明，标为 `scope_unverified`。',
              '- 源端总数为零时暂标 `params_unverified`，不能凭零值判定两端一致。',
              '- 当天（未结算）窗口的差额属预期，标记 provisional，不计入不一致。',
              '- 数据中心的 `totalCount` 与状态表的 `covered_through` 只说明库内事实，不证明源端实时完整。',
              '- 详情/对象覆盖只是库内行数，不代表源端应有数量；库存对象暂无数据集映射。',
              '- 本文件只有聚合计数，不含原始业务明细。']
    return '\n'.join(lines) + '\n'


def write_artifacts(payload, *, outdir=None, doc=False, generated_at=None):
    stamp = datetime.now(TZ).strftime('%Y%m%dT%H%M%S')
    generated_at = generated_at or datetime.now(TZ).strftime('%Y-%m-%d %H:%M %Z')
    outdir = Path(outdir) if outdir else ROOT / '.local-audits' / 'kemai-baseline' / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    private = outdir / 'coverage.json'
    private.write_text(json.dumps({'generated_at': generated_at, **payload},
                                  ensure_ascii=False, indent=2) + '\n')
    os.chmod(private, 0o600)
    paths = {'json': str(private)}
    if doc:
        suffix = 'api-coverage' if payload.get('mode') == 'api' else 'coverage-baseline'
        target = ROOT / 'docs' / f'kemai-lovrabet-{suffix}-{payload.get("to") or payload.get("day")}.md'
        target.write_text(baseline_markdown(payload, generated_at))
        paths['doc'] = str(target)
    return paths


def plan_budget(mode, streams=None, days=1):
    streams = list(streams or CONFIG)
    if mode == 'local':
        return {'mode': mode, 'source_operations_expected': 0, 'source_operations_worst_case': 0,
                'detail_source_operations': 0,
                'note': 'Data-center reads only; no source request, no export task.'}
    if mode == 'api':
        return {'mode': mode, 'streams': streams, 'days': days,
                'source_operations_expected': len(streams) * days,
                'source_operations_worst_case': len(streams) * days * MAX_PAGES,
                'detail_source_operations': 0,
                'source_gate_min_interval_seconds': 2,
                'note': ('Direct allowlisted report queries; every HTTP request uses the '
                         'shared 0.5/s source gate and 30-minute cooldown.')}
    per_stream = 1 if mode == 'probe' else MAX_PAGES
    return {'mode': mode, 'streams': streams, 'days': days,
            'source_operations_expected': len(streams) * days,
            'source_operations_worst_case': len(streams) * days * per_stream,
            'detail_source_operations': 0,
            'seconds_per_source_operation_measured': 10,
            'note': 'Browser capture path: about 10 seconds per page read.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', default='local', choices=('local', 'api', 'probe', 'exact'))
    parser.add_argument('--stream', default='all', choices=('all', *CONFIG))
    parser.add_argument('--from', dest='from_day')
    parser.add_argument('--to', dest='to_day', default=None)
    parser.add_argument('--day', dest='day', help='single day for probe mode')
    parser.add_argument('--days', type=int, default=3, help='api mode: trailing days to compare (default 3)')
    parser.add_argument('--daily', action='store_true', help='local mode: add per-day row counts (extra DB reads)')
    parser.add_argument('--no-objects', action='store_true', help='local mode: skip the object inventory')
    parser.add_argument('--max-pages', type=int, default=20, help='page budget per stream (default 20)')
    parser.add_argument('--interval', type=float, default=0.0,
                        help='extra pause between API pages; shared source gate enforces >=2s')
    parser.add_argument('--session', default='kemai', help='OpenCLI browser session holding the Kemai login')
    parser.add_argument('--dry-run', action='store_true', help='print the budget; issue no request')
    parser.add_argument('--write-doc', action='store_true', help='write the sanitized baseline markdown to docs/')
    parser.add_argument('--outdir')
    args = parser.parse_args(argv)

    to_day = args.to_day or today()
    streams = list(CONFIG) if args.stream == 'all' else [args.stream]
    if args.mode in ('probe', 'exact') and args.stream == 'all':
        parser.error('--mode probe/exact requires a single --stream')

    if args.dry_run:
        print(json.dumps({'ok': True, 'dry_run': True,
                          'data': plan_budget(args.mode, streams, args.days)},
                         ensure_ascii=False, indent=2))
        return 0

    started = time.time()
    try:
        if args.mode == 'local':
            payload = local_view(to_day=to_day, days=args.days, daily=args.daily)
            if args.no_objects:
                payload['objects'] = []
            payload['to'] = to_day
        elif args.mode == 'api':
            payload = api_view(to_day=to_day, days=args.days, streams=streams,
                               auth=browser_auth(args.session), max_pages=args.max_pages,
                               interval=args.interval)
        else:
            adapter = LovrabetAdapter()
            # capture-page already owns the OpenCLI source lease; wrapping it
            # again deadlocks against the same cross-process lock.
            reader = SourceReader()
            if args.mode == 'probe':
                day = args.day or args.from_day or to_day
                if args.from_day and args.to_day and args.from_day != args.to_day:
                    parser.error('probe mode compares a single day; use --day or equal --from/--to')
                payload = probe_day(args.stream, day, adapter=adapter, reader=reader)
                payload['to'] = day
            else:
                start = args.from_day or CONFIG[args.stream]['start']
                payload = exact_total(args.stream, start, to_day, adapter=adapter,
                                      reader=reader, max_pages=args.max_pages)
                payload['to'] = to_day
    except (RuntimeError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': type(error).__name__, 'message': str(error)},
                         ensure_ascii=False))
        return 2
    elapsed = time.time() - started
    payload['summary'] = summarize(payload, elapsed)
    paths = write_artifacts(payload, outdir=args.outdir, doc=args.write_doc)
    if payload.get('mode') == 'api':
        summary = payload['summary']
        ok = summary['mismatch'] == 0 and summary['errors'] == 0 and summary['unverified'] == 0
    else:
        ok = payload.get('match') is not False and payload.get('status') in (None, 'match', 'consistent')
    print(json.dumps({'ok': ok, 'data': {**payload, 'artifacts': paths}}, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
