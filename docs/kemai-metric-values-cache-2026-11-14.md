# 指标值缓存实现记录（2026-11-14）

## 目标与边界

- 指标定义存于 `metric_definitions`；指标计算值与查询行存于 `metric_values`。
- 缓存有效期 10 分钟，过期后重算并追加新世代。
- 该表是**可重算缓存**，不是财务快照或审计台账；旧世代只用于诊断，不参与过期后的业务回答。
- 只写自有 `fresh-market`，不写科迈。

## 表结构

DDL：`db/migrations/schema_metric_values.sql`

关键字段：

| 字段 | 含义 |
|---|---|
| `cache_key` | 版本 + 租户 + 指标域 + 维度 + 门店 + 日期 + 指标集合的确定性哈希 |
| `generation` | 计算世代，epoch 毫秒 |
| `metric_code` | 指标编码 |
| `dimension_key` | 汇总行 `__TOTAL__`，或日/门店/品类/商品等维度键 |
| `value_decimal` | 数值；NULL 表示不可计算 |
| `value_text` | 文本值 |
| `attributes_json` | 行级附加信息，如库存风险、快照时间 |
| `meta_json` | 覆盖率、警告、数据时间等 |
| `calculated_at` / `expires_at` | 计算与过期时间，均为 epoch 毫秒 |

唯一键：`(cache_key, generation, metric_code, dimension_key)`。

## 读写策略

```text
query
→ 计算 cache_key
→ 只读取最新 generation
→ 最新世代完整且未过期：直接返回，cache.hit=true
→ 缺失/过期：执行 SQL 与 BFF 计算
→ 以当前时间作为新 generation，分批 INSERT
→ 返回 cache.hit=false、calculatedAt、expiresAt、storedRows
```

- `forceRefresh=true` 跳过读取、强制重算并追加新世代。
- 不再逐行 DELETE，避免超过 BFF 500 次调用上限，也避免运行态 DELETE 权限限制。
- 分批写入，每批 200 行。
- 读取只取最新世代，按 `generation DESC, id ASC` 分页，单页 200 行，支持超过 200 行的结果。
- 写入失败不伪装为完整缓存：下次读取若汇总指标不完整会重新计算。

## 兼容与修正

- 初版使用 DATETIME 字符串，BFF/驱动时区差异导致误判过期；现统一为 epoch 毫秒。
- 数据集 `calculated_at`、`expires_at` 的 `doType` 已修正为 `NUMBER`，避免 SDK 按日期处理。
- `generation` 纳入唯一键后，重算不再删除旧行，也不要求 DELETE 权限。
- `metricCacheDelete` 仅作早期候选资源保留，**当前 BFF 不调用它**。

迁移文件：

- `db/migrations/migrate_metric_values_epoch.sql`
- `db/migrations/migrate_metric_values_generation.sql`

## 增量分析

| 任务 | ID | 结果 |
|---|---|---|
| 初次新建表分析 | `1da608d9-a237-4db1-98f9-f246dc6cad73` | SUCCESS |
| epoch 字段变更分析 | `d6fb87fd-2ee2-480d-bd1f-6fefdbe2bd7f` | SUCCESS |
| generation 字段分析 | `dd0ffa9a-7599-4f2d-a511-72c2f523458b` | SUCCESS |

数据集：`指标值缓存`，datasetCode `3d4393d0ed434bd49666df8211e4f8b7`。

## 验证

- Node 单元测试：20 项全部通过，覆盖命中、过期、作用域隔离、跨页读取、epoch 时间和世代追加。
- 实际 BFF 强制重算：`storedRows = 1958`，`cache.hit = false`。
- 随后同参数调用：`cache.hit = true`，不再执行组 SQL。
- 过期时间改为过去后：重新计算并追加新世代，再次调用命中。
- `python3 scripts/verify_business_metrics.py` 全部通过，旧看板净销售额差额为 0。

## 后续维护

- 旧世代不参与正常读取；如需控制表体积，应另做经过评审的归档/清理任务，不在查询路径中删除。
- 新增指标仍需对应 SQL/BFF 计算实现，缓存不会自动产生新指标。
