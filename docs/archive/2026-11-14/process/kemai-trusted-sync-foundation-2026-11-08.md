# 科迈可信同步内核加固：交付与验收

> 2026-11-08；当前 `main` 工作区交付。本文记录本地代码、离线测试、显式运行时安装和数据中心只读核验。未访问科迈源站，未启动采集或常驻服务，未执行生产业务写入，未发布 BFF，未提交或推送代码。

## 目标

保留 OpenCLI 源连接器、Python Planner/Runner、Lovrabet CLI/BFF/标准模型 API 的现有分层，补齐可恢复、可核对、非泄漏的执行契约。

| 语义 | 本轮口径 |
|---|---|
| `requested_day_committed` | 只有 committed capture 的实际窗口等于该任务的请求日才为 true |
| `deferred` | 每个未执行或未完成的任务都有独立结果，不因跳出循环而消失 |
| `method` | 新 manifest 在首次数据中心写入前持久化；旧 manifest 缺字段时按 capability 解析，不回写历史工件 |
| `planning_pending` | 本轮预算外的 outage/coverage-hole 补录，不混入执行 `tasks` |
| `coverage_gaps` | detail/object 等缺口只报告为 `unscheduled`，不冒充按天列表任务 |
| `download_ready` | 公开导出任务的下载就绪布尔值；签名 URL 只留在下载命令内部 |
| `capabilities` | 静态命令与副作用元数据；已安装不等于已登录或源站已验收 |

## 已交付改动

### 1. 采购事务与回滚证据

- Node fixture 记录 `{transaction, table, operation}`，验证 `capture.begin`、`batch.upsert`、`window.commit` 都在事务内先更新 `sync_pipeline_streams`，再读取控制行。
- 保留采购 `source_list_hash` 写后读回和 `PERSISTED_LIST_HASH_MISMATCH` 回滚测试，未新增第二套批次协议。
- 强制回滚探针增加独立 before/after 快照：业务行、raw capture、随机 capture/batch/window、stream controller/status 和同日 events。
- 报告只含结构化布尔值与计数，不输出业务 payload。本轮没有运行真实采购回滚探针。

### 2. 恢复、执行方法与 backlog

- Operator 改用标准模型 API 按 `stream + state=running` 查询 active capture，不再只查请求日，也不新增网关 op。
- 旧窗口恢复不会完成另一个请求日；单流失败只 defer 该流后续任务，共享冷却 defer 全部剩余任务。
- `WindowWorkflow.begin/resume` 都在数据中心调用前解析方法；`export_for_data` 以 `EXECUTION_METHOD_NOT_IMPLEMENTED` fail-closed，final 零售不能降级掉导出总数核对。
- 增量计划输出 `reason/planning_pending/backlog_remaining/backlog_by_stream/coverage_gaps`；latest closed-day revisit 会从 pending backlog 中移除。
- `status` 使用 durable final list windows 重建连续游标，继续保留 `covered_through` 只是展示最大值的警告。
- `incremental run` 在 blocked 或 planning pending 存在时保持 `complete=false`。

### 3. OpenCLI 非泄漏输出与 legacy 迁移

- 五个公开导出命令统一 allowlist projection，公开对象和 columns 均无 `sourceUrl`。
- 新增真实日历、导出任务 rows 1–100、采购详情源 ID、HTTPS 导出 origin 和 fetch 错误脱敏校验。
- task 查询、签名 URL 解析、fetch、恢复和工件发布继续位于同一 source lease 内。
- 两个 legacy 消费者改用 `export-download` 并核对任务 ID、行数和路径回执；不再直接读取签名 URL 或用 `urlretrieve` 下载。
- 商品探针只接受严格 XLSX/ZIP，CSV fail-closed；工件进入 `.kemai-artifacts/`，报告进入 `.local-audits/`。

### 4. 静态能力与运行时治理

- `manifest.json` 声明 21 条唯一命令、输出列、限制、`creates_export_task` 和本地下载副作用。
- 静态 `capabilities` 不打开浏览器、不申请源 gate，返回 `source_requests=0`、`authentication=null`、`source_acceptance=not_checked`。
- `scripts/kemai_opencli_runtime.py check` 报告 SHA-256 缺失、额外和漂移，以及 Node/OpenCLI 版本和 gate/冷却快照。
- `install` 只同步项目 `.js/.json`，拒绝未知额外文件、同路径和摘要异常；安装前备份，安装后复核并保留 gate 配置。

## 验证结果

### 离线测试

