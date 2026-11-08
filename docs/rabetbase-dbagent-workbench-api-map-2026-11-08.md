# 数据库 Agent 工作台：接口结构与页面能力核对（2026-11-08）

## 范围与结论

- 检查对象：`/Users/tangshuang/data/gitlab/yuntoo/cli-combo/output/workbench-database-agent/workbench-database-agent.html`（静态概念稿），应用 `app-21522951`，数据库连接 `10510`。
- 已用 `rabetbase 2.5.9-beta.1` 对真实服务端做只读查询，并按技能流程提交一次数据库差异刷新和一次 3 表定向增量分析。此文中的接口字段来自实际 CLI 返回，页面行为来自源码检查；没有进行浏览器视觉验收。
- 页面目前没有网络请求；其任务进度、连接延迟、差异表、实体关系、分析报告、跨库连接均由静态 HTML 和定时器模拟。不能把这些演示数字当成真实分析结果。
- 连接管理、差异检测、定向分析、进度查看、取消任务、数据集字段与已有关系展示有现成服务端能力。完整的智能报告、逐条实时 Agent 日志、可信置信度与节省工时等需要另行定义数据来源或产品接口。

## 本次真实状态

| 项目 | 服务端结果 | 与概念稿的区别 |
| --- | --- | --- |
| 连接 | `id=10510`，`MYSQL 8.0`，`fresh-market` | 概念稿只维护内存中的单个连接 |
| 连接测试 | `connected=true`、`result.success=true` | 没有返回延迟毫秒数；页面的 `12ms/18ms` 是模拟值 |
| 物理表 / 已建数据集 | 分析前 34 / 31；本轮完成并复核后 34 / 34 | 页面写死为 64 张表 |
| 差异 | `purchase_document_detail_state`、`sync_live_cursors`、`sync_reconcile_missing`，均为 `NEW_TABLE` | 页面差异列表是 `order_info` 等演示表 |
| 已有 DB_TABLE 数据集关系 | 分析前 33 条，本轮完成后 34 条事实 | 页面写死为 8 组；关系方向与基数应以事实字段为准 |
| 本轮分析 | 定向 3 表，`planId=43e92aa2-9186-4fbb-ab37-ea9b9944062d`，终态 `SUCCESS` | 页面用每 900 ms 加 1% 的定时器模拟 |

`db list/detail.tableCount` 反映已分析覆盖数，在分析过程中可能滞后；物理表和覆盖数应以 `db tables` 或最新 `db diff` 的对应字段呈现。`db diff --all` 只返回有差异的表，所以它的 `physicalTableCount=3` 不能当成全库物理表总数。

## 服务端返回结构（实测，省略无关字段）

CLI 返回统一外层为 `{ "ok": boolean, "command": string, "data": object }`。以下使用 `data.*` 表示业务字段；不记录密码和令牌。

