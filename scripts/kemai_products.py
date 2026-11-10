#!/usr/bin/env python3
"""Explicit bounded product objects; no scheduler or legacy MySQL entry point."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kemai_pipeline.product_workflow import ProductWorkflow, DEFAULT_SECTIONS, SECTIONS
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter
from kemai_pipeline.source import OpenCLISource


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
    plan.add_argument('--all-objects',action='store_true',help='Inventory: include unchanged masters too; default selects new/changed')
    catalog=sub.add_parser('inventory',help='One list scan; capture IDs, no DB writes')
    catalog.add_argument('--max-pages',type=int,default=200)
    for action in ('run','resume'):
        cmd=sub.add_parser(action);cmd.add_argument('--capture',required=True);cmd.add_argument('--limit',type=int,default=5)
    status=sub.add_parser('status',help='Local capture summaries only');status.add_argument('--capture')
    args=p.parse_args(argv)
    worker=ProductWorkflow(LovrabetAdapter(),OpenCLISource(max_operations=args.max_operations),args.root)
    try:
        if args.action=='plan':
            if args.detail_only and args.section:p.error('--detail-only cannot be combined with --section')
            result=worker.plan(args.ids,after_id=args.after_id,limit=args.limit,inventory=args.inventory,
                               changed_only=not args.all_objects,
                               sections=[] if args.detail_only else args.section or DEFAULT_SECTIONS)
        elif args.action=='inventory':result=worker.inventory(args.max_pages)
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
