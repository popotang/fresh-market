"""Replay previously captured OpenCLI procurement pages through the bounded pilot gateway.

The source files were collected via the shared OpenCLI gate. This script makes
zero new source requests and never presents their old observation time as now.
Run one stream at a time; normal capture receipts make retries idempotent.
"""
import argparse
import hashlib
import json
from pathlib import Path

from kemai_pipeline.artifacts import CaptureStore
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter
from kemai_pipeline.normalize import KINDS, normalize
from kemai_pipeline.operator import execute_tasks
from kemai_pipeline.source import validate_purchase_scope

ROOT = Path(__file__).resolve().parents[1]


class PilotCenter(LovrabetAdapter):
    def gateway(self, op, **params):
        return self.invoke('bff', 'exec', '--name', 'kemaiPurchasePilotGateway', '--params',
                           json.dumps({'op': op, 'tenant': self.config['tenant_id'], **params},
                                      ensure_ascii=False, allow_nan=False))


class CapturedOpenCLI:
    def __init__(self, root):
        self.root = root
        self.operations = 0

    def pages(self, stream, day, *, page_size=1000, until=None):
        if until is not None:
            raise ValueError('Pilot only accepts final closed days')
        path = self.root / f'{stream}-{day}.json'
        pages = json.loads(path.read_text())
        if not pages or len(pages) != 1:
            raise ValueError('Pilot needs one completed source page')
        page = pages[0]
        if (page.get('stream'), page.get('from'), page.get('to'), page.get('page')) != (stream, day, day, 1):
            raise ValueError('Source capture identity mismatch')
        if page.get('total_rows') is not None or len(page.get('rows', [])) >= page_size:
            raise ValueError('Source page completeness not proven')
        validate_purchase_scope(stream, page.get('query_scope', {}), day)
        keys = [normalize(stream, row)['source_key'] for row in page['rows']]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate source keys in captured page')
        yield page


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stream', choices=tuple(KINDS), required=True)
    parser.add_argument('--day', default='2026-11-07')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.day != '2026-11-07':
        raise ValueError('Pilot gateway is restricted to 2026-11-07')
    source_root = ROOT / '.kemai-artifacts' / 'purchase-contract'
    source = CapturedOpenCLI(source_root)
    pages = list(source.pages(args.stream, args.day))
    rows = pages[0]['rows']
    center = PilotCenter()
    db, total = center.page('purchase_documents', where={
        'document_kind': {'$eq': KINDS[args.stream]},
        'source_created_at': {'$gte': args.day + ' 00:00:00',
                              '$lte': args.day + ' 23:59:59'},
    }, select=['source_document_id'], size=100)
    source_keys = {str(r['id']) for r in rows}
    db_keys = {str(r['source_document_id']) for r in db}
    if total != len(db_keys) or source_keys != db_keys:
        raise ValueError('Pilot source/DB key sets differ; no writes allowed')
    report = {'stream': args.stream, 'day': args.day, 'source_rows': len(rows),
              'db_rows_before': total, 'source_file_sha256': hashlib.sha256(
                  (source_root / f'{args.stream}-{args.day}.json').read_bytes()).hexdigest(),
              'source_observed_at': pages[0]['observed_at'],
              'source_requests_this_run': 0, 'executed': args.execute}
    if args.execute:
        report['result'] = execute_tasks(center, [{'stream': args.stream,
            'day': args.day, 'mode': 'final'}],
            artifact_root=ROOT / '.kemai-artifacts' / 'purchase-pilot', source=source)
        if report['result']['complete'] is not True:
            raise RuntimeError('Pilot did not commit; inspect capture receipt and resume')
    report['data_center_requests'] = center.requests
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'source_file_sha256'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
