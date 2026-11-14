# 零售超市全局经营指标口径 v1.0

- 定义版本：`retail-metrics-v1.0.0`
- 应用：`app-21522951`
- 数据源：自有 `fresh-market`（dbId `10510`），科迈只读副本
- 统一入口：Backend Function `businessMetricGateway`
- 验证日期：2026-11-14

本文是经营指标的唯一口径来源。SQL、BFF、看板和 Agent 都必须引用本版本；修改过滤条件、事实表、关联键、退货规则或分子分母都属于口径变更，必须提升版本。

## 1. 通用规则

- 所有事实查询显式限定 `tenant_id`，默认租户 `205149`。
- 营业日取 `sold_at` 的门店本地日期，不做额外时区偏移。
- 区间参数 `startDate/endDate` 为 `YYYY-MM-DD`，含首尾，最长 92 天。
- 金额和数量已经是统一单位，不再二次除以 10000；只有直接解析源 payload 分单位字段时才按已验证规则缩放。
- 正向销售行：`sale_amount > 0`；退货行：`sale_amount < 0`；零金额行不计销售或退货。
- 比率分母为 0、成本缺失、库存覆盖不足时返回 `null`，不返回 0。
- 每个响应都带 `definitionVersion`、`coverage`、`dataAsOf`、`warnings`。
- 任何页面或 Agent 不得自行重写比率公式。

## 2. 销售与订单指标

| 指标编码 | 中文名 | 公式 / 规则 |
|---|---|---|
| `gross_sales_amount` | 正向销售额 | `SUM(sale_amount)`，仅正金额行 |
| `refund_amount` | 退货金额 | `-SUM(sale_amount)`，仅负金额行，正数展示 |
| `net_sales_amount` | 净销售额 | 全部行 `SUM(sale_amount)`；恒等于正向销售额 − 退货金额 |
| `refund_rate` | 退货率 | `refund_amount / gross_sales_amount` |
| `order_count` | 交易订单数 | 去重 `source_order_id`，含纯退货单 |
| `sales_order_count` | 正向销售订单数 | 至少一条正金额流水的去重 `source_order_id` |
| `net_quantity` | 净销售数量 | 全部行 `SUM(quantity)` |
| `gross_quantity` | 正向销售数量 | 正金额行 `SUM(quantity)` |
| `avg_order_value` | 客单价 | `gross_sales_amount / sales_order_count` |
| `items_per_order` | 件单量 | `gross_quantity / sales_order_count` |
| `avg_item_price` | 件均价 | `net_sales_amount / net_quantity` |
| `original_sales_amount` | 正向原价金额 | 正金额行原价金额合计 |
| `discount_amount` | 综合让利额 | `original_sales_amount - gross_sales_amount` |
| `discount_rate` | 综合让利率 | `discount_amount / original_sales_amount` |

订单数不再使用 `receipt_no`。小票号仅用于源单核对。

## 3. 会员指标

会员身份取结构化会员卡号；卡号为空为非会员。不使用会员姓名或手机号判断，不采集手机号。

| 指标编码 | 中文名 | 公式 / 规则 |
|---|---|---|
| `member_net_sales_amount` | 会员净销售额 | 会员卡号非空行 `SUM(sale_amount)` |
| `member_sales_order_count` | 会员正向订单数 | 会员卡号非空且有正金额流水的去重 `source_order_id` |
| `member_sales_share` | 会员销售占比 | `member_net_sales_amount / net_sales_amount` |
| `member_avg_order_value` | 会员客单价 | 会员正向销售额 ÷ `member_sales_order_count` |
| `member_identification_rate` | 会员识别率 | 有会员卡号的正向销售额 ÷ 正向销售额 |

v1.0 不提供会员复购率和生命周期价值：当前会员维表由交易派生，不是完整会员主数据。

## 4. 估算毛利

加权采购成本按租户、商品，只使用实际采购收货和采购退货：

```text
加权采购成本 =
  SUM(收货金额 - 采购退货金额)
  / NULLIF(SUM(收货数量 - 采购退货数量), 0)
```

- 采购订单行不参与成本。
- 只使用查询 `endDate` 之前存在的单据，避免未来成本解释历史销售。
- 分母必须大于 0。
- 不含期初库存、配送费、损耗和期末调整。

