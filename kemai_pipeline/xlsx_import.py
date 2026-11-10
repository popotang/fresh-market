"""Strict offline parser for Kemai purchase-receive-history XLSX exports.

openpyxl is imported only inside functions so that importing this module never
pulls it in (asserted by tests). All validation finishes before the first row
is produced: rows are materialized into a list, the non-empty row count is
checked for strict equality with expected_rows, and every stable key is
computed and de-duplicated up front. Memory tradeoff: one dict per data row
stays resident until the caller finishes iterating; export task sizes are
bounded by the source's export limits, so this is acceptable, and a two-pass
streaming variant could replace it later without changing the interface.
Stable keys reuse kemai_pipeline.normalize.history_key verbatim (including its
Decimal sequence normalization), never a copied implementation.
"""
from pathlib import Path
import zipfile

from .normalize import history_key

ZIP_SIGNATURE=b'PK\x03\x04'

# Resource budgets (fail closed, checked from the zip central directory before
# openpyxl sees the file, so no decompression happens first): no member may
# exceed 32MB uncompressed, the archive may not exceed 64MB uncompressed in
# total, and no member larger than 1MB uncompressed may compress better than
# 100:1 (the 1MB floor keeps small legitimately compressible exports safe).
# While parsing, more than expected_rows+1000 non-empty data rows aborts the
# parse immediately instead of materializing the whole sheet.
MAX_MEMBER_UNCOMPRESSED_BYTES=32*1024*1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES=64*1024*1024
MAX_COMPRESSION_RATIO=100
COMPRESSION_RATIO_MIN_BYTES=1024*1024
MAX_ROW_SURPLUS=1000


class XlsxImportError(Exception):
    pass


def _is_empty(values):
    return all(value is None or value=='' for value in values)


def _looks_like_summary(values):
    """Kemai exports append one trailing 合计 row: first cell '合计', no sheetNo."""
    return (len(values)>1 and str(values[0] or '').strip()=='合计'
            and str(values[1] or '').strip()=='')


def _drop_trailing_summary(numbered_rows):
    """Drop the single trailing 合计 summary row; reject summary rows elsewhere.

    numbered_rows is a list of (row_number, values) pairs of non-empty rows.
    Only the final non-empty row may be a summary; a summary anywhere else, or
    more than one, is a format violation and fails closed.
    """
    if numbered_rows and _looks_like_summary(numbered_rows[-1][1]):
        numbered_rows=numbered_rows[:-1]
    for row_number,values in numbered_rows:
        if _looks_like_summary(values):
            raise XlsxImportError(f'history export has a misplaced summary row at sheet row {row_number}')
    return numbered_rows


def _assert_zip(path):
    with Path(path).open('rb') as stream:
        signature=stream.read(4)
    if not signature.startswith(ZIP_SIGNATURE):
        raise XlsxImportError('history export must be strict XLSX/ZIP; other formats are rejected')


def _assert_archive_budget(path):
    try:
        with zipfile.ZipFile(path) as archive:
            infos=archive.infolist()
    except zipfile.BadZipFile as exc:
        raise XlsxImportError('history export must be strict XLSX/ZIP; other formats are rejected') from exc
    total=0
    for info in infos:
        if info.file_size>MAX_MEMBER_UNCOMPRESSED_BYTES:
            raise XlsxImportError('history export archive member exceeds the uncompressed size budget')
        if (info.file_size>COMPRESSION_RATIO_MIN_BYTES and info.compress_size>0
                and info.file_size/info.compress_size>MAX_COMPRESSION_RATIO):
            raise XlsxImportError('history export compression ratio exceeds the safety budget')
        total+=info.file_size
    if total>MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise XlsxImportError('history export uncompressed size exceeds the safety budget')


def _open_sheet(path):
    from openpyxl import load_workbook
    _assert_zip(path)
    _assert_archive_budget(path)
    try:
        book=load_workbook(path,read_only=True,data_only=True)
    except XlsxImportError:
        raise
    except Exception as exc:
        raise XlsxImportError(f'history export is not a readable XLSX workbook: {type(exc).__name__}') from exc
    return book,book.active


