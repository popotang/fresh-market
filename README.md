# Fresh Market（科迈云帆 → 数据中心）

科迈 SaaS 数据同步项目：OpenCLI 负责取源，Lovrabet CLI / 标准模型 API 负责数据中心读写，
`kemai_pipeline/` 负责确定性编排，Skill 负责选择受控任务并解释证据。

> **Agent 必读**：[`AGENTS.md`](./AGENTS.md) 定义了本仓库的最高优先级铁律——
> **科迈 SaaS 永远只读**；任何时候、任何人都不得修改或删除 SaaS 平台数据，相关请求一律回绝。

## 当前状态（2026-11-09）

商品对象标准 API 新入口与 CLI 2.0.3 升级说明：[操作交接](docs/kemai-opencli-upgrade-products-2026-11-09.md)。2026-11-10 两个商品小样本已通过真实写入/回读及零操作重放；供货 1 行、条码为空，不代表全量补齐或全部页签验收。

- 公共内核与零售两条流的列表链路已通过真实闭环验证；采购、商品、Excel 首录仍在迁移。
- 交易列表可写流以 `kemai_pipeline/acceptance.json` 为唯一白名单（当前 `retail-sales`、`retail-orders`、`purchase-orders`、`purchase-receipts`、`purchase-returns`）。
- `stored-detail` 可从已存采购父单 payload 零源请求回填子行；不关闭缺口、不推进覆盖水位，缺数组仍记 `partial`。
- 常驻采集未启用；零售原详情/支付按用户要求暂停。
- 旧 MySQL 脚本集中在 `legacy/`，默认禁用，仅用于迁移核对与受控维护。

## 目录地图

| 路径 | 用途 |
|---|---|
| `kemai_pipeline/` | 能力表、planner/runner、源与数据中心端口、限流与项目路径、标准 API 网关（`js/`）|
| `kemai_syncctl.py` | 操作入口：`capabilities/status/gaps/history/incremental/resume/abandon/detail/stored-detail` |
| `kemai_pipeline_service.py` | 前台服务包装（当前仅零售，未启用）|
| `kemai_source_gate.py` | 共享源协调器兼容入口；实现在 `kemai_pipeline/source_gate.py` |
| `kemai_opencli/` | 科迈源适配器（需同步到 `~/.opencli/clis/kemai/`）|
| `legacy/` | 旧 MySQL 写入器、supervisor、补采与审计脚本；不要启动旧采集 |
| `scripts/` | 网关构建与受控验证脚本 |
| `tests/python/` | Python 离线测试；`tests/pipeline/` 为 Node 测试 |
| `db/` | 代码仍引用的 DDL；`db/migrations/` 保存一次性迁移与隔离验收 DDL |
| `.runtime/` | 状态、日志、锁、PID 与一次性运行标记（私有，已忽略）|
| `.local-audits/` | 审计与归档证据（私有，已忽略）|
| `.kemai-artifacts/` | 私有不可变源捕获与恢复工件，不是临时缓存 |
| `.samples/` | 私有 CSV/XLSX 样本（已忽略）|
| `docs/` | 方案、运行报告与交接记录；`PROJECT-BRIEF.md` 为历史快照导航 |
| `docs/legacy/` | 旧版 README、早期设计和运行说明，仅作历史证据 |

## 常用命令

```bash
# 只读
python3 kemai_syncctl.py capabilities
python3 kemai_syncctl.py status
python3 kemai_syncctl.py gaps show
python3 kemai_syncctl.py history check --scope db --stream retail-sales --from 2026-11-01 --to 2026-11-07
python3 kemai_syncctl.py stored-detail plan --stream purchase-orders --day 2026-11-07 --retry-failed
# 零源请求回填已有父单 payload；run/resume 会写自有数据中心，不写科迈、不关闭缺口
# python3 kemai_syncctl.py stored-detail run --stream purchase-orders --day 2026-11-07 --retry-failed --max-documents 200

# 离线测试（不访问源站/生产写入）
python3 -m unittest discover -p 'test_kemai*.py'
node --test tests/pipeline/*.test.mjs

# OpenCLI 运行时漂移检查；安装是独立的显式动作
python3 scripts/kemai_opencli_runtime.py check
# python3 scripts/kemai_opencli_runtime.py install

# 网关源码改动后重建（发布是独立动作）
python3 scripts/build_kemai_gateway.py
```

## 阅读顺序

1. `docs/kemai-trusted-sync-foundation-2026-11-08.md`：本轮事务、恢复、OpenCLI 与运行时治理的交付边界和验证证据。
2. `docs/kemai-sync-next-development-plan-2026-11-08.md`：当前开发计划与放行门槛。
3. `docs/kemai-data-sync-target-architecture-2026-11-08.md`：目标分层与数据契约。
4. `docs/kemai-standard-api-delivery-2026-11-08.md`：已落地范围与真实验证证据。
5. `docs/kemai-pipeline-operations-2026-11-08.md`：操作与回退。
6. `docs/kemai-repository-cleanup-plan-2026-11-08.md` + `docs/kemai-repository-cleanup-result-2026-11-08.md`：目录治理与本次整理结果。

## 硬约束

- **科迈 SaaS 永远只读**：任何时候、任何人都不得修改或删除 SaaS 平台数据；相关请求一律回绝。详见 [`AGENTS.md`](./AGENTS.md)。
- 源请求全局 ≤ 0.5 次/秒，限流后共享冷却 30 分钟；业务写入只走标准模型 API，不用 Custom SQL 写业务表。
- 同一时刻只允许一个正式写入者；已完成检查点不倒退、不重写。
- 不把 `partial` 说成完整、不把列表成功说成详情完整、不把入库时间说成源实时时间。
- 口令、Token、签名下载 URL、原始业务明细不写入 `docs/` 或日志。
