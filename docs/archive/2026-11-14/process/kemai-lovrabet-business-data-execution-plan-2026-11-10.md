# 科迈 → Lovrabet：经营数据补齐与同步治理执行计划

日期：2026-11-10。目标应用：`app-21522951`，租户：`205149`。本文供接手 Agent 逐项执行；它是执行顺序和验收契约，不是“所有数据已同步”的声明。**先完成阶段 0 和阶段 1 的只读基线，再按业务价值推进。**每次开始新阶段都重新读取运行状态，不能沿用本文的数字当作实时状态。

## 0. 目标、优先级与绝对边界

目标按顺序为：

1. 让五条已接受的列表流有可信的最新日期和异常清单。
2. 用 Lovrabet 中已入库的销售数据，定位最影响经营分析的成本、库存、促销关联缺口。
3. 只对明确对象和字段做有界补采；把采到的数据接到应用实际使用的结构化表或查询上。
4. 最后决定运行频率和页面呈现。不要先追求“18,000 商品 × 14 页签”或零售逐单全详情。

**先读**仓库根目录 `AGENTS.md`、`docs/kemai-pipeline-operations-2026-11-08.md`、`docs/kemai-standard-api-delivery-2026-11-08.md`、`docs/kemai-bi-dashboards-2026-11-10.md`、`docs/kemai-list-first-on-demand-development-plan-2026-11-10.md`。如文档冲突，以当前代码、运行态只读结果和 `AGENTS.md` 为准。保留所有未提交修改，不 `git clean -fdx`，不 reset，也不要把现有未提交文件当作自己的变更覆盖。

### 绝对禁止

- 科迈 SaaS **永远只读**，不得新增、修改、删除其业务数据、配置、权限、文件或任务。
- **不能调用四个 `*-export-start` 命令。**它们在 OpenCLI 元数据中标为 `[read]`，实际会创建科迈侧导出任务，属于 `AGENTS.md` 禁止的源侧变更。也不得从 `kemai_syncctl.py`、定时器或别的适配器间接触发。
- 不在科迈后台点击保存/导出任务创建/提交等按钮，不做写能力探测；不提供或执行写源站的脚本。
- 不运行旧 MySQL 业务写入器、裸 `batchCreate` 或 Custom SQL 来绕过新管线的接受名单、事务回执与核验。自有数据中心业务写入用已验收的标准模型 API 路径；同一时刻只有一个 writer。
- 源逻辑操作速率 ≤0.5 次/秒，触发限流后共享冷却 30 分钟；逻辑操作数不是准确 HTTP 请求数。零售原详情/支付仍暂停。
- 无浏览器验证要求；按本仓库约定用源码、离线测试和 CLI 只读核验。口令、Token、签名 URL、原始业务明细不得写进 `docs/`。

### 当前已知基线，仅用于核对方向

2026-11-10 20:13 左右，数据中心只读计数：零售订单头 139,365、销售流水 506,903、采购单头 17,297、采购商品行 27,930、商品主档 18,479、商品 profile 18,398、商品子记录 15,754、已审核收货历史行 126,273、让利明细 9,762。两张表的数量差不等于经键集合确认的缺失对象数。`dashboardLatestDate` 在 20:xx 只读返回最新营业日 `2026-11-09`；不能据此推断科迈源站今天无交易。

2026-11-10 的经营诊断使用 2026-10-11～11-09 窗口：现有 BI SQL 算出的采购成本可核算销售额占比 52.7%；库存风险表只有 177 个 SKU 快照；综合让利率 13.2%。这三个数字的口径各不相同，均不是财务结算。老板驾驶舱和店长作战台已发布，但“页面可显示”不证明数据覆盖完整。当前正式 accepted streams 只有 `retail-sales`、`retail-orders`、`purchase-orders`、`purchase-receipts`、`purchase-returns` 的**列表**，见 `kemai_pipeline/acceptance.json`。

## 阶段 1：只读建立新的基线与缺口台账

此阶段只读本地和 Lovrabet 数据中心，**不访问科迈、不写业务数据**。输出放 `.local-audits/`，只把脱敏汇总写入 `docs/`。若原目录已有审计文件，用新时间戳目录，绝不覆盖旧证据。

