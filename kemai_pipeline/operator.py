"""Operator actions compose ports; all scheduling uses durable evidence."""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .artifacts import CaptureStore
from .capabilities import CAPABILITIES, capture_method
from .detail_workflow import DetailWorkflow, detail_scope
from .planner import incremental_plan
from .locking import writer_lock
from .source import OpenCLISource, SourceCooling, SourceError
from .state import stream_state
from .workflow import WindowWorkflow
from .source_gate import cooldown_until, gate_dir
from .routing import annotate_plan, route_task
from .lines_only_workflow import LinesOnlyWorkflow
from .sync_mode import coverage_annotations

ROOT=Path(__file__).resolve().parents[1]
COVERAGE_GAP_DETAIL_LIMIT=1000


def _attach_mode(plan, sync_mode, *, list_scope):
    """Attach the mode contract and separated coverage semantics to a plan."""
    if not sync_mode:
        return plan
    mode, demand = sync_mode['mode'], sync_mode['demand']
    planning_only = str(demand.get('detail_execution', '')).startswith('planning_only_pending_unit3')
    detail_coverage = ('blocked: paused_by_user' if demand['detail_demand'] == 'blocked' else
                       'not scheduled; list evidence only' if demand['detail_demand'] == 'none' else
                       'detail not scheduled; execution pending unit 3' if planning_only else
                       'bounded detail planned for the requested fields/scope; '
                       'per-object verification, never whole-stream completeness')
    mode_block = {**mode, 'detail_demand': demand['detail_demand']}
    if 'note' in demand:
        mode_block['note'] = demand['note']
    if 'detail_execution' in demand:
        mode_block['detail_execution'] = demand['detail_execution']
    plan['sync_mode'] = mode_block
    plan['detail_fields'] = sync_mode['groups']
    plan['detail_demand'] = demand['detail_demand']
    plan['coverage'] = coverage_annotations(mode, demand, list_scope=list_scope,
                                            detail_coverage=detail_coverage,
                                            detail_scope=demand.get('detail_scope'))
    return plan


def plan_incremental(client, streams, *, max_days=2, now=None, include_details=False,
                     strategy='auto', estimated_rows=None, missing_rows=None, required_fields=(),
                     sync_mode=None):
    now=now or datetime.now(ZoneInfo('Asia/Shanghai'))
    cooling=cooldown_until(gate_dir())
    states={stream:stream_state(client,stream,now.date()-timedelta(days=1)) for stream in streams}
    gap_counts={};gap_omitted={}
    for stream in streams:
        where={'stream':{'$eq':stream},'status':{'$eq':'pending'}}
        count=client.count('sync_coverage_gaps',where=where)
        gap_counts[stream]=count
        if count>COVERAGE_GAP_DETAIL_LIMIT:
            gaps=[]
            gap_omitted[stream]=count
        elif count:
            gaps=client.read_all('sync_coverage_gaps',where=where,
                select=['stream','scope_key','missing_parts','source_row_count','status','first_seen_at','last_seen_at'])
        else:gaps=[]
        states[stream]={**states[stream],'coverage_gaps':gaps}
    plan=incremental_plan(states,now,max_days=max_days,
        cooldown_until=datetime.fromtimestamp(cooling,ZoneInfo('Asia/Shanghai')) if cooling else None)
    # Always represent the latest closed-day revisit, including cooling deferrals;
    # it is distinct from outage catch-up and never adds outage backlog.
    if plan['status'] in ('planned','cooling'):
        day=plan['finalize_through']
        for stream in streams:
            if not (states[stream].get('baseline_verified') and not CAPABILITIES[stream].paused and CAPABILITIES[stream].granularity=='day'):
                continue
            revisit={'stream':stream,'day':day,'mode':'final','method':capture_method(stream),'reason':'late_arrival_revisit'}
            if plan['status']=='planned':
                if not any(t['stream']==stream and t['day']==day and t.get('kind')!='detail_pass' for t in plan['tasks']):
                    plan['tasks'].append(revisit)
                    plan['planning_pending']=[task for task in plan.get('planning_pending',[])
                        if not (task['stream']==stream and task['day']==day)]
            elif not any(t['stream']==stream and t['day']==day and t.get('kind')!='detail_pass'
                         for t in plan.get('deferred_tasks',[])+plan.get('planning_pending',[])):
                plan['deferred_tasks'].append(revisit)
    pending=plan.get('planning_pending',[])
    plan['backlog_remaining']=len(pending)
    plan['backlog_by_stream']={stream:sum(task['stream']==stream for task in pending)
        for stream in streams if any(task['stream']==stream for task in pending)}
    plan.update(coverage_gap_counts=gap_counts,coverage_gaps_complete=not gap_omitted,
        coverage_gaps_omitted=sum(gap_omitted.values()),
        coverage_gap_detail_limit_per_stream=COVERAGE_GAP_DETAIL_LIMIT)
    for stream,count in gap_omitted.items():
        plan['blocked'].append({'stream':stream,'reason':'coverage_gap_details_over_budget',
            'count':count,'detail_limit':COVERAGE_GAP_DETAIL_LIMIT})
    if not include_details:
        for key in ('tasks','planning_pending','deferred_tasks'):
            plan[key]=[t for t in plan.get(key,[]) if t.get('kind')!='detail_pass']
        for gap in plan['coverage_gaps']:
            if gap.get('scheduling')=='detail_pass':
                gap.update(scheduling='on_demand',reason='base_sync_does_not_require_full_detail')
        plan['backlog_remaining']=len(plan['planning_pending'])
        plan['backlog_by_stream']={s:sum(t['stream']==s for t in plan['planning_pending']) for s in streams
                                  if any(t['stream']==s for t in plan['planning_pending'])}
    for key in ('tasks','planning_pending','deferred_tasks'):
        for task in plan.get(key,[]):
            task['estimated_rows']=states[task['stream']].get('estimated_scan_rows')
            task['estimate_basis']=states[task['stream']].get('estimate_basis')
    return _attach_mode(annotate_plan(plan,ROOT/'.kemai-artifacts',strategy=strategy,estimated_rows=estimated_rows,
                         missing_rows=missing_rows,required_fields=required_fields),
                        sync_mode, list_scope='planned day windows, backlog and provisional snapshot')