| 命令 / 页面用途 | `data` 核心结构 | 本轮实测 |
| --- | --- | --- |
| `db list` / 连接列表 | `connections[]`、`paging`；连接项含 `id, appCode, dbName, dbType, dbVersion, dbUrl, username, tableCount, latestAnalysisStatus, latestAnalysisTraceId, datasetDependency.activeDatasetCount` | 1 条连接，分析期间状态 `RUNNING` |
| `db detail --id` / 详情 | 上述连接元数据，另有 `dbDesc, dbParam, systemAnalysisResult, gmtCreate, gmtModified, latestAnalysisSuccessTime, incrementalGroupCount`；`password` 被遮蔽 | `systemAnalysisResult` 有 `systemType, primaryDomain, functionalModules[], targetUsers[]`；这不是实体关系报告 |
| `db test --id` / 测连 | `id, connected, result.success` | `true, true`，无延迟字段 |
| `db tables --id` / 表清单 | `dbLinkId, totalCount, physicalTableCount, datasetTableCount, summaryByType, tables[]`；行含 `tableName, tableComment, diffType` | 分析前 `34, 34, 31`；复核后 `34, 34, 34` |
| `db diff-refresh-start/status` / 重新检测 | 启动返回 `traceId`；状态用同一 ID 查询，包含 `status.status, progressRate, currentStep, errorMsg` 及 `isTerminal` | 首次刷新 `f6173d64-44bf-4848-a418-3c473b4cd28b`，终态 `SUCCESS` |
| `db diff --all` / 增量选择 | `dbLinkId, totalCount, physicalTableCount, datasetTableCount, summaryByType, tables[], modifiedFieldDiffs[], toAnalyzeTables[]` | 3 条 `NEW_TABLE`，`toAnalyzeTables` 与上表一致 |
| `db analyze-batch-plan` / 安全分批预览 | `dbLinkId, tableCount, batchCount, batches[]`，每批有 `tableNames[], inputParamsBytes, estimatedDurationSeconds`；`planningEstimate.basis=heuristic` | 3 张表 1 批，启发式估算 180 秒，不能当作服务端 ETA |
| `db analyze-start` / 启动分析 | `planId`，随后只能用原 ID 查询状态；本次 `requestedAnalysis` 为 `mode=TARGETED, tableCount=3, tableNames[]` | 只提交一次，ID 见上 |
| `db analyze-status` / 任务状态 | `planId, dbLinkId, status, requestedAnalysis, parsedJobContext, isTerminal, totalElapsedMs, attemptElapsedMs, links`；`status` 内有 `status, progressRate, currentStep, errorMsg, startTime, endTime, retryCount, maxRetries, traceId` | 运行中先后为 10% `STEP1_EXTRACT`、60% `STEP3_ENTITY`、80% `STEP4_RELATIONSHIP` |
| `dataset list --source DB_TABLE` / 数据集列表 | `datasets[], total`；项含 `id, code, name, source, db, table, pk` | 分析前 31 项，复核后 34 项 |
| `dataset detail --code` / 字段语义 | `code, name, table, fields[], indexes, pk, relations, operations, stats`；字段含 `name, displayName, type, dbType, pk, required, deprecated` | 商品 `products` 数据集有 26 个字段 |
| `dataset relations` / 领域关系 | `appCode, total, relations[]`；每条含 `relationId, from{datasetCode,table,field}, to{datasetCode,table,field,labelField}, relation{cardinality,bizRelationType}` | DB_TABLE→DB_TABLE 分析前 33 条，复核后 34 条 |

分析状态的 `parsedJobContext.knowledgeSnapshotStatus` 曾为 `FAILED`，但主任务 `status.status` 仍为 `RUNNING`，且 `failedTables=[]`。前端不能把这个子字段直接映射成整项失败；以主任务终态、`errorMsg` 和最终差异核对为准。对 `PARTIAL_SUCCESS` 也必须检查缺失表和再次刷新后的剩余差异，不能只看进度 100%。

本轮最终核验：主任务 `SUCCESS`、100%、`STEP6_COMPLETE`，结束于 `2026-11-08T07:38:45Z`；第二次差异刷新 `traceId=520871df-18cc-49b1-a51a-c44903e452a4` 到 `SUCCESS` 后，`db diff --all` 的 `toAnalyzeTables=[]`、`totalCount=0`，`db tables` 为 34 张物理表和 34 张数据集表，`db detail.latestAnalysisStatus=SUCCESS`。这证明这次结构增量分析已闭环；它不代表业务数据采集已更新。

## 页面功能与接入判断

