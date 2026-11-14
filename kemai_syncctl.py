#!/usr/bin/env python3
"""Kemai operator interface: capabilities, live DB status, gaps and bounded audits."""

import argparse
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from kemai_pipeline.audit import check_history
from kemai_pipeline.capabilities import CAPABILITIES
from kemai_pipeline.detail_workflow import DetailWorkflow, STREAM_KIND
from kemai_pipeline.operator import plan_incremental, history_plan, execute_tasks
from kemai_pipeline.workflow import WindowWorkflow
from kemai_pipeline.artifacts import ArtifactError, CaptureStore
from kemai_pipeline.source import OpenCLISource, SourceError
from kemai_pipeline.stored_payload_source import StoredPayloadSource
from kemai_pipeline.locking import WriterBusy
from kemai_pipeline.legacy_evidence import verify_legacy, adopt_legacy
from pathlib import Path
from kemai_pipeline.lovrabet_adapter import ContractError, LovrabetAdapter
from kemai_pipeline.runner import UnknownWrite
from kemai_pipeline.state import stream_state
from kemai_pipeline.routing import route_task
from kemai_pipeline.field_catalog import (
    PAUSED, resolve as resolve_fields, classify, routing_fields)
from kemai_pipeline.sync_mode import resolve_mode, detail_demand


def _resolve_mode_fields(args, streams, *, explicit_range, detail_scope=None):
    """Resolve the mode contract and requested fields across a command's streams.

    A field that resolves for no requested stream is refused; canonical paths
    that only fit some streams apply there. Paused capabilities block the plan
    in every mode. The legacy --include-details scope keeps its original
    purchase-gap semantics without the strict full-mode budget checks.
    """
    mode = resolve_mode(sync_mode=args.sync_mode,
                        include_details=getattr(args, 'include_details', False),
                        required_fields=args.required_fields)
    if getattr(args, 'detail_budget', None) is not None and args.detail_budget < 1:
        raise ValueError('detail budget must be a positive integer')
    groups = {'satisfied_by_list': set(), 'needs_detail': set(), 'paused': set()}
    flats = set()
    ambiguous = {}
    unresolvable = set()
    for stream in streams:
        resolution = resolve_fields(args.required_fields, stream)
        ambiguous.update(resolution['ambiguous'])
        for name in resolution['unknown']:
            if not any(resolve_fields([name], other)['resolved'] for other in streams):
                unresolvable.add(name)
        for spec in resolution['resolved']:
            if spec.detail_status == PAUSED:
                groups['paused'].add(spec.path)
            elif spec.satisfied_by_list:
                groups['satisfied_by_list'].add(spec.path)
            else:
                groups['needs_detail'].add(spec.path)
        flats.update(routing_fields(resolution['resolved'], stream))
    if ambiguous or unresolvable:
        raise ValueError('Field resolution refused (ambiguous/unknown): ' + json.dumps(
            {'ambiguous': {k: ambiguous[k] for k in sorted(ambiguous)},
             'unknown': sorted(unresolvable)}, ensure_ascii=False))
    merged = {k: sorted(v) for k, v in groups.items()}
    if mode['mode_basis'] == 'legacy_include_details':
        demand = {'detail_demand': 'bounded',
                  'detail_scope': 'legacy purchase document-detail gaps',
                  'note': 'legacy --include-details scope'}
    else:
        demand = detail_demand(mode, merged, explicit_objects=False,
                               detail_scope=detail_scope,
                               detail_budget=getattr(args, 'detail_budget', None),
                               explicit_range=explicit_range)
    if merged['paused']:
        demand = {'detail_demand': 'blocked', 'reason': 'paused_by_user',
                  'paused_fields': merged['paused']}
    return mode, merged, demand, sorted(flats)


def _blocked_by_paused_demand(result, streams, demand):
    result.setdefault('blocked', []).append(
        {'streams': list(streams), 'reason': 'paused_by_user',
         'fields': demand.get('paused_fields', [])})