def _validated_headers(sheet,mapping):
    rows=sheet.iter_rows(min_row=3,max_row=3,values_only=True)
    try:
        header_row=next(rows)
    except StopIteration:
        raise XlsxImportError('history export header row 3 is missing') from None
    headers=[str(value or '') for value in header_row]
    expected=list(mapping.keys())
    if headers!=expected:
        raise XlsxImportError('history export header row 3 does not match the verified mapping '
                              f'in name and order (expected {len(expected)} columns, got {len(headers)})')
    return list(mapping.values())


def _is_empty(values):
    return all(value is None or value=='' for value in values)


def count_history_rows(path):
    """Number of non-empty data rows (row 4 onward, trailing 合计 row excluded); raises XlsxImportError."""
    book,sheet=_open_sheet(path)
    try:
        numbered=[(i,values) for i,values in
                  enumerate(sheet.iter_rows(min_row=4,values_only=True),start=4)
                  if not _is_empty(values)]
        return len(_drop_trailing_summary(numbered))
    except XlsxImportError:
        raise
    except Exception as exc:
        raise XlsxImportError(f'history export rows are unreadable: {type(exc).__name__}') from exc
    finally:
        book.close()


def parse_history_xlsx(path,*,expected_rows,mapping):
    """逐行 yield dict（英文字段→值）。失败抛 XlsxImportError，不产出任何部分结果的使用承诺由调用方保证。

    Validation completes before this function returns: it returns an iterator
    over a fully validated, materialized row list (see module docstring for the
    memory tradeoff). Header must be row 3 exactly and match mapping's Chinese
    keys in name and order; data starts at row 4; fully empty rows are skipped;
    the remaining row count must equal expected_rows; every row needs a
    non-empty sheetNo plus the history_key triple; duplicate source_key fails.
    """
    if not isinstance(expected_rows,int) or isinstance(expected_rows,bool) or expected_rows<0:
        raise XlsxImportError('expected_rows must be a nonnegative integer')
    book,sheet=_open_sheet(path)
    try:
        fields=_validated_headers(sheet,mapping)
        numbered=[]
        for row_number,values in enumerate(sheet.iter_rows(min_row=4,values_only=True),start=4):
            if _is_empty(values):
                continue
            numbered.append((row_number,values))
            if len(numbered)>expected_rows+MAX_ROW_SURPLUS+1:
                raise XlsxImportError(f'history export data rows exceed expected_rows+{MAX_ROW_SURPLUS}; '
                                      'parsing stopped early instead of materializing the sheet')
        numbered=_drop_trailing_summary(numbered)
        if len(numbered)>expected_rows+MAX_ROW_SURPLUS:
            raise XlsxImportError(f'history export data rows exceed expected_rows+{MAX_ROW_SURPLUS}; '
                                  'parsing stopped early instead of materializing the sheet')
        rows=[{field:value for field,value in zip(fields,values)} for _,values in numbered]
    except XlsxImportError:
        book.close()
        raise
    except Exception as exc:
        book.close()
        raise XlsxImportError(f'history export rows are unreadable: {type(exc).__name__}') from exc
    book.close()
    if len(rows)!=expected_rows:
        raise XlsxImportError(f'history export row count mismatch: parsed={len(rows)} expected={expected_rows}')
    keys=set()
    for index,row in enumerate(rows):
        sheet_no=row.get('sheetNo')
        if sheet_no is None or str(sheet_no).strip()=='':
            raise XlsxImportError(f'history export data row {index+4} has an empty sheetNo')
        try:
            key=history_key(row)
        except (ValueError,TypeError) as exc:
            raise XlsxImportError(f'history export data row {index+4} lacks a complete stable key: {exc}') from exc
        if key in keys:
            raise XlsxImportError(f'history export contains a duplicate stable line key at data row {index+4}')
        keys.add(key)
    return iter(rows)
