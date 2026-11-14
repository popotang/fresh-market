# 零售超市全局指标语义层实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 5 条只读指标 SQL、1 个统一 `businessMetricGateway` BFF 和一份可检索的公司知识库口径。

**Architecture:** Custom SQL 只返回基础度量和分母；纯函数模块 `business_metrics.mjs` 统一派生比率、覆盖率、风险分类和响应封装；构建脚本把同一模块内联进单文件 ENDPOINT，避免两套公式。

**Tech Stack:** MySQL 8 / MyBatis Custom SQL、Lovrabet Backend Function、Node 24 `node:test`、Python 构建脚本、rabetbase CLI、公司知识库。

**Spec:** `docs/superpowers/specs/2026-11-14-retail-metrics-semantic-layer-design.md`

## Global Constraints

- 定义版本固定 `retail-metrics-v1.0.0`；口径改变必须升版本。
- 所有 SQL 只读自有 `fresh-market`（dbId `10510`），禁止写科迈 SaaS。
- 数值一律显式限定 `tenant_id`；默认租户 `205149`。
- 日期参数 `YYYY-MM-DD` 含首尾；查询窗口最长 92 天。
- 比率分母为 0、成本缺失、库存覆盖不足时返回 `null`，不得返回 0。
- 不修改旧看板页面与旧 SQL；新增资源独立上线。
- 不复制会员手机号，不输出凭据或原始业务明细。
- 新增 SQL 必须 `validate`、`push --dry-run`、`push`、`sql exec` 四步都完成。
- BFF 必须经过单元测试、`push --dry-run`、`push`、`bff exec`。
- 完成口径以实际命令输出为准，保存成功不等于运行验证。

---

### Task 1: 纯指标策略模块与单元测试

**Files:**
- Create: `kemai_pipeline/js/business_metrics.mjs`
- Test: `tests/pipeline/business-metrics-policy.test.mjs`

**Interfaces:**
- Produces: `DEFINITION_VERSION`、`METRIC_GROUPS`、`METRIC_DEFINITIONS`、`DIMENSIONS`、`SQL_BY_GROUP`、`validateQuery(input, today)`、`deriveRetailRows(rows)`、`deriveInventoryRows(rows, coverage)`、`derivePurchaseRows(rows, supplierRows)`、`deriveMarketingRows(rows)`、`buildQueryResult(query, datasets)`。
- Consumes: 无。

- [ ] **Step 1: 写失败测试**

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFINITION_VERSION, METRIC_DEFINITIONS, validateQuery,
  deriveRetailRows, deriveInventoryRows, derivePurchaseRows,
} from '../../kemai_pipeline/js/business_metrics.mjs';

test('definition version and metric codes are stable and unique', () => {
  assert.equal(DEFINITION_VERSION, 'retail-metrics-v1.0.0');
  const codes = METRIC_DEFINITIONS.map(m => m.code);
  assert.equal(new Set(codes).size, codes.length);
  assert.ok(codes.includes('net_sales_amount'));
  assert.ok(codes.includes('estimated_gross_margin'));
});

test('query validation rejects inverted and overlong windows', () => {
  assert.throws(() => validateQuery({groupCode:'retail_performance',startDate:'2026-11-07',endDate:'2026-11-01'}), /INVALID_DATE_RANGE/);
  assert.throws(() => validateQuery({groupCode:'retail_performance',startDate:'2026-01-01',endDate:'2026-06-01'}), /WINDOW_TOO_LARGE/);
});

test('retail ratios use positive sales for AOV and null on zero denominator', () => {
  const [row] = deriveRetailRows([{
    gross_sales_amount: 300, refund_amount: 30, net_sales_amount: 270,
    gross_quantity: 30, net_quantity: 27, order_count: 12, sales_order_count: 10,
    original_sales_amount: 330, member_net_sales_amount: 135,
    member_gross_sales_amount: 150, member_sales_order_count: 5,
    sales_with_cost: 200, estimated_gross_profit: 40,
  }]);
  assert.equal(row.avg_order_value, 30);
  assert.equal(row.refund_rate, 0.1);
  assert.equal(row.member_sales_share, 0.5);
  assert.equal(row.estimated_gross_margin, 0.2);
  assert.equal(row.items_per_order, 3);
});

