import unittest

from kemai_pipeline.marketing_fields import order_marketing, sale_flow_marketing


class MarketingFieldTests(unittest.TestCase):
    def test_sale_flow_mapping_matches_read_only_view(self):
        row = {'promoteCodes': 'SP0000202610300004', 'memberCardNo': '123',
               'memberGradeName': '普通会员', 'discountAmt': '63600',
               'couponShareSaleAmt': '201300', 'saleAmt': '201300',
               'memberPhone': '13800000000'}
        self.assertEqual(sale_flow_marketing(row), {
            'promote_code': 'SP0000202610300004',
            'member_card_no': '123',
            'member_grade_name': '普通会员',
            'discount_amount': '6.36',
        })
        self.assertNotIn('memberPhone', sale_flow_marketing(row).values())
        self.assertNotIn('coupon_share_sale_amount', sale_flow_marketing(row))

    def test_order_mapping_scales_discount_and_omits_absent_values(self):
        row = {'memberCardNo': '123', 'memberGradeId': '35067',
               'memberGradeName': '普通会员', 'discountAmt': '68300'}
        self.assertEqual(order_marketing(row), {
            'member_card_no': '123',
            'member_grade_id': '35067',
            'member_grade_name': '普通会员',
            'discount_amount': '6.83',
        })

    def test_null_and_empty_source_values_stay_null(self):
        self.assertEqual(sale_flow_marketing({'promoteCodes': None, 'discountAmt': None}), {
            'promote_code': None, 'member_card_no': None,
            'member_grade_name': None, 'discount_amount': None,
        })
        self.assertEqual(order_marketing({'discountAmt': ''})['discount_amount'], None)


if __name__ == '__main__':
    unittest.main()
