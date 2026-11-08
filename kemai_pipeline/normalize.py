"""Pure source-to-business mapping; currency and identity rules versioned explicitly."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from zoneinfo import ZoneInfo

SHANGHAI=ZoneInfo('Asia/Shanghai')
KINDS={'purchase-orders':'order','purchase-receipts':'receipt','purchase-returns':'return'}


def dump(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def text(row,key):
    value=row.get(key)
    return None if value is None else str(value)


def scaled(row,key,scale=10000):
    value=row.get(key)
    if value is None or value=='':return None
    number=Decimal(str(value))/scale
    if not number.is_finite():raise ValueError('Invalid source decimal')
    return format(number,'f')


def timestamp(row,key):
    value=row.get(key)
    if value is None or value=='':return None
    if isinstance(value,(float,int)):
        return datetime.fromtimestamp(value/1000,SHANGHAI).strftime('%Y-%m-%d %H:%M:%S')
    parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if parsed.tzinfo:parsed=parsed.astimezone(SHANGHAI)
    return parsed.strftime('%Y-%m-%d %H:%M:%S')


def history_key(row):
    sequence=row.get('sequence')
    if sequence is not None and sequence!='':
        try:
            number=Decimal(str(sequence))
            sequence=str(int(number)) if number.is_finite() and number==number.to_integral_value() else str(sequence).strip()
        except (ValueError,InvalidOperation):sequence=str(sequence).strip()
    if not text(row,'sheetNo') or sequence in (None,'') or not (text(row,'itemCode') or text(row,'itemId')):
        raise ValueError('History row lacks stable key columns')
    return hashlib.sha256(dump([text(row,'sheetNo'),sequence,text(row,'itemCode') or text(row,'itemId')]).encode()).hexdigest()


def normalize(stream,row):
    if not isinstance(row,dict):raise ValueError('Source row must be an object')
    source_id=text(row,'id')
    if stream!='purchase-receive-history' and not source_id:raise ValueError('Source ID missing')
    payload=dump(row)
    data={'payload':payload,'payload_hash':hashlib.sha256(payload.encode()).hexdigest()}
    if stream=='retail-sales':
        names={'source_flow_id':'id','source_order_id':'sheetId','receipt_no':'receiptNo',
          'source_branch_id':'branchId','source_item_id':'itemId','source_supplier_id':'supplierId'}
        data.update({dest:text(row,key) for dest,key in names.items()})
        data.update(quantity=scaled(row,'qty'),sale_amount=scaled(row,'saleAmt'),coupon_share_amount=scaled(row,'couponShareAmt'),
          sold_at=timestamp(row,'gmtSale'),source_created_at=timestamp(row,'gmtCreate'))
    elif stream=='retail-orders':
        names={'source_order_id':'id','receipt_no':'receiptNo','source_receipt_no':'sourceReceiptNo',
          'source_branch_id':'branchId','source_member_id':'memberId','source_type':'sourceType','channel_code':'channel','sale_flag':'saleFlag'}
        data.update({dest:text(row,key) for dest,key in names.items()});data['receipt_no']=data['receipt_no'] or source_id
        data.update(sale_amount=scaled(row,'saleAmt'),order_amount=scaled(row,'orderAmt'),sold_at=timestamp(row,'gmtSale'),source_created_at=timestamp(row,'gmtCreate'))
    elif stream in KINDS:
        names={'source_document_id':'id','sheet_no':'sheetNo','sheet_type':'sheetType','source_document_ref_id':'sourceId',
          'source_document_ref_no':'sourceNo','source_supplier_id':'supplierId','source_branch_id':'branchId',
          'source_receive_branch_id':'receiveBranchId','status_code':'status','source_version':'version'}
        data.update({dest:text(row,key) for dest,key in names.items()})
        data.update(document_kind=KINDS[stream],amount=scaled(row,'sumAmt'),source_created_at=timestamp(row,'gmtCreate'),
          source_modified_at=timestamp(row,'gmtModified'),source_approved_at=timestamp(row,'approveTime'))
    elif stream=='purchase-receive-history':
        source_id=history_key(row)
        names={'source_document_id':'id','sheet_no':'sheetNo','sheet_type':'sheetType',
          'source_supplier_id':'supplierId','source_branch_id':'branchId','source_item_id':'itemId','business_date':'warehousingDate'}
        data.update({dest:text(row,key) for dest,key in names.items()})
        if not data['source_document_id']:raise ValueError('History API row lacks document ID; Excel requires separately validated reference mapping')
        data.update(source_line_key=source_id,quantity=scaled(row,'qty'),amount=scaled(row,'amt'),approved_at=timestamp(row,'approveTime'))
    else:raise ValueError('No verified normalized mapping for '+stream)
    return {'source_key':source_id,'data':data}
