"""Kemai read-only source port; OpenCLI pages plus gated direct statistics."""
import json
import subprocess
import time
from pathlib import Path
from .capabilities import CAPABILITIES
from .rate_limit import is_rate_limited, rate_limit_signal, record_limit
from .source_gate import cooldown_until, gate_dir


class SourceError(RuntimeError): pass
class SourceCooling(SourceError): pass
class SourceRejected(SourceError): pass
class SourceAuthenticationRequired(SourceError): pass


def authentication_signal(value):
    """Inspect diagnostic fields only; never scan product names or raw payload text."""
    if isinstance(value, dict):
        return any(authentication_signal(value.get(key)) for key in
                   ('error', 'message', 'msg', 'code', 'data', 'response', 'result'))
    if isinstance(value, list):
        return any(authentication_signal(item) for item in value if isinstance(item, dict))
    return isinstance(value, str) and any(text in value for text in
        ('登录认证失败', '登录已过期', '登录失效', '未登录', 'AUTH_REQUIRED', 'UNAUTHORIZED'))


PURCHASE_STREAMS=("purchase-orders","purchase-receipts","purchase-returns")
DETAIL_KINDS=('order','receipt','return')


def _contains_url(value):
    """Any URL material (signed download links, credentials) is forbidden in receipts."""
    if isinstance(value,str):return 'http' in value.lower()
    if isinstance(value,dict):return any(_contains_url(key) or _contains_url(item) for key,item in value.items())
    if isinstance(value,(list,tuple)):return any(_contains_url(item) for item in value)
    return False


def _source_contracts():
    return json.loads(Path(__file__).with_name('source_contracts.json').read_text())


def validate_purchase_scope(stream,scope,day):
    """Purchase lists filter by create time; hidden filters must stay empty."""
    profile=_source_contracts()['purchase_scope']
    if str(scope.get(profile['time_field']))!=profile['create_time_value']:
        raise SourceError('Purchase window must use create time (dateType=0); approve-time windows are a different contract')
    for field in profile['window_fields']:
        if str(scope.get(field))!=day:
            raise SourceError(f'Purchase window field {field} does not match the requested day')
    for key in profile['must_be_empty']:
        if scope.get(key) not in (None,'',[],{}):
            raise SourceError(f'Unexpected active purchase filter {key}; inspect query-scope before collecting')
    fixed=profile['stream_fixed_filters'][stream]
    for key,value in fixed.items():
        if str(scope.get(key))!=value:
            raise SourceError(f'Purchase fixed filter {key} changed; inspect query-scope before collecting')
    allowed={profile['time_field'],*profile['window_fields'],*profile['must_be_empty'],*fixed}
    if any(value not in (None,'',[],{}) for key,value in scope.items() if key not in allowed):
        raise SourceError('Unexpected active purchase filter; inspect query-scope before collecting')


def validate_retail_scope(stream,scope):
    profile=json.loads(Path(__file__).with_name('source_contracts.json').read_text())['retail_scope']
    timing=profile[stream]
    if scope.get(timing['time_field'])!=timing['upload_value']:raise SourceError('Retail window must use explicit upload time')
    if sorted(map(str,scope.get('branchIdList',[])))!=profile['branch_ids'] or scope.get('branchCodes')!=profile['branch_codes']:
        raise SourceError('Retail branch scope changed; explicitly migrate scope before collecting')
    if any(scope.get(key) not in (None,'',*profile['branch_ids']) for key in ('branchId','branchId1')):
        raise SourceError('Conflicting retail branch selectors')
    allowed={'branchIdList','branchCodes','branchNames','branchId','branchId1','saleTimeFrom','saleTimeTo','saleTimeRange',timing['time_field']}
    if any(value not in (None,'',[],{}) for key,value in scope.items() if key not in allowed):
        raise SourceError('Unexpected active retail filter; inspect query-scope before collecting')


