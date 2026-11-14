# 科迈 → 数据中心：覆盖基线

生成时间：2026-11-10 21:59 CST。模式：`local`；截止日：2026-11-10；源操作 **0** 次。

## 一、列表流（数据中心口径，未访问源）

| 流 | 表 | 库内行数 | 配置起始 | 连续核验到 | 最后成功 | 最近结果 |
|---|---|---:|---|---|---|---|
| `retail-sales` | `retail_sale_flows` | 506,903 | 2026-08-01 | 2026-11-07 00:00 | 2026-11-10 00:11 | success |
| `retail-orders` | `retail_orders` | 139,365 | 2026-08-01 | 2026-11-07 00:00 | 2026-11-10 00:21 | success |
| `purchase-orders` | `purchase_documents` | 11,444 | 2025-01-01 | 2026-11-07 00:00 | 2026-11-10 00:03 | failed |
| `purchase-receipts` | `purchase_documents` | 3,641 | 2026-08-08 | 2026-11-07 00:00 | 2026-11-10 00:21 | success |
| `purchase-returns` | `purchase_documents` | 2,212 | 2026-01-01 | 2026-11-07 00:00 | 2026-11-10 00:22 | success |
| `purchase-receive-history` | `purchase_receipt_history_lines` | 126,273 | 2025-01-01 | 2026-11-07 00:00 | 2026-11-08 01:47 | success |
| `retail-promote-sharing` | `retail_promote_shares` | 9,762 | 2026-10-01 | — | — | not_registered |

## 二、详情/对象覆盖（仅库内计数）

| 对象 | 表 | 已入库 | 基数 | 1:1 完成度 | 每基数行数 | 状态 |
|---|---|---:|---:|---:|---:|---|
| 商品详情 profile | `product_profile` | 18,398 | 18479 | 99.6% | 1.0 | measured |
| 商品页签子记录 | `product_child_records` | 15,754 | 18479 | — | 0.85 | measured |
| 采购商品行 | `purchase_document_lines` | 27,930 | 17297 | — | 1.61 | measured |
| 采购分支 | `purchase_document_branches` | 174 | 17297 | — | 0.01 | measured |
| 采购审批 | `purchase_document_approvals` | 272 | 17297 | — | 0.02 | measured |
| 库存快照 | `product_branch_stocks` | 未知 | — | — | — | no_dataset_mapping |

待复核覆盖缺口台账：**691** 条 pending。

## 三、边界

- 源端列表接口对全部流返回 `totalRecord=0`，因此“一个请求拿源端总量”不可行；`local` 模式不访问源。
- 数据中心的 `totalCount` 与状态表的 `covered_through` 只说明库内事实，不证明源端实时完整。
- 详情/对象覆盖只是库内行数，不代表源端应有数量；库存对象暂无数据集映射。
- 本文件只有聚合计数，不含原始业务明细。
