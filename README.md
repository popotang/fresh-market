# Fresh Market（科迈云帆 → Lovrabet）

Fresh Market 将科迈云帆的只读数据同步到 Lovrabet 自有数据中心，并在副本上提供同步状态、经营指标和看板。科迈 SaaS 永远只读；任何修改、删除、审批、导入或上传请求都必须拒绝，详见 [`AGENTS.md`](AGENTS.md)。

## 移交入口

- [`docs/HANDOFF.md`](docs/HANDOFF.md)：接手顺序、当前放行矩阵、验收清单。
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：当前分层、数据流和一致性边界。
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md)：只读核查、有界执行、恢复和停止条件。
- [`docs/PLAN.md`](docs/PLAN.md)：移交后分阶段执行方案和放行门槛。
- [`docs/DOCUMENTATION-INDEX.md`](docs/DOCUMENTATION-INDEX.md)：文档时效和归档索引。
- [`PROJECT-BRIEF.md`](PROJECT-BRIEF.md)：一页式项目摘要。

## 目录地图

| 路径 | 用途 |
|---|---|
| `kemai_pipeline/` | 捕获、摘要、限流、锁、规划、恢复和 Lovrabet 适配器 |
| `kemai_syncctl.py` | `capabilities/status/gaps/history/incremental/resume/abandon` 操作入口 |
| `kemai_opencli/` | 科迈只读 OpenCLI 适配器源码 |
| `.rabetbase/` | 自有数据中心 BFF、SQL、页面和数据集配置 |
| `fresh-market-web/` | Lovrabet React 微前端 |
| `sync-status-page/` | 同步状态页的独立前端 |
| `db/` | DDL 和一次性迁移；业务行不通过 SQL 写入 |
| `legacy/` | 旧实现和迁移核对脚本，默认禁用 |
| `tests/` | Python 离线测试和 Node 网关测试 |
| `docs/` | 当前文档、参考资料和历史归档 |
| `.runtime/` | 本机状态、日志、锁和 PID（私有） |
| `.kemai-artifacts/` | 不可变源捕获和恢复工件（私有） |
| `.local-audits/` | 脱敏审计和验收证据（私有） |

## 五分钟检查

```bash
python3 kemai_syncctl.py capabilities
python3 kemai_syncctl.py status
python3 kemai_syncctl.py gaps show
python3 scripts/kemai_opencli_runtime.py check
```

以上命令用于查看能力、数据中心状态、缺口和运行时漂移；运行前不请求科迈源站，也不会写业务数据。

## 离线验证

```bash
python3 -m unittest discover -p 'test_kemai*.py'
node --test tests/pipeline/*.test.mjs
python3 scripts/build_kemai_gateway.py
python3 scripts/build_business_metric_gateway.py
python3 scripts/verify_business_metrics.py
```

需要访问源站或写入自有数据中心时，必须按 [`docs/OPERATIONS.md`](docs/OPERATIONS.md) 先做范围、预算、锁和回读检查。不要启动旧 `legacy/` supervisor，也不要把历史日期文档当成当前状态。
