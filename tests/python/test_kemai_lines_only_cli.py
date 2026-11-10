import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from scripts import kemai_lines_only as cli
from tests.python.test_kemai_pipeline_detail_workflow import (
    FakeDetailCenter, FakeDetailSource, make_doc, make_entity)
from kemai_pipeline.artifacts import CaptureStore
import kemai_pipeline.locking as locking


class LinesOnlyCliTests(unittest.TestCase):
    def test_plan_has_no_source_calls_or_capture(self):
        with tempfile.TemporaryDirectory() as root:
            center = FakeDetailCenter(documents=[make_doc()]); center.requests = 0
            source = FakeDetailSource({'5001': make_entity('5001')})
            argv = ['cmd', 'plan', '--stream', 'purchase-orders', '--day', '2026-11-08',
                    '--artifact-root', root]
            with patch('sys.argv', argv), patch.object(cli, 'LovrabetAdapter', return_value=center), \
                    patch.object(cli, 'OpenCLISource', return_value=source), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(source.operations, 0)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_resume_rejects_server_capture_without_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            center = FakeDetailCenter(); center.requests = 0
            source = FakeDetailSource()
            store = CaptureStore(Path(root))
            state = store.create(tenant='t', stream='purchase-detail:order', window_key='2026-11-08',
                                 source_key_version='purchase-id-v1', mode='final', method='detail_api')
            state['server_started'] = True
            store.save(state)
            path = store.path(state['capture_id']) / 'manifest.json'
            before = path.read_bytes()
            argv = ['cmd', 'resume', '--capture-id', state['capture_id'], '--artifact-root', root]
            with patch('sys.argv', argv), patch.object(cli, 'LovrabetAdapter', return_value=center), \
                    patch.object(cli, 'OpenCLISource', return_value=source), \
                    patch.object(locking, 'RUNTIME_LOCKS', Path(root) / 'locks'), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 2)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(source.operations, 0)

    def test_run_then_resume_uses_same_capture_and_skips_proven_lines(self):
        with tempfile.TemporaryDirectory() as root:
            center = FakeDetailCenter(documents=[make_doc()]); center.requests = 0
            source = FakeDetailSource({'5001': make_entity('5001')})
            def invoke(args):
                out = io.StringIO()
                with patch('sys.argv', ['cmd', *args, '--artifact-root', root]), \
                        patch.object(cli, 'LovrabetAdapter', return_value=center), \
                        patch.object(cli, 'OpenCLISource', return_value=source), \
                        patch.object(locking, 'RUNTIME_LOCKS', Path(root) / 'locks'), redirect_stdout(out):
                    code = cli.main()
                self.assertEqual(code, 0)
                return json.loads(out.getvalue())['result']
            first = invoke(['run', '--stream', 'purchase-orders', '--day', '2026-11-08'])
            second = invoke(['resume', '--capture-id', first['capture_id'], '--max-documents', '1'])
            self.assertEqual(second['capture_id'], first['capture_id'])
            self.assertEqual(second['processed'], 0)
            self.assertEqual(source.operations, 1)
