# Fresh Market（科迈云帆 → 数据中心）

科迈 SaaS 数据同步项目：OpenCLI 负责取源，Lovrabet CLI / 标准模型 API 负责数据中心读写，
`kemai_pipeline/` 负责确定性编排，Skill 负责选择受控任务并解释证据。

## 当前状态（2026-11-08）

- 公共内核与零售两条流的列表链路已通过真实闭环验证；采购、商品、Excel 首录仍在迁移。
- 可写流以 `kemai_pipeline/acceptance.json` 为唯一白名单（当前 `retail-sales`、`retail-orders`）。
- 常驻采集未启用；零售原详情/支付按用户要求暂停。
- 根目录旧脚本是迁移期保留的 legacy 入口，不是当前推荐路径。

## 目录地图

| 路径 | 用途 |
|---|---|
| `kemai_pipeline/` | 能力表、planner/runner、源与数据中心端口、标准 API 网关（`js/`）|
| `kemai_syncctl.py` | 操作入口：`capabilities/status/gaps/history/incremental/resume/abandon` |
| `kemai_pipeline_service.py` | 前台服务包装（当前仅零售，未启用）|
| `kemai_opencli/` | 科迈源适配器（需同步到 `~/.opencli/clis/kemai/`）|
| `scripts/` | 网关构建与受控验证脚本 |
| `tests/pipeline/` | Node 测试；根目录 `test_kemai*.py` 为 Python 测试 |
| `db/migrations/` | 未被代码引用的 DDL（`schema.sql`、`schema_pipeline*.sql` 等）|
| `.runtime/` | 历史日志、PID、一次性运行标记（私有，已忽略）|
| `.local-audits/` | 审计与归档证据（私有，已忽略）|
| `.samples/` | 私有 CSV/XLSX 样本（已忽略）|
| `docs/` | 方案、运行报告与交接记录；`PROJECT-BRIEF.md` 为历史快照导航 |

## 常用命令

```bash
# 只读
python3 kemai_syncctl.py capabilities
python3 kemai_syncctl.py status
python3 kemai_syncctl.py gaps show
python3 kemai_syncctl.py history check --scope db --stream retail-sales --from 2026-11-01 --to 2026-11-07

# 离线测试（不访问源站/生产写入）
python3 -m unittest discover -p 'test_kemai*.py'
node --test tests/pipeline/*.test.mjs

# 网关源码改动后重建（发布是独立动作）
python3 scripts/build_kemai_gateway.py
```

## 阅读顺序

1. `docs/kemai-sync-next-development-plan-2026-11-08.md`：当前开发计划与放行门槛。
2. `docs/kemai-data-sync-target-architecture-2026-11-08.md`：目标分层与数据契约。
3. `docs/kemai-standard-api-delivery-2026-11-08.md`：已落地范围与真实验证证据。
4. `docs/kemai-pipeline-operations-2026-11-08.md`：操作与回退。
5. `docs/kemai-repository-cleanup-plan-2026-11-08.md` + 本目录整理报告：目录治理。

## 硬约束

- 源请求全局 ≤ 0.5 次/秒，限流后共享冷却 30 分钟；业务写入只走标准模型 API，不用 Custom SQL 写业务表。
- 同一时刻只允许一个正式写入者；已完成检查点不倒退、不重写。
- 不把 `partial` 说成完整、不把列表成功说成详情完整、不把入库时间说成源实时时间。
- 口令、Token、签名下载 URL、原始业务明细不写入 `docs/` 或日志。
