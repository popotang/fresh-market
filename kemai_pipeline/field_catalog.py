"""Canonical business field paths and their source capability contracts.

Pure data and pure functions only: no I/O, no CLI, no source probes. A field
path declares whether a completed list capture can satisfy the field, which
detail handler owns it, its acceptance status, storage and refresh rule.

Legacy flat field names (the sets routing.py still consumes) resolve through a
bounded, stream-aware compatibility map that preserves the old semantics:
base/list fields win over line fields, so flat 'amount' on a purchase stream
stays the header amount exactly as BASE_FIELDS did. Ambiguity is reported as
candidate paths, never guessed. Unknown dotted paths are never invented.

Product list-field facts are grounded in local inventory artifacts by field
name only (captures 2f3f4329…, 39207dc6…, d707a11d…): list rows carry
code/name/barcode/subName/size/itemClsId/itemBrandId/itemDepartmentId/
supplierId/status/lifetime/version/retailPrice/purchasePrice/gmtCreate/
approveTime, but not plain unitId nor gmtModified, so product master
source_unit_id and source_modified_at stay detail-only.
"""

from dataclasses import dataclass

PURCHASE_STREAMS = ('purchase-orders', 'purchase-receipts', 'purchase-returns')
HISTORY_STREAM = 'purchase-receive-history'
RETAIL_ORDER_STREAM = 'retail-orders'
RETAIL_SALE_STREAM = 'retail-sales'
PRODUCT_STREAM = 'item_list'
PROMOTE_SHARING_STREAM = 'retail-promote-sharing'

# Documented legacy precedence: flat 'amount' on a purchase stream is the
# header amount (routing.BASE_FIELDS), never the line amount. Demanding the
# line amount requires the canonical purchase.lines.amount path.
LEGACY_DEFAULTS = {'amount': 'purchase.header.amount'}

VERIFIED = 'verified'                # accepted handler, may be scheduled
SMALL_SAMPLE = 'small_sample_verified'  # real write/read-back proof, limited scope
NOT_ACCEPTED = 'not_accepted'        # handler exists, per-tab acceptance pending
PAUSED = 'paused_by_user'            # user-paused, blocked in every mode
NOT_APPLICABLE = 'not_applicable'    # list-satisfied; no detail handler needed


@dataclass(frozen=True)
class FieldSpec:
    path: str
    streams: tuple            # streams whose list rows carry this field
    satisfied_by_list: bool
    detail_handler: str | None = None   # 'lines_only' | 'product_object' | 'retail_order_detail'
    detail_status: str = NOT_APPLICABLE
    refresh: str = 'none'               # 'none'|'list_hash'|'source_version'|'observation_ttl'
    storage: str = ''

    def to_dict(self):
        return {'path': self.path, 'streams': list(self.streams),
                'satisfied_by_list': self.satisfied_by_list,
                'detail_handler': self.detail_handler, 'detail_status': self.detail_status,
                'refresh': self.refresh, 'storage': self.storage}


def _purchase_header():
    names = ('source_document_id', 'sheet_no', 'amount', 'source_version', 'status_code',
             'document_kind', 'sheet_type', 'source_document_ref_id', 'source_document_ref_no',
             'source_supplier_id', 'source_branch_id', 'source_receive_branch_id',
             'source_created_at', 'source_modified_at', 'source_approved_at', 'payload', 'payload_hash')
    return [FieldSpec(f'purchase.header.{n}', PURCHASE_STREAMS, True,
                      storage='purchase_documents', refresh='source_version') for n in names]


def _purchase_lines():
    names = ('source_item_id', 'quantity', 'unit_price', 'amount', 'tax_amount', 'payload')
    return [FieldSpec(f'purchase.lines.{n}', PURCHASE_STREAMS, False, detail_handler='lines_only',
                      detail_status=VERIFIED, refresh='source_version',
                      storage='purchase_document_lines') for n in names]


def _purchase_history():
    names = ('source_line_key', 'sheet_no', 'quantity', 'amount', 'approved_at', 'business_date',
             'source_document_id', 'sheet_type', 'source_supplier_id', 'source_branch_id',
             'source_item_id', 'payload', 'payload_hash')
    return [FieldSpec(f'purchase.history.{n}', (HISTORY_STREAM,), True,
                      storage='purchase_receipt_history_lines') for n in names]


def _retail_orders():
    names = ('source_order_id', 'receipt_no', 'source_receipt_no', 'source_branch_id',
             'source_member_id', 'source_type', 'channel_code', 'sale_flag', 'sale_amount',
             'order_amount', 'sold_at', 'source_created_at', 'payload', 'payload_hash')
    specs = [FieldSpec(f'retail.orders.{n}', (RETAIL_ORDER_STREAM,), True,
                       storage='retail_orders') for n in names]
    specs.append(FieldSpec('retail.orders.details', (RETAIL_ORDER_STREAM,), False,
                           detail_handler='retail_order_detail', detail_status=PAUSED,
                           storage='retail_order_lines'))
    specs.append(FieldSpec('retail.orders.payments', (RETAIL_ORDER_STREAM,), False,
                           detail_handler='retail_order_detail', detail_status=PAUSED,
                           storage='retail_payments'))
    return specs


def _retail_sales():
    names = ('source_flow_id', 'source_order_id', 'source_item_id', 'receipt_no',
             'source_branch_id', 'source_supplier_id', 'coupon_share_amount', 'quantity',
             'sale_amount', 'sold_at', 'source_created_at', 'payload', 'payload_hash')
    return [FieldSpec(f'retail.sales.{n}', (RETAIL_SALE_STREAM,), True,
                      storage='retail_sale_flows') for n in names]