class OpenCLISource:
    def __init__(self, executor=subprocess.run, *, max_pages=1000, max_operations=200):
        if not 0<=max_operations<=1000:raise ValueError('Source operation budget must be 0..1000')
        self.executor=executor;self.max_pages=max_pages;self.operations=0;self.max_operations=max_operations

    def call(self, command, *args):
        if command.endswith('-export-start'):
            raise SourceError('SOURCE_WRITE_FORBIDDEN: source export task creation is disabled')
        if cooldown_until(gate_dir())>time.time(): raise SourceCooling('Shared cooldown active')
        if self.operations>=self.max_operations:raise SourceError('Source operation budget exhausted; no further source calls issued')
        self.operations+=1
        try:
            result=self.executor(['opencli','kemai',command,*args,'-f','json'],
                capture_output=True,text=True,timeout=120)
        except subprocess.TimeoutExpired as error:
            raise SourceError('Source operation timed out; outcome unknown') from error
        if is_rate_limited(result.stderr) or (result.returncode and is_rate_limited(result.stdout)):
            record_limit(command,'RATE_LIMIT: source CLI response',wait=False)
            raise SourceCooling('Rate limited; persisted 30-minute cooldown')
        if result.returncode and 'EXPORT_REJECTED' in (result.stderr + result.stdout):
            raise SourceRejected('Source explicitly rejected the export; no successful submission confirmed')
        try: decoded=json.loads(result.stdout)
        except ValueError: decoded=None
        if authentication_signal(decoded) or (result.returncode and authentication_signal(result.stderr)):
            raise SourceAuthenticationRequired('Kemai authentication failed; sign in again, then resume the same capture')
        if result.returncode: raise SourceError('OpenCLI failed; check session and command contract')
        try: data=json.loads(result.stdout)
        except ValueError as error: raise SourceError('Source JSON invalid') from error
        if rate_limit_signal(data):
            record_limit(command,'RATE_LIMIT: HTTP/business response',wait=False)
            raise SourceCooling('Rate limited; persisted 30-minute cooldown')
        # OpenCLI 2.x always wraps JSON, including explicit -f json. Keep 1.x
        # decoding for rollback, but never mistake an error envelope for data.
        if isinstance(data,dict):
            if data.get('ok') is not True:
                raise SourceError('OpenCLI error envelope; outcome unknown, inspect saved artifacts before retry')
            data=data.get('data')
        if not isinstance(data,list): raise SourceError('Expected OpenCLI record array')
        return data

    def pages(self, stream, day, *, page_size=1000, until=None):
        cap=CAPABILITIES[stream]
        if cap.paused or cap.granularity!='day': raise SourceError('Stream requires separate object workflow')
        if not 1<=page_size<=1000: raise ValueError('Invalid page size')
        total=None
        for page in range(1,self.max_pages+1):
            # A page read is stateless and idempotent; retry once on a transient CLI
            # failure (observed: intermittent 'page component not ready' right after
            # component detection). Cooling, auth and rejection always abort.
            for attempt in range(2):
                try:
                    result=self.call('capture-page','--kind',stream,'--from',day,'--to',day,
                                     '--page',str(page),'--rows',str(page_size),*(['--until',until] if until else []))
                    break
                except (SourceCooling,SourceAuthenticationRequired,SourceRejected):
                    raise
                except SourceError:
                    if attempt: raise
                    time.sleep(3)
            if len(result)!=1:raise SourceError('Missing capture envelope')
            e=result[0]
            expected={'schema_version':1,'stream':stream,'from':day,'to':day,'page':page,'page_size':page_size}
            if until and e.get('until')!=until:raise SourceError('Snapshot cutoff mismatch')
            if any(e.get(k)!=v for k,v in expected.items()):raise SourceError('Capture scope mismatch')
            if stream.startswith('retail-'):validate_retail_scope(stream,e.get('query_scope',{}))
            elif stream in PURCHASE_STREAMS:validate_purchase_scope(stream,e.get('query_scope',{}),day)
            if not isinstance(e.get('rows'),list) or len(e['rows'])>page_size:raise SourceError('Invalid page rows')
            observed=e.get('total_rows')
            if observed is not None and (type(observed) is not int or observed<0):raise SourceError('Invalid source total')
            if page>1 and observed!=total:raise SourceError('Source total changed during capture; retry new capture')
            total=observed
            yield e
            if len(e['rows'])<page_size:return
            if total is not None and page*page_size>=total:return
        raise SourceError('Page budget exhausted; capture incomplete')

    def statistical_total(self, stream, day, *, query_scope):
        """Independent read-only count for a closed retail day with a proven scope."""
        if stream not in ('retail-orders', 'retail-sales'):
            raise SourceError('No accepted direct statistical count for this stream')
        if not isinstance(query_scope, dict):
            raise SourceError('Missing captured query scope for statistical verification')
        validate_retail_scope(stream, query_scope)
        from .direct_read import DirectReadClient, DirectReadError, report_params, session_auth
        expected = report_params(stream, day, day)
        for key in ('branchIdList', 'branchCodes', 'branchId1', 'saleTimeFrom',
                    'saleTimeTo', 'saleTimeRange',
                    'timeType' if stream == 'retail-sales' else 'dateType'):
            if query_scope.get(key) != expected[key]:
                raise SourceError('Direct statistics scope differs from captured list scope')
        remaining = self.max_operations - self.operations
        if remaining < 1:
            raise SourceError('Source operation budget exhausted')
        try:
            client = DirectReadClient(session_auth(), max_operations=remaining)
            try:
                return client.statistical_total(stream, day, day)
            finally:
                self.operations += client.operations
        except DirectReadError as error:
            if 'RATE_LIMIT' in str(error):
                raise SourceCooling('Shared source cooldown active') from None
            raise SourceError(str(error)) from None
        except RuntimeError as error:
            if 'RATE_LIMIT' in str(error):
                raise SourceCooling('Shared source cooldown active') from None
            raise SourceError('Direct statistical read failed; inspect source gate') from None

    def export_start(self, stream, day, capture_id, *, query_scope=None):
        raise SourceError('SOURCE_WRITE_FORBIDDEN: source export task creation is disabled')

    def export_status(self, task):
        # Caller persists task ID and schedules bounded polls. Never store signed URLs.
        rows=self.call('export-task-list','--rows','50')
        matches=[r for r in rows if str(r.get('id'))==task['task_id']]
        if len(matches)!=1:raise SourceError('Export task not in bounded result; cannot infer failure or resubmit')
        row=matches[0]
        if row.get('fileName')!=task['file_name']:raise SourceError('Export task identity mismatch')
        if str(row.get('status'))=='3' or row.get('failReason'):raise SourceError('Source export failed')
        if str(row.get('status'))!='2':return {'state':'pending','task_id':task['task_id']}
        total=row.get('totalRows')
        if isinstance(total,str) and total.isdigit():total=int(total)
        if type(total) is not int or total<0:raise SourceError('Completed export lacks trustworthy total')
        return {'state':'complete','task_id':task['task_id'],'source_rows':total}

    def recover_export(self,stream,day,capture_id):
        rows=self.call('export-task-list','--rows','100')
        matches=[r for r in rows if str(r.get('fileName','')).endswith('_'+capture_id)
                 and day in str(r.get('fileName',''))]
        if len(matches)!=1 or not matches[0].get('id'):
            raise SourceError('Cannot uniquely recover export task; inspect or abandon this capture, never resubmit blindly')
        return {'task_id':str(matches[0]['id']),'stream':stream,'day':day,'file_name':matches[0]['fileName']}

    def export_download(self, task, output_path):
        """Download a completed export via OpenCLI; the receipt is re-validated locally.

        Goes through self.call so it shares the operation budget and the
        cooldown gate with export_start/export_status. The receipt must bind
        the task identity, the task's totalRows, and the resolved output path;
        no field anywhere in the receipt may carry URL material.
        """
        output=Path(output_path)
        raw=task.get('totalRows')
        if isinstance(raw,bool) or not (type(raw) is int or (isinstance(raw,str) and raw.isdigit())):
            raise SourceError('Export task row count is invalid')
        expected=int(raw)
        rows=self.call('export-download','--task-id',str(task['task_id']),'--output',str(output))
        if len(rows)!=1 or not isinstance(rows[0],dict):
            raise SourceError('Export download did not return exactly one receipt')
        receipt=rows[0]
        if _contains_url(receipt):raise SourceError('Export download receipt carries URL material; refusing to persist it')
        if str(receipt.get('task_id') or '')!=str(task['task_id']):
            raise SourceError('Export download receipt identity mismatch')
        actual=receipt.get('rows')
        if isinstance(actual,bool) or not (type(actual) is int or (isinstance(actual,str) and actual.isdigit())) \
                or int(actual)!=expected:
            raise SourceError('Export download receipt row count mismatch')
        if not receipt.get('path') or Path(str(receipt['path'])).resolve()!=output.resolve():
            raise SourceError('Export download receipt path mismatch')
        if not output.is_file():raise SourceError('Export download artifact is missing')
        return receipt

    def document_details(self, kind, docs, directory):
        from .batch_source import document_details
        return document_details(self, kind, docs, directory)

    def document_detail(self, kind, source_id):
        """Fetch one purchase document (header + detail arrays) via OpenCLI.

        Goes through self.call so it shares the operation budget, cooldown gate,
        and error handling with export_start/export_status/export_download.
        Contract: the CLI returns an array of exactly one entity whose id echoes
        the requested source_id (CLI-side SOURCE_IDENTITY_MISMATCH; rechecked here).
        """
        if kind not in DETAIL_KINDS:raise ValueError(f'Unknown purchase detail kind: {kind}')
        if isinstance(source_id,bool) or not isinstance(source_id,(str,int)) or not str(source_id).strip():
            raise ValueError('source_id must be a non-empty string or number')
        rows=self.call('purchase-detail','--kind',kind,'--id',str(source_id))
        if len(rows)!=1 or not isinstance(rows[0],dict):
            raise SourceError('Purchase detail must return exactly one entity')
        entity=rows[0]
        if str(entity.get('id') or '')!=str(source_id):
            raise SourceError('Source detail identity mismatch; do not trust this entity')
        return entity
