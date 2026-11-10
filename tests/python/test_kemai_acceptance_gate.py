import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from kemai_pipeline.acceptance_runbook import (
    FAIL,
    PASS,
    PENDING,
    STREAMS,
    evaluate_runbook,
    evaluate_stream,
)
from scripts.kemai_acceptance_gate import main as gate_main, run_check, version_check
from scripts.verify_kemai_purchase_rollback import build_report, check_entry

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_JSON = ROOT / 'kemai_pipeline' / 'acceptance.json'


def rollback_evidence(streams=('purchase-orders',), passed=True):
    return build_report('2026-11-07', 'kemaiSyncGateway', [
        check_entry(stream, {
            'business_row_unchanged': passed,
            'raw_capture_unchanged': True,
            'random_capture_absent': True,
            'random_batch_absent': True,
            'random_window_absent': True,
            'stream_controller_unchanged': True,
            'stream_status_unchanged': True,
            'same_day_events_unchanged': True,
        }, rolled_back=passed) for stream in streams], data_center_requests=8)


def window_evidence(passed=True):
    return {'schema_version': 1, 'passed': passed, 'capture_id': 'c' * 32,
            'window_key': '2026-11-07', 'batches': [{'batch_id': 'b' * 64, 'sequence': 0}],
            'window_committed': True, 'receipt_consistent': True, 'source_requests': 0}


def replay_evidence(passed=True):
    return {'schema_version': 1, 'passed': passed, 'source_requests': 0, 'hashes_equal': True}


def hash_audit_evidence(passed=True):
    return {'schema_version': 1, 'passed': passed, 'rows': 10, 'mismatched_rows': 0,
            'mismatches': []}


def investigation_evidence(conclusion='closed'):
    return {'schema_version': 1, 'conclusion': conclusion, 'summary': 'hash drift explained'}


def b2_mapping_evidence(passed=True):
    return {'schema_version': 1, 'passed': passed, 'artifact': 'b2-mapping-validation'}


def seed_evidence(root, stream, *, investigation='closed', include_b2=False):
    target = Path(root) / stream
    target.mkdir(parents=True, exist_ok=True)
    (target / 'forced-rollback.json').write_text(json.dumps(rollback_evidence((stream,))))
    (target / 'small-window-closed-loop.json').write_text(json.dumps(window_evidence()))
    (target / 'same-capture-zero-source-replay.json').write_text(json.dumps(replay_evidence()))
    (target / 'key-hash-audit.json').write_text(json.dumps(hash_audit_evidence()))
    if stream == 'purchase-receive-history':
        (target / 'history-hash-investigation.json').write_text(
            json.dumps(investigation_evidence(investigation)))
        if include_b2:
            (target / 'b2-mapping-validation.json').write_text(json.dumps(b2_mapping_evidence()))


def all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from all_strings(key)
            yield from all_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from all_strings(item)


def gateway_and_lock(root, gateway_bytes=b'// gateway candidate\n', lock_hash=None):
    """A local gateway candidate plus a deploy lock; lock_hash=None means aligned."""
    gateway = Path(root) / 'gateway.js'
    gateway.write_bytes(gateway_bytes)
    lock = Path(root) / 'lock.json'
    lock.write_text(json.dumps({'apps': {'app': {
        'lastSync': '2026-11-08T00:00:00',
        'functions': {'ENDPOINT/kemaiSyncGateway': {
            'hash': lock_hash or hashlib.md5(gateway_bytes).hexdigest(),
            'version': '1'}}}}}))
    return gateway, lock


