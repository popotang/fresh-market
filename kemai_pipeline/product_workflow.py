"""Product objects: OpenCLI capture -> durable evidence -> standard model APIs.

No legacy SQL import. An object capture is not a global completeness checkpoint.
Interrupted offset list scans must restart; sealed ID inventories resume by ID.
"""
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .artifacts import atomic_json
from .contracts import digest
from .locking import writer_lock
from .lovrabet_adapter import ContractError
from .normalize import timestamp, scaled
from .source import SourceError, SourceCooling, SourceAuthenticationRequired
from .sync_mode import db_budget, estimate_db_reads

SECTIONS = ('supply','retail_price','delivery_price','stock','promotion','barcode',
            'grading','pack','history_price','scope','combination','mix','split','cut')
DEFAULT_SECTIONS = ('barcode','supply','retail_price','delivery_price','stock','scope')
SHANGHAI = ZoneInfo('Asia/Shanghai')


def now():
    return datetime.now(SHANGHAI).strftime('%Y-%m-%d %H:%M:%S')


def dump(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def item_id(value):
    if isinstance(value,bool) or not re.fullmatch(r'\d{1,32}',str(value)):
        raise ValueError('Invalid product source ID')
    return str(value)


def product_row(entity, observed_at, kind='detail'):
    if not isinstance(entity,dict):raise SourceError('Product entity is not an object')
    source_id=item_id(entity.get('id'))
    if not entity.get('code') or not entity.get('name'):raise SourceError('Product lacks code/name')
    row={'source_item_id':source_id,'item_code':str(entity['code']),
         'item_name':str(entity['name']),'last_seen_at':observed_at,kind+'_hash':digest(entity)}
    names={'barcode':'barcode','sub_name':'subName','item_size':'size','source_category_id':'itemClsId',
           'source_brand_id':'itemBrandId','source_department_id':'itemDepartmentId',
           'source_supplier_id':'supplierId','source_unit_id':'unitId','status_code':'status',
           'lifetime_code':'lifetime','source_version':'version'}
    # Missing/null optional fields preserve prior values, matching the old importer.
    for target,key in names.items():
        if entity.get(key) is not None:row[target]=str(entity[key])
    for target,key in (('retail_price','retailPrice'),('purchase_price','purchasePrice')):
        if entity.get(key) is not None:row[target]=scaled(entity,key)
    for target,key in (('source_created_at','gmtCreate'),('source_modified_at','gmtModified'),('source_approved_at','approveTime')):
        if entity.get(key) not in (None,''):row[target]=timestamp(entity,key)
    return row


def product_create_row(entity, observed_at, kind='detail'):
    row = product_row(entity, observed_at, kind=kind)
    row['first_seen_at'] = observed_at
    return row


def section_rows(section,data):
    if isinstance(data,list):rows=data
    elif isinstance(data,dict):
        key='details' if section in ('combination','split','cut') else 'results'
        rows=data.get(key)
    else:rows=None
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
        raise SourceError('Section rows missing or invalid; not an empty success')
    return rows


def natural_key(row):
    # ID plus business dimensions prevents merging branch/supplier/unit records.
    fields=('id','branchId','supplierId','barcode','unitId','unitName')
    values={k:str(row[k]) for k in fields if row.get(k) not in (None,'')}
    return digest(values) if values else 'payload:'+digest(row)


def child_rows(product_id, section, rows, before, observed_at):
    """Reuse legacy keys by identity, independent of list position; no deletion."""
    existing={}
    for old in before:
        if str(old.get('product_id'))!=str(product_id) or old.get('section')!=section:
            raise ContractError('Product child query returned another scope')
        payload=old['payload']
        if isinstance(payload,str):payload=json.loads(payload)
        key=natural_key(payload)
        if key in existing:raise ContractError('Ambiguous legacy child identity; reconcile explicitly')
        existing[key]=old
    keys=[natural_key(row) for row in rows]
    if len(set(keys))!=len(keys):raise SourceError('Ambiguous source child identities')
    desired=[]
    for row,key in zip(rows,keys):
        source_key=existing[key]['source_key'] if key in existing else 'v2:'+key
        desired.append({'product_id':product_id,'section':section,'source_key':source_key,
                        'payload':dump(row),'payload_hash':digest(row),'captured_at':observed_at})
    extras=len(set(existing)-set(keys))
    return desired,extras


class ProductWorkflow:
    def __init__(self, adapter, source, root):
        self.db=adapter;self.source=source;self.root=Path(root)
        self.tenant=str(adapter.config['tenant_id'])

    def path(self,capture):
        if not re.fullmatch('[0-9a-f]{32}',capture):raise ValueError('Invalid product capture ID')
        return self.root/capture

    def save(self,state):
        atomic_json(self.path(state['capture_id'])/'manifest.json',state)

    def load(self,capture):
        state=json.loads((self.path(capture)/'manifest.json').read_text())
        if state.get('tenant')!=self.tenant or state.get('capture_id')!=capture or state.get('schema_version')!=1:
            raise ContractError('Product capture scope mismatch')
        return state

    def _detail_versions(self, product_ids):
        """Detail-verified versions read from committed profiles. The master row's
        source_version is last-observed (list refreshes rewrite it) and can never
        serve as the detail-debt baseline. Missing profile/version yields None,
        which the caller treats as unknown debt, not as complete."""
        versions={}
        for offset in range(0,len(product_ids),100):
            chunk=product_ids[offset:offset+100]
            profiles=self.db.read_all('product_profile',where={'product_id':{'$in':chunk}},
                                      select=['product_id','payload']) if chunk else []
            for profile in profiles:
                payload=profile.get('payload')
                if isinstance(payload,str):
                    try:payload=json.loads(payload)
                    except ValueError:payload=None
                version=payload.get('version') if isinstance(payload,dict) else None
                key=str(profile['product_id'])
                if key in versions:raise ContractError('Duplicate product profile')
                versions[key]=str(version) if version is not None else None
        return versions

    def plan(self, ids=None, *, after_id=0, limit=10, sections=DEFAULT_SECTIONS, inventory=None,
             changed_only=True, list_only=False, sync_mode=None, max_db_requests=400,
             tab_demands=()):
        if not 1<=limit<=100:raise ValueError('Plan limit must be 1..100')
        sections=list(dict.fromkeys(sections))
        if any(s not in SECTIONS for s in sections):raise ValueError('Unknown product section')
        if tab_demands and inventory:
            # Inventory-scoped tab demand cannot be proven complete: unchanged
            # masters with no version signal (detail_unknown) are only one leak
            # class, and a fully verified master with missing tabs leaks too.
            # Until tab-level TTL/coverage selection lands (unit 3), only an
            # explicit object selection may schedule tab detail. Fail before
            # any capture manifest is persisted, so `run` cannot bypass the gate.
            raise ValueError('inventory-scoped tab demand is planning-only until unit 3 '
                             'tab-level TTL selection; select objects explicitly via --id')
        if tab_demands and ids is None and not inventory:
            raise ValueError('cursor-scoped tab demand is planning-only; select objects explicitly via --id')
        if not 1<=limit<=100:raise ValueError('Plan limit must be 1..100')
        sections=list(dict.fromkeys(sections))
        if any(s not in SECTIONS for s in sections):raise ValueError('Unknown product section')
        if sync_mode is not None:
            if not isinstance(sync_mode,dict) or 'sync_mode' not in sync_mode:
                raise ValueError('sync_mode must be a resolved mode contract')
            if list_only and sync_mode.get('detail_demand')=='bounded':
                raise ValueError('list_only plan cannot carry a bounded detail demand')
        if list_only and not inventory:
            # A list-only capture can only materialize list evidence rows; without
            # a sealed inventory the run would fail on the first object.
            raise ValueError('list-only plan requires a sealed inventory with list evidence')
        catalog=None;rows=[];list_evidence={};detail_needed=[];detail_unknown=[]
        if inventory:
            catalog=self.load(inventory)
            if catalog.get('phase')!='inventory_complete':raise ContractError('Inventory is not sealed')
            ids=catalog['ids']
            for page in catalog['pages']:
                if Path(page['file']).name!=page['file']:raise ContractError('Invalid inventory artifact path')
                envelope=json.loads((self.path(inventory)/page['file']).read_text())
                if digest(envelope)!=page['hash']:raise ContractError('Inventory artifact hash mismatch')
                for row in envelope['data']['results']:
                    key=item_id(row.get('id'))
                    if key in list_evidence:raise ContractError('Duplicate inventory item')
                    list_evidence[key]={'hash':digest(row),'version':str(row['version']) if row.get('version') is not None else None,
                                        'row':row,'observed_at':envelope['observed_at']}
            if set(list_evidence)!=set(ids) or len(ids)!=catalog['source_total']:
                raise ContractError('Inventory ID/count mismatch')
            # Fail closed on the plan-phase DB budget before any data-center read:
            # the selection pass reads one products page per 100 items and at most
            # one profile page per 100 items when list evidence carries versions.
            if changed_only:
                verdict=db_budget(estimate_db_reads(len(ids)),max_db_requests)
                if verdict['over_budget']:
                    raise ValueError('Plan DB budget exceeded: estimated %d > cap %d; %s'
                                     %(verdict['estimated_db_requests'],verdict['db_request_budget'],
                                       verdict['strategy']))
                plan_db_reads=verdict['estimated_db_requests']
            else:plan_db_reads=0
            if changed_only:
                selected=[]
                for offset in range(0,len(ids),100):
                    chunk=ids[offset:offset+100]
                    existing=self.db.read_all('products',where={'source_item_id':{'$in':chunk}},
                                              select=['id','source_item_id','list_hash','detail_hash'])
                    by_id={str(row['source_item_id']):row for row in existing}
                    if len(by_id)!=len(existing):raise ContractError('Duplicate product source IDs')
                    # Version comparison only applies where the list evidence carries a
                    # version; real inventories often omit it entirely, so profiles are
                    # read only for rows that can actually be compared.
                    compare={key for key in chunk if by_id.get(key,{}).get('detail_hash')
                             and list_evidence[key]['version'] is not None}
                    verified_versions=self._detail_versions([by_id[key]['id'] for key in compare]) if compare else {}
                    for key in chunk:
                        prior=by_id.get(key);evidence=list_evidence[key]
                        list_changed=prior is None or prior.get('list_hash')!=evidence['hash']
                        # List diff drives the list queue: detail debt (missing detail_hash
                        # or a source version newer than the committed profile) is tracked
                        # separately and must not re-enqueue an unchanged master. Comparing
                        # against the master row would lose the debt as soon as a list
                        # refresh rewrites its version (Codex comment-2 repro).
                        stale_detail=(prior is None or not prior.get('detail_hash')
                                      or (key in compare
                                          and verified_versions.get(str(prior['id']))!=evidence['version']))
                        if stale_detail:detail_needed.append(key)
                        # Without a list version, freshness of an existing profile cannot
                        # be judged: record it as unknown (visible to on-demand detail
                        # selectors) instead of silently treating detail as complete.
                        if prior is not None and not stale_detail and evidence['version'] is None:
                            detail_unknown.append(key)
                        if list_changed or (stale_detail and not list_only):selected.append(key)
                ids=selected
        elif ids is None:
            plan_db_reads=1  # after_id cursor page only
            rows,_=self.db.page('products',where={'id':{'$gt':after_id}},
                                select=['source_item_id'],size=limit)
            ids=[r['source_item_id'] for r in rows]
        else:plan_db_reads=0  # explicit ids: no plan-phase DB read
        ids=[item_id(i) for i in ids]
        if len(ids)!=len(set(ids)) or (not inventory and len(ids)>100):raise ValueError('Duplicate or oversized ID plan')
        state={'schema_version':1,'capture_id':uuid.uuid4().hex,'tenant':self.tenant,
               'phase':'planned','created_at':now(),'sections':sections,'inventory':inventory,
               'ids':ids,'objects':{},'run_id':uuid.uuid4().int % (2**52),
               'list_evidence':{key:list_evidence[key] for key in ids} if inventory else {},
               'selection':'new_or_changed_master' if inventory and changed_only else 'explicit_objects',
               'list_only':bool(list_only),
               'detail_needed':detail_needed,
               'detail_unknown':detail_unknown,
               'sync_mode':dict(sync_mode) if sync_mode else {'sync_mode':'list' if list_only else 'on_demand',
                                                              'mode_basis':'implicit'},
               'estimated_db_requests':plan_db_reads,
               'db_request_budget':max_db_requests if inventory and changed_only else None,
               'next_after_id':rows[-1]['id'] if not inventory and rows else None,
               'coverage':'selected_objects_only','events':{}}
        self.save(state);return self.summary(state)

    def read(self,kind,source_id=None,section=None,page=1,from_time=None,sort=None):
        command={'list':'product-list','detail':'product-detail','section':'product-section'}[kind]
        args=['--page',str(page),'--rows','100'] if kind=='list' else ['--id',source_id]
        if kind=='list' and sort is not None:args=['--sort',sort]+args
        if kind=='list' and from_time is not None:args=['--from',str(from_time)]+args
        if section:args+=['--section',section,'--page',str(page)]
        rows=self.source.call(command,*args)
        if len(rows)!=1 or not isinstance(rows[0],dict):raise SourceError('Missing product envelope')
        e=rows[0]
        if e.get('schema_version')!=1 or e.get('kind')!=kind or e.get('page')!=page:
            raise SourceError('Product envelope contract mismatch')
        if source_id and str(e.get('object_key'))!=source_id:raise SourceError('Product identity mismatch')
        if section and e.get('section')!=section:raise SourceError('Product section mismatch')
        if not e.get('observed_at'):raise SourceError('Source observation time missing')
        if kind=='detail' and str(e.get('data',{}).get('entity',{}).get('id'))!=source_id:
            raise SourceError('Product detail identity mismatch')
        return e

    def inventory(self, max_pages=200, from_time=None, sort=None):
        """Capture IDs once. No unsafe continuation of unstable page offsets."""
        if not 1<=max_pages<=1000:raise ValueError('Inventory page budget 1..1000')
        if sort is not None and sort not in ('gmtCreate','approveTime'):
            raise ValueError('Product inventory sort must be gmtCreate or approveTime')
        if from_time is not None and not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?',str(from_time)):
            raise ValueError('Product inventory from_time must be YYYY-MM-DD or YYYY-MM-DD HH:mm:ss')
        requested_sort=sort
        sort=requested_sort or 'gmtCreate'
        start=(str(from_time)+' 00:00:00' if len(str(from_time))==10 else
               str(from_time)+':00' if len(str(from_time))==16 else str(from_time)) if from_time else None
        scope=({} if not start and requested_sort is None else {
            'params':({('startGmtCreate' if sort=='gmtCreate' else 'startApproveTime'):start} if start else {}),
            'orders':[{'fieldName':sort,'order':'desc','frontendSort':False}]})
        if not start and requested_sort is None:scope={}
        with writer_lock():
            state={'schema_version':1,'capture_id':uuid.uuid4().hex,'tenant':self.tenant,
                   'phase':'inventory_capturing','created_at':now(),'ids':[],'pages':[],
                   'query_scope':scope}
            self.save(state);total=None;seen=set()
            try:
                for page in range(1,max_pages+1):
                    e=self.read('list',page=page,from_time=from_time,sort=sort)
                    if e.get('query_scope')!=scope:
                        raise SourceError('Product list scope does not match requested window')
                    data=e.get('data',{});rows=data.get('results');observed=data.get('totalRecord')
                    if isinstance(observed,str) and observed.isdigit():observed=int(observed)
                    if not isinstance(rows,list) or type(observed) is not int or observed<0:
                        raise SourceError('Product list total/rows invalid')
                    if total is not None and total!=observed:raise SourceError('Product list total changed; start a new inventory')
                    total=observed
                    if len(rows)!=min(100,max(0,total-(page-1)*100)):raise SourceError('Truncated product list page')
                    ids=[item_id(r.get('id')) for r in rows]
                    if len(set(ids))!=len(ids) or seen.intersection(ids):raise SourceError('Duplicate product IDs across pages')
                    seen.update(ids);state['ids'].extend(ids)
                    name=f'list-{page:06}.json';atomic_json(self.path(state['capture_id'])/name,e)
                    state['pages'].append({'file':name,'hash':digest(e)});self.save(state)
                    if len(seen)==total:
                        state.update(phase='inventory_complete',source_total=total,completed_at=now(),
                                     coverage='filtered_time_window_single_pass' if start or requested_sort
                                     else 'accessible_api_default_list_single_pass')
                        self.save(state);return self.summary(state)
                raise SourceError('Product inventory page budget exhausted')
            except Exception as error:
                state.update(phase='inventory_interrupted',error=type(error).__name__)
                self.save(state);raise

    def snapshot(self,state,source_id,section):
        path=self.path(state['capture_id'])/source_id/(section+'.json')
        if path.exists():
            saved=json.loads(path.read_text())
            if digest(saved['envelopes'])!=saved['hash']:raise ContractError('Product snapshot hash mismatch')
        else:
            envelopes=[];expected=None
            for page in range(1,101):
                e=self.read('detail' if section=='detail' else 'section',source_id,
                            None if section=='detail' else section,page)
                envelopes.append(e)
                if section!='history_price':break
                data=e['data'];rows=section_rows(section,data);total=data.get('totalRecord')
                if isinstance(total,str) and total.isdigit():total=int(total)
                if type(total) is not int or total<0 or (expected is not None and total!=expected):
                    raise SourceError('Invalid/changing price history total')
                expected=total
                if len(rows)!=min(10,max(0,total-(page-1)*10)):raise SourceError('Truncated price history')
                if page*10>=total:break
            else:raise SourceError('Price history page budget exhausted; snapshot not complete')
            saved={'envelopes':envelopes,'hash':digest(envelopes)}
            atomic_json(path,saved)
        for e in saved['envelopes']:
            if str(e.get('object_key'))!=source_id or (section!='detail' and e.get('section')!=section):
                raise ContractError('Saved product snapshot identity mismatch')
        return saved['envelopes']

    def write_section(self, product_id, section, rows, observed_at):
        where={'product_id':{'$eq':product_id},'section':{'$eq':section}}
        before=self.db.read_all('product_child_records',where=where)
        if any(timestamp(r,'captured_at')>observed_at for r in before if r.get('captured_at')):
            raise ContractError('Newer product section exists; start a fresh object plan')
        desired,extras=child_rows(product_id,section,rows,before,observed_at)
        old={r['source_key']:r for r in before};new=[];changes=[]
        for row in desired:
            previous=old.get(row['source_key'])
            if previous is None:new.append(row)
            else:
                patch={k:v for k,v in row.items() if not self.db._same(previous.get(k),v)}
                if patch:changes.append({'id':previous['id'],**patch})
        # Bound both row count and bytes, including large JSON cells.
        chunks=[];chunk=[]
        for row in new:
            if len(dump([row]).encode())>48000:raise ContractError('Product child exceeds batch byte budget')
            if chunk and (len(chunk)>=100 or len(dump(chunk+[row]).encode())>48000):chunks.append(chunk);chunk=[]
            chunk.append(row)
        if chunk:chunks.append(chunk)
        for chunk in chunks:
            self.db.batch_create('product_child_records',chunk,
                unique_keys=['product_id','section','source_key'],preflight=False)
        failure=None
        try:
            for patch in changes:
                self.db.invoke('data','update','--code',self.db.dataset_code('product_child_records'),
                               '--params',dump(patch))
        except ContractError as error:failure=error
        actual=self.db.read_all('product_child_records',where=where)
        by_key={r['source_key']:r for r in actual}
        if len(by_key)!=len(actual) or any(not all(self.db._same(by_key.get(r['source_key'],{}).get(k),v)
                                                  for k,v in r.items()) for r in desired):
            raise ContractError('PRODUCT_CHILD_OUTCOME_UNKNOWN; resume readback before further writes') from failure
        if {r['source_key'] for r in actual}!={*old,*[r['source_key'] for r in new]}:
            raise ContractError('Product child key set changed during write')
        return {'rows':len(desired),'retained_stale_rows':extras,'status':'partial' if extras else 'verified'}

    def run_list_only(self,state,limit,started,source_before,db_before):
        """One bounded batch per call: pre-read, grouped writes, whole-batch readback,
        then persist this batch's verified object set before any reporting write."""
        objects=state.setdefault('objects',{})
        pending=[i for i in state['ids'] if objects.get(i,{}).get('list',{}).get('status') not in ('verified','partial')]
        batch=pending[:limit]
        content_changes=observation_updates=unchanged=0
        if batch:
            desired=[];seen=set()
            for source_id in batch:
                listed=state.get('list_evidence',{}).get(source_id)
                if not listed or not isinstance(listed.get('row'),dict):
                    raise ContractError('List-only plan lacks source list row evidence')
                observed=timestamp({'value':listed['observed_at']},'value')
                desired.append((source_id,observed,product_row(listed['row'],observed,kind='list')))
                seen.add(observed)
            before=self.db.read_all('products',where={'source_item_id':{'$in':batch}})
            existing={}
            for old in before:
                key=str(old['source_item_id'])
                if key in existing:raise ContractError('Duplicate product source ID in database')
                existing[key]=old
            creates=[];changes=[]
            for source_id,observed,row in desired:
                prior=existing.get(source_id)
                if prior and any(timestamp(r,'last_seen_at')>observed for r in [prior] if r.get('last_seen_at')):
                    raise ContractError('Newer product exists; start a fresh object plan')
                if prior is None:
                    row['first_seen_at']=observed
                    creates.append(row);content_changes+=1
                else:
                    # An unknown write outcome is resolved by unique key and field readback:
                    # a row already matching this plan confirms a prior partially-applied
                    # batch and is never replayed through batchCreate.
                    patch={k:v for k,v in row.items() if not self.db._same(prior.get(k),v)}
                    if not patch:unchanged+=1;continue
                    if set(patch)<={'last_seen_at'}:observation_updates+=1
                    else:content_changes+=1
                    changes.append({'id':prior['id'],**patch})
            chunks=[];chunk=[]
            for row in creates:
                if len(dump([row]).encode())>48000:raise ContractError('Product exceeds batch byte budget')
                if chunk and (len(chunk)>=100 or len(dump(chunk+[row]).encode())>48000):chunks.append(chunk);chunk=[]
                chunk.append(row)
            if chunk:chunks.append(chunk)
            for chunk in chunks:
                self.db.batch_create('products',chunk,unique_keys=['source_item_id'],preflight=False)
            groups={}
            for patch in changes:groups.setdefault(dump({k:v for k,v in patch.items() if k!='id'}),[]).append(patch)
            for _,patches in groups.items():
                ids_to_update=[p['id'] for p in patches]
                patch={k:v for k,v in patches[0].items() if k!='id'}
                self.db.invoke('data','update','--code',self.db.dataset_code('products'),
                               '--params',dump({'id':ids_to_update,**patch}))
            actual=self.db.read_all('products',where={'source_item_id':{'$in':batch}})
            by_id={str(row['source_item_id']):row for row in actual}
            if len(by_id)!=len(actual):raise ContractError('Duplicate product source ID in readback')
            written=time.monotonic()
            for source_id,observed,row in desired:
                saved=by_id.get(source_id)
                if saved is None or any(not self.db._same(saved.get(k),v) for k,v in row.items()):
                    raise ContractError('UPSERT_OUTCOME_UNKNOWN: product list batch readback mismatch')
                objects.setdefault(source_id,{})['list']={'status':'verified','rows':1,
                    'source_observed_at':observed,'source_seconds':0,'db_seconds':round(written-started,3)}
            if desired:state['source_observed_at']=max(seen)
            for key in ('error','error_reason','failed_section'):state.pop(key,None)
            state.update(phase='running',last_run_content_changes=content_changes,
                         last_run_observation_updates=observation_updates,last_run_unchanged=unchanged)
            # Durable business completion of this batch precedes any reporting write;
            # a failed audit is retried on resume through pending_audits.
            if 'list' not in state.setdefault('pending_audits',[]):state['pending_audits'].append('list')
            self.save(state)
        remaining=len(pending)-len(batch)
        for section in list(state.get('pending_audits',[])):
            self.audit(state,section,'partial' if remaining else 'success')
            state['pending_audits'].remove(section);self.save(state)
        state.update(last_run_seconds=round(time.monotonic()-started,3),
                     last_run_source_operations=getattr(self.source,'operations',0)-source_before,
                     last_run_db_requests=self.db.requests-db_before)
        # An empty selection or a fully verified object list closes the capture; a
        # bounded batch with work left keeps the same object list resumable.
        state['phase']='complete' if not remaining else 'running'
        self.save(state)

    def audit(self,state,section,status,error=None):
        # One stable event per capture/section; retries update it rather than append duplicates.
        stream='product-object:'+section
        states=[v.get(section,{}) for v in state['objects'].values()]
        if status=='success' and any(v.get('status')=='partial' for v in states):status='partial'
        verified=sum(v.get('status')=='verified' for v in states)
        finished=now();coverage='partial'
        row={'tenant_id':self.tenant,'stream':stream,'mode':'object', 'run_id':state['run_id'],
             'status':status,'source_rows':len(state['ids']),'saved_rows':verified,
             'verification_method':'object_field_readback','coverage_level':coverage,
             'coverage_note':'Selected product objects only; not global coverage or source realtime. Typed derivative tables not refreshed.',
             'started_at':state['created_at'],'finished_at':finished,
             'error_message':type(error).__name__ if error else None,
             'details':dump({'capture_id':state['capture_id'],'section':section,'selected':len(state['ids']),
                             'verified':verified,'source_observed_at':state.get('source_observed_at')})}
        result=self.db.upsert('sync_stream_events',row,unique_keys=['tenant_id','stream','run_id'])
        latest={'tenant_id':self.tenant,'stream':stream,'stream_name':'商品对象 '+section,
                # updated_at is server-managed; last_attempt_at is the observed
                # attempt time. Comparing a client value to the former causes
                # false unknown outcomes when the DB advances it during update.
                'last_attempt_status':status,'last_attempt_at':finished,
                'last_source_rows':len(state['ids']),'last_saved_rows':verified,
                'verification_method':'object_field_readback','coverage_level':coverage,
                'coverage_note':row['coverage_note'],'last_error':row['error_message'],
                'last_event_id':result['row']['id']}
        if status=='success':latest['last_success_at']=finished
        self.db.upsert('sync_stream_status',latest,unique_keys=['tenant_id','stream'])

    def run(self,capture,limit=5,list_only=None):
        if not 1<=limit<=100:raise ValueError('Run limit must be 1..100')
        with writer_lock():
            state=self.load(capture)
            if state['phase'].startswith('inventory'):raise ValueError('Create an object plan from inventory first')
            list_only=state.get('list_only',False) if list_only is None else bool(list_only)
            if list_only and state.get('phase')=='complete':
                # A finished capture replays its original acceptance result before any
                # standard API request: no event/status rewrite, no last_success_at refresh.
                return self.summary(state)
            started=time.monotonic();done=0
            source_before=getattr(self.source,'operations',0);db_before=self.db.requests
            if list_only:
                try:
                    self.run_list_only(state,limit,started,source_before,db_before)
                    return self.summary(state)
                except Exception as error:
                    state.update(phase='cooling' if isinstance(error,SourceCooling) else
                                 'auth_required' if isinstance(error,SourceAuthenticationRequired) else 'failed',
                                 error=type(error).__name__,failed_section='list',
                                 error_reason=str(error)[:300] if isinstance(error,(SourceError,ContractError)) else type(error).__name__)
                    self.save(state)
                    try:self.audit(state,'list',state['phase'],error)
                    except Exception:pass
                    raise
            for source_id in state['ids']:
                phases=['list'] if list_only else ['detail',*state['sections']]
                obj=state['objects'].setdefault(source_id,{})
                if all(obj.get(p,{}).get('status') in ('verified','partial') for p in phases):continue
                if done>=limit:break
                for section in phases:
                    if obj.get(section,{}).get('status') in ('verified','partial'):continue
                    step=time.monotonic()
                    try:
                        if section=='list':
                            listed=state.get('list_evidence',{}).get(source_id)
                            if not listed or not isinstance(listed.get('row'),dict):
                                raise ContractError('List-only plan lacks source list row evidence')
                            entity=listed['row']
                            observed=timestamp({'value':listed['observed_at']},'value')
                            source_seconds=0
                        else:
                            envelopes=self.snapshot(state,source_id,section)
                            source_seconds=time.monotonic()-step
                            observed=timestamp({'value':envelopes[-1]['observed_at']},'value')
                            entity=envelopes[0]['data']['entity'] if section=='detail' else None
                        write_start=time.monotonic()
                        state['source_observed_at']=observed
                        if section=='list':
                            prior,_=self.db.page('products',where={'source_item_id':{'$eq':source_id}},
                                                 select=['last_seen_at'],size=2)
                            row=(product_create_row(entity,observed,kind='list') if not prior
                                 else product_row(entity,observed))
                            if any(timestamp(r,'last_seen_at')>observed for r in prior if r.get('last_seen_at')):
                                raise ContractError('Newer product exists; start a fresh object plan')
                            result=self.db.upsert('products',row,unique_keys=['source_item_id'])
                            obj['product_id']=result['row']['id']
                            outcome={'status':'verified','rows':1}
                        elif section=='detail':
                            entity=envelopes[0]['data']['entity']
                            prior,_=self.db.page('products',where={'source_item_id':{'$eq':source_id}},
                                                 select=['last_seen_at'],size=2)
                            row=(product_create_row(entity,observed) if not prior
                                 else product_row(entity,observed))
                            if any(timestamp(r,'last_seen_at')>observed for r in prior if r.get('last_seen_at')):
                                raise ContractError('Newer product exists; start a fresh object plan')
                            result=self.db.upsert('products',row,unique_keys=['source_item_id'])
                            product_id=result['row']['id'];obj['product_id']=product_id
                            self.db.upsert('product_profile',{'product_id':product_id,'payload':dump(entity),
                                'payload_hash':digest(entity)},unique_keys=['product_id'])
                            outcome={'status':'verified','rows':1}
                            listed=state.get('list_evidence',{}).get(source_id)
                            if listed:
                                # Publish the list comparison hash only after the detail/profile
                                # commit. Drift or unknown versions must remain eligible next scan.
                                if listed['version'] is not None and listed['version']==str(entity.get('version')):
                                    self.db.upsert('products',{'source_item_id':source_id,'list_hash':listed['hash']},
                                                   unique_keys=['source_item_id'])
                                else:outcome['list_version_drift']=True
                        else:
                            rows=[r for e in envelopes for r in section_rows(section,e['data'])]
                            outcome=self.write_section(obj['product_id'],section,rows,observed)
                        obj[section]={**outcome,'source_observed_at':observed,
                            'source_seconds':round(source_seconds,3),'db_seconds':round(time.monotonic()-write_start,3)}
                        # Save business completion before audit; pending audit is retried on resume.
                        state['phase']='running'
                        for key in ('error','error_reason','failed_section'):state.pop(key,None)
                        if section not in state.setdefault('pending_audits',[]):state['pending_audits'].append(section)
                        self.save(state)
                        self.audit(state,section,'partial' if outcome['status']=='partial' else 'success')
                        state['pending_audits'].remove(section);self.save(state)
                    except Exception as error:
                        state.update(phase='cooling' if isinstance(error,SourceCooling) else
                                     'auth_required' if isinstance(error,SourceAuthenticationRequired) else 'failed',error=type(error).__name__,
                                     failed_section=section,
                                     error_reason=str(error)[:300] if isinstance(error,(SourceError,ContractError)) else type(error).__name__)
                        self.save(state)
                        try:self.audit(state,section,state['phase'],error)
                        except Exception:pass  # local durable failure remains authoritative if DB is unavailable
                        raise
                done+=1
            for section in list(state.get('pending_audits',[])):
                self.audit(state,section,'partial' if any(v.get(section,{}).get('status')=='partial' for v in state['objects'].values()) else 'success')
                state['pending_audits'].remove(section);self.save(state)
            remaining=sum(not all(state['objects'].get(i,{}).get(s,{}).get('status') in ('verified','partial')
                                  for s in (['list'] if list_only else ['detail',*state['sections']])) for i in state['ids'])
            state.update(phase='partial' if not remaining and any(v.get(s,{}).get('status')=='partial'
                         for v in state['objects'].values() for s in state['sections']) else 'complete' if not remaining else 'running',
                         last_run_seconds=round(time.monotonic()-started,3))
            state['last_run_source_operations']=getattr(self.source,'operations',0)-source_before
            state['last_run_db_requests']=self.db.requests-db_before
            self.save(state);return self.summary(state)

    @staticmethod
    def summary(state):
        objects=state.get('objects',{});sections=['list'] if state.get('list_only') else ['detail',*state.get('sections',[])]
        complete=sum(all(v.get(s,{}).get('status')=='verified' for s in sections) for v in objects.values())
        terminal=sum(all(v.get(s,{}).get('status') in ('verified','partial') for s in sections) for v in objects.values())
        mode=state.get('sync_mode') or {}
        return {k:state.get(k) for k in ('capture_id','phase','coverage','selection','next_after_id','last_run_seconds',
                                        'last_run_source_operations','last_run_db_requests',
                                        'last_run_content_changes','last_run_observation_updates','last_run_unchanged',
                                        'estimated_db_requests','db_request_budget',
                                        'failed_section','error','error_reason')} | {
            'sync_mode':mode.get('sync_mode'),'mode_basis':mode.get('mode_basis'),
            'detail_demand':mode.get('detail_demand'),
            'selected':len(state['ids']),'verified_objects':complete,'remaining_objects':len(state['ids'])-terminal,
            'partial_objects':terminal-complete,'sections':state.get('sections',[]),
            'detail_needed':len(state.get('detail_needed') or []),
            'detail_unknown':len(state.get('detail_unknown') or [])}
