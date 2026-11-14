import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kemai_pipeline.product_workflow import ProductWorkflow, product_create_row
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter
from tests.python.test_kemai_pipeline_detail_workflow import LockProbe


class FakeProductSource:
    def __init__(self):
        self.calls = []

    def call(self, command, *args):
        self.calls.append((command, args))
        return [{
            'schema_version': 1, 'kind': 'list', 'object_key': None, 'section': None,
            'observed_at': '2026-11-10T01:00:00Z', 'page': 1, 'page_size': 100,
            'query_scope': {
                'params': {'startGmtCreate': '2026-11-09 00:00:00'},
                'orders': [{'fieldName': 'gmtCreate', 'order': 'desc', 'frontendSort': False}],
            },
            'data': {'totalRecord': 1, 'results': [
                {'id': '42', 'code': '00042', 'name': 'Example', 'version': 1}
            ]},
        }]


class ProductIncrementalTests(unittest.TestCase):
    def test_inventory_forwards_incremental_scope_and_seals_filtered_catalog(self):
        with tempfile.TemporaryDirectory() as root:
            source = FakeProductSource()
            config = {'appcode': 'app-test', 'tenant_id': 't', 'datasets': {}}
            workflow = ProductWorkflow(LovrabetAdapter(config), source, Path(root))
            with patch('kemai_pipeline.product_workflow.writer_lock', LockProbe()):
                result = workflow.inventory(from_time='2026-11-09', sort='gmtCreate')
            self.assertEqual(result['phase'], 'inventory_complete')
            state = workflow.load(result['capture_id'])
            self.assertEqual(state['source_total'], 1)
            self.assertEqual(result['coverage'], 'filtered_time_window_single_pass')
            self.assertEqual(source.calls[0][0], 'product-list')
            self.assertEqual(source.calls[0][1], ('--from', '2026-11-09', '--sort', 'gmtCreate',
                                                  '--page', '1', '--rows', '100'))
    def test_new_product_mapping_includes_first_seen_at(self):
        row = product_create_row({'id': '42', 'code': '00042', 'name': 'Example'},
                                 '2026-11-10 10:00:00')
        self.assertEqual(row['first_seen_at'], '2026-11-10 10:00:00')
        self.assertEqual(row['last_seen_at'], '2026-11-10 10:00:00')


if __name__ == '__main__':
    unittest.main()
