"""Allowlisted, read-only Kemai report transport. Authentication stays in memory.

The POST endpoints below are report queries; no export, mutation or discovery
endpoint can be supplied by a caller. Every HTTP attempt uses the shared gate.
"""

import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from .rate_limit import rate_limit_signal, record_limit
from .source_gate import source_lease

ROOT = Path(__file__).resolve().parents[1]
HOST = 'https://yunfan-api.kemai.com.cn'
LIST_PATHS = {
    'retail-orders': '/web/api/order/retail/getListData',
    'retail-sales': '/web/api/order/orderItemFlow/getListData',
    'purchase-orders': '/web/api/purchase/purchaseOrder/getListData',
    'purchase-receipts': '/web/api/stock/purchaseIn/getListData',
    'purchase-returns': '/web/api/stock/purchaseOut/getListData',
    'purchase-receive-history': '/web/api/stock/purchaseCommon/getListData',
    'retail-promote-sharing': '/web/api/order/item/discount/queryPromoteShareDetail',
}
STAT_PATHS = {name: path.rsplit('/', 1)[0] + '/queryStatisticalInfo'
              for name, path in LIST_PATHS.items()}


class DirectReadError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise DirectReadError('Source report redirected; session or endpoint needs review')


def session_auth(session='kemai', runner=subprocess.run):
    """Use OpenCLI only to read the existing session token; never print it."""
    if not session or any(ch.isspace() for ch in session):
        raise ValueError('Invalid OpenCLI session name')
    js = ("(()=>{const u=JSON.parse(localStorage.userInfo||'{}');"
          "const c=(document.cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith('PC_TOKEN='))||'').slice(9);"
          "return JSON.stringify({token:c,loginName:u.loginName||'',shopAdminId:String(u.shopAdminId||''),"
          "merchantName:encodeURIComponent(u.companyName||'')})})()")

    def read_once():
        result = runner(['opencli', 'browser', 'eval', '--session', session, js],
                        capture_output=True, text=True, timeout=60, cwd=ROOT)
        if result.returncode:
            return None
        try:
            payload = json.loads(result.stdout)
            return json.loads(payload['data']) if payload.get('ok') is True else None
        except (ValueError, KeyError, TypeError):
            return None

    auth = read_once()
    if not isinstance(auth, dict) or not auth.get('token'):
        # The automation session can fall back to about:blank after a long idle
        # window (writes take ~20 minutes). Chrome still holds the login cookie,
        # so one read-only re-navigation restores page context without a login.
        runner(['opencli', 'browser', 'open', '--session', session,
                'https://yunfan.kemai.com.cn/retail/salesDetails'],
               capture_output=True, text=True, timeout=90, cwd=ROOT)
        time.sleep(3)
        auth = read_once()
    if not isinstance(auth, dict) or not auth.get('token'):
        raise DirectReadError('Kemai session token unavailable; sign in before reading')
    return auth


def report_params(stream, from_day, to_day):
    """Explicit date and branch selectors; never inherit the user's UI filters."""
    if stream not in LIST_PATHS:
        raise ValueError('Unsupported read-only report')
    start, end = date.fromisoformat(from_day), date.fromisoformat(to_day)
    if start > end:
        raise ValueError('Invalid report window')
    if stream.startswith('retail-'):
        contract = json.loads(Path(__file__).with_name('source_contracts.json').read_text())['retail_scope']
        branch_ids = contract['branch_ids']
        if len(branch_ids) != 1:
            raise DirectReadError('Explicit retail branch scope requires review')
        start_time, end_time = from_day + ' 00:00:00', to_day + ' 23:59:59'
        params = {'branchIdList': branch_ids, 'branchCodes': contract['branch_codes'],
                  'branchId1': branch_ids[0], 'saleTimeFrom': start_time,
                  'saleTimeTo': end_time, 'saleTimeRange': [start_time, end_time]}
        params['timeType' if stream == 'retail-sales' else 'dateType'] = (
            '1' if stream == 'retail-sales' else '0')
        return params
    if stream == 'purchase-receive-history':
        return {'operDateFrom': from_day + ' 00:00', 'operDateTo': to_day + ' 23:59',
                'dateType': '1', 'queryType': '1', 'queryRangeType': '2'}
    params = {'keyword': '', 'itemPackId': '', 'status': '',
              'operDateFrom': from_day, 'operDateTo': to_day, 'dateType': '0'}
    if stream == 'purchase-receipts':
        params['queryType'] = '0'
    return params


