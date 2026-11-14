"""Retail promote-sharing list-row identity and standard-model mapping.

Source id/sheetDetailId can be null and the report may repeat identical rows.
Identity is therefore (tenant_id, source_row_hash, occurrence_no), where the
occurrence number is assigned per full-row hash in capture page order. Exact
duplicates are preserved, never silently merged.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .normalize import dump, scaled, text, timestamp

SHANGHAI = ZoneInfo('Asia/Shanghai')


def epoch_ms(row, key):
    """METADATA DATETIME fields require Unix milliseconds, not display strings."""
    value = timestamp(row, key)
    if value is None:
        return None
    parsed = datetime.fromisoformat(value).replace(tzinfo=SHANGHAI)
    return int(parsed.timestamp() * 1000)


def row_hash(row):
    if not isinstance(row, dict):
        raise ValueError('Promote-sharing row must be an object')
    return hashlib.sha256(dump(row).encode()).hexdigest()


def normalize_row(row, *, tenant_id, occurrence_no, capture_id, page_no, row_no, observed_at):
    """Map one source list row to the retail_promote_shares dataset."""
    if not text(row, 'sheetId') or not text(row, 'itemCode') or not text(row, 'type'):
        raise ValueError('Promote-sharing row lacks stable business dimensions')
    if not isinstance(occurrence_no, int) or occurrence_no < 1:
        raise ValueError('occurrence_no must be a positive integer')
    if not isinstance(page_no, int) or page_no < 1 or not isinstance(row_no, int) or row_no < 1:
        raise ValueError('page_no/row_no must be positive integers')
    source_hash = row_hash(row)
    names = {
        'source_order_id': 'sheetId', 'sheet_no': 'sheetNo', 'marketing_type': 'type',
        'promotion_id': 'promotionId', 'promotion_code': 'promotionCode',
        'activity_code': 'activityCode', 'activity_name': 'activityName',
        'unit_name': 'unitName', 'branch_code': 'branchCode', 'branch_name': 'branchName',
        'item_code': 'itemCode', 'item_subno': 'itemSubno', 'item_name': 'itemName',
        'item_size': 'itemSize', 'item_category_id': 'itemClsId',
        'item_category_code': 'itemClsCode', 'item_category_name': 'itemClsName',
        'item_brand_id': 'itemBrandId', 'item_brand_code': 'itemBrandCode',
        'item_brand_name': 'itemBrandName', 'member_card_no': 'memberCardNo',
        'member_grade_id': 'memberGradeId', 'member_grade_name': 'memberGradeName',
        'channel': 'channel',
    }
    data = {target: text(row, key) for target, key in names.items()}
    data.update(
        tenant_id=str(tenant_id), source_row_hash=source_hash, occurrence_no=occurrence_no,
        amount=scaled(row, 'amt'), original_amount=scaled(row, 'originalAmt'),
        sale_amount=scaled(row, 'saleAmt'), original_price=scaled(row, 'originalPrice'),
        quantity=scaled(row, 'qty'), unit_quantity=scaled(row, 'unitQty'),
        sold_at=epoch_ms(row, 'gmtSale'), source_created_at=epoch_ms(row, 'gmtCreate'),
        source_operation_at=epoch_ms(row, 'gmtOperation'),
        capture_id=capture_id, page_no=page_no, row_no=row_no,
        payload=dump(row), payload_hash=source_hash,
        captured_at=epoch_ms({'value': observed_at}, 'value'),
    )
    return data


def load_capture(root):
    """Verify a completed capture and yield normalized rows in stable page order."""
    root = Path(root)
    state = json.loads((root / 'manifest.json').read_text())
    if state.get('stream') != 'retail-promote-sharing' or state.get('phase') != 'complete':
        raise ValueError('Only a completed retail-promote-sharing capture can be backfilled')
    pages = state.get('pages')
    if not isinstance(pages, list):
        raise ValueError('Invalid promote-sharing page manifest')
    occurrences = {}
    for page_no, page in enumerate(pages, 1):
        name = page.get('file')
        if Path(name).name != name:
            raise ValueError('Invalid capture page path')
        envelope = json.loads((root / name).read_text())
        if row_hash(envelope) != page.get('sha256') or len(envelope.get('rows', [])) != page.get('rows'):
            raise ValueError('Capture page hash or row count mismatch')
        for row_no, row in enumerate(envelope['rows'], 1):
            source_hash = row_hash(row)
            occurrence_no = occurrences.get(source_hash, 0) + 1
            occurrences[source_hash] = occurrence_no
            yield normalize_row(row, tenant_id=state['tenant'], occurrence_no=occurrence_no,
                                capture_id=state['capture_id'], page_no=page_no, row_no=row_no,
                                observed_at=envelope['observed_at'])