class VersionCheckTests(unittest.TestCase):
    def test_version_check_reports_unconfirmed_when_no_attestation(self):
        result = version_check()
        self.assertEqual(result['remote_state'], 'unknown_not_inferred')
        self.assertEqual(result['required_action'], 'authorized_remote_confirmation')
        self.assertEqual(result['comparison'], 'unconfirmed')
        self.assertNotIn('remote_matches', result)
        self.assertNotIn('remote_attestation', result)
        self.assertRegex(result['local_digest']['sha256'], r'^[0-9a-f]{64}$')
        self.assertRegex(result['local_digest']['md5'], r'^[0-9a-f]{32}$')
        self.assertIn('hash', result['lock_digest'])

    def test_version_check_divergent_when_attested_differs(self):
        result = version_check(attest_remote='0' * 64, attest_source='authorized-channel-note')
        self.assertEqual(result['comparison'], 'divergent')
        self.assertEqual(result['remote_state'], 'unknown_not_inferred')
        attestation = result['remote_attestation']
        self.assertEqual(attestation['digest'], '0' * 64)
        self.assertEqual(attestation['source'], 'authorized-channel-note')
        self.assertIn('recorded_at', attestation)

    def test_version_check_consistent_when_attested_matches(self):
        # consistent requires the full three-way agreement: digests_match AND
        # the attestation matches the local (== locked) digest.
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            gateway, lock = gateway_and_lock(temp)
            digest = hashlib.sha256(gateway.read_bytes()).hexdigest()
            result = version_check(attest_remote=digest, gateway_path=gateway, lock_path=lock)
            self.assertTrue(result['digests_match'])
            self.assertEqual(result['comparison'], 'consistent')

    def test_attestation_matching_local_is_not_consistent_when_lock_diverges(self):
        # F1: local != lock must never report consistent, even when the remote
        # attestation matches the local candidate byte for byte.
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            gateway, lock = gateway_and_lock(temp, lock_hash='0' * 32)
            baseline = version_check(gateway_path=gateway, lock_path=lock)
            self.assertFalse(baseline['digests_match'])
            for digest in (baseline['local_digest']['sha256'], baseline['local_digest']['md5']):
                with self.subTest(digest=digest):
                    if len(digest) != 64:
                        continue  # md5-shaped attestations are rejected outright (F2)
                    result = version_check(attest_remote=digest,
                                           gateway_path=gateway, lock_path=lock)
                    self.assertNotEqual(result['comparison'], 'consistent')

    def test_attest_remote_must_be_lowercase_sha256_hex(self):
        # F2: free-text attestation input is rejected before any report exists.
        local = version_check()['local_digest']
        bad_inputs = ('0' * 63, '0' * 65, 'A' * 64, 'g' * 64, local['md5'],
                      '', 'https://attest.example/x?sign=1', 'Bearer ' + 'a' * 57)
        for bad in bad_inputs:
            with self.subTest(length=len(bad)):
                with self.assertRaises(ValueError):
                    version_check(attest_remote=bad)

    def test_attest_source_policy_rejects_urls_credentials_and_control_chars(self):
        # F2: provenance notes are length-capped plain text; URLs, credential
        # markers and control characters are refused, not persisted.
        digest = version_check()['local_digest']['sha256']
        bad_sources = ('see http://example.com/report', 'HTTPS://x', 'token sign=abc',
                       'Bearer abc.def', 'bearer xyz', 'a' * 201, 'line\nbreak',
                       'tab\there', '')
        for bad in bad_sources:
            with self.subTest(length=len(bad)):
                with self.assertRaises(ValueError):
                    version_check(attest_remote=digest, attest_source=bad)
        # A plain provenance note still passes and is persisted.
        result = version_check(attest_remote='0' * 64, attest_source='authorized-channel-note')
        self.assertEqual(result['remote_attestation']['source'], 'authorized-channel-note')

    def test_cli_rejects_invalid_attestation_without_writing_report(self):
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / 'report.json'
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = gate_main(['version-check', '--attest-remote', 'not-a-digest',
                                  '--attest-source', 'http://leak.example/?sign=1',
                                  '--report', str(report)])
            self.assertEqual(code, 2)
            self.assertFalse(report.exists())
            printed = output.getvalue()
            self.assertNotIn('http', printed)
            self.assertNotIn('sign=', printed)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.evidence_root = Path(self.temp.name)

    def test_check_without_evidence_is_no_go_and_does_not_touch_acceptance_json(self):
        before_bytes = ACCEPTANCE_JSON.read_bytes()
        before_stat = ACCEPTANCE_JSON.stat()
        report = run_check(evidence_root=self.evidence_root)
        self.assertEqual(report['overall'], 'no-go')
        for stream in STREAMS:
            self.assertEqual(report['streams'][stream]['verdict'], 'no-go')
        self.assertEqual(ACCEPTANCE_JSON.read_bytes(), before_bytes)
        after_stat = ACCEPTANCE_JSON.stat()
        self.assertEqual((before_stat.st_mtime_ns, before_stat.st_size),
                         (after_stat.st_mtime_ns, after_stat.st_size))

    def test_real_probe_items_pending_without_authorization_record(self):
        result = evaluate_stream('purchase-orders', self.evidence_root)
        statuses = {check['id']: check['status'] for check in result['checks']}
        self.assertEqual(statuses['forced-rollback'], PENDING)
        self.assertEqual(statuses['small-window-closed-loop'], PENDING)
        self.assertEqual(statuses['same-capture-zero-source-replay'], PENDING)
        self.assertEqual(statuses['key-hash-audit'], FAIL)
        self.assertEqual(result['verdict'], 'no-go')

    def test_single_item_failure_fails_entire_stream(self):
        seed_evidence(self.evidence_root, 'purchase-orders')
        target = self.evidence_root / 'purchase-orders' / 'key-hash-audit.json'
        target.write_text(json.dumps(hash_audit_evidence(passed=False)))
        result = evaluate_stream('purchase-orders', self.evidence_root)
        statuses = {check['id']: check['status'] for check in result['checks']}
        self.assertEqual(statuses['key-hash-audit'], FAIL)
        self.assertEqual(statuses['forced-rollback'], PASS)
        self.assertEqual(statuses['small-window-closed-loop'], PASS)
        self.assertEqual(statuses['same-capture-zero-source-replay'], PASS)
        self.assertEqual(result['verdict'], 'no-go')

    def test_receive_history_blocked_until_hash_investigation_closed(self):
        seed_evidence(self.evidence_root, 'purchase-receive-history',
                      investigation='open', include_b2=True)
        result = evaluate_stream('purchase-receive-history', self.evidence_root)
        statuses = {check['id']: check['status'] for check in result['checks']}
        self.assertEqual(statuses['history-hash-investigation'], FAIL)
        self.assertEqual(result['verdict'], 'no-go')
        seed_evidence(self.evidence_root, 'purchase-receive-history',
                      investigation='closed', include_b2=True)
        result = evaluate_stream('purchase-receive-history', self.evidence_root)
        statuses = {check['id']: check['status'] for check in result['checks']}
        self.assertEqual(statuses['history-hash-investigation'], PASS)
        self.assertEqual(result['verdict'], 'go')

    def test_malformed_evidence_fails_closed(self):
        seed_evidence(self.evidence_root, 'purchase-receipts')
        target = self.evidence_root / 'purchase-receipts' / 'key-hash-audit.json'
        target.write_text('{not json')
        result = evaluate_stream('purchase-receipts', self.evidence_root)
        statuses = {check['id']: check['status'] for check in result['checks']}
        self.assertEqual(statuses['key-hash-audit'], FAIL)
        self.assertEqual(result['verdict'], 'no-go')

    def test_report_contains_no_credentials_or_urls(self):
        seed_evidence(self.evidence_root, 'purchase-orders')
        reports = [run_check(evidence_root=self.evidence_root),
                   version_check(attest_remote='f' * 64, attest_source='manual-authorized-note')]
        forbidden = ('http://', 'https://', 'sign=', 'signature=', 'access_token', 'Bearer ')
        for report in reports:
            for text in all_strings(report):
                for marker in forbidden:
                    self.assertNotIn(marker, text)


class RollbackReportSchemaTests(unittest.TestCase):
    def test_forced_rollback_report_schema_is_stable(self):
        entry = check_entry('purchase-orders', {'business_row_unchanged': True,
                                                'raw_capture_unchanged': False},
                            rolled_back=True)
        report = build_report('2026-11-07', 'kemaiSyncGateway', [entry], data_center_requests=4)
        self.assertEqual(report['schema_version'], 1)
        self.assertIs(report['passed'], False)
        self.assertIs(report['rolled_back'], True)
        self.assertIsInstance(report['checks'], list)
        self.assertEqual(report['source_requests'], 0)
        self.assertEqual(report['data_center_requests'], 4)
        self.assertEqual(entry['name'], 'purchase-orders')
        self.assertEqual(entry['stream'], 'purchase-orders')
        self.assertIs(entry['passed'], False)
        self.assertIsInstance(entry['detail'], dict)
        self.assertIs(entry['existing_row_found'], True)
        self.assertIs(entry['rolled_back'], True)
        passing = build_report('2026-11-07', 'kemaiSyncGateway', [
            check_entry('purchase-orders', {'a': True}, rolled_back=True)], data_center_requests=1)
        self.assertIs(passing['passed'], True)


if __name__ == '__main__':
    unittest.main()
