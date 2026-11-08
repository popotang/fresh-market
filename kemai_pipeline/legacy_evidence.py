"""Import old source-count evidence using standard DB reads, without recapturing SaaS."""
from datetime import date, datetime, timedelta
from pathlib import Path
import hashlib
import re
from zoneinfo import ZoneInfo
from .artifacts import atomic_json
from .capabilities import CAPABILITIES
from .locking import writer_lock

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'.kemai-artifacts'/'legacy-coverage.json'


def paths_for(stream):
    return sorted(set(ROOT.glob(f'kemai*{stream}*.log'))|set((ROOT/'kemai_collection_logs').rglob(f'commerce:{stream}*.log')))


def extract(stream,paths):
    complete={};exports={};fingerprints=[]
    prefix=re.escape(stream)
    finish=re.compile(rf'{prefix} (\d{{4}}-\d{{2}}-\d{{2}}) pages=\d+ saved=(\d+)')
    export=re.compile(rf'{prefix} (\d{{4}}-\d{{2}}-\d{{2}}) verified export_total=(\d+)')
    empty=re.compile(rf'{prefix} (\d{{4}}-\d{{2}}-\d{{2}})\.\.(\d{{4}}-\d{{2}}-\d{{2}}) empty month')
    for path in paths:
        content=path.read_bytes();fingerprints.append({'file':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(content).hexdigest()})
        for line in content.decode(errors='replace').splitlines():
            for pattern,target in ((finish,complete),(export,exports)):
                match=pattern.search(line)
                if match:target.setdefault(match[1],set()).add(int(match[2]))
            match=empty.search(line)
            if match:
                left,right=map(date.fromisoformat,match.groups())
                if not 0<=(right-left).days<32:raise ValueError('Invalid historical empty range')
                while left<=right:complete.setdefault(left.isoformat(),set()).add(0);left+=timedelta(days=1)
    return complete,exports,fingerprints


def verify_legacy(client,stream,*,through=None,cache=CACHE):
    cap=CAPABILITIES[stream]
    if stream=='purchase-receive-history' or cap.granularity!='day':raise ValueError('Requires separate range/export evidence import')
    through=through or (datetime.now(ZoneInfo('Asia/Shanghai')).date()-timedelta(days=1))
    complete,exports,files=extract(stream,paths_for(stream))
    eligible={day:next(iter(values)) for day,values in complete.items() if len(values)==1 and cap.configured_from<=day<=through.isoformat()}
    days={};issues=[]
    kind={'purchase-orders':'order','purchase-receipts':'receipt','purchase-returns':'return'}.get(stream)
    def count(left,right):
        where={cap.date_field:{'$gte':left,'$lt':(date.fromisoformat(right)+timedelta(days=1)).isoformat()}}
        if kind:where['document_kind']={'$eq':kind}
        return client.count(cap.table,where=where)
    with writer_lock():
        keys=sorted(eligible);i=0
        while i<len(keys):
            day=keys[i];expected=eligible[day];end=i
            # One DB count proves a contiguous source-confirmed zero range remains empty.
            if expected==0:
                while end+1<len(keys) and eligible[keys[end+1]]==0 and date.fromisoformat(keys[end+1])==date.fromisoformat(keys[end])+timedelta(days=1):end+=1
            actual=count(day,keys[end])
            for key in keys[i:end+1]:
                wanted=eligible[key]
                if actual!=wanted or (exports.get(key) and exports[key]!={wanted}):
                    issues.append({'day':key,'reason':'count_or_export_mismatch'})
                else:days[key]={'rows':wanted,'verification_method':'legacy_export_and_db' if exports.get(key) else 'legacy_single_scan_and_db'}
            i=end+1
        for day,values in complete.items():
            if cap.configured_from<=day<=through.isoformat() and len(values)>1:issues.append({'day':day,'reason':'conflicting_source_counts'})
        import json
        output=json.loads(cache.read_text()) if cache.exists() else {'schema_version':1,'tenant_id':client.config['tenant_id'],'streams':{}}
        if output['tenant_id']!=client.config['tenant_id']:raise ValueError('Legacy cache tenant mismatch')
        entry={'baseline_from':cap.configured_from,'verified_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
            'window_time_field':cap.date_field,
            'scope':'list_counts','days':days,'issues':issues,'source_files':files,
            'source_requests':0,'db_writes':0,'details_verified':False}
        output['streams'][stream]=entry;atomic_json(cache,output)
    return {'stream':stream,'verified_days':len(days),'issues':issues,'cache':str(cache),'source_requests':0,'db_writes':0}


def cached_days(client,stream,cache=CACHE):
    import json
    if not cache.exists():return set()
    value=json.loads(cache.read_text())
    if value.get('tenant_id')!=client.config['tenant_id']:raise ValueError('Legacy cache tenant mismatch')
    entry=value.get('streams',{}).get(stream,{})
    if entry.get('window_time_field')!=CAPABILITIES[stream].date_field:return set()
    return {date.fromisoformat(d) for d in entry.get('days',{})}


def adopt_legacy(client,stream,cache=CACHE):
    import json
    from .contracts import digest
    value=json.loads(cache.read_text())
    if value.get('tenant_id')!=client.config['tenant_id']:raise ValueError('Legacy cache tenant mismatch')
    entry=value['streams'][stream]
    if entry.get('window_time_field')!=CAPABILITIES[stream].date_field:raise ValueError('Reverify legacy evidence under the current source time field')
    days=[{'day':day,**facts} for day,facts in sorted(entry['days'].items())]
    results=[]
    with writer_lock():
        for offset in range(0,len(days),20):
            result=client.gateway('coverage.import',stream=stream,days=days[offset:offset+20],evidence_hash=digest(entry))
            results.extend(result['days'])
    return {'stream':stream,'days':results,'source_requests':0,'source_freshness_advanced':False}
