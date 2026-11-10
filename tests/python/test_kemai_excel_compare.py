import json
from pathlib import Path
import tempfile
import unittest
from scripts.kemai_excel_compare import compare
from kemai_pipeline.artifacts import CaptureStore, atomic_json
from kemai_pipeline.normalize import normalize
from tests.python.test_kemai_pipeline_export_data_workflow import make_xlsx, sample_rows


class ExcelCompareTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.xlsx=self.root/'sample.xlsx'
        rows=sample_rows()
        for r in rows:r.update(branchCode='b',supplierCode='s',storeCode='w')
        make_xlsx(self.xlsx,rows)
        self.entity={'id':'100','sheetNo':'CG0001','sumAmt':1500000,'version':'1',
                     'branchCode':'b','supplierCode':'s','storeCode':'w',
                     'details':[{'id':str(i),'sequence':r['sequence'],'itemCode':r['itemCode'],
                                 'qty':r['qty']*10000,'amt':r['amt']*10000} for i,r in enumerate(rows)]}

    def add_parent(self,tenant='t'):
        store=CaptureStore(self.root)
        state=store.create(tenant=tenant,stream='purchase-receipts',window_key='2026-11-06',
                           source_key_version='purchase-id-v1',mode='final')
        store.append_page(state,[normalize('purchase-receipts',self.entity)])

    def test_parent_and_line_comparison_is_offline_not_import_acceptance(self):
        self.add_parent();r=compare(self.xlsx,self.root,'t')
        self.assertEqual(r['parent_comparison']['unique_parent'],1)
        self.assertEqual(r['parent_comparison']['parent_amount_matches'],1)
        self.assertEqual(r['parent_comparison']['matching_detail_numeric_rows'],2)
        self.assertFalse(r['ready_for_canonical_line_import'])
        self.assertEqual(r['db_writes'],0)

    def test_other_tenant_and_ambiguous_versions_are_not_matched(self):
        self.add_parent('other');r=compare(self.xlsx,self.root,'t')
        self.assertEqual(r['parent_comparison']['missing_parent'],1)
        self.add_parent();self.entity['version']='2';self.add_parent()
        r=compare(self.xlsx,self.root,'t');self.assertEqual(r['parent_comparison']['ambiguous_parent'],1)

    def test_receipt_tampering_is_rejected(self):
        atomic_json(Path(str(self.xlsx)+'.meta.json'),{'sha256':'bad','rows':2})
        with self.assertRaises(ValueError):compare(self.xlsx,self.root,'t')
