# 老板驾驶舱 / 店长作战台（BI 自定义页面）

- 日期：2026-11-10
- 范围：仅只读查询自有数据中心（`app-21522951`），不向科迈云帆发起任何写请求。
- 页面类型：`CUSTOM`（React JSX + ECharts 5）
- 发布状态：两个页面均已于 2026-11-10 发布（`FORMAL`）
- 本地源码：
  - `.rabetbase/custom-pages/owner-cockpit/`
  - `.rabetbase/custom-pages/store-ops/`

## 1. 两个角色，两套看板

| 角色 | 页面 | Page ID | 菜单 ID | 关注点 |
|---|---|---|---|---|
| 老板 | 老板驾驶舱 · 经营总览 | 1560 | 79838 | 增长、结构、利润、供应商集中度、库存风险；默认近 7 天 |
| 超市经理 | 店长作战台 | 1561 | 79839 | 分时峰值、收银人效、补货、滞销、让利；默认近 7 天，可切今日 |

页面地址：

- 老板驾驶舱
  - 最新保存内容：`https://app.lovrabet.com/app/app-21522951/pages/e026e757db914413b440a2015353f8f2`
  - 运行态（发布后）：`https://app-21522951.app.lovrabet.com/e026e757db914413b440a2015353f8f2`
  - 编辑器：`https://app.lovrabet.com/app/app-21522951/ide/1560`
- 店长作战台
  - 最新保存内容：`https://app.lovrabet.com/app/app-21522951/pages/d87b6c0b2d93425aa1a83da6872058fb`
  - 运行态（发布后）：`https://app-21522951.app.lovrabet.com/d87b6c0b2d93425aa1a83da6872058fb`
  - 编辑器：`https://app.lovrabet.com/app/app-21522951/ide/1561`

## 2. 数据与 SQL 资源

| SQL | sqlCode | 用途 |
|---|---|---|
| `dashboardRetailDaily` | `21522951-59797d83` | 按营业日 × 门店汇总销售额、订单、件数、退货、会员销售、估算毛利 |
| `dashboardRetailBreakdown` | `21522951-65f96f23` | 按 `dimension`（hour / department / category / item / cashier / shift / member / branch）汇总 |
| `dashboardInventoryRisk` | `21522951-0306b05f` | 库存快照 × 区间销量，输出断货 / 偏低 / 积压 / 滞销 |
| `dashboardPurchaseSpend` | `21522951-95f72415` | 按供应商汇总采购下单、收货、退货净额 |
| ~~`dashboardOrderDaily`~~ | `21522951-d5f261b9` | 已停用（生成列上线后会员改回流水口径），资源待清理 |
| `dashboardLatestDate` | `21522951-b95661de` | 返回最近有流水的营业日，用于页面默认锚定，避免空跑一次查询 |

本地 SQL 文件位于 `.rabetbase/sql/app-21522951/fresh-market/`，参数统一为 `startDate`、`endDate`（`YYYY-MM-DD`，含首尾），可选的 `branchId`、`dimension`。

## 3. 指标口径（必须在页面中如实展示）

- 销售额：`retail_sale_flows.sale_amount` 汇总，含退货负金额行；订单按 `receipt_no` 去重。
- 时区：`sold_at` 已按门店本地时间存储，直接取日期/小时，不再做时区偏移。
- 毛利：`(销售额 − 件数 × 商品加权采购单价)`，采购单价来自 `purchase_document_lines` 的 `SUM(amount)/SUM(quantity)`；仅对能匹配成本价的商品计算，页面同时展示“成本覆盖”，不等同于财务毛利。
- 让利率：`(原价金额 − 实付金额) / 原价金额`，原价取自流水 payload 的 `originalAmt`（按 1/10000 元换算）。
- 会员：两页统一按流水的 `member_card` 生成列判断（原 `payload.memberCardNo`），会员占比同时受“销售金额”和“订单数”两个口径影响。
- 库存：来自 `product_branch_stocks` 快照（当前仅 177 个 SKU，存在负库存），可售天数 = 当前库存 ÷（近 7 天销量 ÷ 7），为估算值。
- 采购：`purchase_documents` 按 `document_kind`（order / receipt / return）拆分，净额 = 收货 − 退货。

## 4. 页面刷新与发布

```bash
# 修改本地源码后更新页面内容（未发布状态）
rabetbase page custom-update --id 1560 --page-dir .rabetbase/custom-pages/owner-cockpit --dry-run
rabetbase page custom-update --id 1560 --page-dir .rabetbase/custom-pages/owner-cockpit
rabetbase page custom-update --id 1561 --page-dir .rabetbase/custom-pages/store-ops --dry-run
rabetbase page custom-update --id 1561 --page-dir .rabetbase/custom-pages/store-ops

# 发布当前保存内容（会影响实际访问）
rabetbase page custom-publish --id 1560 --dry-run
rabetbase page custom-publish --id 1560
rabetbase page custom-publish --id 1561
```

