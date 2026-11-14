# 营销/会员字段回填与营销域接入方案（B/C/D）

日期：2026-11-10。本文是方案与验收说明，**不是启动采集或写入的指令**。前置结论已由本地证据确立：科迈零售流水的 `couponShareAmt`（优惠券分摊优惠金额）在 90,155 条源行中全为 0，`couponShareSaleAmt`（券后销售额）与 `saleAmt` 逐行恒等（`saleAmt = couponShareSaleAmt + couponShareAmt`）。数据中心只读聚合确认 `coupon_share_amount != '0'` 行数为 0——库与源一致，无同步失真。

## 0. 边界（与 AGENTS.md 一致）

- 科迈 SaaS 永远只读；本方案任何阶段都不对科迈发起写操作。
- 回填只写自有数据中心（app-21522951），走标准模型 API + 唯一键逐字段回读；不回写科迈。
- 源访问（仅 C 阶段调研）经 `source_gate`，≤0.5 次/秒，限流共享冷却 30 分钟；不点击任何保存/提交/审批按钮。
- 个人信息：默认**不采集 `memberPhone`**；会员姓名/卡号按需评估，文档与日志不落原始业务明细。

## B. 营销/会员字段零源回填

### B.1 字段清单与映射

数据源：自有库业务行的 `payload` 列（源行 JSON 全量已存）与 `source_capture` 表。**零科迈请求。**

| 目标表.新列 | 源字段（payload 内） | 快照证据 | 缩放 | 说明 |
|---|---|---|---|---|
| `retail_sale_flows.promote_code` | `promoteCodes` | 19,588/90,155 行有值（SP…） | 无 | 促销活动单号；与营销域关联键 |
| `retail_sale_flows.member_card_no` | `memberCardNo` | 57,974 行有值 | 无 | 会员卡号 |
| `retail_sale_flows.member_grade_name` | `memberGradeName` | 57,974 行有值 | 无 | 会员等级名 |
| `retail_orders.member_card_no` | `memberCardNo` | 11,091/22,072 行有值 | 无 | 订单头会员卡号（与既有 `source_member_id` 互补） |
| `retail_orders.member_grade_id` / `member_grade_name` | `memberGradeId` / `memberGradeName` | 11,091 行有值 | 无 | 订单头会员等级 |
| `retail_orders.discount_amount` | `discountAmt` | 12,224 行非零 | `/10000` | 订单折扣金额（与 `sale_amount`/`order_amount` 同缩放） |

**明确不加**：`couponShareSaleAmt`（与 `sale_amount` 逐行恒等，零信息量）；`integral`/`exchangeUseIntegral`/`commissionAmt`（源端全 0/全 null）；`memberPhone`（个人信息，默认排除）。

### B.2 数据集加列与迁移

- 在 Lovrabet 数据集字段管理为 `retail_sale_flows`、`retail_orders` 新增上述列（NULL 允许，保持既有行不动）；`db/migrations/` 留存对应 DDL 证据（`ALTER TABLE ... ADD COLUMN`，含列注释：源字段名与缩放口径）。
- 加列属数据中心结构变更，先小样本环境验证标准模型 API 的读写/回读，再推广；不在未验证前对生产数据集批量回填。
- `retail_orders` 可复用既有 `idx_retail_member` 设计；`retail_sale_flows` 若需按促销单号分析，评估加 `(tenant_id, promote_code)` 索引（DDL 一并留存）。

### B.3 回填机制（幂等、可恢复）

- 入口：`scripts/kemai_marketing_backfill.py`（新），子命令 `plan/run/resume/verify`，与 `stored-detail` 同风格；`--stream retail-sales|retail-orders --from/--to`（按 `sold_at` 日窗）+ `--max-rows` 预算。
- 流程：按唯一键（`tenant_id,source_flow_id` / `tenant_id,source_order_id`）分页读业务行 → 从行内 `payload` 提取新字段 → 仅对**值有变化**的行 `data update` → 唯一键回读逐字段确认 → 记录回执。
- 幂等：已回填且值一致的行 0 写入；断点按主键游标；失败从游标恢复，不盲目重放；未知写结果先回读确认。
- 报告：`rows_scanned / rows_changed / rows_unchanged / db_requests`；不改写 `sync_stream_status.covered_through`，不关闭任何缺口（新列是补充字段，不是详情覆盖证据）。
- 小样本验收：1 天（约数千行）→ 回读核对与 payload 一致 → 全量回填。

### B.4 归一化同步接入（新数据直接带字段）

- `kemai_pipeline/normalize.py`：`retail-sales` 增加 `promote_code`、`member_card_no`、`member_grade_name`；`retail-orders` 增加 `member_card_no`、`member_grade_id`、`member_grade_name`、`discount_amount`（scaled）。
- 同步新增字段进 `routing.BASE_FIELDS`（列表可满足）与 `field_catalog.py` 对应规范路径（`retail.sales.promote_code`、`retail.orders.discount_amount` 等）。
- 新写入行自动带字段；历史行由 B.3 回填补齐，二者口径一致。

