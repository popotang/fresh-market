# P1.1/P1.2 采购源契约 + 目录整理执行报告（2026-11-08）

> 依据：[后续开发计划](kemai-sync-next-development-plan-2026-11-08.md)、[目录整理计划](kemai-repository-cleanup-plan-2026-11-08.md)。
> 本轮完成**只读基线、P1.1 源契约、P1.2 代码侧、目录整理阶段 3/4 的一部分**；真实窗口重放与采购放行未执行。

## 一、只读基线

- 三个写锁（`.kemai-writer.lock` / `.kemai-live.lock` / `.kemai-live.service.lock`）全部 **FREE**，无采集进程。
- `kemai_syncctl.py capabilities/status` 可读；限流 `ready`。
- 测试基线：Python **142 → 152**（新增 10），Node **21 → 23**，全部通过。

## 二、P1.1 采购源契约（真实样本已取）

样本保存于 `.kemai-artifacts/purchase-contract/`（私有，不入库）。

| 验证项 | 实测结论 |
|---|---|
| `dateType=0` | 命中行 `gmtCreate` 落在窗口内、`approveTime` 在次日 → **0 = 制单时间** |
| `dateType=1` | 11-08 窗口返回了 11-07 创建、11-08 审核的行 → **1 = 审核时间** |
| `reported_total_rows` | 实际 20 行却返回 **0** → `totalRecord` 不可靠，只能声明单轮唯一键数 |
| 旧单修改 | 11-07 制单的单在 11-08 出现 `gmtModified/approveTime` → 制单日窗口不能保证旧单修订不漏 |
| 查询范围 | `query_scope = {dateType, operDateFrom, operDateTo, keyword, itemPackId, status}` |

落成代码：

1. `kemai_pipeline/source_contracts.json` 升到 schema v2，新增 `purchase_scope`（时间字段、值语义、`total_record_reliable=false`、必须为空的筛选、迟到修改策略）。
2. `kemai_pipeline/source.py` 新增 `validate_purchase_scope()`：强制 `dateType=0`、窗口字段与请求日一致、`keyword/itemPackId/status` 必须为空、拒绝未知非空筛选；`pages()` 对采购流调用。
3. `kemai_pipeline/workflow.py` 证据新增 `source_total_reliable`（采购 false）与 `late_modification_policy`。
4. `normalize.py` 已有采购映射（`document_kind`、`gmtCreate/gmtModified/approveTime`），无需改动。
5. **DB 索引**：线上新增 `idx_purchase_created (tenant_id, document_kind, source_created_at)`，实测 0.11s 完成，`SHOW INDEX` 读回三条；`2026-11-07` 日计数 order 28 / receipt 34 / return 15。回滚：`ALTER TABLE purchase_documents DROP INDEX idx_purchase_created`。

## 三、P1.2 标准网关与放行机制

- 新增 `kemai_pipeline/acceptance.json` + `acceptance.py`：**可写流由数据文件决定**，当前仅 `retail-sales`、`retail-orders`。
- `sync_gateway.mjs` 的 accepted 列表改为 `ACCEPTED_STREAMS`（未注入时回退零售两条），写入与 `capabilities` 都读它。
- `scripts/build_kemai_gateway.py` 从 `acceptance.json` 注入 `__KEMAI_ACCEPTED_STREAMS__`；已重建生成文件并核对注入值。
- 新增测试：
  - Python：采购 scope 接受/拒绝、隐藏筛选、窗口不匹配、契约事实、验收注册表与 `STREAM_ACCEPTANCE_REQUIRED`、`OpenCLISource.pages` 对 `dateType=1` 在源端口即拒绝。
  - Node：采购流未放行时 `capture.begin` 报 `STREAM_ACCEPTANCE_REQUIRED`；注入 accepted 后 `capabilities` 生效（证明放行是数据驱动）。
- 现有网关已具备：`(tenant,document_kind,source_document_id)` 唯一键、同批同回执/异内容拒绝、业务行+原始捕获+回执同事务、窗口提交前 100,000 行/5,000 批预算、`DATABASE_WINDOW_COUNT_MISMATCH` 校验、空日证据。

## 四、目录整理（阶段 3/4 部分）

根目录普通文件 **142 → 87**。

| 动作 | 数量 | 去向 |
|---|---:|---|
| 历史日志 | 20 | `.runtime/logs/` |
| PID 文件 | 3 | `.runtime/pid/` |
| 一次性 `.done` 标记（无引用） | 1 | `.runtime/state/` |
| 一次性审计/核对/完整性 JSON | 21 | `.local-audits/archive/2026-11-08/` |
| CSV/XLSX 样本 | 2 | `.samples/` |
| 未被代码引用的 SQL | 5 | `db/migrations/` |

同时：新增根目录 `README.md`（体系、目录地图、命令、阅读顺序、硬约束）；`.gitignore` 增加 `.runtime/`、`.samples/`、`.rabetbase/*.lock.json`；清单与移动日志存于 `.local-audits/archive/2026-11-08/{root-manifest,move-log}.json`（含 SHA-256、mtime、是否被代码引用）。

**有意暂不移动**（避免破坏运行/导入）：仍被代码引用的 4 个 `schema*.sql`、状态与冷却 JSON、`.done` 迁移标记、锁文件、旧脚本与测试文件。它们按计划在“引用替换后”再迁移。

## 五、未完成（下一阶段门槛）

1. **真实窗口重放**：对每条采购流选一个已结束小日，标准接口事务内试写并强制回滚、读回比对；再真实重放并 0 源预算重放同 capture。
2. **放行采购**：上述证据通过后才把 `purchase-orders/receipts/returns` 写入 `acceptance.json`，重建并**发布**网关（发布是独立动作）。
3. **P1.3 详情队列**：`purchase_document_detail_state` 的对象队列与子表事务仍未接入新内核。
4. **目录整理后续**：legacy 脚本迁 `legacy/`、测试迁 `tests/python/`、旧文档迁 `docs/legacy/`、运行状态文件迁 `.runtime/state/`（需先统一 `project_paths` 兼容读取）。

## 六、回退

- 索引：`ALTER TABLE purchase_documents DROP INDEX idx_purchase_created`。
- 网关：`acceptance.json` 恢复到只含零售两条后执行 `python3 scripts/build_kemai_gateway.py`；若已发布，按发布流程回退上一版本。
- 代码：本轮改动集中在 `source.py`/`workflow.py`/`source_contracts.json`/`acceptance.*`/`build_kemai_gateway.py`/网关源与测试，可逐文件回退；未改任何业务行。
