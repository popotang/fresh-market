# 指标定义落库与 BFF 改造记录（2026-11-14）

## 目标

把指标字典从 BFF 代码常量改为自有数据库表，同时保持页面、查询白名单和口径一致。

## 建表与写入

- DDL：`db/migrations/schema_metric_definitions.sql`
- 物理表：`metric_definitions`
- 同步脚本：`scripts/seed_metric_definitions.py`
- 写入源：`kemai_pipeline/js/business_metrics.mjs` 的 `METRIC_DEFINITIONS`
- 实测：`before=0, after=46, written=46`
- 版本：`retail-metrics-v1.0.0`
- 启用：全部 `enabled=1`

## 增量分析

- 差异刷新 trace：`3974fdfb-dedd-4fc4-aec6-e081ef9ac12f`，SUCCESS。
- 定向分析 plan：`623cb19a-4ef7-4aa8-b3c7-87c548c0b4ae`，SUCCESS / STEP6_COMPLETE。
- 仅分析 `metric_definitions`；既有 `retail_sale_flows` 历史结构漂移未处理。
- 新数据集：`指标定义`
- datasetCode：`94efeaffd86e41cf86eddc4b58dcbbb6`
- 运行态 `lovrabet data filter` 回读 46 行，首行 `gross_sales_amount`，末行 `promotion_link_rate`。

## BFF 改造

`businessMetricGateway`：

- `catalog` 通过 `models.byTable('metric_definitions', {dblinkId: 10510})` 读取当前版本、启用定义。
- 数据库字段映射到页面原有 `code/name/group/unit/formula/source/nullable` 结构。
- 检查 `totalCount === tableData.length`，避免不完整目录被当作完整事实。
- 空目录返回 `METRIC_DEFINITIONS_EMPTY`，不静默退回代码常量。
- `query` 的指标白名单也使用表内定义，避免目录和查询口径分裂。
- SQL 与指标计算逻辑未改。

## 验证

- Node 单元测试：15 项全部通过。
- 新增“catalog 从表读取并映射字段”测试，先失败后通过。
- BFF push 成功，runtime catalog 返回 46 条。
- `retail_performance` query 返回净销售额 `1,140,893.57` 和客单价 `37.74046442589966`。
- `python3 scripts/verify_business_metrics.py` 全部通过，旧看板净销售差额为 0。

## 边界

- 当前只有“从源文件幂等同步到表”的路径，没有业务人员在线编辑页面。
- 新增指标仍需对应 SQL/BFF 计算实现；仅向表插入新行不会自动生成计算逻辑。
- 未对科迈执行任何写操作。
