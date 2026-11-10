"""Offline XLSX/retained-capture comparison. Reports counts only; never promotes acceptance."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kemai_pipeline.artifacts import CaptureStore, atomic_json
from kemai_pipeline.export_mappings import HISTORY_EXPORT_FIELDS
from kemai_pipeline.normalize import scaled, history_key
from kemai_pipeline.xlsx_import import count_history_rows, parse_history_xlsx


def compare(xlsx,root,tenant):
    xlsx=Path(xlsx);root=Path(root)
    sha=hashlib.sha256(xlsx.read_bytes()).hexdigest()
    meta_path=Path(str(xlsx)+'.meta.json')
    meta=json.loads(meta_path.read_text()) if meta_path.exists() else None
    if meta and meta.get('sha256')!=sha:raise ValueError('XLSX receipt digest mismatch')
    count=meta['rows'] if meta else count_history_rows(xlsx)
    rows=list(parse_history_xlsx(xlsx,expected_rows=count,mapping=HISTORY_EXPORT_FIELDS))
    groups=defaultdict(list)
    for row in rows:groups[str(row['sheetNo'])].append(row)
    parents=defaultdict(dict);store=CaptureStore(root);invalid=0
    for path in root.glob('*/manifest.json'):
        try:
            state=store.load(path.parent.name)
            if str(state.get('tenant'))!=str(tenant) or state['stream'] not in ('purchase-receipts','purchase-returns'):continue
            verified_rows=list(store.rows(state))
            for record in verified_rows:
                data=record['data']
                if str(data.get('sheet_no')) not in groups:continue
                payload=data.get('payload');entity=json.loads(payload) if isinstance(payload,str) else payload
                if not isinstance(entity,dict) or str(entity.get('sheetNo'))!=str(data['sheet_no']):
                    raise ValueError('Parent scope mismatch')
                key=(state['stream'],str(data['source_document_id']),str(data.get('source_version')),data.get('payload_hash'))
                parents[str(data['sheet_no'])][key]=entity
        except (OSError,ValueError,KeyError,TypeError):invalid+=1
    stats=Counter();fields=Counter()
    for row in rows:
        for field in ('itemName','itemCode','itemSubno','itemSize','unitName','qty','amt','taxAmt'):
            fields[field]+=row.get(field) not in (None,'')
    for sheet,items in groups.items():
        candidates=list(parents[sheet].values())
        if len(candidates)!=1:
            stats['ambiguous_parent' if candidates else 'missing_parent']+=1
            continue
        entity=candidates[0];stats['unique_parent']+=1
        scope=all(all(str(row.get(k))==str(entity.get(k)) and row.get(k) not in (None,'')
                      for k in ('branchCode','supplierCode','storeCode')) for row in items)
        stats['parent_scope_matches']+=scope
        from decimal import Decimal
        amount=sum(Decimal(scaled(row,'amt',1)) for row in items)
        stats['parent_amount_matches']+=amount==Decimal(scaled(entity,'sumAmt'))
        details=entity.get('details')
        if not isinstance(details,list):
            stats['documents_without_detail_evidence']+=1
            continue
        keyed=defaultdict(list)
        for line in details:keyed[history_key({**line,'sheetNo':sheet})].append(line)
        for row in items:
            matches=keyed[history_key(row)]
            if len(matches)!=1:
                stats['unresolved_detail_rows']+=1;continue
            stats['compared_detail_rows']+=1
            line=matches[0]
            pairs=[(scaled(row,f,1),scaled(line,f)) for f in ('qty','amt','taxAmt')]
            stats['matching_detail_numeric_rows']+=all((a is None and b is None) or
                (a is not None and b is not None and Decimal(a)==Decimal(b)) for a,b in pairs)
    return {'schema_version':1,'xlsx_sha256':sha,'xlsx_rows':len(rows),'xlsx_documents':len(groups),
            'independent_file_receipt':meta is not None,'parent_comparison':dict(stats),
            'field_present_rows':dict(fields),'invalid_context_captures':invalid,
            'ready_for_canonical_line_import':False,
            'limitations':['Offline retained observations are not current DB state',
                'Parent matching does not prove stable detail IDs, per-line numeric equivalence or import acceptance'],
            'source_requests':0,'db_writes':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xlsx',type=Path,required=True)
    parser.add_argument('--artifact-root',type=Path,default=Path(__file__).resolve().parents[1]/'.kemai-artifacts')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        config=json.loads((Path(__file__).resolve().parents[1]/'kemai_pipeline/datasets.json').read_text())
        report=compare(args.xlsx,args.artifact_root,config['tenant_id'])
        if args.output:atomic_json(args.output,report)
        print(json.dumps(report,ensure_ascii=False,indent=2));return 0
    except Exception as error:
        print(json.dumps({'ok':False,'error_type':type(error).__name__,'source_requests':0,'db_writes':0}));return 1


if __name__=='__main__':raise SystemExit(main())
