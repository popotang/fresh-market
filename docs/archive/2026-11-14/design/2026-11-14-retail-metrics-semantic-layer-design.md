# 零售超市全局指标语义层 v1.0 设计

日期：2026-11-14  
应用：`app-21522951`  
状态：已确认按“Custom SQL 基础度量 + 统一 BFF 指标入口”实施

## 1. 目标与边界

建立一套版本化、可执行、可审计的零售超市指标语义层，使看板、Agent、Backend Function 和知识库对同一指标使用同一口径。

本期交付：

- 5 条只读 Custom SQL，负责同库 JOIN、过滤和基础聚合。
- 1 个只读 ENDPOINT `businessMetricGateway`，负责指标目录、派生公式、参数校验、版本和覆盖率。
- 项目文档与公司知识库《零售超市全局经营指标口径 v1.0》。
- 真实窗口执行、BFF smoke、数值不变量和知识库回读验证。

本期不做：

- 不修改老板驾驶舱、店长作战台的 UI 或调用链；旧 SQL 暂作兼容资源。
- 不建汇总表、定时刷新任务或物化回填。
- 不采集新的科迈主数据，不写科迈 SaaS，不改源数据。
- 不把估算毛利、抽样库存、交易派生会员/促销维表包装成财务或源主数据结论。

## 2. 统一语义原则

### 2.1 数据范围

- 所有事实查询显式限定 `tenant_id`；当前默认租户为 `205149`，BFF 参数必须显式传递或在受控服务端常量中固定，不能由 SQL 调用方省略。
- 门店维度统一使用 `source_branch_id`；编码维度关联可用 `branch_code`，两种键不得在同一 JOIN 中混用。
- 营业日取 `sold_at` 的门店本地日期，不做额外时区偏移；区间参数为 `startDate/endDate`，含首尾，SQL 使用 `< endDate + 1 day`。
- 采购单据日期使用 `source_created_at`；库存快照使用 `captured_at`；同步入库时间不得冒充业务发生时间。

### 2.2 金额、退货和空值

- 事实表中的金额、数量已经是本项目统一单位，不再额外除以 10000；只有直接解析源 payload 的分单位字段时才按已验证规则缩放。
- 正向销售行：`sale_amount > 0`；退货行：`sale_amount < 0`；零金额行不计销售或退货。
- 比率分母为 0、成本缺失、库存覆盖不足或数据不可用时返回 `NULL` 和原因，不返回伪造的 0。
- 指标响应必须带 `definitionVersion`、`dataAsOf`、`coverage`、`warnings`；抽样或覆盖不足不能省略提示。

### 2.3 指标版本

- 当前版本：`retail-metrics-v1.0.0`。
- 修改过滤条件、事实表、关联键、退货规则、分子或分母都属于口径变更，必须提升版本并记录变更。
- 性能优化若不改变结果，可以不升业务版本，但需保留对账证据。

## 3. 指标目录

### 3.1 销售与订单

| metricCode | 中文名 | 公式 / 规则 |
|---|---|---|
| `gross_sales_amount` | 正向销售额 | `SUM(sale_amount)`，仅 `sale_amount > 0` |
| `refund_amount` | 退货金额 | `-SUM(sale_amount)`，仅 `sale_amount < 0`，正数展示 |
| `net_sales_amount` | 净销售额 | 全部行 `SUM(sale_amount)`；恒等于正向销售额 − 退货金额 |
| `refund_rate` | 退货率 | `refund_amount / gross_sales_amount` |
| `order_count` | 交易订单数 | 区间内所有流水去重 `source_order_id`，含纯退货单 |
| `sales_order_count` | 正向销售订单数 | 至少有一条正金额流水的去重 `source_order_id` |
| `net_quantity` | 净销售数量 | 全部行 `SUM(quantity)` |
| `gross_quantity` | 正向销售数量 | 正金额行 `SUM(quantity)` |
| `avg_order_value` | 客单价 | `gross_sales_amount / sales_order_count` |
| `items_per_order` | 件单量 | `gross_quantity / sales_order_count` |
| `avg_item_price` | 件均价 | `net_sales_amount / net_quantity` |
| `original_sales_amount` | 正向原价金额 | 正金额行原价金额合计 |
| `discount_amount` | 综合让利额 | 正金额行 `original_amount - sale_amount` 合计 |
| `discount_rate` | 综合让利率 | `discount_amount / original_sales_amount` |

