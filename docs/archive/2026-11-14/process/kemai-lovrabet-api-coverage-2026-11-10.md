# 科迈 → 数据中心：覆盖基线

生成时间：2026-11-10 22:26 CST。模式：`api`；截止日：2026-11-10；源操作 **21** 次。

## 一、近 3 天列表总量对比（直连只读 API）

| 流 | 日期 | 源端 | 数据中心 | 差额 | 结果 | 依据 | 页数 |
|---|---|---:|---:|---:|---|---|---:|
| `retail-sales` | 2026-11-10 | 12,770 | 0 | +12,770 | mismatch | source_statistical_total | 0 |
| `retail-sales` | 2026-11-09 | 13,228 | 13,228 | +0 | match | source_statistical_total | 0 |
| `retail-sales` | 2026-11-08 | 19,482 | 19,482 | +0 | match | source_statistical_total | 0 |
| `retail-orders` | 2026-11-10 | 3,772 | 0 | +3,772 | mismatch | source_statistical_total | 0 |
| `retail-orders` | 2026-11-09 | 3,856 | 2,024 | +1,832 | mismatch | source_statistical_total | 0 |
| `retail-orders` | 2026-11-08 | 4,940 | 4,940 | +0 | match | source_statistical_total | 0 |
| `purchase-orders` | 2026-11-10 | 62 | 0 | +62 | mismatch | source_statistical_total | 0 |
| `purchase-orders` | 2026-11-09 | 33 | 20 | +13 | mismatch | source_statistical_total | 0 |
| `purchase-orders` | 2026-11-08 | 33 | 35 | -2 | mismatch | source_statistical_total | 0 |
| `purchase-receipts` | 2026-11-10 | 35 | 0 | +35 | mismatch | source_statistical_total | 0 |
| `purchase-receipts` | 2026-11-09 | 24 | 22 | +2 | mismatch | source_statistical_total | 0 |
| `purchase-receipts` | 2026-11-08 | 28 | 28 | +0 | match | source_statistical_total | 0 |
| `purchase-returns` | 2026-11-10 | 8 | 0 | +8 | mismatch | source_statistical_total | 0 |
| `purchase-returns` | 2026-11-09 | 9 | 7 | +2 | mismatch | source_statistical_total | 0 |
| `purchase-returns` | 2026-11-08 | 19 | 19 | +0 | match | source_statistical_total | 0 |
| `purchase-receive-history` | 2026-11-10 | 527 | 0 | +527 | mismatch | source_statistical_total | 0 |
| `purchase-receive-history` | 2026-11-09 | 357 | 0 | +357 | mismatch | source_statistical_total | 0 |
| `purchase-receive-history` | 2026-11-08 | 352 | 0 | +352 | mismatch | source_statistical_total | 0 |
| `retail-promote-sharing` | 2026-11-10 | 9,594 | 0 | +9,594 | mismatch | source_statistical_total | 0 |
| `retail-promote-sharing` | 2026-11-09 | 9,762 | 9,762 | +0 | match | source_statistical_total | 0 |
| `retail-promote-sharing` | 2026-11-08 | 14,127 | 0 | +14,127 | mismatch | source_statistical_total | 0 |

## 三、边界

- 列表接口本身对全部流返回 `totalRecord=0`；精确总数取自各报表的 `queryStatisticalInfo`（实测每条流 1 次请求）。
- 零售流水在数据中心按 `sold_at` 计数（有索引）；`source_created_at` 无索引会 15 秒超时。
- 当天（未结算）窗口的差额属预期，标记 provisional，不计入不一致。
- 数据中心的 `totalCount` 与状态表的 `covered_through` 只说明库内事实，不证明源端实时完整。
- 详情/对象覆盖只是库内行数，不代表源端应有数量；库存对象暂无数据集映射。
- 本文件只有聚合计数，不含原始业务明细。
