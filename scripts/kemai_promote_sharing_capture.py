#!/usr/bin/env python3
"""Bounded read-only capture of Kemai retail promote-sharing rows.

Writes only private local artifacts. It never writes the Kemai SaaS or the data
center. A complete day is proven only by a short/empty final page within the
explicit page budget; exhausting the budget is reported as incomplete.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
STREAM = 'retail-promote-sharing'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def read_page(from_day, to_day, page, rows, *, runner=subprocess.run):
    result = runner(['opencli', 'kemai', STREAM, '--from', from_day, '--to', to_day,
                     '--page', str(page), '--rows', str(rows), '-f', 'json'],
                    capture_output=True, text=True, timeout=120, cwd=ROOT)
    if result.returncode:
        raise RuntimeError('OpenCLI read failed; inspect rate limit/authentication')
    envelope = json.loads(result.stdout)
    if isinstance(envelope, dict) and envelope.get('ok') is False:
        raise RuntimeError(f"OpenCLI read refused: {envelope.get('error', {}).get('code', 'unknown')}")
    rows_out = envelope.get('data') if isinstance(envelope, dict) else envelope
    if not isinstance(rows_out, list):
        raise ValueError('Invalid OpenCLI row envelope')
    return rows_out


def capture(from_day, to_day, *, rows=1000, max_pages=10, root=None, runner=subprocess.run):
    if not 1 <= rows <= 1000:
        raise ValueError('rows must be 1..1000')
    if not 1 <= max_pages <= 100:
        raise ValueError('max_pages must be 1..100')
    for value in (from_day, to_day):
        datetime.strptime(value, '%Y-%m-%d')
    if from_day > to_day:
        raise ValueError('from_day must not be after to_day')
    root = Path(root) if root else ROOT / '.kemai-artifacts' / 'promote-sharing'
    capture_id = uuid.uuid4().hex
    directory = root / capture_id
    directory.mkdir(parents=True, exist_ok=False)
    state = {'schema_version': 1, 'capture_id': capture_id, 'stream': STREAM,
             'tenant': '205149', 'from': from_day, 'to': to_day,
             'phase': 'capturing', 'source_rows': 0, 'source_operations': 0,
             'started_at': datetime.now(timezone.utc).isoformat(), 'pages': []}

    def save():
        (directory / 'manifest.json').write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))

    save()
    try:
        for page in range(1, max_pages + 1):
            observed_at = datetime.now(timezone.utc).isoformat()
            items = read_page(from_day, to_day, page, rows, runner=runner)
            envelope = {'schema_version': 1, 'stream': STREAM, 'page': page,
                        'page_size': rows, 'observed_at': observed_at,
                        'query_scope': {'from': from_day, 'to': to_day}, 'rows': items}
            name = f'page-{page:06}.json'
            (directory / name).write_text(json.dumps(envelope, ensure_ascii=False))
            state['pages'].append({'file': name, 'rows': len(items), 'sha256': digest(envelope)})
            state['source_rows'] += len(items)
            state['source_operations'] += 1
            save()
            if len(items) < rows:
                state['phase'] = 'complete'
                state['list_evidence'] = 'short_or_empty_final_page'
                break
        else:
            state['phase'] = 'incomplete'
            state['list_evidence'] = 'page_budget_exhausted'
    except Exception as error:
        state['phase'] = 'failed'
        state['error'] = type(error).__name__
        save()
        raise
    state['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()
    return {k: state[k] for k in ('capture_id', 'stream', 'from', 'to', 'phase',
                                  'source_rows', 'source_operations', 'list_evidence')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='from_day', required=True)
    parser.add_argument('--to', dest='to_day', required=True)
    parser.add_argument('--rows', type=int, default=1000)
    parser.add_argument('--max-pages', type=int, default=10)
    parser.add_argument('--root', type=Path)
    args = parser.parse_args(argv)
    try:
        result = capture(args.from_day, args.to_day, rows=args.rows,
                         max_pages=args.max_pages, root=args.root)
    except Exception as error:
        print(json.dumps({'ok': False, 'error': type(error).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps({'ok': True, 'data': result, 'db_writes': 0}, ensure_ascii=False))
    return 0 if result['phase'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