1. 记录 `git status --short --branch`、`opencli --version`、`python3 scripts/kemai_opencli_runtime.py check`。确认安装适配器与仓库一致、源 gate/cooldown 状态可读；核对当前是否有 writer/采集进程，不根据旧文档中的 PID 推断。
2. 运行 `python3 kemai_syncctl.py capabilities`。只把 `accepted_streams`、`paused`、`source_requests=0`、`db_writes=0` 摘要记录下来。`capabilities` 声明某源命令存在，不等于该流能正式入库。
3. 对五条 accepted streams 分别运行 `python3 kemai_syncctl.py status --stream STREAM`、`python3 kemai_syncctl.py gaps show --stream STREAM`、`python3 kemai_syncctl.py incremental plan --stream STREAM --max-catchup-days 2`。命令都不应请求科迈或写 DB。报告逐流列出：最后成功入库时间、最后源观察时间（若可证）、最后连续核验日、最近失败日、待办日窗、列表/详情覆盖级别。**不要把 `covered_through` 展示最大值当作连续历史证明。**
4. 对 `2026-11-08` 采购订单运行 `python3 kemai_syncctl.py history check --scope db --stream purchase-orders --from 2026-11-08 --to 2026-11-08`。之前源端 34 张、库内 35 张，源端有“单据不存在”；最近只读检查仍是 `latest_attempt_not_success`。即使普通增量计划没有列出这天，也要把它作为独立异常。不得删除库内旧单，不得伪造完成证据。
5. 对五条流的 `2026-11-09` 日窗分别做 `history check --scope db`，再看 11 月 10 日 `provisional`。`history check` 只比较**已保存**源证据和当前 DB，不是新的科迈核对；没有源证据时写 `unknown`。
6. 只读运行可信 SQL `lovrabet sql exec --sqlcode 21522951-b95661de --params '{}' --appcode app-21522951 --format compress`，记录 `dashboardLatestDate.latest_date`。这表示库内最近营业日，不是源实时时间。
7. 用 `kemai_pipeline.lovrabet_adapter.LovrabetAdapter.page(table, size=1)` 的 `totalCount` 读取关键表总量；不要把 `last_saved_rows` 当全表行数。至少记录上述九张表及 `purchase_document_branches`、`purchase_document_approvals`。按日期/流区分数量，不用全表总量证明某日完整。
8. 对 `sync_coverage_gaps` 汇总按 stream/status 的条数。当前约 691 条 pending；它是待复核台账，后续补采未必自动关闭，**不是缺失业务行数**。每条缺口只在对应对象、字段与核验范围满足后关闭。

**阶段 1 交付物**：`docs/kemai-lovrabet-coverage-baseline-YYYY-MM-DD.md`（仅汇总与证据路径），包含一张逐流矩阵；私有 JSON/命令输出留在 `.local-audits/`。若某只读命令失败，报告固定错误类型和未知项，不补造数字。

## 阶段 2：先消除零售日终路径的科迈写副作用，再恢复列表新鲜度

这是**P0 阻断项**。`kemai_pipeline/workflow.py` 当前对零售非空的 `final` 日窗会在约 138～158 行经 `self.source.export_start(...)` 核对导出总数；`kemai_pipeline/source.py` 会调用 `retail-*-export-start`。该调用会创建科迈侧任务，与 `AGENTS.md` 冲突。接手 Agent 在修复并离线验收之前，**不得运行零售 `history fill`、`incremental run`、旧脚本或服务来处理可能走到此分支的窗口**。仅看 `plan` 不足以证明执行路径安全。

具体修复顺序：

