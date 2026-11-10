"""Pure outage planning. Requires explicit evidence, never display maxima."""

import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from .capabilities import CAPABILITIES, capture_method

DETAIL_GAP_STREAMS = ("purchase-orders", "purchase-receipts", "purchase-returns")
DETAIL_GAP_PARTS = frozenset({"document_lines", "branch_allocations", "approval_history"})


def _gap_parts(gap):
    parts = gap.get("missing_parts")
    if isinstance(parts, str):
        try:
            parts = json.loads(parts)
        except ValueError:
            return None
    if not isinstance(parts, (list, tuple)):
        return None
    return {str(part) for part in parts}


def _gap_day(gap):
    try:
        return date.fromisoformat(str(gap.get("scope_key"))).isoformat()
    except ValueError:
        return None


def _task(stream, day, mode, reason):
    return {"stream": stream, "day": day.isoformat(), "mode": mode,
            "method": capture_method(stream, provisional=mode == "provisional"),
            "reason": reason}


def incremental_plan(states, now, *, max_days=2, cooldown_until=None):
    if now.tzinfo is None or not 1 <= max_days <= 31:
        raise ValueError("Timezone-aware now and max_days 1..31 required")
    now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    cooling = cooldown_until and cooldown_until > now
    cutoff = now.date() - timedelta(days=1 if now.hour >= 2 else 2)
    tasks, pending, blocked, coverage_gaps = [], [], [], []
    backlog_by_stream = {}
    for stream, state in states.items():
        cap = CAPABILITIES[stream]
        detail_schedulable = (stream in DETAIL_GAP_STREAMS and not cap.paused
                              and not state.get("paused") and cap.granularity == "day")
        detail_days = set()
        for gap in state.get("coverage_gaps", []):
            parts, day = _gap_parts(gap), _gap_day(gap)
            if (detail_schedulable and parts is not None and parts <= DETAIL_GAP_PARTS
                    and day is not None):
                coverage_gaps.append({**gap, "scheduling": "detail_pass",
                                      "reason": "detail_coverage_pass"})
                if day not in detail_days:
                    detail_days.add(day)
                    tasks.append({"kind": "detail_pass", "stream": stream, "day": day,
                                  "mode": "final", "reason": "detail_coverage_gap"})
            else:
                coverage_gaps.append({**gap, "scheduling": "unscheduled",
                                      "reason": "requires_scope_specific_workflow"})
        if cap.paused or state.get("paused"):
            blocked.append({"stream": stream, "reason": "paused_by_user"})
            continue
        if cap.granularity != "day":
            blocked.append({"stream": stream, "reason": "requires_object_queue"})
            continue
        # Today's bounded snapshot is independent of whether old coverage was migrated.
        tasks.append(_task(stream, now.date(), "provisional", "provisional_snapshot"))
        if not state.get("baseline_verified") or not state.get("baseline_from"):
            blocked.append({"stream": stream, "reason": "baseline_evidence_required"})
            continue
        baseline = date.fromisoformat(state["baseline_from"])
        cursor = state.get("last_contiguous_verified_day")
        first = date.fromisoformat(cursor) + timedelta(days=1) if cursor else baseline
        if baseline > now.date() or first < baseline or first > now.date():
            raise ValueError("Invalid baseline or future cursor")
        covered = {date.fromisoformat(d) for d in state.get("covered_days", [])}
        missing, hole_days = set(), set()
        for hole in state.get("holes", []):
            left, right = date.fromisoformat(hole["from"]), date.fromisoformat(hole["to"])
            if left < baseline or right < left:
                raise ValueError("Invalid coverage hole")
            day = left
            while day <= min(right, cutoff):
                missing.add(day)
                hole_days.add(day)
                day += timedelta(days=1)
        day = first
        while day <= cutoff:
            if day not in covered:
                missing.add(day)
            day += timedelta(days=1)
        ordered = sorted(missing)
        for day in ordered[:max_days]:
            tasks.append(_task(stream, day, "final",
                               "coverage_hole" if day in hole_days else "outage_catchup"))
        stream_pending = [_task(stream, day, "final",
                                "coverage_hole" if day in hole_days else "outage_catchup")
                          for day in ordered[max_days:]]
        pending.extend(stream_pending)
        if stream_pending:
            backlog_by_stream[stream] = len(stream_pending)
    tasks.sort(key=lambda task: task["mode"] != "provisional")
    result = {"tasks": tasks, "blocked": blocked, "planning_pending": pending,
              "backlog_remaining": len(pending), "backlog_by_stream": backlog_by_stream,
              "coverage_gaps": coverage_gaps, "status": "planned",
              "finalize_through": cutoff.isoformat(), "source_requests": 0, "db_writes": 0}
    if cooling:
        # Cooling stops execution, not evidence analysis. Keep provisional deferrals
        # visible without counting them as outage backlog.
        result['deferred_tasks'] = tasks
        result['planning_pending'] = [task for task in tasks if task['mode'] == 'final'] + pending
        result['tasks'] = []
        result['backlog_remaining'] = len(result['planning_pending'])
        result['backlog_by_stream'] = {stream: sum(task['stream'] == stream for task in result['planning_pending'])
            for stream in states if any(task['stream'] == stream for task in result['planning_pending'])}
        result.update(status='cooling', resume_after=cooldown_until.isoformat())
    return result
