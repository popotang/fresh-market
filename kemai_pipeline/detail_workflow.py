"""Bounded purchase-detail pass over day-scoped coverage gaps.

Composes the pure functions of detail_state/normalize_detail with the
Lovrabet adapter and the OpenCLI source. One writer at a time: begin/resume
hold writer_lock() for the whole pass. Captures reuse the CaptureStore state
machine under the identifiable scope stream `purchase-detail:<kind>` (legacy
capture naming, see legacy/kemai_purchase_detail_backfill.py) with the day in
window_key. Resume never trusts cursors or the manifest snapshot for skip
decisions: already-success documents are skipped by re-reading
purchase_document_detail_state.

Child rows are never deleted. Extra DB child rows mark the document partial
(stale_child_rows_require_review) and keep the day's coverage gap open.
"""
import hashlib
from contextlib import nullcontext
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .artifacts import ArtifactError
from .capabilities import CAPABILITIES
from .detail_state import DetailVerdict, classify_entity, eligible, needs_detail, next_state_row
from .locking import writer_lock
from .lovrabet_adapter import ContractError, LovrabetAdapter
from .normalize_detail import (normalize_purchase_approvals, normalize_purchase_branches,
                               normalize_purchase_lines)
from .source import DETAIL_KINDS, SourceCooling, SourceError
from .stored_payload_source import StoredPayloadVersionMismatch

TZ = ZoneInfo('Asia/Shanghai')
STREAM_KIND = {'purchase-orders': 'order', 'purchase-receipts': 'receipt',
               'purchase-returns': 'return'}
DETAIL_METHOD = 'detail_api'
DETAIL_GAP_PARTS = frozenset({'document_lines', 'branch_allocations', 'approval_history'})
STALE_CHILD_ROWS = 'stale_child_rows_require_review'
MAX_DOCUMENTS_LIMIT = 10000
# (table, verdict count key, normalizer, per-table unique key with document_id)
CHILD_TABLES = (('purchase_document_lines', 'lines', normalize_purchase_lines, 'source_line_id'),
                ('purchase_document_branches', 'branches', normalize_purchase_branches, 'source_key'),
                ('purchase_document_approvals', 'approvals', normalize_purchase_approvals, 'source_key'))
STATE_TABLE = 'purchase_document_detail_state'
STATE_SELECT = ['document_id', 'status', 'attempts', 'line_count', 'branch_count',
                'approval_count', 'source_version', 'attempted_source_version',
                'payload_hash', 'last_error', 'attempted_at', 'completed_at']


def detail_scope(stream):
    """Identifiable capture scope stream for a purchase detail pass."""
    if stream not in STREAM_KIND:
        raise ValueError(f'No purchase detail workflow for stream: {stream}')
    return f'purchase-detail:{STREAM_KIND[stream]}'


def gap_parts(gap):
    parts = gap.get('missing_parts')
    if isinstance(parts, str):
        try:
            parts = json.loads(parts)
        except ValueError:
            return None
    if not isinstance(parts, (list, tuple)):
        return None
    return {str(part) for part in parts}


def _dump(value):
    # Canonical form shared with legacy kemai_sync.dump.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, default=str)


def _sha(value):
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def counts_digest(*, documents, lines, branches, approvals):
    return _sha({'documents': documents, 'lines': lines,
                 'branches': branches, 'approvals': approvals})


def resolution_hash(resolution):
    return _sha(resolution)


def _now():
    return datetime.now(TZ).isoformat()


