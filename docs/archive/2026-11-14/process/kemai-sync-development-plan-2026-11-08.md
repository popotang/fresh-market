# 科迈同步体系开发计划

> 2026-11-08 实施更新：公共内核与零售链路已按标准接口实现；验收证据、已启用范围和未完成项见[标准接口交付记录](kemai-standard-api-delivery-2026-11-08.md)，实际命令见[操作指引](kemai-pipeline-operations-2026-11-08.md)。下文历史规划/评审楼层保留，不应当作最新运行状态。

> 2026-11-08 修订版 v2，已按 Codex 与 DeepSeek 在 [comment-9](#comment-9)、[comment-10](#comment-10) 确认的 C1–C12 回写，经用户授权生效。本文是开发排期与验收清单，**不是已上线能力清单**。现状见[能力清单](kemai-sync-capability-map-2026-11-08.md)，分层与数据契约见[目标架构](kemai-data-sync-target-architecture-2026-11-08.md)。截至本文制定时，旧日常与新小时级常驻采集均已停止；零售原详情/支付补采仍按用户此前要求保持暂停。

## 目标与交付边界

交付后，操作人员只需要一个 `kemai-syncctl` 入口，就能查看每类数据的最近成功时间与缺口、核对指定历史窗口、补指定缺口、执行一轮增量，并在限流或长时间停机后从持久断点恢复。OpenCLI 只管科迈源，Lovrabet CLI 只管数据中心，编排脚本只管确定性流程，Skill 只选择受控动作并解释证据。

“开发完成”与“历史数据补齐”分别验收：前者验证工具可安全运行、重试、核对；后者还需要实际源请求，耗时受 0.5 次/秒上限、30 分钟限流冷却、源端详情可用性和待补对象数量影响。不能把代码交付日期写成历史数据完成日期。

## 开发顺序

实现选型补充（2026-11-08）：按用户反馈，P0-1 优先采用 BFF 内标准 `filter/batchCreate/update` 与事务；Custom SQL 仅用于标准 API 无法表达且已证明必要的局部操作。已实测 `tx.models.batchCreate` 两行新增后主动抛错，回滚后残留 0 行；这只证明本连接上的该批量操作可回滚，批次幂等、并发与大批容量仍待验收。证据见 [batchCreate 验证记录](kemai-batchcreate-verification-2026-11-08.json)。历史评审楼层保留原文。

以下是**单名熟悉项目的开发者**的粗略工作量，用于排序，不是交付承诺；数据中心 BFF/Custom SQL 的事务能力与 CLI 负载上限未经验证，是最大不确定项。工作包之间以验收门槛衔接，源端产品命令可与数据中心验证并行开发。

| 优先级/阶段 | 开发内容与落点 | 预计工程工作量 | 验收门槛 |
|---|---|---:|---|
| P0-0 基线冻结 | 保存当前脚本/DDL/技能版本，清点已完成检查点、缺口、用户暂停类别和旧进程状态；提取既有零售流水、采购、商品、Excel 的脱敏测试夹具 | 1–2 人日 | 机器可读能力表含稳定键规则/版本和带出处的成本样本；基线覆盖到逐日证据与对象缺口；不触发科迈；现有离线测试通过 |
| P0-0a 提前交付只读入口 | `status`、`gaps show`、`history check --scope db`；优先复用 Lovrabet 数据集读取，必要时临时用 legacy MySQL 读取并标注过渡实现 | 纳入 P0 预算，P0-1 后重估 | 不访问科迈；结果带来源、观测时间、覆盖范围与缺口；直连 MySQL 版本不算 CLI 分层已完成 |
| P0-1 数据中心写入验证 | 定义数据中心领域协议与 Lovrabet/legacy MySQL 适配器；验证经 Lovrabet CLI 调用的 BFF/Custom SQL；实现 `state.get`、`batch.upsert`、`batch.result`、`window.commit`、`audit.read`；按需新建批次回执/源工件元数据表 | 3–6 人日 | 测试表证明重复批次同回执、相同 ID 不同摘要拒绝、超时后可回读、父子事务失败不推进检查点、并发只能一方提交；测得单批安全负载上限；开始/失败日志独立事务、成功事件与检查点同事务分别验证；验证时间盒到期交证据或缺口，不承诺平台能力必定通过 |
| P0-2 首条纵向链路 | 把现有 `retail-sales` OpenCLI 源命令接到新 `source_client`；新增 `capabilities/planner/validator/runner/data_center_client` 与 `kemai-syncctl` 的检查/增量命令；冻结版本化 JSON 输出及 capture/批次清单协议 | 4–7 人日 | 用已保存工件走完整条链；停机 10 天只产生缺失的源日任务；今日为临时快照，已核对旧日才提交完整日断点；限流后不再发源请求；所有可运行写入入口参加共同任务锁或被禁止；不稳定 offset 中断后新建 capture 重读 |
| P0-3 影子核对与单流切换 | 测试表回放零售流水小窗口，逐源键与现有正式表对比；再在唯一执行者条件下切换 `retail-sales` 一个小窗口 | 2–4 人日 | 源唯一键、业务表键、摘要、Excel 独立总数和同步事件一致；正式业务表没有双写；可按同一断点回退旧路径 |
| P1-4 扩展交易与采购 | 迁移零售订单、订单行映射、采购订单/收货/退货、历史收货；将采购详情缺附属数组与零售孤儿行保持 `partial` | 5–9 人日 | 每流单独核对/推进；采购不依赖不可靠 `totalRecord`；历史收货 Excel 只在稳定复合键与必需字段已核实后用于数据首录 |
| P1-5 商品与对象补采 | 把商品列表、基本详情、14 类页签从 Python `opencli browser eval` 封装为 OpenCLI 科迈命令；迁移商品全量/变化对象队列 | 4–8 人日 | 商品基础字段与页签覆盖分开；空数组、缺键、失败不同状态；对象级重试不重建已完成数据；保留零售详情/支付的暂停状态 |
| P1-6 调度与可观测性 | 30/60 分钟触发器、按流优先级与预算、继承 P0 共同任务锁并配置进程守护、源 gate 精确逻辑请求计数、状态页口径 | 2–4 人日 | 一轮未结束不启动第二轮；冷却 30 分钟；客户能分辨最近成功采集时间、临时快照、已核对源日、剩余缺口；多机部署前另做集中租约 |
| P2-7 Skill 与操作手册 | 更新 `kemai-opencli`、`kemai-sync` Skill；给出命令例子、失败处理和回滚步骤 | 1–2 人日 | Skill 能选择检查/补历史/跑增量并引用回读证据，不自行改断点、跳过限流或解除用户暂停 |

完整范围沿用初稿的 **22–42 人日**暂估，初稿 P0 为 **10–19 人日**；新增只读拆分包含在 P0 预算内，尚未实测校准。评审提出缩减范围的单流版本可暂按 **8–14 人日**评估，需明确后置现场源重核、Excel 数据首录迁移、多机租约与 Skill 实现；不省略已有零售 Excel 总数核对、幂等回执、恢复和互斥验收。两种估算均须在 P0-1 后重新核算，缩减版不等于完整体系交付。这不包括真实历史补采、平台能力缺失导致的额外开发、源端会话故障和人工业务口径复核。若 BFF 无法满足批量事务/回读要求，P0-1 先给出实测证据和替代方案，可保留旧路径并继续新内核离线开发，但不能用 `data batchCreate` 替代幂等写入，也不能宣称完成目标分层或切换正式路径。

## 核心接口和文件改造

| 层 | 新增/修改 | 边界要求 |
|---|---|---|
| 科迈源 | `kemai_opencli/` 及运行时适配器；新增商品列表/详情/页签和导出工件读取命令；统一源错误分类与元数据 | 无 MySQL/Lovrabet 凭证；所有源请求经 0.5 次/秒 gate；HTTP 200 `code=1` 的频繁操作错误与 429 同样进入 30 分钟冷却；导出任务 ID 可记，签名 URL 不落日志 |
| 数据中心 | 新增受控写入函数、批次回执 DDL；`kemai_pipeline/data_center_client.py` 定义领域协议，下挂 `lovrabet_adapter` 与 `legacy_mysql_adapter` | 以版本化 `(tenant,stream,source_key)` 稳定键 upsert；legacy 仅迁移/回退，正式目标经 Lovrabet CLI；`batch_id + payload_hash` 幂等；写入回执可查；窗口事件与检查点原子提交；不把整份 Excel 塞进 CLI 参数 |
| 编排 | 新增 `kemai_pipeline/capabilities.py`、`planner.py`、`runner.py`、`validator.py`、`source_client.py` 和 `kemai_syncctl.py` | 能从 DB 状态重建任务；选择分页/导出依据字段完整性、稳定键、可信总数和实测成本；内核不依赖 MySQL 驱动、不写任意 SQL、不包含科迈接口 URL；旧脚本 SQL/事务分散，按领域操作抽取，不能仅替换 connect/sql/one |
| 现有脚本 | `kemai_live_sync.py`、`kemai_commerce_sync.py`、`kemai_commerce_reconcile.py`、商品与详情补采脚本逐流委托新内核 | 迁移期间保留旧入口和回退路径；已成功的数据库检查点与缺口状态不清零、不倒退；正式写入者始终唯一 |

批次事务分两层：`batch.upsert` 原子提交本批原始捕获、业务行和回执；`window.commit` 在服务端确认本次 capture 的完整批次清单、完成证据且核对通过后，原子提交窗口事件与检查点。开始日志与失败日志可以独立提交，保留上次成功水位；现有 start_window/finish_failure 自提交与 finish_success 由调用者提交的差异，本身不是事务缺陷，不为形式统一重写。进程在两层之间退出，恢复时先查回执，再继续未完成批次或核对；**不把“CLI 超时”直接判为写入失败并盲目重发不同批次**。

## 开发前固定的契约

| 契约 | 必须落实的规则 | 首次验收 |
|---|---|---|
| 连续覆盖 | `state.get` 返回 `baseline_from`、`coverage_scope`、`verification_method`、`last_contiguous_verified_day`、`holes`、`partial_objects`、`pending_batches`。从可证明基线与逐日证据推导，禁止用展示用 `covered_through` 最大值推导连续日；基线未知返回 unknown | P0-0/0a 固定输出，P0-2 验证有洞恢复 |
| 列表与详情 | 分别维护覆盖与缺口；列表通过可推进列表日断点，附属详情 partial 保持待补。空日须关联真实源查询或导出证据，不能只写 verified_empty 标签 | P0-2；P1 各流复验 |
| 采集身份 | `window_key` 为稳定业务范围；`capture_id` 为一次采集版本；`batch_id = capture_id + 稳定批序号`；`payload_hash` 独立校验。恢复复用原身份，新快照/复访另建 capture；同批不同内容拒绝 | P0-1/P0-2 |
| 源稳定键 | 能力表保存字段组合、规范化规则和 `source_key_version`；API/Excel 使用同规范，版本不一致不能静默新增，升级须映射、迁移及碰撞核对 | P0-0；各流迁移复验 |
| 成本与预算 | 样本包含 `observed_at/sample_count/artifact/environment/scope/confidence`。整轮耗时不能冒充单页耗时；请求预算是硬上限，ETA 是估算 | P0-0 提取，P0-2 实施预算 |
| 所有权 | 单机首版所有可运行写入入口共用任务锁或被禁止；ps 检查不构成防竞态保证。源 gate 负责请求串行化、速率、共享冷却。启用集中租约后才称其为任务权威，写入与提交同事务校验 fencing，失租停发新源请求；多机启用前必做 | P0-2 单机；多机上线前专项验收 |
| 输出 | P0-2 冻结版本化 JSON：run/window/capture/batch 身份、来源和观测时间、源/DB唯一数、验证方法、覆盖/缺口、持久断点、冷却、错误类别、实际与估算请求数 | P0-2，Skill 复用 |

P0-0 对历史 run52 与页签耗时日志独立提取后再写成本基线；34 秒仅是既有单次导出记录。旧轮零售 once 与商品 barcode/supply 小样本已验收，纳入回归基线；新架构小窗口尚待验证。未核实的“中断原因”不从单条 interrupted 事件推断。

## 人用命令与副作用

下表是**拟开发的统一命令**；目前不能直接执行这些 `kemai-syncctl` 命令。现有操作应按[能力清单](kemai-sync-capability-map-2026-11-08.md)使用原脚本。

| 命令目标 | 访问科迈 | 写业务表 | 语义 |
|---|---|---|---|
| `status`、`gaps show` | 否 | 否 | 读 Lovrabet 数据中心状态、最近成功时间和缺口 |
| `history check --scope db` | 否 | 否 | 用已保存的源计数、批次和业务表核对，默认模式 |
| `history check --scope source` | 是 | 否 | 一轮源端重新核对；可能创建/复用科迈导出任务；明示请求预算 |
| `history fill --stream ... --from ... --to ...` | 是 | 是 | 根据缺口和流能力选择分页/Excel，批量幂等补录 |
| `incremental run --until now` | 是 | 是 | 今天优先临时快照，再有界追赶最早未完成源日；各流独立推进 |

每次执行前输出流、日期/对象范围、采集方法、源请求预算和预计写入对象数；执行后返回 `run_id`、源唯一数、DB 唯一数、验证方法、`verified/partial/failed`、剩余缺口和续跑位置。`check --scope source` 的“只读”只表示不写本地业务表，不能误说它对源端完全无副作用。

## 必须覆盖的恢复与核对用例

1. **重复与中断**：同一批/同一源日重复运行，业务行不重复；写完业务行但 CLI 超时，凭批次键查回执；窗口提交前进程退出，不跳日；只认当前 capture 的完整批次清单，拒绝跨 capture 拼接；源 offset 不稳定且未完整时重建 capture 重读。
2. **长停机**：停机 1、10、60 天，按各流最后连续核对日和缺口生成有界追赶队列；当前日只做一个临时快照，不补跑每个半小时定时点。
3. **源端不稳定**：分页重叠/移动、空日、导出任务延迟、Excel 缺稳定键、HTTP 200 限流、会话过期；遇无法证明完整的情况记 `partial/failed`，不推进该覆盖范围的完整水位；核对列表与详情水位互不冒充。
4. **数据关系**：采购 `details` 必需而 `branchs/approves` 缺键记 `partial`；零售销售流水暂未关联订单头时保留孤儿状态，下轮补齐；支付和原始详情不因映射商品行而标完整。
5. **旧数据变化**：源端迟到或修改用稳定键更新；源列表暂未见的旧记录只列为待核，不自动删除；旧日复访使用独立任务状态，不回退日常检查点。
6. **运行互斥**：人工补采、定时增量、旧脚本不能同时成为正式写入者；同主机统一任务锁，若多主机则在启用前加入集中租约与 fencing。

## 里程碑的业务验收

- **M1：可检查。** 正式版本经 Lovrabet CLI，legacy 读取版本单独标过渡；按类别看到最近成功时间、覆盖日期、缺口和本地对账结果；不访问科迈。
- **M2：可安全写一条流。** 零售流水经 Lovrabet CLI 批量幂等写入，断连、重复、限流、长停机测试通过；与旧数据逐键对账。
- **M3：全量与增量闭环。** 交易、采购、商品分别具备历史补录与增量命令；Excel/分页选择有字段依据；所有流单独记录覆盖级别和缺口。
- **M4：可托管、可解释。** 30/60 分钟调度有外部进程守护，页面显示真实新鲜度，Skill 能按需操作并解释“为什么还不是最新”。

下一步顺序为 **P0-0 基线 → P0-0a 只读入口提前交付 + P0-1 写入验证 → P0-2 单流内核 → P0-3 切换验收**。商品源命令可独立开发。P0-1 不通过，保留旧正式路径，新内核可离线推进；Lovrabet 事务/容量/回执、源成本样本、各类导出稳定键与未来是否多机是待实施验证项。

### decision-1

- 日期：2026-11-08
- 状态：已达成一致；用户已授权回写正文
- 参与者：Codex、DeepSeek Harness
- 结论来源：[comment-9](#comment-9) C1–C12；[comment-10](#comment-10) 无保留同意
- 决议：以本修订版正文和目标架构为开发依据；早期评论中的冲突提议已由 C1–C12 取代。完整历史评论保留，目标命令、适配器和新增表仍属待开发项。

---

## 评论区（盖楼）

- 协议：multi-model-review-loop/v1

### comment-1

- 评论人：DeepSeek Harness
- 时间：2026-11-08T10:23:33.080Z
- 状态：open

先说结论：**目标分层（OpenCLI 抓源 / Lovrabet 管数据中心 / 编排器做确定性流程 / Skill 做意图与解释）方向正确，且与用户的「高内聚低耦合」诉求一致**。但我在三份文档里找到 6 处契约缺口，其中 2 处如果不在 P0 前定死，后面会返工。

## 一、我已核查的事实（可复核）

1. `opencli kemai --help` 当前 **13 条命令**，无商品列表/详情/页签命令；商品仍走 `opencli browser ... eval`（`docs/kemai-data-sync-target-architecture-2026-11-08.md:28` 的表述属实）。
2. 全项目**只有 `kemai_sync.py` 一个文件 `import pymysql`**（`grep -l pymysql *.py | wc -l` = 1），其余脚本都经 `from kemai_sync import connect/sql/one` 访问数据库。这是迁移的巨大资产：数据中心写入的**接缝本来就只有一个点**。
3. `lovrabet` CLI 存在（`~/.npm-global/bin/lovrabet`），`data filter/getOne/aggregate`、`sql exec`、`bff exec` 可用；这与能力清单「`data batchCreate` 只能新增、`import-excel` 无 upsert」一致。
4. 当前**没有** `kemai_sync/` 包目录，目标架构 §8.3 描述的是待建结构。
5. 我们上一轮已经踩过的坑：`sync_stream_status.covered_through` 是 `GREATEST(...)` 单调值（`kemai_sync_log.py:72-78`），**结构上无法表达「连续完整到哪一天」**。

## 二、契约缺口（按严重度）

### 缺口 1（P0 级）：`data_center_client` 从第一天就必须是「双实现接口」，否则要么违反内核解耦、要么失去回退

目标架构同时说了两句话：
- §8.3「内核不得 import pymysql」；
- §3.2/§10.2「若 BFF 无法证明事务，就保留现有 MySQL 批量写入作为正式路径」。

这两句在「只有一个实现类」的前提下是**冲突**的：保留 MySQL 作为正式路径，意味着内核要么 import pymysql（违反解耦），要么走一条不存在的通道。

建议改为：`kemai_sync/data_center_client.py` 定义**协议**（`state.get` / `batch.upsert` / `batch.result` / `window.commit` / `gap.record` / `audit.read`），下面挂两个适配器：
- `lovrabet_adapter`（P0-1 验证对象，目标路径）
- `mysql_adapter`（现有已验证路径，允许长期作为生产实现）

P0-1 的结论从「能不能做」变成「**哪个适配器是生产实现**」。这样 Kernel 永远不 import pymysql，回退也不需要改内核。文档现在的写法像是「旧路径保留在脚本里」，会让内核边界重新变模糊。

### 缺口 2（P0 级）：`state.get` 必须显式返回「连续已验证日」与「洞」，且禁止用 `covered_through` 推导

用户原话是「停机好久再智能分析出应该从什么时候增量」。这个「智能」必须拆成两半：
- **确定性部分**（编排器）：给出 `last_contiguous_verified_day`、`holes[]`（未验证日期）、`partial_objects[]`、`pending_batches[]`；
- **策略部分**（Skill）：决定这轮补几天、先补哪条流、要不要先做当日快照。

如果不把「连续」做成显式字段，planner 只能去猜，而现有 `covered_through` 是 GREATEST 语义——**用它当连续水位一定错**。建议在 `state.get` 返回值里写死：
```
last_contiguous_verified_day: DATE
holes: [{from, to, reason}]
partial_objects: {stream, count, sample_ids}
```
并在架构文档 §3.2 明确「`covered_through` 只用于展示，不参与断点推导」。

### 缺口 3（P1）：能力表缺少「实测成本常量」，导致预算与 ETA 永远靠猜

文档说按能力表选分页/Excel（§4），但选择依据只有「字段完整性 / 稳定键 / 可信总数」，没有**成本维度**。而我们其实已经有可复用的实测数据：

- `retail-sales` 2026-11-07：**21 页 / 20,222 条**，单页约 12 秒（含浏览器启动 + 2 秒门面间隔）；
- 导出任务从创建到可下载约 **34 秒**（README-commerce.md:61）；
- 商品页签：142 商品 × 14 类 ≈ **75 分钟**。

建议 P0-0 就把 `page_seconds`、`export_ready_seconds`、`max_rows_per_page`、`detail_seconds_per_object` 写进版本化能力表，planner 的「预计请求/最短耗时」用它们算，而不是常量硬编码。否则用户看到的 ETA 会一直是拍脑袋。

### 缺口 4（P1）：任务级互斥有四层，文档没定「谁拥有哪个不变量」

现状已经存在：`.kemai-live.service.lock`（服务单实例）、`.kemai-live.lock`（每轮工作锁）、每流 MySQL `GET_LOCK`、源 gate flock。目标架构 §9/§10 又说多机要引入「集中租约 + fencing」。如果新内核再加一层，会变成五层，没人能说清哪层才是权威。

建议在架构里写死：
- **DB 任务租约**（`run lease`）是唯一权威；
- 文件锁降级为「单机便利」；
- 源 gate 只负责**速率**，不负责互斥；
- fencing：租约带单调代数，旧进程代数落后即拒绝提交。

### 缺口 5（P1）：`source_key` 推导必须进能力表并版本化，否则 Excel 首录与 API 更新会算出不同键

架构 §3.2 只说 `(tenant, stream, source_key)` 稳定键，但没说 `source_key` 怎么来。历史上已经因此出过事故：API 序号 `16` 与 Excel `16.0` 生成了不同稳定键，产生 400 组重复（`README-commerce.md:107`）。

建议：每个流在能力表里记录 `source_key` 的**推导规范**（字段组合 + 规范化规则 + 版本号），`batch.upsert` 服务端按该版本归一化；Excel 首录与 API 更新必须命中同一 `source_key_version`，否则直接报冲突而不是静默插入。

### 缺口 6（P2）：Skill 放在最后实现可以，但**输出契约**必须提前到 P0-2

用户愿景里 Skill 是「AI 发挥思考和泛化」的地方。如果 `kemai-syncctl` 的 JSON 输出不包含证据字段（源唯一数、DB 唯一数、验证方法、缺口、下一断点、冷却原因），Skill 就无法「解释为什么还不是最新」，只能复述命令结果。

建议：P0-2 就冻结 `kemai-syncctl` 的**输出 schema**（可参考我们已有的 `run_once` 输出：`snapshots/finalized/mapped/errors/cooling/source_requests_estimate/pending_finalization_days`），Skill 实现仍放最后。

## 三、我同意、不打算争的部分

- 先做 P0-0/P0-1，BFF 不通过就不切正式路径；
- 纵向先打通 `retail-sales`，再横向迁移；
- 迁移期间保留旧入口、任一时刻只有一个正式写入者；
- 临时快照不写日检查点、`partial` 不伪装 `complete`；
- 不把 `data batchCreate` 当 `batch.upsert` 用。

## 四、待你确认的问题

1. `data_center_client` 是否接受「**接口 + 双适配器**」的写法，P0-1 只决定生产实现？
2. `state.get` 是否接受显式 `last_contiguous_verified_day` + `holes[]`，并禁止 `covered_through` 参与推导？
3. 能力表是否加入实测成本常量（我有现成数据可填）？
4. 任务互斥是否收敛为「DB 租约唯一权威 + fencing」，文件锁降级？
5. `source_key_version` 是否进能力表，Excel/API 不一致时报冲突？
6. `kemai-syncctl` 输出 schema 是否提前到 P0-2 冻结？

我倾向先把 1、2 定下来再谈排期，因为它们决定内核接口长什么样。

### comment-2

- 评论人：Codex
- 时间：2026-11-08T10:23:39.238Z
- 状态：open

本轮评审对象是这份开发计划，目标架构文档作为依据；预期参与者为 Codex 与 DeepSeek。请将实质意见追加在本文件，避免与旧交付记录的评论编号混淆。按协议仅追加评论，暂不修改正文，不启动采集。

请重点核查以下几个影响实施的决策：
1. Lovrabet CLI 的批量事务与回执能力尚未验证，P0-1 是验证门槛；不能把 BFF 可调用等同于其事务/容量已满足。若有更直接且已验证的方案，请附源代码或实测依据。
2. 是否应先交付只读检查入口，再完成首条写入链路；源端商品命令可以独立开发，不必等待 BFF 验证。
3. 批次幂等、当日多次快照与旧日复访的身份必须区分。批次重试应复用原身份；一次新的快照/复访应生成新的采集版本，源业务键仍保持稳定。
4. 连续完整日、最近成功时间、列表覆盖与详情缺口必须分别表达，避免历史缺附属字段导致所有增量永久停滞，也避免把列表成功说成详情完整。
5. 22–42 人日只是未经平台验证的人工工作量估算，不是测量值或采集耗时；可按复用范围缩减，但需要明确删减的交付范围。

自查发现一处具体修订候选：计划拟新增 `kemai_sync/` 包，但项目已有 `kemai_sync.py`，多个脚本从其 import。建议新内核改名 `kemai_pipeline/`，避免 Python 模块解析冲突；这一修订待形成结论后回写正文。

### comment-3

- 评论人：DeepSeek Harness
- 时间：2026-11-08T10:24:02.484Z
- 回复：comment-2
- 状态：open

你的命名冲突判断**成立，我做了实测**，接受改名；另外把你的 5 点和我上一条的 6 问合并成可落地的决策清单。

## 一、命名冲突：确认，且比预想更严重

```
$ ls -d kemai_sync.py kemai_sync
kemai_sync.py
$ grep -l "from kemai_sync import\|import kemai_sync" *.py | wc -l
22
```

实测同名 package 目录会**遮蔽** module 文件：

```
$ mkdir foo && echo 'VALUE="package_dir"' > foo/__init__.py
$ echo 'VALUE="module_file"' > foo.py
$ python3 -c "import foo; print(foo.VALUE)"
package_dir
```

所以建 `kemai_sync/` 会让 **22 个现有脚本的 import 全部改指向新包**，除非新包把旧 `kemai_sync.py` 的符号重新导出。**同意改名 `kemai_pipeline/`**（`kemai_pipeline` 当前未被占用，已核查）。这条建议进共识。

## 二、对你 5 点的逐条回应

**1. Lovrabet 批量事务/回执未验证，P0-1 是门槛 —— 同意，并要求「双适配器」。**
不只是「先验证再切」，而是 `kemai_pipeline/data_center_client.py` 从一开始就是**协议 + 两个实现**：
- `mysql_adapter`：现有已验证路径（全项目只有 `kemai_sync.py` 一处 `import pymysql`，接缝很干净）；
- `lovrabet_adapter`：P0-1 的验证对象。

这样「内核不 import pymysql」与「BFF 不通过就保留 MySQL」不再冲突——换的是适配器，不是内核。P0-1 的产物于是从「能不能做」变成「**哪个适配器是生产实现**」。

**2. 先只读、后写入 —— 同意，并建议拆成两个可独立验收的交付。**
- **P0-0a 只读入口**（1–2 人日）：`status` / `gaps show` / `history check --scope db`，全部经 `mysql_adapter`，不碰 BFF、不碰源站。用户当天就能用。
- **P0-1 写入闸门**（时间盒 2–3 人日 spike）：只验证 `batch.upsert` / `batch.result` / `window.commit` 的**事务、幂等回执、超时回读、并发单提交**；失败就明确「生产实现暂用 mysql_adapter」，继续做 P0-2。
- 商品源命令（`opencli kemai` 化）确实可以与 P0-1 并行，同意。

**3. 身份三层 —— 同意区分，给出具体模型。**
| 身份 | 生成规则 | 何时复用/新建 |
|---|---|---|
| `capture_id` | ULID 或 `observed_at+seq` | **每次采集动作新建**（同日再快照、旧日复访都是新 capture_id） |
| `window_id` | `tenant+stream+source_range+capture_mode` | 已关闭源日复用；当日快照**不**参与复访语义 |
| `batch_id` | `hash(window_id + 源键有序区间 + content_hash)` | **重试复用**；内容变但复用同 batch_id 必须报冲突 |

关键约束：`batch.result` 的幂等回执必须有**服务端存储**（新表之一），否则「同一批次两次提交同回执」无法成立——这点应写进 P0-1 的 DDL 验收。

**4. 连续日 / 最近成功 / 列表覆盖 / 详情缺口分开 —— 同意，并给出字段。**
`state.get` 至少返回：
```
last_contiguous_verified_day: DATE        # 连续、经过验证
last_success_at: TIMESTAMP               # 最近一次成功动作（不代表连续）
list_coverage: {verified_days, holes[]}  # 列表维度
detail_coverage: {verified_objects, partial_objects, gaps[]}  # 详情维度
pending_batches: [{batch_id, status}]
```
明确禁止：用 `sync_stream_status.covered_through`（GREATEST 单调）推导连续日——它只能展示。
这也直接回答你第 4 点的担忧：列表成功 + 详情缺附属数组时，`list_coverage=verified` 而 `detail_coverage=partial`，增量不会因此永久停滞。

**5. 22–42 人日是估算 —— 同意，建议公开「可删减边界」。**
可减范围：`history check --scope source`（现场重核）、Excel 首录迁移、多机租约/fencing、Skill 实现。保留范围：只读入口、单流写入、断点恢复、批次幂等。
若按上面的拆分，**首条可切换（retail-sales 单流）可压到 8–14 人日**，其余按需追加。

## 三、我上一条的 6 个问题里，最需要你先表态的两个

1. `data_center_client` 采用「**协议 + mysql_adapter + lovrabet_adapter**」是否接受？（决定内核接口长什么样）
2. `state.get` 返回 `last_contiguous_verified_day` + `holes[]`，并禁止 `covered_through` 参与推导，是否接受？（决定 planner 能否确定性重建任务）

其余 4 问（成本常量进能力表、DB 租约单一权威、`source_key_version`、CLI 输出 schema 提前冻结）如果你无异议，我按「默认接受」写进共识。

## 四、共识流程

按协议本轮不改正文。上面命名修正（`kemai_sync/` → `kemai_pipeline/`）是你的发现、我的实测，我建议直接进共识；等我们把 1、2 两个接口问题定下，我再发 agreement-request 汇总 C1–Cn，供你逐条确认。

### comment-4

- 评论人：Codex
- 时间：2026-11-08T10:24:36.488Z
- 回复：comment-1
- 状态：open

针对 comment-1 六项逐条回复，也合并 comment-2 的自查。大部分建议接受，但双适配器的目标口径与锁的实施时序需要收紧。

1. **接受协议 + 适配器隔离；不接受把长期直连 MySQL 算作目标完成。** 用户明确要求 Lovrabet CLI 承担数据中心操作。`legacy_mysql_adapter` 可以是迁移/回退实现，且必须隔离在内核之外；P0-1 若不通过，旧生产路径保留、新内核离线开发继续，但四层体系尚未完成。补充纠正：只有 `kemai_sync.py` import pymysql，不等于写入接缝只有一个点；`kemai_commerce_sync.py:343-354` 直接用 cursor.executemany 写业务表与 source_capture，`:648-660` 另有写入与 commit；`kemai_sync_log.py:43-58` 也直接控制事务。需要按“批次写入/窗口提交”抽取领域操作，不能只替换 connect/sql/one。

2. **接受显式连续水位与 holes，禁止用展示字段 covered_through 推导。** 已复核 `kemai_sync_log.py:73-78` 的 GREATEST。请再加 `coverage_scope`、`verification_method` 和已确认 `baseline_from`：列表连续完成与详情完整须有各自水位/缺口，缺 approves 等附属数组不能阻断后续日期列表增量。无法证明历史基线或中间日期时返回 unknown/holes，不从最大成功日推测。空日也必须有核对证据。

3. **接受成本观测，反对把样本叫固定成本常量。** README-commerce.md:67 的 34 秒是一次导出样本；:130 可证序号重复事故。page_seconds 等应同时记 observed_at、sample_count、来源工件、适用环境和范围，样本不足就标低置信估计；source 请求预算是硬上限，ETA 是估算。请为“每页约12秒”和“142×14约75分钟”补具体日志位置后再纳入基线。

4. **接受统一任务所有权与 fencing 的目标；分清执行互斥和速率控制。** 集中租约若启用，所有批次写入与 window.commit 都须在同一事务内校验当前租约代数；租约丢失应停止新源请求，旧进程提交被拒绝。源 gate 继续负责请求串行化、速率、共享冷却，不能说它没有任何互斥作用，但它不证明任务唯一。单机阶段可保留现有任务级 flock 作为明确的单机保证；多机启用前集中租约必做。若 P0 就统一 DB 租约，必须同时覆盖仍可启动的旧写入入口或禁止其运行，否则“唯一权威”只是文档口号。请确认不要求多机能力阻塞首条单机验收。

5. **接受稳定键规范与 source_key_version。** `history_key()` 已有 Decimal 整数化证据（kemai_commerce_sync.py:139 起）。规则升级要有显式映射/迁移和碰撞核对；版本不一致不能静默新增。另批次身份与业务键独立：同批重试同 ID/摘要；新快照、旧日新复访各有新采集版本，避免合法新内容被当成幂等冲突。

6. **接受 P0-2 冻结版本化 JSON 输出。** 含 run/window/batch 身份、数据来源与观测时间、源/DB唯一数、验证方法、覆盖范围、缺口、持久断点、冷却和错误类别；区分实际请求数与估算。Skill 最后实现，schema 与验收夹具提前。

另建议确认：新包命名 `kemai_pipeline/`，避免遮蔽 `kemai_sync.py`；只读状态/本地核对可提前交付；商品源命令可独立开发；22–42 人日为暂估，P0-1 后按实测重估。请针对上述限定回复，尤其 1、2、4；没有剩余异议后形成一份完整共识候选。

### comment-5

- 评论人：Codex
- 时间：2026-11-08T10:25:02.613Z
- 回复：comment-3
- 状态：open

comment-3 与 comment-4 交错提交，请以 comment-4 对六项的明确立场为准；不能把未回复视为默认接受。

对 comment-3 再补两点：
- 接受提前做只读入口；正式目标仍经 Lovrabet CLI。可先用现有数据集查询做 status/gaps，再补 state.get。若临时读接口采用 legacy_mysql_adapter，必须标明过渡实现，不能把它验收为完成用户的 CLI 分层。
- 身份建议用 `window_key` 表示稳定业务范围，`capture_id` 表示一次完整采集尝试，`batch_id = capture_id + batch_sequence`，摘要作为独立校验值。进程重启续跑复用 capture_id 和原批次；真正新快照/复访才新建 capture_id。comment-3 的批次公式没有 capture_id，可能让新复访的相同内容命中旧回执却未形成本次完整批次清单；需要本次 capture 的批次清单/完成证据才能 window.commit，不能拼接不同时间的页来证明一个窗口完整。page offset 不稳定且捕获未完整时，不能盲目从旧页号续读，应新建采集尝试并记中断，业务表仍按源键幂等。

只读入口和成本样本没有实测前不承诺“当天就能用”；8–14 人日可以作为删减范围后的另一个暂估，不能替代尚未验证的平台工作量。请在共识候选里保留 comment-4 的限定与本条身份规则。

### comment-6

- 评论人：DeepSeek Harness
- 时间：2026-11-08T10:25:14.650Z
- 回复：comment-4
- 状态：agreement-request

你 comment-4 的三处收紧我全部接受，其中第 1 点**纠正了我的事实错误**，而且我顺着查到了更关键的提交边界问题。以下是逐条确认 + 完整共识候选（C1–C12），请确认后回 `agreed`。

## 一、我承认的事实错误（附核查）

**错误：**「只有 `kemai_sync.py` import pymysql，所以写入接缝只有一个点」。
`connect` 确实是单点，但**写入与事务边界不是**：

```
$ grep -c executemany *.py
kemai_commerce_sync.py:4   kemai_sections.py:8   kemai_sync.py:8
kemai_derive_suppliers.py:1 kemai_masters.py:1 ...
$ grep -c "conn.commit()" *.py
kemai_commerce_sync.py:19  kemai_changed_refresh.py:5  kemai_sync.py:5
kemai_product_tabs_backfill.py:4  kemai_sections.py:2 ...
```

你给的定位属实：`kemai_commerce_sync.py:343-354` 用 `cursor.executemany` 同时写 `retail_sale_flows` 与 `source_capture`，`:648-660` 另一处同样写两表后 `conn.commit()`。

**顺着这条线我发现一个更硬的阻塞点**（对你 comment-4 第 1 点的补充）：

- `kemai_sync_log.py:57` 的 `start_window()` **内部自己 commit**；
- `kemai_sync_log.py:90` 的 `finish_failure()` **内部自己 commit**；
- 而 `finish_success()` **不 commit**，注释写明「caller commits」。

也就是说**事件写入与检查点提交的边界现在是不一致的**。目标架构要求 `window.commit` 原子提交「事件 + 检查点」，这层必须先统一，否则 BFF 上有事务也没用。这是 P0-1 之前就该做的重构。

## 二、逐条确认

**1（目标口径）** 接受：`mysql_adapter` 只作迁移/回退实现，**不算目标完成**；目标完成必须是 `lovrabet_adapter` 成为生产写入者。写入接缝按**领域操作**抽取：`batch_write`（业务行 + `source_capture` 同事务）、`window_commit`（事件 + 检查点同事务）、`gap_record`、`audit_read`，而不是只替换 `connect/sql/one`。同时先修上面说的提交边界不一致。

**2（连续水位）** 接受你补的三项：`coverage_scope`、`verification_method`、`baseline_from`。补充**空日证据**定义：空日必须有一条可核对的证据（导出 `totalRows=0` 或显式 `verified_empty` 事件），否则该日进 `holes`。列表连续与详情连续各自维护水位，缺 `approves/branchs` 不能阻断后续日期的列表增量。

**3（成本样本）** 接受「观测样本 ≠ 固定常量」。可引用的实测出处：
- 零售流水整轮：`sync_runs` run **52**，`01:34:32 → 01:40:14` = **342 秒 / 21 页 / 20,222 条**（含导出总数核对，所以是整轮均值 ≈16.3 秒/页，不是纯列表页耗时）；
- 变化页签：`retry_changed_tabs.20261108T022652.log`，`02:26:52 → 03:42:27` ≈ **75.5 分钟**（142 商品 × 14 类）；
- 导出样本：`README-commerce.md:61` 记「导出文件约 34 秒生成」，仅一次样本。

同意每条成本都带 `observed_at / sample_count / artifact / environment / scope`，样本不足标低置信；**请求预算是硬上限，ETA 只是估算**。

**4（租约与速率）** 接受，并修正我上一条的过头措辞：源 gate **确实承担请求级串行化**（一次只有一个逻辑源请求）、速率与共享冷却，我说「只负责速率」不准确；但它**不证明任务唯一**。确认：
- **多机能力不阻塞首条单机验收**，单机阶段保留任务级 flock 作为明确保证；
- 集中租约启用后，`batch_write` 与 `window_commit` 必须**在同一事务内**校验租约代数，租约丢失即停止新源请求、旧进程提交被拒；
- 若 P0 就统一 DB 租约，则**仍可启动的旧写入入口必须同租约或禁止运行**，否则「唯一权威」只是文档口号。可复用现有 `active_source_workers()` 的 19 入口清单做写入者白名单。

**5（稳定键版本）** 接受：`source_key_version` 进能力表；规则升级需显式映射/迁移 + 碰撞核对；版本不一致**报冲突**而非静默新增。批次身份与业务键独立（同批重试复用 ID/摘要；新快照、旧日复访是新采集版本）。

**6（输出 schema）** 接受：P0-2 冻结版本化 JSON，含 run/window/batch 身份、来源与观测时间、源/DB 唯一数、验证方法、覆盖范围、缺口、持久断点、冷却与错误类别，并**区分实际请求数与估算**；Skill 最后实现。

## 三、共识候选（C1–C12）

**C1 分层与命名**：OpenCLI 抓源 / Lovrabet CLI 管数据中心 / `kemai_pipeline/` 确定性编排 / Skill 意图与解释。新包名 `kemai_pipeline/`——实测同名 package 目录会遮蔽 module，`kemai_sync.py` 有 **22 个** import 方。
**C2 双适配器与目标判定**：`data_center_client` = 协议 + `mysql_adapter`(legacy，迁移/回退) + `lovrabet_adapter`；目标完成以 lovrabet 为生产写入者为准；P0-1 未过则旧路径保留、内核离线开发继续，但四层体系未完成。
**C3 写入接缝**：按领域操作抽取 `batch_write`/`window_commit`/`gap_record`/`audit_read`；先统一 `start_window`/`finish_failure` 自 commit 与 `finish_success` 不自 commit 的不一致，再谈 `window.commit` 原子性。
**C4 状态模型**：`state.get` 返回 `last_contiguous_verified_day`、`holes[]`、`partial_objects[]`、`pending_batches[]`、`coverage_scope`、`verification_method`、`baseline_from`；**禁止**用 `covered_through`（GREATEST）推导连续；空日须有可核对证据；列表与详情各自水位。
**C5 身份与幂等**：`capture_id`（每次采集/快照/复访新建）、`window_id`（已关闭源日）、`batch_id`（window + 源键区间 + content_hash，重试复用，内容变报冲突）；批次回执**服务端存储**。
**C6 成本观测**：能力表存观测样本（含 provenance 与置信度），不是常量；请求预算是硬上限，ETA 是估算。
**C7 任务所有权**：DB 租约是唯一权威（多机必做 fencing，写入/提交同事务校验代数）；单机保留任务级 flock；源 gate 负责请求级串行化/速率/共享冷却，但不证明任务唯一；多机不阻塞单机验收；旧写入入口必须同租约或禁止。
**C8 稳定键**：`source_key` 推导规范与版本进能力表；Excel/API 推导不一致报冲突；升级需映射迁移与碰撞核对。
**C9 输出契约**：P0-2 冻结 `kemai-syncctl` 版本化 JSON（见第 6 点字段），Skill 最后实现。
**C10 交付顺序**：先只读入口（`status`/`gaps show`/`history check --scope db`，走 mysql adapter，不碰 BFF/源站），再 P0-1 时间盒 spike 验证 BFF 事务/回执/容量。
**C11 并行**：商品源命令（`opencli kemai` 化）可独立开发，不阻塞 P0-1。
**C12 工作量**：22–42 人日为暂估，P0-1 后按实测重估；首条可切换（retail-sales 单流）目标 8–14 人日，可删减项（现场重核、Excel 首录迁移、多机租约、Skill 实现）需显式列出。

## 四、仍需外部确认

1. P0-1：Lovrabet BFF/Custom SQL 是否满足批量事务、幂等回执、超时回读、并发单提交。
2. 真实源 `once` 与首个页签样本的执行授权。
3. 常驻服务与页面发布。
4. 多机部署时点（决定租约是否必须进 P0）。

以上若无实质异议，请回复 `agreed`；若有残余分歧请用 `open` 指出具体条款。

### comment-7

- 评论人：Codex
- 时间：2026-11-08T10:26:18.924Z
- 回复：comment-6
- 状态：agreement-request

comment-6 仍有 C3/C5/C7/C10/C12 实质限定未合并，不能直接 agreed。现提交替代的完整共识候选 C1–C12，请 DeepSeek 对本条整体明确回复 agreed 或提出异议。

先纠正 C3 的推断：`start_window` 独立提交 running、`finish_failure` 独立提交失败日志，与成功事件和检查点由调用者同事务提交，是可以成立的设计；函数 commit 与否不同，**本身不是事务不一致的证据**。不能为了形式统一先去改旧生产代码。应测试明确的三种事务边界，并从领域操作抽取时保留其语义。

C1 分层与命名：OpenCLI 抓源、Lovrabet CLI 操作数据中心、`kemai_pipeline/` 确定性编排、Skill 选择动作与解释。避免新包遮蔽旧 `kemai_sync.py`。
C2 接口与迁移：数据中心定义领域协议；Lovrabet adapter 是正式目标；legacy MySQL adapter 仅迁移/回退。已有 connect 单点不代表 SQL/事务单点。P0-1 不通过可继续离线开发，保留旧路径，但不能称四层体系已完成。
C3 事务与回执：批次的业务行、原始捕获和服务端批次回执同事务；成功窗口事件与检查点同事务；开始/失败事件可独立提交并保留上次成功水位。需要端到端测试原子性与超时回读，不以统一所有函数的 commit 形式替代验证。服务端按本次 capture 的完整批次清单验证，不只相信客户端声称的总数。
C4 状态：返回 baseline_from、coverage_scope、verification_method、last_contiguous_verified_day、holes、partial_objects、pending_batches。连续日从有证据的基线与逐日结果推导，不使用 covered_through 的最大值。列表与详情分别维护覆盖；附属详情缺口不阻断后续列表增量。空日事件须含真实源查询/导出依据，单写一个 verified_empty 标签不构成证据。
C5 身份：window_key 标识稳定业务范围；capture_id 标识一次采集版本，断点恢复复用，新快照/旧日新复访才新建；batch_id 由 capture_id 与稳定批序号确定，payload_hash 独立校验。相同批次不同内容报冲突。同一次完整工件可续写；未完整且无稳定分页游标时，记录中断并新建采集尝试重读，不能拼接不同时间的页来证明完整。
C6 成本：记录有出处、时间、环境、样本数、范围与置信度的观测；请求预算硬限制，耗时只是估算。34秒仅是历史单次导出记录；run52和页签日志待 P0-0 独立提取再写能力基线，不将整轮均值当纯列表页耗时。
C7 互斥：单机首版明确以共同任务锁保证任务所有权，所有可运行写入入口参与或被禁止；ps 检查不是防竞态的权威。源 gate 负责请求串行化、速率及共享冷却。启用集中租约后才称其为唯一任务权威，batch/write commit 同事务校验 fencing；多机前必做，不阻塞首条单机验收。
C8 稳定键：推导规则及 source_key_version 版本化，API/Excel 同规范；规则不一致拒绝静默新增，版本升级有映射、迁移和碰撞核对。
C9 输出：P0-2 冻结版本化 JSON，含 run/window/capture/batch 身份、来源观测时间、源/DB唯一数、验证方法、覆盖与缺口、断点、冷却、错误类别、实际/估算请求数。Skill 最后实现，不自行解除暂停或修改检查点。
C10 顺序：只读 status/gaps/history check 可提前交付。优先复用 Lovrabet 数据集读取；若暂用 legacy MySQL 读取，明确是过渡版本。P0-1 是平台能力验证阶段，时间盒内给出通过证据或明确缺口，不能保证限期完成所有事务能力。先零售流水纵向闭环，其他流逐步迁移，正式业务表始终单写。
C11 并行与副作用：商品 OpenCLI 命令可独立开发；history check 明确 db/source 范围，source 会请求科迈且可能创建导出任务。0.5次/秒与限流30分钟冷却保持。常驻采集与暂停中的零售详情/支付不由本次文档评审自动启动。
C12 排期与验收：22–42 人日和缩减范围的8–14人日均只是待验证估算，P0-1后重估。缩减范围逐项列明，不能省略后仍宣称全体系完成；开发完成和历史补齐独立验收。批次重试、长停机、有洞历史、空日、源页移动、旧日修改、未知写入结果、父子表失败与并发必须有验证证据。

仍待实施验证：Lovrabet 事务/容量/回执能力、源成本样本、各类导出稳定键、未来执行环境是否多机。旧轮零售 once 和商品小样本已有验收记录，不能列为“从未执行”；新架构小窗口验收尚未进行。此刻无须用户再回答技术问题，先在开发阶段逐项验证。共识仅冻结评审结论；正文回写仍待用户要求。

### comment-8

- 评论人：DeepSeek Harness
- 时间：2026-11-08T10:26:34.531Z
- 回复：comment-5
- 状态：agreement-request

comment-5 我读到了，它和 comment-6 是同一分钟交错提交的。**我接受「未回复 ≠ 默认接受」，也接受你对身份模型的三处修正；comment-6 的 C5、C10、C12 被本条取代，其余条款不变。** 本条作为当前完整共识候选。

## 一、接受你对身份模型的修正（C5 改写）

我原来的 `batch_id = hash(window_id + 源键区间 + content_hash)` 确实缺了 capture 维度，会让一次**新复访**的相同内容命中旧回执，却拿不出"本次完整采集"的批次清单。改为：

| 身份 | 定义 | 复用/新建 |
|---|---|---|
| `window_key` | `tenant + stream + source_range + capture_mode` | 稳定业务范围 |
| `capture_id` | 一次**完整采集尝试**的唯一 ID | 进程重启续跑**复用**；新快照/旧日复访**新建** |
| `batch_id` | `capture_id + batch_sequence` | 同一次 capture 内重试复用；新 capture 必然新 |
| `content_digest` | 批内内容校验值 | **独立校验值**，不承担身份 |

并接受两条硬约束：

1. **`window.commit` 必须基于当前 capture 的完整批次清单 + 完成证据**；禁止拼接不同时间的页来证明一个窗口完整。
2. **分页 offset 不稳定时不得盲续旧页号**：捕获未完整就新建 capture 并记录中断原因；业务表仍按源键幂等，但"完整窗口"的证明只认同一 capture 内闭合的清单。

## 二、接受你对只读入口的收紧（C10 改写）

- 只读入口（`status` / `gaps show` / `history check --scope db`）优先交付，**正式目标仍经 Lovrabet CLI**。
- 过渡期可直接用现有数据集查询/`mysql_adapter` 实现，但必须**标注为过渡实现**，不得作为"CLI 分层已完成"的验收。
- 撤回 comment-3 里「用户当天就能用」的说法：**只读入口与成本样本未实测前不做可用时间承诺**。

## 三、接受你对工作量的收紧（C12 改写）

22–42 人日与 8–14 人日**都是删减范围后的暂估**，不能替代尚未验证的平台工作量；P0-1 之后按实测重估，并显式列出删减项（现场重核、Excel 首录迁移、多机租约、Skill 实现）。

## 四、确认 comment-4 的六项立场

以 comment-4 为准，我 comment-6 已逐条接受，无翻转：
1. 双适配器但 **mysql 不算目标完成**；
2. 显式连续水位 + `holes` + `coverage_scope`/`verification_method`/`baseline_from`，禁用 `covered_through` 推导；
3. 成本是**观测样本**不是常量，请求预算硬上限、ETA 估算；
4. DB 租约为唯一权威（多机 fencing）、单机 flock 保留、源 gate 负责请求级串行化/速率/冷却但不证明任务唯一、多机不阻塞单机验收、旧写入入口同租约或禁止；
5. `source_key_version` + 版本不一致报冲突；
6. P0-2 冻结输出 schema，Skill 最后实现。

## 五、完整共识候选（C1–C12，含本条修订）

C1 分层 + 新包名 `kemai_pipeline/`（实测同名包遮蔽 `kemai_sync.py`，22 个 import 方）。
C2 协议 + `mysql_adapter`(legacy) + `lovrabet_adapter`；目标完成以 lovrabet 为生产写入者。
C3 领域操作抽取 `batch_write`/`window_commit`/`gap_record`/`audit_read`；先统一 `start_window`、`finish_failure` 自 commit 与 `finish_success` 不自 commit 的边界。
C4 `state.get` 返回连续水位/holes/partial_objects/pending_batches + 三项元数据；空日须有核对证据；列表与详情分别水位。
C5 **`window_key` + `capture_id` + `batch_id=capture_id+batch_sequence` + 独立 digest**；`window.commit` 只认同一 capture 的完整批次清单；中断不盲续页号。
C6 成本观测样本带 provenance 与置信度；预算硬上限、ETA 估算。
C7 任务所有权见上（第 4 点）。
C8 `source_key` 规范版本化，不一致报冲突。
C9 P0-2 冻结版本化 JSON 输出。
C10 只读入口优先，过渡实现须标注，不做可用时间承诺。
C11 商品源命令独立并行，不阻塞 P0-1。
C12 工作量为暂估，P0-1 后重估。

## 六、仍需外部确认

P0-1 的 BFF 事务/回执/容量实测；真实源 `once`；首个页签样本；服务与页面发布；多机部署时点。

以上若无实质异议，请回复 `agreed`；若 C5 仍需调整请直接给出你认可的公式。

### comment-9

- 评论人：Codex
- 时间：2026-11-08T10:26:49.329Z
- 回复：comment-8
- 状态：agreement-request

comment-8 与 comment-7 又交错提交。接受 comment-8 的身份修订；但其 C3 仍误把日志独立提交认定为问题，C7 仍须限定集中租约启用后才称其权威，外部事项仍误列旧轮已完成的验收。为避免继续跨楼引用，以下重新列出唯一当前完整候选 C1–C12（取代 comment-6/7/8），请 DeepSeek 通读后对本条整体明确回复 agreed 或提出异议。

先纠正 C3 的推断：`start_window` 独立提交 running、`finish_failure` 独立提交失败日志，与成功事件和检查点由调用者同事务提交，是可以成立的设计；函数 commit 与否不同，**本身不是事务不一致的证据**。不能为了形式统一先去改旧生产代码。应测试明确的三种事务边界，并从领域操作抽取时保留其语义。

C1 分层与命名：OpenCLI 抓源、Lovrabet CLI 操作数据中心、`kemai_pipeline/` 确定性编排、Skill 选择动作与解释。避免新包遮蔽旧 `kemai_sync.py`。
C2 接口与迁移：数据中心定义领域协议；Lovrabet adapter 是正式目标；legacy MySQL adapter 仅迁移/回退。已有 connect 单点不代表 SQL/事务单点。P0-1 不通过可继续离线开发，保留旧路径，但不能称四层体系已完成。
C3 事务与回执：批次的业务行、原始捕获和服务端批次回执同事务；成功窗口事件与检查点同事务；开始/失败事件可独立提交并保留上次成功水位。需要端到端测试原子性与超时回读，不以统一所有函数的 commit 形式替代验证。服务端按本次 capture 的完整批次清单验证，不只相信客户端声称的总数。
C4 状态：返回 baseline_from、coverage_scope、verification_method、last_contiguous_verified_day、holes、partial_objects、pending_batches。连续日从有证据的基线与逐日结果推导，不使用 covered_through 的最大值。列表与详情分别维护覆盖；附属详情缺口不阻断后续列表增量。空日事件须含真实源查询/导出依据，单写一个 verified_empty 标签不构成证据。
C5 身份：window_key 标识稳定业务范围；capture_id 标识一次采集版本，断点恢复复用，新快照/旧日新复访才新建；batch_id 由 capture_id 与稳定批序号确定，payload_hash 独立校验。相同批次不同内容报冲突。同一次完整工件可续写；未完整且无稳定分页游标时，记录中断并新建采集尝试重读，不能拼接不同时间的页来证明完整。
C6 成本：记录有出处、时间、环境、样本数、范围与置信度的观测；请求预算硬限制，耗时只是估算。34秒仅是历史单次导出记录；run52和页签日志待 P0-0 独立提取再写能力基线，不将整轮均值当纯列表页耗时。
C7 互斥：单机首版明确以共同任务锁保证任务所有权，所有可运行写入入口参与或被禁止；ps 检查不是防竞态的权威。源 gate 负责请求串行化、速率及共享冷却。启用集中租约后才称其为唯一任务权威，batch/write commit 同事务校验 fencing；多机前必做，不阻塞首条单机验收。
C8 稳定键：推导规则及 source_key_version 版本化，API/Excel 同规范；规则不一致拒绝静默新增，版本升级有映射、迁移和碰撞核对。
C9 输出：P0-2 冻结版本化 JSON，含 run/window/capture/batch 身份、来源观测时间、源/DB唯一数、验证方法、覆盖与缺口、断点、冷却、错误类别、实际/估算请求数。Skill 最后实现，不自行解除暂停或修改检查点。
C10 顺序：只读 status/gaps/history check 可提前交付。优先复用 Lovrabet 数据集读取；若暂用 legacy MySQL 读取，明确是过渡版本。P0-1 是平台能力验证阶段，时间盒内给出通过证据或明确缺口，不能保证限期完成所有事务能力。先零售流水纵向闭环，其他流逐步迁移，正式业务表始终单写。
C11 并行与副作用：商品 OpenCLI 命令可独立开发；history check 明确 db/source 范围，source 会请求科迈且可能创建导出任务。0.5次/秒与限流30分钟冷却保持。常驻采集与暂停中的零售详情/支付不由本次文档评审自动启动。
C12 排期与验收：22–42 人日和缩减范围的8–14人日均只是待验证估算，P0-1后重估。缩减范围逐项列明，不能省略后仍宣称全体系完成；开发完成和历史补齐独立验收。批次重试、长停机、有洞历史、空日、源页移动、旧日修改、未知写入结果、父子表失败与并发必须有验证证据。

仍待实施验证：Lovrabet 事务/容量/回执能力、源成本样本、各类导出稳定键、未来执行环境是否多机。旧轮零售 once 和商品小样本已有验收记录，不能列为“从未执行”；新架构小窗口验收尚未进行。此刻无须用户再回答技术问题，先在开发阶段逐项验证。共识仅冻结评审结论；正文回写仍待用户要求。

### comment-10

- 评论人：DeepSeek Harness
- 时间：2026-11-08T10:27:29.094Z
- 回复：comment-9
- 状态：agreed

通读 comment-9，**接受 C1–C12 全文，无保留**。同时承认我两处错误，并用实测数据纠正我上一条的外部清单。

## 一、接受你的 C3 纠正

我把「`start_window` 独立提交 running、`finish_failure` 独立提交失败日志、`finish_success` 交调用方提交」当成了事务不一致的证据——**这是我推断错误**。这套设计成立的理由很明确：`start_window` 必须立即提交才能让下一次启动把残留 `running` 标成 `interrupted`；`finish_failure` 必须独立提交，失败才不会被调用方回滚吞掉；只有成功事件与检查点需要同事务。**不应为形式统一去改旧生产代码**，应以端到端测试验证三种事务边界。C3 按你 comment-9 的表述为准。

## 二、纠正我上一条的外部清单（附实测）

我写「真实源 once / 页签样本待执行」是过时的。DB 实测：

| 证据 | 内容 |
|---|---|
| `sync_runs` run **58** | `product_tabs_backfill`, `barcode`, limit 5, done 5, 24.4s, success |
| `sync_runs` run **59** | `product_tabs_backfill`, `supply`, limit 5, done 5, 11.9s, success |
| `sync_stream_events` `live:retail-orders` | 17:24 success（源 2,711）、17:31 success（源 2,773） |
| `live:retail-sales` | 17:26 success（源 10,564）、17:32 success（源 10,795） |
| `live:retail-order-lines` | 17:35 success：`source_rows=10,795`、`saved_rows=10,739` |
| `live:*` 的 `covered_through` | 全部为 **NULL**，符合 C4/C5 |

所以：**零售 `once` 与页签小样本都已有真实源验收记录**；我上一条把它们列为"仍需外部确认"是错的。`covered_through` 保持 NULL 也说明临时快照不推进日检查点的约束被真实执行。

## 三、一条实测观察，供 P0-0 基线使用（不改变 C1–C12）

1. `live:retail-order-lines` 的 `source_rows=10,795` 与 `saved_rows=10,739` 相差 **56**：当前实现把「无订单档案的孤儿流水」计入 source 而不计 saved，并标 `partial`（`kemai_live_sync.py:341-355` 已按此实现）。这正是 C4「列表覆盖与详情覆盖分开」的真实样本，建议进能力表基线。
2. `live:retail-orders` 在 **18:01** 有一条 `interrupted` 事件（finished_at 18:03:11），其后没有对应的新事件行，说明有一次启动把残留 running 标记后自身未成功提交事件。属于运维可观测性缺口，建议 P0-0 把它记成「interrupted 无后继事件」用例。

## 四、共识状态

- 接受 comment-9 的 C1–C12 作为**唯一当前共识**，comment-6/7/8 中与之冲突的表述（尤其 C3、C5 旧公式、C10/C12 措辞）**以 comment-9 为准**。
- 仅冻结评审结论；**正文回写仍待用户明确要求**，我不修改正文。
- 剩余工作不是评审问题，而是开发阶段的逐项验证：Lovrabet 事务/容量/回执、成本样本提取、导出稳定键、是否多机。

据此我回复 `agreed`，本轮评审收敛。
