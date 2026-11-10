"""Bounded line backfill entrypoint. Raw snapshots stay private; stdout is metadata only."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kemai_pipeline.artifacts import CaptureStore
from kemai_pipeline.detail_workflow import STREAM_KIND
from kemai_pipeline.lines_only_workflow import LinesOnlyWorkflow
from kemai_pipeline.locking import writer_lock
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter
from kemai_pipeline.source import OpenCLISource

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'run', 'resume'])
    parser.add_argument('--stream', choices=STREAM_KIND)
    parser.add_argument('--day')
    parser.add_argument('--capture-id')
    parser.add_argument('--artifact-root', type=Path, default=ROOT / '.kemai-artifacts')
    parser.add_argument('--max-documents', type=int, default=10)
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--source-mode', choices=['batch', 'single'], default='batch',
                        help='batch: up to 10 details per OpenCLI invocation; single: compatibility fallback')
    args = parser.parse_args()
    if not 1 <= args.max_documents <= 100:
        parser.error('--max-documents must be 1..100')
    if args.action == 'resume':
        if not args.capture_id:
            parser.error('resume requires --capture-id')
    elif not args.stream or not args.day:
        parser.error('plan/run require --stream and --day')
    center = LovrabetAdapter()
    source = OpenCLISource(max_operations=args.max_documents)
    store = CaptureStore(args.artifact_root)
    workflow = LinesOnlyWorkflow(store, source, center, batch_source=args.source_mode == 'batch')
    try:
        if args.action == 'plan':
            result = workflow.plan(args.stream, args.day, retry_failed=args.retry_failed)
        elif args.action == 'run':
            result = workflow.begin(args.stream, args.day, max_documents=args.max_documents,
                                    retry_failed=args.retry_failed)
        else:
            # A smaller per-invocation work budget is operational metadata, not coverage.
            # Preserve the capture ID and all evidence. The shared lock spans this update and resume.
            with writer_lock():
                state = store.load(args.capture_id)
                workflow._scope_parts(state)
                if state.get('method') != 'detail_api' or state.get('server_started') != 'local_only':
                    raise ValueError('Resume requires a local detail_api capture')
                if state.get('phase') == 'interrupted':
                    raise ValueError('Interrupted capture requires explicit investigation')
                state['max_documents'] = args.max_documents
                if args.retry_failed:
                    state['retry_failed'] = True
                store.save(state)
                result = workflow.resume(args.capture_id)
        print(json.dumps({'ok': True, 'result': result, 'source_operations': source.operations,
                          'source_count_basis': 'reserved document slots in batch mode; logical calls in single mode',
                          'source_http_requests': None, 'data_center_requests': center.requests},
                         ensure_ascii=False))
        return 0
    except Exception as error:
        # Raw errors may contain business data. Consult private manifests for recovery.
        print(json.dumps({'ok': False, 'error_type': type(error).__name__,
                          'source_count_basis': 'reserved document slots in batch mode; logical calls in single mode',
                          'source_operations': source.operations,
                          'data_center_requests': center.requests}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