class DetailWorkflow:
    def __init__(self, store, source, center, *, resolve_gaps=True, method=DETAIL_METHOD,
                 write_partial=False, verification_scope='source_detail', capture_control=True):
        self.store, self.source, self.center = store, source, center
        self.resolve_gaps = resolve_gaps
        self.method = method
        self.write_partial = write_partial
        self.verification_scope = verification_scope
        self.capture_control = capture_control

    @staticmethod
    def _stream(stream):
        if stream not in STREAM_KIND:
            raise ValueError(f'No purchase detail workflow for stream: {stream}')
        return stream, STREAM_KIND[stream]

    @staticmethod
    def _day(day):
        try:
            return date.fromisoformat(str(day))
        except ValueError:
            raise ValueError(f'Invalid detail pass day: {day!r}') from None

    def _candidates(self, kind, day):
        # One batch read for the day's documents; never per-document.
        return self.center.read_all('purchase_documents', where={
            'document_kind': {'$eq': kind},
            'source_created_at': {'$gte': day.isoformat(),
                                  '$lt': (day + timedelta(days=1)).isoformat()}},
            select=['id', 'source_document_id', 'source_version', 'document_kind'])

    def _states(self, document_ids):
        # One batch read for the day; raises on placeholder datasets before any
        # source call (fail-closed via the adapter's dataset_code check).
        if not document_ids:
            return {}
        rows = self.center.read_all(STATE_TABLE,
                                    where={'document_id': {'$in': list(document_ids)}},
                                    select=STATE_SELECT)
        states = {}
        for row in rows:
            key = str(row.get('document_id'))
            if key in states:
                raise ContractError(f'Duplicate detail state rows for document {key}')
            states[key] = row
        return states

    @staticmethod
    def _view(doc, state):
        # Same shape as the legacy rows_for_day LEFT JOIN.
        state = state or {}
        return {'status': state.get('status'),
                'attempts': state.get('attempts') or 0,
                'source_version': doc.get('source_version'),
                'detail_version': state.get('source_version'),
                'attempted_source_version': state.get('attempted_source_version')}

    def _work(self, docs, states, *, retry_failed):
        work = []
        for doc in docs:
            view = self._view(doc, states.get(str(doc['id'])))
            if eligible(view, retry_failed=retry_failed):
                work.append(doc)
        return work

    def plan(self, stream, day, *, retry_failed=False):
        """Read-only pass preview: candidate documents and eligibility counts.

        Reads purchase_documents and detail_state only; zero source requests,
        zero writes, no capture creation.
        """
        stream, kind = self._stream(stream)
        day = self._day(day)
        docs = self._candidates(kind, day)
        states = self._states([doc['id'] for doc in docs])
        views = [self._view(doc, states.get(str(doc['id']))) for doc in docs]
        return {'stream': stream, 'day': day.isoformat(), 'documents': len(docs),
                'complete': sum(not needs_detail(view) for view in views),
                'eligible': sum(eligible(view, retry_failed=retry_failed) for view in views),
                'partial': sum(view['status'] == 'partial' for view in views),
                'blocked_after_retries': sum(needs_detail(view) and not eligible(view)
                                             for view in views),
                'source_requests': 0, 'db_writes': 0}

    def verify(self, stream, day):
        """Read-only verification; creates no capture and performs no writes."""
        stream, kind = self._stream(stream)
        day = self._day(day)
        docs = self._candidates(kind, day)
        states = self._states([doc['id'] for doc in docs])
        return self._verify(stream, day, docs, states)

    def begin(self, stream, day, *, max_documents=200, retry_failed=False):
        stream, kind = self._stream(stream)
        day = self._day(day)
        if isinstance(max_documents, bool) or not 1 <= max_documents <= MAX_DOCUMENTS_LIMIT:
            raise ValueError(f'max_documents must be 1..{MAX_DOCUMENTS_LIMIT}')
        with writer_lock():
            # Reads happen before capture creation and before any source call:
            # placeholder detail datasets fail closed here.
            docs = self._candidates(kind, day)
            states = self._states([doc['id'] for doc in docs])
            work = self._work(docs, states, retry_failed=retry_failed)
            state = self.store.create(tenant=self.center.config['tenant_id'],
                                      stream=detail_scope(stream),
                                      window_key=day.isoformat(),
                                      source_key_version=CAPABILITIES[stream].source_key_version,
                                      mode='final', method=self.method)
            # Eligible snapshot is evidence only; skip decisions always come
            # from detail_state, never from this list.
            state.update(eligible_snapshot=[str(doc['source_document_id']) for doc in work],
                         max_documents=max_documents, retry_failed=retry_failed)
            if getattr(self,'routing_decision',None):state['routing']=self.routing_decision
            self.store.save(state)
            if self.capture_control:
                try:
                    self.center.begin_capture(state)
                    state['server_started'] = True
                    self.store.save(state)
                except Exception:
                    state['begin_outcome'] = 'unknown'
                    self.store.save(state)
                    raise
            else:
                state['server_started'] = 'local_only'
                self.store.save(state)
            return self._run(state, docs, states, work)

    def resume(self, capture_id):
        with writer_lock():
            state = self.store.load(capture_id)
            if state['phase'] == 'interrupted':
                raise ArtifactError('Interrupted capture requires a fresh capture ID')
            if state.get('method') != self.method:
                raise ArtifactError('Capture method does not match this detail workflow')
            stream, kind, day = self._scope_parts(state)
            if not state.get('server_started') and self.capture_control:
                self.center.begin_capture(state)
                state['server_started'] = True
                self.store.save(state)
            elif not state.get('server_started'):
                state['server_started'] = 'local_only'
                self.store.save(state)
            docs = self._candidates(kind, day)
            states = self._states([doc['id'] for doc in docs])
            work = self._work(docs, states, retry_failed=bool(state.get('retry_failed')))
            return self._run(state, docs, states, work)

    def _scope_parts(self, state):
        scope = state.get('stream', '')
        prefix, _, kind = scope.partition(':')
        if prefix != 'purchase-detail' or kind not in DETAIL_KINDS:
            raise ArtifactError(f'Not a purchase detail capture: {scope!r}')
        stream = next(name for name, value in STREAM_KIND.items() if value == kind)
        return stream, kind, self._day(state['window_key'])

    def _classify(self, entity):
        return classify_entity(entity)

    def _measure(self, phase):
        return nullcontext()

    def _fetch_detail(self, state, kind, doc):
        return self.source.document_detail(kind, doc['source_document_id'])

    def _after_document(self, state, kind, doc, entity, row, stale):
        pass

    def _run(self, state, docs, states, work):
        stream, kind, day = self._scope_parts(state)
        limit = int(state.get('max_documents') or 200)
        processed = succeeded = partial = failed = 0
        try:
            for doc in work:
                if processed >= limit:
                    break
                processed += 1
                prev = states.get(str(doc['id']))
                entity, stale = None, True
                try:
                    entity = self._fetch_detail(state, kind, doc)
                    verdict = self._classify(entity)
                    if verdict.complete or self.write_partial:
                        stale = self._write_children(doc, entity, verdict)
                        row = next_state_row(prev, verdict, now=_now())
                        if not verdict.complete and self.verification_scope == 'lines_only':
                            row['last_error'] = 'lines_only_backfill; ancillary deferred'
                        elif not verdict.complete and self.verification_scope == 'stored_payload':
                            row['last_error'] = ('stored payload omitted ancillary arrays: '
                                                 + ','.join(verdict.missing))
                        if stale:
                            row.update(status='partial', last_error=STALE_CHILD_ROWS,
                                       completed_at=None)
                    else:
                        row = next_state_row(prev, verdict, now=_now())
                except StoredPayloadVersionMismatch as error:
                    stub = DetailVerdict(
                        complete=False, missing=('stored_payload_version_mismatch',),
                        counts={'lines': None, 'branches': None, 'approvals': None},
                        payload_hash=error.payload_hash,
                        source_version=(None if error.entity_version is None
                                        else str(error.entity_version)))
                    row = next_state_row(prev, stub, now=_now())
                    row['last_error'] = 'stored_payload_version_mismatch'
                except (ContractError, SourceError):
                    raise
                except Exception as error:
                    # Per-document failure (invalid arrays etc.): state row only,
                    # sanitized error, pass continues like legacy mark_failure.
                    stub = DetailVerdict(complete=False, missing=(),
                                         counts={'lines': None, 'branches': None,
                                                 'approvals': None},
                                         payload_hash=None,
                                         source_version=doc.get('source_version'))
                    row = next_state_row(prev, stub, now=_now(), error=error)
                if row.get('document_id') is None:
                    row['document_id'] = doc['id']
                with self._measure('state_write_verify'):
                    self.center.upsert(STATE_TABLE, row, unique_keys=('document_id',))
                self._after_document(state, kind, doc, entity, row, stale)
                states[str(doc['id'])] = {**(prev or {}), **row}
                succeeded += row['status'] == 'success'
                partial += row['status'] == 'partial'
                failed += row['status'] == 'failed'
        except SourceCooling:
            state['last_error'] = 'cooling_30_minutes'
            self.store.save(state)
            if self.capture_control:
                self.center.note_capture(state, 'cooling', '30-minute shared source cooldown')
            raise
        except SourceError:
            # Identity/contract violations fail the capture; the document is
            # never skipped silently.
            if self.capture_control:
                self.center.fail_capture(state, 'source_detail_contract_violation')
            if state['phase'] != 'interrupted':
                state.update(phase='interrupted', reason='source_detail_contract_violation')
            state['last_error'] = 'source_detail_contract_violation'
            self.store.save(state)
            raise
        except Exception as error:
            # Unknown writes stay resumable; never retry blindly.
            state['last_error'] = type(error).__name__
            self.store.save(state)
            if self.capture_control:
                try:
                    self.center.note_capture(state, 'failed',
                                             'Data-center outcome requires receipt reconciliation')
                except Exception:
                    pass
            raise
        base = {'capture_id': state['capture_id'], 'stream': stream,
                'day': day.isoformat(), 'documents': len(docs), 'processed': processed,
                'succeeded': succeeded, 'partial': partial, 'failed': failed}
        remainder = len(work) - processed
        if remainder:
            state['last_result'] = {**base, 'state': 'limit_reached', 'remainder': remainder}
            self.store.save(state)
            return {**base, 'state': 'limit_reached', 'remainder': remainder}
        verification = self._verify(stream, day, docs, states)
        closed = 0
        if verification['verified']:
            if self.resolve_gaps:
                closed = self._close_gaps(stream, day, state['capture_id'], verification)
            state.update(phase='committed')
        result = {**base, 'remainder': 0, 'verified': verification['verified'],
                  'gap_closed': bool(closed), 'verification': verification,
                  'verification_scope': self.verification_scope,
                  'coverage_gaps_untouched': not self.resolve_gaps,
                  'state': 'committed' if verification['verified'] else 'incomplete'}
        state['last_result'] = {key: value for key, value in result.items()
                                if key != 'verification'}
        self.store.save(state)
        if verification['verified'] and self.capture_control:
            note = ('Detail pass verified; coverage gap resolution recorded' if self.resolve_gaps
                    else 'Stored payload pass verified; coverage gaps untouched')
            try:
                self.center.note_capture(state, 'committed', note)
            except Exception:
                result['commit_note'] = 'unconfirmed'
        return result

    def _record_child_stats(self, table, stats):
        pass

    def _write_children(self, doc, entity, verdict):
        """Upsert child rows, then reconcile counts by read-back.

        Returns True when the DB holds MORE rows than the source arrays (stale
        rows are kept for review, never deleted). Fewer rows than the source
        after a confirmed upsert is a data-center inconsistency and raises.
        """
        stale = False
        for table, count_key, normalize, key_field in CHILD_TABLES:
            expected = verdict.counts[count_key]
            if expected is None:
                continue  # Omitted arrays stay unknown; never turn them into zero rows.
            rows = normalize(doc['id'], entity)
            with self._measure('children_pre_read'):
                before = self.center.read_all(table, where={'document_id': {'$eq': doc['id']}})

            result = self.center.reconcile_children(table, doc['id'], rows, before,
                                                    measure=self._measure)
            stale = stale or result['stale']
            self._record_child_stats(table, result['stats'])
        return stale

    def _verify(self, stream, day, docs, states):
        """Port of legacy verify_day: all documents success and version-aligned,
        child counts equal between state rows and DB, pending gap row counts
        consistent. Gaps carrying non-detail parts block verification."""
        success = []
        for doc in docs:
            row = states.get(str(doc['id'])) or {}
            aligned = (doc.get('source_version') is None or
                       (row.get('source_version') is not None and
                        str(doc['source_version']) == str(row['source_version'])))
            if row.get('status') == 'success' and aligned:
                success.append(doc)
        expected = {'lines': sum(int((states[str(doc['id'])]).get('line_count') or 0)
                                 for doc in success),
                    'branches': sum(int((states[str(doc['id'])]).get('branch_count') or 0)
                                    for doc in success),
                    'approvals': sum(int((states[str(doc['id'])]).get('approval_count') or 0)
                                     for doc in success)}
        ids = [doc['id'] for doc in docs]
        actual = {count_key: (self.center.count(table, where={'document_id': {'$in': ids}})
                              if ids else 0)
                  for table, count_key, _, _ in CHILD_TABLES}
        gaps = self.center.read_all('sync_coverage_gaps',
                                    where={'stream': {'$eq': stream},
                                           'scope_key': {'$eq': day.isoformat()}},
                                    select=['stream', 'scope_key', 'missing_parts',
                                            'source_row_count', 'status'])
        pending = [gap for gap in gaps if gap.get('status') == 'pending']
        gap_ok = True
        for gap in pending:
            parts = gap_parts(gap)
            if parts is None or not parts <= DETAIL_GAP_PARTS:
                gap_ok = False  # non-detail coverage is not ours to verify or close
            elif int(gap.get('source_row_count') or -1) != len(docs):
                gap_ok = False
        verified = (len(success) == len(docs) and gap_ok and
                    all(expected[key] == actual[key] for key in expected))
        return {'documents': len(docs), 'complete': len(success),
                'expected_children': expected, 'actual_children': actual,
                'pending_gaps': len(pending), 'gap_count_matches': gap_ok,
                'verified': verified}

    def _close_gaps(self, stream, day, capture_id, verification):
        """Resolve the day's detail gaps exactly once. A row already resolved
        with the same resolution_hash is a no-op; different content conflicts
        and is never overwritten."""
        counts = counts_digest(documents=verification['documents'],
                               lines=verification['expected_children']['lines'],
                               branches=verification['expected_children']['branches'],
                               approvals=verification['expected_children']['approvals'])
        resolution = {'capture_id': capture_id,
                      'documents': verification['documents'], 'counts_digest': counts}
        digest = resolution_hash(resolution)
        rows = self.center.read_all('sync_coverage_gaps',
                                    where={'stream': {'$eq': stream},
                                           'scope_key': {'$eq': day.isoformat()}},
                                    select=['stream', 'scope_key', 'status',
                                            'missing_parts', 'resolution_hash'])
        closed = 0
        for existing in rows:
            parts = gap_parts(existing)
            if parts is None or not parts <= DETAIL_GAP_PARTS:
                continue
            if existing.get('status') == 'resolved':
                if existing.get('resolution_hash') == digest:
                    continue  # idempotent no-op
                raise ContractError('Coverage gap already resolved with a different '
                                    f'resolution: {stream} {day.isoformat()}')
            now = _now()
            self.center.upsert('sync_coverage_gaps',
                               {'tenant_id': self.center.config['tenant_id'],
                                'stream': stream, 'scope_key': day.isoformat(),
                                'status': 'resolved', 'resolved_at': now,
                                'last_seen_at': now,
                                'resolution': _dump(resolution),
                                'resolution_hash': digest},
                               unique_keys=('tenant_id', 'stream', 'scope_key'))
            closed += 1
        return closed
