"""One bounded window: capture once, export count, then resumable ingestion."""
import hashlib
from datetime import date, datetime
from zoneinfo import ZoneInfo
from .artifacts import ArtifactError
from .capabilities import CAPABILITIES, capture_method
from .export_mappings import HISTORY_EXPORT_FIELDS
from .locking import writer_lock
from .mapping_verification import load_verification
from .normalize import normalize, normalize_history_export_row
from .runner import ingest_capture
from .preparation import prepare_ingestion
from .source import SourceError, SourceCooling, SourceRejected, PURCHASE_STREAMS, OpenCLISource
from .xlsx_import import XlsxImportError, parse_history_xlsx


def resolve_capture_method(stream, mode, method=None, *, root=None, export_mapping_verified=None):
    """Resolve legacy manifests without rewriting them; reject unsafe fallbacks.

    export_for_data is unlocked only by a verified mapping artifact under the
    capture store root (load_verification reads <root>/export-mapping/<stream>.json).
    Caller-supplied export_mapping_verified is accepted for compatibility and
    ignored: the artifact is the only authority.
    """
    capability=CAPABILITIES[stream]
    verified=False
    if capability.export_role=='data_with_validated_mapping' and root is not None:
        verified=load_verification(stream,root) is not None
    expected=capture_method(stream,provisional=mode=='provisional',export_mapping_verified=verified)
    resolved=method or expected
    if resolved=='export_for_data' and not verified:
        raise SourceError('EXECUTION_METHOD_NOT_IMPLEMENTED: XLSX row importer is not available')
    # Mapping verification permits Excel; it must not disable the existing API path.
    allowed={expected}
    if capability.export_role=='data_with_validated_mapping':
        allowed.add('api_only')
    if resolved not in allowed:
        raise SourceError(f'CAPTURE_METHOD_CONFLICT: expected {expected}, got {resolved}')
    return resolved


