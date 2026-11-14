#!/usr/bin/env python3
"""Idempotent backfill of a completed promote-sharing list capture.

Reads only local immutable artifacts and writes the data center through the
standard model API. No Kemai source request is made. Existing identical rows
are verified and skipped; conflicting rows fail closed instead of overwriting.
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kemai_pipeline.artifacts import atomic_json
from kemai_pipeline.locking import writer_lock
from kemai_pipeline.lovrabet_adapter import ContractError, LovrabetAdapter
from kemai_pipeline.promote_sharing import load_capture

TABLE = 'retail_promote_shares'
KEYS = ('tenant_id', 'source_row_hash', 'occurrence_no')


def key(row):
    return tuple(str(row[k]) for k in KEYS)


def read_existing(adapter, rows):
    """Read identities within the METADATA dataset's actual query limits.

    The dataset currently fixes pageSize=20 and rejects $in lists longer than
    three values. Keep these quirks local to this workflow instead of weakening
    the adapter's strict contracts for every other dataset.
    """
    hashes = sorted({row['source_row_hash'] for row in rows})
    collected = []
    for offset in range(0, len(hashes), 3):
        where = {'source_row_hash': {'$in': hashes[offset:offset + 3]}}
        before_count = len(collected)
        for page_no in range(1, adapter.max_pages + 1):
            chunk, total = adapter.page(TABLE, where=where, page=page_no, size=20)
            collected.extend(chunk)
            if len(collected) - before_count == total:
                break
        else:
            raise ContractError('Promote-sharing identity read exceeded page budget')
    return collected


def create_verified(adapter, rows):
    """Batch-create new rows, then confirm every field by unique-key read-back."""
    payloads = [dict(row) for row in rows]
    try:
        adapter.invoke('data', 'batchCreate', '--code', adapter.dataset_code(TABLE),
                       '--params', json.dumps(payloads, ensure_ascii=False))
    except ContractError as error:
        saved = {key(row): row for row in read_existing(adapter, rows)}
        if not all(key(row) in saved and all(adapter._same(saved[key(row)].get(k), v)
                                             for k, v in row.items()) for row in rows):
            raise ContractError('UPSERT_OUTCOME_UNKNOWN: promote-sharing batch create') from error
    saved = {key(row): row for row in read_existing(adapter, rows)}
    for row in rows:
        actual = saved.get(key(row))
        if actual is None or any(not adapter._same(actual.get(k), v) for k, v in row.items()):
            raise ContractError('UPSERT_OUTCOME_UNKNOWN: promote-sharing readback mismatch')


def backfill(adapter, capture_root, *, batch_size=100, max_rows=None):
    if not 1 <= batch_size <= 100:
        raise ValueError('batch_size must be 1..100')
    capture_root = Path(capture_root)
    all_rows = list(load_capture(capture_root))
    rows = all_rows
    if max_rows is not None:
        if not isinstance(max_rows, int) or max_rows < 1:
            raise ValueError('max_rows must be a positive integer')
        rows = all_rows[:max_rows]
    state_path = capture_root / 'backfill.json'
    manifest = json.loads((capture_root / 'manifest.json').read_text())
    capture_id = manifest['capture_id']
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get('capture_id') != capture_id:
            raise ContractError('Backfill state belongs to another capture')
    else:
        state = {'schema_version': 1, 'capture_id': capture_id,
                 'phase': 'planned', 'next_row': 0, 'verified_rows': 0,
                 'created_rows': 0, 'unchanged_rows': 0}
    if state['phase'] == 'complete':
        return {**state, 'source_operations': 0, 'db_requests': 0,
                'note': 'completed backfill replays without any standard-API request'}
    state['source_rows'] = len(all_rows)
    started = time.monotonic()
    db_before = adapter.requests
    try:
        while state['next_row'] < len(rows):
            batch = rows[state['next_row']:state['next_row'] + batch_size]
            before = read_existing(adapter, batch)
            existing = {}
            for old in before:
                identity = key(old)
                if identity in existing:
                    raise ContractError('Duplicate promote-sharing identity in data center')
                existing[identity] = old
            creates = []
            unchanged = 0
            for row in batch:
                old = existing.get(key(row))
                if old is None:
                    creates.append(row)
                elif all(adapter._same(old.get(k), v) for k, v in row.items()):
                    unchanged += 1
                else:
                    raise ContractError('Existing promote-sharing row conflicts with capture')
            if creates:
                create_verified(adapter, creates)
            state['next_row'] += len(batch)
            state['verified_rows'] += len(batch)
            state['created_rows'] += len(creates)
            state['unchanged_rows'] += unchanged
            state['phase'] = 'complete' if state['next_row'] == len(all_rows) else 'running'
            state['last_batch'] = {'rows': len(batch), 'created': len(creates), 'unchanged': unchanged}
            state.pop('error', None)
            atomic_json(state_path, state)
    except Exception as error:
        state['phase'] = 'failed'
        state['error'] = type(error).__name__
        atomic_json(state_path, state)
        raise
    state['seconds'] = round(time.monotonic() - started, 3)
    state['db_requests'] = adapter.requests - db_before
    state['source_operations'] = 0
    atomic_json(state_path, state)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'run'))
    parser.add_argument('--capture', required=True, help='Capture directory or ID under .kemai-artifacts/promote-sharing')
    parser.add_argument('--batch-size', type=int, default=100)
    parser.add_argument('--max-rows', type=int)
    args = parser.parse_args(argv)
    capture_root = Path(args.capture)
    if not capture_root.is_absolute():
        capture_root = ROOT / '.kemai-artifacts' / 'promote-sharing' / args.capture
    if args.action == 'plan':
        rows = list(load_capture(capture_root))
        if args.max_rows:
            rows = rows[:args.max_rows]
        result = {'capture_id': rows[0]['capture_id'] if rows else None,
                  'source_rows': len(rows), 'source_requests': 0, 'db_writes': 0,
                  'scope': 'completed list capture only; no detail reads'}
    else:
        with writer_lock():
            result = backfill(LovrabetAdapter(), capture_root,
                              batch_size=args.batch_size, max_rows=args.max_rows)
    print(json.dumps({'ok': True, 'data': result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
