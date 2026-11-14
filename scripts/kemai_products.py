#!/usr/bin/env python3
"""Explicit bounded product objects; no scheduler or legacy MySQL entry point."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kemai_pipeline.product_workflow import ProductWorkflow, DEFAULT_SECTIONS, SECTIONS
from kemai_pipeline.field_catalog import resolve as resolve_fields, classify
from kemai_pipeline.sync_mode import resolve_mode, detail_demand
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter
from kemai_pipeline.source import OpenCLISource


def plan_mode(args, parser):
    """Resolve --sync-mode against field demand; returns (mode_info, list_only, sections).

    Legacy invocations keep their original semantics: --list-only is a list
    plan, and a bare plan stays the original bounded detail plan (sections,
    run --limit) recorded as basis 'legacy_detail_plan'.
    """
    if args.detail_only and args.section:
        parser.error('--detail-only cannot be combined with --section')
    tabs = []
    groups = {'satisfied_by_list': [], 'needs_detail': [], 'paused': []}
    if args.required_fields:
        resolution = resolve_fields(args.required_fields, 'item_list')
        if resolution['ambiguous']:
            parser.error('ambiguous field names (choose canonical paths): '
                         + json.dumps(resolution['ambiguous'], ensure_ascii=False, sort_keys=True))
        if resolution['unknown']:
            parser.error('unknown field paths: ' + ','.join(sorted(resolution['unknown'])))
        groups = classify(resolution['resolved'])
        if groups['paused']:
            parser.error('paused detail capability requested: ' + ','.join(sorted(groups['paused'])))
        tabs = sorted({p.rsplit('.', 1)[-1] for p in groups['needs_detail']
                       if p.startswith('product.tabs.')})
        if tabs and args.detail_only:
            parser.error('--detail-only cannot be combined with tab field demands')
    try:
        mode = resolve_mode(sync_mode=args.sync_mode, list_only=args.list_only,
                            required_fields=args.required_fields)
    except ValueError as error:
        parser.error(str(error))
    explicit_scope = bool(args.ids or args.section)  # --inventory is a list source, not an explicit detail-object selection
    if args.sync_mode is None and mode['mode_basis'] == 'default':
        # Legacy bare plan: the original detail plan for selected objects.
        mode = {'sync_mode': 'on_demand', 'mode_basis': 'legacy_detail_plan',
                'detail_demand': 'bounded', 'detail_scope': 'selected objects; default sections'}
    else:
        if mode['sync_mode'] == 'full' and not (args.ids or args.inventory):
            parser.error('full requires an explicit object scope: --id or --inventory')
        try:
            # Explicit object selection legitimizes detail only when no field
            # demand was stated at all: satisfied-only required fields degrade.
            mode.update(detail_demand(
                mode, groups, explicit_objects=explicit_scope and not args.required_fields,
                detail_scope={'fields': sorted(groups['needs_detail'])} if groups['needs_detail']
                else {'objects': 'explicit selection', 'sections': 'default'},
                detail_budget=args.limit if mode['sync_mode'] == 'full' else None,
                explicit_range=bool(args.ids or args.inventory)))
        except ValueError as error:
            parser.error(str(error))
    degrade = mode['sync_mode'] == 'on_demand' and mode.get('detail_demand') == 'none'
    list_only = bool(args.list_only or degrade or args.sync_mode == 'list')
    if list_only and (args.section or args.detail_only or tabs):
        parser.error('a list-mode plan cannot combine with --section, --detail-only or detail fields')
    if list_only and not args.inventory:
        parser.error('a list-mode plan requires --inventory (run inventory first); '
                     'without one, request detail explicitly via --sync-mode on_demand --required-field')
    # Explicit field demand scopes sections exactly: master detail fields need the
    # detail phase only; no demand means the legacy default sections apply.
    sections = sorted(set(list(args.section or []) + tabs))
    if list_only:
        sections = []
    elif not sections:
        sections = tabs if args.required_fields else ([] if args.detail_only else DEFAULT_SECTIONS)
    return mode, list_only, sections


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=ROOT/'.kemai-artifacts/product-objects')
    p.add_argument('--max-operations',type=int,default=250)
    sub=p.add_subparsers(dest='action',required=True)
    plan=sub.add_parser('plan',help='Read own DB or sealed inventory; no source requests or DB writes')
    scope=plan.add_mutually_exclusive_group()
    scope.add_argument('--id',action='append',dest='ids')
    scope.add_argument('--inventory',help='Sealed list inventory capture ID; includes newly discovered products')
    plan.add_argument('--after-id',type=int,default=0)
    plan.add_argument('--limit',type=int,default=10)
    plan.add_argument('--section',action='append',choices=SECTIONS)
    plan.add_argument('--detail-only',action='store_true')
    plan.add_argument('--list-only',action='store_true',help='Legacy alias for --sync-mode list')
    plan.add_argument('--all-objects',action='store_true',help='Inventory: include unchanged masters too; default selects new/changed')
    plan.add_argument('--sync-mode',choices=('list','on_demand','full'),help='Synchronization scope contract')
    plan.add_argument('--required-field',action='append',dest='required_fields',default=[],
                      help='Canonical field path, e.g. product.tabs.stock')
    plan.add_argument('--max-db-requests',type=int,default=400,
                      help='Plan-phase data-center read budget; fail-closed before any read')
    catalog=sub.add_parser('inventory',help='One list scan; capture IDs, no DB writes')
    catalog.add_argument('--max-pages',type=int,default=200)
    catalog.add_argument('--from',dest='from_time',help='Optional inclusive start time: YYYY-MM-DD or YYYY-MM-DD HH:mm:ss')
    catalog.add_argument('--sort',choices=('gmtCreate','approveTime'),help='Optional descending time field')
    for action in ('run','resume'):
        cmd=sub.add_parser(action);cmd.add_argument('--capture',required=True);cmd.add_argument('--limit',type=int,default=5)
    status=sub.add_parser('status',help='Local capture summaries only');status.add_argument('--capture')
    args=p.parse_args(argv)
    worker=ProductWorkflow(LovrabetAdapter(),OpenCLISource(max_operations=args.max_operations),args.root)
    try:
        if args.action=='plan':
            mode,list_only,sections=plan_mode(args,p)
            demanded_tabs=sorted({p.rsplit('.',1)[-1] for p in (mode.get('detail_fields') or [])
                                  if p.startswith('product.tabs.')})
            result=worker.plan(args.ids,after_id=args.after_id,limit=args.limit,inventory=args.inventory,
                               changed_only=not args.all_objects,list_only=list_only,
                               sections=sections,sync_mode=mode,max_db_requests=args.max_db_requests,
                               tab_demands=demanded_tabs)
        elif args.action=='inventory':result=worker.inventory(args.max_pages,from_time=args.from_time,sort=args.sort)
        elif args.action=='status':
            captures=[args.capture] if args.capture else sorted(path.parent.name for path in args.root.glob('*/manifest.json'))
            result=[worker.summary(worker.load(c)) for c in captures]
        else:result=worker.run(args.capture,args.limit)
        print(json.dumps({'ok':True,'data':result},ensure_ascii=False));return 0
    except Exception as error:
        # Do not emit arbitrary API bodies, source rows or URLs in diagnostics.
        print(json.dumps({'ok':False,'error':type(error).__name__,
                          'capture_id':getattr(args,'capture',None),
                          'hint':'Inspect local status/snapshot; resume same capture after fixing the error. Interrupted inventory starts anew.'}))
        return 2

if __name__=='__main__':raise SystemExit(main())
