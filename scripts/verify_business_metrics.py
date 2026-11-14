#!/usr/bin/env python3
"""Verify the published retail metric semantic layer without printing business rows."""

from __future__ import annotations

import json
import subprocess
import sys

APP = "app-21522951"
ENDPOINT = "businessMetricGateway"
DASHBOARD_SQL = "21522951-59797d83"
WINDOW = {"startDate": "2026-11-01", "endDate": "2026-11-07"}
MARKETING_WINDOW = {"startDate": "2026-11-09", "endDate": "2026-11-09"}


def bff_raw(params: dict) -> tuple[int, dict | None, str]:
    completed = subprocess.run(
        ["lovrabet", "bff", "exec", "--appcode", APP, "--name", ENDPOINT,
         "--params", json.dumps(params, ensure_ascii=False), "--format", "compress"],
        capture_output=True, text=True, timeout=180,
    )
    if completed.returncode:
        return completed.returncode, None, completed.stderr.strip()
    envelope = json.loads(completed.stdout)
    return 0, envelope.get("data", {}).get("result"), ""


def bff(params: dict) -> dict:
    code, result, error = bff_raw(params)
    if code or not isinstance(result, dict):
        raise RuntimeError(error or "BFF returned no result object")
    return result


def close(left: float, right: float, tolerance: float = 1e-6) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def invariant(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    summary: dict[str, object] = {}

    catalog = bff({"action": "catalog"})
    invariant(catalog.get("definitionVersion") == "retail-metrics-v1.0.0", "catalog version mismatch", failures)
    invariant(len(catalog.get("metrics", [])) >= 40, "catalog metric count too small", failures)

    retail = bff({"action": "query", "groupCode": "retail_performance", **WINDOW, "dimension": "branch"})
    metrics = retail["metrics"]
    invariant(retail.get("definitionVersion") == "retail-metrics-v1.0.0", "retail version missing", failures)
    invariant(
        close(metrics["gross_sales_amount"] - metrics["refund_amount"], metrics["net_sales_amount"], 0.02),
        "retail gross-refund != net",
        failures,
    )
    for row in retail.get("rows", []):
        invariant(
            close(row["gross_sales_amount"] - row["refund_amount"], row["net_sales_amount"], 0.02),
            "retail row invariant failed",
            failures,
        )
    for key in ("refund_rate", "cost_coverage_rate"):
        value = metrics.get(key)
        invariant(value is None or 0 <= value <= 1, f"{key} outside [0,1]", failures)
    summary["retail"] = {
        "net_sales_amount": metrics.get("net_sales_amount"),
        "order_count": metrics.get("order_count"),
        "cost_coverage_rate": metrics.get("cost_coverage_rate"),
    }

    inventory = bff({"action": "query", "groupCode": "inventory_health",
                     "endDate": WINDOW["endDate"], "dimension": "branch"})
    inv = inventory["metrics"]
    risk_total = sum(inv.get(key) or 0 for key in (
        "stockout_sku_count", "low_stock_sku_count", "overstock_sku_count", "slow_sku_count"))
    invariant(risk_total <= (inv.get("snapshot_sku_count") or 0), "inventory risk counts exceed snapshots", failures)
    for key in ("sold_sku_snapshot_coverage", "snapshot_sales_coverage"):
        value = inv.get(key)
        invariant(value is None or 0 <= value <= 1, f"{key} outside [0,1]", failures)
    summary["inventory"] = {
        "snapshot_sku_count": inv.get("snapshot_sku_count"),
        "risk_total": risk_total,
        "snapshot_sales_coverage": inv.get("snapshot_sales_coverage"),
    }

    purchase = bff({"action": "query", "groupCode": "purchase_performance",
                    **WINDOW, "dimension": "branch"})
    purchase_metrics = purchase["metrics"]
    top5 = purchase_metrics.get("supplier_top5_share")
    invariant(top5 is None or 0 <= top5 <= 1, "supplier_top5_share outside [0,1]", failures)
    summary["purchase"] = {
        "net_receipt_amount": purchase_metrics.get("net_receipt_amount"),
        "supplier_top5_share": top5,
    }

    marketing = bff({"action": "query", "groupCode": "marketing_performance",
                     **MARKETING_WINDOW, "dimension": "promotion"})
    marketing_metrics = marketing["metrics"]
    link_rate = marketing_metrics.get("promotion_link_rate")
    invariant(link_rate is None or 0 <= link_rate <= 1, "promotion_link_rate outside [0,1]", failures)
    summary["marketing"] = {
        "marketing_discount_amount": marketing_metrics.get("marketing_discount_amount"),
        "promotion_link_rate": link_rate,
    }

    health = bff({"action": "health"})
    invariant(bool(health.get("coverage", {}).get("latest_biz_date")), "health latest_biz_date missing", failures)
    summary["health"] = {
        "latest_biz_date": health["coverage"].get("latest_biz_date"),
        "payment_fact_count": health["coverage"].get("payment_fact_count"),
        "warnings": health.get("warnings", []),
    }

    for name, params in {
        "invalid_metric": {"action": "query", "groupCode": "retail_performance", **WINDOW,
                           "metricCodes": ["made_up"]},
        "inverted_dates": {"action": "query", "groupCode": "retail_performance",
                           "startDate": "2026-11-07", "endDate": "2026-11-01"},
        "oversized_window": {"action": "query", "groupCode": "retail_performance",
                             "startDate": "2026-01-01", "endDate": "2026-06-01"},
    }.items():
        code, _, _ = bff_raw(params)
        invariant(code != 0, f"{name} was not rejected", failures)

    dashboard = subprocess.run(
        ["rabetbase", "sql", "exec", "--sqlcode", DASHBOARD_SQL,
         "--params", json.dumps(WINDOW), "--format", "compress"],
        capture_output=True, text=True, timeout=180,
    )
    if dashboard.returncode:
        failures.append("old dashboard query failed")
    else:
        rows = json.loads(dashboard.stdout)["data"]["rows"]
        old_net = sum(float(row.get("sales_amount") or 0) for row in rows)
        invariant(close(old_net, metrics["net_sales_amount"], 0.02), "old dashboard net sales mismatch", failures)
        summary["legacy_net_sales_amount"] = round(old_net, 2)

    print(json.dumps({"ok": not failures, "failures": failures, "summary": summary},
                     ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "failures": [str(exc)]}, ensure_ascii=False))
        sys.exit(1)
