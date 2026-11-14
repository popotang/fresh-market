"""Marketing/member fields extracted from stored retail source rows.

These fields already exist inside the business rows' payload JSON, so reporting
can use read-only JSON_EXTRACT views without schema changes. This module keeps
the same mapping for a future optional materialization after the data-center
columns are explicitly added. couponShareSaleAmt is deliberately excluded: it
is identical to saleAmt whenever couponShareAmt is zero. memberPhone is
personal information and is never selected here.
"""
from .normalize import scaled, text


def sale_flow_marketing(row):
    """Map a retail sale-flow source row to optional marketing columns."""
    return {
        'promote_code': text(row, 'promoteCodes'),
        'member_card_no': text(row, 'memberCardNo'),
        'member_grade_name': text(row, 'memberGradeName'),
        'discount_amount': scaled(row, 'discountAmt'),
    }


def order_marketing(row):
    """Map a retail order-header source row to optional marketing columns."""
    return {
        'member_card_no': text(row, 'memberCardNo'),
        'member_grade_id': text(row, 'memberGradeId'),
        'member_grade_name': text(row, 'memberGradeName'),
        'discount_amount': scaled(row, 'discountAmt'),
    }
