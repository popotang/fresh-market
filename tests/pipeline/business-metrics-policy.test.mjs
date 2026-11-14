import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFINITION_VERSION,
  METRIC_DEFINITIONS,
  validateQuery,
  deriveRetailRows,
  deriveInventoryRows,
  derivePurchaseRows,
  deriveMarketingRows,
} from '../../kemai_pipeline/js/business_metrics.mjs';

test('definition version and metric codes are stable and unique', () => {
  assert.equal(DEFINITION_VERSION, 'retail-metrics-v1.0.0');
  const codes = METRIC_DEFINITIONS.map(metric => metric.code);
  assert.equal(new Set(codes).size, codes.length);
  assert.ok(codes.includes('net_sales_amount'));
  assert.ok(codes.includes('estimated_gross_margin'));
  assert.ok(codes.includes('days_cover_7d'));
});

test('query validation rejects inverted and overlong windows', () => {
  assert.throws(
    () => validateQuery({ groupCode: 'retail_performance', startDate: '2026-11-07', endDate: '2026-11-01' }),
    /INVALID_DATE_RANGE/,
  );
  assert.throws(
    () => validateQuery({ groupCode: 'retail_performance', startDate: '2026-01-01', endDate: '2026-06-01' }),
    /WINDOW_TOO_LARGE/,
  );
});

test('query validation rejects unknown metric and dimension', () => {
  assert.throws(
    () => validateQuery({ groupCode: 'retail_performance', startDate: '2026-11-01', endDate: '2026-11-07', metricCodes: ['made_up'] }),
    /UNKNOWN_METRIC/,
  );
  assert.throws(
    () => validateQuery({ groupCode: 'inventory_health', startDate: '2026-11-01', endDate: '2026-11-07', dimension: 'cashier' }),
    /UNKNOWN_DIMENSION/,
  );
});

test('retail ratios use positive sales for AOV and null on zero denominator', () => {
  const [row] = deriveRetailRows([{
    gross_sales_amount: 300,
    refund_amount: 30,
    net_sales_amount: 270,
    gross_quantity: 30,
    net_quantity: 27,
    order_count: 12,
    sales_order_count: 10,
    original_sales_amount: 330,
    member_net_sales_amount: 135,
    member_gross_sales_amount: 150,
    member_sales_order_count: 5,
    sales_with_cost: 200,
    estimated_gross_profit: 40,
  }]);
  assert.equal(row.avg_order_value, 30);
  assert.equal(row.refund_rate, 0.1);
  assert.equal(row.member_sales_share, 0.5);
  assert.equal(row.estimated_gross_margin, 0.2);
  assert.equal(row.items_per_order, 3);
  assert.equal(row.cost_coverage_rate, 200 / 270);
});

test('retail ratios return null when denominator is zero', () => {
  const [row] = deriveRetailRows([{
    gross_sales_amount: 0,
    refund_amount: 0,
    net_sales_amount: 0,
    gross_quantity: 0,
    net_quantity: 0,
    order_count: 0,
    sales_order_count: 0,
    original_sales_amount: 0,
    member_net_sales_amount: 0,
    member_gross_sales_amount: 0,
    member_sales_order_count: 0,
    sales_with_cost: 0,
    estimated_gross_profit: 0,
  }]);
  assert.equal(row.avg_order_value, null);
  assert.equal(row.estimated_gross_margin, null);
  assert.equal(row.member_sales_share, null);
});

test('inventory classifies risk and preserves null days cover without sales', () => {
  const rows = deriveInventoryRows([
    { stock_qty: -1, sold_qty_7d: 3, stock_value: 0 },
    { stock_qty: 10, sold_qty_7d: 0, stock_value: 20 },
    { stock_qty: 10, sold_qty_7d: 14, stock_value: 20 },
    { stock_qty: 0, sold_qty_7d: 0, stock_value: 0 },
  ], {
    sold_sku_snapshot_coverage: 0.5,
    snapshot_sales_coverage: 0.4,
  });
  assert.equal(rows[0].risk_level, 'stockout');
  assert.equal(rows[1].risk_level, 'slow');
  assert.equal(rows[1].days_cover_7d, null);
  assert.equal(rows[2].days_cover_7d, 5);
  assert.equal(rows[3].risk_level, 'inactive');
  assert.equal(rows[0].sold_sku_snapshot_coverage, 0.5);
});

test('purchase top5 share uses the full supplier denominator', () => {
  const [row] = derivePurchaseRows(
    [{ purchase_order_amount: 0, receipt_amount: 1000, purchase_return_amount: 100, net_receipt_amount: 900 }],
    [
      { net_receipt_amount: 400 },
      { net_receipt_amount: 300 },
      { net_receipt_amount: 100 },
      { net_receipt_amount: 50 },
      { net_receipt_amount: 40 },
      { net_receipt_amount: 10 },
    ],
  );
  assert.equal(row.supplier_top5_share, 890 / 900);
  assert.equal(row.purchase_return_rate, 0.1);
});

test('marketing keeps marketing discount separate from net sales', () => {
  const [row] = deriveMarketingRows([{
    marketing_discount_amount: 50,
    marketing_sales_amount: 450,
    marketing_original_amount: 500,
    marketing_order_count: 20,
    net_sales_amount: 1000,
    promotion_rows: 40,
    promotion_linked_rows: 30,
  }]);
  assert.equal(row.marketing_discount_rate, 0.1);
  assert.equal(row.marketing_sales_share, 0.45);
  assert.equal(row.promotion_link_rate, 0.75);
});
