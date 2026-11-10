"""Offline verification of the Kemai history XLSX export column mapping.

Parses a downloaded sample XLSX through kemai_pipeline.xlsx_import (strict
header/row-count/key checks), then asserts per row that the stable line key
depends only on (sheetNo, normalized sequence, itemCode|itemId): blanking or
forging the ``id`` field must not change history_key, and Decimal sequence
normalization must be stable. Only when everything passes is a self-verifying
artifact written to <artifact-root>/export-mapping/<stream>.json (directory
0o700, file 0o600); any failure writes nothing and exits non-zero. stdout
carries only counts, the verdict and digests -- never business row content.
"""
import argparse
import hashlib
import json
import os
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kemai_pipeline.artifacts import atomic_json
from kemai_pipeline.export_mappings import HISTORY_EXPORT_FIELDS, MAPPING_VERSION
from kemai_pipeline.mapping_verification import SCHEMA_VERSION, canonical_artifact_digest
from kemai_pipeline.normalize import dump, history_key
from kemai_pipeline.xlsx_import import XlsxImportError, count_history_rows, parse_history_xlsx

ROOT = Path(__file__).resolve().parents[1]
ID_SENTINEL = 'mapping-verification-sentinel'


class MappingVerificationError(Exception):
    pass


def _assert_key_independence(row, line):
    """history_key must ignore id entirely and normalize sequence via Decimal."""
    base = history_key(row)
    if history_key({**row, 'id': ID_SENTINEL}) != base:
        raise MappingVerificationError(f'data row {line}: stable key changes when id is forged')
    if history_key({key: value for key, value in row.items() if key != 'id'}) != base:
        raise MappingVerificationError(f'data row {line}: stable key changes when id is removed')
    try:
        as_decimal = Decimal(str(row.get('sequence')))
    except InvalidOperation:
        raise MappingVerificationError(f'data row {line}: sequence is not Decimal-parseable') from None
    if history_key({**row, 'sequence': as_decimal}) != base:
        raise MappingVerificationError(f'data row {line}: Decimal sequence normalization is unstable')
    return base


def verify_sample(stream, sample, *, artifact_root, limit=None, mapping=HISTORY_EXPORT_FIELDS):
    """Verify one sample file; returns the artifact dict, writes nothing on failure."""
    total = count_history_rows(sample)
    rows = list(parse_history_xlsx(sample, expected_rows=total, mapping=mapping))
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        raise MappingVerificationError('sample has no data rows to verify')
    covered = 0
    for offset, row in enumerate(rows):
        try:
            _assert_key_independence(row, offset + 4)
        except (ValueError, TypeError) as exc:
            raise MappingVerificationError(f'data row {offset + 4}: stable key assertion failed: {exc}') from exc
        covered += 1
    artifact = {'schema_version': SCHEMA_VERSION, 'stream': stream, 'mapping_version': MAPPING_VERSION,
                'header_sha256': hashlib.sha256(dump(list(mapping.keys())).encode()).hexdigest(),
                'sample_rows': covered, 'key_coverage': covered / len(rows),
                'verifier': 'scripts/verify_kemai_history_export_mapping.py',
                'verified_at': datetime.now().astimezone().isoformat(timespec='seconds')}
    artifact['artifact_sha256'] = canonical_artifact_digest(artifact)
    return artifact


def write_artifact(artifact_root, stream, artifact):
    directory = Path(artifact_root) / 'export-mapping'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / f'{stream}.json'
    atomic_json(path, artifact)  # mkstemp-backed: lands with 0o600
    os.chmod(path, 0o600)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stream', required=True, choices=['purchase-receive-history'])
    parser.add_argument('--sample', required=True, type=Path, help='downloaded sample XLSX, offline only')
    parser.add_argument('--limit', type=int, default=None,
                        help='verify only the first N data rows (file is still fully validated)')
    parser.add_argument('--artifact-root', type=Path, default=ROOT / '.kemai-artifacts')
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be a positive integer')
    try:
        artifact = verify_sample(args.stream, args.sample, artifact_root=args.artifact_root,
                                 limit=args.limit, mapping=HISTORY_EXPORT_FIELDS)
    except (XlsxImportError, MappingVerificationError, OSError, ValueError) as exc:
        print(json.dumps({'stream': args.stream, 'verdict': 'fail',
                          'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False))
        return 2
    path = write_artifact(args.artifact_root, args.stream, artifact)
    print(json.dumps({'stream': args.stream, 'verdict': 'pass', 'sample_rows': artifact['sample_rows'],
                      'header_sha256': artifact['header_sha256'],
                      'artifact_sha256': artifact['artifact_sha256'], 'artifact': str(path)},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