1. 用源码调用链枚举所有能到达 `export_start`、商品档案导出或历史收货导出的入口；在执行器层加 fail-closed 门禁，保证任何禁用导出都不会先产生源请求。先写离线测试：非空零售 `final` 计划执行时，mock 源适配器一旦收到 `*-export-start` 就失败；验证实际执行零次导出任务创建。
2. 设计并实现**纯读取**的日终核验路径：完整列表分页工件、唯一源键、页连续性、源查询范围、已观察行数、数据中心键/摘要和日计数都要核对。若源 `totalRecord=0` 但有行，不能当空日；需有界扫到末页。若源端无法提供独立可信总数，必须把验证方法降级并将窗口标记为“列表采集已核、独立总数未知”，不能沿用 `export_total_and_capture` 这一旧证据名或悄悄推进既有强度的完成水位。旧已完成 capture 保持原证据，不回写或重解释。
3. 保持采购三类列表走 API，禁止因为积压或阈值切 Excel；见 `kemai_pipeline/routing_policy.json`。历史收货 Excel 是独立流，不能代替采购列表或商品行。
4. 跑全部**离线**回归：`python3 scripts/test_kemai_offline.py`、`node --test tests/pipeline/*.test.mjs`、`git diff --check`。测试不得触碰生产 source gate/cooldown，也不得发科迈请求。检查不一致时停止，不以通过率掩盖失败。
5. 在确认无其他 writer、无冷却、源账号只读可用、执行计划范围和预算明确后，才以**一条流、一个有界日窗**做受控源读取与自有库写入。先处理 11 月 9 日日终，再处理 11 月 10 日临时窗口；每次执行后从相同 capture/回执恢复，不另起并行任务。`--max-source-operations` 限制的是逻辑操作，不是准确 HTTP 数。若旧 capture 已进入等待导出状态，先隔离并审计，不运行会继续创建/提交导出的恢复路径。
6. 每个日窗报告源唯一键数、DB 键数、源观察时间、入库时间、核验方法、是否 final/provisional、未解决缺口。`2026-11-08` 采购订单另立异常单，不因为追完 11 月 9 日就认为它已解决。

**阶段 2 验收**：所有新源路径对科迈只有查询；五条列表流的最近日窗有可复核证据；没有把临时快照、单轮计数或旧导出复用说成独立完整证明。若无法实现纯读的正式日终核验，停在真实的 `provisional/unknown`，不要恢复常驻调度。

## 阶段 3：从应用实际问题倒推“补什么”，先生成只读经营覆盖报告

以 `dashboardLatestDate` 返回的最新**已入库营业日**为结束日，取近 30 天；记录查询时间与日期窗口。不要固定写死“今天”，也不要用源端创建日代替销售营业日。第一版报告先复用已发布的只读 SQL：

- `dashboardRetailDaily`：`21522951-59797d83`，参数 `startDate/endDate`；销售额、`sales_with_cost`、成本覆盖。
- `dashboardRetailBreakdown`：`21522951-65f96f23`，参数 `startDate/endDate/dimension='item'`；返回最多 300 个商品，含 `dim_key`（源商品 ID）、销售额、`sales_with_cost`、估算毛利。**300 行上限不能被称作全商品清单。**
- `dashboardInventoryRisk`：`21522951-0306b05f`，参数 `startDate/endDate`；当前从 `product_branch_stocks` 读，只有少量抽样 SKU。
- `dashboardPurchaseSpend`：`21522951-95f72415`，按供应商汇总采购单据头，不是商品级真实成本。

先核对上述 SQL 的 `sqlCode` 与本地 `.rabetbase/sql/app-21522951/fresh-market/` 源文件及平台详情一致，再用 `lovrabet sql exec` 只读调用。不要从未知标识猜测并调用 Custom SQL。若要全商品、缺口原因及关联率，需要设计新的**只读**查询/脚本：先本地实现、验证参数和数据范围、`rabetbase sql validate`、dry-run；在形成可审阅 SQL 和脱敏样例前不要推送到线上。只读查询必须有租户、日期、门店范围，避免无界扫描或导出原始业务明细。

报告至少给出以下指标，按**销售额权重**排序，不按“有多少张表/多少行”排序：

