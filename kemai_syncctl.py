#!/usr/bin/env python3
"""Kemai operator interface: capabilities, live DB status, gaps and bounded audits."""

import argparse
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from kemai_pipeline.audit import check_history
from kemai_pipeline.capabilities import CAPABILITIES
from kemai_pipeline.operator import plan_incremental, history_plan, execute_tasks
from kemai_pipeline.workflow import WindowWorkflow
from kemai_pipeline.artifacts import CaptureStore
from kemai_pipeline.source import OpenCLISource, SourceError
from kemai_pipeline.locking import WriterBusy
from kemai_pipeline.legacy_evidence import verify_legacy, adopt_legacy
from pathlib import Path
from kemai_pipeline.lovrabet_adapter import ContractError, LovrabetAdapter
from kemai_pipeline.runner import UnknownWrite


def main(argv=None, *, adapter=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    status = sub.add_parser("status")
    status.add_argument("--stream")
    gaps = sub.add_parser("gaps").add_subparsers(dest="action", required=True).add_parser("show")
    gaps.add_argument("--stream")
    gaps.add_argument("--include-resolved", action="store_true")
    history = sub.add_parser("history").add_subparsers(dest="action", required=True)
    check = history.add_parser("check")
    check.add_argument("--scope", choices=("db",), default="db",
                       help="source comparison is not yet enabled")
    check.add_argument("--stream", choices=tuple(CAPABILITIES), required=True)
    check.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    check.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    for name in ("plan", "fill"):
        action = history.add_parser(name)
        action.add_argument("--stream", choices=tuple(CAPABILITIES), required=True)
        action.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
        action.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
        if name=='fill':action.add_argument('--max-source-operations',type=int,default=200)
    legacy = history.add_parser("verify-legacy")
    legacy.add_argument("--stream", choices=tuple(CAPABILITIES), required=True)
    adopt = history.add_parser("adopt-legacy")
    adopt.add_argument("--stream", choices=tuple(CAPABILITIES), required=True)
    incremental = sub.add_parser("incremental").add_subparsers(dest="action", required=True)
    for name in ("plan", "run"):
        action = incremental.add_parser(name)
        action.add_argument("--stream", choices=tuple(CAPABILITIES), action="append", required=True)
        action.add_argument("--max-catchup-days", type=int, default=2)
        if name=='run':action.add_argument('--max-source-operations',type=int,default=200)
    resume = sub.add_parser("resume")
    resume.add_argument("--capture-id", required=True)
    resume.add_argument('--max-source-operations',type=int,default=200)
    abandon = sub.add_parser("abandon", help="Interrupt one attempt, retain rows and artifacts; next capture uses a new ID")
    abandon.add_argument("--capture-id", required=True)
    args = parser.parse_args(argv)
    client = adapter or LovrabetAdapter()
    output = {"schema_version": 1, "as_of": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
              "appcode": client.config["appcode"], "tenant_id": client.config["tenant_id"],
              "source_requests": 0, "db_writes": 0, "backend": "lovrabet_cli"}
    code = 0
    if args.command in ("resume","abandon") or getattr(args,"action",None) in ("fill","run"):
        output.update(source_requests=None, db_writes=None)
    elif getattr(args,"action",None) == "adopt-legacy":
        output["db_writes"] = None
    try:
        if args.command == "capabilities":
            gateway = client.gateway("capabilities")
            result = {"streams": [cap.to_dict() for cap in CAPABILITIES.values()],
                      "enabled_commands": ["status", "gaps show", "history check --scope db", "history plan",
                          "history fill", "history verify-legacy", "history adopt-legacy", "incremental plan", "incremental run", "resume"],
                      "writes_enabled": gateway["writes_enabled"],
                      "accepted_streams": gateway.get("accepted_streams", []),
                      "implementation_note": "Only accepted_streams can write; catalog/object migration remains separate"}
        elif args.command == "status":
            where = {"stream": {"$eq": args.stream}} if args.stream else {}
            since=(datetime.now(ZoneInfo('Asia/Shanghai')).date()-timedelta(days=7)).isoformat()
            windows=client.read_all('sync_pipeline_windows',where={**where,'window_key':{'$gte':since}},
                select=['stream','window_key','mode','row_count','completed_at','evidence'])
            result = {"rows": client.read_all("sync_stream_status", where=where,
                select=["stream", "stream_name", "last_attempt_status", "last_attempt_at",
                        "last_success_at", "covered_through", "coverage_level", "coverage_note",
                        "verification_method", "last_source_rows", "last_saved_rows"]),
                "last_contiguous_verified_day": None,
                "recent_window_evidence":windows,
                "evidence_range_from":since,
                "freshness_note":"last_success_at is ingestion time; source observation and snapshot cutoff are in each window's evidence",
                "note": "covered_through is a display maximum, not continuous verified coverage"}
        elif args.command == "gaps":
            where = {} if args.include_resolved else {"status": {"$eq": "pending"}}
            if args.stream:
                where["stream"] = {"$eq": args.stream}
            rows = client.read_all("sync_coverage_gaps", where=where,
                select=["stream", "scope_key", "missing_parts", "source_row_count", "status",
                        "first_seen_at", "last_seen_at"])
            result = {"rows": rows, "count": len(rows)}
        elif args.command == "incremental":
            result = plan_incremental(client, args.stream, max_days=args.max_catchup_days)
            if args.action == "run":
                plan=result
                result = execute_tasks(client, plan["tasks"],source=OpenCLISource(max_operations=args.max_source_operations)) if plan["tasks"] else {
                    "complete":False,"results":[],"source_requests":0,"state":plan["status"]}
                result['planning_blocked']=plan.get('blocked',[])
                if result['planning_blocked']:result['complete']=False
                output["source_requests"] = result["source_requests"]
                output["db_writes"] = None
                if not result["complete"]: code = 2
        elif args.command in ("resume","abandon"):
            root = Path(__file__).resolve().parent / ".kemai-artifacts"
            source = OpenCLISource(max_operations=getattr(args,'max_source_operations',0))
            workflow=WindowWorkflow(CaptureStore(root), source, client)
            result = workflow.resume(args.capture_id) if args.command=='resume' else workflow.abandon(args.capture_id)
            output.update(source_requests=0 if source.operations==0 else None, source_operations=source.operations, db_writes=None)
            if args.command=='resume' and result.get("state") != "committed": code = 2
        elif args.action == "adopt-legacy":
            result = adopt_legacy(client,args.stream)
            output["db_writes"] = None
        elif args.action == "verify-legacy":
            result = verify_legacy(client,args.stream)
            if result["issues"]: code = 2
        elif args.action in ("plan", "fill"):
            result = history_plan(args.stream, args.start, args.end)
            if args.action == "fill":
                result = execute_tasks(client, result["tasks"],source=OpenCLISource(max_operations=args.max_source_operations))
                output.update(source_requests=result["source_requests"], db_writes=None)
                if not result["complete"]: code = 2
        else:
            result = check_history(client, args.stream, args.start, args.end)
            if result["holes"]:
                code = 2
        output.update(ok=True, result=result)
    except KeyboardInterrupt:
        output.update(ok=False, error={"type":"Interrupted", "message":"Capture artifacts preserved; resume the same capture ID"})
        code = 130
    except (ContractError, ValueError, SourceError, WriterBusy, UnknownWrite) as error:
        output.update(ok=False, error={"type": type(error).__name__, "message": str(error)})
        code = 1
    output["data_center_requests"] = client.requests
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
