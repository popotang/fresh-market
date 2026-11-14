# 文档索引与时效规则

## 当前权威入口

| 文档 | 用途 |
|---|---|
| [`../README.md`](../README.md) | 项目入口、目录地图、最小命令 |
| [`../PROJECT-BRIEF.md`](../PROJECT-BRIEF.md) | 一页式项目摘要和边界 |
| [`HANDOFF.md`](HANDOFF.md) | 交接顺序、当前放行矩阵、验收清单 |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 当前架构和数据流 |
| [`OPERATIONS.md`](OPERATIONS.md) | 当前操作、恢复和停止条件 |
| [`PLAN.md`](PLAN.md) | 移交后分阶段执行方案和放行门槛 |
| [`kemai-pipeline-operations-2026-11-08.md`](kemai-pipeline-operations-2026-11-08.md) | 标准接口的详细参考；历史日期不代表实时状态 |
| [`kemai-standard-api-delivery-2026-11-08.md`](kemai-standard-api-delivery-2026-11-08.md) | 已实施能力和真实验收摘要 |
| [`kemai-source-gate.md`](kemai-source-gate.md) | 源请求协调器的实现参考 |
| [`kemai-sync-capability-map-2026-11-08.md`](kemai-sync-capability-map-2026-11-08.md) | 能力与命令对照 |
| [`kemai-bi-dashboards-2026-11-10.md`](kemai-bi-dashboards-2026-11-10.md) | 只读 BI 页面和数据集参考 |
| [`kemai-global-business-metrics-v1.md`](kemai-global-business-metrics-v1.md) | 经营指标 v1.0 唯一口径 |
| [`rabetbase-dbagent-workbench-api-map-2026-11-08.md`](rabetbase-dbagent-workbench-api-map-2026-11-08.md) | 数据集/工作台参考 |

## 归档规则

`archive/2026-11-14/` 保存整理前的过程材料和脱敏证据。文件仍可用于追溯，但不能作为当前运行状态、覆盖数量、放行矩阵或实时数据依据。归档目录的说明见 [`archive/2026-11-14/README.md`](archive/2026-11-14/README.md)。

### 已归档的过程类

开发计划、日报、接续说明、临时方案、一次性完成度检查、历史运行报告和目录清理报告均移入 `archive/2026-11-14/process/`。包括旧版架构、旧版增量计划、DeepSeek/Kimi 接续文档、商品行补采、营销域接入和页面改版过程。

### 已归档的证据类

脱敏 JSON 验证、代码基线、Excel 比对和数据关系快照移入 `archive/2026-11-14/evidence/`。源工件、数据库原始明细、回执和凭据仍只在受保护的私有目录中保存。

### 已归档的设计类

已实施的指标语义层 spec/plan 移入 `archive/2026-11-14/design/`；实现和口径以代码、SQL、BFF、测试及 `kemai-global-business-metrics-v1.md` 为准。

## 写新文档的规则

- 变更当前操作、边界或数据口径时，先更新本索引对应的权威文档。
- 日报、一次性验证和开发计划放入归档目录，不在根 `docs/` 增加新的日期版入口。
- 所有数字必须带生成时间、范围、来源和“是否实时”说明；不能把 `partial`、列表覆盖或入库时间说成完整/源实时。
- 不写入口令、Token、Cookie、签名 URL 或原始业务明细。
