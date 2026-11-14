# 让利物理表与业务维表建设记录（2026-11-14）

应用：`app-21522951`；数据库：自有 `fresh-market`（dblink `10510`）。
本次只写自有 RDS 和 Lovrabet 元数据；科迈 SaaS 没有任何写操作。
数据库凭据不进入本文、迁移脚本或 DDL。

## 一、交付物

- DDL：`db/migrations/schema_business_dimensions.sql`
- 维表派生：`db/migrations/seed_business_dimensions.sql`
- 让利迁移：`scripts/migrate_retail_promote_shares.py`

新增 5 张物理表及对应 DB_TABLE 数据集：

| 物理表 | 数据集 | datasetCode | 行数 |
|---|---|---|---:|
| `retail_promote_shares` | 零售让利明细（物理表） | `1004be63fb644d64973590f8d6c5d0f8` | 9,762 |
| `members` | 会员 | `af02c01b09ff4f76b2b851eb1d66cf13` | 18,808 |
| `staff_members` | 人员 | `5c75b16092a94065ba614a0bcb9e699a` | 44 |
| `payways` | 付款方式 | `66e172bda29743bfa0aaffc0a1ae4eeb` | 1 |
| `promotions` | 促销活动 | `1baeadf555b94711836b4e81e0534bc7` | 122 |

原 METADATA 数据集 `零售让利明细`（`fde264b695ce39d2bca6d0065449f31e`）保留，未删除、未改写。
新物理表数据集显式改名，避免两个同名数据集混淆。

## 二、让利明细迁移

迁移脚本从原 METADATA 按 `id` 稳定分页读取，每页 500 行，共 20 页；
目标按 `(tenant_id, source_row_hash, occurrence_no)` 唯一键 `INSERT IGNORE`，支持中断后续跑。

实测结果：

```json
{"sourceTotal":9762,"targetBefore":0,"targetAfter":9762,"insertedThisRun":9762,"complete":true}
```

源端字段逐列映射，金额、数量、原始 JSON、源观察时间均保留；DATETIME 的 epoch/ISO 表示统一转为 UTC naive DATETIME。
本次是一次性副本迁移，**不代表原 METADATA 后续新增行会自动进入物理表**；持续同步仍需在流水线中切换目标或增加双写验收。

## 三、维表口径

当前记录是从**自有交易副本派生的初始维度**，不是科迈会员/人员/付款方式/促销主数据已经同步的证明：

- 会员：订单中的来源会员 ID、会员卡号、等级，以及让利明细中的卡号/等级。
- 人员：采购审批人 + 零售订单收银员；用 `approver_flag` / `cashier_flag` 区分来源角色。
- 付款方式：当前已采集零售支付行中的编码和名称。只有 1 行维度是因为当前支付事实仅 2 行，不能推断科迈只有 1 种付款方式。
- 促销活动：促销 ID 与促销单号按两种身份分别补齐，避免组合去重造成断链。

会员表没有 `memberPhone`、手机号或其他联系方式列；维表派生也没有复制订单 payload。
实测会员 payload 全为 NULL，手机号列数为 0。

## 四、新增/修正关系

新增 10 条（relationId `38603`–`38612`）：

| relationId | from | to | labelField |
|---|---|---|---|
| 38603 | `retail_promote_shares.source_order_id` | `retail_orders.source_order_id` | `receipt_no` |
| 38604 | `retail_promote_shares.branch_code` | `branches.branch_code` | `branch_name` |
| 38605 | `retail_promote_shares.item_category_id` | `categories.source_category_id` | `category_name` |
| 38606 | `retail_promote_shares.item_brand_id` | `brands.source_brand_id` | `brand_name` |
| 38607 | `retail_promote_shares.member_card_no` | `members.member_card_no` | `member_card_no` |
| 38608 | `retail_promote_shares.promotion_id` | `promotions.source_promotion_id` | `activity_name` |
| 38609 | `retail_promote_shares.promotion_code` | `promotions.promotion_code` | `activity_name` |
| 38610 | `retail_orders.source_member_id` | `members.source_member_id` | `member_card_no` |
| 38611 | `purchase_document_approvals.source_approver_id` | `staff_members.source_employee_id` | `employee_name` |
| 38612 | `retail_payments.payway_code` | `payways.payway_code` | `payway_name` |

定向分析自动产生的 `38602` 原指向只有 100 行的 `product_items`，已改指：
`retail_promote_shares.item_code → products.item_code`，label=`item_name`。

未建 `retail_promote_shares.activity_code → promotions.activity_code`：
源交易不是按活动编码唯一，补齐促销身份后存在 3 组重复活动编码，强行关联会扇出。

## 五、数据核验

自有 RDS 的实际 JOIN 覆盖：

| 关系 | 覆盖 |
|---|---:|
| 让利 → 零售订单 | 9,762 / 9,762 |
| 让利 → 商品 `products` | 9,762 / 9,762 |
| 让利 → 门店 | 9,762 / 9,762 |
| 让利 → 分类 | 9,762 / 9,762 |
| 让利 → 品牌 | 9,762 / 9,762 |
| 让利 → 会员卡号 | 7,095 / 7,095（非空卡号行） |
| 让利 → 促销单号 | 2,995 / 2,995（非空单号行） |
| 让利 → 促销 ID | 2,995 / 2,995（非空 ID 行） |
| 零售订单 → 会员 | 75,377 / 75,377（非空会员 ID 行） |
| 采购审批 → 人员 | 272 / 272 |
| 零售支付 → 付款方式 | 2 / 2 |

运行态回读：5 个新数据集均可通过 `lovrabet data filter` 访问，计数与 RDS 一致。
数据集总数 47（46 DB_TABLE + 1 METADATA）；关系总数 59。

```bash
rabetbase dataset relation-audit --format compress --jq '.data | {total,summary}'
# total=59；59 ok / 0 warning / 0 error / 0 manualReview
```

## 六、结构分析记录与边界

- 差异刷新 trace：`0e4600fc-6236-4ea0-9647-3c1f69bbae98`，终态 SUCCESS。
- 定向分析 plan：`64f3eb10-11f9-4dbd-a545-21e1d3096968`，终态 SUCCESS / 100% / STEP6_COMPLETE。
- 只分析本次 5 张新表；差异中已有的 `retail_sale_flows` MODIFIED_TABLE 属于另行处理项，本次未分析、未修复。

后续若接入科迈会员、人员、付款方式、促销主数据，应先扩只读采集端点，再按来源唯一键合并维表；
不得把当前交易派生维度当作源主数据全量覆盖证明。
