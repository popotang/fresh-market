import unittest

from kemai_pipeline.field_catalog import (
    CATALOG, LEGACY_DEFAULTS, PAUSED, SMALL_SAMPLE, NOT_ACCEPTED,
    resolve, classify, routing_fields)
from kemai_pipeline.capabilities import CAPABILITIES


class CatalogFactsTests(unittest.TestCase):
    def test_catalog_paths_are_unique_and_dotted(self):
        self.assertEqual(len(CATALOG), len(set(CATALOG)))
        self.assertTrue(all('.' in p for p in CATALOG))

    def test_plan_table_examples_match(self):
        self.assertTrue(CATALOG['purchase.header.amount'].satisfied_by_list)
        for path in ('purchase.lines.quantity', 'purchase.lines.unit_price',
                     'purchase.lines.amount', 'purchase.lines.tax_amount'):
            spec = CATALOG[path]
            self.assertFalse(spec.satisfied_by_list)
            self.assertEqual(spec.detail_handler, 'lines_only')
            self.assertEqual(spec.detail_status, 'verified')
        self.assertTrue(CATALOG['retail.orders.sale_amount'].satisfied_by_list)
        self.assertTrue(CATALOG['retail.sales.quantity'].satisfied_by_list)
        self.assertEqual(CATALOG['retail.orders.payments'].detail_status, PAUSED)
        self.assertEqual(CATALOG['retail.orders.details'].detail_status, PAUSED)
        self.assertTrue(CATALOG['product.master.item_name'].satisfied_by_list)
        self.assertTrue(CATALOG['product.master.barcode'].satisfied_by_list)
        for tab in ('stock', 'retail_price', 'supply', 'barcode'):
            spec = CATALOG[f'product.tabs.{tab}']
            self.assertFalse(spec.satisfied_by_list)
            self.assertEqual(spec.detail_handler, 'product_object')

    def test_list_field_presence_is_grounded_in_artifacts(self):
        # Field-name evidence from local inventories: list rows lack plain
        # unitId and gmtModified, so those two master fields stay detail-only.
        for path in ('product.master.source_unit_id', 'product.master.source_modified_at'):
            spec = CATALOG[path]
            self.assertFalse(spec.satisfied_by_list)
            self.assertEqual(spec.detail_handler, 'product_object')
        for path in ('product.master.barcode', 'product.master.retail_price',
                     'product.master.source_approved_at', 'product.master.sub_name'):
            self.assertTrue(CATALOG[path].satisfied_by_list)

    def test_tab_refresh_rules(self):
        for tab in ('stock', 'retail_price', 'delivery_price', 'promotion', 'history_price'):
            self.assertEqual(CATALOG[f'product.tabs.{tab}'].refresh, 'observation_ttl')
        for tab in ('supply', 'barcode', 'grading', 'scope', 'cut'):
            self.assertEqual(CATALOG[f'product.tabs.{tab}'].refresh, 'source_version')

    def test_tab_acceptance_only_small_sample_or_pending(self):
        self.assertEqual(CATALOG['product.tabs.supply'].detail_status, SMALL_SAMPLE)
        self.assertEqual(CATALOG['product.tabs.barcode'].detail_status, SMALL_SAMPLE)
        self.assertEqual(CATALOG['product.tabs.grading'].detail_status, NOT_ACCEPTED)

    def test_promote_sharing_fields_are_list_satisfied_but_stream_starts_paused(self):
        for path in ('retail.promote_sharing.amount', 'retail.promote_sharing.promotion_code',
                     'retail.promote_sharing.member_card_no'):
            self.assertTrue(CATALOG[path].satisfied_by_list)
            self.assertEqual(CATALOG[path].storage, 'retail_promote_shares')
        capability = CAPABILITIES['retail-promote-sharing']
        self.assertTrue(capability.paused)
        self.assertEqual(capability.source_key_fields, ('source_row_hash', 'occurrence_no'))