订单去重不再使用 `receipt_no`。`receipt_no` 仅用于展示和源单核对，避免跨日、跨店或流水映射时重复计数。

### 3.2 会员

会员身份统一取已结构化的会员卡号；会员卡号为空为非会员，不使用会员名称或手机号判断。

| metricCode | 中文名 | 公式 / 规则 |
|---|---|---|
| `member_net_sales_amount` | 会员净销售额 | 会员卡号非空行 `SUM(sale_amount)` |
| `member_sales_order_count` | 会员正向订单数 | 会员卡号非空且有正金额流水的去重 `source_order_id` |
| `member_sales_share` | 会员销售占比 | `member_net_sales_amount / net_sales_amount` |
| `member_avg_order_value` | 会员客单价 | 会员正向销售额 ÷ `member_sales_order_count` |
| `member_identification_rate` | 会员识别率 | 有会员卡号的正向销售额 ÷ 正向销售额 |

会员复购率、会员生命周期价值不进入 v1.0：当前会员维表由交易派生，不能当作完整会员主数据。

### 3.3 估算毛利

加权采购成本按租户、商品计算，只使用实际采购收货与采购退货：

```text
加权采购成本 =
  SUM(收货金额 - 采购退货金额)
  / NULLIF(SUM(收货数量 - 采购退货数量), 0)
```

- 采购订单行不参与成本计算。
- 成本只使用查询 `endDate` 之前已存在的单据，避免用未来采购价解释历史销售。
- 分母必须大于 0，否则该商品成本不可用。
- 成本不是财务移动加权成本，不含期初库存、配送费、损耗和期末调整。

| metricCode | 中文名 | 公式 / 规则 |
|---|---|---|
| `sales_with_cost` | 可核算销售额 | 能匹配有效成本行的 `SUM(sale_amount)` |
| `cost_coverage_rate` | 成本覆盖率 | `sales_with_cost / net_sales_amount` |
| `estimated_gross_profit` | 估算毛利 | 成本覆盖行 `sale_amount - quantity × 加权采购成本` |
| `estimated_gross_margin` | 估算毛利率 | `estimated_gross_profit / sales_with_cost` |

无成本商品的毛利为未知，不按 0 毛利处理。

### 3.4 库存

库存统一取查询截止时点前每个 `tenant_id + branch_id + product_id` 的最新快照。

| metricCode | 中文名 | 公式 / 规则 |
|---|---|---|
| `stock_qty` | 库存数量 | 最新有效快照 `stock_qty` |
| `stock_value` | 库存金额 | `stock_qty × cost_price`；成本价缺失时该行为 NULL |
| `days_cover_7d` | 近7天可售天数 | `stock_qty / (近7天净销量 / 7)`；近7天净销量必须大于 0 |
| `stockout_sku_count` | 缺货 SKU 数 | 近7天有销量且库存 `<= 0` |
| `low_stock_sku_count` | 低库存 SKU 数 | `0 < days_cover_7d < 3` |
| `overstock_sku_count` | 高库存 SKU 数 | `days_cover_7d > 30` |
| `slow_sku_count` | 滞销 SKU 数 | 近7天净销量 `= 0` 且库存 `> 0` |
| `snapshot_sku_count` | 快照 SKU 数 | 当前有效快照覆盖的商品数 |
| `sold_sku_snapshot_coverage` | 销售 SKU 快照覆盖率 | 有快照的近7天销售 SKU ÷ 近7天全部销售 SKU |
| `snapshot_sales_coverage` | 销售额快照覆盖率 | 有快照商品的近7天销售额 ÷ 近7天总销售额 |
| `snapshot_age_hours` | 快照年龄 | 查询执行时间 − 最新快照时间 |

可售天数固定回看 7 天，不随展示区间改变。当前库存样本小，所有库存响应必须带覆盖提示，不能称“全店库存周转天数”。

### 3.5 采购与供应商

