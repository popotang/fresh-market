"""Rebuild catch-up from durable per-day windows, never display maxima or attempts."""
from datetime import date,timedelta
import json
from statistics import median
from .capabilities import CAPABILITIES
from .legacy_evidence import cached_days


def stream_state(client,stream,through):
    cap=CAPABILITIES[stream]
    if cap.paused:return {'paused':True}
    if not cap.configured_from or cap.granularity!='day':return {'baseline_verified':False}
    baseline=date.fromisoformat(cap.configured_from)
    windows=client.read_all('sync_pipeline_windows',where={'stream':{'$eq':stream},
        'window_key':{'$gte':str(baseline),'$lte':str(through)},'mode':{'$eq':'final'},'coverage_scope':{'$eq':'list'}},
        select=['stream','window_key','row_count','evidence','completed_at'])
    covered={d for d in cached_days(client,stream) if baseline<=d<=through}
    recent_counts=[]
    for window in windows:
        day=date.fromisoformat(window['window_key']);evidence=window.get('evidence') or {}
        if isinstance(evidence,str):evidence=json.loads(evidence)
        if (evidence.get('window_time_field')==cap.date_field
            and type(evidence.get('source_rows')) is int and evidence['source_rows']==window['row_count']
            and evidence.get('verification_method') in ('export_total_and_capture','reused_export_total_and_capture','single_scan_count',
                'empty_page_and_db','legacy_export_and_db','legacy_single_scan_and_db','xlsx_rows_v1')):
            if evidence.get('verification_method')=='xlsx_rows_v1' and (
                    cap.export_role!='data_with_validated_mapping'
                    or not evidence.get('xlsx_sha256') or not evidence.get('mapping_version')
                    or evidence.get('parsed_rows')!=window['row_count']):
                continue
            covered.add(day)
            if 0 <= (through-day).days <= 7:
                recent_counts.append((day,window['row_count']))
    cursor=baseline-timedelta(days=1)
    while cursor+timedelta(days=1) in covered:cursor+=timedelta(days=1)
    return {'baseline_verified':baseline in covered,'baseline_from':str(baseline),
        'baseline_meaning':'Explicit lower bound with per-day list evidence using the source query time field',
        'last_contiguous_verified_day':str(cursor) if cursor>=baseline else None,
        'coverage_scope':'list_counts','window_time_field':cap.date_field,'verification_method':'durable_windows',
        'covered_days':[str(d) for d in sorted(covered)],'holes':[],
        'estimated_scan_rows':int(median([count for _,count in recent_counts])) if recent_counts else None,
        'estimate_basis':{'kind':'median_verified_recent_windows','windows':len(recent_counts),
                          'latest_day':str(max(d for d,_ in recent_counts)) if recent_counts else None},
        'limitations':['No new source read','Recorded counts do not prove key-set or detail coverage']}
