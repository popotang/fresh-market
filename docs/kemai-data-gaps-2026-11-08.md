# 科迈 SaaS 导入数据欠缺评估（2026-11-08 运行态实测）

> 证据口径：`lovrabet` 运行态只读查询 app-21522951 的 34 个数据集（DB_TABLE），
> 查询时间 2026-11-08 16:20–16:40 CST。数字是**查询时点**读数，后续同步即会变化。
> 与 `kemai-deepseek-run-report-2026-11-08.md` 的差异：本文用运行态表重新计数，并补充了
> 「单据级行覆盖」等此前未量化的口径；两者冲突时以本文的运行态 SQL 计数为准并注明时间。

## 一、结论速览

1. **新鲜度欠约 18 小时**：库内零售最新成交时间 `2026-11-07 22:30:27`，**2026-11-08 订单 0 单**；
   最近一次成功任务停在 `2026-11-08 03:42`，日常 supervisor 处于 `paused_by_user`，`sync_live_cursors` 为空。
2. **粒度性缺口（结构性）**：零售只有单头 + 流水映射行（无订单详情/支付）；采购 17,166 张单里
   只有 **1,493 张（8.7%）** 有商品行、106 张有审批、126 张有分支分配；商品 14 类页签只有
   **177/18,390 商品（0.96%）** 采过，待补 204,836 个对象。
3. **字段没落成列、只躺在 payload 里**：支付方式、会员、收银员、供应商名、税额、审批人、
   商品名/条码/进价/零售价等都能在 `payload` JSON 里读到，但结构化列很薄，无法直接按字段过滤、聚合、建页面。

## 二、按数据类别现状

| 类别 | 已入库 | 覆盖级别 | 已确认欠缺 |
|---|---:|---|---|
| 零售订单头 | 132,401 | `headers_only` | 11-08 无数据；订单详情/支付数组在 payload 中为 `null` |
| 销售流水 | 474,193 | `full_list` | 无稳定源主键用于单行对账；结构化列少 |
| 零售订单行 | 474,193 | `flow_mapped` | 由流水映射，非订单详情；行数与流水完全相等 |
| 零售支付 | **2** | 采样 | 首次详情补采失败（零售详情等待超时），`retail_order_detail_state` 仅 1 行 failed |
| 采购单据头 | 17,166（订单 11,389 / 收货 3,591 / 退货 2,186） | `partial_detail` | 647 张单据缺附属数组（branchs/approves） |
| 采购单据行 | 24,814 行 / **1,493 张单据** | 同上 | 15,673 张单据无行；多数可从 payload `details` 零请求回填 |
| 采购分支分配 | 126 行 / 126 张单 | 同上 | 覆盖率 0.7% |
| 采购审批 | 176 行 / 106 张单 | 同上 | 覆盖率 0.6% |
| 历史已审核收货行 | 126,273 | `approved_rows_only` | 11 个月窗缺内部单据 ID / 完整单头；未审核历史源端无接口 |
| 商品主档 | 18,390 | `full_catalog` | 字段齐全（条码/进价/零售价/类别/品牌/供应商 100% 非空） |
| 商品详情 | 18,390 | `full_catalog` | 已全覆盖 |
| 商品 14 类页签 | 每类 177 个商品 | `changed_only` | 待补 204,836 个对象；价格/库存/价格历史等只剩极少量快照 |
| 商品子记录 | 15,727 | 同上 | 按 section：supply 14,390 / history_price 462 / price 类 354 / cut 138 / promotion 28 / split 1 |
| 供应商 | 213 | `observed_only` | 只含商品清单引用到的供应商，非完整名录 |
| 主数据（机构/部门/类别/品牌/单位） | 2 / 6 / 801 / 1,816 / 116 | `full_catalog` | 完整；但零售仅来自 1 家门店（`69028303`） |
| 原始捕获 | 803,017 | — | 可作为回填与复算底座 |
| 缺口台账 `sync_coverage_gaps` | 690 | — | **全部 pending**，无 resolved |
| 对账缺失 `sync_reconcile_missing` | **0** | — | 流程未产出记录，不能据此认为无缺失 |

## 三、缺口台账明细（690 条，全 pending）

| stream | 条数 | 缺什么 |
|---|---:|---|
| purchase-orders | 310 | `document_lines`、`branch_allocations`、`approval_history` |
| purchase-returns | 272 | 同上 |
| purchase-receipts | 68 | 同上 |
| retail-orders | 28（逐日） | `order_details`、`payment_flows` |
| purchase-receive-history | 11（月窗） | 内部单据 ID、完整单头/明细 |
| product-tabs | 1 | 13 类页签初始全量（204,836 对象） |

## 四、字段级：结构化列 vs 原始 payload

