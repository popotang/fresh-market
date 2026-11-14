import importlib.util
import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'kemai_total_reconcile', ROOT / 'scripts' / 'kemai_total_reconcile.py')
reconcile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)


def day_of(where):
    value = list(where.values())[0] if where else None
    if isinstance(value, dict):
        value = value.get('$gte')
    if isinstance(value, int):
        return reconcile.datetime.fromtimestamp(value / 1000, reconcile.TZ).strftime('%Y-%m-%d')
    return str(value)[:10]


class FakeAdapter:
    def __init__(self, totals=None, daily=None, status=None):
        self.totals = totals or {}
        self.daily = daily or {}
        self.status = status or []
        self.requests = 0

    def _scoped_where(self, table, where):
        return {'$and': [{'tenant_id': {'$eq': '205149'}}, where or {}]}

    def dataset_code(self, table):
        return 'code:' + table

    def count(self, table, *, where=None):
        return self.page(table, where=where, size=1)[1]

    def invoke(self, *args):
        self.requests += 1
        params = json.loads(args[args.index('--params') + 1])
        where = params.get('where') or {}
        if '$and' in where:
            where = where['$and'][-1]
        day = day_of(where)
        total = next((value for (_, candidate), value in self.daily.items()
                      if candidate == day), 0)
        return {'paging': {'totalCount': total}}

    def page(self, table, *, where=None, select=None, page=1, size=100):
        self.requests += 1
        if table == 'product_branch_stocks':
            raise reconcile.ContractError('No verified dataset mapping: product_branch_stocks')
        if table == 'sync_stream_status':
            return self.status, len(self.status)
        key = (table, day_of(where))
        if key in self.daily:
            return [], self.daily[key]
        return [], self.totals.get(table, 0)


def fake_reader(pages):
    def run(argv, **kwargs):
        stream = argv[argv.index('--kind') + 1]
        page = int(argv[argv.index('--page') + 1])
        rows = [{}] * pages.get(page, 0)
        return SimpleNamespace(returncode=0,
                               stdout=json.dumps({'data': [{'stream': stream, 'rows': rows}]}),
                               stderr='')
    return reconcile.SourceReader(runner=run)


class WindowTests(unittest.TestCase):
    def test_datetime_window_bounds_are_well_formed(self):
        where = reconcile.window_where(reconcile.CONFIG['retail-orders'], '2026-11-01', '2026-11-09')
        self.assertEqual(where['source_created_at']['$gte'], '2026-11-01 00:00:00')
        self.assertEqual(where['source_created_at']['$lt'], '2026-11-10 00:00:00')
        self.assertEqual(where['source_branch_id'], {'$eq': '69028303'})

    def test_sales_uses_upload_date_even_if_db_index_is_missing(self):
        where = reconcile.window_where(reconcile.CONFIG['retail-sales'], '2026-11-01', '2026-11-09')
        self.assertIn('source_created_at', where)

    def test_epoch_window_covers_one_day(self):
        where = reconcile.window_where(reconcile.CONFIG['retail-promote-sharing'], '2026-11-09', '2026-11-09')
        start, end = where['sold_at']['$gte'], where['sold_at']['$lt']
        self.assertIsInstance(start, int)
        self.assertEqual(end - start, 86400000)

    def test_purchase_kind_filter_is_applied(self):
        where = reconcile.window_where(reconcile.CONFIG['purchase-receipts'], '2026-11-01', '2026-11-01')
        self.assertEqual(where['document_kind'], {'$eq': 'receipt'})


