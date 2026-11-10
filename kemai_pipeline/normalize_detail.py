"""Pure purchase-detail child-row mapping, ported from legacy purchase().

Maps the details/branchs/approves arrays of a purchase detail entity into
child-row dicts matching the purchase_document_lines / _branches / _approvals
columns. Money and quantity values keep the legacy /10000 scaling and the
legacy `or` fallback semantics (Decimal 0 is falsy). No I/O, no imports of
lovrabet_adapter/source/artifacts, and independent of detail_state; Task 6
composes the two.
"""
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation


def _dump(value):
    # Same canonical form as legacy kemai_sync.dump.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, default=str)


def _val(obj, key):
    value = obj.get(key)
    return str(value) if value is not None else None


def _money(obj, key):
    value = obj.get(key)
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)) / 10000
    except InvalidOperation:
        return None


def _dt(obj, key):
    value = obj.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d %H:%M:%S")
    return str(value)[:19]


def _details_array(entity):
    rows = entity.get("details")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("purchase detail lacks a valid details array")
    return rows


def normalize_purchase_lines(document_id, entity):
    rows = _details_array(entity)
    return [{
        "document_id": document_id,
        "source_line_id": _val(row, "id") or f"ordinal:{i}",
        "source_item_id": _val(row, "itemId"),
        "source_line_ref_id": _val(row, "sourceDetailId"),
        "quantity": _money(row, "qty"),
        "unit_price": _money(row, "price") or _money(row, "purchasePrice"),
        "amount": _money(row, "amt") or _money(row, "amount"),
        "tax_amount": _money(row, "taxAmt"),
        "payload": _dump(row),
    } for i, row in enumerate(rows)]


def normalize_purchase_branches(document_id, entity):
    rows = entity.get("branchs") or []
    return [{
        "document_id": document_id,
        "source_key": _val(row, "id") or _val(row, "branchId") or f"ordinal:{i}",
        "source_branch_id": _val(row, "branchId"),
        "payload": _dump(row),
    } for i, row in enumerate(rows)]


def normalize_purchase_approvals(document_id, entity):
    rows = entity.get("approves") or []
    return [{
        "document_id": document_id,
        "source_key": _val(row, "id") or f"ordinal:{i}",
        "source_approver_id": _val(row, "approverId"),
        "status_code": _val(row, "status"),
        "approved_at": _dt(row, "approveTime"),
        "payload": _dump(row),
    } for i, row in enumerate(rows)]
