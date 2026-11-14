import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from kemai_pipeline.direct_read import DirectReadClient, DirectReadError, report_params
from kemai_pipeline.source import OpenCLISource, SourceError


class Reply:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class DirectReadTests(unittest.TestCase):
    auth = {'token': 'private-test-token'}

    def test_statistical_query_is_gated_and_has_fixed_scope(self):
        calls, leases = [], []

        @contextmanager
        def lease(label, *, fail_on_cooldown=False):
            leases.append((label, fail_on_cooldown))
            yield

        def opener(request, timeout):
            calls.append(request)
            return Reply({'code': 0, 'data': {'totalRecord': 3856}})

        client = DirectReadClient(self.auth, opener=opener, lease=lease)
        self.assertEqual(client.statistical_total('retail-orders', '2026-11-09', '2026-11-09'), 3856)
        self.assertEqual(client.operations, 1)
        self.assertEqual(leases, [('direct-read:retail-orders:stat', True)])
        self.assertTrue(calls[0].full_url.endswith('/queryStatisticalInfo'))
        body = json.loads(calls[0].data)
        self.assertEqual(body['params']['branchIdList'], ['69028303'])
        self.assertEqual(body['params']['dateType'], '0')
        self.assertNotIn('timeType', body['params'])

    def test_business_limit_records_cooldown_without_logging_payload(self):
        active = []

        @contextmanager
        def lease(*_args, **_kwargs):
            active.append(True)
            try:
                yield
            finally:
                active.pop()

        client = DirectReadClient(self.auth, opener=lambda *_args, **_kwargs:
                                  Reply({'code': 1, 'msg': '您的操作太频繁，请稍后再试'}),
                                  lease=lease)
        with patch('kemai_pipeline.direct_read.record_limit') as record:
            record.side_effect = lambda *_args, **_kwargs: self.assertEqual(active, [True])
            with self.assertRaisesRegex(DirectReadError, 'RATE_LIMIT'):
                client.statistical_total('purchase-orders', '2026-11-09', '2026-11-09')
        self.assertEqual(client.operations, 1)
        self.assertEqual(record.call_args.args[1], 'Source business rate limit')

    def test_invalid_total_fails_closed(self):
        client = DirectReadClient(self.auth, opener=lambda *_args, **_kwargs:
                                  Reply({'code': 0, 'data': {'totalRecord': 0.5}}),
                                  lease=lambda *_args, **_kwargs: _NullLease())
        with self.assertRaisesRegex(DirectReadError, 'invalid'):
            client.statistical_total('purchase-orders', '2026-11-09', '2026-11-09')

    def test_report_time_fields_and_filter_whitelist(self):
        sales = report_params('retail-sales', '2026-11-09', '2026-11-09')
        history = report_params('purchase-receive-history', '2026-11-09', '2026-11-09')
        self.assertEqual(sales['timeType'], '1')
        self.assertEqual(history['dateType'], '1')
        self.assertEqual(history['queryType'], '1')
        self.assertNotIn('queryType', report_params('purchase-orders', '2026-11-09', '2026-11-09'))
        with self.assertRaises(ValueError):
            report_params('source-write', '2026-11-09', '2026-11-09')

    def test_opencli_export_start_is_rejected_before_executor(self):
        calls = []
        source = OpenCLISource(executor=lambda *_args, **_kwargs: calls.append(1))
        with self.assertRaisesRegex(SourceError, 'SOURCE_WRITE_FORBIDDEN'):
            source.call('retail-orders-export-start')
        self.assertEqual(calls, [])
        self.assertEqual(source.operations, 0)

    def test_pipeline_statistic_requires_exact_captured_scope(self):
        source = OpenCLISource(executor=lambda *_args, **_kwargs: None)
        scope = report_params('retail-sales', '2026-11-09', '2026-11-09')
        with patch('kemai_pipeline.direct_read.session_auth') as auth:
            with self.assertRaisesRegex(SourceError, 'scope differs'):
                source.statistical_total('retail-sales', '2026-11-09',
                                         query_scope={**scope, 'saleTimeTo': '2026-11-09 22:00:00'})
            auth.assert_not_called()

    def test_pipeline_statistic_counts_the_direct_source_operation(self):
        class FakeClient:
            def __init__(self, auth, *, max_operations):
                self.operations = 0

            def statistical_total(self, *args):
                self.operations += 1
                return 13228

        source = OpenCLISource(executor=lambda *_args, **_kwargs: None)
        scope = report_params('retail-sales', '2026-11-09', '2026-11-09')
        with patch('kemai_pipeline.direct_read.session_auth', return_value=self.auth), \
             patch('kemai_pipeline.direct_read.DirectReadClient', FakeClient):
            self.assertEqual(source.statistical_total('retail-sales', '2026-11-09',
                                                       query_scope=scope), 13228)
        self.assertEqual(source.operations, 1)


class _NullLease:
    def __enter__(self):
        return None

    def __exit__(self, *_):
        return False


if __name__ == '__main__':
    unittest.main()