| 指标编码 | 中文名 | 公式 / 规则 |
|---|---|---|
| `sales_with_cost` | 可核算销售额 | 能匹配有效成本的净销售额 |
| `cost_coverage_rate` | 成本覆盖率 | `sales_with_cost / net_sales_amount` |
| `estimated_gross_profit` | 估算毛利 | 成本覆盖行 `sale_amount - quantity × 加权采购成本` |
| `estimated_gross_margin` | 估算毛利率 | `estimated_gross_profit / sales_with_cost` |

无成本商品毛利为未知，不按 0 毛利处理。该指标是估算毛利，不是财务毛利。

## 5. 库存指标

取查询截止时点前每个门店+商品的最新库存快照。可售天数固定回看 7 天，不随看板区间变化。

| 指标编码 | 中文名 | 公式 / 规则 |
|---|---|---|
| `stock_qty` | 库存数量 | 最新快照数量 |
| `stock_value` | 库存金额 | `stock_qty × cost_price`；成本价缺失时该行为 null |
| `days_cover_7d` | 近7天可售天数 | `stock_qty / (近7天净销量 / 7)` |
| `stockout_sku_count` | 缺货 SKU 数 | 近7天有销量且库存 `<= 0` |
| `low_stock_sku_count` | 低库存 SKU 数 | `0 < days_cover_7d < 3` |
| `overstock_sku_count` | 高库存 SKU 数 | `days_cover_7d > 30` |
| `slow_sku_count` | 滞销 SKU 数 | 近7天净销量 `= 0` 且库存 `> 0` |
| `snapshot_sku_count` | 快照 SKU 数 | 最新快照覆盖商品数 |
| `sold_sku_snapshot_coverage` | 销售 SKU 快照覆盖率 | 有快照的近7天销售 SKU ÷ 全部销售 SKU |
| `snapshot_sales_coverage` | 销售额快照覆盖率 | 有快照商品销售额 ÷ 近7天销售额 |
| `snapshot_age_hours` | 快照年龄 | 查询时间 − 最新快照时间 |

当前库存为抽样快照。库存响应必须带覆盖提示，不能称“全店库存周转天数”。

## 6. 采购与供应商指标

按 `document_kind` 区分下单、收货、退货；日期取 `source_created_at`。

| 指标编码 | 中文名 | 公式 / 规则 |
|---|---|---|
| `purchase_order_amount` | 采购下单额 | `document_kind = order` 金额合计 |
| `receipt_amount` | 采购收货额 | `document_kind = receipt` 金额合计 |
| `purchase_return_amount` | 采购退货额 | `document_kind = return` 金额合计 |
| `net_receipt_amount` | 净收货额 | `receipt_amount - purchase_return_amount` |
| `purchase_return_rate` | 采购退货率 | `purchase_return_amount / receipt_amount` |
| `supplier_top5_share` | 供应商 Top5 集中度 | 净收货额前5供应商 ÷ 全部供应商净收货额 |

v1.0 不提供采购及时率和订单满足率：当前没有稳定承诺交期和完整订单行到收货行对账口径。

## 7. 营销指标

营销优惠与综合让利分开计算，不互相替代。

| 指标编码 | 中文名 | 公式 / 规则 |
|---|---|---|
| `marketing_discount_amount` | 营销优惠金额 | `retail_promote_shares.amount` 合计 |
| `marketing_sales_amount` | 营销销售额 | 促销 ID 或促销单号非空的让利行销售额 |
| `marketing_original_amount` | 营销原价金额 | 对应让利行原价金额 |
| `marketing_discount_rate` | 营销让利率 | 营销优惠金额 ÷ 营销原价金额 |
| `marketing_sales_share` | 营销销售覆盖率 | 营销销售额 ÷ 同窗口同门店净销售额 |
| `promotion_link_rate` | 促销关联率 | 能关联促销维表的非空促销行 ÷ 全部非空促销行 |

活动 ROI、增量销售和活动利润贡献不在 v1.0：没有对照组和增量归因时，不能用优惠额除以销售额冒充 ROI。

## 8. 数据质量伴随指标

每次查询返回：

