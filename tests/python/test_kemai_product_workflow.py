import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from kemai_pipeline.product_workflow import ProductWorkflow, product_row, child_rows, section_rows
from kemai_pipeline.contracts import digest
from kemai_pipeline.source import SourceError, SourceCooling
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter, ContractError

OBS='2026-11-09T10:00:00Z'
STAMP='2026-11-09 18:00:00'
ENTITY={'id':12,'code':'0012','name':'test','retailPrice':12300,'version':1}

class MemoryDB(LovrabetAdapter):
    def __init__(self):
        config=json.loads(Path('kemai_pipeline/datasets.json').read_text())
        super().__init__(config);self.tables={k:[] for k in config['datasets']};self.writes=[];self.fail_table=None
    def matches(self,row,where):
        for k,v in where.items():
            if k=='$and':
                if not all(self.matches(row,w) for w in v):return False
            elif k=='$or':
                if not any(self.matches(row,w) for w in v):return False
            elif '$eq' in v and str(row.get(k))!=str(v['$eq']):return False
            elif '$in' in v and str(row.get(k)) not in list(map(str,v['$in'])):return False
            elif '$gt' in v and row.get(k,0)<=v['$gt']:return False
        return True
    def invoke(self,*args):
        self.requests+=1
        verb=args[1];code=args[args.index('--code')+1];params=json.loads(args[args.index('--params')+1])
        table=next(k for k,v in self.config['datasets'].items() if v==code);data=self.tables[table]
        if verb=='filter':
            rows=sorted([copy.deepcopy(r) for r in data if self.matches(r,params['where'])],key=lambda r:r[self._pk(table)])
            size=params['pageSize'];page=params['currentPage']
            return {'tableData':rows[(page-1)*size:page*size],
                    'paging':{'currentPage':page,'pageSize':size,'totalCount':len(rows)}}
        self.writes.append((table,verb))
        if self.fail_table==table:raise ContractError('injected transport failure')
        if verb in ('create','batchCreate'):
            for row in params if verb=='batchCreate' else [params]:
                row=dict(row);row.setdefault(self._pk(table),len(data)+1);data.append(row)
        elif verb=='update':
            # Grouped list updates address several rows sharing one patch.
            key=self._pk(table);targets=params[key] if isinstance(params[key],list) else [params[key]]
            for target in targets:
                row=next(r for r in data if r[key]==target)
                row.update({k:v for k,v in params.items() if k!=key})
        else:raise AssertionError(verb)
        return {}

class Source:
    def __init__(self):self.calls=[];self.fail=None;self.sections={'barcode':[{'barcode':'123'}]};self.list_pages={}
    def call(self,command,*args):
        self.calls.append(command);p=dict(zip(args[::2],args[1::2]));section=p.get('--section')
        if self.fail and self.fail in (command,section):raise SourceCooling('injected')
        kind={'product-list':'list','product-detail':'detail','product-section':'section'}[command]
        page=int(p.get('--page',1))
        data=copy.deepcopy({'entity':ENTITY} if kind=='detail' else self.sections.get(section,[]))
        if kind=='list':data=self.list_pages[page]
        return [{'schema_version':1,'kind':kind,'object_key':p.get('--id'),'page':page,'section':section,
                 'query_scope':{},'observed_at':OBS,'data':data}]

class ProductTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=MemoryDB();self.source=Source();self.worker=ProductWorkflow(self.db,self.source,Path(self.tmp.name)/'captures')
        self.lock=patch('kemai_pipeline.locking.RUNTIME_LOCKS',Path(self.tmp.name)/'locks');self.lock.start();self.addCleanup(self.lock.stop)
    def plan(self,**kw):return self.worker.plan(['12'],sections=['barcode'],**kw)['capture_id']
    def test_money_missing_fields(self):
        row=product_row(ENTITY,STAMP);self.assertEqual(row['retail_price'],'1.23');self.assertNotIn('barcode',row)
    def test_plan_has_no_source_or_writes(self):
        self.plan();self.assertEqual(self.source.calls,[]);self.assertEqual(self.db.writes,[])
    def test_complete_and_replay_no_source_or_writes(self):
        cid=self.plan();result=self.worker.run(cid);self.assertEqual(result['phase'],'complete')
        self.assertEqual(len(self.db.tables['products']),1);self.assertEqual(len(self.db.tables['product_profile']),1)
        self.assertEqual(self.db.tables['product_profile'][0]['product_id'],1)
        self.assertNotIn('updated_at',self.db.tables['product_profile'][0])
        calls=len(self.source.calls);writes=len(self.db.writes)
        self.worker.run(cid);self.assertEqual(len(self.source.calls),calls);self.assertEqual(len(self.db.writes),writes)
        self.assertTrue(all(r['coverage_level']=='partial' for r in self.db.tables['sync_stream_status']))
        self.assertTrue(all('updated_at' not in r for r in self.db.tables['sync_stream_status']))
    def test_cooldown_resume_keeps_completed_detail(self):
        cid=self.plan();self.source.fail='barcode'
        with self.assertRaises(SourceCooling):self.worker.run(cid)
        self.assertEqual(self.worker.load(cid)['phase'],'cooling');self.source.fail=None
        self.worker.run(cid);self.assertEqual(self.source.calls.count('product-detail'),1)
        self.assertEqual(len(self.db.tables['products']),1)
    def test_db_failure_reuses_durable_source(self):
        cid=self.plan();self.db.fail_table='product_profile'
        with self.assertRaises(ContractError):self.worker.run(cid)
        self.db.fail_table=None;self.worker.run(cid)
        self.assertEqual(self.source.calls.count('product-detail'),1)
        self.assertEqual(len(self.db.tables['products']),1)
    def test_missing_array_is_failure_not_empty(self):
        for data in ({},{'results':None},None,{'details':[]}):
            with self.assertRaises(SourceError):section_rows('barcode',data)
        self.assertEqual(section_rows('barcode',[]),[])
    def test_reorder_reuses_legacy_keys(self):
        rows=[{'barcode':'a'},{'barcode':'b'}]
        before=[{'product_id':1,'section':'barcode','source_key':str(i),'payload':json.dumps(r)} for i,r in enumerate(rows)]
        desired,extras=child_rows(1,'barcode',rows[::-1],before,STAMP)
        self.assertEqual([r['source_key'] for r in desired],['1','0']);self.assertEqual(extras,0)
    def test_ambiguous_identity_refused(self):
        with self.assertRaises(SourceError):child_rows(1,'barcode',[{'id':1,'x':1},{'id':1,'x':2}],[],STAMP)
    def test_stale_child_retained_and_partial(self):
        cid=self.plan();self.worker.run(cid);self.source.sections['barcode']=[]
        result=self.worker.run(self.plan());self.assertEqual(result['phase'],'partial')
        self.assertEqual(len(self.db.tables['product_child_records']),1)
    def test_new_cycle_refreshes_tabs_even_same_master_version(self):
        self.worker.run(self.plan());self.source.sections['barcode'][0]['price']=2
        self.worker.run(self.plan());self.assertEqual(self.source.calls.count('product-section'),2)
        self.assertEqual(len(self.db.tables['product_child_records']),1)
        self.assertEqual(json.loads(self.db.tables['product_child_records'][0]['payload'])['price'],2)
    def test_snapshot_tamper_refused(self):
        cid=self.plan();self.db.fail_table='products'
        with self.assertRaises(ContractError):self.worker.run(cid)
        p=self.worker.path(cid)/'12/detail.json';d=json.loads(p.read_text());d['hash']='bad';p.write_text(json.dumps(d))
        self.db.fail_table=None
        with self.assertRaisesRegex(ContractError,'hash'):self.worker.run(cid)
    def test_newer_database_refuses_old_snapshot(self):
        cid=self.plan();self.worker.run(cid)
        self.db.tables['products'][0]['last_seen_at']='2026-11-10 10:00:00'
        with self.assertRaisesRegex(ContractError,'Newer product'):self.worker.run(self.plan())
    def test_inventory_single_scan_and_plan(self):
        self.source.list_pages={1:{'results':[ENTITY],'totalRecord':1}}
        inventory=self.worker.inventory();self.assertEqual(inventory['phase'],'inventory_complete')
        plan=self.worker.plan(inventory=inventory['capture_id'],sections=[])
        self.assertEqual(plan['selected'],1);self.assertEqual(self.db.writes,[])
    def test_inventory_truncated_fails_and_is_not_resumable(self):
        self.source.list_pages={1:{'results':[ENTITY],'totalRecord':2}}
        with self.assertRaises(SourceError):self.worker.inventory()
        cid=next(self.worker.root.glob('*/manifest.json')).parent.name
        self.assertEqual(self.worker.load(cid)['phase'],'inventory_interrupted')
        with self.assertRaises(ContractError):self.worker.plan(inventory=cid)
    def test_inventory_duplicate_ids_rejected(self):
        self.source.list_pages={1:{'results':[ENTITY,ENTITY],'totalRecord':2}}
        with self.assertRaises(SourceError):self.worker.inventory()
    def test_list_only_writes_master_without_detail_or_tabs(self):
        self.source.list_pages={1:{'results':[ENTITY],'totalRecord':1}}
        inventory=self.worker.inventory()['capture_id']
        cid=self.worker.plan(inventory=inventory,sections=[],list_only=True)['capture_id']
        calls_before=len(self.source.calls)
        result=self.worker.run(cid)
        self.assertEqual(result['phase'],'complete')
        self.assertEqual(len(self.db.tables['products']),1)
        self.assertEqual(self.db.tables['product_profile'],[])
        self.assertEqual(self.db.tables['product_child_records'],[])
        self.assertTrue(self.db.tables['products'][0]['list_hash'])
        self.assertEqual(self.db.tables['products'][0]['first_seen_at'],self.db.tables['products'][0]['last_seen_at'])
        self.assertEqual(len(self.source.calls),calls_before)
        state=self.worker.load(cid)
        self.assertTrue(state['list_only'])
        self.assertEqual(state['list_evidence']['12']['row']['name'],'test')

    def test_list_only_batch_writes_products_and_audits_once(self):
        entities=[ENTITY,{**ENTITY,'id':13,'code':'0013'},{**ENTITY,'id':14,'code':'0014'}]
        self.source.list_pages={1:{'results':entities,'totalRecord':len(entities)}}
        inventory=self.worker.inventory()['capture_id']
        cid=self.worker.plan(inventory=inventory,sections=[],list_only=True)['capture_id']
        result=self.worker.run(cid)
        self.assertEqual(result['phase'],'complete')
        self.assertEqual(len(self.db.tables['products']),3)
        self.assertEqual(self.db.writes.count(('products','batchCreate')),1)
        self.assertEqual(self.db.writes.count(('products','create')),0)
        self.assertEqual(len(self.db.tables['sync_stream_events']),1)
        self.assertEqual(len(self.db.tables['sync_stream_status']),1)

    def test_inventory_incremental_skips_unchanged_after_verified_profile(self):
        self.source.list_pages={1:{'results':[ENTITY],'totalRecord':1}}
        inventory=self.worker.inventory()['capture_id']
        cid=self.worker.plan(inventory=inventory,sections=[])['capture_id'];self.worker.run(cid)
        plan=self.worker.plan(inventory=inventory,sections=[])
        self.assertEqual(plan['selected'],0)
        self.assertEqual(self.worker.plan(inventory=inventory,sections=[],changed_only=False)['selected'],1)
    def test_failed_profile_does_not_publish_list_hash(self):
        self.source.list_pages={1:{'results':[ENTITY],'totalRecord':1}}
        inventory=self.worker.inventory()['capture_id']
        cid=self.worker.plan(inventory=inventory,sections=[])['capture_id'];self.db.fail_table='product_profile'
        with self.assertRaises(ContractError):self.worker.run(cid)
        self.assertFalse(self.db.tables['products'][0].get('list_hash'))
        self.assertEqual(self.worker.plan(inventory=inventory,sections=[])['selected'],1)
    def test_inventory_artifact_hash_checked_before_plan(self):
        self.source.list_pages={1:{'results':[ENTITY],'totalRecord':1}}
        cid=self.worker.inventory()['capture_id']
        p=self.worker.path(cid)/'list-000001.json';d=json.loads(p.read_text());d['data']['results'][0]['name']='tampered';p.write_text(json.dumps(d))
        with self.assertRaisesRegex(ContractError,'hash'):self.worker.plan(inventory=cid)
    def test_capture_tenant_mismatch_rejected(self):
        cid=self.plan();state=self.worker.load(cid);state['tenant']='other';self.worker.save(state)
        with self.assertRaises(ContractError):self.worker.run(cid)
    def test_wrong_source_object_refused(self):
        cid=self.worker.plan(['99'],sections=[])['capture_id']
        with self.assertRaises(SourceError):self.worker.run(cid)
        self.assertEqual(self.db.tables['products'],[])
    def test_plan_cursor_from_db(self):
        self.db.tables['products']=[{'id':3,'tenant_id':'205149','source_item_id':'12'}]
        plan=self.worker.plan(after_id=2,limit=1,sections=[])
        self.assertEqual(plan['next_after_id'],3)
    def test_byte_limit_preflight(self):
        with self.assertRaises(ContractError):
            self.worker.write_section(1,'barcode',[{'barcode':'a','large':'x'*49000}],STAMP)
        self.assertEqual(self.db.writes,[])
    def test_pending_audit_retried_without_source(self):
        cid=self.plan();self.db.fail_table='sync_stream_events'
        with self.assertRaises(ContractError):self.worker.run(cid)
        self.db.fail_table=None;self.worker.run(cid)
        self.assertEqual(self.source.calls.count('product-detail'),1)
        self.assertEqual(len(self.db.tables['sync_stream_events']),2)

    # ---- P0 list-only correctness (list-first on-demand plan, 2026-11-10) ----
    def _two_item_catalog(self):
        entities=[ENTITY,{**ENTITY,'id':13,'code':'0013'}]
        self.source.list_pages={1:{'results':entities,'totalRecord':2}}
        return entities,self.worker.inventory()['capture_id']
    def test_list_only_run_limit_bounds_first_batch(self):
        # Baseline repro 1: a limit=1 run used to write the whole plan at once.
        _,inventory=self._two_item_catalog()
        cid=self.worker.plan(inventory=inventory,sections=[],list_only=True)['capture_id']
        result=self.worker.run(cid,1)
        self.assertEqual(result['phase'],'running')
        self.assertEqual(result['verified_objects'],1);self.assertEqual(result['remaining_objects'],1)
        self.assertEqual(len(self.db.tables['products']),1)
        result=self.worker.run(cid,1)
        self.assertEqual(result['phase'],'complete');self.assertEqual(result['verified_objects'],2)
        self.assertEqual(len(self.db.tables['products']),2)
    def test_list_only_complete_replay_makes_zero_requests(self):
        # Baseline repro 2: replaying a complete capture used to issue DB requests
        # and refresh last_success_at; it must return the original result untouched.
        _,inventory=self._two_item_catalog()
        cid=self.worker.plan(inventory=inventory,sections=[],list_only=True)['capture_id']
        self.worker.run(cid)
        requests=self.db.requests;writes=len(self.db.writes)
        status_rows=copy.deepcopy(self.db.tables['sync_stream_status'])
        events_rows=copy.deepcopy(self.db.tables['sync_stream_events'])
        result=self.worker.run(cid)
        self.assertEqual(result['phase'],'complete')
        self.assertEqual(self.db.requests,requests);self.assertEqual(len(self.db.writes),writes)
        self.assertEqual(self.db.tables['sync_stream_status'],status_rows)
        self.assertEqual(self.db.tables['sync_stream_events'],events_rows)
    def test_list_only_replan_selects_none_but_tracks_detail_debt(self):
        # Baseline repro 3: a missing detail_hash used to push unchanged masters
        # back into the list queue; selection must be 0 with detail debt separate.
        _,inventory=self._two_item_catalog()
        cid=self.worker.plan(inventory=inventory,sections=[],list_only=True)['capture_id']
        self.worker.run(cid)
        plan=self.worker.plan(inventory=inventory,sections=[],list_only=True)
        self.assertEqual(plan['selected'],0)
        self.assertEqual(plan['detail_needed'],2)
    def test_list_only_update_preserves_detail_fields_and_reports_observation(self):
        # A list refresh of an existing profile only advances the seen time; detail
        # fields survive and the summary reports observation separately from content.
        self.source.list_pages={1:{'results':[ENTITY],'totalRecord':1}}
        inventory=self.worker.inventory()['capture_id']
        prior={**product_row(ENTITY,'2026-11-08 08:00:00'),'id':1,'tenant_id':'205149',
               'list_hash':digest(ENTITY),'sub_name':'kept','barcode':'560'}
        self.db.tables['products']=[prior]
        cid=self.worker.plan(inventory=inventory,sections=[],list_only=True,changed_only=False)['capture_id']
        result=self.worker.run(cid)
        saved=self.db.tables['products'][0]
        self.assertEqual(saved['sub_name'],'kept');self.assertEqual(saved['barcode'],'560')
        self.assertTrue(saved.get('detail_hash'))
        self.assertEqual(saved['last_seen_at'],'2026-11-09 18:00:00')
        self.assertEqual(result['last_run_observation_updates'],1)
        self.assertEqual(result['last_run_content_changes'],0)
    def test_list_only_resume_confirms_applied_rows_without_replay(self):
        # A lost write response (rows landed, manifest recorded nothing) is resolved
        # by unique-key readback; batchCreate must not be replayed blindly.
        entities,inventory=self._two_item_catalog()
        cid=self.worker.plan(inventory=inventory,sections=[],list_only=True)['capture_id']
        for entity in entities:
            self.db.tables['products'].append({'id':len(self.db.tables['products'])+1,'tenant_id':'205149',
                **product_row(entity,STAMP,kind='list'),'first_seen_at':STAMP})
        result=self.worker.run(cid)
        self.assertEqual(result['phase'],'complete');self.assertEqual(result['verified_objects'],2)
        self.assertEqual(self.db.writes.count(('products','batchCreate')),0)
        self.assertEqual(self.db.writes.count(('products','update')),0)
        self.assertEqual(result['last_run_unchanged'],2)
    def test_list_only_failure_keeps_verified_batch_for_resume(self):
        # A failure after one bounded batch keeps that batch's verified set durable;
        # resume continues the same object list without rewriting verified rows.
        _,inventory=self._two_item_catalog()
        cid=self.worker.plan(inventory=inventory,sections=[],list_only=True)['capture_id']
        self.worker.run(cid,1)
        self.db.fail_table='products'
        with self.assertRaises(ContractError):self.worker.run(cid,1)
        state=self.worker.load(cid)
        self.assertEqual(state['phase'],'failed')
        self.assertEqual(state['objects']['12']['list']['status'],'verified')
        self.db.fail_table=None
        result=self.worker.run(cid,1)
        self.assertEqual(result['phase'],'complete');self.assertEqual(len(self.db.tables['products']),2)
        first=[r for r in self.db.tables['products'] if r['source_item_id']=='12'][0]
        self.assertEqual(first['id'],1)
    def test_detail_debt_survives_list_version_overwrite(self):
        # Codex comment-2 repro: a list refresh rewrites the master source_version, so
        # the detail-debt baseline must be the committed profile version, never the
        # master row. The debt persists across inventories until detail is re-verified.
        self.source.list_pages={1:{'results':[ENTITY],'totalRecord':1}}
        first=self.worker.inventory()['capture_id']
        self.worker.run(self.worker.plan(inventory=first,sections=[])['capture_id'])  # detail v1 verified
        self.source.list_pages={1:{'results':[{**ENTITY,'version':2}],'totalRecord':1}}
        second=self.worker.inventory()['capture_id']
        plan=self.worker.plan(inventory=second,sections=[],list_only=True)
        self.assertEqual(plan['selected'],1);self.assertEqual(plan['detail_needed'],1)
        self.worker.run(plan['capture_id'])  # list-only sync: master source_version becomes 2
        replan=self.worker.plan(inventory=second,sections=[],list_only=True)
        self.assertEqual(replan['selected'],0)
        self.assertEqual(replan['detail_needed'],1)  # profile still v1: debt must not vanish
    def test_plan_db_budget_fails_closed_before_any_read(self):
        # The estimate and its cap are checked before the selection pass issues
        # a single data-center request; over budget means split or raise, never read.
        _,inventory=self._two_item_catalog()
        before=self.db.requests
        with self.assertRaisesRegex(ValueError,'Plan DB budget exceeded'):
            self.worker.plan(inventory=inventory,sections=[],list_only=True,max_db_requests=1)
        self.assertEqual(self.db.requests,before)
    def test_plan_carries_mode_contract_and_db_estimate(self):
        _,inventory=self._two_item_catalog()
        result=self.worker.plan(inventory=inventory,sections=[],list_only=True,
                                sync_mode={'sync_mode':'list','mode_basis':'explicit',
                                           'detail_demand':'none'})
        self.assertEqual(result['sync_mode'],'list')
        self.assertEqual(result['mode_basis'],'explicit')
        self.assertEqual(result['estimated_db_requests'],2)   # 1 products page + <=1 profile page
        self.assertEqual(result['db_request_budget'],400)
    def test_plan_rejects_list_only_with_bounded_demand(self):
        _,inventory=self._two_item_catalog()
        with self.assertRaisesRegex(ValueError,'bounded detail demand'):
            self.worker.plan(inventory=inventory,sections=[],list_only=True,
                             sync_mode={'sync_mode':'list','mode_basis':'explicit',
                                        'detail_demand':'bounded'})
    def test_list_only_without_inventory_refused_at_plan_time(self):
        # A list-only capture without list evidence can never run; refuse the
        # plan itself instead of delivering a capture that fails on run.
        with self.assertRaisesRegex(ValueError,'sealed inventory'):
            self.worker.plan(['12'],sections=[],list_only=True)
    def test_inventory_tab_demand_refused_before_any_capture_persisted(self):
        # comment-11: tab demand on an inventory can neither be proven complete
        # nor bypassed by running a rejected capture; refuse before save().
        _,inventory=self._two_item_catalog()
        with self.assertRaisesRegex(ValueError,'planning-only'):
            self.worker.plan(inventory=inventory,sections=['stock'],tab_demands=['stock'])
        manifests=[p.parent.name for p in Path(self.worker.root).glob('*/manifest.json')]
        self.assertEqual(manifests,[inventory])  # only the sealed inventory exists
    def test_explicit_id_tab_demand_is_schedulable(self):
        result=self.worker.plan(['12'],sections=['stock'],tab_demands=['stock'])
        self.assertEqual(result['sections'],['stock'])
        self.assertEqual(result['selected'],1)
    def _seed_verified_detail(self,row,observed='2026-11-08 08:00:00'):
        self.db.tables['products']=[{**product_row(row,observed),'id':1,'tenant_id':'205149',
                                     'list_hash':digest(row)}]
        self.db.tables['product_profile']=[{'id':1,'tenant_id':'205149','product_id':1,
            'payload':json.dumps(ENTITY),'payload_hash':digest(ENTITY)}]
    def test_detail_unknown_when_versionless_list_unchanged(self):
        # Real inventories (Codex comment-4) often carry no version: freshness of an
        # existing profile cannot be judged, must surface as unknown — not complete,
        # and without issuing useless product_profile reads.
        row_nv={k:v for k,v in ENTITY.items() if k!='version'}
        self.source.list_pages={1:{'results':[row_nv],'totalRecord':1}}
        inventory=self.worker.inventory()['capture_id']
        self._seed_verified_detail(row_nv)
        before=self.db.requests
        plan=self.worker.plan(inventory=inventory,sections=[],list_only=True)
        self.assertEqual(plan['selected'],0)
        self.assertEqual(plan['detail_needed'],0)
        self.assertEqual(plan['detail_unknown'],1)
        self.assertEqual(self.db.requests-before,1)  # products read only; no profile query
    def test_detail_unknown_when_versionless_list_changed(self):
        # A changed version-less list row enqueues for the list queue, and detail
        # freshness stays unknown so on-demand detail selectors can still see it.
        row_nv={k:v for k,v in ENTITY.items() if k!='version'}
        self.source.list_pages={1:{'results':[row_nv],'totalRecord':1}}
        inventory=self.worker.inventory()['capture_id']
        self._seed_verified_detail(ENTITY)  # prior list hash differs from the new row
        plan=self.worker.plan(inventory=inventory,sections=[],list_only=True)
        self.assertEqual(plan['selected'],1)
        self.assertEqual(plan['detail_needed'],0)
        self.assertEqual(plan['detail_unknown'],1)

if __name__=='__main__':unittest.main()
