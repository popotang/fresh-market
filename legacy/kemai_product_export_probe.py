#!/usr/bin/env python3
"""Inspect one Kemai itemArchives Excel export after source sync is idle."""

import argparse
import csv
import json
import os
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from kemai_commerce_sync import ROOT, call, encoded_export_url, pause


def ensure_source_idle():
    for filename, required in (("kemai_followup_status.json", "success"),
                               ("kemai_catchup_status.json", "success")):
        path = ROOT / filename
        state = json.loads(path.read_text()) if path.exists() else {}
        if state.get("status") != required:
            raise RuntimeError(f"source sync not complete: {filename} status={state.get('status')}")
    daily = ROOT / '.runtime' / 'state' / 'kemai_daily_status.json'
    state = json.loads(daily.read_text()) if daily.exists() else {}
    if state.get("status") in ("running", "retry_wait") or any(
            job.get("status") == "running"
            for jobs in state.get("days", {}).values() for job in jobs.values()):
        raise RuntimeError("daily source sync is active; wait before probing product export")


def completed(task):
    return (str(task.get("status")) == "2" and task.get("sourceUrl")
            and task.get("totalRows") is not None)


def await_export(snapshot, expected_total):
    started = time.monotonic()
    task = call("item-archives-export-start", "--snapshot", snapshot,
                "--expected-total", str(expected_total))[0]
    pause(started, 0.5)
    task_id = str(task["id"])
    for _ in range(30):
        if completed(task):
            return task
        if task.get("failReason") or str(task.get("status")) == "3":
            raise RuntimeError(f"product export task {task_id} failed: {task.get('failReason')}")
        time.sleep(10)
        rows = call("export-task-list", "--rows", "50")
        current = next((row for row in rows if str(row.get("id")) == task_id), None)
        if current:
            task = {**task, **{key: value for key, value in current.items()
                             if value is not None}}
    raise RuntimeError(f"product export task {task_id} did not complete in five minutes")


def inspect_rows(rows):
    first = []
    total_rows = 0
    for row in rows:
        values = tuple(row)
        if any(value not in (None, "") for value in values):
            total_rows += 1
        if len(first) < 6:
            first.append(values)
    header_at = next((index for index, row in enumerate(first)
                      if any(str(value or "").strip() in ("商品编码", "商品名称")
                             for value in row)), None)
    headers = ([str(value or "").strip() for value in first[header_at]]
               if header_at is not None else [])
    # Headers are often preceded by one or two title rows. File row counts
    # remain observational until the source task's totalRows is checked.
    prefix = (sum(any(value not in (None, "") for value in row)
                  for row in first[:header_at + 1]) if header_at is not None else 0)
    data_rows = total_rows - prefix
    return {"nonempty_rows": total_rows, "header_row_number": header_at + 1 if header_at is not None else None,
            "headers": headers, "estimated_data_rows": data_rows,
            "first_six_row_widths": [len(row) for row in first]}


def inspect_file(path):
    with path.open("rb") as stream:
        signature = stream.read(4)
    if signature.startswith(b"PK\x03\x04"):
        from openpyxl import load_workbook
        # openpyxl rejects the temporary .download suffix even when the file
        # signature is XLSX. Keep the stream open through lazy worksheet reads.
        with path.open("rb") as stream:
            book = load_workbook(stream, read_only=True, data_only=True)
            try:
                return {"format": "xlsx", "sheets": {
                    sheet.title: inspect_rows(sheet.iter_rows(values_only=True))
                    for sheet in book.worksheets}}
            finally:
                book.close()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as stream:
                return {"format": "csv", "encoding": encoding,
                        "sheets": {"csv": inspect_rows(csv.reader(stream))}}
        except UnicodeDecodeError:
            continue
    raise RuntimeError("product export is neither xlsx nor a decodable CSV")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="YYYY-MM-DD; same value reuses an existing export task")
    parser.add_argument("--expected-total", required=True, type=int,
                        help="current product list total from a completed catalog scan")
    args = parser.parse_args()
    if args.expected_total < 0:
        parser.error("--expected-total must be nonnegative")
    snapshot = args.snapshot
    try:
        valid_snapshot = (len(snapshot) == 10 and
                          datetime.strptime(snapshot, "%Y-%m-%d").strftime("%Y-%m-%d") == snapshot)
    except ValueError:
        valid_snapshot = False
    if not valid_snapshot:
        parser.error("--snapshot must be YYYY-MM-DD")
    ensure_source_idle()
    task = await_export(snapshot, args.expected_total)
    if task.get("sheetType") != "itemArchives":
        raise RuntimeError(f"unexpected product export sheet type: {task.get('sheetType')}")
    url = encoded_export_url(str(task["sourceUrl"]))
    with tempfile.NamedTemporaryFile(prefix="kemai-product-", suffix=".download", dir=ROOT,
                                     delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        urllib.request.urlretrieve(url, temp_path)
        os.chmod(temp_path, 0o600)
        inspected = inspect_file(temp_path)
        extension = ".xlsx" if inspected["format"] == "xlsx" else ".csv"
        file_path = ROOT / f"kemai_item_archives_{snapshot}{extension}"
        temp_path.replace(file_path)
    finally:
        temp_path.unlink(missing_ok=True)
    task_total = int(task["totalRows"])
    report = {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot": snapshot,
        "task_id": str(task["id"]),
        "task_total_rows": task_total,
        "expected_catalog_total": args.expected_total,
        "task_total_matches_catalog": task_total == args.expected_total,
        "downloaded_file": str(file_path),
        **inspected,
    }
    report_path = ROOT / f"kemai_item_archives_export_probe_{snapshot}.json"
    temp_report = report_path.with_suffix(".json.tmp")
    temp_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temp_report, 0o600)
    temp_report.replace(report_path)
    print(json.dumps({"report": str(report_path), "file": str(file_path),
                      "task_total": task_total, "matches_catalog": report["task_total_matches_catalog"],
                      "sheets": {name: {"estimated_data_rows": value["estimated_data_rows"],
                                        "header_row_number": value["header_row_number"]}
                                 for name, value in inspected["sheets"].items()}}, ensure_ascii=False))
    if not report["task_total_matches_catalog"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