| metricCode | 中文名 | 公式 / 规则 |
|---|---|---|
| `purchase_order_amount` | 采购下单额 | `document_kind = order` 的 `SUM(amount)` |
| `receipt_amount` | 采购收货额 | `document_kind = receipt` 的 `SUM(amount)` |
| `purchase_return_amount` | 采购退货额 | `document_kind = return` 的 `SUM(amount)`，正数展示 |
| `net_receipt_amount` | 净收货额 | `receipt_amount - purchase_return_amount` |
| `purchase_return_rate` | 采购退货率 | `purchase_return_amount / receipt_amount` |
| `supplier_top5_share` | 供应商 Top5 集中度 | 净收货额前 5 供应商之和 ÷ 全部供应商净收货额 |

采购及时率、订单满足率不进入 v1.0，因为当前数据没有稳定承诺交期和完整订单行到收货行对账口径。

### 3.6 营销

综合让利与营销优惠分别计算，不相互替代。

| metricCode | 中文名 | 公式 / 规则 |
|---|---|---|
| `marketing_discount_amount` | 营销优惠金额 | `retail_promote_shares.amount` 合计 |
| `marketing_sales_amount` | 营销销售额 | 促销 ID 或促销单号非空的让利行 `sale_amount` 合计 |
| `marketing_original_amount` | 营销原价金额 | 对应让利行 `original_amount` 合计 |
| `marketing_discount_rate` | 营销让利率 | `marketing_discount_amount / marketing_original_amount` |
| `marketing_sales_share` | 营销销售覆盖率 | `marketing_sales_amount / net_sales_amount`，同窗口同门店 |
| `promotion_link_rate` | 促销关联率 | 非空促销 ID/单号能关联促销维表的行数 ÷ 非空促销行数 |

活动 ROI、增量销售、活动利润贡献不进入 v1.0；没有对照组和增量归因时不能用优惠额除以销售额冒充 ROI。

### 3.7 数据质量伴随指标

每次查询按适用范围返回：

- `latest_biz_date`：最近有零售流水的营业日。
- `cost_coverage_rate`：成本覆盖率。
- `sold_sku_snapshot_coverage`、`snapshot_sales_coverage`、最新/最旧库存快照时间。
- `member_identification_rate`：会员识别率。
- `promotion_link_rate`：促销关联率。
- `payment_fact_count`：当前支付事实行数；不足以做支付结构分析时明确提示。
- `source_window_warnings`：缺日、样本、旧快照或口径限制。

## 4. Custom SQL 设计

所有 SQL 位于 `.rabetbase/sql/app-21522951/fresh-market/`，只读自有数据库 `10510 / fresh-market`。

### 4.1 `metricRetailPerformance`

输入：`tenantId`、`startDate`、`endDate`、可选 `branchId`、`dimension`（`day/branch/department/category/item/cashier/hour`）。

输出按日期或维度分组的基础度量：正/负销售金额、数量、订单键、原价、会员金额、成本覆盖金额、估算毛利。比率不在 SQL 中重复定义，由 BFF 统一计算。

成本子查询按 `tenant_id + source_item_id` 汇总实际收货与退货，并限制到 `endDate` 截止。

### 4.2 `metricInventoryHealth`

输入：`tenantId`、`endDate`、可选 `branchId`、`dimension`（`branch/category/item`）。固定回看 `endDate - 6` 至 `endDate`。

输出最新库存快照、近7天销量、最后销售时间、库存金额、快照时间及风险分类所需的原始字段。SKU 计数和覆盖率由 BFF 汇总，单 SQL 不返回“全店”结论。

### 4.3 `metricPurchasePerformance`

输入：`tenantId`、`startDate`、`endDate`、可选 `branchId`、`dimension`（`day/branch/supplier`）。

输出下单、收货、退货、净收货额和供应商维度基础值；Top5 集中度由 BFF 根据完整供应商集合计算，不能从 SQL `LIMIT 5` 的结果推算全体分母。

### 4.4 `metricMarketingPerformance`

输入：`tenantId`、`startDate`、`endDate`、可选 `branchId`、`dimension`（`day/branch/promotion/category/item`）。

输出营销优惠、营销销售、原价、订单、关联成功/失败基础计数与金额；与综合让利分开命名。

### 4.5 `metricDataCoverage`

输入：`tenantId`、可选 `startDate/endDate/branchId`。

输出最新营业日、事实行数、销售成本覆盖、库存快照覆盖、会员识别、促销关联、支付事实行数和数据时间范围。它只描述自有副本，不宣称源端实时状态。

## 5. `businessMetricGateway` 契约

