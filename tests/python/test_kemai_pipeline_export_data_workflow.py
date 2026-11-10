"""export_for_data 集成链路测试（全 fake：不触网、不触真实数据库/源站）。"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from openpyxl import Workbook

from kemai_pipeline.artifacts import CaptureStore
from kemai_pipeline.export_mappings import HISTORY_EXPORT_FIELDS, MAPPING_VERSION
from kemai_pipeline.mapping_verification import canonical_artifact_digest
from kemai_pipeline.normalize import dump, history_key, normalize_history_export_row
from kemai_pipeline.source import OpenCLISource, SourceError
from kemai_pipeline.workflow import WindowWorkflow, resolve_capture_method
from kemai_pipeline.locking import writer_lock

STREAM = 'purchase-receive-history'
DAY = '2026-11-06'
HEADERS = list(HISTORY_EXPORT_FIELDS.keys())
COL = {field: index for index, field in enumerate(HISTORY_EXPORT_FIELDS.values())}


def make_xlsx(path, rows):
    """Miniature history-export workbook: header at row 3, data from row 4."""
    book = Workbook()
    sheet = book.active
    for index, header in enumerate(HEADERS, start=1):
        sheet.cell(row=3, column=index, value=header)
    for offset, row in enumerate(rows):
        values = [None] * len(HEADERS)
        for field, value in row.items():
            values[COL[field]] = value
        for index, value in enumerate(values, start=1):
            sheet.cell(row=4 + offset, column=index, value=value)
    book.save(path)


def sample_rows():
    return [
        {'sheetNo': 'CG0001', 'sequence': 1, 'itemCode': 'ITEM-1', 'qty': 10, 'amt': 100,
         'approveTime': '2026-11-06 10:00:00', 'warehousingDate': '2026-11-06'},
        {'sheetNo': 'CG0001', 'sequence': 2, 'itemCode': 'ITEM-2', 'qty': 5, 'amt': 50,
         'approveTime': '2026-11-06 10:00:00', 'warehousingDate': '2026-11-06'},
    ]


def write_verification_artifact(root, stream=STREAM):
    artifact = {'schema_version': 1, 'stream': stream, 'mapping_version': MAPPING_VERSION,
                'header_sha256': hashlib.sha256(dump(list(HISTORY_EXPORT_FIELDS.keys())).encode()).hexdigest(),
                'sample_rows': 2, 'key_coverage': 1.0,
                'verifier': 'scripts/verify_kemai_history_export_mapping.py',
                'verified_at': '2026-11-09T10:00:00+08:00'}
    artifact['artifact_sha256'] = canonical_artifact_digest(artifact)
    directory = Path(root) / 'export-mapping'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{stream}.json').write_text(json.dumps(artifact, ensure_ascii=False))
    return artifact


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


class FakeHistorySource:
    """全 fake 的 export_for_data 源端口；任何分页扫描调用都是测试失败。"""

    def __init__(self, xlsx_bytes, total_rows, *, pending_polls=0, receipt_sha=None):
        self.xlsx_bytes = xlsx_bytes
        self.total_rows = total_rows
        self.pending_polls = pending_polls
        self.receipt_sha = receipt_sha
        self.operations = 0
        self.starts = []
        self.status_polls = 0
        self.downloads = []
        self.pages_called = False

    def pages(self, *args, **kwargs):
        self.pages_called = True
        raise AssertionError('export_for_data must never scan list pages')

    def export_start(self, stream, day, capture_id, *, query_scope=None):
        self.operations += 1
        self.starts.append((stream, day, capture_id))
        return {'task_id': '9001', 'stream': stream, 'day': day,
                'file_name': f'history_{day}_{capture_id}', 'reused': False,
                'reuse_reason': None, 'source_task_no': 'T-1',
                'export_created_at': '2026-11-07 09:00:00'}

    def export_status(self, task):
        self.operations += 1
        self.status_polls += 1
        if self.status_polls <= self.pending_polls:
            return {'state': 'pending', 'task_id': task['task_id']}
        return {'state': 'complete', 'task_id': task['task_id'], 'source_rows': self.total_rows}

    def export_download(self, task, output_path):
        self.operations += 1
        self.downloads.append((dict(task), str(output_path)))
        Path(output_path).write_bytes(self.xlsx_bytes)
        return {'task_id': task['task_id'], 'path': str(output_path),
                'rows': self.total_rows, 'bytes': len(self.xlsx_bytes),
                'sha256': self.receipt_sha or sha256_bytes(self.xlsx_bytes)}


class FakeCenter:
    config = {'tenant_id': 't'}

    def __init__(self):
        self.notes = []
        self.failed = []
        self.writes = 0
        self.commits = 0
        self.rows = {}

    def begin_capture(self, state):
        pass

    def bind_capture(self, state):
        pass

    def fail_capture(self, state, reason):
        self.failed.append((state['capture_id'], reason))

    def note_capture(self, state, status, reason):
        self.notes.append((state['capture_id'], status, reason))

    def page(self, table, *, where, select, size):
        return ([], 0)

    def batch_upsert(self, batch):
        self.writes += 1
        self.rows.update({r['source_key']: r for r in batch['records']})
        receipt = {key: batch[key] for key in ('batch_id', 'capture_id', 'window_key', 'payload_hash')}
        receipt.update(state='committed', row_count=len(batch['records']))
        return receipt

    def commit_window(self, manifest):
        self.commits += 1
        return {'capture_id': manifest['capture_id'], 'state': 'committed'}


def string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from string_values(key)
            yield from string_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from string_values(item)


class ExportDataWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        lock_patch = patch('kemai_pipeline.workflow.writer_lock',
                           lambda: writer_lock(Path(self.temp.name) / 'workflow.lock'))
        lock_patch.start()
        self.addCleanup(lock_patch.stop)
        self.store = CaptureStore(Path(self.temp.name) / 'artifacts')
        self.artifact = write_verification_artifact(self.store.root)
        self.xlsx_path = Path(self.temp.name) / 'sample.xlsx'
        make_xlsx(self.xlsx_path, sample_rows())
        self.xlsx_bytes = self.xlsx_path.read_bytes()
        self.center = FakeCenter()

    def workflow(self, source):
        return WindowWorkflow(self.store, source, self.center)

    def committed_state(self, result):
        self.assertEqual(result['state'], 'committed')
        return self.store.load(result['capture_id'])

    def test_full_loop_start_poll_download_parse_seal_ingest(self):
        source = FakeHistorySource(self.xlsx_bytes, 2)
        result = self.workflow(source).begin(STREAM, DAY)
        state = self.committed_state(result)
        self.assertEqual(state['method'], 'export_for_data')
        self.assertEqual(state['phase'], 'committed')
        self.assertEqual(len(source.starts), 1)
        self.assertEqual(source.status_polls, 1)
        self.assertEqual(len(source.downloads), 1)
        self.assertEqual(source.downloads[0][0]['totalRows'], 2)
        self.assertFalse(source.pages_called)
        evidence = state['evidence']
        self.assertEqual(evidence['verification_method'], 'xlsx_rows_v1')
        self.assertEqual(evidence['xlsx_sha256'], sha256_bytes(self.xlsx_bytes))
        self.assertEqual(evidence['export_total'], 2)
        self.assertEqual(evidence['parsed_rows'], 2)
        self.assertEqual(evidence['source_rows'], 2)
        self.assertEqual(evidence['mapping_version'], MAPPING_VERSION)
        self.assertEqual(evidence['header_sha256'], self.artifact['header_sha256'])
        self.assertEqual(len(state['pages']), 1)
        rows = list(self.store.rows(state))
        self.assertEqual([row['source_key'] for row in rows],
                         [history_key(row) for row in sample_rows()])
        self.assertEqual(rows[0]['data']['source_line_key'], rows[0]['source_key'])
        self.assertEqual(rows[0]['data']['quantity'], '10')
        self.assertEqual(self.center.writes, 1)
        self.assertEqual(self.center.commits, 1)
        self.assertEqual(set(self.center.rows), {row['source_key'] for row in rows})

    def test_pending_export_returns_awaiting_export_same_as_count_only(self):
        source = FakeHistorySource(self.xlsx_bytes, 2, pending_polls=1)
        workflow = self.workflow(source)
        result = workflow.begin(STREAM, DAY)
        self.assertEqual(result['state'], 'awaiting_export')
        self.assertEqual(result['next_action'], 'resume_same_capture')
        state = self.store.load(result['capture_id'])
        self.assertEqual(state['phase'], 'capturing')
        self.assertEqual(source.downloads, [])
        self.assertEqual(self.center.writes, 0)
        self.assertIn((result['capture_id'], 'awaiting_export', 'Exact export task pending; capture retained'),
                      self.center.notes)
        result = workflow.resume(result['capture_id'])
        state = self.committed_state(result)
        self.assertEqual(len(source.downloads), 1)
        self.assertEqual(state['evidence']['export_total'], 2)

    def test_row_count_mismatch_fails_capture_and_never_writes(self):
        source = FakeHistorySource(self.xlsx_bytes, 3)  # task claims 3 rows, file holds 2
        with self.assertRaises(SourceError):
            self.workflow(source).begin(STREAM, DAY)
        self.assertEqual(self.center.writes, 0)
        self.assertEqual(self.center.commits, 0)
        self.assertEqual(len(self.center.failed), 1)
        capture_id, reason = self.center.failed[0]
        self.assertEqual(reason, 'xlsx_row_count_mismatch')
        state = self.store.load(capture_id)
        self.assertEqual(state['phase'], 'interrupted')
        self.assertEqual(state['reason'], 'xlsx_row_count_mismatch')
        self.assertEqual(state['pages'], [])
        self.assertEqual(list((self.store.path(capture_id)).glob('page-*.json')), [])

    def craft_downloaded_capture(self, file_bytes, receipt_sha):
        state = self.store.create(tenant='t', stream=STREAM, window_key=DAY,
                                  source_key_version='history-decimal-sequence-v2',
                                  mode='final', method='export_for_data')
        state['server_started'] = True
        state['export_task'] = {'task_id': '9001', 'stream': STREAM, 'day': DAY,
                                'file_name': f'history_{DAY}_x'}
        xlsx = self.store.path(state['capture_id']) / 'export.xlsx'
        xlsx.write_bytes(file_bytes)
        state['export_receipt'] = {'task_id': '9001', 'sha256': receipt_sha, 'rows': 2}
        self.store.save(state)
        return state

    def test_resume_reuses_downloaded_artifact_after_sha_check(self):
        state = self.craft_downloaded_capture(self.xlsx_bytes, sha256_bytes(self.xlsx_bytes))
        source = FakeHistorySource(self.xlsx_bytes, 2)
        result = self.workflow(source).resume(state['capture_id'])
        state = self.committed_state(result)
        self.assertEqual(source.downloads, [])
        self.assertEqual(source.starts, [])
        self.assertEqual(state['evidence']['parsed_rows'], 2)
        self.assertEqual(self.center.writes, 1)

    def test_tampered_xlsx_sha_mismatch_blocks_before_prepare(self):
        tampered = b'PK\x03\x04 tampered local artifact'
        state = self.craft_downloaded_capture(tampered, sha256_bytes(self.xlsx_bytes))
        # Re-download is dishonest too: receipt claims the original digest.
        source = FakeHistorySource(tampered, 2, receipt_sha=sha256_bytes(self.xlsx_bytes))
        with self.assertRaises(SourceError):
            self.workflow(source).resume(state['capture_id'])
        self.assertEqual(self.center.writes, 0)
        self.assertEqual(self.center.commits, 0)
        capture_dir = self.store.path(state['capture_id'])
        self.assertEqual(list(capture_dir.glob('page-*.json')), [])
        self.assertFalse((capture_dir / 'ingestion.json').exists())
        self.assertEqual(self.store.load(state['capture_id'])['phase'], 'interrupted')

    def test_no_signed_url_or_credentials_in_state_or_evidence(self):
        source = FakeHistorySource(self.xlsx_bytes, 2)
        result = self.workflow(source).begin(STREAM, DAY)
        state = self.committed_state(result)
        capture_dir = self.store.path(state['capture_id'])
        artifacts = [capture_dir / 'manifest.json', *(capture_dir / item['file'] for item in state['pages']),
                     capture_dir / 'ingestion.json']
        for artifact_path in artifacts:
            for text in string_values(json.loads(artifact_path.read_text())):
                self.assertNotIn('http', text.lower())
        self.assertEqual(set(state['export_receipt']), {'task_id', 'sha256', 'rows'})

    def test_download_counts_against_source_operation_budget(self):
        output = Path(self.temp.name) / 'out' / 'export.xlsx'
        receipt = {'schema_version': 1, 'task_id': '17', 'path': str(output),
                   'rows': 2, 'bytes': 8, 'sha256': 'a' * 64}

        def executor(command, **kwargs):
            output.parent.mkdir(parents=True, exist_ok=True)
            Path(command[command.index('--output') + 1]).write_bytes(b'PK\x03\x04xx')
            return type('Proc', (), {'returncode': 0, 'stdout': json.dumps([receipt]), 'stderr': ''})()

        run = Mock(side_effect=executor)
        with patch('kemai_pipeline.source.cooldown_until', return_value=0):
            source = OpenCLISource(run, max_operations=1)
            result = source.export_download({'task_id': '17', 'totalRows': 2}, output)
            self.assertEqual(result['task_id'], '17')
            self.assertEqual(source.operations, 1)
            args = run.call_args.args[0]
            self.assertEqual(args[1:3], ['kemai', 'export-download'])
            self.assertIn('--task-id', args)
            self.assertEqual(args[args.index('--task-id') + 1], '17')
            with self.assertRaisesRegex(SourceError, 'budget exhausted'):
                source.export_download({'task_id': '17', 'totalRows': 2}, output)
        self.assertEqual(run.call_count, 1)

    def test_export_download_receipt_revalidation(self):
        output = Path(self.temp.name) / 'out' / 'export.xlsx'
        base = {'schema_version': 1, 'task_id': '17', 'path': str(output),
                'rows': 2, 'bytes': 8, 'sha256': 'a' * 64}
        cases = {
            'url_in_receipt': {**base, 'file_name': 'x', 'note': 'see https://example.com/x'},
            'row_count_mismatch': {**base, 'rows': 3},
            'path_mismatch': {**base, 'path': str(Path(self.temp.name) / 'elsewhere.xlsx')},
            'identity_mismatch': {**base, 'task_id': '18'},
        }
        for name, receipt in cases.items():
            with self.subTest(name=name):
                def executor(command, **kwargs):
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b'PK\x03\x04xx')
                    return type('Proc', (), {'returncode': 0, 'stdout': json.dumps([receipt]),
                                             'stderr': ''})()
                with patch('kemai_pipeline.source.cooldown_until', return_value=0):
                    source = OpenCLISource(Mock(side_effect=executor))
                    with self.assertRaises(SourceError):
                        source.export_download({'task_id': '17', 'totalRows': 2}, output)
        with patch('kemai_pipeline.source.cooldown_until', return_value=0):
            # 回执合法但文件未落盘同样拒绝。
            missing = Path(self.temp.name) / 'missing' / 'export.xlsx'
            receipt = {**base, 'path': str(missing)}
            run = Mock(return_value=type('Proc', (), {'returncode': 0,
                                                      'stdout': json.dumps([receipt]), 'stderr': ''})())
            with self.assertRaisesRegex(SourceError, 'artifact is missing'):
                OpenCLISource(run).export_download({'task_id': '17', 'totalRows': 2}, missing)

    def test_retail_streams_never_resolve_export_for_data(self):
        root = self.store.root
        write_verification_artifact(root, 'retail-sales')
        for stream in ('retail-sales', 'retail-orders'):
            with self.subTest(stream=stream):
                self.assertEqual(resolve_capture_method(stream, 'final', root=root),
                                 'api_for_data+export_for_count')
                with self.assertRaisesRegex(SourceError, 'EXECUTION_METHOD_NOT_IMPLEMENTED'):
                    resolve_capture_method(stream, 'final', 'export_for_data', root=root)

    def test_evidence_verification_method_is_xlsx_rows_v1(self):
        source = FakeHistorySource(self.xlsx_bytes, 2)
        result = self.workflow(source).begin(STREAM, DAY)
        state = self.committed_state(result)
        self.assertEqual(state['evidence']['verification_method'], 'xlsx_rows_v1')

    def test_malicious_approve_time_fails_capture_with_fixed_code_no_raw_text(self):
        # F7: a row whose approveTime carries URL/credential text fails the
        # capture with the fixed xlsx_import_invalid code; neither the raised
        # error nor any persisted state contains the raw cell text.
        evil = 'http://evil.example/cb?sign=deadbeef&token=Bearer xyz'
        evil_path = Path(self.temp.name) / 'evil.xlsx'
        make_xlsx(evil_path, [dict(sample_rows()[0], approveTime=evil)])
        source = FakeHistorySource(evil_path.read_bytes(), 1)
        with self.assertRaises(SourceError) as caught:
            self.workflow(source).begin(STREAM, DAY)
        for marker in ('http', 'sign=', 'Bearer', 'evil.example'):
            self.assertNotIn(marker, str(caught.exception))
        self.assertEqual(self.center.writes, 0)
        self.assertEqual(self.center.commits, 0)
        self.assertEqual(len(self.center.failed), 1)
        capture_id, reason = self.center.failed[0]
        self.assertEqual(reason, 'xlsx_import_invalid')
        state = self.store.load(capture_id)
        self.assertEqual(state['phase'], 'interrupted')
        self.assertEqual(state['reason'], 'xlsx_import_invalid')
        manifest = self.store.path(capture_id) / 'manifest.json'
        for text in string_values(json.loads(manifest.read_text())):
            for marker in ('sign=', 'evil.example', 'Bearer'):
                self.assertNotIn(marker, text)


class NormalizeHistoryExportRowTests(unittest.TestCase):
    def test_xlsx_row_normalizes_without_id_column(self):
        row = sample_rows()[0]
        normalized = normalize_history_export_row(row)
        self.assertEqual(normalized['source_key'], history_key(row))
        data = normalized['data']
        self.assertEqual(data['source_line_key'], normalized['source_key'])
        self.assertEqual(data['sheet_no'], 'CG0001')
        self.assertEqual(data['source_supplier_code'], None)
        self.assertEqual(data['quantity'], '10')
        self.assertEqual(data['amount'], '100')
        self.assertEqual(data['approved_at'], '2026-11-06 10:00:00')
        self.assertEqual(data['business_date'], '2026-11-06')
        self.assertEqual(data['payload_hash'], hashlib.sha256(data['payload'].encode()).hexdigest())
        self.assertEqual(json.loads(data['payload']), json.loads(dump(row)))

    def test_xlsx_row_rejects_incomplete_key(self):
        with self.assertRaises(ValueError):
            normalize_history_export_row({'sheetNo': 'CG0001', 'sequence': 1})

    def test_timestamp_error_is_sanitized_never_carries_source_text(self):
        # F7: an unparseable approveTime carrying URL/credential text must not
        # leak into the raised error (same sanitization as detail_state's
        # last_error policy: field name + error type only).
        evil = 'http://evil.example/cb?sign=deadbeef&token=Bearer xyz'
        with self.assertRaises(ValueError) as caught:
            normalize_history_export_row(dict(sample_rows()[0], approveTime=evil))
        message = str(caught.exception)
        self.assertNotIn(evil, message)
        self.assertNotIn('http', message)
        self.assertNotIn('sign=', message)
        self.assertNotIn('Bearer', message)
        self.assertIn('approveTime', message)
        self.assertTrue(caught.exception.__suppress_context__)


if __name__ == '__main__':
    unittest.main()
