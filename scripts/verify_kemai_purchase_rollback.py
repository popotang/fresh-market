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


def _records(rows):
    return sorted(rows, key=lambda row: json.dumps(row, ensure_ascii=False,
                                                  sort_keys=True, default=str))


def snapshot_validation_state(center, *, stream, day, source_key, capture_id, batch_id):
    """Read structural state through the standard adapter; never call the source."""
    stream_where = {'stream': {'$eq': stream}}
    return {
        'business': existing(center, stream, source_key),
        'raw_capture': _records(center.read_all('source_capture', where={
            **stream_where, 'source_key': {'$eq': source_key}})),
        'captures': _records(center.read_all('sync_pipeline_captures', where={
            **stream_where, 'capture_id': {'$eq': capture_id}})),
        'batches': _records(center.read_all('sync_pipeline_batches', where={
            **stream_where, 'batch_id': {'$eq': batch_id}})),
        'windows': _records(center.read_all('sync_pipeline_windows', where={
            **stream_where, 'capture_id': {'$eq': capture_id}})),
        'controller': _records(center.read_all('sync_pipeline_streams', where=stream_where)),
        'status': _records(center.read_all('sync_stream_status', where=stream_where)),
        'events': _records(center.read_all('sync_stream_events', where={
            **stream_where, 'source_from': {'$eq': day}, 'source_to': {'$eq': day}})),
    }


def assert_validation_rollback(before, after):
    checks = {
        'business_row_unchanged': before['business'] == after['business'],
        'raw_capture_unchanged': before['raw_capture'] == after['raw_capture'],
        'random_capture_absent': not before['captures'] and not after['captures'],
        'random_batch_absent': not before['batches'] and not after['batches'],
        'random_window_absent': not before['windows'] and not after['windows'],
        'stream_controller_unchanged': before['controller'] == after['controller'],
        'stream_status_unchanged': before['status'] == after['status'],
        'same_day_events_unchanged': before['events'] == after['events'],
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError('Rollback verification failed: ' + ','.join(failed))
    return checks


def check_entry(stream, checks, rolled_back):
    """One stable per-stream report entry; field names and types are a contract."""
    return {'name': stream, 'stream': stream,
            'passed': bool(rolled_back) and all(checks.values()),
            'detail': dict(checks),
            'existing_row_found': True,
            'rolled_back': bool(rolled_back),
            'source_requests': 0}


def build_report(day, gateway_name, entries, data_center_requests):
    """The frozen report schema: {passed, checks:[{name,passed,detail}], rolled_back, ...}."""
    entries = list(entries)
    return {'schema_version': 1, 'day': day, 'gateway_name': gateway_name,
            'passed': bool(entries) and all(entry['passed'] for entry in entries),
            'rolled_back': bool(entries) and all(entry['rolled_back'] for entry in entries),
            'checks': entries,
            'data_center_requests': data_center_requests,
            'source_requests': 0}


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
                existing(center, stream, key)
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
        before = snapshot_validation_state(
            center, stream=stream, day=args.day, source_key=sample['source_key'],
            capture_id=batch['capture_id'], batch_id=batch['batch_id'])
        result = center.invoke('bff', 'exec', '--name', args.gateway_name, '--params',
                               json.dumps(params, ensure_ascii=False, allow_nan=False))
        after = snapshot_validation_state(
            center, stream=stream, day=args.day, source_key=sample['source_key'],
            capture_id=batch['capture_id'], batch_id=batch['batch_id'])
        checks = assert_validation_rollback(before, after)
        if result.get('rolled_back') is not True:
            raise RuntimeError(f'Gateway did not confirm forced rollback for {stream}')
        evidence.append(check_entry(stream, checks, rolled_back=True))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(
        build_report(args.day, args.gateway_name, evidence, center.requests), indent=2) + '\n')
    print(json.dumps({'day': args.day, 'checks': evidence,
                      'data_center_requests': center.requests, 'source_requests': 0}))


if __name__ == '__main__':
    main()