class LocalModeTests(unittest.TestCase):
    def test_local_mode_issues_zero_source_operations(self):
        adapter = FakeAdapter(totals={'retail_orders': 100, 'products': 10})
        payload = reconcile.local_view(to_day='2026-11-10', adapter=adapter)
        self.assertEqual(payload['mode'], 'local')
        self.assertEqual(payload['source_operations'], 0)
        self.assertGreater(adapter.requests, 0)

    def test_local_mode_joins_stream_status(self):
        adapter = FakeAdapter(status=[{'stream': 'retail-orders', 'covered_through': 1793980800000,
                                       'last_success_at': 1794241262000, 'last_attempt_status': 'success'}])
        payload = reconcile.local_view(to_day='2026-11-10', adapter=adapter)
        row = {item['stream']: item for item in payload['streams']}['retail-orders']
        self.assertEqual(row['covered_through'], 1793980800000)
        self.assertEqual(row['last_attempt_status'], 'success')

    def test_object_completion_is_local_ratio(self):
        adapter = FakeAdapter(totals={'products': 200, 'product_profile': 150})
        payload = reconcile.local_view(to_day='2026-11-10', adapter=adapter)
        rows = {item['table']: item for item in payload['objects']}
        self.assertEqual(rows['product_profile']['completion'], 75.0)
        self.assertEqual(rows['product_profile']['base']['rows'], 200)

    def test_object_without_dataset_mapping_is_reported_not_fatal(self):
        payload = reconcile.local_view(to_day='2026-11-10', adapter=FakeAdapter(totals={'products': 10}))
        rows = {item['table']: item for item in payload['objects']}
        self.assertEqual(rows['product_branch_stocks']['status'], 'no_dataset_mapping')
        self.assertIsNone(rows['product_branch_stocks']['completion'])

    def test_daily_counts_are_opt_in(self):
        adapter = FakeAdapter(totals={'retail_orders': 5})
        reconcile.local_view(to_day='2026-11-10', adapter=adapter)
        baseline = adapter.requests
        adapter2 = FakeAdapter(totals={'retail_orders': 5})
        payload = reconcile.local_view(to_day='2026-11-10', adapter=adapter2, daily=True, days=3)
        self.assertGreater(adapter2.requests, baseline)
        self.assertEqual(len(payload['streams'][0]['daily']), 3)


class ProbeTests(unittest.TestCase):
    def test_short_tail_after_stored_range_yields_exact_total(self):
        adapter = FakeAdapter(daily={('retail_orders', '2026-11-09'): 2024})
        payload = reconcile.probe_day('retail-orders', '2026-11-09', adapter=adapter,
                                      reader=fake_reader({3: 1000, 4: 856}))
        self.assertEqual(payload['status'], 'mismatch')
        self.assertEqual(payload['source_total'], 3856)
        self.assertEqual(payload['delta'], 1832)
        self.assertEqual(payload['source_operations'], 2)

    def test_no_rows_beyond_stored_range_is_consistent_with_one_read(self):
        adapter = FakeAdapter(daily={('retail_orders', '2026-11-09'): 3856})
        payload = reconcile.probe_day('retail-orders', '2026-11-09', adapter=adapter,
                                      reader=fake_reader({}))
        self.assertEqual(payload['status'], 'consistent')
        self.assertTrue(payload['match'])
        self.assertEqual(payload['source_operations'], 1)

    def test_full_page_beyond_range_proves_missing_rows(self):
        adapter = FakeAdapter(daily={('retail_orders', '2026-11-09'): 100})
        payload = reconcile.probe_day('retail-orders', '2026-11-09', adapter=adapter,
                                      reader=fake_reader({1: 1000}))
        self.assertEqual(payload['status'], 'mismatch')
        self.assertEqual(payload['source_total'], 1000)
        self.assertEqual(payload['delta'], 900)


class ExactTests(unittest.TestCase):
    def test_exact_total_counts_to_short_page(self):
        adapter = FakeAdapter(daily={('retail_orders', '2026-11-09'): 3856})
        payload = reconcile.exact_total('retail-orders', '2026-11-09', '2026-11-09', adapter=adapter,
                                        reader=fake_reader({1: 1000, 2: 1000, 3: 1000, 4: 856}))
        self.assertEqual(payload['status'], 'match')
        self.assertEqual(payload['source_total'], 3856)
        self.assertEqual(payload['source_operations'], 4)

    def test_page_budget_exhausted_is_unavailable(self):
        adapter = FakeAdapter()
        payload = reconcile.exact_total('retail-orders', '2026-11-09', '2026-11-09', adapter=adapter,
                                        reader=fake_reader({1: 1000, 2: 1000}), max_pages=2)
        self.assertEqual(payload['status'], 'source_total_unavailable')
        self.assertIsNone(payload['match'])