class DirectReadClient:
    def __init__(self, auth, *, opener=None, lease=source_lease, max_operations=100):
        if not isinstance(auth, dict) or not auth.get('token'):
            raise DirectReadError('Kemai session token unavailable')
        if not 0 < max_operations <= 1000:
            raise ValueError('Invalid source operation budget')
        self.auth = auth
        self.opener = opener or urllib.request.build_opener(_NoRedirect()).open
        self.lease = lease
        self.max_operations = max_operations
        self.operations = 0

    def _request(self, stream, kind, from_day, to_day, *, page=1, rows=1000):
        paths = STAT_PATHS if kind == 'stat' else LIST_PATHS
        if stream not in paths or kind not in ('stat', 'list'):
            raise ValueError('Endpoint is not an allowlisted read-only report')
        if type(page) is not int or page < 1 or type(rows) is not int or not 1 <= rows <= 1000:
            raise ValueError('Invalid report pagination')
        if self.operations >= self.max_operations:
            raise DirectReadError('Source operation budget exhausted')
        body = {'page': page, 'rows': 1 if kind == 'stat' else rows,
                'params': report_params(stream, from_day, to_day),
                'filters': [], 'orders': [], 'currentPageOrders': []}
        headers = {'Content-Type': 'application/json;charset=UTF-8',
                   'token': self.auth['token'], 'loginName': self.auth.get('loginName', ''),
                   'shopAdminId': self.auth.get('shopAdminId', ''),
                   'merchantName': self.auth.get('merchantName', ''),
                   'Origin': 'https://yunfan.kemai.com.cn',
                   'Referer': 'https://yunfan.kemai.com.cn/'}
        request = urllib.request.Request(HOST + paths[stream],
                  data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method='POST')
        label = f'direct-read:{stream}:{kind}'
        try:
            with self.lease(label, fail_on_cooldown=True):
                self.operations += 1
                try:
                    with self.opener(request, timeout=30) as response:
                        payload = json.loads(response.read().decode())
                except urllib.error.HTTPError as error:
                    if error.code == 429:
                        record_limit(label, 'HTTP 429', wait=False)
                        raise DirectReadError('RATE_LIMIT: shared 30-minute cooldown recorded') from None
                    raise DirectReadError(f'Source report HTTP {error.code}; outcome unknown') from None
                if not isinstance(payload, dict):
                    raise DirectReadError('Invalid source report envelope')
                if rate_limit_signal({key: payload.get(key) for key in ('code', 'msg', 'message', 'error')}) \
                        or str(payload.get('code')) == '429':
                    record_limit(label, 'Source business rate limit', wait=False)
                    raise DirectReadError('RATE_LIMIT: shared 30-minute cooldown recorded')
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise DirectReadError(f'Source report transport failed: {type(error).__name__}') from None
        if payload.get('code') != 0 or not isinstance(payload.get('data'), dict):
            raise DirectReadError('Source report rejected or unrecognized; inspect session and scope')
        return payload['data']

    def statistical_total(self, stream, from_day, to_day):
        data = self._request(stream, 'stat', from_day, to_day)
        value = data.get('totalRecord')
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        if type(value) is not int or value < 0:
            raise DirectReadError('Statistical total absent or invalid')
        return value

    def list_page(self, stream, from_day, to_day, page, *, rows=1000):
        data = self._request(stream, 'list', from_day, to_day, page=page, rows=rows)
        result = data.get('results')
        if not isinstance(result, list) or len(result) > rows:
            raise DirectReadError('List page absent or invalid')
        return result, data.get('totalRecord')
