"""Pure data + evaluators for the real purchase acceptance gate (read-only).

This module defines the evidence contract per purchase stream and evaluates
evidence files. It never writes ``kemai_pipeline/acceptance.json``; promotion
stays a reviewed data change. Missing or malformed evidence fails closed:
real-probe checks become ``pending_user_authorization`` and offline checks
become ``fail``.
"""
import json
from pathlib import Path

PASS = 'pass'
FAIL = 'fail'
PENDING = 'pending_user_authorization'

STREAMS = ('purchase-orders', 'purchase-receipts', 'purchase-returns',
           'purchase-receive-history')

_EVIDENCE_TEMPLATE = '{stream}/{check_id}.json'


def _check(check_id, title, evaluator, pass_criteria, requires_authorization):
    return {'id': check_id, 'title': title,
            'evidence_path': _EVIDENCE_TEMPLATE.format(stream='{stream}', check_id=check_id),
            'evaluator': evaluator, 'pass_criteria': pass_criteria,
            'requires_authorization': requires_authorization}


def _shared_checks():
    return [
        _check('forced-rollback',
               'Forced standard-API rollback leaves business and control state untouched',
               'forced-rollback',
               "Report passed==true and every structured stream check passed==true",
               True),
        _check('small-window-closed-loop',
               'Single-day final window closed loop (capture/batches/window/receipt)',
               'small-window-closed-loop',
               "Evidence passed==true with capture_id, non-empty batches, "
               "window_committed==true, receipt_consistent==true, source_requests==0",
               True),
        _check('same-capture-zero-source-replay',
               'Replaying the same capture performs zero source requests with equal hashes',
               'same-capture-zero-source-replay',
               "Evidence source_requests==0 and hashes_equal==true and passed==true",
               True),
        _check('key-hash-audit',
               'Offline capture hash audit recomputation matches stored row hashes',
               'key-hash-audit',
               "Audit report passed==true",
               False),
    ]


def runbook():
    """The full purchase acceptance runbook as pure data."""
    streams = {}
    for stream in ('purchase-orders', 'purchase-receipts', 'purchase-returns'):
        streams[stream] = {'checks': _shared_checks()}
    streams['purchase-receive-history'] = {
        'note': 'Depends on the B2 mapping-validation artifact evidence in addition '
                'to the shared checks; no-go until the hash investigation is closed.',
        'checks': _shared_checks() + [
            _check('history-hash-investigation',
                   'Hash audit investigation report concluded',
                   'history-hash-investigation',
                   "Investigation report conclusion=='closed'",
                   False),
            _check('b2-mapping-validation',
                   'B2 stage mapping-validation artifact evidence',
                   'b2-mapping-validation',
                   "Mapping validation evidence passed==true",
                   True),
        ],
    }
    return {'schema_version': 1, 'streams': streams}


def _eval_forced_rollback(evidence, stream):
    checks = evidence.get('checks')
    if evidence.get('passed') is not True or not isinstance(checks, list) or not checks:
        return False, 'rollback_report_not_passed'
    entry = next((c for c in checks if isinstance(c, dict) and c.get('stream') == stream), None)
    if entry is None:
        return False, 'stream_not_covered_by_rollback_report'
    if entry.get('passed') is not True or entry.get('rolled_back') is not True:
        return False, 'rollback_check_failed'
    return True, 'rollback_report_passed'


def _eval_small_window_closed_loop(evidence, stream):
    required = (evidence.get('passed') is True
                and isinstance(evidence.get('capture_id'), str) and evidence['capture_id']
                and isinstance(evidence.get('window_key'), str) and evidence['window_key']
                and isinstance(evidence.get('batches'), list) and bool(evidence['batches'])
                and evidence.get('window_committed') is True
                and evidence.get('receipt_consistent') is True
                and evidence.get('source_requests') == 0)
    return (True, 'window_closed_loop_evidence_passed') if required \
        else (False, 'window_closed_loop_evidence_failed_contract')


def _eval_zero_source_replay(evidence, stream):
    ok = (evidence.get('passed') is True and evidence.get('source_requests') == 0
          and evidence.get('hashes_equal') is True)
    return (True, 'zero_source_replay_confirmed') if ok \
        else (False, 'zero_source_replay_failed_contract')


def _eval_key_hash_audit(evidence, stream):
    ok = evidence.get('passed') is True
    return (True, 'hash_audit_passed') if ok else (False, 'hash_audit_not_passed')


def _eval_history_investigation(evidence, stream):
    ok = evidence.get('conclusion') == 'closed'
    return (True, 'investigation_closed') if ok else (False, 'investigation_not_closed')


def _eval_b2_mapping(evidence, stream):
    ok = evidence.get('passed') is True
    return (True, 'b2_mapping_validated') if ok else (False, 'b2_mapping_not_validated')


EVALUATORS = {
    'forced-rollback': _eval_forced_rollback,
    'small-window-closed-loop': _eval_small_window_closed_loop,
    'same-capture-zero-source-replay': _eval_zero_source_replay,
    'key-hash-audit': _eval_key_hash_audit,
    'history-hash-investigation': _eval_history_investigation,
    'b2-mapping-validation': _eval_b2_mapping,
}


def evaluate_check(check, stream, evidence_root):
    """Evaluate one check; fail-closed on missing or malformed evidence."""
    evidence_path = check['evidence_path'].format(stream=stream)
    path = Path(evidence_root) / evidence_path
    if not path.is_file():
        status = PENDING if check['requires_authorization'] else FAIL
        return {'id': check['id'], 'title': check['title'], 'status': status,
                'detail': 'evidence_missing', 'evidence_path': evidence_path}
    try:
        evidence = json.loads(path.read_text())
    except (OSError, ValueError):
        return {'id': check['id'], 'title': check['title'], 'status': FAIL,
                'detail': 'evidence_malformed', 'evidence_path': evidence_path}
    if not isinstance(evidence, dict):
        return {'id': check['id'], 'title': check['title'], 'status': FAIL,
                'detail': 'evidence_malformed', 'evidence_path': evidence_path}
    passed, detail = EVALUATORS[check['evaluator']](evidence, stream)
    return {'id': check['id'], 'title': check['title'],
            'status': PASS if passed else FAIL, 'detail': detail,
            'evidence_path': evidence_path}


def evaluate_stream(stream, evidence_root):
    streams = runbook()['streams']
    if stream not in streams:
        raise ValueError('Stream not covered by the purchase acceptance runbook: ' + stream)
    checks = [evaluate_check(check, stream, evidence_root)
              for check in streams[stream]['checks']]
    statuses = {check['status'] for check in checks}
    if statuses == {PASS}:
        verdict = 'go'
    elif FAIL in statuses:
        verdict = 'no-go'
    else:
        verdict = PENDING
    result = {'stream': stream, 'verdict': verdict, 'checks': checks}
    if 'note' in streams[stream]:
        result['note'] = streams[stream]['note']
    return result


def evaluate_runbook(streams=None, evidence_root=None):
    evidence_root = evidence_root or Path('.local-audits') / 'kemai-acceptance'
    selected = tuple(streams) if streams else STREAMS
    results = {stream: evaluate_stream(stream, evidence_root) for stream in selected}
    verdicts = {result['verdict'] for result in results.values()}
    if verdicts == {'go'}:
        overall = 'go'
    elif 'no-go' in verdicts:
        overall = 'no-go'
    else:
        overall = PENDING
    return {'schema_version': 1, 'overall': overall, 'streams': results}
