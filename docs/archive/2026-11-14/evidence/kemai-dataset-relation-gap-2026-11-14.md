# 数据集关系缺口分析与补齐记录（2026-11-14）

应用：`app-21522951`（自有数据中心，科迈只读副本）。本次全部改动只发生在 Lovrabet 研发态关系元数据，未触碰科迈 SaaS。

> 后续实施：本文第五、六节所述让利物理表和四类维表已于同日建成，最终关系数增至 59。
> 见 `docs/kemai-business-dimension-tables-2026-11-14.md`；下文保留缺口分析时点的原始记录。

## 一、起点

- 平台已有关系 **34 条**，全部来自 DBAgent 自动分析。
- `rabetbase dataset relation-audit`：**28 ok / 6 warning / 0 error**。
- 24 张表有关系，18 张零关系；其中 16 张为同步运维表（可接受），2 张业务表为真遗漏：`retail_order_detail_state`、`retail_promote_shares`。

## 二、本次新增 14 条（relationId 38588–38601）

全部先 `--dry-run` 通过，再正式创建，最后回读核验。

| relationId | from | to | cardinality | labelField | 依据 |
|---|---|---|---|---|---|
| 38588 | `retail_order_detail_state.order_id` | `retail_orders.id` | ONE_TO_ONE | `receipt_no` | 与 `purchase_document_detail_state` 对称，原自动分析遗漏 |
| 38589 | `product_supply_qualifications.source_supplier_id` | `suppliers.source_supplier_id` | MANY_TO_ONE | `supplier_name` | 14,395 行有供应商 ID |
| 38590 | `product_supply_qualifications.source_branch_id` | `branches.source_branch_id` | MANY_TO_ONE | `branch_name` | schema 既有 `idx_supply_branch` 联合索引 |
| 38591 | `product_branch_stocks.source_branch_id` | `branches.source_branch_id` | MANY_TO_ONE | `branch_name` | 177 行有值；按店汇总库存的前提 |
| 38592 | `product_branch_prices.source_branch_id` | `branches.source_branch_id` | MANY_TO_ONE | `branch_name` | 门店价格核心维度 |
| 38593 | `product_branch_scopes.source_branch_id` | `branches.source_branch_id` | MANY_TO_ONE | `branch_name` | 经营范围按店 |
| 38594 | `categories.parent_source_category_id` | `categories.source_category_id` | MANY_TO_ONE | `category_name` | 790/801 行有父分类；源端确认 `parentId/parentCode/level/path` |
| 38595 | `categories.source_department_id` | `departments.source_department_id` | MANY_TO_ONE | `department_name` | 359/801 行有部门；源端确认 `departmentId/departmentName` |
| 38596 | `branches.parent_source_branch_id` | `branches.source_branch_id` | MANY_TO_ONE | `branch_name` | 2/2 行有父机构 |
| 38597 | `product_items.item_code` | `products.item_code` | MANY_TO_ONE | `item_name` | 抽样 5/5 命中；`products.item_code` 无重复，无扇出 |
| 38598 | `product_items.category_code` | `categories.category_code` | MANY_TO_ONE | `category_name` | 原先只有 level1/level2，末级缺失；`category_code` 唯一 |
| 38599 | `product_items.supplier_code` | `suppliers.supplier_code` | MANY_TO_ONE | `supplier_name` | 原完全缺失 |
| 38600 | `product_items.brand_code` | `brands.brand_code` | MANY_TO_ONE | `brand_name` | 原完全缺失 |
| 38601 | `purchase_documents.source_document_ref_id` | `purchase_documents.source_document_id` | MANY_TO_ONE | `sheet_no` | 收货单 3,639/3,641 有 ref、退货单 161 有 ref，支撑订单→收货→退货链路 |

`product_items.source_item_id` 全空（0/100），因此商品档案与商品同步之间只能走 `item_code` 编码桥，不能走源 ID。

## 三、本次修正 6 条既有关系

labelField 由技术字段 `id` 改为业务可读字段：

| relationId | 关系 | 改动 |
|---|---|---|
| 38413 | `product_branch_stocks.product_id → products.id` | label → `item_name` |
| 38412 | `product_branch_scopes.product_id → products.id` | label → `item_name` |
| 38414 | `product_child_records.product_id → products.id` | label → `item_name` |
| 38411 | `product_branch_prices.product_id → products.id` | label → `item_name` |
| 38415 | `product_profile.product_id → products.id` | label → `item_name`；cardinality → ONE_TO_ONE（`product_id` 为 PK） |
| 38468 | `purchase_document_detail_state.document_id → purchase_documents.id` | label → `sheet_no`；cardinality → ONE_TO_ONE（`document_id` 为 PK） |

更新均带 `--expect-to-label-column id` 做漂移检测。

## 四、终态核验

```bash
rabetbase dataset relations --format compress          # total = 48（34 + 14）
rabetbase dataset relation-audit --format compress     # 48 ok / 0 warning / 0 error / 0 manualReview
```

`to.labelField == "id"` 的关系已清零；ONE_TO_ONE 共 3 条（38415、38468、38588）。

## 五、明确不建 / 建不了

- **`brands.parent_source_brand_id` 自关联**：0 行有值，建了是死关系，不建。
- **零售让利明细 `retail_promote_shares`（METADATA）→ 任何 DB_TABLE**：dry-run 实测返回
  `Error: METADATA to DB_TABLE ER relation is not supported`。
  平台仅支持 DB_TABLE→DB_TABLE、DB_TABLE→METADATA、METADATA→METADATA。
  该表有 9,762 行带 `source_order_id`，且 `item_code` / `branch_code` / `item_category_id` / `item_brand_id` 均有值，
  理论上可关联订单、商品、门店、分类、品牌 5 个方向，但方向上当不了 from 端。
  待决策：反向建模（价值有限），或为让利明细建 DB_TABLE 物理表。**本次未改动其结构。**

## 六、关系之外的缺口（缺实体，不是缺边）

以下目标主数据表不存在，补关系无法解决：

| 待关联字段 | 缺失主数据 | 备注 |
|---|---|---|
| `retail_orders.source_member_id` | 会员 | 会员单占比较高，优先级最高 |
| `purchase_document_approvals.source_approver_id` | 人员 / 收银员 | |
| `retail_payments.payway_code` | 付款方式维表 | 目前支付方式只在 payload |
| `retail_promote_shares.promotion_code` | 促销活动主数据 | |

opencli 适配器 `MASTERS` 当前只有 branches / departments / categories / brands / units 五类，
会员、收银员、付款方式、促销单**均无只读采集命令**，需要先做只读端点扩展。

## 七、源端只读取证

`opencli kemai master-list --kind categories --page 1 --rows 3`，1 次源请求，观测时间 `2026-11-14T06:35:40Z`。
确认分类的 `parentId`、`parentCode`、`level`、`leafFlag`、`path`、`parentIds[]`、`departmentId`、`departmentName`
均为科迈源生字段，第 38594 / 38595 两条关系不是本地推测。

附带字段层建议（未执行）：本地 `categories` 未落列的 `parentCode`、`path`、`leafFlag`、`departmentName`
可考虑补齐，便于按编码树与部门名直接查询。

## 八、复核命令

```bash
rabetbase dataset relations --format compress \
  --jq '.data.relations[] | select(.relationId >= 38588) | "\(.from.table).\(.from.field) -> \(.to.table).\(.to.field)"'
rabetbase dataset relation-audit --format compress --jq '.data.summary'
```