| 页面功能 | 接入判断 | 实施要点 |
| --- | --- | --- |
| 连接列表、详情、测试 | 可接入 | `db list/detail/test`。只展示服务端实际字段；无耗时字段时隐藏毫秒数。不要把遮蔽密码回填成固定字符串。 |
| 新建、编辑、删除连接 | 可接入，需服务端代理 | `db create/update/delete` 已有；凭据只经受控服务端提交。删除前展示关联数据集数并做数量保护。当前 `10510` 有 31 个活跃依赖，不可沿用概念稿“点击即删除”的行为。 |
| 差异检测、搜索、选表增量 | 可接入 | 先 `diff-refresh-start`，轮询同一个 `traceId` 到终态，再 `diff --all` 渲染。仅把可分析的新增/修改表加入选择；按批次规划并串行提交。 |
| 全量/定向分析、进度、取消 | 可接入 | `analyze-start/status/cancel`；持久保存 `planId`，刷新页面后恢复轮询。失败、重试、部分成功要有独立状态。不能通过前端定时器推进进度。 |
| Agent 控制台 | 部分可接入 | 当前可显示 `currentStep, progressRate, retryCount, errorMsg, startTime`；逐条“正在识别某字段”的事件流及全局队列数没有从这些接口取到，需要任务日志/事件接口。 |
| 智能分析报告、复制/导出 | 部分可实现 | 可由 `db detail.systemAnalysisResult`、`dataset list/detail/relations` 和差异结果合成真实 Markdown 摘要；“12 实体/8 关系/96.4%/节省 18.4 小时/3 风险”等没有对应实测字段，必须删除或另设有依据的计算接口。 |
| 领域模型（ER）、悬停、缩放 | 可接入基础关系 | 使用真实数据集及关系事实生成节点边；缩放/悬停可沿用现有前端逻辑。要有业务实体聚合、布局与关系审核策略；平台现成 ER 页面是 `https://app.lovrabet.com/app/app-21522951/data/er?dbId=10510`。 |
| 跨库分析 | 接口已具备，当前不可执行 | `db cross-analyze-start/status/latest` 要至少两个不同连接；此应用当前 `db list` 只有一条真实连接。页面的 `inventory-db/crm-demo` 是假数据。 |
| Demo 数据库、任务中心、导航 | 概念演示 | Demo 目前只是刷新页面。任务中心的全局列表和队列数需另查产品接口，不可从单个 `analyze-status` 推断。 |

## 建议的接入边界与顺序

1. 增加一个服务端 BFF 代理 `rabetbase` 所对应的平台接口，前端只收必要的脱敏视图数据；连接凭据不放到静态 HTML、浏览器持久化或报表中。
2. 先把连接卡、测连、差异列表、分析状态接入真实数据，并持久化 `traceId/planId`；用轮询恢复页面刷新后的状态。
3. 接入数据集字段和关系事实，生成真实的领域模型和可追溯报告；展示“已识别关系数”时说明是数据集关系事实，不等同于聚合后的业务实体关系数。
4. 最后处理跨库、完整任务中心与逐条日志；在有第二个真实连接及相应接口以前关闭入口或显示未满足条件。

**源码特别注意：** `openDetail()` 把可编辑的连接名称、描述、主机、用户名拼入 `innerHTML`，接入真实输入前应改为 DOM 节点与 `textContent`，避免注入。概念稿的 JDBC 复制把数据库名里的 `-` 改为 `_`，会把实际 `fresh-market` 改错；应使用真实名称并正确编码参数。

## 复核命令

```bash
rabetbase db list --format compress
rabetbase db detail --id 10510 --format compress
rabetbase db test --id 10510 --format compress
rabetbase db tables --id 10510 --format compress
rabetbase db diff --id 10510 --all --format compress
rabetbase db analyze-status --id 10510 --plan 43e92aa2-9186-4fbb-ab37-ea9b9944062d --format compress
rabetbase dataset list --source DB_TABLE --format compress
rabetbase dataset relations --from-source DB_TABLE --to-source DB_TABLE --format compress
```

重跑 `analyze-start`、`diff-refresh-start` 会新建任务，复核时不要用它们替代既有 `planId/traceId` 的状态查询。
