"""Deterministic source-method selection. No browser, DB or source probes.

An estimate chooses transport; it never establishes coverage or a write delta.
Recovery takes precedence over a new transport decision.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from .capabilities import CAPABILITIES, capture_method
from .mapping_verification import load_verification
from .source_gate import cooldown_until, gate_dir

BASE_FIELDS = {
    'purchase-orders': {'source_document_id','sheet_no','amount','source_version','status_code'},
    'purchase-receipts': {'source_document_id','sheet_no','amount','source_version','status_code'},
    'purchase-returns': {'source_document_id','sheet_no','amount','source_version','status_code'},
    'purchase-receive-history': {'sheet_no','quantity','amount','approved_at','business_date'},
    'retail-orders': {'source_order_id','receipt_no','sale_amount','sold_at'},
    'retail-sales': {'source_flow_id','source_order_id','source_item_id','quantity','sale_amount','sold_at'},
}
PURCHASE_LINE_FIELDS = {'source_item_id','quantity','unit_price','amount','tax_amount','payload'}
for stream in ('purchase-orders','purchase-receipts','purchase-returns'):
    BASE_FIELDS[stream].update({'document_kind','sheet_type','source_document_ref_id','source_document_ref_no',
        'source_supplier_id','source_branch_id','source_receive_branch_id','source_created_at',
        'source_modified_at','source_approved_at','payload','payload_hash'})
BASE_FIELDS['retail-orders'].update({'source_receipt_no','source_branch_id','source_member_id','source_type',
    'channel_code','sale_flag','order_amount','source_created_at','payload','payload_hash'})
BASE_FIELDS['retail-sales'].update({'receipt_no','source_branch_id','source_supplier_id','coupon_share_amount',
    'source_created_at','payload','payload_hash'})
HISTORY_EXPORT_FIELDS = BASE_FIELDS['purchase-receive-history'] | {'source_line_key','payload','payload_hash'}
BASE_FIELDS['purchase-receive-history']=HISTORY_EXPORT_FIELDS | {'source_document_id','sheet_type',
    'source_supplier_id','source_branch_id','source_item_id'}


def policy():
    value = json.loads(Path(__file__).with_name('routing_policy.json').read_text())
    for field in ('export_min_scan_rows','export_min_backlog_days','api_page_size','cost_min_samples','cost_max_age_hours'):
        if type(value.get(field)) is not int or value[field] < 1:
            raise ValueError('Invalid routing policy')
    if not 0 < value['export_cost_margin'] <= 1:
        raise ValueError('Invalid routing cost margin')
    return value


def choose_route(stream, *, mode='final', estimated_rows=None, missing_rows=None,
                 backlog_days=0, required_fields=(), preference='auto', mapping_verified=False,
                 accepted=True, active_capture=None, cooling=False, costs=None, config=None):
    cap = CAPABILITIES[stream]
    settings = config or policy()
    if mode not in ('final','provisional') or preference not in ('auto','api','excel'):
        raise ValueError('Invalid routing request')
    for value in (estimated_rows, missing_rows):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError('Row estimates must be nonnegative integers')
    if type(backlog_days) is not int or backlog_days < 0:
        raise ValueError('Invalid backlog estimate')
    if not isinstance(required_fields,(list,tuple,set)) or any(not isinstance(f,str) for f in required_fields):
        raise ValueError('Invalid required fields')
    result = {'schema_version':1,'stream':stream,'status':'selected','method':None,
              'action':'capture_window','estimated_rows':estimated_rows,'missing_rows':missing_rows,
              'estimate_only':True,'backlog_days':backlog_days,'preference':preference,
              'required_fields':sorted(set(required_fields)), 'rejected_alternatives':[],
              'source_requests':0,'db_writes':0,
              'note':'A small missing count does not make a full-window scan small; equal counts do not prove equality.'}
    if cap.paused:
        return {**result,'status':'blocked','reason':'paused_by_user'}
    if cooling:
        return {**result,'status':'cooling','reason':'shared_cooldown_no_transport_switch'}
    if active_capture:
        return {**result,'action':'resume_capture','capture_id':active_capture['capture_id'],
                'method':active_capture.get('method'),'reason':'reuse_capture_and_exact_artifacts'}
    if cap.granularity != 'day':
        return {**result,'status':'blocked','reason':'requires_object_workflow'}
    absent = set(required_fields)-BASE_FIELDS.get(stream,set())
    if absent:
        line_request = stream in ('purchase-orders','purchase-receipts','purchase-returns') and absent <= PURCHASE_LINE_FIELDS
        if line_request and not accepted:
            return {**result,'status':'blocked','reason':'stream_acceptance_required'}
        return {**result,'status':'needs_detail' if line_request else 'blocked',
                'action':'lines_only' if line_request else 'none','method':'detail_api' if line_request else None,
                'reason':'required_fields_not_in_base_rows','missing_fields':sorted(absent)}
    if stream in settings.get('api_only_streams',[]):
        return {**result,'method':'api_only' if preference!='excel' else None,
                'status':'selected' if accepted and preference!='excel' else 'blocked',
                'reason':'user_policy_api_only' if accepted else 'stream_acceptance_required',
                'rejected_alternatives':[{'method':'export_for_data','reason':'user_policy_api_only'}]}
    export_fields_ok = stream=='purchase-receive-history' and set(required_fields)<=HISTORY_EXPORT_FIELDS
    export_ok = cap.export_role == 'data_with_validated_mapping' and mapping_verified and mode == 'final' and export_fields_ok
    if not export_ok:
        reason = ('export_count_only' if cap.export_role=='count_only' else
                  'mapping_not_verified' if cap.export_role=='data_with_validated_mapping' and not mapping_verified else
                  'required_fields_not_in_export' if cap.export_role=='data_with_validated_mapping' and not export_fields_ok else
                  'provisional_export_scope_not_verified' if cap.export_role=='data_with_validated_mapping' else
                  'export_data_contract_unavailable')
        result['rejected_alternatives'].append({'method':'export_for_data','reason':reason})
        if preference == 'excel':
            return {**result,'status':'blocked','reason':reason}
    api = capture_method(stream,provisional=mode=='provisional')
    method, reason = api, 'api_for_small_or_unknown_window'
    if export_ok:
        if preference == 'excel':
            method,reason='export_for_data','explicit_excel_with_verified_mapping'
        elif preference != 'api':
            # Only same-scope, recent measured costs may override the configured thresholds.
            usable = (isinstance(costs,dict) and costs.get('stream')==stream and costs.get('scope')=='same_window'
                      and type(costs.get('samples')) is int and costs['samples']>=settings['cost_min_samples']
                      and isinstance(costs.get('age_hours'),(int,float)) and 0<=costs['age_hours']<=settings['cost_max_age_hours']
                      and estimated_rows is not None)
            if usable:
                for field in ('api_page_seconds','export_fixed_seconds','export_row_seconds'):
                    value=costs.get(field)
                    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                        usable=False
            if usable:
                api_cost=max(1,math.ceil(estimated_rows/settings['api_page_size']))*costs['api_page_seconds']
                excel_cost=costs['export_fixed_seconds']+estimated_rows*costs['export_row_seconds']
                result['estimated_seconds']={'api':api_cost,'excel':excel_cost}
                method='export_for_data' if excel_cost < api_cost*settings['export_cost_margin'] else api
                reason='measured_same_scope_cost'
            elif estimated_rows is not None and estimated_rows>=settings['export_min_scan_rows']:
                method,reason='export_for_data','large_scan_window'
            elif estimated_rows is None and backlog_days>=settings['export_min_backlog_days']:
                method,reason='export_for_data','long_outage_unknown_volume'
    if preference=='api':reason='explicit_api'
    result.update(method=method,reason=reason)
    if not accepted:
        result.update(status='blocked',reason='stream_acceptance_required',candidate_reason=reason)
    return result


def route_task(task, root, *, backlog_days=0, costs=None):
    """Local evidence is authoritative; caller booleans cannot grant acceptance/mapping."""
    accepted=json.loads(Path(__file__).with_name('acceptance.json').read_text())['streams']
    if costs is None:
        try:
            observation=json.loads((Path(root)/'routing-costs.json').read_text())[task['stream']]
            measured=datetime.fromisoformat(observation['measured_at'])
            if measured.tzinfo is not None and observation.get('window_time_field')==CAPABILITIES[task['stream']].date_field:
                costs={**observation,'stream':task['stream'],
                       'age_hours':(datetime.now(timezone.utc)-measured).total_seconds()/3600}
        except (OSError,ValueError,KeyError,TypeError):
            pass  # Optional observations never unlock a source contract.
    return choose_route(task['stream'], mode=task.get('mode','final'),
        estimated_rows=task.get('estimated_rows'), missing_rows=task.get('missing_rows'),
        required_fields=task.get('required_fields',()), preference=task.get('strategy','auto'),
        backlog_days=task.get('backlog_days',backlog_days), costs=costs,
        cooling=cooldown_until(gate_dir())>datetime.now(timezone.utc).timestamp(),
        mapping_verified=load_verification(task['stream'],root) is not None,
        accepted=task['stream'] in accepted)


def annotate_plan(plan, root, *, strategy='auto', estimated_rows=None, missing_rows=None, required_fields=()):
    all_tasks=plan.get('tasks',[])+plan.get('planning_pending',[])+plan.get('deferred_tasks',[])
    for task in all_tasks:
        if task.get('kind')=='detail_pass':
            continue
        task.update(strategy=strategy,estimated_rows=estimated_rows if estimated_rows is not None else task.get('estimated_rows'),missing_rows=missing_rows,
                    required_fields=list(required_fields))
        task['backlog_days']=len({t['day'] for t in all_tasks if t['stream']==task['stream'] and t['mode']=='final'})
        task['routing']=route_task(task,root)
        if task['routing']['method']:
            task['method']=task['routing']['method']
    plan['routing_summary']={status:sum(t.get('routing',{}).get('status')==status for t in all_tasks)
                             for status in ('selected','blocked','needs_detail','cooling')}
    return plan
