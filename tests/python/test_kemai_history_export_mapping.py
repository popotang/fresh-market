import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

from kemai_pipeline.export_mappings import HISTORY_EXPORT_FIELDS, MAPPING_VERSION
from kemai_pipeline.mapping_verification import load_verification
from kemai_pipeline.xlsx_import import XlsxImportError

from scripts.verify_kemai_history_export_mapping import MappingVerificationError, main, verify_sample

STREAM = 'purchase-receive-history'
HEADERS = list(HISTORY_EXPORT_FIELDS.keys())
COL = {field: index for index, field in enumerate(HISTORY_EXPORT_FIELDS.values())}


def make_xlsx(path, rows, headers=None):
    book = Workbook()
    sheet = book.active
    headers = HEADERS if headers is None else headers
    for index, header in enumerate(headers, start=1):
        sheet.cell(row=3, column=index, value=header)
    for offset, row in enumerate(rows):
        values = [None] * len(headers)
        for field, value in row.items():
            values[COL[field]] = value
        for index, value in enumerate(values, start=1):
            sheet.cell(row=4 + offset, column=index, value=value)
    book.save(path)


def key_row(sheet_no, sequence, item_code='ITEM-1'):
    return {'sheetNo': sheet_no, 'sequence': sequence, 'itemCode': item_code}


class VerifyHistoryExportMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sample = self.root / 'sample.xlsx'
        self.artifact = self.root / 'export-mapping' / f'{STREAM}.json'

    def run_main(self, *extra):
        return main(['--stream', STREAM, '--sample', str(self.sample),
                     '--artifact-root', str(self.root), *extra])

    def test_valid_sample_writes_loadable_artifact(self):
        make_xlsx(self.sample, [key_row('CG0001', 1), key_row('CG0001', '2.0')])
        self.assertEqual(self.run_main(), 0)
        self.assertTrue(self.artifact.is_file())
        loaded = load_verification(STREAM, self.root)
        self.assertIsInstance(loaded, dict)
        self.assertEqual(loaded['sample_rows'], 2)
        self.assertEqual(loaded['key_coverage'], 1.0)
        self.assertEqual(loaded['mapping_version'], MAPPING_VERSION)
        self.assertEqual(oct(self.artifact.stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct(self.artifact.parent.stat().st_mode & 0o777), '0o700')

    def test_forged_mapping_with_id_in_key_rejected(self):
        make_xlsx(self.sample, [key_row('CG0001', 1)])
        forged = dict(HISTORY_EXPORT_FIELDS)
        forged['单据编号'] = 'id'  # key would ride on the unstable id column
        with self.assertRaises((MappingVerificationError, XlsxImportError, ValueError)):
            verify_sample(STREAM, self.sample, artifact_root=self.root, mapping=forged)
        self.assertFalse(self.artifact.exists())
        with mock.patch('scripts.verify_kemai_history_export_mapping.HISTORY_EXPORT_FIELDS', forged):
            self.assertEqual(self.run_main(), 2)
        self.assertFalse(self.artifact.exists())

    def test_header_drift_writes_no_artifact_and_fails(self):
        headers = list(HEADERS)
        headers[8] = '商品编码 '
        make_xlsx(self.sample, [key_row('CG0001', 1)], headers=headers)
        self.assertEqual(self.run_main(), 2)
        self.assertFalse(self.artifact.exists())

    def test_duplicate_keys_write_no_artifact(self):
        make_xlsx(self.sample, [key_row('CG0001', 1), key_row('CG0001', '1')])
        self.assertEqual(self.run_main(), 2)
        self.assertFalse(self.artifact.exists())

    def test_limit_verifies_subset(self):
        make_xlsx(self.sample, [key_row('CG0001', i) for i in range(1, 6)])
        self.assertEqual(self.run_main('--limit', '3'), 0)
        loaded = load_verification(STREAM, self.root)
        self.assertEqual(loaded['sample_rows'], 3)


if __name__ == '__main__':
    unittest.main()
