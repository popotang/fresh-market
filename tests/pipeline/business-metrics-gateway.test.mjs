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

const makeClient = (handler, metricRows = defaultMetricRows) => {
  const cacheRows = [];
  const filterCalls = [];
  const client = {
    models: {
      byTable(name) {
        if (name === 'metric_definitions') {
          return {
            filter: async () => ({ tableData: metricRows, paging: { totalCount: metricRows.length } }),
          };
        }
        if (name === 'metric_values') {
          return {
            filter: async ({ where = {}, currentPage = 1, pageSize = 200 }) => {
              filterCalls.push({ name, currentPage, pageSize });
              const key = where.cache_key?.$eq;
              const matched = key ? cacheRows.filter(row => row.cache_key === key) : cacheRows;
              matched.sort((a, b) => ((Number(b.generation) || 0) - (Number(a.generation) || 0)) || (a.id - b.id));
              const effectiveSize = Math.min(pageSize, 200);
              const start = (currentPage - 1) * effectiveSize;
              const tableData = matched.slice(start, start + effectiveSize);
              return { tableData, paging: { totalCount: matched.length, currentPage, pageSize: effectiveSize } };
            },
            delete: async ({ id }) => {
              const index = cacheRows.findIndex(row => row.id === id);
              if (index >= 0) cacheRows.splice(index, 1);
              return id;
            },
            batchCreate: async (rows) => {
              for (const row of rows) {
                cacheRows.push({ ...row, id: cacheRows.length + 1 });
              }
              return rows.map(row => row.cache_key);
            },
          };
        }
        throw new Error(`Unexpected model ${name}`);
      },
    },
    sql: {
      byName(name) {
        return {
          execute: async ({ params }) => {
            if (name === 'metricCacheDelete') {
              const key = params.cacheKey;
              for (let index = cacheRows.length - 1; index >= 0; index -= 1) {
                if (cacheRows[index].cache_key === key) cacheRows.splice(index, 1);
              }
              return [];
            }
            return handler(name, params);
          },
        };
      },
    },
    db: {
      transaction: async action => action(client),
    },
    __cacheRows: cacheRows,
    __filterCalls: filterCalls,
  };
  return client;
};

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

const cachedRetailHandler = (calls) => (name) => {
  calls.push(name);
  if (name === 'metricRetailPerformance') {
    return [{
      dim_key: '69028303',
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
};

test('query cache is written then hit without re-running group SQL', async () => {
  const calls = [];
  const client = makeClient(cachedRetailHandler(calls));
  const params = {
    action: 'query', groupCode: 'retail_performance',
    startDate: '2026-11-01', endDate: '2026-11-07',
    dimension: 'branch', metricCodes: ['net_sales_amount', 'avg_order_value'],
  };
  const first = await gateway(params, { client });
  assert.equal(first.cache.hit, false);
  assert.ok(client.__cacheRows.length > 0);
  assert.equal(typeof client.__cacheRows[0].calculated_at, 'number');
  assert.equal(typeof client.__cacheRows[0].expires_at, 'number');
  const groupCalls = calls.filter(name => name === 'metricRetailPerformance').length;
  const second = await gateway(params, { client });
  assert.equal(second.cache.hit, true);
  assert.equal(calls.filter(name => name === 'metricRetailPerformance').length, groupCalls);
  assert.equal(second.metrics.net_sales_amount, 90);
});

test('expired cache recomputes and refreshes stored values', async () => {
  const calls = [];
  const client = makeClient(cachedRetailHandler(calls));
  const params = {
    action: 'query', groupCode: 'retail_performance',
    startDate: '2026-11-01', endDate: '2026-11-07', dimension: 'branch',
  };
  await gateway(params, { client });
  const before = client.__cacheRows.length;
  for (const row of client.__cacheRows) row.expires_at = '2000-01-01 00:00:00';
  const second = await gateway(params, { client });
  assert.equal(second.cache.hit, false);
  assert.equal(calls.filter(name => name === 'metricRetailPerformance').length, 2);
  assert.ok(client.__cacheRows.length > before, 'recompute must append a new generation without deleting the old one');
});

test('cache keys isolate branches and metric selections', async () => {
  const calls = [];
  const client = makeClient(cachedRetailHandler(calls));
  const base = {
    action: 'query', groupCode: 'retail_performance',
    startDate: '2026-11-01', endDate: '2026-11-07', dimension: 'branch',
  };
  await gateway({ ...base, branchId: '69028303' }, { client });
  const firstKeys = new Set(client.__cacheRows.map(row => row.cache_key));
  await gateway({ ...base, branchId: 'another-branch' }, { client });
  const secondKeys = new Set(client.__cacheRows.map(row => row.cache_key));
  assert.equal(firstKeys.size, 1);
  assert.equal(secondKeys.size, 2);
  assert.notDeepEqual(firstKeys, secondKeys);
  await gateway({ ...base, metricCodes: ['net_sales_amount'] }, { client });
  assert.equal(new Set(client.__cacheRows.map(row => row.cache_key)).size, 3);
});

test('epoch-millisecond expiry from MySQL is treated as fresh cache', async () => {
  const calls = [];
  const client = makeClient(cachedRetailHandler(calls));
  const params = {
    action: 'query', groupCode: 'retail_performance',
    startDate: '2026-11-01', endDate: '2026-11-07', dimension: 'branch',
  };
  await gateway(params, { client });
  for (const row of client.__cacheRows) row.expires_at = String(Date.now() + 600000);
  const second = await gateway(params, { client });
  assert.equal(second.cache.hit, true);
});

test('cache hit works when the cached result spans multiple pages', async () => {
  const calls = [];
  const client = makeClient((name) => {
    calls.push(name);
    if (name === 'metricRetailPerformance') {
      return Array.from({ length: 150 }, (_, index) => ({
        dim_key: `item-${index}`,
        gross_sales_amount: 100,
        refund_amount: 0,
        net_sales_amount: 100,
        gross_quantity: 1,
        net_quantity: 1,
        order_count: 1,
        sales_order_count: 1,
        original_sales_amount: 100,
        member_net_sales_amount: 0,
        member_gross_sales_amount: 0,
        member_sales_order_count: 0,
        sales_with_cost: 0,
        estimated_gross_profit: 0,
      }));
    }
    return [{ latest_biz_date: '2026-11-07' }];
  });
  const params = {
    action: 'query', groupCode: 'retail_performance',
    startDate: '2026-11-01', endDate: '2026-11-07', dimension: 'item',
  };
  await gateway(params, { client });
  assert.ok(client.__cacheRows.length > 200);
  client.__filterCalls.length = 0;
  const second = await gateway(params, { client });
  assert.equal(second.cache.hit, true);
  assert.equal(calls.filter(name => name === 'metricRetailPerformance').length, 1);
  assert.equal(client.__filterCalls.filter(call => call.name === 'metric_values').length, 1);
});

test('health reuses cached coverage within the TTL', async () => {
  const calls = [];
  const client = makeClient((name) => {
    calls.push(name);
    return [{ latest_biz_date: '2026-11-09', payment_fact_count: 2 }];
  });
  const first = await gateway({ action: 'health' }, { client });
  assert.equal(first.cache.hit, false);
  const second = await gateway({ action: 'health' }, { client });
  assert.equal(second.cache.hit, true);
  assert.equal(calls.filter(name => name === 'metricDataCoverage').length, 1);
});
