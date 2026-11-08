"""Operator actions compose ports; all scheduling uses durable evidence."""
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .artifacts import CaptureStore
from .capabilities import CAPABILITIES
from .planner import incremental_plan
from .locking import writer_lock
from .source import OpenCLISource, SourceCooling, SourceError
from .state import stream_state
from .workflow import WindowWorkflow
from kemai_source_gate import cooldown_until, gate_dir

ROOT=Path(__file__).resolve().parents[1]


def plan_incremental(client, streams, *, max_days=2, now=None):
    now=now or datetime.now(ZoneInfo('Asia/Shanghai'))
    cooling=cooldown_until(gate_dir())
    states={stream:stream_state(client,stream,now.date()-timedelta(days=1)) for stream in streams}
    plan=incremental_plan(states,now,max_days=max_days,
        cooldown_until=datetime.fromtimestamp(cooling,ZoneInfo('Asia/Shanghai')) if cooling else None)
    # Always revisit the latest closed day for delayed posting; distinct from outage catch-up.
    if plan['status']=='planned':
        day=plan['finalize_through']
        for stream in streams:
            if states[stream].get('baseline_verified') and not CAPABILITIES[stream].paused and CAPABILITIES[stream].granularity=='day' and not any(t['stream']==stream and t['day']==day for t in plan['tasks']):
                from .capabilities import capture_method
                plan['tasks'].append({'stream':stream,'day':day,'mode':'final','method':capture_method(stream),'reason':'late_arrival_revisit'})
    return plan


def history_plan(stream,start,end):
    cap=CAPABILITIES[stream]
    if cap.paused or cap.granularity!='day':raise ValueError('Use separate object backfill workflow')
    if not 1<=(end-start).days+1<=31:raise ValueError('Use bounded windows of 1..31 days')
    if end>=datetime.now(ZoneInfo('Asia/Shanghai')).date():raise ValueError('History ends before today')
    return {'tasks':[{'stream':stream,'day':(start+timedelta(days=i)).isoformat(),'mode':'final'}
                     for i in range((end-start).days+1)],'source_requests':0,'db_writes':0}


def execute_tasks(client, tasks, *, artifact_root=None, source=None):
    with writer_lock():
        return _execute_tasks(client, tasks, artifact_root=artifact_root, source=source)


def _execute_tasks(client, tasks, *, artifact_root=None, source=None):
    store=CaptureStore(artifact_root or ROOT/'.kemai-artifacts')
    workflow=WindowWorkflow(store,source or OpenCLISource(),client)
    results=[];busy_streams=set()
    for task in tasks:
        stream=task['stream']
        if stream in busy_streams:continue
        try:
            state=client.gateway('state',stream=stream,**{'from':task['day'],'to':task['day']})
            active=[c for c in state['captures'] if c['state']=='running']
            if len(active)>1:raise ValueError('Multiple active captures; refuse ambiguous resume')
            if active:
                capture=active[0]
                path=store.path(capture['capture_id'])/'manifest.json'
                if not path.exists():
                    client.fail_capture(capture,'artifact_missing_restart_with_new_capture')
                    result={'state':'interrupted','capture_id':capture['capture_id'],'next_action':'next_cycle_recapture'}
                else:result=workflow.resume(capture['capture_id'])
                # Resuming old work must not be counted as completion of a different requested day.
                result={**result,'resumed_window':capture['window_key']}
            else:result=workflow.begin(stream,task['day'],mode=task['mode'])
            results.append({'stream':stream,'requested_day':task['day'],**result})
            if result.get('state')!='committed':busy_streams.add(stream)
        except SourceCooling:
            results.append({'stream':stream,'state':'cooling','reason':'Shared 30-minute cooldown'});break
        except Exception as error:
            results.append({'stream':stream,'requested_day':task['day'],'state':'failed','error_type':type(error).__name__,
                            'reason':str(error) if isinstance(error,SourceError) else 'Inspect data-center receipt/contract diagnostics'})
            busy_streams.add(stream)
    return {'results':results,'source_operations':workflow.source.operations,
        'source_requests':None,'source_requests_basis':'HTTP-level counter not implemented',
        'complete':all(r.get('state')=='committed' for r in results) and len(results)==len(tasks)}
