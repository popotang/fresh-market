#!/usr/bin/env python3
"""Refresh the human-readable Kemai checkpoint from completed local audits."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def main():
    checkpoint_path = ROOT / "kemai_manual_checkpoint.json"
    checkpoint = read(checkpoint_path.name)
    full = read("kemai_followup_status.json")
    catchup = read("kemai_catchup_status.json")
    daily = read("kemai_daily_status.json")
    integrity = read("kemai_integrity_final_2026-11-07.json")
    products = read("kemai_product_coverage_audit_2026-11-07.json")
    history = read("kemai_history_reconciliation_2026-11-07.json")
    export = read("kemai_item_archives_export_probe_2026-11-07.json")
    streams = [
        "purchase-orders", "purchase-receipts", "purchase-returns",
        "retail-orders", "retail-sales",
    ]
    audits = {
        stream: read(f"kemai_{stream.replace('-', '_')}_audit_2026-11-07.json")
        for stream in streams
    }
    assert full["status"] == catchup["status"] == "success"
    assert catchup["complete_through"] == daily["last_complete_day"] == "2026-11-06"
    assert daily["status"] == "watching"
    assert all(not audit["issues"] and audit["checkpoint_day"] == "2026-11-06"
               for audit in audits.values())
    assert not products["issues"] and not history["issues"]
    assert not integrity["strict_failures"]["duplicates"]
    assert not integrity["strict_failures"]["orphans"]
    assert export["task_total_matches_catalog"]

    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    checkpoint["saved_at"] = now
    checkpoint["state"] = "daily_watching"
    checkpoint["reason"] = "full through 2026-11-05 and catch-up through 2026-11-06 succeeded; daily supervisor watching"
    checkpoint["automatic_resume"] = True
    checkpoint["processes"]["full_supervisor"]["state"] = "success"
    checkpoint["processes"]["catchup_supervisor"]["state"] = "success"
    checkpoint["processes"]["catchup_supervisor"]["complete_through"] = "2026-11-06"
    checkpoint["processes"]["daily_supervisor"]["state"] = "watching"
    checkpoint["processes"]["daily_supervisor"]["last_complete_day"] = "2026-11-06"

    counts = integrity["table_counts"]
    progress = checkpoint["log_progress"]
    progress["products"].update({
        "master_and_basic_detail": counts["products"],
        "db_products": counts["products"],
        "db_product_profiles": counts["product_profile"],
        "tabs": "initial full deferred; 206491 objects pending or failed",
        "initial_tab_pending_or_failed": products["initial_tab_pending_or_failed"],
        "changed_tab_objects_done": products["latest_changed_products"] * 14,
        "changed_tab_objects_expected": products["latest_changed_products"] * 14,
    })
    count_names = {
        "retail_orders": "retail_orders",
        "retail_sales": "retail_sale_flows",
        "purchase_receive_history": "purchase_receipt_history_lines",
    }
    for key, table in count_names.items():
        progress[key]["db_rows"] = counts[table]
    for key in ["purchase_orders", "purchase_receipts", "purchase_returns",
                "retail_orders", "retail_sales", "purchase_receive_history"]:
        progress[key]["last_complete_day"] = "2026-11-06"
    progress["purchase_receive_history"].update({
        "db_checkpoint": "2026-11-06",
        "source_window_through": "2026-11-06",
        "source_window_total": history["source_total"],
        "capture_rows": history["raw_capture_rows"],
        "reconciliation": "kemai_history_reconciliation_2026-11-07.json",
    })
    progress["retail_sales"]["daily_audit_checkpoint"] = "2026-11-06"
    for stream in ["purchase-orders", "purchase-receipts", "purchase-returns"]:
        progress[stream.replace("-", "_")]["db_rows"] = next(
            row["n"] for row in integrity["source_counts"] if row["stream"] == stream
        )
    for stream, audit in audits.items():
        key = stream.replace("-", "_")
        progress[key]["daily_audit"] = f"kemai_{key}_audit_2026-11-07.json"
        progress[key]["daily_audit_issues"] = len(audit["issues"])
        progress[key]["completed_source_days"] = audit["completed_source_days"]
        if "export_verified_or_empty_days" in audit:
            progress[key]["export_verified_or_empty_days"] = audit["export_verified_or_empty_days"]
            progress[key]["scan_only_days"] = audit["scan_only_days"]

    verification = checkpoint["verification"]
    verification.update({
        "last_report": "kemai_integrity_final_2026-11-07.json",
        "last_report_scope": "full_and_incremental_through_2026-11-06",
        "duplicates": sum(integrity["duplicates"].values()),
        "orphans": sum(integrity["orphans"].values()),
        "final_completeness_pending": False,
        "known_deferred_gaps_remain": True,
        "coverage_gap_records": integrity["coverage_gaps"] if isinstance(integrity["coverage_gaps"], int) else len(integrity["coverage_gaps"]),
    })
    for stream in ["purchase-orders", "purchase-receipts", "purchase-returns"]:
        key = stream.replace("-", "_")
        audit = audits[stream]
        verification["purchase_daily_audit"][key].update({
            "checkpoint": audit["checkpoint_day"],
            "days": audit["completed_source_days"],
            "database_capture_confirmed_days": audit["database_capture_confirmed_days"],
            "issues": len(audit["issues"]),
            "report": f"kemai_{key}_audit_2026-11-07.json",
        })
    verification["product_export_probe"] = {
        "command": "opencli kemai item-archives-export-start --snapshot YYYY-MM-DD --expected-total N -f json",
        "registered": True,
        "source_test_pending": False,
        "task_id": export["task_id"],
        "task_total_rows": export["task_total_rows"],
        "xlsx_data_rows": export["sheets"]["商品档案"]["estimated_data_rows"],
        "report": "kemai_item_archives_export_probe_2026-11-07.json",
        "field_audit": "kemai_item_archives_export_field_audit_2026-11-07.json",
    }

    temp = checkpoint_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n")
    temp.replace(checkpoint_path)
    print(json.dumps({"state": checkpoint["state"], "through": daily["last_complete_day"],
                      "duplicates": verification["duplicates"], "orphans": verification["orphans"],
                      "coverage_gap_records": verification["coverage_gap_records"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