def _sha256_file(path):
    digest=hashlib.sha256()
    with open(path,'rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


class WindowWorkflow:
    def __init__(self, store, source, center):
        self.store,self.source,self.center=store,source,center

    def begin(self, stream, day, *, mode='final', method=None):
        method=resolve_capture_method(stream,mode,method,root=self.store.root)
        if method=='export_for_data' and isinstance(self.source,OpenCLISource):
            raise SourceError('SOURCE_WRITE_FORBIDDEN: new source export tasks are disabled')
        day=date.fromisoformat(day)
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        cap=CAPABILITIES[stream]
        if cap.paused or cap.granularity!='day':raise ValueError('Stream is paused or requires the object workflow')
        if day>today or (mode=='final' and day>=today):raise ValueError('Final windows must be closed days')
        with writer_lock():
            state=self.store.create(tenant=self.center.config['tenant_id'],stream=stream,
                window_key=day.isoformat(),source_key_version=cap.source_key_version,mode=mode,method=method)
            if getattr(self,'routing_decision',None):
                state['routing']=self.routing_decision
                self.store.save(state)
            if mode=='provisional' and stream.startswith('retail-'):
                state['as_of_cutoff']=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S') if day==today else day.isoformat()+' 23:59:59'
                self.store.save(state)
            # Save capture ID before first server write. Unknown begin result resumes same ID.
            try:
                self.center.begin_capture(state)
                state['server_started']=True;self.store.save(state)
            except Exception:
                state['begin_outcome']='unknown';self.store.save(state);raise
            return self._resume(state)

    def resume(self,capture_id):
        with writer_lock():
            state=self.store.load(capture_id)
            resolve_capture_method(state['stream'],state['mode'],state.get('method'),root=self.store.root)
            if not state.get('server_started'):
                self.center.begin_capture(state);state['server_started']=True;self.store.save(state)
            if state['phase']=='capturing' and state['pages'] and not state.get('pages_complete'):
                # No reliable source cursor: don't join old pages with a new snapshot.
                state=self.store.interrupt_incomplete(capture_id)
                self.center.fail_capture(state,'unstable_offset_requires_new_capture')
                return {'capture_id':capture_id,'state':'interrupted','next_action':'begin_new_capture'}
            return self._resume(state)

    def abandon(self,capture_id):
        """Release a failed attempt; keep artifacts and already committed rows/receipts."""
        with writer_lock():
            state=self.store.load(capture_id)
            if state['phase']=='committed':raise ArtifactError('A committed capture cannot be abandoned')
            self.center.fail_capture(state,'operator_abandoned_capture')
            state.update(phase='interrupted',reason='operator_abandoned_capture');self.store.save(state)
            return {'capture_id':capture_id,'state':'interrupted','data_deleted':False}

    def _resume(self,state):
        stream,day=state['stream'],state['window_key']
        if state['phase']=='interrupted':raise ArtifactError('Interrupted capture requires a fresh capture ID')
        method=resolve_capture_method(stream,state['mode'],state.get('method'),root=self.store.root)
        self.center.bind_capture(state)
        try:
            if state['phase']=='capturing' and method=='export_for_data':
                outcome=self._capture_export_data(state)
                if outcome is not None:return outcome
            else:
                if state['phase']=='capturing' and not state.get('pages_complete'):
                    total=None;seen=set();observed_at=None
                    for envelope in self.source.pages(stream,day,until=state.get('as_of_cutoff')):
                        scope=envelope.get('query_scope')
                        if scope is not None:
                            if 'query_scope' in state and scope!=state['query_scope']:raise SourceError('Query scope changed between pages')
                            state['query_scope']=scope
                        rows=[normalize(stream,r) for r in envelope['rows']]
                        keys=[r['source_key'] for r in rows]
                        if len(set(keys))!=len(keys) or seen.intersection(keys):raise SourceError('Duplicate keys across source pages')
                        seen.update(keys);total=envelope['total_rows'];observed_at=envelope['observed_at']
                        self.store.append_page(state,rows)
                    if total is not None and total!=len(seen):raise SourceError('Source total and unique rows disagree')
                    state.update(pages_complete=True,unique_rows=len(seen),observed_at=observed_at)
                    self.store.save(state)
                if state['phase']=='capturing':
                    evidence={'source_rows':state['unique_rows'],'source_observed_at':state['observed_at'],
                        'verification_method':'single_scan_count' if state['mode']=='final' else 'provisional_single_scan',
                        'coverage_scope':'list','detail_coverage':'not_checked','snapshot_cutoff':state.get('as_of_cutoff')}
                    if state.get('query_scope') is not None:evidence['query_scope']=state['query_scope']
                    evidence['window_time_field']=CAPABILITIES[stream].date_field
                    evidence['source_total_reliable']=CAPABILITIES[stream].export_role=='count_only'
                    if stream in PURCHASE_STREAMS:
                        evidence['late_modification_policy']='create_day_window_with_previous_closed_day_revisit'
                    if stream=='retail-orders':evidence['comparison_policy']='retail-order-query-metadata-v1'
                    if state.get('routing'):evidence['routing']=state['routing']
                    if state['mode']=='final' and state['unique_rows']==0:
                        evidence['verification_method']='empty_page_and_db'
                    if state['mode']=='final' and state['unique_rows']>0 and CAPABILITIES[stream].export_role=='count_only':
                        if isinstance(self.source,OpenCLISource):
                            total=self.source.statistical_total(stream,day,query_scope=state.get('query_scope'))
                            if total!=state['unique_rows']:
                                self.center.fail_capture(state,'independent_statistical_count_mismatch')
                                state.update(phase='interrupted',reason='independent_statistical_count_mismatch');self.store.save(state)
                                raise SourceError('Independent statistical total differs; new capture required')
                            evidence.update(verification_method='statistical_total_and_capture',
                                            statistical_total=total)
                        elif state.get('export_submission')=='unknown':
                            task=self.source.recover_export(stream,day,state['capture_id'])
                            state.update(export_task=task,export_submission='known');self.store.save(state)
                        if not isinstance(self.source,OpenCLISource) and not state.get('export_task'):
                            state['export_submission']='unknown';self.store.save(state)
                            try:task=self.source.export_start(stream,day,state['capture_id'],query_scope=state.get('query_scope'))
                            except SourceRejected:
                                state['export_submission']='rejected';self.store.save(state);raise
                            state.update(export_task=task,export_submission='known');self.store.save(state)
                        if not isinstance(self.source,OpenCLISource):
                            result=self.source.export_status(state['export_task'])
                            if result['state']=='pending':
                                self.center.note_capture(state,'awaiting_export','Exact export task pending; capture retained')
                                return {'capture_id':state['capture_id'],'state':'awaiting_export','next_action':'resume_same_capture'}
                            if result['source_rows']!=state['unique_rows']:
                                self.center.fail_capture(state,'independent_export_count_mismatch')
                                state.update(phase='interrupted',reason='independent_export_count_mismatch');self.store.save(state)
                                raise SourceError('Independent export total differs; new capture required')
                            evidence.update(verification_method='export_total_and_capture',export_total=result['source_rows'],export_task_id=result['task_id'])
                            if state['export_task'].get('reused'):
                                evidence.update(verification_method='reused_export_total_and_capture',export_reuse_reason=state['export_task']['reuse_reason'],
                                    export_created_at=state['export_task'].get('export_created_at'),source_task_no=state['export_task'].get('source_task_no'))
                    self.store.seal(state,evidence)
            if callable(getattr(self.center,'page',None)):
                prepare_ingestion(self.store,state,self.center)
            return ingest_capture(self.store,state['capture_id'],self.center,max_rows=50)
        except SourceCooling:
            # Capture/export task is retained. Next invocation honors shared cooldown.
            state['last_error']='cooling_30_minutes';self.store.save(state)
            self.center.note_capture(state,'cooling','30-minute shared source cooldown')
            raise
        except SourceError:
            state['last_error']='source_capture_failed';self.store.save(state)
            # Leave artifacts recoverable; failure event never advances a checkpoint.
            if method=='export_for_data' and state.get('export_submission')=='unknown':
                self.center.note_capture(state,'awaiting_export','Submission outcome unknown; recover exact capture task')
            elif not state.get('pages_complete'):
                if state['phase']!='interrupted':  # export_for_data parse failures already failed with a precise reason
                    self.center.fail_capture(state,'source_capture_failed')
                    state['phase']='interrupted';self.store.save(state)
            elif state['phase']!='interrupted':
                self.center.note_capture(state,'failed','Source validation or export failed; capture retained')
            raise
        except Exception as error:
            # Unknown writes remain resumable. Never change capture/batch IDs to retry.
            state=self.store.load(state['capture_id'])
            state['last_error']=type(error).__name__;self.store.save(state)
            try:self.center.note_capture(state,'failed','Data-center outcome requires receipt reconciliation')
            except Exception:pass  # DB outage must not mask the original error or claim a log persisted.
            raise

    def _capture_export_data(self,state):
        """export_for_data: the verified XLSX export is both data and count evidence.

        Returns the awaiting_export outcome dict while the task is pending;
        otherwise seals the capture and returns None so ingestion proceeds.
        """
        stream,day=state['stream'],state['window_key']
        if not state.get('export_task'):
            if state.get('export_submission')=='unknown':
                task=self.source.recover_export(stream,day,state['capture_id'])
            else:
                state['export_submission']='unknown';self.store.save(state)
                operations_before=self.source.operations
                try:
                    task=self.source.export_start(stream,day,state['capture_id'],query_scope=state.get('query_scope'))
                except SourceRejected:
                    state['export_submission']='rejected';self.store.save(state)
                    raise
                except SourceError:
                    # A shared cooldown/budget preflight can reject before OpenCLI is invoked.
                    # Only a proven zero-operation rejection may allow a later first submission.
                    if self.source.operations==operations_before:
                        state['export_submission']='not_started';self.store.save(state)
                    raise
            state.update(export_task=task,export_submission='known');self.store.save(state)
        task=state['export_task']
        xlsx_path=self.store.path(state['capture_id'])/'export.xlsx'
        receipt=state.get('export_receipt')
        reusable=(receipt and receipt.get('task_id')==task['task_id'] and type(receipt.get('rows')) is int
                  and receipt['rows']>=0 and xlsx_path.is_file()
                  and _sha256_file(xlsx_path)==receipt.get('sha256'))
        result=({'state':'complete','source_rows':receipt['rows']} if reusable else self.source.export_status(task))
        if result['state']=='pending':
            self.center.note_capture(state,'awaiting_export','Exact export task pending; capture retained')
            return {'capture_id':state['capture_id'],'state':'awaiting_export','next_action':'resume_same_capture'}
        total=result['source_rows']
        if receipt and receipt.get('task_id')==task['task_id'] and receipt.get('rows')==total \
                and xlsx_path.is_file() and _sha256_file(xlsx_path)==receipt.get('sha256'):
            pass  # Idempotent resume: verified artifact is reused, never re-downloaded.
        else:
            downloaded=self.source.export_download({**task,'totalRows':total},xlsx_path)
            file_sha=_sha256_file(xlsx_path)
            if downloaded.get('sha256') and downloaded['sha256']!=file_sha:
                raise SourceError('Downloaded export digest disagrees with its receipt')
            receipt={'task_id':downloaded['task_id'],'sha256':file_sha,'rows':downloaded['rows']}
            state['export_receipt']=receipt;self.store.save(state)
        try:
            parsed=parse_history_xlsx(xlsx_path,expected_rows=total,mapping=HISTORY_EXPORT_FIELDS)
            rows=[normalize_history_export_row(row) for row in parsed]
        except XlsxImportError as error:
            reason='xlsx_row_count_mismatch' if 'row count mismatch' in str(error) else 'xlsx_import_invalid'
            self.center.fail_capture(state,reason)
            state.update(phase='interrupted',reason=reason);self.store.save(state)
            raise SourceError('XLSX export content failed verified-mapping validation; new capture required') from error
        except ValueError:
            # Row normalization failures land here (errors are sanitized at the
            # normalize layer). The capture fails with the same fixed code —
            # never with source cell text — and the original error is not chained.
            reason='xlsx_import_invalid'
            self.center.fail_capture(state,reason)
            state.update(phase='interrupted',reason=reason);self.store.save(state)
            raise SourceError('XLSX export row failed verified normalization; new capture required') from None
        if not state.get('pages_complete'):
            self.store.append_page(state,rows)
            state.update(pages_complete=True,unique_rows=len(rows),observed_at=None)
            self.store.save(state)
        verification=load_verification(stream,self.store.root)
        evidence={'source_rows':total,'verification_method':'xlsx_rows_v1',
            'coverage_scope':'list','detail_coverage':'not_checked',
            'window_time_field':CAPABILITIES[stream].date_field,
            'export_task_id':task['task_id'],'export_created_at':task.get('export_created_at'),
            'export_reused':bool(task.get('reused')),'source_observed_at':state.get('observed_at'),
            'xlsx_sha256':receipt['sha256'],'export_total':total,'parsed_rows':len(rows),
            'mapping_version':verification['mapping_version'],'header_sha256':verification['header_sha256']}
        if state.get('routing'):evidence['routing']=state['routing']
        self.store.seal(state,evidence)
        return None
