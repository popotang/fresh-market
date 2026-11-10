"""Line-only materialization with durable full-detail snapshots and measured I/O."""
from contextlib import contextmanager
import time
import uuid
from .artifacts import atomic_json, ArtifactError
from .contracts import digest
from .detail_workflow import DetailWorkflow
from .detail_state import eligible
from .line_evidence import LineEvidence
from .lovrabet_adapter import LovrabetAdapter, ContractError
from .normalize_detail import normalize_purchase_lines
from .source import SourceError, SourceCooling


class LinesOnlyWorkflow(DetailWorkflow):
    def __init__(self, store, source, center, **overrides):
        self.batch_source = overrides.pop('batch_source', True)
        options = dict(resolve_gaps=False, method='detail_api', write_partial=True,
                       verification_scope='lines_only', capture_control=False)
        options.update(overrides)
        super().__init__(store, source, center, **options)
        self.evidence = LineEvidence(store.root, center.config['tenant_id'])
        self._metric = None
        self._snapshots = {}

    def _proven(self, kind, docs):
        expected = {}
        for doc in docs:
            receipt = self.evidence.receipt(kind, doc)
            if not receipt or receipt.get('scope') != 'lines_only':
                continue
            cached = self.evidence.load(kind, doc)
            if not cached or receipt.get('snapshot_sha256') != cached[0]:
                continue
            rows = normalize_purchase_lines(doc['id'], cached[1]['entity'])
            if receipt.get('row_count') != len(rows):
                raise ArtifactError('Line receipt count mismatch')
            expected[str(doc['id'])] = rows
        if not expected:
            return set()
        observed = self.center.read_all('purchase_document_lines',
            where={'document_id': {'$in': list(expected)}})
        grouped = {key: [] for key in expected}
        for row in observed:
            key = str(row['document_id'])
            if key not in grouped:
                raise ArtifactError('Line read returned another document')
            grouped[key].append(row)
        proven = set()
        for key, rows in expected.items():
            actual = grouped[key]
            mapped = {str(row['source_line_id']): row for row in actual}
            if len(actual) != len(rows) or len(mapped) != len(actual):
                continue
            if all(str(row['source_line_id']) in mapped and all(
                    LovrabetAdapter._same(mapped[str(row['source_line_id'])].get(field), value)
                    for field, value in row.items()) for row in rows):
                proven.add(key)
        return proven

    def _candidates(self, kind, day):
        docs = super()._candidates(kind, day)
        proven = self._proven(kind, docs)
        return [doc for doc in docs if str(doc['id']) not in proven]

    def plan(self, stream, day, *, retry_failed=False):
        stream, kind = self._stream(stream)
        day = self._day(day)
        docs = self._candidates(kind, day)
        states = self._states([doc['id'] for doc in docs])
        work = self._work(docs, states, retry_failed=retry_failed)
        return {'stream': stream, 'day': day.isoformat(), 'documents': len(docs),
                'complete': 0, 'eligible': len(work), 'unverified_lines': len(docs),
                'blocked_after_retries': len(docs) - len(work),
                'coverage_scope': 'candidate_document_lines_only',
                'source_requests': 0, 'db_writes': 0}

    def _work(self, docs, states, *, retry_failed):
        # A generic success/partial detail state is not proof of line completeness.
        return [doc for doc in docs if (states.get(str(doc['id'])) or {}).get('status') != 'failed'
                or eligible(self._view(doc, states.get(str(doc['id']))), retry_failed=retry_failed)]

    @contextmanager
    def _measure(self, phase):
        started = time.perf_counter()
        before = getattr(self.center, 'requests', None)
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            if hasattr(self, '_phase_totals'):
                self._phase_totals[phase] = self._phase_totals.get(phase, 0) + elapsed
            if self._metric is not None:
                part = self._metric['phases'].setdefault(phase, {'seconds': 0, 'calls': 0,
                                                                'data_center_requests': 0})
                part['seconds'] += elapsed
                part['calls'] += 1
                after = getattr(self.center, 'requests', None)
                if isinstance(before, int) and isinstance(after, int):
                    part['data_center_requests'] += after - before
                else:
                    part['data_center_requests'] = None

    def _record_child_stats(self, table, stats):
        if self._metric is not None:
            self._metric.setdefault('child_changes', {})[table] = stats

    def _flush_metric(self, outcome):
        if self._metric is None:
            return
        metric = self._metric
        metric['outcome'] = outcome
        metric['total_seconds'] = time.perf_counter() - self._metric_started
        atomic_json(self.store.path(self._capture_id) / 'timings' / (uuid.uuid4().hex + '.json'), metric)
        self._measurements.append(metric)
        self._metric = None

    def _prepare_batch(self, state, kind):
        selected = self._batch_queue[:10]
        self._batch_queue = self._batch_queue[10:]
        missing = [doc for doc in selected if not self.evidence.load(kind, doc)]
        if not missing:
            return
        with self._measure('source_batch'):
            entities, metrics, error = self.source.document_details(
                kind, missing, self.store.path(state['capture_id']) / 'source-batches')
        self._batch_metrics.append(metrics)
        # Save every completed response before propagating cooling/failure.
        for doc in missing:
            entity = entities.get(str(doc['source_document_id']))
            if entity is not None:
                with self._measure('snapshot_write'):
                    cached = self.evidence.save(kind, doc, entity, state['capture_id'])
                self._batch_snapshots[str(doc['id'])] = cached
        if error:
            if isinstance(error, SourceCooling):
                raise error
            raise ContractError('SOURCE_BATCH_INCOMPLETE: retained artifacts; resume same capture') from error

    def _fetch_detail(self, state, kind, doc):
        self._metric_started = time.perf_counter()
        self._metric = {'schema_version': 1, 'document_key': digest(str(doc['id'])),
                        'phases': {}, 'source_attempts': 0, 'source_operations': 0, 'source_http_requests': None,
                        'source_count_basis': 'logical detail calls, not HTTP-level instrumentation',
                        'cache_hit': False}
        if self.batch_source and hasattr(self.source, 'document_details'):
            # The shared writer lock already spans the run. Batch only the bounded selected work.
            if self._batch_queue and str(doc['id']) == str(self._batch_queue[0]['id']):
                try:
                    self._prepare_batch(state, kind)
                except (ArtifactError, OSError, ValueError) as error:
                    raise ContractError('SOURCE_BATCH_ARTIFACT_FAILED') from error
        try:
            with self._measure('snapshot_read'):
                cached = self._batch_snapshots.get(str(doc['id'])) or self.evidence.load(kind, doc)
        except (ArtifactError, OSError, ValueError) as error:
            raise ContractError('DETAIL_SNAPSHOT_INVALID: inspect private evidence') from error
        if cached:
            self._metric['cache_hit'] = str(doc['id']) not in self._batch_snapshots
            self._metric['batch_prefetched'] = str(doc['id']) in self._batch_snapshots
        else:
            with self._measure('source_read'):
                self._metric['source_attempts'] = 1
                before = self.source.operations
                try:
                    entity = self.source.document_detail(kind, doc['source_document_id'])
                finally:
                    self._metric['source_operations'] = self.source.operations - before
            try:
                with self._measure('snapshot_write'):
                    cached = self.evidence.save(kind, doc, entity, state['capture_id'])
            except (OSError, ValueError) as error:
                raise ContractError('DETAIL_SNAPSHOT_WRITE_FAILED') from error
        sha, snapshot = cached
        entity = snapshot['entity']
        if str(entity.get('id')) != str(doc['source_document_id']):
            raise SourceError('DETAIL_IDENTITY_MISMATCH')
        if doc.get('source_version') is not None and snapshot['version'] != str(doc['source_version']):
            raise SourceError('DETAIL_VERSION_MISMATCH: refresh document list before retry')
        self._snapshots[str(doc['id'])] = (sha, entity)
        return entity

    def _after_document(self, state, kind, doc, entity, row, stale):
        if not stale and row['status'] != 'failed' and isinstance(entity.get('details'), list):
            sha, _ = self._snapshots[str(doc['id'])]
            with self._measure('receipt_write'):
                self.evidence.confirm(kind, doc, sha, len(entity['details']), state['capture_id'])
            outcome = 'lines_verified'
        else:
            outcome = 'unverified'
        self._flush_metric(outcome)

    def _run(self, state, docs, states, work):
        self._capture_id = state['capture_id']
        self._measurements = []
        self._phase_totals = {}
        self._snapshots = {}
        self._batch_snapshots = {}
        self._batch_metrics = []
        self._batch_queue = list(work[:int(state.get('max_documents') or 200)])
        source_before = self.source.operations
        started = time.perf_counter()
        requests_before = getattr(self.center, 'requests', None)
        try:
            result = super()._run(state, docs, states, work)
            return result
        finally:
            self._flush_metric('interrupted')
            after = getattr(self.center, 'requests', None)
            summary = {'schema_version': 1, 'seconds': time.perf_counter() - started,
                       'documents_measured': len(self._measurements),
                       'source_operations': self.source.operations - source_before,
                       'cache_hits': sum(m['cache_hit'] for m in self._measurements),
                       'data_center_requests': after - requests_before
                       if isinstance(after, int) and isinstance(requests_before, int) else None,
                       'source_http_requests': None, 'scope': 'run only; excludes initial planning reads',
                       'phases': dict(self._phase_totals)}
            summary['source_batches'] = self._batch_metrics
            summary['source_count_basis'] = 'batch source slots conservatively reserved; not exact HTTP requests'
            summary['child_changes'] = [m.get('child_changes', {}) for m in self._measurements]
            # Keep each attempt, including failed attempts, rather than overwriting history.
            atomic_json(self.store.path(state['capture_id']) / 'timings' /
                        ('summary-' + uuid.uuid4().hex + '.json'), summary)

    def _verify(self, stream, day, docs, states):
        _, kind = self._stream(stream)
        with self._measure('lines_verify'):
            proven = self._proven(kind, docs)
        return {'verified': len(proven) == len(docs), 'documents': len(docs),
                'complete': len(proven), 'coverage_scope': 'selected_document_lines_only',
                'ancillary': 'deferred'}

    def _classify(self, entity):
        verdict = super()._classify(entity)
        return verdict._replace(complete=False, counts={
            **verdict.counts, 'branches': None, 'approvals': None})
