"""Recheck DB counts against recorded source events without accessing Kemai."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from .capabilities import CAPABILITIES

TZ = ZoneInfo("Asia/Shanghai")
METHODS = {"reused_export_total_and_capture","export_total_and_capture", "single_scan_and_capture", "single_scan_unique_keys", "single_scan_count", "legacy_log_and_db", "empty_page_and_db"}


def source_day(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, TZ).date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (dt.astimezone(TZ) if dt.tzinfo else dt).date()


def check_history(client, stream, start, end):
    cap = CAPABILITIES[stream]
    if cap.granularity != "day" or not cap.table:
        raise ValueError("Object/catalog coverage requires the object audit adapter")
    if not 1 <= (end - start).days + 1 <= 31:
        raise ValueError("History checks accept 1..31 days per invocation")
    events = client.read_all("sync_stream_events", where={"stream": {"$eq": stream},
        "source_from": {"$gte": start.isoformat()}, "source_to": {"$lte": end.isoformat()}},
        select=["stream", "source_from", "source_to", "status", "source_rows", "saved_rows",
                "verification_method", "coverage_level", "mode", "finished_at"])
    by_day = {}
    for event in events:
        day = source_day(event.get("source_from"))
        if day != source_day(event.get("source_to")) or day is None:
            continue  # multi-day reconstructed events do not prove per-day counts
        if not start <= day <= end or event.get("stream") != stream:
            raise ValueError("Event filter returned out-of-scope data")
        by_day[day] = event  # adapter guarantees ascending unique IDs
    days = []
    contiguous = None
    broken = False
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        where = {cap.date_field: {"$gte": day.isoformat(),
                                 "$lt": (day + timedelta(days=1)).isoformat()}}
        if stream.startswith("purchase-") and stream != "purchase-receive-history":
            where["document_kind"] = {"$eq": {"purchase-orders":"order",
                "purchase-receipts":"receipt", "purchase-returns":"return"}[stream]}
        count = client.count(cap.table, where=where)
        event = by_day.get(day)
        reason = "missing_source_evidence"
        if event:
            if event.get("status") != "success":
                reason = "latest_attempt_not_success"
            elif event.get("verification_method") not in METHODS:
                reason = "unrecognized_verification_method"
            elif event.get("coverage_level") in ("provisional", "unknown", None):
                reason = "unverified_coverage"
            elif event.get("source_rows") == event.get("saved_rows") == count:
                reason = "recorded_counts_match"
            else:
                reason = "count_mismatch"
        good = reason == "recorded_counts_match"
        if not good:
            broken = True
        if not broken:
            contiguous = day.isoformat()
        days.append({"day": day.isoformat(), "db_rows": count,
                     "source_rows": event.get("source_rows") if event else None,
                     "evidence_event_id": event.get("id") if event else None,
                     "verification_method": event.get("verification_method") if event else None,
                     "coverage_level": event.get("coverage_level") if event else None,
                     "status": reason})
    return {"stream": stream, "scope": "db", "coverage_scope": "list_counts",
            "from": start.isoformat(), "to": end.isoformat(), "days": days,
            "baseline_from": cap.configured_from, "baseline_verified": False,
            "last_contiguous_verified_day": None,
            "range_contiguous_recorded_counts_through": contiguous,
            "holes": [row for row in days if row["status"] != "recorded_counts_match"],
            "detail_coverage": "not_checked",
            "limitation": "Recorded event counts rechecked against DB; no new source capture, "
                          "no key-set or detail validation, no proof of global continuous history"}