class ApiModeTests(unittest.TestCase):
    auth = {'token': 'x', 'loginName': 'u', 'shopAdminId': '1', 'merchantName': 'm'}

    def worker(self, pages, reported=None):
        calls = []

        def call(stream, from_day, to_day, page, auth, *, rows=reconcile.API_ROWS):
            calls.append(page)
            if rows == 1:
                return [], reported, 0.1
            return [{}] * pages.get(page, 0), 0, 0.1
        call.calls = calls
        return call

    def test_total_record_is_trusted_in_one_call(self):
        worker = self.worker({}, reported=18492)
        result = reconcile.api_window_total('retail-orders', '2026-11-09', '2026-11-09',
                                            self.auth, worker=worker, interval=0,
                                            stats_worker=lambda *a, **k: None)
        self.assertEqual(result['source_total'], 18492)
        self.assertEqual(result['pages'], 1)
        self.assertEqual(result['basis'], 'source_totalRecord')

    def test_zero_total_record_falls_back_to_page_count(self):
        worker = self.worker({1: 1000, 2: 1000, 3: 1000, 4: 856})
        result = reconcile.api_window_total('retail-orders', '2026-11-09', '2026-11-09',
                                            self.auth, worker=worker, interval=0,
                                            stats_worker=lambda *a, **k: None)
        self.assertEqual(result['source_total'], 3856)
        self.assertEqual(result['pages'], 4)
        self.assertEqual(result['operations'], 5)
        self.assertTrue(result['complete'])

    def test_summary_endpoint_replaces_the_page_scan(self):
        worker = self.worker({}, reported=0)
        result = reconcile.api_window_total('retail-orders', '2026-11-09', '2026-11-09',
                                            self.auth, worker=worker, interval=0,
                                            stats_worker=lambda *a, **k: 3856)
        self.assertEqual(result['source_total'], 3856)
        self.assertEqual(result['operations'], 1)
        self.assertEqual(result['pages'], 0)
        self.assertEqual(result['basis'], 'source_statistical_total')
        self.assertEqual(worker.calls, [])

    def test_summary_failure_falls_back_to_paging(self):
        worker = self.worker({1: 1000, 2: 856})
        result = reconcile.api_window_total('retail-orders', '2026-11-09', '2026-11-09',
                                            self.auth, worker=worker, interval=0,
                                            stats_worker=lambda *a, **k: None)
        self.assertEqual(result['source_total'], 1856)
        self.assertEqual(result['basis'], 'page_count_short_final_page')

    def test_page_budget_marks_incomplete(self):
        worker = self.worker({1: 1000, 2: 1000})
        result = reconcile.api_window_total('retail-sales', '2026-11-09', '2026-11-09',
                                            self.auth, worker=worker, max_pages=2, interval=0,
                                            stats_worker=lambda *a, **k: None)
        self.assertFalse(result['complete'])
        self.assertEqual(result['basis'], 'page_budget_exhausted')

    def test_api_view_compares_source_with_data_center_per_day(self):
        adapter = FakeAdapter(daily={('retail_orders', '2026-11-09'): 2024})
        worker = self.worker({1: 1000, 2: 1000, 3: 1000, 4: 856})
        payload = reconcile.api_view(to_day='2026-11-09', days=1, streams=['retail-orders'],
                                     adapter=adapter, auth=self.auth, worker=worker, interval=0,
                                     stats_worker=lambda *a, **k: None)
        row = payload['streams'][0]
        self.assertEqual(row['source_total'], 3856)
        self.assertEqual(row['data_center_total'], 2024)
        self.assertEqual(row['delta'], 1832)
        self.assertEqual(row['status'], 'mismatch')

    def test_api_view_reports_errors_without_aborting(self):
        def boom(*args, **kwargs):
            raise RuntimeError('Source API rejected the request: code=140001')
        payload = reconcile.api_view(to_day='2026-11-09', days=1, streams=['retail-orders'],
                                     adapter=FakeAdapter(), auth=self.auth, worker=boom, interval=0,
                                     stats_worker=lambda *a, **k: None)
        self.assertEqual(payload['streams'][0]['status'], 'error')
        self.assertEqual(payload['source_operations'], 0)

    def test_list_params_use_report_specific_date_fields(self):
        retail = reconcile.list_params('retail-orders', '2026-11-09', '2026-11-09')
        sales = reconcile.list_params('retail-sales', '2026-11-09', '2026-11-09')
        purchase = reconcile.list_params('purchase-orders', '2026-11-09', '2026-11-09')
        self.assertEqual(retail['saleTimeFrom'], '2026-11-09 00:00:00')
        self.assertEqual(retail['branchIdList'], ['69028303'])
        self.assertEqual(sales['timeType'], '1')
        self.assertEqual(purchase['operDateFrom'], '2026-11-09')

    def test_browser_auth_refuses_missing_token(self):
        runner = Mock(return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps({'ok': True, 'data': json.dumps(
                {'token': '', 'loginName': 'u', 'shopAdminId': '1', 'merchantName': 'm'})}), stderr=''))
        with self.assertRaises(RuntimeError):
            reconcile.browser_auth(runner=runner)

    def test_browser_auth_never_echoes_the_token(self):
        runner = Mock(return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps({'ok': True, 'data': json.dumps(
                {'token': 'secret-value', 'loginName': 'u', 'shopAdminId': '1', 'merchantName': 'm'})}),
            stderr=''))
        auth = reconcile.browser_auth(runner=runner)
        self.assertEqual(auth['token'], 'secret-value')
        self.assertNotIn('secret-value', json.dumps(reconcile.plan_budget('api')))

    def test_api_budget_includes_shared_gate(self):
        budget = reconcile.plan_budget('api', ['retail-orders'], days=1)
        self.assertEqual(budget['source_operations_expected'], 1)
        self.assertEqual(budget['source_gate_min_interval_seconds'], 2)

    def test_sales_count_uses_upload_day_and_branch_scope(self):
        payload = reconcile.api_view(to_day='2026-11-09', days=1, streams=['retail-sales'],
                                     adapter=FakeAdapter(daily={('retail_sale_flows','2026-11-09'):13228}), auth=self.auth,
                                     worker=self.worker({}), interval=0,
                                     stats_worker=lambda *a: 13228)
        row = payload['streams'][0]
        self.assertEqual(row['status'], 'match')
        self.assertTrue(row['match'])
        self.assertEqual(row['data_center_total'], 13228)

    def test_db_timeout_preserves_source_total_without_claiming_match(self):
        adapter = FakeAdapter()
        adapter.invoke = Mock(side_effect=RuntimeError('timeout'))
        payload = reconcile.api_view(to_day='2026-11-09', days=1, streams=['retail-sales'],
                                     adapter=adapter, auth=self.auth, worker=self.worker({}),
                                     stats_worker=lambda *a: 13228)
        row = payload['streams'][0]
        self.assertEqual(row['source_total'], 13228)
        self.assertEqual(row['status'], 'db_window_unavailable')
        self.assertIsNone(row['match'])


