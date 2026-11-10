"""Pure purchase-detail state machine: legacy backfill semantics without DB access.

Ported from legacy/kemai_purchase_detail_backfill.py (needs_detail/eligible/
mark_failure/attempt accounting) and legacy/kemai_commerce_sync.py purchase()
(missing-part classification, state-row upsert values). No I/O, no imports of
lovrabet_adapter/source/artifacts, and independent of normalize_detail; Task 6
composes the two.
"""
import hashlib
import json
from typing import NamedTuple

MAX_ATTEMPTS = 3
REQUIRED_ARRAYS = ('details', 'branchs', 'approves')
# Source array name -> missing_parts vocabulary used by coverage gaps and the
# partial state note (legacy sync_day record_gap uses these names).
MISSING_PARTS = {'details': 'document_lines', 'branchs': 'branch_allocations',
                 'approves': 'approval_history'}


def _dump(value):
    # Same canonical form as legacy kemai_sync.dump, so hashes match.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, default=str)


def _hash(obj):
    return hashlib.sha256(_dump(obj).encode()).hexdigest()


def _val(obj, key):
    value = obj.get(key)
    return str(value) if value is not None else None


class DetailVerdict(NamedTuple):
    complete: bool
    missing: tuple          # missing_parts vocabulary
    counts: dict            # {'lines':int|None,'branches':int|None,'approvals':int|None}
    payload_hash: str
    source_version: str


def classify_entity(entity):
    """Classify one purchase detail entity.

    A None array means the source omitted it: the part is missing and its count
    stays None (legacy stores NULL counts for omitted ancillary arrays). An
    empty array is present and complete with count 0. A non-list array or an
    array with non-dict rows is invalid and raises, as legacy required_arrays /
    purchase_arrays do.
    """
    if not isinstance(entity, dict):
        raise ValueError("purchase detail is not an object")
    missing = []
    counts = {}
    for field, count_key in (('details', 'lines'), ('branchs', 'branches'),
                             ('approves', 'approvals')):
        rows = entity.get(field)
        if rows is None:
            missing.append(MISSING_PARTS[field])
            counts[count_key] = None
        elif isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            counts[count_key] = len(rows)
        else:
            raise ValueError(f"purchase detail has invalid {field} array")
    missing = tuple(missing)
    return DetailVerdict(complete=not missing, missing=missing, counts=counts,
                         payload_hash=_hash(entity),
                         source_version=_val(entity, "version"))


def needs_detail(state_row):
    """Verbatim legacy rule: not success, or the document source_version moved
    past the version stored by the detail state row (detail_version)."""
    return (state_row["status"] != "success" or
            (state_row["source_version"] is not None and
             (state_row["detail_version"] is None or
              str(state_row["source_version"]) != str(state_row["detail_version"]))))


def eligible(state_row, *, retry_failed=False):
    """Verbatim legacy rule: partial rows are not retried unless retry_failed
    or the source version moved; failures stop at MAX_ATTEMPTS attempts."""
    if not needs_detail(state_row):
        return False
    new_source_version = (state_row["source_version"] is not None and
                          state_row["attempted_source_version"] is not None and
                          str(state_row["source_version"]) != str(state_row["attempted_source_version"]))
    if state_row["status"] == "partial" and not retry_failed and not new_source_version:
        return False
    return retry_failed or new_source_version or int(state_row["attempts"] or 0) < MAX_ATTEMPTS


def next_state_row(prev, verdict, *, now, error=None):
    """Compute the next purchase_document_detail_state row (dict keyed by the
    table's columns).

    Attempt accounting matches the legacy upsert: a change of
    attempted_source_version resets attempts to 1, otherwise attempts+1; the
    first attempt is 1. Success stamps completed_at; partial overwrites every
    column (legacy purchase() upserts all columns via VALUES); failure follows
    legacy mark_failure and only moves status/attempts/attempted_source_version/
    last_error/attempted_at — a prev row's counts, payload_hash and
    completed_at are preserved so a retry failure after success does not erase
    the earlier verified detail. Failure errors are sanitized and never carry
    source payload text (exception type name only).
    """
    attempted = verdict.source_version
    prior_attempted = prev.get("attempted_source_version") if prev else None
    # SQL NULL semantics: a NULL new attempted version never resets attempts.
    drift = (prior_attempted is not None and attempted is not None and
             str(prior_attempted) != str(attempted))
    if prev is None or drift:
        attempts = 1
    else:
        attempts = int(prev.get("attempts") or 0) + 1
    if error is not None:
        status = "failed"
        last_error = type(error).__name__
        if prev is None:
            line_count = verdict.counts["lines"]
            branch_count = verdict.counts["branches"]
            approval_count = verdict.counts["approvals"]
            payload_hash = verdict.payload_hash
            completed_at = None
        else:
            line_count = prev.get("line_count")
            branch_count = prev.get("branch_count")
            approval_count = prev.get("approval_count")
            payload_hash = prev.get("payload_hash")
            completed_at = prev.get("completed_at")
    elif verdict.complete:
        status = "success"
        last_error = None
        completed_at = now
        line_count = verdict.counts["lines"]
        branch_count = verdict.counts["branches"]
        approval_count = verdict.counts["approvals"]
        payload_hash = verdict.payload_hash
    else:
        status = "partial"
        last_error = f"source omitted ancillary arrays: {','.join(verdict.missing)}"
        completed_at = None
        line_count = verdict.counts["lines"]
        branch_count = verdict.counts["branches"]
        approval_count = verdict.counts["approvals"]
        payload_hash = verdict.payload_hash
    return {
        "document_id": prev.get("document_id") if prev else None,
        "status": status,
        "attempts": attempts,
        "line_count": line_count,
        "branch_count": branch_count,
        "approval_count": approval_count,
        "source_version": verdict.source_version,
        "attempted_source_version": attempted,
        "payload_hash": payload_hash,
        "last_error": last_error,
        "attempted_at": now,
        "completed_at": completed_at,
    }