SQL 修改流程：编辑本地文件 → `rabetbase sql validate --file <file>` → `rabetbase sql push --sqlcode <code> --dry-run` → `rabetbase sql push --sqlcode <code>` → `rabetbase sql exec` 验证。

## 5. 性能排查与优化（2026-11-10）

### 5.1 实测瓶颈

用 `sql exec` 的 `elapsed` 逐条测量（30 天窗口，`retail_sale_flows` 约 50.7 万行）：

| 查询 | 优化前 | 优化后 | 说明 |
|---|---|---|---|
| 按日汇总（含会员、原价） | 7–10s | 5.8s | 去掉未使用的 `originalAmt` JSON 解析 |
| 品类维度 | 9–18s | 8.9s | 去掉未使用的 `member_sales` 聚合 |
| 会员维度 | 12–21s | 已从老板页移除 | 改为订单口径（见下） |
| 收银员维度 | 15s | 已从老板页移除 | 仅在店长页按单日查询 |
| 采购供应商 | 9s | 2.8s | 供应商名改为 JOIN `suppliers`，不再解析大 payload |
| 库存风险 | 7–15s | 1.6s | 用 `source_item_id` 替代 JSON `$.itemId` |
| 店长页单日维度 | 1.1s/个 | 1.1s/个 | 单日数据量小，已可接受 |

关键结论：

- **不是索引缺失导致的**。当前 30 天窗口几乎覆盖全表，`sold_at` 范围索引的选择性接近 100%，走索引反而更慢（实测带 `tenant_id` 走索引 2.9s vs 全表 1.6s）。
- 现有索引都以 `tenant_id` 打头（`(tenant_id, sold_at)`、`(tenant_id, source_branch_id, receipt_no)`），而看板 SQL 没有传 `tenant_id`，因此**用不上**。
- 主要成本是**全表扫描 + 宽表 payload 读取**：每行 payload 约 2KB，30 天扫描约 1GB；`JSON_EXTRACT` 再叠加解析成本。单日查询靠 `WHERE sold_at` 过滤后成本很低，所以店长页明显更快。
- 单表 `SELECT` 的 `COUNT(*)` 约 2s，说明即便最简单的全扫也要这个量级；查询数量比单条优化更关键。

### 5.2 已执行的数据库变更（2026-11-10）

经数据所有者授权，直接在 `fresh-market` 库执行（该库为自有数据中心，非科迈源系统）：

1. `retail_sale_flows` 新增 6 个 STORED 生成列，把高频 payload 字段固化到列上，读时不再解析 JSON：

```sql
ALTER TABLE retail_sale_flows
  ADD COLUMN member_card VARCHAR(64)
    GENERATED ALWAYS AS (COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.memberCardNo')), '')) STORED,
  ADD COLUMN item_department VARCHAR(64)
    GENERATED ALWAYS AS (COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.itemDepartmentName')), ''), '未分类')) STORED,
  ADD COLUMN item_category VARCHAR(64)
    GENERATED ALWAYS AS (COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.itemClsName')), ''), '未分类')) STORED,
  ADD COLUMN item_name VARCHAR(255)
    GENERATED ALWAYS AS (COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.itemName')), ''), '未知商品')) STORED,
  ADD COLUMN cashier_name VARCHAR(64)
    GENERATED ALWAYS AS (COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.cashierName')), ''), '未知收银员')) STORED,
  ADD COLUMN original_amt_cents BIGINT
    GENERATED ALWAYS AS (COALESCE(JSON_UNQUOTE(JSON_EXTRACT(payload, '$.originalAmt')) + 0, 0)) STORED;
```

> MySQL 8 对 STORED 生成列只支持 `ALGORITHM=COPY`，本次为整表重建（约 50 万行 / 1.3GB），已在无同步任务时执行。

2. 新增两个覆盖索引，主要服务店长页的单日维度查询：

```sql
ALTER TABLE retail_sale_flows
  ADD INDEX idx_bi_dept (sold_at, item_department, sale_amount, quantity, receipt_no, original_amt_cents, source_item_id),
  ADD INDEX idx_bi_cashier (sold_at, cashier_name, sale_amount, quantity, receipt_no);
```