## C. 营销域只读调研（已完成，科迈数据为证）

### C.1 调研结论（2026-11-10，受控只读、source_gate 包装、零写操作）

| 探查项 | 科迈实测结果 |
|---|---|
| `/market/TicketList`（赠券促销单） | **403 暂无权限**（管理员未授权）——优惠券/赠券模块未开通 |
| `/retail/PromoteSharingList`（零售让利明细）近 1 周 | 合计 **0 条** |
| 同报表历史窗口（2026-10-01 ~ 2026-11-10） | 合计 **12,739 条** |
| 让利明细类型分布（首页 100 行） | `type=5/13/19`，**0 条优惠券**；促销类型名：限时特价 46、抓堆促销 6、限时折扣 1、无类型 47 |
| 让利明细行字段 | 无 couponId/couponType 等券字段；有 `amt`（营销优惠金额）、`promotionCode`、`activityName`、`sheetId/sheetNo`、商品/会员字段 |
| 关联键 | 让利明细 `promotionCode`（SP…）与流水 `promoteCodes` **完全一致**（同一批 SP 单号） |

**最终口径**：该租户**没有优惠券业务**（赠券模块未开通 + 让利明细 0 券记录），`couponShareAmt` 恒 0 是**源真值**，不是同步缺陷。实际营销优惠全部是**促销活动**（限时特价/抓堆促销/限时折扣/手工折扣），其数据路径是**独立报表"零售让利明细"**——这才是"营销优惠相关没同步"的正解。

### C.2 关键接口（只读探测所得，路径与字段名）

- `POST /web/api/order/item/discount/queryPromoteShareDetail`：零售让利明细分页（`data.page/rows/totalRecord/totalPage/results[]`；行含 `amt`、`type`、`promotionCode`、`activityName`、`sheetId`、`sheetNo`、`gmtSale/gmtCreate`、商品与会员字段）
- `POST /web/api/order/item/discount/queryStatisticalInfo`：合计（`saleAmt/originalAmt/qty/amt/totalRecord`）
- `POST /web/api/order/item/discount/loadListPage`：查询条件模型（日期/机构/营销类型等筛选）
- 赠券促销单（`/market/TicketList`）无权限，**不纳入采集范围**。

## D. 营销域接入方案（list-first / on-demand）

### D.1 首选新流：`retail-promote-sharing`（零售让利明细）

- **源**：`queryPromoteShareDetail`，日窗（`gmtSale`）+ 机构，分页 `totalRecord` 可核对——与现有交易流同构，直接复用 WindowWorkflow。
- **价值**：回答"营销优惠金额（amt）、促销活动（type/promotionCode/activityName）"——业务真正关心的营销口径；与流水经 `promotionCode`/`sheetId` 双键关联。
- **字段**：`sheetId`、`sheetNo`、`type`、`promotion_code`、`activity_code/name`、`amt`、`sale_amt`、`original_amt`、`original_price`、`qty`、`gmt_sale/gmt_create`、商品（itemCode/itemId/规格/类别/品牌）、机构、收银员、会员（memberCardNo/memberGradeName，**不含 memberPhone**）。
- **缩放**：金额字段与流水同口径（源字符串金额，按既有 scaled 规则处理，先小样本核对缩放因子）。

### D.2 次要新流（按 C 的接口证据评估后逐流评审）

- `marketing-promotions`（促销单 `/market/PromoteList`，有权限）：SP 单主档，与让利明细/流水的 `promotionCode` 对账。
- `marketing-members`（会员档案，对象粒度）：会员主数据。
- 赠券/优惠券：**不接入**（模块未开通，403）。

### D.3 同步模式与验收

- 新流默认 `paused=True`，逐流评审进 acceptance 白名单；字段目录新增 `retail.promote_sharing.*` 规范路径（列表可满足）。
- **先列表日窗 → 详情按需**：让利明细为行级报表，列表即完整（无独立详情阶段）；促销单/会员若需详情，走单元 ③ 统一有界详情任务。
- 验收：让利明细 1 天小样本 → 与报表 `totalRecord`/合计 `amt` 对账 → 与流水 `promoteCodes` 关联一致性核对 → 再评开放范围；全程零科迈写操作。

## 验收清单

- B：回填后抽样行的 `promote_code`/会员/折扣字段与 payload 完全一致；重复执行 0 写入；断点恢复不重写已验证行；`coupon_share_amount` 保持原值（全 0）不受影响。
- C：调研文档只含接口/字段/计数，无原始业务明细、无凭据；未发生任何科迈写操作。
- D：营销域未进 acceptance 白名单前，任何计划/执行均 blocked；小样本证据齐全后方可逐流放开。

## E. 实施状态（2026-11-10）

### B：只读视图已上线并小样本验证（替代直接加列/回填）