def _product_master():
    listed = ('item_code', 'item_name', 'barcode', 'sub_name', 'item_size',
              'source_category_id', 'source_brand_id', 'source_department_id',
              'source_supplier_id', 'status_code', 'lifetime_code', 'source_version',
              'retail_price', 'purchase_price', 'source_created_at', 'source_approved_at')
    specs = [FieldSpec(f'product.master.{n}', (PRODUCT_STREAM,), True,
                       storage='products', refresh='list_hash') for n in listed]
    # Not present in list rows (artifact field-name evidence): unitId, gmtModified.
    for name in ('source_unit_id', 'source_modified_at'):
        specs.append(FieldSpec(f'product.master.{name}', (PRODUCT_STREAM,), False,
                               detail_handler='product_object', detail_status=SMALL_SAMPLE,
                               refresh='source_version', storage='products'))
    return specs


def _product_tabs():
    ttl_tabs = ('stock', 'retail_price', 'delivery_price', 'promotion', 'history_price')
    statuses = {'supply': SMALL_SAMPLE, 'barcode': SMALL_SAMPLE}
    specs = []
    for tab in ('supply', 'retail_price', 'delivery_price', 'stock', 'promotion', 'barcode',
                'grading', 'pack', 'history_price', 'scope', 'combination', 'mix', 'split', 'cut'):
        specs.append(FieldSpec(f'product.tabs.{tab}', (PRODUCT_STREAM,), False,
                               detail_handler='product_object', detail_status=statuses.get(tab, NOT_ACCEPTED),
                               refresh='observation_ttl' if tab in ttl_tabs else 'source_version',
                               storage='product_child_records'))
    return specs


def _promote_sharing():
    names = ('source_order_id', 'sheet_no', 'marketing_type', 'promotion_id',
             'promotion_code', 'activity_code', 'activity_name', 'amount',
             'original_amount', 'sale_amount', 'original_price', 'quantity',
             'unit_quantity', 'member_card_no', 'member_grade_id', 'member_grade_name')
    return [FieldSpec(f'retail.promote_sharing.{name}', (PROMOTE_SHARING_STREAM,), True,
                      storage='retail_promote_shares') for name in names]


CATALOG = {spec.path: spec for spec in
           (*_purchase_header(), *_purchase_lines(), *_purchase_history(),
            *_retail_orders(), *_retail_sales(), *_product_master(), *_product_tabs(),
            *_promote_sharing())}

# Leaves shared by several specs (e.g. amount: header/lines/history) resolve
# through LEGACY_DEFAULTS precedence inside resolve(), never through a map that
# would silently keep the last writer.


def _stream_candidates(name, stream):
    return [p for p, s in CATALOG.items()
            if p.rsplit('.', 1)[-1] == name and (not s.streams or stream in s.streams)]


def resolve(fields, stream=None):
    """Resolve requested field names to canonical specs without guessing.

    Canonical paths pass through when the stream context matches. Legacy flat
    names resolve through the bounded stream-aware alias map; a leaf shared by
    several specs resolves to the documented legacy default when one exists,
    otherwise it is reported as ambiguous with its candidates. Without a stream
    context a flat name resolves only when unique in the catalog.
    """
    if not isinstance(fields, (list, tuple)) or any(not isinstance(f, str) or not f for f in fields):
        raise ValueError('Field names must be non-empty strings')
    resolved, ambiguous, unknown = [], {}, []
    for name in fields:
        spec = CATALOG.get(name)
        if spec is not None:
            if stream is not None and spec.streams and stream not in spec.streams:
                unknown.append(name)  # incompatible context; never re-route silently
            else:
                resolved.append(spec)
            continue
        if '.' in name:
            unknown.append(name)      # dotted but not canonical: never invented
            continue
        if stream is not None:
            candidates = _stream_candidates(name, stream)
            legacy = LEGACY_DEFAULTS.get(name)
            if legacy and legacy in candidates:
                resolved.append(CATALOG[legacy])
            elif len(candidates) == 1:
                resolved.append(CATALOG[candidates[0]])
            elif candidates:
                ambiguous[name] = sorted(candidates)
            else:
                unknown.append(name)
            continue
        candidates = sorted(p for p in CATALOG if p.rsplit('.', 1)[-1] == name)
        if len(candidates) == 1:
            resolved.append(CATALOG[candidates[0]])
        elif candidates:
            ambiguous[name] = candidates  # no context: never pick a default blindly
        else:
            unknown.append(name)
    return {'resolved': resolved, 'ambiguous': ambiguous, 'unknown': unknown}


def classify(specs):
    """Group resolved specs into routing verdicts; paused blocks every mode."""
    groups = {'satisfied_by_list': [], 'needs_detail': [], 'paused': []}
    for spec in specs:
        if spec.detail_status == PAUSED:
            groups['paused'].append(spec.path)
        elif spec.satisfied_by_list:
            groups['satisfied_by_list'].append(spec.path)
        else:
            groups['needs_detail'].append(spec.path)
    return groups


def routing_fields(specs, stream):
    """Flat field names for routing.choose_route, preserving its legacy contract.

    List-satisfied fields map to their leaf names. Any purchase line demand maps
    to the 'quantity' trigger because LinesOnlyWorkflow materializes the full
    line record (quantity/unit_price/amount/tax_amount) together. Paused and
    product-domain fields never reach the day-window router.
    """
    flats = []
    for spec in specs:
        if spec.satisfied_by_list and (not spec.streams or stream in spec.streams):
            flats.append(spec.path.rsplit('.', 1)[-1])
        elif stream in PURCHASE_STREAMS and spec.path.startswith('purchase.lines.'):
            flats.append('quantity')
    return sorted(set(flats))