3. 执行 `ANALYZE TABLE retail_sale_flows` 更新统计信息；已试建日汇总覆盖索引 `idx_bi_daily`，实测无收益（30 天仍全表 5.8s）后删除，避免无谓写入开销。

### 5.3 优化后实测

| 查询 | 优化前 | 现在 |
|---|---|---|
| 老板页按日汇总（14 天，含环比） | 7–10s | 3.1s |
| 品类 / 收银员 / 商品维度（7 天） | 9–21s | 1.3–1.6s |
| 采购供应商（7 天） | 9s | 0.2s |
| 库存风险（7 天） | 7–15s | 0.9s |
| 店长页单日维度 | 1.1s | 0.3–0.6s |
| 最新营业日探测 | — | 0.15s |

页面侧同时做了：

1. 老板页查询数 6 → 4；店长页去掉独立会员维度查询，默认区间 30 天 → 近 7 天（另支持单日）。
2. 所有流水 SQL 改用生成列 / `source_item_id`，采购 SQL 改用 `suppliers` 表，查询里不再解析 payload JSON。
3. 两个页面默认锚定最新营业日：先调用 `dashboardLatestDate`（约 0.15s），再按目标日期/区间加载，避免先用“今天”空跑一次。
4. 老板页默认近 7 天，并额外多取前 7 天仅用于环比；店长页默认近 7 天，可切“今日”（单日），对比口径分别为“前 7 天”和“上周同日”。
5. 两阶段加载：先出 KPI 与趋势，再出品类、供应商、库存图表。
6. `sessionStorage` 缓存 10 分钟（带版本前缀），重复进入直接命中；手动“刷新”强制取最新。

预期体验：老板页首屏约 3s、完整约 5s；店长页近 7 天约 3s、单日约 2s；再次进入 < 0.3s。

### 5.4 下一步：汇总表（可选）

30 天窗口仍需扫描 50 万行，单条 5–6s 已接近当前数据库的上限。若要把首屏压到亚秒级，建议增加每日汇总表（DDL 见下），由同步任务在批次完成后幂等刷新，看板只查几十行：

```sql
CREATE TABLE bi_retail_daily (
  biz_date DATE NOT NULL,
  branch_id VARCHAR(32) NOT NULL,
  sales_amount DECIMAL(18,2),
  order_count INT,
  qty DECIMAL(18,2),
  refund_amount DECIMAL(18,2),
  member_sales DECIMAL(18,2),
  member_orders INT,
  sales_with_cost DECIMAL(18,2),
  gross_profit DECIMAL(18,2),
  PRIMARY KEY (biz_date, branch_id)
);

CREATE TABLE bi_retail_dim_daily (
  biz_date DATE NOT NULL,
  branch_id VARCHAR(32) NOT NULL,
  dimension VARCHAR(24) NOT NULL,
  dim_key VARCHAR(128) NOT NULL,
  dim_label VARCHAR(255),
  sales_amount DECIMAL(18,2),
  qty DECIMAL(18,2),
  order_count INT,
  original_amount DECIMAL(18,2),
  sales_with_cost DECIMAL(18,2),
  gross_profit DECIMAL(18,2),
  PRIMARY KEY (biz_date, branch_id, dimension, dim_key)
);
```

刷新逻辑按“同步批次完成后重算最近 N 天”执行，看板切换为查汇总表；这是把首屏做进 300ms 的唯一可持续方案。

**索引结论**：30 天窗口覆盖全表，普通范围索引无法降低扫描量；索引只对单日/单门店切片有效。已保留的两个覆盖索引就是为店长页服务的，不建议再堆更多宽索引。

## 6. 已知限制

- `retail_payments` 仅 2 行，暂不做支付方式结构分析，改用收银员 / 自助机维度。
- 门店当前只有“新鲜年代超市”1 家，门店对比图未启用，避免单点数据误导。
- 熟食、烘焙等品类缺少采购价，毛利率不计算，页面以“无成本价”标注。
- 库存快照为抽样同步，断货 / 滞销结论需店长现场复核。
- 页面默认锚定“最新有数据的营业日”，避免把空白的当天算进近 7 天 / 近 30 天。
- 库存风险统一使用近 7 天销量，不再跟随所选区间；这是为了兼顾速度与补货决策。
- 临时排查 SQL `21522951-38a87508`（`dashboardExplainA`）和已停用的 `21522951-d5f261b9`（`dashboardOrderDaily`）目前保留在平台；删除命令属于 high-risk-write，需人工确认后清理。

## 7. 只读边界

两个页面只调用 Custom SQL 查询；不包含任何 `create/update/delete/approve` 调用，不提供回写科迈云帆的入口。若业务需要更正源数据，仍由数据所有者在科迈官方后台完成。