def history_plan(stream,start,end, *, strategy='auto', estimated_rows=None, missing_rows=None,
                 required_fields=(), sync_mode=None):
    cap=CAPABILITIES[stream]
    if cap.paused or cap.granularity!='day':raise ValueError('Use separate object backfill workflow')
    if not 1<=(end-start).days+1<=31:raise ValueError('Use bounded windows of 1..31 days')
    if end>=datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError('History ends before today')
    plan={'tasks':[{'stream':stream,'day':(start+timedelta(days=i)).isoformat(),'mode':'final',
                      'method':capture_method(stream),'reason':'explicit_history_backfill'}
                     for i in range((end-start).days+1)],
            'blocked':[],'planning_pending':[],'backlog_remaining':0,'backlog_by_stream':{},
            'coverage_gaps':[],'status':'planned','source_requests':0,'db_writes':0}
    return _attach_mode(annotate_plan(plan,ROOT/'.kemai-artifacts',strategy=strategy,estimated_rows=estimated_rows,
                         missing_rows=missing_rows,required_fields=required_fields),
                        sync_mode, list_scope=f'{start.isoformat()}..{end.isoformat()} day windows')


def execute_tasks(client, tasks, *, artifact_root=None, source=None):
    with writer_lock():
        return _execute_tasks(client, tasks, artifact_root=artifact_root, source=source)


def _run_lines_task(store,source,client,task,decision):
    workflow=LinesOnlyWorkflow(store,source,client)
    workflow.routing_decision=decision
    server_active=client.read_all('sync_pipeline_captures',where={
        'stream':{'$eq':detail_scope(task['stream'])},'state':{'$eq':'running'}},select=['capture_id'])
    if server_active:raise ValueError('Existing full-detail capture requires explicit recovery')
    active=[]
    for path in store.root.glob('*/manifest.json'):
        state=store.load(path.parent.name)
        if (str(state.get('tenant'))==str(client.config['tenant_id'])
                and state.get('stream')==detail_scope(task['stream'])
                and state.get('window_key')==task['day'] and state.get('server_started')=='local_only'
                and state.get('method')=='detail_api' and state.get('phase')!='committed'):
            active.append(state)
    if len(active)>1:raise ValueError('Ambiguous local line capture; choose explicit resume')
    if active:
        state=active[0]
        state['max_documents']=10
        state['routing']=decision
        store.save(state)
        return workflow.resume(state['capture_id'])
    return workflow.begin(task['stream'],task['day'],max_documents=10)