| 指标 | 明确计算和限制 |
|---|---|
| 销售列表覆盖 | 近 30 天逐日源证据状态；DB 销售额和订单数；缺日/临时日单列。 |
| 商品身份关联 | 销售行有源商品 ID的比例、能关联 `products.source_item_id` 的比例；未知 ID/无效 ID单列。 |
| 成本覆盖 | 现有 BI `sales_with_cost / sales_amount` 口径与新审计的有效成本口径**并列**；退货负金额可能抵消，另给 `SUM(ABS(sale_amount))` 权重覆盖。 |
| 缺成本原因 | 没有采购行、采购行商品 ID 缺失或未匹配、数量/金额无效、只有订单未收货、状态不明、业务从未建成本卡，分别计数和金额。不能把最后一类伪称同步失败。 |
| 库存覆盖 | 有可靠门店+商品+观察时间的库存快照 SKU、相应销售额占比、快照年龄；无快照与 0 库存分开。 |
| 让利关联 | 流水 `promoteCodes` 与 `retail_promote_shares.promotion_code` 的关联率、金额差异、重复行情况；13.2% 综合折让不能直接标为促销费用。 |

**关键口径审计**：现有 `dashboardRetailDaily`/`dashboardRetailBreakdown` 成本子查询对 `purchase_document_lines` 按商品直接 `SUM(amount)/SUM(quantity)`，没有先通过 `purchase_documents` 区分 `order/receipt/return`、单据状态和有效日期。可能把下单与收货、退货混在一起。先检查真实单据类型、状态与字段样本，再提出可审计成本算法；不要仅因为补行后覆盖率上升，就宣称全店毛利准确。若需业务确认，给出两套可核对数字和差异来源，不猜采购会计规则。

**阶段 3 交付物**：只读覆盖报告、Top 缺口商品清单（文档仅放脱敏汇总，具体商品清单留私有审计目录）、SQL/脚本、数据窗口、查询耗时、核验方式。报告要把“源未采到”“已采但未投影”“业务原本没记录”分开。

## 阶段 4：按覆盖报告有界补成本、库存和让利，不按全表盲抓

### 4A. 采购商品行与成本

1. 优先选近 30 天**销售额高且缺有效采购成本**的商品，查它们是否有对应采购收货/退货单和已经保存的详情快照；先做数据中心只读关联，不先请求科迈。
2. 对确有已入库单头但商品行未验证的日期，先用 `python3 scripts/kemai_lines_only.py plan --stream purchase-receipts --day YYYY-MM-DD` 等只读计划确认候选单数。此计划只证明候选商品行，不关闭分支/审批缺口。按单据类型分开计划，**最近 30 天优先**。
3. 若要执行，沿用现有未完成 capture；`run/resume` 每轮建议最多 10 单，`source-mode=batch` 仍逐单受源 gate 限速。先做 1 单 → 5～10 单，逐单核对源商品 ID、数量、单价、金额、税额、行哈希和唯一键；`details=null/缺失` 只能记 partial，不能当合法 0 行，也不能删旧子行。恢复时先查快照和回执，不能重发不确定写入。
4. 商品名称/编码/条码等可能只在 `payload`，不保证有结构化列；成本和展示需要分别验收。业务上根本不存在采购单的自营熟食/联营/自采商品，要列为**业务成本卡待建**，由数据所有者决定在自有中心或正式业务流程补录；Agent 不能推断一个成本价，更不能回写科迈。
5. 重新计算成本覆盖与估算毛利，分别显示“按现有 BI 公式”和“按已核定有效收货成本”；未核定规则前不改线上毛利标题。

### 4B. 库存

1. 从阶段 3 的高销售额/高缺货风险商品选有限对象（先 1 件、再 5 件），逐个只读核验 `product-section --section stock` 的源响应、门店 ID、商品 ID、数量、时间。`product.tabs.stock` 在当前字段目录里是 `not_accepted`，并且需要独立 TTL；不能用主档版本一致判断库存新鲜。
2. 当前 `ProductWorkflow` 的商品页签写入目标是 `product_child_records`，而 `dashboardInventoryRisk` 查询 `product_branch_stocks`。因此**仅采 stock 页签不会自动改善库存看板**。先设计、离线测试并小样本验收从已验证页签快照到 `product_branch_stocks` 的投影/刷新：稳定商品+门店键、数量单位、观察时间、幂等回读、缺字段和合法 0 的区分；不能覆盖更新较新的库存观察。
3. 单元②的 inventory/游标形式显式页签需求应在保存 capture 前拒绝；截至本文写作，GLM 的 comment-12 声称已修，尚待独立复核。只允许显式 `--id` 小样本进入页签验证。先复核代码/测试，再运行真实小样本；不能绕过门禁以全清单展开库存。
4. 库存页面同步展示“已覆盖 SKU / 销售额占比 / 最旧和最新快照时间”；小样本不能推断全店断货率。

