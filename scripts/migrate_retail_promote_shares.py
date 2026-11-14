#!/usr/bin/env python3
"""Copy retail promote-share rows from Lovrabet METADATA to the owned MySQL table.

The source read is paginated and ordered by id. Target writes use INSERT IGNORE,
so rerunning after an interruption does not rewrite already verified rows.
Credentials are read from FRESH_MARKET_DSN and are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import unquote, urlparse

import pymysql

APP_CODE = "app-21522951"
SOURCE_DATASET = "fde264b695ce39d2bca6d0065449f31e"
TABLE = "retail_promote_shares"
COLUMNS = [
    "id", "tenant_id", "source_row_hash", "occurrence_no", "source_order_id",
    "sheet_no", "marketing_type", "promotion_id", "promotion_code",
    "activity_code", "activity_name", "amount", "original_amount",
    "sale_amount", "original_price", "quantity", "unit_quantity", "unit_name",
    "sold_at", "source_created_at", "source_operation_at", "branch_code",
    "branch_name", "item_code", "item_subno", "item_name", "item_size",
    "item_category_id", "item_category_code", "item_category_name",
    "item_brand_id", "item_brand_code", "item_brand_name", "member_card_no",
    "member_grade_id", "member_grade_name", "channel", "capture_id", "page_no",
    "row_no", "payload", "payload_hash", "captured_at",
]
DATETIME_COLUMNS = {"sold_at", "source_created_at", "source_operation_at", "captured_at"}


def connect_from_env() -> pymysql.connections.Connection:
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


def mysql_datetime(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, (int, float)):
        seconds = value / 1000 if abs(value) >= 100_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(tzinfo=None)
    text = str(value).strip()
    if text.replace(".", "", 1).isdigit():
        return mysql_datetime(float(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Unsupported DATETIME representation: {type(value).__name__}") from exc
    return mysql_datetime(parsed)


def normalize_row(row: dict[str, Any]) -> tuple[Any, ...]:
    values = []
    for column in COLUMNS:
        value = row.get(column)
        if column in DATETIME_COLUMNS:
            value = mysql_datetime(value)
        elif column == "payload" and not isinstance(value, str):
            value = json.dumps(value if value is not None else {}, ensure_ascii=False)
        values.append(value)
    return tuple(values)


def read_page(page: int, page_size: int) -> dict[str, Any]:
    params = {
        "select": COLUMNS,
        "orderBy": [{"id": "asc"}],
        "currentPage": page,
        "pageSize": page_size,
    }
    command = [
        "lovrabet", "data", "filter", "--appcode", APP_CODE,
        "--code", SOURCE_DATASET, "--params", json.dumps(params, separators=(",", ":")),
        "--format", "compress",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if completed.returncode:
        raise RuntimeError(f"Source page {page} read failed: {completed.stderr.strip()[:500]}")
    envelope = json.loads(completed.stdout)
    if not envelope.get("ok"):
        raise RuntimeError(f"Source page {page} returned ok=false")
    return envelope["data"]["result"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Read one source page without writing")
    parser.add_argument("--page-size", type=int, default=500)
    args = parser.parse_args()
    if not 1 <= args.page_size <= 1000:
        parser.error("--page-size must be between 1 and 1000")

    first = read_page(1, args.page_size)
    source_total = int(first["paging"]["totalCount"])
    if args.dry_run:
        sample = first["tableData"][:1]
        if sample:
            normalize_row(sample[0])
        print(json.dumps({"dryRun": True, "sourceTotal": source_total, "firstPageRows": len(first["tableData"])}))
        return 0

    connection = connect_from_env()
    inserted = 0
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM " + TABLE)
            before = cursor.fetchone()[0]
        sql = (
            f"INSERT IGNORE INTO {TABLE} ({','.join(COLUMNS)}) VALUES "
            f"({','.join(['%s'] * len(COLUMNS))})"
        )
        page = 1
        result = first
        while True:
            rows = result["tableData"]
            if not rows:
                break
            with connection.cursor() as cursor:
                affected = cursor.executemany(sql, [normalize_row(row) for row in rows])
            connection.commit()
            inserted += max(affected, 0)
            print(json.dumps({"page": page, "read": len(rows), "insertedTotal": inserted}), flush=True)
            paging = result["paging"]
            if int(paging["totalCount"]) != source_total:
                raise RuntimeError("Source total changed during migration; rerun after investigating")
            if page * args.page_size >= source_total:
                break
            page += 1
            result = read_page(page, args.page_size)
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM " + TABLE)
            after = cursor.fetchone()[0]
        print(json.dumps({
            "sourceTotal": source_total,
            "targetBefore": before,
            "targetAfter": after,
            "insertedThisRun": inserted,
            "complete": after >= source_total,
        }))
        return 0 if after >= source_total else 2
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        sys.exit(1)
