# 根目录整理结果（2026-11-08）

## 结果

根目录普通文件收敛为 6 个：`.gitignore`、`README.md`、`PROJECT-BRIEF.md`、`kemai_syncctl.py`、`kemai_pipeline_service.py`、`kemai_source_gate.py`。本轮不覆盖式移动 80 项（含 source gate 实现迁入包内），删除 2 项确证临时产物。未执行 `git reset/clean`，未启动采集或 supervisor，未写业务数据，未发布网关。

| 类别 | 处理 |
|---|---|
| 旧 MySQL 写入器、supervisor、补采和审计脚本 | 30 个脚本移入 `legacy/`，默认禁用；以 `python3 -m legacy.<module>` 做受控维护 |
| Python 测试 | 14 个测试移入 `tests/python/`，根目录 unittest discovery 保持可用 |
| 共享限流与源协调 | 实现移入 `kemai_pipeline/rate_limit.py`、`source_gate.py`；根目录保留 source-gate 兼容入口 |
| 仍被代码读取的 DDL | 4 份移入 `db/`；一次性迁移仍在 `db/migrations/` |
| 状态、冷却、迁移标记 | 移入 `.runtime/state/`，未删除检查点或冷却证据 |
| 日志与采集日志目录 | 移入 `.runtime/logs/`，历史证据导入已同步支持新位置 |
| 锁 | 移入 `.runtime/locks/`，新旧写入者继续共用同一位置 |
| 一次性审计与核验报告 | 移入 `.local-audits/archive/2026-11-08/`，未作为垃圾删除 |
| OpenCLI 命令草稿 | 移入 `docs/drafts/kemai-opencli-commands/` |
| 陈旧全量 PID | 确认无活动科迈进程后删除 |
| `__pycache__` | 删除，可再生 |

移动前后文件名、类别与 SHA-256 见 `.local-audits/archive/2026-11-08/root-cleanup-final-manifest.json`。原根目录运行产物移入已忽略目录后，Git 会显示原路径删除；提交后这些私有运行产物不再按根目录文件跟踪。

## 兼容性修复

- `kemai_pipeline/project_paths.py` 统一项目、状态、日志、锁和 PID 路径。
- legacy 脚本的项目根与脚本目录分开，避免移动后误读写 `legacy/.runtime`。
- `legacy_evidence.py`、旧零售审计与历史回填读取归档后的日志和审计 JSON。
- OpenCLI source-gate 配置已刷新到包内脚本及 `.runtime/state/kemai_rate_limit_status.json`；原状态和日志均存在，冷却时间未重置。
- 测试包保留旧模块名导入兼容；跨测试引用改为包路径。

## 验证

- Python：**154 个测试全部通过**。
- Node：**25 个测试全部通过**。
- `python3 kemai_syncctl.py capabilities`：`ok=true`、`db_writes=0`、`source_requests=0`。
- `python3 kemai_syncctl.py status`：`ok=true`，读取 21 条流状态与 9 条近期窗口证据。
- `history check --scope db --stream retail-sales --from 2026-11-01 --to 2026-11-07`：`ok=true`、缺口 0、零源请求、零业务写入。

## 边界

`legacy/` 只是目录归位，不表示旧 MySQL 写入链路已迁移或可以恢复运行。采购正式写入仍未放行，零售原详情/支付继续暂停。历史文档中的根目录旧命令保留当时口径；当前入口以根目录 `README.md` 为准。
