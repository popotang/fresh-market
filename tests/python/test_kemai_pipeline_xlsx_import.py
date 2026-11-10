import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from kemai_pipeline.export_mappings import HISTORY_EXPORT_FIELDS
from kemai_pipeline.normalize import history_key
from kemai_pipeline.xlsx_import import XlsxImportError, count_history_rows, parse_history_xlsx

HEADERS = list(HISTORY_EXPORT_FIELDS.keys())
COL = {field: index for index, field in enumerate(HISTORY_EXPORT_FIELDS.values())}


def make_xlsx(path, rows, headers=None, header_row=3):
    """Build a miniature history-export workbook: rows land at 4.., header at header_row."""
    book = Workbook()
    sheet = book.active
    headers = HEADERS if headers is None else headers
    for index, header in enumerate(headers, start=1):
        sheet.cell(row=header_row, column=index, value=header)
    for offset, row in enumerate(rows):
        values = [None] * len(headers)
        for field, value in row.items():
            values[COL[field]] = value
        for index, value in enumerate(values, start=1):
            sheet.cell(row=header_row + 1 + offset, column=index, value=value)
    book.save(path)


def key_row(sheet_no, sequence, item_code='ITEM-1'):
    return {'sheetNo': sheet_no, 'sequence': sequence, 'itemCode': item_code}


class ParseHistoryXlsxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'sample.xlsx'

    def parse(self, expected_rows, mapping=None):
        return list(parse_history_xlsx(self.path, expected_rows=expected_rows,
                                       mapping=mapping or HISTORY_EXPORT_FIELDS))

    def test_valid_export_yields_english_field_dicts(self):
        make_xlsx(self.path, [key_row('CG0001', 1), key_row('CG0001', 2)])
        rows = self.parse(expected_rows=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['sheetNo'], 'CG0001')
        self.assertEqual(rows[0]['itemCode'], 'ITEM-1')
        self.assertIn('sequence', rows[0])
        self.assertNotIn('单据编号', rows[0])

    def test_header_mismatch_fails_before_any_row(self):
        headers = list(HEADERS)
        headers[0] = '单据编号X'
        make_xlsx(self.path, [key_row('CG0001', 1)], headers=headers)
        with self.assertRaises(XlsxImportError):
            self.parse(expected_rows=1)

    def test_header_on_row_2_fails(self):
        make_xlsx(self.path, [key_row('CG0001', 1)], header_row=2)
        with self.assertRaises(XlsxImportError):
            self.parse(expected_rows=1)

    def test_fewer_rows_than_expected_fails(self):
        make_xlsx(self.path, [key_row('CG0001', 1)])
        with self.assertRaises(XlsxImportError):
            self.parse(expected_rows=2)

    def test_more_rows_than_expected_fails(self):
        make_xlsx(self.path, [key_row('CG0001', 1), key_row('CG0001', 2)])
        with self.assertRaises(XlsxImportError):
            self.parse(expected_rows=1)

    def test_duplicate_source_key_fails(self):
        make_xlsx(self.path, [key_row('CG0001', 1), key_row('CG0001', '1.0')])
        with self.assertRaises(XlsxImportError):
            self.parse(expected_rows=2)

    def test_empty_sheet_no_fails(self):
        make_xlsx(self.path, [{'sheetNo': '', 'sequence': 1, 'itemCode': 'ITEM-1'}])
        with self.assertRaises(XlsxImportError):
            self.parse(expected_rows=1)

    def test_missing_item_code_and_id_fails(self):
        make_xlsx(self.path, [{'sheetNo': 'CG0001', 'sequence': 1, 'itemCode': None}])
        with self.assertRaises(XlsxImportError):
            self.parse(expected_rows=1)

    def test_sequence_representations_share_one_key(self):
        keys = []
        for sequence in ('1.0', 1, '1'):
            make_xlsx(self.path, [key_row('CG0001', sequence)])
            rows = self.parse(expected_rows=1)
            keys.append(history_key(rows[0]))
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(keys[1], keys[2])

    def test_module_import_does_not_pull_openpyxl(self):
        for name in [name for name in sys.modules
                     if name == 'openpyxl' or name.startswith('openpyxl.')
                     or name == 'kemai_pipeline.xlsx_import']:
            del sys.modules[name]
        try:
            import kemai_pipeline.xlsx_import  # noqa: F401
            self.assertNotIn('openpyxl', sys.modules)
        finally:
            import openpyxl  # restore for other tests in this process