test('inventory classifies risk and preserves null days cover without sales', () => {
  const rows = deriveInventoryRows([
    {stock_qty:-1,sold_qty_7d:3,stock_value:0},
    {stock_qty:10,sold_qty_7d:0,stock_value:20},
    {stock_qty:10,sold_qty_7d:14,stock_value:20},
  ], {sold_sku_snapshot_coverage:0.5,snapshot_sales_coverage:0.4});
  assert.equal(rows[0].risk_level, 'stockout');
  assert.equal(rows[1].risk_level, 'slow');
  assert.equal(rows[2].days_cover_7d, 5);
});

test('purchase top5 share uses full supplier denominator', () => {
  const [row] = derivePurchaseRows(
    [{purchase_order_amount:0,receipt_amount:1000,purchase_return_amount:100,net_receipt_amount:900}],
    [{net_receipt_amount:400},{net_receipt_amount:300},{net_receipt_amount:100},{net_receipt_amount:50},{net_receipt_amount:40},{net_receipt_amount:10}],
  );
  assert.equal(row.supplier_top5_share, 0.9888888888888889);
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `node --test tests/pipeline/business-metrics-policy.test.mjs`  
Expected: FAIL，模块不存在或导出未定义。

- [ ] **Step 3: 实现最小策略模块**

实现要点：

- `DIMENSIONS` 白名单：销售 `day/branch/department/category/item/cashier/hour`；库存 `branch/category/item`；采购 `day/branch/supplier`；营销 `day/branch/promotion/category/item`。
- `SQL_BY_GROUP` 映射：`retail_performance→metricRetailPerformance`、`inventory_health→metricInventoryHealth`、`purchase_performance→metricPurchasePerformance`、`marketing_performance→metricMarketingPerformance`。
- `ratio(n,d)` 在 `!Number.isFinite` 或分母 0 时返回 `null`，否则返回原值，不做四舍五入。
- 零售派生包括 `refund_rate`、`avg_order_value`、`items_per_order`、`avg_item_price`、`discount_amount`、`discount_rate`、`member_sales_share`、`member_avg_order_value`、`member_identification_rate`、`cost_coverage_rate`、`estimated_gross_margin`。
- 库存按 `stock_qty<=0 && sold_qty_7d>0`、近7天无销量且库存>0、`days_cover<3`、`days_cover>30` 顺序分类；只输出 `risk_level`，不自行编造全店口径。
- 采购 Top5 使用完整供应商集合排序，不依赖 SQL `LIMIT`。
- `buildQueryResult` 合并 `definitionVersion/rows/metrics/coverage/dataAsOf/warnings`。

- [ ] **Step 4: 运行测试并确认通过**

Run: `node --test tests/pipeline/business-metrics-policy.test.mjs`  
Expected: PASS，全部断言通过。

- [ ] **Step 5: 提交**

```bash
git add kemai_pipeline/js/business_metrics.mjs tests/pipeline/business-metrics-policy.test.mjs
git commit -m "feat: 增加全局经营指标纯策略模块"
```

---

### Task 2: BFF 构建脚本、单元测试与发布

**Files:**
- Create: `scripts/build_business_metric_gateway.py`
- Create: `.rabetbase/bff/app-21522951/ENDPOINT/businessMetricGateway.js`（脚本生成）
- Test: `tests/pipeline/business-metrics-gateway.test.mjs`

**Interfaces:**
- Consumes: Task 1 的导出。
- Produces: `businessMetricGateway(p, context)`；`p.action = catalog|query|health`；`context.client.sql.byName(name).execute({params})`。

- [ ] **Step 1: 写失败测试**

测试从生成的 BFF 文件读取源码，用 base64 动态 import，并构造 mock client：

```javascript
test('catalog exposes version and does not call SQL', async () => {
  const calls = [];
  const result = await gateway({action:'catalog'}, {client:{sql:{byName(name){calls.push(name);return {execute:async()=>[]};}}}});
  assert.equal(result.definitionVersion, 'retail-metrics-v1.0.0');
  assert.ok(result.metrics.some(m => m.code === 'net_sales_amount'));
  assert.equal(calls.length, 0);
});

test('query routes group to published SQL and computes ratios', async () => {
  const sql = {byName(name){return {execute:async()=>({
    retail_performance:[{gross_sales_amount:100,refund_amount:10,net_sales_amount:90,gross_quantity:10,net_quantity:9,order_count:4,sales_order_count:3,original_sales_amount:120,member_net_sales_amount:45,member_gross_sales_amount:50,member_sales_order_count:2,sales_with_cost:60,estimated_gross_profit:12}],
    data_coverage:[{latest_biz_date:'2026-11-07'}],
  })[name];}}};
  const out = await gateway({action:'query',groupCode:'retail_performance',startDate:'2026-11-01',endDate:'2026-11-07'}, {client:{sql}});
  assert.equal(out.metrics.avg_order_value, 100/3);
  assert.equal(out.coverage.latest_biz_date, '2026-11-07');
});
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `node --test tests/pipeline/business-metrics-gateway.test.mjs`  
Expected: FAIL，BFF 文件不存在。

- [ ] **Step 3: 实现构建脚本并生成 BFF**

构建脚本只做确定性内联：

- 读取 `kemai_pipeline/js/business_metrics.mjs`。
- 剥离顶层 `export ` 前缀，保留其余源码。
- 追加 ENDPOINT wrapper：

```javascript
export default async function businessMetricGateway(p, context) {
  const input = p || {};
  if (input.action === 'catalog') return buildCatalog();
  if (input.action === 'health') {
    const rows = await context.client.sql.byName('metricDataCoverage').execute({params: coverageParams(input)});
    return buildHealthResult(rows);
  }
  const query = validateQuery(input);
  const rows = await context.client.sql.byName(SQL_BY_GROUP[query.groupCode]).execute({params: sqlParams(query)});
  const coverage = await context.client.sql.byName('metricDataCoverage').execute({params: coverageParams(query)});
  let supplierRows = [];
  if (query.groupCode === 'purchase_performance') supplierRows = await context.client.sql.byName('metricPurchasePerformance').execute({params:{...sqlParams(query),dimension:'supplier'}});
  return buildQueryResult(query, {rows, coverage, supplierRows});
}
```

- wrapper 不读取环境变量，不接受任意 SQL 名，不包含写操作。
- 生成文件必须有注释：`Generated by scripts/build_business_metric_gateway.py. Source: kemai_pipeline/js/business_metrics.mjs.`

- [ ] **Step 4: 运行单元测试**

Run: `python3 scripts/build_business_metric_gateway.py && node --test tests/pipeline/business-metrics-policy.test.mjs tests/pipeline/business-metrics-gateway.test.mjs`  
Expected: 两个测试文件全部 PASS。

- [ ] **Step 5: 预览、推送并运行 smoke**

```bash
rabetbase bff push --type ENDPOINT --name businessMetricGateway --dry-run --format compress
rabetbase bff push --type ENDPOINT --name businessMetricGateway --format compress
rabetbase bff exec --name businessMetricGateway --params '{"action":"catalog"}' --format compress
```

Expected: dry-run 显示 create；push `ok=true`；exec 返回版本和指标目录。

- [ ] **Step 6: 提交**

```bash
git add scripts/build_business_metric_gateway.py .rabetbase/bff/app-21522951/ENDPOINT/businessMetricGateway.js tests/pipeline/business-metrics-gateway.test.mjs
git commit -m "feat: 增加统一经营指标 BFF 入口"
```

---

### Task 3: 销售与数据覆盖 SQL

**Files:**
- Create: `.rabetbase/sql/app-21522951/fresh-market/<sqlCode>_metricRetailPerformance.sql`
- Create: `.rabetbase/sql/app-21522951/fresh-market/<sqlCode>_metricDataCoverage.sql`

**Interfaces:**
- Produces: 销售基础度量字段（正/负金额、数量、订单键、原价、会员、成本覆盖、估算毛利）和覆盖字段。
- Consumes: `retail_sale_flows`、`retail_orders`、`purchase_documents`、`purchase_document_lines`、`product_branch_stocks`、`retail_promote_shares`、`promotions`。

- [ ] **Step 1: 先用 `sql create` 建资源**

```bash
rabetbase sql create --name metricRetailPerformance --db-id 10510 --mode sql --dry-run --format compress
rabetbase sql create --name metricRetailPerformance --db-id 10510 --mode sql --format compress
rabetbase sql create --name metricDataCoverage --db-id 10510 --mode sql --dry-run --format compress
rabetbase sql create --name metricDataCoverage --db-id 10510 --mode sql --format compress
```

保存返回的真实 `sqlCode`，不得猜测。

- [ ] **Step 2: 写 SQL 并校验**

`metricRetailPerformance` 必须：

- 输入 `tenantId/startDate/endDate/branchId/dimension`。
- 显式 `f.tenant_id = #{tenantId}`。
- 成本子查询只取 `purchase_documents.document_kind IN ('receipt','return')`，并按 `source_created_at < endDate+1` 截止。
- 返回 `gross_sales_amount/refund_amount/net_sales_amount/gross_quantity/net_quantity/order_count/sales_order_count/original_sales_amount/member_net_sales_amount/member_gross_sales_amount/member_sales_order_count/sales_with_cost/estimated_gross_profit`。
- 不返回比率。

`metricDataCoverage` 必须返回：

- `latest_biz_date`、`sales_row_count`、`gross_sales_amount`、`net_sales_amount`。
- `sales_with_cost`、`cost_coverage_rate_raw`（分子、分母都返回，BFF 重算）。
- `snapshot_sku_count`、`sold_sku_count`、`covered_sold_sku_count`、`sold_sales_amount`、`covered_sales_amount`、最新/最旧 `captured_at`。
- `member_gross_sales_amount/member_identified_sales_amount`。
- `promotion_rows/promotion_linked_rows`、`payment_fact_count`。

校验：

```bash
rabetbase sql validate --file <metricRetailPerformance文件> --format compress
rabetbase sql validate --file <metricDataCoverage文件> --format compress
```

- [ ] **Step 3: dry-run、push、真实执行**

```bash
rabetbase sql push --sqlcode <code1> --dry-run --format compress
rabetbase sql push --sqlcode <code1> --format compress
rabetbase sql exec --sqlcode <code1> --params '{"tenantId":"205149","startDate":"2026-11-01","endDate":"2026-11-07","dimension":"day"}' --format compress
```

`metricDataCoverage` 同流程，参数只传 `tenantId` 和同日窗。

Expected: 返回真实行；`net_sales_amount = gross_sales_amount - refund_amount` 在每个同一行成立。

- [ ] **Step 4: 提交**

```bash
git add .rabetbase/sql/app-21522951/fresh-market/*metricRetailPerformance* .rabetbase/sql/app-21522951/fresh-market/*metricDataCoverage* .rabetbase/sql.lock.json
git commit -m "feat: 增加销售与数据覆盖指标 SQL"
```

---

### Task 4: 库存与采购 SQL

**Files:**
- Create: `.rabetbase/sql/app-21522951/fresh-market/<sqlCode>_metricInventoryHealth.sql`
- Create: `.rabetbase/sql/app-21522951/fresh-market/<sqlCode>_metricPurchasePerformance.sql`

**Interfaces:**
- 库存输出：`branch_id/branch_name/dim_key/dim_label/item_id/item_name/unit_name/stock_qty/cost_price/stock_value/snapshot_at/sold_qty_7d/sales_amount_7d/last_sold_at`。
- 采购输出：`biz_date/branch_id/branch_name/dim_key/dim_label/supplier_id/supplier_name/document_count/purchase_order_amount/receipt_amount/purchase_return_amount/net_receipt_amount`。

- [ ] **Step 1: 创建资源**

按 Task 3 相同流程 `sql create` 两个名称，保存真实 `sqlCode`。

- [ ] **Step 2: 写 SQL 并校验**

库存：

- 固定近 7 天：`DATE_SUB(#{endDate}, INTERVAL 6 DAY)` 到 `endDate + 1 day`。
- 每个门店+商品取最新快照：以 `ROW_NUMBER() OVER (PARTITION BY tenant_id,source_branch_id,product_id ORDER BY captured_at DESC,id DESC)` 取 `rn=1`。
- 库存关联商品主档按 `product_id`，销售按 `tenant_id + source_item_id`。
- 不返回全店结论字段。
- 允许维度 `branch/category/item`，参数分支使用 `<choose>`。

采购：

- 显式 `tenant_id`、日期 `source_created_at`。
- 按 `document_kind` 拆下单、收货、退货；净收货 = 收货 − 退货。
- 维度 `day/branch/supplier`；供应商维度返回完整供应商集合，不 `LIMIT`。
- 不计算及时率或满足率。

- [ ] **Step 3: 校验、推送、执行**

```bash
rabetbase sql validate --file <file> --format compress
rabetbase sql push --sqlcode <code> --dry-run --format compress
rabetbase sql push --sqlcode <code> --format compress
rabetbase sql exec --sqlcode <code> --params '{"tenantId":"205149","endDate":"2026-11-07","dimension":"branch"}' --format compress
```

Expected: 库存返回当前 177 SKU 范围内的真实快照；采购供应商行数与源聚合一致。

- [ ] **Step 4: 提交**

```bash
git add .rabetbase/sql/app-21522951/fresh-market/*metricInventoryHealth* .rabetbase/sql/app-21522951/fresh-market/*metricPurchasePerformance* .rabetbase/sql.lock.json
git commit -m "feat: 增加库存与采购指标 SQL"
```

---

### Task 5: 营销 SQL

**Files:**
- Create: `.rabetbase/sql/app-21522951/fresh-market/<sqlCode>_metricMarketingPerformance.sql`

**Interfaces:**
- 输出：`biz_date/branch_id/dim_key/dim_label/marketing_discount_amount/marketing_sales_amount/marketing_original_amount/marketing_order_count/net_sales_amount/promotion_rows/promotion_linked_rows`。

- [ ] **Step 1: 创建资源**

```bash
rabetbase sql create --name metricMarketingPerformance --db-id 10510 --mode sql --dry-run --format compress
rabetbase sql create --name metricMarketingPerformance --db-id 10510 --mode sql --format compress
```

- [ ] **Step 2: 写 SQL 并校验**

- `retail_promote_shares` 显式 `tenant_id`，日期使用 `sold_at`。
- 净销售额同窗口同门店从 `retail_sale_flows` 计算，作为 `net_sales_amount` 返回，供 BFF 计算覆盖率。
- 促销关联使用 `promotion_id→promotions.source_promotion_id` 或 `promotion_code→promotions.promotion_code` 任一命中。
- 维度 `day/branch/promotion/category/item`。
- 不以优惠额除以销售额冒充 ROI。

- [ ] **Step 3: 校验、推送、执行**

```bash
rabetbase sql validate --file <file> --format compress
rabetbase sql push --sqlcode <code> --dry-run --format compress
rabetbase sql push --sqlcode <code> --format compress
rabetbase sql exec --sqlcode <code> --params '{"tenantId":"205149","startDate":"2026-11-01","endDate":"2026-11-07","dimension":"promotion"}' --format compress
```

Expected: 营销优惠金额与 9,762 行让利明细口径一致；关联率不超 100%。

- [ ] **Step 4: 提交**

```bash
git add .rabetbase/sql/app-21522951/fresh-market/*metricMarketingPerformance* .rabetbase/sql.lock.json
git commit -m "feat: 增加营销让利指标 SQL"
```

---

### Task 6: BFF 端到端 smoke 与口径对账

**Files:**
- Create: `scripts/verify_business_metrics.py`
- Create: `docs/kemai-global-business-metrics-v1.md`

- [ ] **Step 1: 写验证脚本**

脚本通过 `rabetbase bff exec` 调用四组查询和 health，并校验：

- 返回 `definitionVersion`。
- 零售行 `net_sales_amount == gross_sales_amount - refund_amount`（允许浮点 1e-6）。
- 所有比率在 `[0,1]`，`estimated_gross_margin` 允许负值。
- 库存风险计数之和不超过快照 SKU 数。
- 采购 Top5 集中度不超过 1。
- 营销关联率不超过 1。
- 非法指标、倒置日期、93 天窗口均被拒绝。
- 同窗口净销售额与旧 `dashboardRetailDaily` 求和一致；订单、毛利、库存的差异只作为已解释差异记录，不强制相等。

脚本只输出汇总和差异，不打印明细行。

- [ ] **Step 2: 运行并修复**

Run: `python3 scripts/verify_business_metrics.py`  
Expected: 所有 invariants 通过；否则按实际错误修 SQL 或 BFF 并重跑。

- [ ] **Step 3: 写项目口径文档**

文档必须包含：

- 指标编码、中文名、公式、来源、维度、单位、NULL 规则。
- 数据质量伴随指标与限制。
- 5 条 SQL 与 BFF 的真实 `sqlCode/functionName`。
- 调用示例、错误码、版本变更规则。
- 已知差异：旧看板订单按 `receipt_no`、旧毛利混合采购单行、旧库存天数跟随区间。

- [ ] **Step 4: 提交**

```bash
git add scripts/verify_business_metrics.py docs/kemai-global-business-metrics-v1.md
git commit -m "docs: 固化全局经营指标口径与验收结果"
```

---

### Task 7: 公司知识库发布与回读

**Files:**
- Source: `docs/kemai-global-business-metrics-v1.md`

- [ ] **Step 1: 预览创建**

```bash
rabetbase kb create --title '零售超市全局经营指标口径 v1.0' --file docs/kemai-global-business-metrics-v1.md --dry-run --format compress
```

- [ ] **Step 2: 正式创建**

```bash
rabetbase kb create --title '零售超市全局经营指标口径 v1.0' --file docs/kemai-global-business-metrics-v1.md --format compress
```

保存真实 KB id。

- [ ] **Step 3: 回读与检索**

```bash
rabetbase kb detail --id <kbId> --format compress
rabetbase kb search --query '净销售额 估算毛利 可售天数 营销优惠' --topk 5 --format compress
```

Expected: detail 标题/版本正确；search 能召回该知识库。

- [ ] **Step 4: 最终状态与提交**

```bash
rabetbase sql status --format compress
rabetbase bff status --format compress
git status --short
```

确认无未推送 SQL/BFF 变更后提交知识库实施记录（如需要生成记录文档）。

---

## Self-Review

- 规格 2.1–2.3 的租户、退货、空值、版本规则均覆盖 Task 1/3–5。
- 指标 3.1–3.7 全部由 Task 1 纯函数与 Task 3–5 SQL 实现；数据质量由 Task 3 覆盖 SQL 提供。
- 4.1–4.5 五条 SQL 均有独立任务和真实执行要求。
- 5.1–5.4 BFF 契约由 Task 2 wrapper + Task 6 负向测试覆盖。
- 6 兼容边界：计划未修改或删除旧页面与旧 SQL。
- 7.1–7.3 验证与 8 完成定义由 Task 6/7 覆盖。
- 无 TBD、TODO 或未定义标识；所有新增资源都要求保存真实 sqlCode/KB id。
