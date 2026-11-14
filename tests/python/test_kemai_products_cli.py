import importlib.util
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location(
    'kemai_products_cli', ROOT / 'scripts' / 'kemai_products.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class DummyParser:
    def error(self, message):
        raise SystemExit(message)


def args(**overrides):
    base = dict(detail_only=False, section=None, required_fields=[], sync_mode=None,
                list_only=False, ids=None, inventory=None, limit=10)
    base.update(overrides)
    return Namespace(**base)


class ProductsCliModeTests(unittest.TestCase):
    def plan(self, **overrides):
        return cli.plan_mode(args(**overrides), DummyParser())

    def test_bare_plan_keeps_legacy_detail_semantics(self):
        mode, list_only, sections = self.plan()
        self.assertEqual(mode['sync_mode'], 'on_demand')
        self.assertEqual(mode['mode_basis'], 'legacy_detail_plan')
        self.assertFalse(list_only)
        self.assertEqual(sections, cli.DEFAULT_SECTIONS)

    def test_legacy_list_only_maps_to_list(self):
        mode, list_only, sections = self.plan(list_only=True, inventory='abc')
        self.assertEqual(mode['sync_mode'], 'list')
        self.assertEqual(mode['mode_basis'], 'legacy_list_only')
        self.assertTrue(list_only)
        self.assertEqual(sections, [])

    def test_explicit_list_mode(self):
        mode, list_only, sections = self.plan(sync_mode='list', inventory='abc')
        self.assertEqual(mode['sync_mode'], 'list')
        self.assertTrue(list_only)

    def test_list_mode_without_inventory_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.plan(sync_mode='list')

    def test_legacy_list_only_without_inventory_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.plan(list_only=True)

    def test_on_demand_tab_field_derives_sections(self):
        mode, list_only, sections = self.plan(sync_mode='on_demand', ids=['12'],
                                               required_fields=['product.tabs.stock'])
        self.assertEqual(mode['detail_demand'], 'bounded')
        self.assertEqual(mode['detail_fields'], ['product.tabs.stock'])
        self.assertFalse(list_only)
        self.assertEqual(sections, ['stock'])

    def test_on_demand_without_demand_degrades_to_list_with_note(self):
        mode, list_only, sections = self.plan(sync_mode='on_demand', inventory='abc')
        self.assertEqual(mode['detail_demand'], 'none')
        self.assertIn('executes as list', mode['note'])
        self.assertTrue(list_only)
        self.assertEqual(sections, [])

    def test_on_demand_with_only_list_fields_degrades_to_list(self):
        mode, list_only, sections = self.plan(sync_mode='on_demand', inventory='abc',
                                              required_fields=['product.master.item_name',
                                                               'product.master.barcode'])
        self.assertEqual(mode['detail_demand'], 'none')
        self.assertTrue(list_only)

    def test_on_demand_with_explicit_objects_stays_bounded(self):
        # --id is an explicit detail-object selection; --inventory alone is not
        # (a catalog source never legitimizes detail by itself).
        mode, list_only, sections = self.plan(sync_mode='on_demand', ids=['12'])
        self.assertEqual(mode['detail_demand'], 'bounded')
        self.assertFalse(list_only)
        mode, list_only, sections = self.plan(sync_mode='on_demand', inventory='abc')
        self.assertEqual(mode['detail_demand'], 'none')
        self.assertTrue(list_only)

    def test_on_demand_satisfied_fields_with_objects_degrades_to_list(self):
        mode, list_only, sections = self.plan(sync_mode='on_demand', inventory='abc',
                                              required_fields=['product.master.item_name'])
        self.assertEqual(mode['detail_demand'], 'none')
        self.assertTrue(list_only)
        self.assertEqual(sections, [])

    def test_master_detail_field_scopes_sections_exactly(self):
        # A master-only detail demand must not silently widen into the six
        # default tabs; the detail phase covers it with no sections at all.
        mode, list_only, sections = self.plan(sync_mode='on_demand',
                                              required_fields=['product.master.source_modified_at'])
        self.assertEqual(mode['detail_demand'], 'bounded')
        self.assertEqual(sections, [])

    def test_mixed_master_and_tab_demand_scopes_sections_to_tabs(self):
        mode, list_only, sections = self.plan(
            sync_mode='on_demand', ids=['12'],
            required_fields=['product.master.source_modified_at', 'product.tabs.stock'])
        self.assertEqual(sections, ['stock'])

    def test_master_detail_only_field_demands_detail(self):
        mode, list_only, sections = self.plan(sync_mode='on_demand',
                                              required_fields=['product.master.source_modified_at'])
        self.assertEqual(mode['detail_fields'], ['product.master.source_modified_at'])
        self.assertFalse(list_only)

    def test_list_mode_with_detail_field_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.plan(sync_mode='list', required_fields=['product.tabs.stock'])

    def test_list_mode_with_section_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.plan(sync_mode='list', section=['stock'])

    def test_detail_only_with_tab_field_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.plan(required_fields=['product.tabs.stock'], detail_only=True)

    def test_full_without_object_scope_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.plan(sync_mode='full')

    def test_full_with_explicit_ids_is_bounded_by_limit(self):
        mode, list_only, sections = self.plan(sync_mode='full', ids=['12'], limit=5)
        self.assertEqual(mode['detail_demand'], 'bounded')
        self.assertEqual(mode['detail_budget'], 5)
        self.assertFalse(list_only)

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.plan(required_fields=['wibble'])

    def test_paused_field_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.plan(required_fields=['retail.orders.payments'])

    def test_contradictory_mode_and_legacy_flag_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.plan(sync_mode='on_demand', list_only=True)


if __name__ == '__main__':
    unittest.main()