为避免不必要的生产表结构变更，B 先以**只读 SQL 视图**交付，直接读取业务行 `payload`，无需加列、无需回填、零业务写入：

- `retailMarketingFlows`（sqlCode `21522951-adc95c26`）：流水的 `promote_code`、`member_card_no`、`member_grade_name`、`discount_type`、`discount_amount`；真实执行 2026-11-01~09 返回 5 行，促销单号与折扣金额提取/缩放正确。
- `retailMarketingOrders`（sqlCode `21522951-0e618815`）：订单头的 `member_card_no`、`member_grade_id/name`、`discount_amount`；真实执行返回 5 行，会员与折扣字段正确。
- 两个视图均通过 `rabetbase sql validate`（`isSelectOnly=true`、`isLockingRead=false`、`isDangerous=false`），已 push 并 `sql exec` 小样本验证。
- `kemai_pipeline/marketing_fields.py` 统一后续可选物化的字段映射，3 条离线测试通过；`db/migrations/migrate_marketing_fields.sql` 仅为**草案**，未执行。
- **未做**：生产表加列、`normalize.py` 接入和批量回填；这些仅在物化确有必要时另行评审，不影响当前分析使用。

### D：零售让利明细只读适配器已完成并真实读取

- 新增 `kemai_opencli/retail-promote-sharing.js` 与 `utils.js` 的 `retail-promote-sharing` 类型（路由 `/retail/PromoteSharingList`，组件 `smallTicketList`），调用 `/web/api/order/item/discount/queryPromoteShareDetail`；`manifest.json` 声明只读契约，OpenCLI 注册测试更新为 23 个命令。
- 项目源码与 `~/.opencli/clis/kemai/` 已同步，`scripts/kemai_opencli_runtime.py check`：33 文件全匹配、无缺失/多余/变更/错误。
- 真实小样本：`opencli kemai retail-promote-sharing --from 2026-11-09 --to 2026-11-10 --page 1 --rows 2 -f json` 成功（自动共享源租约，只读）；随后 100 行只读主键探测完成。
- **主键约束（重要）**：源行 `id` 与 `sheetDetailId` 可为 null；`sheetId+promotionId+itemCode+itemSubno+type+gmtOperation` 在 100 行中有 1 组重复，重复行仅在 `amt/originalAmt/qty/saleAmt` 上不同。因此未来落库唯一键必须包含让利金额等行级维度（或使用完整行内容哈希），不能只用单据 ID/商品 ID。
- **未做**：新数据集、`normalize.py` 分支、capability/acceptance 白名单和常态同步。上述均待新流评审；适配器可读取不等于已授权入库。
- **首次列表小样本已完成**：`scripts/kemai_promote_sharing_capture.py` 以短尾页判定完整、页预算耗尽判不完整，写入私有快照 `.kemai-artifacts/promote-sharing/bc94b9bd6d9d4812ab585d7fcb34659f/`。2026-11-09 全天共 **9,762 行、10 页、10 次源逻辑操作**，末页 762 行，`db_writes=0`；`verification.json` 证明页哈希全部有效。源内另有 **94 条完全相同的重复行**（81 组，全部在页内，跨页重复组为 0），已按源事实保留，未擅自去重；未来入库需用“行哈希 + 页内序号”保留重复出现，不能错误合并。当前**只抓列表，不展开详情**。
- **数据中心小样本写入已验证**：经 METADATA 数据集设计/校验/创建/回读，建立 `retail_promote_shares`（sqlCode/datasetCode `fde264b695ce39d2bca6d0065449f31e`，43 字段含主键），唯一身份为 `(tenant_id, source_row_hash, occurrence_no)`；`kemai_pipeline/promote_sharing.py` 负责归一化，`scripts/kemai_promote_sharing_backfill.py` 从上述私有快照幂等回填。DATETIME 按 METADATA 要求写 epoch 毫秒；分页固定 20、`$in` 每组最多 3 个哈希的限制仅在回填器内适配，未放宽全局适配器。前 100 行已完成真实写入和逐字段回读：新增 50、既有核验 50、源请求 0；独立 `filter` 返回 `totalCount=100`。全天剩余行正在按同一快照分批回填，完成前不宣称全天入库完成。
- **2026-11-09 全天入库已完成**：`retail_promote_shares` 共 **9,762 行**，新增 9,612 行、既有回读 150 行；独立 `filter` 返回 `totalCount=9762`。完成重放 `db_requests=0`、`source_operations=0`，未产生重复行。回填期间数据中心发布导致 Bad Gateway，按断点先回读再续写，故障前落库批次被正确跳过。全量离线回归 Python **562/562**、Node **57/57**、`git diff --check` 通过。
- `capabilities.py` 已以 `paused=True` 登记 `retail-promote-sharing`（`source_key_version=promote-sharing-row-hash-occurrence-v1`）；`field_catalog.py` 登记 `retail.promote_sharing.*` 列表字段。**常态同步仍未放行**，只有上述显式小样本/本次快照回填在运行。