class ResolveTests(unittest.TestCase):
    def test_canonical_paths_pass_through_with_stream_context(self):
        result = resolve(['purchase.header.amount'], 'purchase-orders')
        self.assertEqual([s.path for s in result['resolved']], ['purchase.header.amount'])
        self.assertEqual(result['ambiguous'], {})
        self.assertEqual(result['unknown'], [])

    def test_canonical_path_with_incompatible_stream_is_unknown(self):
        result = resolve(['purchase.header.amount'], 'retail-sales')
        self.assertEqual(result['resolved'], [])
        self.assertEqual(result['unknown'], ['purchase.header.amount'])

    def test_unknown_dotted_path_never_invented(self):
        result = resolve(['purchase.header.wibble'], 'purchase-orders')
        self.assertEqual(result['unknown'], ['purchase.header.wibble'])

    def test_flat_quantity_is_stream_disambiguated(self):
        self.assertEqual(resolve(['quantity'], 'purchase-orders')['resolved'][0].path,
                         'purchase.lines.quantity')
        self.assertFalse(resolve(['quantity'], 'purchase-orders')['resolved'][0].satisfied_by_list)
        self.assertEqual(resolve(['quantity'], 'retail-sales')['resolved'][0].path,
                         'retail.sales.quantity')
        self.assertTrue(resolve(['quantity'], 'retail-sales')['resolved'][0].satisfied_by_list)
        self.assertEqual(resolve(['quantity'], 'purchase-receive-history')['resolved'][0].path,
                         'purchase.history.quantity')

    def test_flat_amount_keeps_legacy_header_semantics_on_purchase(self):
        spec = resolve(['amount'], 'purchase-orders')['resolved'][0]
        self.assertEqual(spec.path, LEGACY_DEFAULTS['amount'])
        self.assertTrue(spec.satisfied_by_list)
        line = resolve(['purchase.lines.amount'], 'purchase-orders')['resolved'][0]
        self.assertFalse(line.satisfied_by_list)

    def test_flat_amount_without_stream_is_ambiguous_not_guessed(self):
        result = resolve(['amount'], None)
        self.assertEqual(result['resolved'], [])
        self.assertIn('purchase.header.amount', result['ambiguous']['amount'])
        self.assertIn('purchase.lines.amount', result['ambiguous']['amount'])

    def test_flat_quantity_without_stream_is_ambiguous(self):
        result = resolve(['quantity'], None)
        self.assertEqual(result['resolved'], [])
        self.assertGreaterEqual(len(result['ambiguous']['quantity']), 3)

    def test_shared_flat_names_need_stream_context(self):
        for flat in ('source_branch_id', 'payload', 'sold_at'):
            self.assertIn(flat, resolve([flat], None)['ambiguous'],
                          f'{flat} should be ambiguous without a stream')
            self.assertTrue(resolve([flat], 'retail-sales')['resolved'])

    def test_truly_unknown_flat_name(self):
        result = resolve(['wibble'], 'retail-sales')
        self.assertEqual(result['unknown'], ['wibble'])

    def test_paused_paths_resolve_and_classify_as_paused(self):
        result = resolve(['retail.orders.payments', 'retail.orders.sale_amount'], 'retail-orders')
        groups = classify(result['resolved'])
        self.assertEqual(groups['paused'], ['retail.orders.payments'])
        self.assertEqual(groups['satisfied_by_list'], ['retail.orders.sale_amount'])


class ClassifyTests(unittest.TestCase):
    def test_groups_are_disjoint(self):
        result = resolve(['purchase.header.amount', 'purchase.lines.quantity',
                          'retail.orders.payments'], 'purchase-orders')
        # retail path is incompatible with the purchase context:
        self.assertEqual(result['unknown'], ['retail.orders.payments'])
        groups = classify(result['resolved'])
        self.assertEqual(groups['satisfied_by_list'], ['purchase.header.amount'])
        self.assertEqual(groups['needs_detail'], ['purchase.lines.quantity'])
        self.assertEqual(groups['paused'], [])


class RoutingFieldsTests(unittest.TestCase):
    def test_satisfied_fields_map_to_flat_leaves(self):
        specs = resolve(['purchase.header.amount', 'purchase.header.sheet_no'],
                        'purchase-orders')['resolved']
        self.assertEqual(routing_fields(specs, 'purchase-orders'), ['amount', 'sheet_no'])

    def test_any_line_field_maps_to_quantity_trigger(self):
        specs = resolve(['purchase.lines.unit_price'], 'purchase-orders')['resolved']
        self.assertEqual(routing_fields(specs, 'purchase-orders'), ['quantity'])

    def test_paused_and_product_fields_never_reach_day_router(self):
        specs = resolve(['retail.orders.payments'], 'retail-orders')['resolved']
        self.assertEqual(routing_fields(specs, 'retail-orders'), [])
        specs = resolve(['product.tabs.stock'], 'item_list')['resolved']
        self.assertEqual(routing_fields(specs, 'purchase-orders'), [])


if __name__ == '__main__':
    unittest.main()
