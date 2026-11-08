"""OpenCLI-only source port. No DB imports, direct HTTP, or browser scripting."""
import json
import subprocess
import time
from pathlib import Path
from .capabilities import CAPABILITIES
from kemai_rate_limit import is_rate_limited, rate_limit_signal, record_limit
from kemai_source_gate import cooldown_until, gate_dir


class SourceError(RuntimeError): pass
class SourceCooling(SourceError): pass
class SourceRejected(SourceError): pass


PURCHASE_STREAMS=("purchase-orders","purchase-receipts","purchase-returns")


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
    allowed={profile['time_field'],*profile['window_fields'],*profile['must_be_empty']}
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
        if result.returncode and 'EXPORT_REJECTED' in result.stderr:
            raise SourceRejected('Source explicitly rejected the export; no successful submission confirmed')
        if result.returncode: raise SourceError('OpenCLI failed; check session and command contract')
        try: data=json.loads(result.stdout)
        except ValueError as error: raise SourceError('Source JSON invalid') from error
        if rate_limit_signal(data):
            record_limit(command,'RATE_LIMIT: HTTP/business response',wait=False)
            raise SourceCooling('Rate limited; persisted 30-minute cooldown')
        if not isinstance(data,list): raise SourceError('Expected OpenCLI record array')
        return data

    def pages(self, stream, day, *, page_size=1000, until=None):
        cap=CAPABILITIES[stream]
        if cap.paused or cap.granularity!='day': raise SourceError('Stream requires separate object workflow')
        if not 1<=page_size<=1000: raise ValueError('Invalid page size')
        total=None
        for page in range(1,self.max_pages+1):
            result=self.call('capture-page','--kind',stream,'--from',day,'--to',day,
                             '--page',str(page),'--rows',str(page_size),*(['--until',until] if until else []))
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

    def export_start(self, stream, day, capture_id, *, query_scope=None):
        if stream not in ('retail-orders','retail-sales','purchase-receive-history'):
            raise SourceError('No verified export command')
        extra=[]
        if stream.startswith('retail-'):
            extra=['--time-type','1']
            if query_scope is not None:extra+=['--scope-json',json.dumps(query_scope,ensure_ascii=False)]
        rows=self.call(stream+'-export-start','--from',day,'--to',day,'--capture-id',capture_id,*extra)
        if len(rows)!=1 or not rows[0].get('id'):raise SourceError('Export submission result unknown; do not resubmit blindly')
        row=rows[0];reused=row.get('reuse_reason')=='server_24h_dedup'
        if reused:
            if not str(row.get('requested_file_name','')).endswith('_'+capture_id) or str(row.get('taskNo'))!=row.get('reused_task_no'):
                raise SourceError('Source export reuse evidence mismatch')
        elif not str(row.get('fileName','')).endswith('_'+capture_id):raise SourceError('Export capture identity mismatch')
        return {'task_id':str(row['id']),'stream':stream,'day':day,'file_name':row['fileName'],
                'reused':reused,'reuse_reason':row.get('reuse_reason'),'source_task_no':row.get('taskNo'),
                'export_created_at':row.get('createTime')}

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