| 表 | 已结构化列 | payload 里还有但未落列 |
|---|---|---|
| `retail_orders` | 20 列（含单号、金额、成交时间、渠道） | 会员姓名/卡号/手机（会员单非空 67,666/132,401）、收银员/班次、折扣、`payAnalysis`、订单类型、状态、门店名 |
| `retail_sale_flows` | 15 列 | 商品名/编码/条码/规格、供应商名、单位、折扣/券分摊明细、促销码、销售员 |
| `purchase_documents` | 20 列（供应商/门店只有 ID） | `supplierName`/Code/联系人、`payState`/`settledAmount`/`payTime`、`approveResult`/审批人、税额/不含税/零售额合计、`status` |
| `purchase_document_lines` | 8 列（数量/单价/金额/税额） | 商品名/编码/条码/规格、单位、零售价、进价、供应商金额、税率、批次/效期 |

## 五、可用性与数据质量问题

1. **大表查询必须带 `tenant_id` 前缀**：`retail_sale_flows`（474,193 行）不带前缀按 `sold_at` 排序
   15s 超时；加 `tenant_id=205149` 后 1.1s 返回。建页面/接口时要用带前缀的复合索引条件。
2. **毫秒时间戳做 where 条件会被静默忽略**：`{"sold_at":{"$gte":1794067200000}}` 与不加条件返回
   完全相同的行；改成 `"2026-11-08 00:00:00"` 才生效（实测 11-08 返回 0 单）。聚合/页面筛选一律用日期字符串。
3. `product_branch_stocks` 的 `source_key` 不含商品维度：177 行共用同一 key，跨商品重复；
   DDL 用 `(product_id, source_key)` 联合唯一键兜住，但该 key 不能当跨表关联键。
4. `product_items` 仍有 100 行遗留平面导入（与 `products` 重复），未清理。
5. 缺口台账只增不减：没有任何 `resolved` 记录；采购类 gap 即使后续取到详情也不会自动关闭。
6. `product-tabs` 每类 18,213 个商品停在 pending（`attempts<3` 才有资格被选中），无独立重试入口。
7. 零售/支付补采保持暂停；`retail_order_detail_state` 只有 1 条失败记录，132,400 单从未尝试。

## 六、可补 / 不可补判断

| 欠项 | 可补性 | 量级 |
|---|---|---|
| 采购商品行（15,673 张单） | **优先零请求回填**：payload `details` 已存 | 需先抽样验证 details 非空比例 |
| 采购 orders 的分支/审批 | payload 里 orders 通常带 1 条 branchs/approves，可回填 | 需按单据校验 |
| 采购 receipts/returns 的 branchs/approves | 源端列表接口即返回 `null`，需详情补采 | 647 张单 + 历史全量，0.5/s |
| 零售订单详情/支付 | 可补但慢：127k+ 单，0.5/s 理论下限 70h+ | 仅 1 单试过且失败 |
| 商品 13 类页签 | 可补：0.5/s 理论下限约 114h | 204,836 对象 |
| 历史未审核收货 | 源端无接口 | 不可补 |
| 零售 OD Excel | 26 个单头字段，无商品行/支付 | 不能替代详情 |
| 支付方式/会员分析 | 依赖订单详情；或从 `payAnalysis`/payload 解析 | 部分可做 |

## 七、复现命令（只读）

```bash
# 数据新鲜度
lovrabet data filter --code daf9828f96f742c8ad748dcc651b148d \
  --params '{"currentPage":1,"pageSize":1,"orderBy":[{"sold_at":"desc"}]}' --format json
lovrabet data filter --code daf9828f96f742c8ad748dcc651b148d \
  --params '{"currentPage":1,"pageSize":1,"where":{"sold_at":{"$gte":"2026-11-08 00:00:00"}}}' --format json

# 采购「单据级行覆盖」：1,493 张单有行
lovrabet data aggregate --code f571f75aeafa4cc38d15817a986d75b4 \
  --params '{"aggregate":[{"type":"COUNT","column":"id","alias":"cnt"}],"groupBy":["document_id"],"pageSize":1}' --format json

# 商品页签补采进度（按 stream/status 汇总）
lovrabet data aggregate --code edfb7b0eb0304e6ba1002a839e3e224a \
  --params '{"aggregate":[{"type":"COUNT","column":"source_key","alias":"cnt"}],"groupBy":["stream","status"],"pageSize":100}' --format json

# 缺口台账
lovrabet data aggregate --code 594b7816ca994de1b65c815914dc1e88 \
  --params '{"aggregate":[{"type":"COUNT","column":"id","alias":"cnt"}],"groupBy":["stream","status"],"pageSize":100}' --format json
```

## 八、建议优先级

- **P0**：先决定新鲜度（恢复日常 supervisor 或启用 `kemai_live_service` 托管），否则所有分析都停留在 11-07。
- **P1**：零请求回填采购行 + orders 分支/审批（验证 payload 后跑批），把「有行单据占比」从 8.7% 拉起来。
- **P1**：字段结构化决策：支付/会员/供应商名/税额等是落列、还是统一走 payload 解析视图。
- **P2**：零售详情/支付与商品页签全量补采（都是小时级到天级的 0.5/s 任务，需分批授权）。
- **P2**：清理 `product_items` 遗留表；给 `retail_order_lines`/`retail_sale_flows` 的常用查询加带 `tenant_id` 前缀的索引条件；修正 `product_branch_stocks.source_key` 语义。
