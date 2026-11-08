"""Pure outage planning. Requires explicit evidence, never display maxima."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo
from .capabilities import CAPABILITIES, capture_method


def incremental_plan(states, now, *, max_days=2, cooldown_until=None):
    if now.tzinfo is None or not 1<=max_days<=31:
        raise ValueError("Timezone-aware now and max_days 1..31 required")
    now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    if cooldown_until and cooldown_until > now:
        return {"tasks": [], "status": "cooling", "resume_after": cooldown_until.isoformat()}
    cutoff = now.date() - timedelta(days=1 if now.hour >= 2 else 2)
    tasks, blocked = [], []
    for stream, state in states.items():
        cap = CAPABILITIES[stream]
        if cap.paused or state.get("paused"):
            blocked.append({"stream": stream, "reason": "paused_by_user"})
            continue
        if cap.granularity != "day":
            blocked.append({"stream": stream, "reason": "requires_object_queue"})
            continue
        # Today's bounded snapshot is independent of whether old coverage was migrated.
        tasks.append({"stream": stream, "day": now.date().isoformat(), "mode": "provisional",
                      "method": capture_method(stream, provisional=True)})
        if not state.get("baseline_verified") or not state.get("baseline_from"):
            blocked.append({"stream": stream, "reason": "baseline_evidence_required"})
            continue
        baseline = date.fromisoformat(state["baseline_from"])
        cursor = state.get("last_contiguous_verified_day")
        first = date.fromisoformat(cursor) + timedelta(days=1) if cursor else baseline
        if baseline > now.date() or first < baseline or first > now.date():
            raise ValueError("Invalid baseline or future cursor")
        covered = {date.fromisoformat(d) for d in state.get("covered_days", [])}
        missing = set()
        for hole in state.get("holes", []):
            left, right = date.fromisoformat(hole["from"]), date.fromisoformat(hole["to"])
            if left < baseline or right < left:
                raise ValueError("Invalid coverage hole")
            for offset in range(min(max_days, max(0, (min(right, cutoff)-left).days+1))):
                missing.add(left + timedelta(days=offset))
        day = first
        while day <= cutoff and len(missing) < max_days:
            if day not in covered:
                missing.add(day)
            day += timedelta(days=1)
        for day in sorted(missing)[:max_days]:
            tasks.append({"stream": stream, "day": day.isoformat(), "mode": "final",
                          "method": capture_method(stream)})
    tasks.sort(key=lambda task: task["mode"] != "provisional")
    return {"tasks": tasks, "blocked": blocked, "status": "planned",
            "finalize_through": cutoff.isoformat(), "source_requests": 0, "db_writes": 0}