- `latest_biz_date`：最近有零售流水的营业日。
- `cost_coverage_rate`：成本覆盖率。
- `sold_sku_snapshot_coverage`、`snapshot_sales_coverage`、最新/最旧快照时间、快照年龄。
- `member_identification_rate`：会员识别率。
- `promotion_link_rate`：促销关联率。
- `payment_fact_count`：支付事实行数；不足 100 行时提示不可做支付结构分析。

警告码：

| 警告码 | 含义 |
|---|---|
| `COVERAGE_UNAVAILABLE` | 无法取得覆盖信息 |
| `COST_COVERAGE_PARTIAL` | 部分销售没有可用采购成本 |
| `MEMBER_IDENTIFICATION_PARTIAL` | 部分正向销售未识别会员 |
| `INVENTORY_SNAPSHOT_PARTIAL` | 库存快照未覆盖全部近7天销售 |
| `INVENTORY_SNAPSHOT_STALE` | 最新库存快照超过 24 小时 |
| `PROMOTION_LINK_PARTIAL` | 部分促销行未关联促销维表 |
| `PAYMENT_FACTS_INSUFFICIENT` | 支付事实不足，不能做支付结构结论 |

## 9. 实现与调用

### 9.1 Custom SQL

| SQL 名称 | sqlCode | 作用 |
|---|---|---|
| `metricRetailPerformance` | `21522951-4724143d` | 销售、订单、会员、综合让利、估算毛利基础度量 |
| `metricDataCoverage` | `21522951-f1358599` | 数据窗口与覆盖率 |
| `metricInventoryHealth` | `21522951-6eed52f3` | 最新库存快照、固定近7天销量 |
| `metricPurchasePerformance` | `21522951-632fa16c` | 采购下单、收货、退货、供应商维度 |
| `metricMarketingPerformance` | `21522951-b42dc83a` | 营销优惠、促销销售与关联基础度量 |

SQL 只返回基础度量和分母；比率由 BFF 统一派生。销售和覆盖 SQL 对 `retail_sale_flows` 显式使用 `(tenant_id,sold_at)` 索引，避免优化器误选租户索引导致全表扫描。

### 9.2 指标定义存储

指标定义存储在自有 MySQL 表 `metric_definitions` 中，不再由页面或 BFF 内嵌常量直接提供。

- DB_TABLE 数据集：`指标定义`
- datasetCode：`94efeaffd86e41cf86eddc4b58dcbbb6`
- 当前定义：46 条，`definition_version = retail-metrics-v1.0.0`，`enabled = 1`
- 唯一键：`(definition_version, metric_code)`
- 排序：`sort_order`，与 `business_metrics.mjs` 中原始目录一致

字段映射：

| 表字段 | 页面/BFF 字段 |
|---|---|
| `metric_code` | `code` |
| `name` | `name` |
| `group_code` | `group` |
| `unit` | `unit` |
| `formula` | `formula` |
| `source_name` | `source` |
| `nullable` | `nullable` |

初始化与同步：

- DDL：`db/migrations/schema_metric_definitions.sql`
- 同步脚本：`scripts/seed_metric_definitions.py`
- 脚本以 `kemai_pipeline/js/business_metrics.mjs` 为发布源，幂等 UPSERT，不删除表中其他版本或额外定义。
- `catalog` 从表读取当前版本、启用状态的指标；`query` 的指标白名单也使用同一份表内定义。

### 9.3 BFF

- 函数名：`businessMetricGateway`
- 类型：`ENDPOINT`
- 读取 `context.client.sql.byName(<sqlName>)`，不接受任意 SQL 名。
- 无业务写入、无外部请求、无通知。

`catalog`：

```json
{"action":"catalog"}
```

`query`：

```json
{
  "action": "query",
  "groupCode": "retail_performance",
  "startDate": "2026-11-01",
  "endDate": "2026-11-07",
  "branchId": null,
  "dimension": "branch",
  "metricCodes": ["net_sales_amount", "avg_order_value", "cost_coverage_rate"]
}
```

`health`：

```json
{"action":"health"}
```

分组与维度：

| groupCode | 默认维度 | 可选维度 |
|---|---|---|
| `retail_performance` | `day` | day / branch / department / category / item / cashier / hour |
| `inventory_health` | `branch` | branch / category / item |
| `purchase_performance` | `day` | day / branch / supplier |
| `marketing_performance` | `day` | day / branch / promotion / category / item |