def _execute_tasks(client, tasks, *, artifact_root=None, source=None):
    store=CaptureStore(artifact_root or ROOT/'.kemai-artifacts')
    workflow=WindowWorkflow(store,source or OpenCLISource(),client)
    detail=DetailWorkflow(store,workflow.source,client)
    results=[];busy_streams=set();cooling=False

    def deferred(task,state='deferred',reason='Earlier work prevents this task'):
        return {'stream':task['stream'],'requested_day':task['day'],'state':state,
                'requested_day_committed':False,'deferred':True,'reason':reason}

    for index,task in enumerate(tasks):
        stream=task['stream']
        if CAPABILITIES[stream].paused:
            results.append(deferred(task,state='blocked',reason='paused_by_user'))
            continue
        if cooling:
            results.append(deferred(task,reason='Shared 30-minute cooldown'))
            continue
        try:
            # Detail passes run under their own capture scope so they never
            # collide with the stream's day-window captures. scope must stay
            # bound on every failure path (the except branch records it).
            scope=stream
            if task.get('kind')=='detail_pass':scope=detail_scope(stream)
            decision=None
            line_decision=None
            if task.get('kind')!='detail_pass':
                decision=route_task(task,store.root)
                if decision['status']=='needs_detail':
                    line_decision=decision
                    decision=route_task({**task,'required_fields':[]},store.root)
            if scope in busy_streams:
                results.append(deferred(task,reason='Earlier capture for this stream is incomplete'))
                continue
            active=client.read_all('sync_pipeline_captures',where={
                'stream':{'$eq':scope},'state':{'$eq':'running'}},
                select=['capture_id','window_key','state','stream','mode','source_key_version'])
            if len(active)>1:raise ValueError('Multiple active captures; refuse ambiguous resume')
            if active:
                capture=active[0]
                path=store.path(capture['capture_id'])/'manifest.json'
                if not path.exists():
                    client.fail_capture(capture,'artifact_missing_restart_with_new_capture')
                    result={'state':'interrupted','capture_id':capture['capture_id'],'next_action':'next_cycle_recapture'}
                else:result=(detail if task.get('kind')=='detail_pass' else workflow).resume(capture['capture_id'])
                # Resuming old work must not be counted as completion of a different requested day.
                result={**result,'resumed_window':capture['window_key']}
                result_window=capture['window_key']
            else:
                if task.get('kind')=='detail_pass':
                    result=detail.begin(stream,task['day'])
                else:
                    # Re-evaluate local mapping/acceptance at execution; plan flags never grant permission.
                    if cooldown_until(gate_dir())>datetime.now().timestamp():
                        raise SourceCooling('Shared source cooldown')
                    if decision['status']!='selected':
                        results.append({**deferred(task,state='blocked',reason=decision['reason']),
                                        'routing':decision})
                        continue
                    workflow.routing_decision=decision
                    result=workflow.begin(stream,task['day'],mode=task['mode'],method=decision['method'])
                    result={**result,'routing':decision}
                result_window=task['day']
            if line_decision and result.get('state')=='committed' and result_window==task['day']:
                base_capture=result.get('capture_id')
                result=_run_lines_task(store,workflow.source,client,task,line_decision)
                result={**result,'routing':line_decision,'requested_scope':'lines_only',
                        'base_window_committed':True,'base_capture_id':base_capture}
            committed=result.get('state')=='committed' and result_window==task['day']
            results.append({'stream':stream,'requested_day':task['day'],**result,
                'requested_day_committed':committed,'deferred':not committed})
            if not committed:busy_streams.add(scope)
        except SourceCooling:
            results.append(deferred(task,state='cooling',reason='Shared 30-minute cooldown'))
            cooling=True
        except Exception as error:
            results.append({'stream':stream,'requested_day':task['day'],'state':'failed',
                'requested_day_committed':False,'deferred':True,'error_type':type(error).__name__,
                'reason':str(error) if isinstance(error,SourceError) else 'Inspect data-center receipt/contract diagnostics'})
            busy_streams.add(scope)
    return {'results':results,'source_operations':workflow.source.operations,
        'source_requests':None,'source_requests_basis':'HTTP-level counter not implemented',
        'complete':len(results)==len(tasks) and all(r.get('requested_day_committed') for r in results)}
