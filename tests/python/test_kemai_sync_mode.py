import unittest

from kemai_pipeline.field_catalog import classify, resolve
from kemai_pipeline.sync_mode import (
    resolve_mode, detail_demand, coverage_annotations, estimate_db_reads, db_budget)


def groups_for(fields, stream):
    return classify(resolve(fields, stream)['resolved'])


class ResolveModeTests(unittest.TestCase):
    def test_default_is_list(self):
        self.assertEqual(resolve_mode(),
                         {'sync_mode': 'list', 'mode_basis': 'default', 'legacy_flags': []})

    def test_legacy_list_only_maps_to_list(self):
        mode = resolve_mode(list_only=True)
        self.assertEqual(mode['sync_mode'], 'list')
        self.assertEqual(mode['mode_basis'], 'legacy_list_only')

    def test_legacy_include_details_maps_to_full_purchase_scope(self):
        mode = resolve_mode(include_details=True)
        self.assertEqual(mode['sync_mode'], 'full')
        self.assertEqual(mode['mode_basis'], 'legacy_include_details')

    def test_legacy_required_fields_imply_on_demand(self):
        mode = resolve_mode(required_fields=['quantity'])
        self.assertEqual(mode['sync_mode'], 'on_demand')
        self.assertEqual(mode['mode_basis'], 'implicit_from_required_fields')

    def test_same_mode_legacy_flag_is_allowed(self):
        mode = resolve_mode(sync_mode='list', list_only=True)
        self.assertEqual(mode['sync_mode'], 'list')

    def test_contradictions_fail(self):
        with self.assertRaises(ValueError):
            resolve_mode(list_only=True, include_details=True)
        with self.assertRaises(ValueError):
            resolve_mode(sync_mode='on_demand', list_only=True)
        with self.assertRaises(ValueError):
            resolve_mode(sync_mode='list', include_details=True)
        with self.assertRaises(ValueError):
            resolve_mode(sync_mode='wibble')


class DetailDemandTests(unittest.TestCase):
    def test_list_with_detail_fields_is_a_contradiction(self):
        groups = groups_for(['purchase.lines.quantity'], 'purchase-orders')
        with self.assertRaises(ValueError):
            detail_demand(resolve_mode(sync_mode='list'), groups)

    def test_on_demand_without_demand_executes_as_list_with_note(self):
        demand = detail_demand(resolve_mode(sync_mode='on_demand'), groups_for([], None))
        self.assertEqual(demand['detail_demand'], 'none')
        self.assertIn('executes as list', demand['note'])

    def test_on_demand_with_line_field_is_bounded(self):
        mode = resolve_mode(sync_mode='on_demand')
        demand = detail_demand(mode, groups_for(['purchase.lines.quantity'], 'purchase-orders'))
        self.assertEqual(demand['detail_demand'], 'bounded')
        self.assertEqual(demand['detail_fields'], ['purchase.lines.quantity'])

    def test_full_requires_range_scope_and_budget(self):
        mode = resolve_mode(sync_mode='full')
        groups = groups_for(['purchase.lines.quantity'], 'purchase-orders')
        with self.assertRaises(ValueError):
            detail_demand(mode, groups)  # no range
        with self.assertRaises(ValueError):
            detail_demand(mode, groups, explicit_range=True)  # no scope
        with self.assertRaises(ValueError):
            detail_demand(mode, groups, explicit_range=True,
                          detail_scope={'days': ['2026-11-07']})  # no budget
        demand = detail_demand(mode, groups, explicit_range=True,
                               detail_scope={'days': ['2026-11-07']}, detail_budget=10)
        self.assertEqual(demand['detail_budget'], 10)

    def test_paused_fields_block_every_mode(self):
        groups = groups_for(['retail.orders.payments', 'retail.orders.sale_amount'],
                            'retail-orders')
        for mode_info in (resolve_mode(sync_mode='list'),
                          resolve_mode(sync_mode='on_demand'),
                          resolve_mode(sync_mode='full')):
            demand = detail_demand(mode_info, groups, explicit_range=True,
                                   detail_scope={'days': ['2026-11-06']}, detail_budget=5)
            self.assertEqual(demand['detail_demand'], 'blocked')
            self.assertEqual(demand['reason'], 'paused_by_user')
            self.assertEqual(demand['paused_fields'], ['retail.orders.payments'])


class CoverageAnnotationTests(unittest.TestCase):
    def test_block_separates_list_and_detail(self):
        mode = resolve_mode(sync_mode='on_demand')
        demand = detail_demand(mode, groups_for(['purchase.lines.quantity'], 'purchase-orders'))
        block = coverage_annotations(mode, demand, list_scope='2026-11-06..2026-11-07',
                                     detail_coverage='bounded lines for selected days')
        self.assertEqual(block['sync_mode'], 'on_demand')
        self.assertIn('day-window', block['list_coverage']['evidence'])
        self.assertEqual(block['detail_demand'], 'bounded')
        self.assertIn('source_observed_at', block['timing_semantics'])
        self.assertIn('committed_at', block['timing_semantics'])


class DbBudgetTests(unittest.TestCase):
    def test_estimate_matches_chunked_reads(self):
        self.assertEqual(estimate_db_reads(0)['total_max'], 0)
        self.assertEqual(estimate_db_reads(89)['total_max'], 2)
        self.assertEqual(estimate_db_reads(100)['total_max'], 2)
        self.assertEqual(estimate_db_reads(18386)['total_max'], 368)
        with self.assertRaises(ValueError):
            estimate_db_reads(-1)

    def test_budget_verdict_and_split_strategy(self):
        verdict = db_budget(estimate_db_reads(89), 400)
        self.assertFalse(verdict['over_budget'])
        self.assertEqual(verdict['strategy'], 'within budget')
        verdict = db_budget(estimate_db_reads(18386), 200)
        self.assertTrue(verdict['over_budget'])
        self.assertIn('split into explicit-id plans', verdict['strategy'])
        with self.assertRaises(ValueError):
            db_budget(estimate_db_reads(10), 0)


if __name__ == '__main__':
    unittest.main()
