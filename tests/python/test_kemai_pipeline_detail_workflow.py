import io
import json
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import kemai_syncctl
from kemai_pipeline.artifacts import CaptureStore
from kemai_pipeline.audit import check_history
from kemai_pipeline.detail_workflow import (DETAIL_METHOD, DetailWorkflow, counts_digest,
                                            detail_scope, resolution_hash)
from kemai_pipeline.lovrabet_adapter import ContractError, LovrabetAdapter
from kemai_pipeline.operator import _execute_tasks
from kemai_pipeline.planner import incremental_plan
from kemai_pipeline.source import SourceCooling, SourceError

DAY = '2026-11-08'
NOW = datetime(2026, 11, 18, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
BASE_STATE = {'baseline_verified': True, 'baseline_from': '2025-01-01',
              'last_contiguous_verified_day': '2026-11-07'}


def make_entity(source_id, version='7', lines=2, branches=1, approvals=1):
    entity = {'id': str(source_id), 'version': version,
              'details': [{'id': f'{source_id}-L{i}', 'itemId': 'it1', 'qty': 10000,
                           'price': 5000, 'amt': 50000, 'taxAmt': 0} for i in range(lines)],
              'approves': [{'id': f'{source_id}-A{i}', 'approverId': 'u1', 'status': '1',
                            'approveTime': '2026-11-08 10:00:00'} for i in range(approvals)]}
    if branches is not None:
        entity['branchs'] = [{'id': f'{source_id}-B{i}', 'branchId': 'b1'}
                             for i in range(branches)]
    return entity


def make_doc(doc_id=101, source_id='5001', version='7', kind='order', day=DAY):
    return {'id': doc_id, 'tenant_id': 't', 'document_kind': kind,
            'source_document_id': source_id, 'source_version': version,
            'source_created_at': f'{day} 09:00:00'}


def success_state(doc_id=101, version='7', lines=2, branches=1, approvals=1):
    return {'document_id': doc_id, 'status': 'success', 'attempts': 1,
            'line_count': lines, 'branch_count': branches, 'approval_count': approvals,
            'source_version': version, 'attempted_source_version': version,
            'payload_hash': 'h', 'last_error': None,
            'attempted_at': '2026-11-08T10:00:00+08:00',
            'completed_at': '2026-11-08T10:00:00+08:00'}


def pending_gap(day=DAY, stream='purchase-orders', documents=1, parts=('document_lines',)):
    return {'id': 55, 'tenant_id': 't', 'stream': stream, 'scope_key': day,
            'missing_parts': list(parts), 'source_row_count': documents, 'status': 'pending',
            'first_seen_at': day, 'last_seen_at': day}


def _match(row, where):
    for key, cond in (where or {}).items():
        if not isinstance(cond, dict):
            cond = {'$eq': cond}
        for op, value in cond.items():
            actual = row.get(key)
            if op == '$eq':
                if actual is None or str(actual) != str(value):
                    return False
            elif op == '$in':
                if str(actual) not in {str(v) for v in value}:
                    return False
            elif op == '$gte':
                if actual is None or str(actual) < str(value):
                    return False
            elif op == '$lt':
                if actual is None or str(actual) >= str(value):
                    return False
            else:
                raise AssertionError(f'unsupported operator {op}')
    return True


class FakeDetailCenter:
    def __init__(self, *, documents=(), states=(), gaps=(), children=None):
        self.config = {'tenant_id': 't', 'appcode': 'app-test'}
        self.tables = {'purchase_documents': [dict(r) for r in documents],
                       'purchase_document_detail_state': [dict(r) for r in states],
                       'sync_coverage_gaps': [dict(r) for r in gaps],
                       'purchase_document_lines': [],
                       'purchase_document_branches': [],
                       'purchase_document_approvals': []}
        for table, rows in (children or {}).items():
            self.tables[table] = [{'id': 90000 + i, **r} for i, r in enumerate(rows)]
        self.ops = []
        self.fail_upsert_once = None
        self._next_id = 100000

    def read_all(self, table, *, where=None, select=None):
        self.ops.append(('read_all', table))
        return [dict(row) for row in self.tables[table] if _match(row, where)]

    def batch_create(self, table, rows, *, unique_keys, preflight=True):
        self.ops.append(('batch_create', table))
        if self.fail_upsert_once == table:
            self.fail_upsert_once = None
            raise ContractError('UPSERT_OUTCOME_UNKNOWN: simulated transport loss')
        created = []
        for row in rows:
            existing = next((stored for stored in self.tables[table]
                             if all(str(stored.get(k)) == str(row.get(k)) for k in unique_keys)), None)
            if existing is not None:
                raise ContractError('BATCH_CREATE_CONFLICT')
            stored = {'id': self._next_id, 'tenant_id': 't', **row}
            self._next_id += 1
            self.tables[table].append(stored)
            created.append(dict(stored))
        return {'action': 'created', 'count': len(created), 'rows': created}

    def reconcile_children(self, table, document_id, rows, before, *, measure=None):
        from kemai_pipeline.child_batch import reconcile, TABLE_KEYS
        client = LovrabetAdapter({'tenant_id': 't', 'datasets': {table: 'fake'}, 'appcode': 'fake'})
        client.read_all = self.read_all
        def invoke(*args):
            payload = json.loads(args[args.index('--params') + 1])
            if args[1] == 'batchCreate':
                return self.batch_create(table, payload, unique_keys=('document_id', TABLE_KEYS[table]))
            for pk in payload['id']:
                old = next(r for r in self.tables[table] if str(r['id']) == str(pk))
                self.upsert(table, {**old, **{k: v for k, v in payload.items() if k != 'id'}},
                            unique_keys=('document_id', TABLE_KEYS[table]))
        client.invoke = invoke
        return reconcile(client, table, document_id, rows, before, measure=measure)

    def count(self, table, *, where=None):
        self.ops.append(('count', table))
        return sum(1 for row in self.tables[table] if _match(row, where))

    def upsert(self, table, row, *, unique_keys):
        self.ops.append(('upsert', table))
        if self.fail_upsert_once == table:
            self.fail_upsert_once = None
            raise ContractError('UPSERT_OUTCOME_UNKNOWN: simulated transport loss')
        existing = next((stored for stored in self.tables[table]
                         if all(str(stored.get(k)) == str(row.get(k)) for k in unique_keys)), None)
        if existing is None:
            stored = {'id': self._next_id, 'tenant_id': 't', **row}
            self._next_id += 1
            self.tables[table].append(stored)
            return {'action': 'created', 'row': dict(stored)}
        changed = {k: v for k, v in row.items()
                   if k not in unique_keys and existing.get(k) != v}
        existing.update(changed)
        return {'action': 'updated' if changed else 'unchanged', 'row': dict(existing)}

    def begin_capture(self, state):
        self.ops.append(('begin_capture', state['capture_id']))

    def note_capture(self, state, status, reason):
        self.ops.append(('note_capture', status))

    def fail_capture(self, state, reason):
        self.ops.append(('fail_capture', reason))

    def op_index(self, op):
        return next(i for i, entry in enumerate(self.ops) if entry == op)


class FakeDetailSource:
    def __init__(self, entities=None, errors=None, lock_probe=None):
        self.entities = entities or {}
        self.errors = errors or {}
        self.lock_probe = lock_probe
        self.operations = 0
        self.fetched = []

    def document_detail(self, kind, source_id):
        if self.lock_probe is not None and self.lock_probe.held != 1:
            raise AssertionError('writer lock not held during source call')
        self.operations += 1
        self.fetched.append((kind, str(source_id)))
        error = self.errors.get(str(source_id))
        if error:
            raise error
        return self.entities[str(source_id)]


class LockProbe:
    def __init__(self):
        self.held = 0
        self.acquisitions = 0

    @contextmanager
    def __call__(self, *args, **kwargs):
        self.acquisitions += 1
        self.held += 1
        try:
            yield
        finally:
            self.held -= 1


def make_workflow(center, source, root):
    return DetailWorkflow(CaptureStore(root), source, center)


class PlanTests(unittest.TestCase):
    def test_plan_is_read_only_and_counts_candidates_and_eligible(self):
        center = FakeDetailCenter(
            documents=[make_doc(), make_doc(doc_id=102, source_id='5002')],
            states=[success_state(101)])
        source = FakeDetailSource()
        with tempfile.TemporaryDirectory() as root:
            result = make_workflow(center, source, root).plan('purchase-orders', DAY)
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertEqual(result['documents'], 2)
        self.assertEqual(result['complete'], 1)
        self.assertEqual(result['eligible'], 1)
        self.assertEqual(result['source_requests'], 0)
        self.assertEqual(result['db_writes'], 0)
        self.assertEqual(source.operations, 0)
        self.assertFalse(any(op[0] in ('upsert', 'batch_create', 'begin_capture',
                                       'note_capture', 'fail_capture') for op in center.ops))

    def test_non_purchase_streams_are_rejected(self):
        center = FakeDetailCenter()
        with tempfile.TemporaryDirectory() as root:
            workflow = make_workflow(center, FakeDetailSource(), root)
            for stream in ('retail-sales', 'purchase-receive-history', 'retail-order-detail'):
                with self.subTest(stream=stream), self.assertRaises(ValueError):
                    workflow.begin(stream, DAY)
                with self.subTest(stream=stream, op='plan'), self.assertRaises(ValueError):
                    workflow.plan(stream, DAY)


class BeginPassTests(unittest.TestCase):
    def test_complete_document_writes_children_then_success_then_closes_gap(self):
        center = FakeDetailCenter(documents=[make_doc()], gaps=[pending_gap()])
        source = FakeDetailSource({'5001': make_entity('5001')})
        with tempfile.TemporaryDirectory() as root, patch(
                'kemai_pipeline.detail_workflow.writer_lock', LockProbe()):
            result = make_workflow(center, source, root).begin('purchase-orders', DAY)
        self.assertEqual(result['state'], 'committed')
        self.assertTrue(result['gap_closed'])
        self.assertEqual(len(center.tables['purchase_document_lines']), 2)
        self.assertEqual(len(center.tables['purchase_document_branches']), 1)
        self.assertEqual(len(center.tables['purchase_document_approvals']), 1)
        state_row = center.tables['purchase_document_detail_state'][0]
        self.assertEqual(state_row['status'], 'success')
        self.assertEqual(state_row['document_id'], 101)
        self.assertEqual(state_row['line_count'], 2)
        gap = center.tables['sync_coverage_gaps'][0]
        self.assertEqual(gap['status'], 'resolved')
        resolution = json.loads(gap['resolution'])
        self.assertEqual(resolution['capture_id'], result['capture_id'])
        self.assertEqual(resolution['documents'], 1)
        self.assertEqual(resolution['counts_digest'],
                         counts_digest(documents=1, lines=2, branches=1, approvals=1))
        self.assertEqual(gap['resolution_hash'], resolution_hash(resolution))
        # Child rows are bulk-created and read back before the success state;
        # the success state is written before the gap is closed.
        lines_batch = center.op_index(('batch_create', 'purchase_document_lines'))
        state_write = center.op_index(('upsert', 'purchase_document_detail_state'))
        self.assertLess(lines_batch, state_write)
        self.assertLess(state_write,
                        center.op_index(('upsert', 'sync_coverage_gaps')))
        lines_reads = [i for i, op in enumerate(center.ops)
                       if op == ('read_all', 'purchase_document_lines')]
        self.assertGreaterEqual(len(lines_reads), 2)
        self.assertLess(lines_reads[0], lines_batch)
        self.assertLess(lines_batch, lines_reads[-1])
        self.assertFalse(any(op == ('upsert', 'purchase_document_lines') for op in center.ops))
        self.assertLess(center.op_index(('begin_capture', result['capture_id'])), lines_batch)
        self.assertEqual(source.fetched, [('order', '5001')])

    def test_partial_document_only_writes_state_and_never_closes_gap(self):
        center = FakeDetailCenter(documents=[make_doc()], gaps=[pending_gap()])
        source = FakeDetailSource({'5001': make_entity('5001', branches=None)})
        with tempfile.TemporaryDirectory() as root:
            result = make_workflow(center, source, root).begin('purchase-orders', DAY)
        self.assertEqual(result['state'], 'incomplete')
        self.assertFalse(result['gap_closed'])
        upserted = [table for op, table in center.ops if op == 'upsert']
        self.assertEqual(upserted, ['purchase_document_detail_state'])
        state_row = center.tables['purchase_document_detail_state'][0]
        self.assertEqual(state_row['status'], 'partial')
        self.assertEqual(state_row['last_error'],
                         'source omitted ancillary arrays: branch_allocations')
        self.assertEqual(center.tables['sync_coverage_gaps'][0]['status'], 'pending')

    def test_extra_child_rows_mark_partial_without_deletion_and_gap_stays_open(self):
        seeded = [{'document_id': 101, 'source_line_id': '5001-L0'},
                  {'document_id': 101, 'source_line_id': '5001-L1'},
                  {'document_id': 101, 'source_line_id': '5001-STALE'}]
        center = FakeDetailCenter(documents=[make_doc()], gaps=[pending_gap()],
                                  children={'purchase_document_lines': seeded})
        source = FakeDetailSource({'5001': make_entity('5001')})
        with tempfile.TemporaryDirectory() as root:
            result = make_workflow(center, source, root).begin('purchase-orders', DAY)
        self.assertEqual(result['state'], 'incomplete')
        state_row = center.tables['purchase_document_detail_state'][0]
        self.assertEqual(state_row['status'], 'partial')
        self.assertEqual(state_row['last_error'], 'stale_child_rows_require_review')
        self.assertEqual(len(center.tables['purchase_document_lines']), 3)
        self.assertEqual(center.tables['sync_coverage_gaps'][0]['status'], 'pending')

    def test_upsert_unknown_outcome_propagates_without_blind_retry(self):
        center = FakeDetailCenter(documents=[make_doc()], gaps=[pending_gap()])
        center.fail_upsert_once = 'purchase_document_lines'
        source = FakeDetailSource({'5001': make_entity('5001')})
        with tempfile.TemporaryDirectory() as root:
            workflow = make_workflow(center, source, root)
            with self.assertRaisesRegex(ContractError, 'UPSERT_OUTCOME_UNKNOWN'):
                workflow.begin('purchase-orders', DAY)
        attempts = [op for op in center.ops if op == ('batch_create', 'purchase_document_lines')]
        self.assertEqual(len(attempts), 1)
        self.assertNotIn(('upsert', 'sync_coverage_gaps'),
                         [op for op in center.ops if op[0] == 'upsert'])
        self.assertIn(('note_capture', 'failed'), center.ops)

    def test_writer_lock_is_held_for_the_whole_pass(self):
        center = FakeDetailCenter(documents=[make_doc()], gaps=[pending_gap()])
        probe = LockProbe()
        source = FakeDetailSource({'5001': make_entity('5001')}, lock_probe=probe)
        with tempfile.TemporaryDirectory() as root:
            with patch('kemai_pipeline.detail_workflow.writer_lock', probe):
                make_workflow(center, source, root).begin('purchase-orders', DAY)
        self.assertEqual(probe.acquisitions, 1)
        self.assertEqual(probe.held, 0)
        self.assertEqual(source.operations, 1)

    def test_max_documents_stops_and_reports_remainder(self):
        docs = [make_doc(), make_doc(doc_id=102, source_id='5002'),
                make_doc(doc_id=103, source_id='5003')]
        center = FakeDetailCenter(documents=docs, gaps=[pending_gap(documents=3)])
        source = FakeDetailSource({str(s): make_entity(s)
                                   for s in ('5001', '5002', '5003')})
        with tempfile.TemporaryDirectory() as root:
            result = make_workflow(center, source, root).begin(
                'purchase-orders', DAY, max_documents=2)
        self.assertEqual(result['state'], 'limit_reached')
        self.assertEqual(result['processed'], 2)
        self.assertEqual(result['remainder'], 1)
        self.assertEqual(len(source.fetched), 2)
        self.assertEqual(center.tables['sync_coverage_gaps'][0]['status'], 'pending')

    def test_identity_mismatch_fails_capture_instead_of_skipping_document(self):
        center = FakeDetailCenter(documents=[make_doc()], gaps=[pending_gap()])
        source = FakeDetailSource(errors={'5001': SourceError(
            'Source detail identity mismatch; do not trust this entity')})
        with tempfile.TemporaryDirectory() as root:
            workflow = make_workflow(center, source, root)
            with self.assertRaises(SourceError):
                workflow.begin('purchase-orders', DAY)
            capture_id = next(op[1] for op in center.ops if op[0] == 'begin_capture')
            manifest = json.loads((Path(root) / capture_id / 'manifest.json').read_text())
        self.assertIn(('fail_capture', 'source_detail_contract_violation'), center.ops)
        self.assertEqual(manifest['phase'], 'interrupted')
        self.assertEqual(center.tables['purchase_document_detail_state'], [])


class ResumeTests(unittest.TestCase):
    def create_capture(self, store, **extra):
        state = store.create(tenant='t', stream=detail_scope('purchase-orders'),
                             window_key=DAY, source_key_version='purchase-id-v1',
                             mode='final', method=DETAIL_METHOD)
        state.update(server_started=True, **extra)
        store.save(state)
        return state

    def test_resume_skips_success_documents_even_with_corrupt_manifest_snapshot(self):
        children = {'purchase_document_lines': [{'document_id': 101, 'source_line_id': 'L0'},
                                                {'document_id': 101, 'source_line_id': 'L1'}],
                    'purchase_document_branches': [{'document_id': 101, 'source_key': 'B0'}],
                    'purchase_document_approvals': [{'document_id': 101, 'source_key': 'A0'}]}
        center = FakeDetailCenter(
            documents=[make_doc(), make_doc(doc_id=102, source_id='5002')],
            states=[success_state(101)], children=children)
        source = FakeDetailSource({'5002': make_entity('5002')})
        with tempfile.TemporaryDirectory() as root:
            store = CaptureStore(root)
            capture = self.create_capture(store, eligible_snapshot='CORRUPT-not-a-list',
                                          max_documents=200, retry_failed=False)
            result = DetailWorkflow(store, source, center).resume(capture['capture_id'])
        self.assertEqual(source.fetched, [('order', '5002')])
        self.assertEqual(result['state'], 'committed')

    def test_cooling_interrupts_and_resume_does_not_refetch_success_documents(self):
        docs = [make_doc(), make_doc(doc_id=102, source_id='5002')]
        center = FakeDetailCenter(documents=docs, gaps=[pending_gap(documents=2)])
        cooling = FakeDetailSource({'5001': make_entity('5001')},
                                   errors={'5002': SourceCooling('rate limited')})
        with tempfile.TemporaryDirectory() as root:
            store = CaptureStore(root)
            workflow = DetailWorkflow(store, cooling, center)
            with self.assertRaises(SourceCooling):
                workflow.begin('purchase-orders', DAY)
            capture_id = next(op[1] for op in center.ops if op[0] == 'begin_capture')
            self.assertIn(('note_capture', 'cooling'), center.ops)
            self.assertEqual(center.tables['purchase_document_detail_state'][0]['status'],
                             'success')
            source = FakeDetailSource({'5001': make_entity('5001'),
                                       '5002': make_entity('5002')})
            result = DetailWorkflow(store, source, center).resume(capture_id)
        self.assertEqual(source.fetched, [('order', '5002')])
        self.assertEqual(result['state'], 'committed')
        self.assertEqual(center.tables['sync_coverage_gaps'][0]['status'], 'resolved')

    def test_gap_close_is_noop_when_resolution_hash_matches(self):
        children = {'purchase_document_lines': [{'document_id': 101, 'source_line_id': 'L0'},
                                                {'document_id': 101, 'source_line_id': 'L1'}],
                    'purchase_document_branches': [{'document_id': 101, 'source_key': 'B0'}],
                    'purchase_document_approvals': [{'document_id': 101, 'source_key': 'A0'}]}
        center = FakeDetailCenter(documents=[make_doc()], states=[success_state(101)],
                                  children=children)
        source = FakeDetailSource()
        with tempfile.TemporaryDirectory() as root:
            store = CaptureStore(root)
            capture = self.create_capture(store, max_documents=200, retry_failed=False)
            resolution = {'capture_id': capture['capture_id'], 'documents': 1,
                          'counts_digest': counts_digest(documents=1, lines=2,
                                                         branches=1, approvals=1)}
            gap = {**pending_gap(), 'status': 'resolved',
                   'resolution': json.dumps(resolution, sort_keys=True),
                   'resolution_hash': resolution_hash(resolution)}
            center.tables['sync_coverage_gaps'] = [gap]
            result = DetailWorkflow(store, source, center).resume(capture['capture_id'])
        self.assertEqual(result['state'], 'committed')
        self.assertEqual(source.operations, 0)
        self.assertNotIn(('upsert', 'sync_coverage_gaps'),
                         [op for op in center.ops if op[0] == 'upsert'])

    def test_gap_close_conflict_raises_without_overwriting(self):
        children = {'purchase_document_lines': [{'document_id': 101, 'source_line_id': 'L0'},
                                                {'document_id': 101, 'source_line_id': 'L1'}],
                    'purchase_document_branches': [{'document_id': 101, 'source_key': 'B0'}],
                    'purchase_document_approvals': [{'document_id': 101, 'source_key': 'A0'}]}
        center = FakeDetailCenter(documents=[make_doc()], states=[success_state(101)],
                                  children=children)
        foreign = {'capture_id': 'f' * 32, 'documents': 1,
                   'counts_digest': counts_digest(documents=1, lines=2, branches=1, approvals=1)}
        gap = {**pending_gap(), 'status': 'resolved',
               'resolution': json.dumps(foreign, sort_keys=True),
               'resolution_hash': resolution_hash(foreign)}
        center.tables['sync_coverage_gaps'] = [gap]
        with tempfile.TemporaryDirectory() as root:
            store = CaptureStore(root)
            capture = self.create_capture(store, max_documents=200, retry_failed=False)
            with self.assertRaisesRegex(ContractError, 'different resolution'):
                DetailWorkflow(store, FakeDetailSource(), center).resume(capture['capture_id'])
        self.assertNotIn(('upsert', 'sync_coverage_gaps'),
                         [op for op in center.ops if op[0] == 'upsert'])
        self.assertEqual(center.tables['sync_coverage_gaps'][0]['resolution_hash'],
                         resolution_hash(foreign))


class FailClosedTests(unittest.TestCase):
    def test_placeholder_datasets_fail_closed_before_any_source_call(self):
        doc = {'id': 101, 'tenant_id': '205149', 'document_kind': 'order',
               'source_document_id': '5001', 'source_version': '7',
               'source_created_at': '2026-11-08 09:00:00'}
        envelope = {'ok': True, 'data': {'result': {
            'tableData': [doc],
            'paging': {'currentPage': 1, 'pageSize': 100, 'totalCount': 1}}}}
        executor = Mock(return_value=SimpleNamespace(returncode=0,
                                                     stdout=json.dumps(envelope)))
        # Placeholders no longer live in the real datasets.json (datasets are
        # registered now); simulate the pre-registration config explicitly.
        config = {'appcode': 'app-test', 'tenant_id': '205149',
                  'datasets': {'purchase_documents': 'code-docs',
                               'purchase_document_detail_state': 'PENDING_DATASET_REGISTRATION',
                               'purchase_document_lines': 'PENDING_DATASET_REGISTRATION',
                               'purchase_document_branches': 'PENDING_DATASET_REGISTRATION',
                               'purchase_document_approvals': 'PENDING_DATASET_REGISTRATION'}}
        adapter = LovrabetAdapter(config, executor=executor)
        source = FakeDetailSource({'5001': make_entity('5001')})
        with tempfile.TemporaryDirectory() as root:
            workflow = DetailWorkflow(CaptureStore(root), source, adapter)
            with self.assertRaisesRegex(ContractError, '待数据集注册'):
                workflow.begin('purchase-orders', DAY)
            self.assertEqual(list(Path(root).iterdir()), [])
        self.assertEqual(source.operations, 0)
        self.assertEqual(executor.call_count, 1)  # only the purchase_documents read


class PlannerDetailTests(unittest.TestCase):
    def plan(self, stream, gaps):
        state = {**BASE_STATE, 'coverage_gaps': gaps}
        return incremental_plan({stream: state}, NOW, max_days=1)

    def test_purchase_detail_gap_produces_detail_pass_task(self):
        result = self.plan('purchase-orders', [
            {'stream': 'purchase-orders', 'scope_key': '2026-11-05',
             'missing_parts': ['document_lines', 'branch_allocations'], 'status': 'pending'}])
        detail = [t for t in result['tasks'] if t.get('kind') == 'detail_pass']
        self.assertEqual([(t['stream'], t['day']) for t in detail],
                         [('purchase-orders', '2026-11-05')])
        self.assertEqual(result['coverage_gaps'][0]['scheduling'], 'detail_pass')

    def test_missing_parts_json_string_is_parsed(self):
        result = self.plan('purchase-receipts', [
            {'stream': 'purchase-receipts', 'scope_key': '2026-11-05',
             'missing_parts': '["approval_history"]', 'status': 'pending'}])
        self.assertEqual([t for t in result['tasks'] if t.get('kind') == 'detail_pass'][0]['day'],
                         '2026-11-05')

    def test_non_detail_parts_and_non_day_scope_stay_unscheduled(self):
        for parts, scope in ((['list_rows'], '2026-11-05'), (['document_lines'], 'detail:1')):
            with self.subTest(parts=parts, scope=scope):
                result = self.plan('purchase-orders', [
                    {'stream': 'purchase-orders', 'scope_key': scope,
                     'missing_parts': parts, 'status': 'pending'}])
                self.assertEqual(result['coverage_gaps'][0]['scheduling'], 'unscheduled')
                self.assertFalse([t for t in result['tasks'] if t.get('kind') == 'detail_pass'])

    def test_retail_detail_gaps_stay_unscheduled_without_detail_pass(self):
        result = self.plan('retail-order-detail', [
            {'stream': 'retail-order-detail', 'scope_key': '2026-11-05',
             'missing_parts': ['document_lines'], 'status': 'pending'}])
        self.assertEqual(result['coverage_gaps'][0]['scheduling'], 'unscheduled')
        self.assertEqual(result['coverage_gaps'][0]['reason'],
                         'requires_scope_specific_workflow')
        self.assertEqual(result['tasks'], [])
        self.assertEqual(result['blocked'][0]['reason'], 'paused_by_user')


def detail_task(stream='purchase-orders', day='2026-11-05'):
    return {'kind': 'detail_pass', 'stream': stream, 'day': day, 'mode': 'final',
            'reason': 'detail_coverage_gap'}


class CaptureCenter:
    config = {'tenant_id': 't'}

    def __init__(self, active=()):
        self.active = [dict(r) for r in active]
        self.failed = []

    def read_all(self, table, *, where, select=None):
        assert table == 'sync_pipeline_captures'
        return [dict(r) for r in self.active
                if r['stream'] == where['stream']['$eq'] and r['state'] == 'running']

    def fail_capture(self, capture, reason):
        self.failed.append((capture['capture_id'], reason))


class FakeDetailPass:
    def __init__(self, source, results=None):
        self.source = source
        self.results = list(results or [])
        self.begun = []
        self.resumed = []

    def _next(self):
        result = self.results.pop(0) if self.results else {'state': 'committed'}
        if isinstance(result, Exception):
            raise result
        return dict(result)

    def begin(self, stream, day, *, max_documents=200, retry_failed=False):
        self.begun.append((stream, day))
        return {**self._next(), 'capture_id': 'd' * 32}

    def resume(self, capture_id):
        self.resumed.append(capture_id)
        return {**self._next(), 'capture_id': capture_id}


class FakeWindow:
    def __init__(self, source):
        self.source = source
        self.begun = []

    def begin(self, stream, day, *, mode='final', method=None):
        self.begun.append((stream, day))
        return {'state': 'committed', 'capture_id': 'c' * 32}

    def resume(self, capture_id):
        raise AssertionError('unexpected window resume')


class OperatorDetailTests(unittest.TestCase):
    def run_tasks(self, center, tasks, detail, window=None, artifact_root=None):
        source = Mock(operations=0)
        detail.source = source
        window = window or FakeWindow(source)
        with patch('kemai_pipeline.operator.WindowWorkflow', lambda *a: window), \
             patch('kemai_pipeline.operator.DetailWorkflow', lambda *a: detail):
            return _execute_tasks(center, tasks,
                                  artifact_root=artifact_root or tempfile.mkdtemp(),
                                  source=source)

    def test_detail_pass_begins_via_detail_workflow(self):
        detail = FakeDetailPass(None)
        result = self.run_tasks(CaptureCenter(), [detail_task()], detail)
        self.assertEqual(detail.begun, [('purchase-orders', '2026-11-05')])
        row = result['results'][0]
        self.assertEqual(row['stream'], 'purchase-orders')
        self.assertTrue(row['requested_day_committed'])
        self.assertTrue(result['complete'])

    def test_detail_pass_resumes_active_detail_capture(self):
        center = CaptureCenter()
        with tempfile.TemporaryDirectory() as root:
            store = CaptureStore(root)
            state = store.create(tenant='t', stream='purchase-detail:order',
                                 window_key='2026-11-05', source_key_version='purchase-id-v1',
                                 mode='final', method=DETAIL_METHOD)
            center.active.append({'capture_id': state['capture_id'], 'state': 'running',
                                  'stream': 'purchase-detail:order', 'window_key': '2026-11-05',
                                  'mode': 'final', 'source_key_version': 'purchase-id-v1'})
            detail = FakeDetailPass(None)
            result = self.run_tasks(center, [detail_task()], detail, artifact_root=root)
        self.assertEqual(detail.resumed, [state['capture_id']])
        self.assertEqual(detail.begun, [])
        self.assertTrue(result['results'][0]['requested_day_committed'])

    def test_detail_failure_defers_same_scope_but_not_other_streams(self):
        detail = FakeDetailPass(None, results=[SourceError('bad source')])
        window = FakeWindow(Mock(operations=0))
        tasks = [detail_task(), detail_task(day='2026-11-06'),
                 {'stream': 'retail-sales', 'day': '2026-11-05', 'mode': 'final'}]
        result = self.run_tasks(CaptureCenter(), tasks, detail, window=window)
        rows = result['results']
        self.assertEqual(rows[0]['state'], 'failed')
        self.assertEqual(rows[1]['state'], 'deferred')
        self.assertEqual(rows[1]['reason'], 'Earlier capture for this stream is incomplete')
        self.assertTrue(rows[2]['requested_day_committed'])
        self.assertEqual(window.begun, [('retail-sales', '2026-11-05')])
        self.assertFalse(result['complete'])

    def test_malformed_detail_pass_fails_itself_and_later_tasks_still_run(self):
        # detail_scope raises ValueError for a non-purchase stream; the failure
        # must stay scoped to this task, never escape _execute_tasks.
        detail = FakeDetailPass(None)
        window = FakeWindow(Mock(operations=0))
        tasks = [{'kind': 'detail_pass', 'stream': 'retail-sales', 'day': '2026-11-05',
                  'mode': 'final', 'reason': 'detail_coverage_gap'},
                 {'stream': 'retail-orders', 'day': '2026-11-05', 'mode': 'final'}]
        result = self.run_tasks(CaptureCenter(), tasks, detail, window=window)
        row = result['results'][0]
        self.assertEqual(row['state'], 'failed')
        self.assertTrue(row['deferred'])
        self.assertEqual(row['error_type'], 'ValueError')
        self.assertEqual(detail.begun, [])
        self.assertTrue(result['results'][1]['requested_day_committed'])
        self.assertEqual(window.begun, [('retail-orders', '2026-11-05')])

    def test_detail_cooling_defers_remaining_tasks(self):
        detail = FakeDetailPass(None, results=[SourceCooling('cooling')])
        result = self.run_tasks(CaptureCenter(),
                                [detail_task(), detail_task('purchase-receipts')], detail)
        rows = result['results']
        self.assertEqual(rows[0]['state'], 'cooling')
        self.assertEqual(rows[1]['state'], 'deferred')
        self.assertEqual(rows[1]['reason'], 'Shared 30-minute cooldown')
        self.assertTrue(all(row['deferred'] for row in rows))


class CliDetailTests(unittest.TestCase):
    class Adapter(FakeDetailCenter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.requests = 0

        def read_all(self, table, *, where=None, select=None):
            self.requests += 1
            return super().read_all(table, where=where, select=select)

        def count(self, table, *, where=None):
            self.requests += 1
            return super().count(table, where=where)

    def invoke(self, argv, adapter):
        output = io.StringIO()
        with redirect_stdout(output):
            code = kemai_syncctl.main(argv, adapter=adapter)
        return code, json.loads(output.getvalue())

    def test_detail_plan_reports_zero_source_requests_and_db_writes(self):
        adapter = self.Adapter(documents=[make_doc()], states=[success_state(101)])
        code, output = self.invoke(['detail', 'plan', '--stream', 'purchase-orders',
                                    '--day', DAY], adapter)
        self.assertEqual(code, 0)
        self.assertTrue(output['ok'])
        self.assertEqual(output['source_requests'], 0)
        self.assertEqual(output['db_writes'], 0)
        self.assertEqual(output['result']['source_requests'], 0)
        self.assertEqual(output['result']['db_writes'], 0)
        self.assertEqual(output['result']['documents'], 1)
        self.assertEqual(output['result']['eligible'], 0)

    def test_detail_run_commits_through_versioned_envelope(self):
        adapter = self.Adapter(documents=[make_doc()], gaps=[pending_gap()])
        source = FakeDetailSource({'5001': make_entity('5001')})
        with patch('kemai_syncctl.OpenCLISource', lambda **kwargs: source):
            code, output = self.invoke(['detail', 'run', '--stream', 'purchase-orders',
                                        '--day', DAY], adapter)
        self.assertEqual(code, 0, output)
        self.assertTrue(output['ok'])
        self.assertEqual(output['result']['state'], 'committed')
        self.assertIsNone(output['db_writes'])
        self.assertEqual(output['source_operations'], 1)

    def test_placeholder_dataset_error_uses_versioned_envelope(self):
        adapter = self.Adapter(documents=[make_doc()])

        def refuse(table, *, where=None, select=None):
            if table == 'purchase_document_detail_state':
                raise ContractError('Dataset pending registration (待数据集注册), '
                                    f'access refused: {table}')
            return CliDetailTests.Adapter.read_all(adapter, table, where=where, select=select)

        adapter.read_all = refuse
        code, output = self.invoke(['detail', 'plan', '--stream', 'purchase-orders',
                                    '--day', DAY], adapter)
        self.assertEqual(code, 1)
        self.assertFalse(output['ok'])
        self.assertEqual(output['schema_version'], 1)
        self.assertEqual(output['error']['type'], 'ContractError')
        self.assertIn('待数据集注册', output['error']['message'])


class AuditDetailTests(unittest.TestCase):
    def client(self, documents, states=None, state_error=False):
        client = Mock()
        client.count.return_value = 0

        def read_all(table, *, where=None, select=None):
            if table == 'sync_stream_events':
                return []
            if table == 'purchase_documents':
                return [dict(row) for row in documents]
            if table == 'purchase_document_detail_state':
                if state_error:
                    raise ContractError('Dataset pending registration (待数据集注册), '
                                        f'access refused: {table}')
                return [dict(row) for row in (states or [])]
            raise AssertionError(f'unexpected read {table}')

        client.read_all.side_effect = read_all
        return client

    def check(self, client):
        return check_history(client, 'purchase-orders',
                             date(2026, 11, 7), date(2026, 11, 7))

    def test_detail_coverage_verified_when_all_state_rows_success(self):
        result = self.check(self.client([{'id': 101}], [success_state(101)]))
        self.assertEqual(result['detail_coverage'], 'verified')

    def test_detail_coverage_partial_and_failed_aggregation(self):
        partial = {**success_state(101), 'status': 'partial'}
        self.assertEqual(self.check(self.client([{'id': 101}], [partial]))['detail_coverage'],
                         'partial')
        failed = {**success_state(101), 'status': 'failed'}
        mixed = [success_state(101), failed]
        docs = [{'id': 101}, {'id': 102}]
        self.assertEqual(self.check(self.client(docs, mixed))['detail_coverage'], 'failed')

    def test_detail_coverage_not_checked_without_rows_or_when_unregistered(self):
        self.assertEqual(self.check(self.client([{'id': 101}], []))['detail_coverage'],
                         'not_checked')
        self.assertEqual(self.check(self.client([{'id': 101}], state_error=True))
                         ['detail_coverage'], 'not_checked')

    def test_detail_coverage_missing_state_row_is_not_verified(self):
        # F4: two documents that day but only one success state row -> partial.
        docs = [{'id': 101}, {'id': 102}]
        result = self.check(self.client(docs, [success_state(101)]))
        self.assertEqual(result['detail_coverage'], 'partial')

    def test_detail_coverage_stale_source_version_is_not_verified(self):
        # F4: a success row whose source_version lags the document's current
        # version is partial, not verified; alignment restores verified.
        docs = [{'id': 101, 'source_version': '8'}]
        result = self.check(self.client(docs, [success_state(101, version='7')]))
        self.assertEqual(result['detail_coverage'], 'partial')
        aligned = self.check(self.client([{'id': 101, 'source_version': '7'}],
                                         [success_state(101, version='7')]))
        self.assertEqual(aligned['detail_coverage'], 'verified')


if __name__ == '__main__':
    unittest.main()