类型：ENDPOINT；文件目标 `.rabetbase/bff/app-21522951/ENDPOINT/businessMetricGateway.js`。

### 5.1 `action=catalog`

无需日期。返回指标编码、中文名、单位、公式说明、所属 SQL、适用维度、限制和 `definitionVersion`。

### 5.2 `action=query`

参数：

```json
{
  "action": "query",
  "groupCode": "retail_performance",
  "metricCodes": ["net_sales_amount", "avg_order_value"],
  "tenantId": "205149",
  "startDate": "2026-11-01",
  "endDate": "2026-11-07",
  "branchId": null,
  "dimension": "day"
}
```

- `groupCode` 可选值：`retail_performance`、`inventory_health`、`purchase_performance`、`marketing_performance`。
- `metricCodes` 缺省返回该组全部指标；传数组时只返回白名单内指标。
- 日期必须是 `YYYY-MM-DD` 且 `startDate <= endDate`；默认窗口为最近有数据的 7 天，不直接假设今天有数据。
- 查询窗口最长 92 天；库存仍固定回看 7 天。
- 维度只允许各组白名单，拒绝任意表名、字段名、SQL 片段或动态排序表达式。

返回：

```json
{
  "definitionVersion": "retail-metrics-v1.0.0",
  "groupCode": "retail_performance",
  "dimension": "day",
  "rows": [],
  "metrics": {},
  "coverage": {},
  "dataAsOf": null,
  "warnings": []
}
```

### 5.3 `action=health`

调用 `metricDataCoverage`，返回覆盖和新鲜度，不返回经营结论。

### 5.4 错误与权限

- 非法指标、维度、日期或租户参数返回明确错误，不静默换默认值。
- BFF 使用 `context.client.sql.byName(sqlName).execute({ params })` 调用已发布 SQL。
- SQL 或数据集权限错误保留原始错误，不改写为“无数据”。
- 本 ENDPOINT 无任何业务写入、外部通知或科迈调用。

## 6. 兼容与迁移边界

- 现有 `dashboardRetailDaily`、`dashboardRetailBreakdown`、`dashboardInventoryRisk`、`dashboardPurchaseSpend` 保留，不在本期推送页面变更。
- 新旧销售净额应在同窗口一致；订单数、估算毛利和库存可售天数因本次口径修正允许出现已解释差异。
- 旧页面迁移是后续独立任务，迁移后旧 SQL 才能标记退役；本期不删除任何旧 SQL。

## 7. 验证方案

### 7.1 SQL 与数值

- `rabetbase sql validate`、push dry-run、正式发布、真实 `sql exec`。
- 至少执行单日、近7天、跨月三类窗口，并带/不带门店参数。
- 验证：`gross_sales_amount - refund_amount = net_sales_amount`。
- 验证：订单去重键全部为 `source_order_id`；纯退货单不进入客单价分母。
- 验证：比率分母为 0 返回 NULL；无成本商品不贡献估算毛利。
- 验证：采购订单不进入加权成本，未来单据不进入历史成本。
- 验证：营销优惠与综合让利分别对账，促销关联率不超过 100%。
- 与旧看板同窗口核对净销售额，记录订单、毛利、库存口径的预期差异。

### 7.2 BFF

- `catalog`、`health`、四组 `query` smoke。
- 非法指标、非法维度、倒置日期、超过 92 天必须拒绝。
- 返回包含版本、覆盖、数据时间和警告；SQL 权限错误不得被吞掉。

### 7.3 知识库

- 项目文档与 KB 使用同一 UTF-8 Markdown 源文件。
- `rabetbase kb create --dry-run` 后创建公司知识库。
- 用 `kb detail` 回读正文和版本，用 `kb search` 验证“净销售额、估算毛利、可售天数、营销优惠”能召回。
- 文档与日志不写凭据、签名 URL、会员手机号或原始业务明细。

## 8. 完成定义

只有以下全部满足才称完成：

1. 5 条 SQL 和 BFF 已发布，且上述真实查询与负向测试通过。
2. 指标目录、公式、数据来源、限制和版本在代码与文档中一致。
3. 关键销售不变量和覆盖率核验有结果，不把未验证项写成通过。
4. 公司知识库可回读、可检索。
5. 旧页面和旧 SQL 未被擅自修改或删除，科迈侧零写操作。