未传日期时，查询先取最近营业日并回看 7 天。非法指标、非法维度、倒置日期和超过 92 天窗口会被拒绝。

返回结构：

```json
{
  "definitionVersion": "retail-metrics-v1.0.0",
  "groupCode": "retail_performance",
  "dimension": "branch",
  "window": {"startDate": "2026-11-01", "endDate": "2026-11-07"},
  "rows": [],
  "metrics": {},
  "coverage": {},
  "dataAsOf": {},
  "warnings": []
}
```

### 9.4 指标值缓存

- 表：`metric_values`，数据集：`指标值缓存`，datasetCode `3d4393d0ed434bd49666df8211e4f8b7`。
- 缓存 10 分钟；`cache_key` 同时隔离口径版本、租户、指标域、维度、门店、日期和指标集合。
- 每次重算写入新 `generation`，读取只取最新完整世代；`forceRefresh=true` 强制重算。
- 同时保存汇总指标与查询行，避免图表仍重复查询 SQL；NULL 表示不可计算，不等同缓存缺失。
- 响应包含 `cache.hit / calculatedAt / expiresAt / storedRows`，写入失败不会伪装为完整缓存。
- `health` 也使用同一 10 分钟缓存，避免首屏覆盖查询成为固定等待。
- 每个计算世代额外保存 `__PAYLOAD__` 完整响应行，命中时优先返回，避免逐页重组大结果集。
- 页面另用 `sessionStorage` 做 10 分钟会话缓存，重复进入直接渲染；手动刷新清空并强制重算。
- 该表是可重算缓存，不是历史审计快照。实现与验证记录见 `docs/kemai-metric-values-cache-2026-11-14.md` 与 `docs/kemai-metric-center-performance-2026-11-14.md`。

## 10. 2026-11-14 验收结果

以 2026-11-01 至 2026-11-07 全门店窗口验收：

| 检查 | 结果 |
|---|---|
| 净销售额 | 1,140,893.57 |
| 与旧 `dashboardRetailDaily` 净销售差额 | 0.00 |
| 成本覆盖率 | 50.17% |
| 库存快照 SKU | 177 |
| 库存风险计数合计 | 99（不超过快照 SKU） |
| 库存销售额快照覆盖率 | 1.19% |
| 采购净收货额 | 813,970.45 |
| 供应商 Top5 集中度 | 43.54% |
| 营销优惠金额（2026-11-09） | 7,512.99 |
| 促销关联率（2026-11-09） | 100% |
| 非法指标 / 倒置日期 / 93 天窗口 | 均被拒绝 |

自动核对脚本：`scripts/verify_business_metrics.py`。

## 11. 与旧看板的已知差异

旧看板 SQL 保留，不在 v1.0 自动改写。迁移时应按以下差异复核：

| 项目 | 旧看板 | v1.0 |
|---|---|---|
| 订单数 | `receipt_no` 去重 | `source_order_id` 去重 |
| 毛利成本 | 混合采购订单、收货、退货行 | 仅实际收货减采购退货，且截止查询结束日 |
| 库存可售天数 | 跟随看板日期区间 | 固定近 7 天 |
| 营销与综合让利 | 口径分散 | 分开定义，不互相替代 |
| 租户过滤 | 部分 SQL 依赖单租户现状 | 所有 SQL 显式限定 tenant_id |

旧看板迁移为独立任务；迁移完成前，新入口与旧看板并存，但新页面和 Agent 必须使用 `businessMetricGateway`。

## 12. 变更流程

1. 修改口径前先更新本文和 `definitionVersion`。
2. 同步修改 `kemai_pipeline/js/business_metrics.mjs` 与对应 SQL。
3. 运行 `python3 scripts/build_business_metric_gateway.py` 生成 BFF。
4. 运行 `node --test tests/pipeline/business-metrics-policy.test.mjs tests/pipeline/business-metrics-gateway.test.mjs`。
5. 运行 `python3 scripts/verify_business_metrics.py`。
6. 发布 SQL、BFF，并更新公司知识库。

任何口径变更都不得只改页面展示或只改 SQL 注释。
