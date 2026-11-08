"""Verify one existing procurement row per stream through a forced standard-API rollback.

Reads a previously captured private source page; never calls Kemai or commits
business data. All output is structural and safe for an audit report.
"""
import argparse
import hashlib
import json
from pathlib import Path
import uuid

from kemai_pipeline.contracts import Batch
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter
from kemai_pipeline.normalize import KINDS, normalize


ROOT = Path(__file__).resolve().parents[1]


def existing(center, stream, source_key):
    rows, total = center.page('purchase_documents', where={
        'document_kind': {'$eq': KINDS[stream]},
        'source_document_id': {'$eq': source_key},
    }, size=2)
    if total != 1 or len(rows) != 1:
        raise RuntimeError('Expected exactly one existing procurement business row')
    return rows[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', default='2026-11-07')
    parser.add_argument('--stream', choices=tuple(KINDS), action='append')
    parser.add_argument('--gateway-name', choices=('kemaiSyncGateway', 'kemaiPurchasePilotGateway'),
                        default='kemaiSyncGateway')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    center = LovrabetAdapter()
    evidence = []
    for stream in args.stream or KINDS:
        path = ROOT / '.kemai-artifacts' / 'purchase-contract' / f'{stream}-{args.day}.json'
        pages = json.loads(path.read_text())
        if len(pages) != 1 or pages[0].get('stream') != stream or pages[0].get('from') != args.day:
            raise RuntimeError('Invalid private source capture identity')
        sample = None
        for raw in pages[0]['rows']:
            key = str(raw['id'])
            try:
                before = existing(center, stream, key)
            except RuntimeError:
                continue
            sample = normalize(stream, raw)
            break
        if sample is None:
            raise RuntimeError(f'No existing source row found for {stream}')
        batch = Batch(center.config['tenant_id'], stream, args.day, uuid.uuid4().hex,
                      0, 'purchase-id-v1', (sample,)).envelope()
        records_json = json.dumps(batch['records'], ensure_ascii=False, sort_keys=True,
                                  separators=(',', ':'), allow_nan=False)
        params = {'op': 'validate', 'tenant': center.config['tenant_id'], 'stream': stream,
                  'capture_id': batch['capture_id'], 'window_key': args.day, 'mode': 'final',
                  'source_key_version': 'purchase-id-v1', 'sequence': 0,
                  'batch_id': batch['batch_id'], 'records_json': records_json,
                  'payload_hash': hashlib.sha256(records_json.encode()).hexdigest()}
        result = center.invoke('bff', 'exec', '--name', args.gateway_name, '--params',
                               json.dumps(params, ensure_ascii=False, allow_nan=False))
        after = existing(center, stream, sample['source_key'])
        if result.get('rolled_back') is not True or before != after:
            raise RuntimeError(f'Rollback/readback failed for {stream}')
        evidence.append({'stream': stream, 'existing_row_found': True,
                         'rolled_back': True, 'business_row_unchanged': True,
                         'source_requests': 0})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'day': args.day, 'gateway_name': args.gateway_name, 'checks': evidence,
                                       'data_center_requests': center.requests,
                                       'source_requests': 0}, indent=2) + '\n')
    print(json.dumps({'day': args.day, 'checks': evidence,
                      'data_center_requests': center.requests, 'source_requests': 0}))


if __name__ == '__main__':
    main()