| 项目 | 结果 |
|---|---:|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -p 'test_kemai*.py'` | **201/201 通过**（评审后为最新基线；评审前为 195/195） |
| `node --test tests/pipeline/*.test.mjs` | **41/41 通过** |
| 本地候选网关 `python3 scripts/build_kemai_gateway.py` | 构建成功 |
| OpenCLI draft XML 解析、`git diff --check` | 通过 |

Node 事务顺序测试证明的是 JavaScript mock 中的调用顺序，不是实际 InnoDB MVCC 或锁行为。离线回滚测试也没有冒充真实模型 API 验收。

### 多模型评审修复（2026-11-09，评论区达成一致）

交付后经 Codex 评审发现并修复四个相邻缺陷，全部有「修复前可复现、修复后通过」的定向回归；共识见交接文档评论区 comment-6/comment-7：

1. active capture 查询补齐 `stream/mode/source_key_version`，本地 manifest 丢失时也能经真实 `capture_scope()` 释放服务端 capture。
2. 冷却不再清空 backlog/gaps：final catchup 保留在 `planning_pending`，provisional 仅放入 `deferred_tasks`。
3. coverage gaps 改为逐流 `count` + 每流 ≤1000 条有界明细，超限显式省略并在 `blocked` 标记 `coverage_gap_details_over_budget`，不再全表读取。
4. 冷却时最近已结算日的 `late_arrival_revisit` 也进入 `deferred_tasks`（不计 outage backlog），`incremental run` 传播 `deferred_tasks` 字段。

### 本地运行时安装与零源冒烟

安装前真实 check 为 4 个缺失、7 个变化、0 个额外文件；Node `v24.15.0`、OpenCLI `1.8.8` 满足声明要求。完整离线套件通过后执行显式 install：

- 更新 11 个项目文件，备份 26 个既有运行时文件；备份目录为 `~/.opencli/kemai-source-gate/adapter-backups/adapter-backup-qwyr6af6/`。
- 安装后 0 缺失、0 额外、0 漂移，gate 配置摘要保持不变。
- `opencli kemai --help` 加载成功；`opencli kemai capabilities -f json` 返回 21 条命令、0 源请求，公开 columns 不含 `sourceUrl`。
- 私有证据位于 `.local-audits/kemai-opencli-runtime-*-2026-11-08.json` 和 `.local-audits/kemai-opencli-capabilities-2026-11-08.json`。

这次安装只更新本机 OpenCLI 适配器，不发布 BFF，不检查登录态，也不执行任何源命令。

### 数据中心只读核验

私有输出位于 `.local-audits/kemai-foundation-readonly-2026-11-08/`。所有命令均为 `source_requests=0`、`db_writes=0`。

| 命令 | 结果 |
|---|---|
| `kemai_syncctl.py capabilities` | 成功；accepted streams 仍只有 `retail-sales`、`retail-orders` |
| `status` | 成功；零售销售 durable cursor 为 `2026-11-07`，其他流未据此宣称连续完整 |
| `gaps show` | 成功；当前返回 691 个 pending gap，仅作为缺口快照，不展开业务明细 |
| 零售销售 `history check`（2026-11-01..07） | 该列表核对范围 0 hole；`detail_coverage=not_checked` |
| 零售销售 `incremental plan` | 只生成 2 个计划任务、0 pending backlog，未执行采集或写入 |

## 候选网关与发布边界

本地 `kemaiSyncGateway.js`：MD5 `5a8fbc0383b5aedf95a7a21f14a573e6`，SHA-256 `16d8fce3757f08d8f61386c5bc6ef6f976e0ec431dd3eaf8d4af4706561e149a`。

`.rabetbase/bff.lock.json` 记录的部署锁摘要为 MD5 `dbe81f5a96d0253f08b5b0ce1eedfa3d`、version 10，与本地候选不同。该差异只说明本地候选不是锁文件记录的版本，不能据此推断远端实时源码；本轮未发布。

## 后续真实验收门槛

1. 确认候选与部署版本后，逐采购流运行强制回滚、小窗口闭环、同 capture 零源重放和 key/hash 审计，再单独修改 acceptance。
2. 在真实 InnoDB/标准模型 API 环境验证串行、事务回滚和未知写结果恢复；不能用 mock 结果替代。
3. 在明确放行后验证源登录、导出格式、独立总数和全局限速；Excel 行导入仍需先证明字段与稳定键映射。
4. 真实日增量验收后才启用常驻服务；零售原详情与支付继续暂停。

## 不做

- 不放行采购生产写入，不运行全量补录，不启动旧 supervisor。
- 不删除 hash、行数、键集合或读回校验来制造通过。
- 不公开签名 URL，不保留 legacy URL 下载 fallback，不把列表成功升级为详情完整。
- 不提交、推送或发布本轮改动。

### 三阶段交付（2026-11-09，C→B2→B1）

在 2026-11-08 基线之上完成阶段 C（验收门禁）、B2（XLSX row importer）、B1（采购按单据详情状态机）的离线交付与评审修复。全部为本地代码与离线测试：未执行真实探针、采集或生产写入，未提交或推送代码，`kemai_pipeline/acceptance.json` 内容不变（工作区与 HEAD 无差异）。

**测试基线更新**：以下数字为本节撰写时实测——`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -p 'test_kemai*.py'` **332/332 通过**，`node --test tests/pipeline/*.test.mjs` **41/41 通过**，`git diff --check` 干净。**332/332 为评审后最新基线**；前文「离线测试」表中的 201/201 是 2026-11-08 交付节点的评审后基线，属不同时间点的口径，以本节为准。基线演进：201 → 214（C）→ 239（B2 核心）→ 253（B2 集成）→ 291（B1 纯函数层）→ 303（B1 基础设施）→ 332（B1 集成与评审修复）。

**阶段 C（验收门禁，纯只读）**

- 新增 `scripts/kemai_acceptance_gate.py`（`version-check` / `check` 子命令）、`kemai_pipeline/acceptance_runbook.py`（五检查项求值器）、`scripts/kemai_capture_hash_audit.py`（capture 目录只读重算 hash 审计）。
- `version-check` 输出三态 `unconfirmed`/`consistent`/`divergent`，`remote_state` 恒为 `unknown_not_inferred`，绝不推断远端网关状态；`check` 按 runbook 逐流求值并给出 go/no-go/pending。
- 代码中无任何 `acceptance.json` 写路径；真实探针类检查缺证据记 `pending_user_authorization`，离线类缺证据 fail-closed 记 fail。真实探针执行需用户单独放行后人工运行既有探针脚本，报告路径喂给 `check`。

**阶段 B2（XLSX row importer）**

- 新增 `kemai_pipeline/export_mappings.py`（`HISTORY_EXPORT_FIELDS` 38 项中文表头映射，自 legacy 逐字移植并经程序验证 38/38 一致；`MAPPING_VERSION='history-export-mapping-v1'`）、`xlsx_import.py`（表头名称+顺序严格相等、行数强等、key 完整性，全部校验通过才产出任何行）、`mapping_verification.py`（验证工件驱动 verified，含 canonical 自校验 digest；调用方传参无效）、`scripts/verify_kemai_history_export_mapping.py`。
- workflow `export_for_data` 分支：`export_start`→`awaiting_export`→`export_download`→parse→`append_page`→evidence（`verification_method='xlsx_rows_v1'`）→seal→ingest；行数不等 fail capture（`xlsx_row_count_mismatch`）；resume 经 sha256 复查幂等复用已下载工件。
- fail-closed 边界：无验证工件保持 `EXECUTION_METHOD_NOT_IMPLEMENTED`（消息逐字不变）；retail 两流永不走此路径。
- 外部前置 P3：一次经用户放行的样本导出产生验证工件，在此之前该流维持既有默认行为。

**阶段 B1（采购按单据详情状态机）**

- 纯函数层：`detail_state.py`（`MAX_ATTEMPTS=3`，`classify_entity`/`needs_detail`/`eligible`/`next_state_row`，语义逐字移植 legacy，failed 重试保留旧 counts/hash/completed_at 五列语义）、`normalize_detail.py`（`ordinal:{i}` 兜底键、金额/数量 10000 缩放）。
- 基础设施：`LovrabetAdapter.upsert`（标准模型 API 读改写 + 写后读回比对，未知写结果抛 `UPSERT_OUTCOME_UNKNOWN`，不盲重试）、`source.document_detail`（kind 限 order/receipt/return，id 回显复查）、`datasets.json` 四表 `PENDING_DATASET_REGISTRATION` 占位（任何访问在任何 CLI 调用前 fail-closed）。
- `detail_workflow.py`：plan/begin/resume；writer_lock 全程持有；SourceCooling 断点续跑按 detail_state 跳过已 success 单据；`max_documents=200` 有界；verify_day 三对齐后幂等关 gap——已 resolved 且 `resolution_hash` 一致为 no-op，内容不同抛冲突不覆盖；多余子行不删除，置 partial/`stale_child_rows_require_review`。
- 集成：planner 对 detail 类 gap 产出 `detail_pass` 任务；operator 按独立 capture scope（`purchase-detail:<kind>`）路由；`kemai_syncctl.py` 新增 `detail plan/run/resume` 子命令；audit.py 的 purchase 三流 `detail_coverage` 从状态表读真实值（verified/partial/failed，无数据或未注册保持 `not_checked`）。
- 运行时 fail-closed：外部前置 P1（注册四个 Lovrabet 数据集）完成前，任何 DetailWorkflow 运行在任何源调用之前失败。
- 两点口径说明（来自 Task 6 裁决）：
  - retail 暂停流 planner 仍报 `paused_by_user`（而非早稿所称 `requires_object_queue`）——现状正确，未改动；
  - detail capture 完成时 best-effort `note_capture('committed')` 终止 running 捕获，服务端是否认账待 P1 注册后联调；若服务端不认，残留 running 捕获需人工清理（resume 幂等 no-op 兜底已测）。

**legacy 停用声明**：B1 落地后，legacy `kemai_purchase_detail_backfill.py` 必须停用（双写者风险）；本轮不删除该脚本，仅声明。

**外部前置清单**：P1 = 注册 `purchase_document_detail_state`、`purchase_document_lines`、`purchase_document_branches`、`purchase_document_approvals` 四个 Lovrabet 数据集；P3 = 经用户放行的样本导出产生 B2 映射验证工件；C 真实探针 = 用户单独放行后人工运行既有探针脚本。

**声明**：本轮不执行 git commit/push、不发布 BFF；采购 acceptance 保持关闭（历史 capture 27/28 hash 不一致仍待独立调查，修复另立任务）。
