import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { METRIC_DEFINITIONS } from '../../kemai_pipeline/js/business_metrics.mjs';

const source = readFileSync('.rabetbase/bff/app-21522951/ENDPOINT/businessMetricGateway.js', 'utf8');
const gateway = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;

const defaultMetricRows = METRIC_DEFINITIONS.map((metric, index) => ({
  id: index + 1,
  definition_version: 'retail-metrics-v1.0.0',
  metric_code: metric.code,
  name: metric.name,
  group_code: metric.group,
  unit: metric.unit,
  formula: metric.formula,
  source_name: metric.source,
  nullable: metric.nullable ? 1 : 0,
  enabled: 1,
  sort_order: index + 1,
}));

const makeClient = (handler, metricRows = defaultMetricRows) => ({
  models: {
    byTable(name) {
      if (name !== 'metric_definitions') throw new Error(`Unexpected model ${name}`);
      return {
        filter: async () => ({ tableData: metricRows, paging: { totalCount: metricRows.length } }),
      };
    },
  },
  sql: {
    byName(name) {
      return { execute: async ({ params }) => handler(name, params) };
    },
  },
});

test('catalog reads metric definitions from the table and maps database fields', async () => {
  const client = makeClient(() => {
    throw new Error('catalog must not execute SQL');
  }, [{
    id: 9,
    definition_version: 'retail-metrics-v1.0.0',
    metric_code: 'db_backed_metric',
    name: '表内指标',
    group_code: 'retail_performance',
    unit: 'CNY',
    formula: 'db_formula',
    source_name: 'metric_definitions',
    nullable: 0,
    enabled: 1,
    sort_order: 1,
  }]);
  const result = await gateway({ action: 'catalog' }, { client });
  assert.equal(result.metrics.length, 1);
  assert.deepEqual(result.metrics[0], {
    code: 'db_backed_metric',
    name: '表内指标',
    group: 'retail_performance',
    unit: 'CNY',
    formula: 'db_formula',
    source: 'metric_definitions',
    nullable: false,
  });
});

test('catalog exposes version and does not call SQL', async () => {
  const calls = [];
  const client = makeClient((name) => {
    calls.push(name);
    return [];
  });
  const result = await gateway({ action: 'catalog' }, { client });
  assert.equal(result.definitionVersion, 'retail-metrics-v1.0.0');
  assert.ok(result.metrics.some(metric => metric.code === 'net_sales_amount'));
  assert.ok(result.groups.some(group => group.code === 'inventory_health'));
  assert.deepEqual(calls, []);
});

test('query routes group to published SQL and computes ratios', async () => {
  const calls = [];
  const client = makeClient((name) => {
    calls.push(name);
    if (name === 'metricRetailPerformance') {
      return [{
        biz_date: '2026-11-07',
        gross_sales_amount: 100,
        refund_amount: 10,
        net_sales_amount: 90,
        gross_quantity: 10,
        net_quantity: 9,
        order_count: 4,
        sales_order_count: 3,
        original_sales_amount: 120,
        member_net_sales_amount: 45,
        member_gross_sales_amount: 50,
        member_sales_order_count: 2,
        sales_with_cost: 60,
        estimated_gross_profit: 12,
      }];
    }
    return [{ latest_biz_date: '2026-11-07', net_sales_amount: 90, sales_with_cost: 60 }];
  });
  const result = await gateway({
    action: 'query',
    groupCode: 'retail_performance',
    startDate: '2026-11-01',
    endDate: '2026-11-07',
    metricCodes: ['net_sales_amount', 'avg_order_value'],
  }, { client });
  assert.equal(result.metrics.net_sales_amount, 90);
  assert.equal(result.metrics.avg_order_value, 100 / 3);
  assert.equal(result.coverage.latest_biz_date, '2026-11-07');
  assert.ok(calls.includes('metricRetailPerformance'));
  assert.ok(calls.includes('metricDataCoverage'));
});

test('query defaults to latest business date when dates are omitted', async () => {
  const calls = [];
  const client = makeClient((name) => {
    calls.push(name);
    if (name === 'metricRetailPerformance') {
      return [{
        gross_sales_amount: 50, refund_amount: 0, net_sales_amount: 50,
        gross_quantity: 5, net_quantity: 5, order_count: 1, sales_order_count: 1,
        original_sales_amount: 50, member_net_sales_amount: 0,
        member_gross_sales_amount: 0, member_sales_order_count: 0,
        sales_with_cost: 0, estimated_gross_profit: 0,
      }];
    }
    return [{ latest_biz_date: '2026-11-09' }];
  });
  const result = await gateway({ action: 'query', groupCode: 'retail_performance' }, { client });
  assert.equal(result.window.endDate, '2026-11-09');
  assert.equal(result.window.startDate, '2026-11-03');
  assert.equal(calls[0], 'metricDataCoverage');
});

test('invalid query is rejected before group SQL runs', async () => {
  const calls = [];
  const client = makeClient((name) => {
    calls.push(name);
    return [];
  });
  await assert.rejects(
    gateway({ action: 'query', groupCode: 'retail_performance', startDate: '2026-11-07', endDate: '2026-11-01' }, { client }),
    /INVALID_DATE_RANGE/,
  );
  assert.equal(calls.includes('metricRetailPerformance'), false);
});

test('health returns coverage without querying a group SQL', async () => {
  const client = makeClient(() => [{
    latest_biz_date: '2026-11-09',
    payment_fact_count: 2,
    promotion_rows: 10,
    promotion_linked_rows: 8,
  }]);
  const result = await gateway({ action: 'health' }, { client });
  assert.equal(result.definitionVersion, 'retail-metrics-v1.0.0');
  assert.equal(result.coverage.promotion_link_rate, 0.8);
  assert.ok(result.warnings.includes('PAYMENT_FACTS_INSUFFICIENT'));
});
