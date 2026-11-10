"""Read-only capture hash audit: recompute per-row hashes from page files.

Recomputes the ``source_list_hash`` caliber (sha256 of the canonical payload,
per ``scripts/verify_kemai_capture_db.py`` and ``kemai_pipeline/normalize.py``)
for every captured row and lists mismatching keys with a field-level diff
summary. Never dumps payload text, never touches network or the data center.
Malformed capture directories (missing manifest, page digest mismatch) fail
closed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kemai_pipeline.artifacts import ArtifactError, CaptureStore, atomic_json
from kemai_pipeline.contracts import digest
from kemai_pipeline.normalize import normalize

ROOT = Path(__file__).resolve().parents[1]


def _row_diff(stream, row):
    """Field names whose hash component disagrees; empty when the row is clean."""
    data = row.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('payload'), str):
        return ['payload']
    payload = data['payload']
    fields = set()
    if hashlib.sha256(payload.encode()).hexdigest() != data.get('payload_hash'):
        fields.add('payload_hash')
    try:
        raw = json.loads(payload)
        recomputed = normalize(stream, raw)
    except (ValueError, TypeError, KeyError):
        return sorted(fields | {'payload'})
    if recomputed['source_key'] != row.get('source_key'):
        fields.add('source_key')
    expected = recomputed['data']
    for key in set(expected) | set(data):
        if key == 'payload':
            continue  # hash already covers the payload; never diff raw text
        if expected.get(key) != data.get(key):
            fields.add(key)
    return sorted(fields)


def _failure(capture_id, reason):
    return {'schema_version': 1, 'capture_id': capture_id, 'passed': False,
            'error': 'malformed_capture_dir', 'detail': reason,
            'rows': 0, 'mismatched_rows': 0, 'mismatches': []}


def audit_capture(artifact_root, capture_id):
    store = CaptureStore(artifact_root)
    try:
        state = store.load(capture_id)
        stream = state['stream']
        rows = list(store.rows(state))
    except (ArtifactError, OSError, ValueError, KeyError) as exc:
        return _failure(capture_id, type(exc).__name__)
    mismatches = []
    keys = []
    for row in rows:
        keys.append(row['source_key'])
        fields = _row_diff(stream, row)
        if fields:
            mismatches.append({'source_key': row['source_key'], 'fields': fields})
    return {'schema_version': 1, 'capture_id': capture_id, 'stream': stream,
            'window_key': state.get('window_key'), 'hash_caliber': 'source_list_hash-v1',
            'rows': len(rows), 'mismatched_rows': len(mismatches),
            'mismatches': mismatches, 'keyset_digest': digest(sorted(keys)),
            'passed': not mismatches}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-id', required=True)
    parser.add_argument('--artifact-root', type=Path, default=ROOT / '.kemai-artifacts')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit_capture(args.artifact_root, args.capture_id)
    atomic_json(args.report, report)
    verdict = 'pass' if report['passed'] else 'fail'
    print(json.dumps({'capture_id': report['capture_id'], 'rows': report['rows'],
                      'mismatched_rows': report['mismatched_rows'], 'verdict': verdict},
                     ensure_ascii=False))
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
