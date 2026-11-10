import copy
import json
import unittest
from kemai_pipeline.lovrabet_adapter import LovrabetAdapter, ContractError
from kemai_pipeline.child_batch import reconcile

TABLE = 'purchase_document_lines'


def row(i, qty=0, **extra):
    return {'document_id': 10, 'source_line_id': str(i), 'quantity': qty, **extra}


class MemoryClient(LovrabetAdapter):
    def __init__(self, rows):
        super().__init__({'tenant_id': 't', 'appcode': 'fake', 'datasets': {TABLE: 'fake'}})
        self.rows = [{'id': i+1, **r} for i, r in enumerate(rows)]
        self.calls = []
        self.lost_ack = False
        self.partial_write = False
        self.corrupt_read = False

    def invoke(self, *args):
        verb = args[1]
        payload = json.loads(args[args.index('--params') + 1])
        self.calls.append((verb, payload))
        if verb == 'update':
            targets = payload['id'][:1] if self.partial_write else payload['id']
            for pk in targets:
                for r in self.rows:
                    if r['id'] == pk:
                        r.update({k: v for k, v in payload.items() if k != 'id'})
        elif verb == 'batchCreate':
            for r in payload:
                self.rows.append({'id': len(self.rows)+1, **r})
        if self.lost_ack or self.partial_write:
            raise ContractError('transport unknown')

    def read_all(self, table, *, where):
        self.calls.append(('filter', where))
        found = copy.deepcopy([r for r in self.rows if r['document_id'] == where['document_id']['$eq']])
        if self.corrupt_read:
            found[0]['quantity'] = -1
        return found

    def run(self, desired, before=None):
        return reconcile(self, TABLE, 10, desired, copy.deepcopy(self.rows) if before is None else before)


class ChildBatchTests(unittest.TestCase):
    def test_identical_patches_group_and_readback_once(self):
        client = MemoryClient([row(i) for i in range(3)])
        result = client.run([row(i, 4) for i in range(3)])
        self.assertEqual([v for v, _ in client.calls], ['update', 'filter'])
        self.assertEqual(client.calls[0][1], {'id': [1, 2, 3], 'quantity': 4})
        self.assertEqual(result['stats']['changed_fields'], {'quantity': 3})
        self.assertEqual(result['stats']['updated_rows'], 3)
        self.assertEqual(result['stats']['update_groups'], 1)

    def test_different_values_never_merge(self):
        client = MemoryClient([row(1), row(2)])
        result = client.run([row(1, 4), row(2, 5)])
        self.assertEqual(result['stats']['update_groups'], 2)
        self.assertEqual([v for v, _ in client.calls], ['update', 'update', 'filter'])

    def test_distinct_payload_is_a_distinct_patch(self):
        client = MemoryClient([row(1), row(2)])
        result = client.run([row(1, 4, payload='{"id":1}'), row(2, 4, payload='{"id":2}')])
        self.assertEqual(result['stats']['update_groups'], 2)
        self.assertEqual(result['stats']['changed_fields'], {'quantity': 2, 'payload': 2})

    def test_create_update_unchanged_share_final_read(self):
        client = MemoryClient([row(1), row(2, 2)])
        result = client.run([row(1, 1), row(2, 2), row(3, 3)])
        self.assertEqual([v for v, _ in client.calls], ['batchCreate', 'update', 'filter'])
        self.assertEqual(result['stats']['unchanged_rows'], 1)
        self.assertFalse(result['stale'])

    def test_id_count_is_bounded(self):
        client = MemoryClient([row(i) for i in range(201)])
        result = client.run([row(i, 4) for i in range(201)])
        self.assertEqual(result['stats']['update_groups'], 1)
        self.assertEqual(result['stats']['update_requests'], 2)
        self.assertEqual([len(p['id']) for v, p in client.calls if v == 'update'], [200, 1])

    def test_all_size_checks_happen_before_any_write(self):
        client = MemoryClient([row(1), row(2)])
        with self.assertRaisesRegex(ContractError, 'BYTE_LIMIT'):
            client.run([row(1, 1), row(2, 2, payload='x'*50000)])
        self.assertEqual(client.calls, [])

    def test_lost_ack_landed_is_confirmed_without_retry(self):
        client = MemoryClient([row(1), row(2)])
        client.lost_ack = True
        result = client.run([row(1, 3), row(2, 3)])
        self.assertTrue(result['unknown_write_reconciled'])
        self.assertEqual([v for v, _ in client.calls], ['update', 'filter'])

    def test_partial_unknown_stops_remaining_writes_and_requires_reconcile(self):
        client = MemoryClient([row(1), row(2), row(3)])
        client.partial_write = True
        with self.assertRaisesRegex(ContractError, 'UPSERT_OUTCOME_UNKNOWN'):
            client.run([row(1, 3), row(2, 3), row(3, 4)])
        self.assertEqual([v for v, _ in client.calls], ['update', 'filter'])
        self.assertEqual(client.rows[2]['quantity'], 0)

    def test_silent_bad_write_is_rejected(self):
        client = MemoryClient([row(1)])
        client.corrupt_read = True
        with self.assertRaisesRegex(ContractError, 'UPSERT_OUTCOME_UNKNOWN'):
            client.run([row(1, 3)])

    def test_scope_keys_and_ids_checked_before_write(self):
        cases = [[{'id': 1, **row(1), 'document_id': 11}],
                 [row(1)], [{'id': 1, **row(1)}, {'id': 2, **row(1)}],
                 [{'id': 1, **row(1)}, {'id': 1, **row(2)}]]
        for before in cases:
            with self.subTest(before=before):
                client = MemoryClient([])
                with self.assertRaises(ContractError):
                    client.run([row(1, 3)], before=before)
                self.assertEqual(client.calls, [])

    def test_extra_rows_are_retained_and_reported(self):
        client = MemoryClient([row(1), row(2)])
        self.assertTrue(client.run([row(1)])['stale'])
        self.assertEqual(len(client.rows), 2)

    def test_zero_rows_still_have_a_readback(self):
        client = MemoryClient([])
        self.assertFalse(client.run([])['stale'])
        self.assertEqual([v for v, _ in client.calls], ['filter'])