class BudgetAndCliTests(unittest.TestCase):
    def test_local_budget_is_zero_source_operations(self):
        budget = reconcile.plan_budget('local')
        self.assertEqual(budget['source_operations_expected'], 0)
        self.assertEqual(budget['source_operations_worst_case'], 0)

    def test_probe_budget_is_one_read_per_day(self):
        budget = reconcile.plan_budget('probe', ['retail-orders'], days=1)
        self.assertEqual(budget['source_operations_expected'], 1)

    def test_dry_run_issues_no_calls(self):
        called = []
        originals = {}
        for name in ('local_view', 'probe_day', 'exact_total'):
            originals[name] = getattr(reconcile, name)
            setattr(reconcile, name, lambda *a, _name=name, **k: called.append(_name))
        try:
            code = reconcile.main(['--mode', 'local', '--dry-run'])
        finally:
            for name, original in originals.items():
                setattr(reconcile, name, original)
        self.assertEqual(code, 0)
        self.assertEqual(called, [])

    def test_probe_requires_single_stream(self):
        with self.assertRaises(SystemExit):
            reconcile.main(['--mode', 'probe', '--dry-run', '--stream', 'all'])

    def test_artifacts_write_private_json_and_doc(self):
        payload = reconcile.local_view(to_day='2026-11-10', adapter=FakeAdapter(totals={'products': 4}))
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            paths = reconcile.write_artifacts(payload, outdir=tmp)
            self.assertTrue(Path(paths['json']).exists())
            saved = json.loads(Path(paths['json']).read_text())
            self.assertEqual(saved['source_operations'], 0)
            self.assertEqual(saved['to'], '2026-11-10')


if __name__ == '__main__':
    unittest.main()