### 4C. 促销让利

1. `retail_promote_shares` 已有 2026-11-09 的 9,762 行快照，但 `retail-promote-sharing` 仍 `paused=True` 且不在 acceptance 白名单。先**只读**核对这一天与流水的促销单号、单据 ID、金额、活动类型和重复行身份；源内完全相同行也可能重复出现，不得按哈希粗暴去重。
2. 验证页面需要的“促销让利金额”与“原价－实付”是不同指标。旧报告建议满减券已失效：只读源实测赠券模块 403、该租户无券业务；不要把 `couponShareAmt=0` 解释成同步错误，不据此设计券活动。
3. 只有一日小样本的键、总额、重复保留和重放通过后，才提议为此流建立独立日窗、源观察时间与常规增量；不能因为 9,762 行在库就把它列为已自动同步。

## 阶段 5：Lovrabet 页面和运行节奏

- 数据同步状态页（page 1550）按列表、采购商品行、商品主档、商品 profile、库存页签、让利流**分列**覆盖；同时展示 `source_observed_at` 与 `last_success_at`。`success` 必须绑定明确的对象/窗口范围，不代表“整类数据完整”。27 个通道中“已配置”与“正式 accepted”分开。
- 老板驾驶舱（page 1560）与店长作战台（page 1561）保留“估算毛利”“成本覆盖”“库存抽样”提示；只有数据模型和 SQL 验收后才修改指标。任何页面/SQL 发布先本地修改、校验、dry-run、审阅差异，再执行已明确授权的发布。
- 日常频率最后决定。当前用户的既有目标是具备按需增量与历史补采能力，**没有要求立即恢复常驻 supervisor**。先证明单次有界运行、停机恢复和连续几个日窗核对，再提出每天一次或更高频的运维建议；没有这一轮证据时不要启动常驻服务或开机自启。

## 不能提前做或不能宣称的事

1. 不恢复零售原详情/支付；现有 2 条支付采样不能支持支付方式结构分析。
2. 不对 18,000 多商品自动展开 14 类页签。已完成的 89 件主档 list-only，不表示 89 件详情或库存齐全。
3. 不把历史收货 Excel 直接写进采购单据商品行；跨来源稳定键与父单关联尚未完整验收。
4. 不把 `product_branch_stocks` 的 177 个抽样 SKU 当全店库存，不把商品目录约 18,000 件当目前有库存记录。
5. 不把旧状态页的 `item_detail 18390/18390` 当作当前商品详情实时全量；它是旧时点、旧范围的状态。新旧对象以源 ID 键集合和观察时间重新核对。
6. 不把采购源端缺失的一张旧单直接从自有库删除；先制定保留/墓碑/不可见规则并保留证据。

## 每阶段统一验收与交接模板

接手 Agent 每完成一阶段，在报告中固定填写：

```text
阶段 / 本次范围：
执行日期（Asia/Shanghai）与数据日期窗口：
源是否访问、逻辑操作数、HTTP 请求数是否未知：
数据中心请求数与业务写入数：
源行数 / 唯一键数 / 库内行数 / 内容摘要或字段回读结果：
列表、详情、页签分别是什么覆盖级别：
源观察时间 / 数据中心提交时间：
本次 capture_id、可恢复位置、下一次从哪里继续：
未解决缺口与阻断原因：
离线测试、差异审阅、业务口径验收结果：
```

**完成定义**：阶段 1 的只读基线与阶段 3 的经营覆盖报告必须先交付；阶段 2 的源侧零写门禁必须先通过，才能执行可能触发新零售日终采集的任务。成本、库存、让利各自以“应用可用的结构化结果 + 观察时间 + 可复核业务口径”验收，不能只以源命令返回成功或库表增加行数验收。