class SummaryRowTests(unittest.TestCase):
    """Kemai exports append one trailing 合计 row; it is skipped, never counted."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'sample.xlsx'

    def make_with_summary(self, data_rows, summary_positions):
        book = Workbook()
        sheet = book.active
        for index, header in enumerate(HEADERS, start=1):
            sheet.cell(row=3, column=index, value=header)
        for offset, row in enumerate(data_rows):
            values = [None] * len(HEADERS)
            for field, value in row.items():
                values[COL[field]] = value
            for index, value in enumerate(values, start=1):
                sheet.cell(row=4 + offset, column=index, value=value)
        last_data_row = 3 + len(data_rows)
        for position in summary_positions:
            sheet.cell(row=last_data_row + position, column=1, value='合计')
            sheet.cell(row=last_data_row + position, column=5, value='123.45')
        book.save(self.path)

    def test_trailing_summary_row_is_skipped_and_not_counted(self):
        self.make_with_summary([key_row('CG0001', 1), key_row('CG0001', 2)],
                               summary_positions=[1])
        rows = list(parse_history_xlsx(self.path, expected_rows=2,
                                       mapping=HISTORY_EXPORT_FIELDS))
        self.assertEqual(len(rows), 2)
        self.assertEqual(count_history_rows(self.path), 2)

    def test_summary_row_not_in_final_position_fails(self):
        self.make_with_summary([key_row('CG0001', 1), key_row('CG0001', 2)],
                               summary_positions=[1])
        # A second summary-looking row after the first one: the first is no
        # longer trailing, so it must fail closed.
        book = Workbook()
        sheet = book.active
        # rebuild: summary, then a data row after it
        for index, header in enumerate(HEADERS, start=1):
            sheet.cell(row=3, column=index, value=header)
        sheet.cell(row=4, column=1, value='合计')
        sheet.cell(row=4, column=5, value='123.45')
        values = [None] * len(HEADERS)
        for field, value in key_row('CG0001', 1).items():
            values[COL[field]] = value
        for index, value in enumerate(values, start=1):
            sheet.cell(row=5, column=index, value=value)
        book.save(self.path)
        with self.assertRaisesRegex(XlsxImportError, 'misplaced summary row'):
            list(parse_history_xlsx(self.path, expected_rows=1,
                                    mapping=HISTORY_EXPORT_FIELDS))
        with self.assertRaisesRegex(XlsxImportError, 'misplaced summary row'):
            count_history_rows(self.path)

    def test_data_row_with_heji_prefix_but_sheet_no_is_not_summary(self):
        # A real data row whose 序号 cell reads 合计 but carries a sheetNo is
        # data, not a summary row.
        book = Workbook()
        sheet = book.active
        for index, header in enumerate(HEADERS, start=1):
            sheet.cell(row=3, column=index, value=header)
        values = [None] * len(HEADERS)
        for field, value in key_row('CG0001', 1).items():
            values[COL[field]] = value
        values[COL['rowNo']] = '合计'
        for index, value in enumerate(values, start=1):
            sheet.cell(row=4, column=index, value=value)
        book.save(self.path)
        rows = list(parse_history_xlsx(self.path, expected_rows=1,
                                       mapping=HISTORY_EXPORT_FIELDS))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['rowNo'], '合计')

    def test_non_zip_content_rejected(self):
        self.path.write_bytes(b'not a zip file at all')
        with self.assertRaises(XlsxImportError):
            list(parse_history_xlsx(self.path, expected_rows=0,
                                    mapping=HISTORY_EXPORT_FIELDS))


class ArchiveBudgetTests(unittest.TestCase):
    """F6: zip bombs and row floods are rejected before/while openpyxl parses."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'sample.xlsx'

    def parse(self, path, expected_rows):
        return list(parse_history_xlsx(path, expected_rows=expected_rows,
                                       mapping=HISTORY_EXPORT_FIELDS))

    def test_high_compression_ratio_zip_rejected_before_parsing(self):
        # 2MB of zeros deflates to a few KB: ratio far beyond the 100:1 budget.
        bomb = Path(self.temp.name) / 'bomb.xlsx'
        with zipfile.ZipFile(bomb, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('xl/worksheets/sheet1.xml', b'0' * (2 * 1024 * 1024))
        with self.assertRaisesRegex(XlsxImportError, 'compression ratio'):
            self.parse(bomb, expected_rows=0)

    def test_small_members_never_ratio_rejected(self):
        # Ratio judgment only applies above 1MB uncompressed: normal small
        # exports (whose members can compress well) always pass the budget.
        make_xlsx(self.path, [key_row('CG0001', 1), key_row('CG0001', 2)])
        rows = self.parse(self.path, expected_rows=2)
        self.assertEqual(len(rows), 2)

    def test_oversized_member_rejected(self):
        make_xlsx(self.path, [key_row('CG0001', 1)])
        with patch('kemai_pipeline.xlsx_import.MAX_MEMBER_UNCOMPRESSED_BYTES', 1):
            with self.assertRaisesRegex(XlsxImportError, 'member'):
                self.parse(self.path, expected_rows=1)

    def test_total_uncompressed_budget_rejected(self):
        make_xlsx(self.path, [key_row('CG0001', 1)])
        with patch('kemai_pipeline.xlsx_import.MAX_ARCHIVE_UNCOMPRESSED_BYTES', 1):
            with self.assertRaisesRegex(XlsxImportError, 'uncompressed size'):
                self.parse(self.path, expected_rows=1)

    def test_row_surplus_stops_parsing_early(self):
        rows = [key_row('CG0001', index + 1) for index in range(6)]
        make_xlsx(self.path, rows)
        with patch('kemai_pipeline.xlsx_import.MAX_ROW_SURPLUS', 2):
            # Stops at expected+surplus instead of materializing all rows and
            # only then reporting the count mismatch.
            with self.assertRaisesRegex(XlsxImportError, 'exceed'):
                self.parse(self.path, expected_rows=1)


if __name__ == '__main__':
    unittest.main()