def main(argv=None, *, adapter=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capabilities")
    def routing_options(action):
        action.add_argument('--strategy',choices=('auto','api','excel'),default='auto')
        action.add_argument('--estimated-rows',type=int,help='Estimated rows to scan per window, not proven coverage')
        action.add_argument('--missing-rows',type=int,help='Known/estimated missing rows; does not imply a selective source query')
        action.add_argument('--required-field',action='append',default=[],dest='required_fields')
        action.add_argument('--sync-mode',choices=('list','on_demand','full'),
                            help='Synchronization scope contract; default list, legacy flags map onto it')
        action.add_argument('--detail-budget',type=int,
                            help='Explicit per-run detail budget required by --sync-mode full')
    route=sub.add_parser('route',help='Explain source selection using local contracts; zero source and DB calls')
    route.add_argument('--stream',choices=tuple(CAPABILITIES),required=True)
    route.add_argument('--mode',choices=('final','provisional'),default='final')
    route.add_argument('--backlog-days',type=int,default=0)
    routing_options(route)
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
        routing_options(action)
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
        routing_options(action)
        action.add_argument('--include-details',action='store_true',help='Explicitly schedule full purchase detail gaps as well as base data')
        action.add_argument("--stream", choices=tuple(CAPABILITIES), action="append", required=True)
        action.add_argument("--max-catchup-days", type=int, default=2)
        if name=='run':action.add_argument('--max-source-operations',type=int,default=200)
    resume = sub.add_parser("resume")
    resume.add_argument("--capture-id", required=True)
    resume.add_argument('--max-source-operations',type=int,default=200)
    abandon = sub.add_parser("abandon", help="Interrupt one attempt, retain rows and artifacts; next capture uses a new ID")
    abandon.add_argument("--capture-id", required=True)
    detail = sub.add_parser("detail", help="Bounded purchase detail pass over day-scoped gaps")
    detail_actions = detail.add_subparsers(dest="action", required=True)
    for name in ("plan", "run"):
        action = detail_actions.add_parser(name)
        action.add_argument("--stream", choices=tuple(STREAM_KIND), required=True)
        action.add_argument("--day", type=date.fromisoformat, required=True)
        if name == "run":
            action.add_argument("--max-documents", type=int, default=200)
            action.add_argument("--retry-failed", action="store_true")
            action.add_argument("--max-source-operations", type=int, default=200)
    detail_resume = detail_actions.add_parser("resume")
    detail_resume.add_argument("--capture-id", required=True)
    stored = sub.add_parser("stored-detail", help="Backfill purchase children only from stored parent payloads")
    stored_actions = stored.add_subparsers(dest="action", required=True)
    for name in ("plan", "run", "verify"):
        action = stored_actions.add_parser(name)
        action.add_argument("--stream", choices=tuple(STREAM_KIND), required=True)
        action.add_argument("--day", type=date.fromisoformat, required=True)
        if name in ("plan", "run"):
            action.add_argument("--retry-failed", action="store_true")
        if name == "run":
            action.add_argument("--max-documents", type=int, default=200)
    stored_resume = stored_actions.add_parser("resume")
    stored_resume.add_argument("--capture-id", required=True)
    args = parser.parse_args(argv)
    client = adapter or LovrabetAdapter()
    output = {"schema_version": 1, "as_of": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
              "appcode": client.config["appcode"], "tenant_id": client.config["tenant_id"],
              "source_requests": 0, "db_writes": 0, "backend": "lovrabet_cli"}
    code = 0
    if args.command in ("resume","abandon") or getattr(args,"action",None) in ("fill","run"):
        output.update(source_requests=None, db_writes=None)
    elif args.command == "detail" and args.action in ("run", "resume"):
        output.update(source_requests=None, db_writes=None)
    elif getattr(args,"action",None) == "adopt-legacy":
        output["db_writes"] = None
    try:
        if args.command == 'route':
            result=route_task({'stream':args.stream,'mode':args.mode,'strategy':args.strategy,
                'estimated_rows':args.estimated_rows,'missing_rows':args.missing_rows,
                'required_fields':args.required_fields,'backlog_days':args.backlog_days},
                Path(__file__).resolve().parent/'.kemai-artifacts')
            if result['status']!='selected':code=2
        elif args.command == "capabilities":
            gateway = client.gateway("capabilities")
            result = {"streams": [cap.to_dict() for cap in CAPABILITIES.values()],
                      "enabled_commands": ["status", "gaps show", "history check --scope db", "history plan",
                          "history fill", "history verify-legacy", "history adopt-legacy", "incremental plan", "incremental run",
                          "detail plan", "detail run", "detail resume", "resume", "abandon"],
                      "writes_enabled": gateway["writes_enabled"],
                      "accepted_streams": gateway.get("accepted_streams", []),
                      "implementation_note": "Only accepted_streams can write; catalog/object migration remains separate"}
        elif args.command == "status":
            where = {"stream": {"$eq": args.stream}} if args.stream else {}
            since=(datetime.now(ZoneInfo('Asia/Shanghai')).date()-timedelta(days=7)).isoformat()
            windows=client.read_all('sync_pipeline_windows',where={**where,'window_key':{'$gte':since}},
                select=['stream','window_key','mode','row_count','completed_at','evidence'])
            durable_streams=[args.stream] if args.stream else list(CAPABILITIES)
            through=datetime.now(ZoneInfo('Asia/Shanghai')).date()-timedelta(days=1)
            durable={stream:stream_state(client,stream,through) for stream in durable_streams}
            result = {"rows": client.read_all("sync_stream_status", where=where,
                select=["stream", "stream_name", "last_attempt_status", "last_attempt_at",
                        "last_success_at", "covered_through", "coverage_level", "coverage_note",
                        "verification_method", "last_source_rows", "last_saved_rows"]),
                "last_contiguous_verified_day":durable[args.stream].get('last_contiguous_verified_day') if args.stream else None,
                "last_contiguous_verified_day_by_stream":None if args.stream else {
                    stream:state.get('last_contiguous_verified_day') for stream,state in durable.items()},
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
            mode, groups, demand, flats = _resolve_mode_fields(args, args.stream, explicit_range=False)
            result = plan_incremental(client, args.stream, max_days=args.max_catchup_days,
                include_details=(mode['sync_mode'] == 'full' and demand['detail_demand'] == 'bounded'),
                strategy=args.strategy,estimated_rows=args.estimated_rows,
                missing_rows=args.missing_rows,required_fields=flats,
                sync_mode={'mode': mode, 'demand': demand, 'groups': groups})
            if demand['detail_demand'] == 'blocked':
                _blocked_by_paused_demand(result, args.stream, demand)
                code = 2
            if args.action == "run":
                plan=result
                # Paused detail demand and planning-only full mode never reach the
                # executor; the blocked plan is returned read-only instead.
                execution_refused = (demand['detail_demand'] == 'blocked' or
                                     (mode['sync_mode'] == 'full'
                                      and mode['mode_basis'] != 'legacy_include_details'))
                result = execute_tasks(client, plan["tasks"],source=OpenCLISource(max_operations=args.max_source_operations)) if plan["tasks"] and not execution_refused else {
                    "complete":False,"results":[],"source_requests":0,"state":plan["status"]}
                if execution_refused:
                    result['execution_refused'] = True
                    result['note'] = ('no tasks executed: paused detail demand refused; '
                                      're-issue a list-only request for base lists' if demand['detail_demand'] == 'blocked' else
                                      'no tasks executed: full detail execution is planning-only until unit 3')
                if demand['detail_demand'] == 'blocked':
                    result['complete'] = False
                result['planning_blocked']=plan.get('blocked',[])
                result['planning_pending']=plan.get('planning_pending',[])
                result['deferred_tasks']=plan.get('deferred_tasks',[])
                result['planning_backlog']=plan.get('backlog_remaining',0)
                result['backlog_by_stream']=plan.get('backlog_by_stream',{})
                result['coverage_gaps']=plan.get('coverage_gaps',[])
                result['coverage_gap_counts']=plan.get('coverage_gap_counts',{})
                result['coverage_gaps_complete']=plan.get('coverage_gaps_complete',True)
                result['coverage_gaps_omitted']=plan.get('coverage_gaps_omitted',0)
                result['coverage_gap_detail_limit_per_stream']=plan.get('coverage_gap_detail_limit_per_stream')
                if result['planning_blocked'] or result['planning_pending']:result['complete']=False
                output["source_requests"] = result["source_requests"]
                output["db_writes"] = None
                if not result["complete"]: code = 2
        elif args.command == "stored-detail":
            root = Path(__file__).resolve().parent / ".kemai-artifacts"
            source = StoredPayloadSource(client)
            workflow = DetailWorkflow(CaptureStore(root), source, client,
                                      resolve_gaps=False, method='stored_payload',
                                      write_partial=True, verification_scope='stored_payload',
                                      capture_control=False)
            if args.action == "plan":
                result = workflow.plan(args.stream, args.day.isoformat(),
                                       retry_failed=args.retry_failed)
            elif args.action == "verify":
                result = workflow.verify(args.stream, args.day.isoformat())
                if not result.get("verified"):
                    code = 2
            elif args.action == "run":
                result = workflow.begin(args.stream, args.day.isoformat(),
                                        max_documents=args.max_documents,
                                        retry_failed=args.retry_failed)
                if result.get("state") != "committed":
                    code = 2
            else:
                result = workflow.resume(args.capture_id)
                if result.get("state") != "committed":
                    code = 2
            output.update(source_requests=0, source_operations=source.operations,
                          db_writes=None if args.action in ("run", "resume") else 0)
        elif args.command == "detail":
            root = Path(__file__).resolve().parent / ".kemai-artifacts"
            store = CaptureStore(root)
            if args.action == "plan":
                # Read-only preview; the source is never touched and its budget is zero.
                result = DetailWorkflow(store, OpenCLISource(max_operations=0),
                                        client).plan(args.stream, args.day.isoformat())
            else:
                source = OpenCLISource(max_operations=getattr(args, "max_source_operations", 200))
                workflow = DetailWorkflow(store, source, client)
                if args.action == "run":
                    result = workflow.begin(args.stream, args.day.isoformat(),
                                            max_documents=args.max_documents,
                                            retry_failed=args.retry_failed)
                else:
                    result = workflow.resume(args.capture_id)
                output.update(source_requests=0 if source.operations == 0 else None,
                              source_operations=source.operations, db_writes=None)
                if result.get("state") != "committed":
                    code = 2
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
            days = [args.start.isoformat(), args.end.isoformat()]
            mode, groups, demand, flats = _resolve_mode_fields(
                args, [args.stream], explicit_range=True, detail_scope={'days': days})
            result = history_plan(args.stream, args.start, args.end,strategy=args.strategy,
                estimated_rows=args.estimated_rows,missing_rows=args.missing_rows,required_fields=flats,
                sync_mode={'mode': mode, 'demand': demand, 'groups': groups})
            if demand['detail_demand'] == 'blocked':
                _blocked_by_paused_demand(result, [args.stream], demand)
                code = 2
            if args.action == "fill":
                # A refused demand must not reach the executor: paused detail
                # capabilities and planning-only full mode fail closed with a
                # read-only plan instead of producing write side effects.
                full_planning_only = (mode['sync_mode'] == 'full'
                                      and mode['mode_basis'] != 'legacy_include_details')
                if demand['detail_demand'] == 'blocked' or full_planning_only:
                    if full_planning_only:
                        result['blocked'].append(
                            {'stream': args.stream,
                             'reason': 'full_detail_execution_pending_unit3',
                             'note': 'full is planning-only until bounded detail tasks land; '
                                     're-issue without --sync-mode full or use the legacy scope'})
                    result = {**result, 'complete': False, 'results': [],
                              'note': 'no tasks executed: refused or planning-only demand'}
                    output.update(source_requests=0, db_writes=None)
                    code = 2
                else:
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
    except (ContractError, ValueError, SourceError, WriterBusy, UnknownWrite, ArtifactError) as error:
        output.update(ok=False, error={"type": type(error).__name__, "message": str(error)})
        code = 1
    output["data_center_requests"] = client.requests
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
