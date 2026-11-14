#!/usr/bin/env python3
"""Seed canonical metric definitions from business_metrics.mjs into MySQL."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "kemai_pipeline/js/business_metrics.mjs"


def load_definitions() -> dict:
    module_url = MODULE.as_uri()
    script = (
        f"import {{ DEFINITION_VERSION, METRIC_DEFINITIONS }} from {json.dumps(module_url)};\n"
        "process.stdout.write(JSON.stringify({version:DEFINITION_VERSION,metrics:METRIC_DEFINITIONS}));"
    )
    output = subprocess.check_output(
        ["node", "--input-type=module", "-e", script],
        text=True,
        cwd=ROOT,
        timeout=30,
    )
    return json.loads(output)


def connect() -> pymysql.connections.Connection:
    raw = os.environ.get("FRESH_MARKET_DSN")
    if not raw:
        raise RuntimeError("FRESH_MARKET_DSN is required")
    parsed = urlparse(raw.removeprefix("jdbc:"))
    if parsed.scheme != "mysql":
        raise RuntimeError("FRESH_MARKET_DSN must be a mysql URL")
    return pymysql.connect(
        host=parsed.hostname or "",
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=parsed.path.lstrip("/"),
        charset="utf8mb4",
        autocommit=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    payload = load_definitions()
    metrics = payload["metrics"]
    if not metrics:
        raise RuntimeError("No metric definitions exported")
    print(json.dumps({"version": payload["version"], "sourceCount": len(metrics), "dryRun": args.dry_run}, ensure_ascii=False))
    if args.dry_run:
        return 0

    connection = connect()
    sql = """
      INSERT INTO metric_definitions (
        definition_version, metric_code, name, group_code, unit, formula,
        source_name, nullable, enabled, sort_order
      ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
      ON DUPLICATE KEY UPDATE
        name = VALUES(name),
        group_code = VALUES(group_code),
        unit = VALUES(unit),
        formula = VALUES(formula),
        source_name = VALUES(source_name),
        nullable = VALUES(nullable),
        enabled = VALUES(enabled),
        sort_order = VALUES(sort_order)
    """
    rows = [
        (
            payload["version"],
            metric["code"],
            metric["name"],
            metric["group"],
            metric["unit"],
            metric["formula"],
            metric["source"],
            1 if metric.get("nullable", True) else 0,
            1,
            index + 1,
        )
        for index, metric in enumerate(metrics)
    ]
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM metric_definitions WHERE definition_version = %s", (payload["version"],))
            before = cursor.fetchone()[0]
            cursor.executemany(sql, rows)
            cursor.execute("SELECT COUNT(*) FROM metric_definitions WHERE definition_version = %s AND enabled = 1", (payload["version"],))
            after = cursor.fetchone()[0]
        connection.commit()
        print(json.dumps({"before": before, "after": after, "written": len(rows)}, ensure_ascii=False))
        if after != len(rows):
            raise RuntimeError("Metric definition count mismatch after write")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    main()