## 2026-11-10 晚间补充：`queryStatisticalInfo` 是候选纯读总量接口，尚未验收为同步依据

另一个 Agent 在 `docs/kemai-lovrabet-api-coverage-2026-11-10.md` 和临时交接文档中报告：七类报表均有 `queryStatisticalInfo` 路径；一次调用可返回整数 `totalRecord`，其试验窗口得到 21 组总量。该发现有价值：如果范围、时间字段、门店和计数语义确实与列表捕获一致，它可以替代会创建源侧任务的导出总数核验。**目前只把它当候选接口和缺口线索，不把报告中的 match/mismatch 直接写入完成水位或自动补采队列。**

接手 Agent 在任何下一次 `--mode api` 源调用前先完成以下 P0 修正；本轮不删除其他 Agent 的代码：

1. **共享限速**：`scripts/kemai_total_reconcile.py` 的 `api_stats_total`/`api_page` 目前用 `urllib` 直连，未使用 `source_lease`。`--interval=1` 只在分页回退的页与页之间 sleep，21 个统计接口请求之间没有统一 2 秒间隔，也未共用 30 分钟冷却。接入项目 source gate，对**每个**真实源 HTTP 调用保守计数、限速和冷却；不得把交接文档提出的“所有者确认例外”当作可用路径。HTTP 429 与 HTTP 200 的业务限流都要触发共享冷却，异常/回退请求也计入操作数。先用离线假接口及假时钟证明间隔和冷却，不能为了测试请求科迈。
2. **逐流核对同一窗口**：现有正式零售流水使用 `timeType=1` 的上传时间，对应 DB `source_created_at`；新脚本却发送 `timeType=0` 并按 `sold_at` 计数。这可以成为单独的营业日分析，但不能证明正式上传日同步是否完整。零售订单 `dateType=0`、采购制单日 `dateType=0` 还要逐个核对门店/机构及固定筛选。历史收货既有适配器使用 `dateType=1, queryType=1` 和带时分的窗口，新脚本却使用 `dateType=0, queryType=0`，与 DB `approved_at` 对比前必须重新证实时间口径。让利明细源参数是销售时间，而新脚本 DB 计数是 `source_created_at`（`gmtCreate`），也不能直接说日窗对齐。任何一项未证明时输出 `scope_unverified`，不输出 `match`/`mismatch`。
3. **零值与响应契约**：统计接口的 0 不能单凭一个整数认定真实空日；用已知空/非空窗口和同筛选列表小样本交叉验证。严格检查业务码、计数字段、窗口和机构；不能让 `code=None` 或任意 `total/totalCount` 字段蒙混过关。失败回退要保留失败证据和真实操作数，不能静默吞异常。源总数与 DB 总数相同仅证明数量相同，不证明键集合和内容一致。
4. **证据字段**：私有工件记录源观察时间、流、窗口、时间字段/值、机构范围、参数摘要、统计字段路径、实际调用数、结果和是否经同范围列表核对；公开文档只保留脱敏汇总。不记录 `PC_TOKEN`、Cookie 或业务原始行。不要把“页面默认筛选”或当前浏览器会话当成已验证的固定租户范围。
5. **接入方式**：优先把已证实纯读的统计查询封装到受控 OpenCLI 源适配器，继承共享 gate、错误分类和查询范围证据，再由 `kemai_pipeline/workflow.py` 在列表采集完整之后使用。新核验方法要另起名称与版本；不得改写旧 capture 的导出证据。只有同一固定范围的小窗口与列表/DB 键及计数反复核对通过，才考虑把它加入 `final` 验收。未验收前 `*-export-start` 仍禁止，不能以新接口的存在解除阶段 2 阻断。

当前 API 表中 `retail-orders` 11-09 的 3,856 vs 2,024、`purchase-orders` 11-08 的 33 vs 35、历史收货 11-08～10 库内为 0 等均是**优先复核线索**。采购 11-08 曾观察到 34 vs 35，本次变成 33 vs 35；先确定同一筛选口径与源端变化，再讨论保留/墓碑规则，绝不删除库内记录。当天窗口仍是 provisional，不用 0 行旧快照判源端当前为 0。
