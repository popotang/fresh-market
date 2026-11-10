"""Read-only purchase acceptance gate: gateway version attestation + runbook check.

``version-check`` digests the local gateway candidate and the deploy lock; it
never infers remote live code. Without an authorized remote attestation the
comparison stays ``unconfirmed``. ``check`` evaluates the acceptance runbook
against evidence files. Neither subcommand writes
``kemai_pipeline/acceptance.json`` — promotion remains a reviewed data change.
Reports go to a caller path or ``.local-audits/`` and contain only booleans,
counts, digests and key lists.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kemai_pipeline.acceptance_runbook import STREAMS, evaluate_runbook
from kemai_pipeline.artifacts import atomic_json

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_CANDIDATE = ROOT / '.rabetbase' / 'bff' / 'app-21522951' / 'ENDPOINT' / 'kemaiSyncGateway.js'
LOCK_FILE = ROOT / '.rabetbase' / 'bff.lock.json'
LOCK_FUNCTION = 'ENDPOINT/kemaiSyncGateway'
AUDITS = ROOT / '.local-audits'

# Attestation input policy (fail closed): the remote digest must be a lowercase
# sha256 hex string; the provenance note is capped plain text with no URLs,
# credential markers or control characters. Violations are rejected before any
# report is written, so hostile or pasted-wrong text never lands on disk.
ATTEST_REMOTE_RE = re.compile(r'^[0-9a-f]{64}$')
ATTEST_SOURCE_MAX_LEN = 200
ATTEST_SOURCE_FORBIDDEN = ('http', 'sign=', 'bearer')


def _validate_attest_remote(value):
    if not isinstance(value, str) or not ATTEST_REMOTE_RE.fullmatch(value):
        raise ValueError('attest-remote must be a lowercase 64-character sha256 hex digest')
    return value


def _validate_attest_source(value):
    if value is None:
        return 'unspecified'
    lowered = value.lower() if isinstance(value, str) else ''
    if (not isinstance(value, str) or not value or len(value) > ATTEST_SOURCE_MAX_LEN
            or any(marker in lowered for marker in ATTEST_SOURCE_FORBIDDEN)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError('attest-source violates the provenance-note policy '
                         '(plain text, <=200 chars, no URLs/credentials/control characters)')
    return value


def _file_digests(path):
    data = Path(path).read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'md5': hashlib.md5(data).hexdigest()}


def _lock_summary(lock_path, function):
    lock = json.loads(Path(lock_path).read_text())
    for app in lock.get('apps', {}).values():
        entry = app.get('functions', {}).get(function)
        if entry:
            return {'hash': entry.get('hash'), 'version': entry.get('version'),
                    'last_sync': app.get('lastSync')}
    return {'hash': None, 'version': None, 'last_sync': None}


def version_check(attest_remote=None, attest_source=None,
                  gateway_path=GATEWAY_CANDIDATE, lock_path=LOCK_FILE):
    local = _file_digests(gateway_path)
    lock = _lock_summary(lock_path, LOCK_FUNCTION)
    result = {'schema_version': 1,
              'gateway': str(Path(gateway_path).relative_to(ROOT)),
              'local_digest': local,
              'lock_digest': lock,
              'digests_match': local['md5'] == lock['hash'],
              'remote_state': 'unknown_not_inferred',
              'required_action': 'authorized_remote_confirmation',
              'comparison': 'unconfirmed'}
    if attest_remote is not None:
        digest = _validate_attest_remote(attest_remote)
        result['remote_attestation'] = {
            'digest': digest,
            'source': _validate_attest_source(attest_source),
            'recorded_at': datetime.now(timezone.utc).isoformat()}
        # consistent requires the full three-way agreement: local == lock
        # (digests_match) AND the attestation names that same code. When the
        # local candidate diverges from the deploy lock no attestation can
        # confirm consistency, so the comparison stays non-consistent
        # ('divergent': the attested code is not the locked deploy).
        result['comparison'] = ('consistent' if (result['digests_match']
                                                 and digest in local.values())
                                else 'divergent')
    return result


def run_check(streams=None, evidence_root=None):
    return evaluate_runbook(streams=streams, evidence_root=evidence_root)


def _default_report_path(kind):
    return AUDITS / f'kemai-acceptance-gate-{kind}-{date.today().isoformat()}.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    version = sub.add_parser('version-check', help='Digest local gateway + lock; '
                                                   'never infers remote state')
    version.add_argument('--attest-remote', metavar='DIGEST',
                         help='Remote digest obtained through an authorized channel')
    version.add_argument('--attest-source', metavar='NOTE',
                         help='Free-text provenance note for the attestation')
    version.add_argument('--report', type=Path)
    check = sub.add_parser('check', help='Evaluate the purchase acceptance runbook')
    check.add_argument('--stream', choices=STREAMS, action='append')
    check.add_argument('--evidence-root', type=Path,
                       default=AUDITS / 'kemai-acceptance')
    check.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'version-check':
        try:
            report = version_check(attest_remote=args.attest_remote,
                                   attest_source=args.attest_source)
        except ValueError as error:
            # Rejected attestation input: no report is written, and the
            # offending value is never echoed (fixed message only).
            print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
            return 2
        atomic_json(args.report or _default_report_path('version-check'), report)
        print(json.dumps({'digests_match': report['digests_match'],
                          'remote_state': report['remote_state'],
                          'comparison': report['comparison']}, ensure_ascii=False))
        return 0
    report = run_check(streams=args.stream, evidence_root=args.evidence_root)
    atomic_json(args.report or _default_report_path('check'), report)
    print(json.dumps({'overall': report['overall'],
                      'streams': {s: r['verdict'] for s, r in report['streams'].items()}},
                     ensure_ascii=False))
    return 0 if report['overall'] == 'go' else 2


if __name__ == '__main__':
    raise SystemExit(main())
