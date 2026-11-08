# Fresh Market 项目根目录整理计划（交给 DeepSeek 执行）

本文件是[同步体系后续开发计划](kemai-sync-next-development-plan-2026-11-08.md)的配套任务。2026-11-08 盘点时，根目录有 **141 个普通文件**：47 个 `.py`、35 个 `.json`、30 个 `.log`、9 个 `.sql`、8 个 `.md`、4 个 `.pid`、3 个 `.lock`、2 个 `.done`、1 个 `.xlsx`、1 个 `.csv`、1 个无后缀文件。数字只表示当时目录清单，执行前重新盘点。**本文件不执行删除或搬迁**；DeepSeek 应在同步代码迁移期间按下列门槛完成。

## 目录目标

```text
fresh-market/
  PROJECT-BRIEF.md             # 项目入口，保留根目录并标注历史快照
  README.md                    # 新增：当前体系、目录地图、常用入口
  .gitignore
  .lovrabet.json               # CLI 项目绑定，先检查实际依赖，不公开内容
  .rabetbase.json              # 平台项目绑定，先检查实际依赖，不公开内容
  kemai_syncctl.py             # 兼容的用户命令入口，可保留薄包装
  kemai_pipeline_service.py    # 兼容的服务入口，可保留薄包装
  kemai_pipeline/              # 新确定性编排和标准 API 网关
  kemai_opencli/               # 科迈源适配器
  legacy/                     # 旧 MySQL 同步器、旧监督进程及旧补采脚本
  audits/                     # 可复用的只读审计源码
  db/migrations/              # 正式 DDL 和数据迁移脚本
  scripts/                    # 构建、验证、受控维护脚本
  tests/                      # Python 与 Node 测试
  docs/                       # 正式方案、运行报告、归档说明
  fresh-market-web/           # 当前业务前端
  sync-status-page/           # 同步状态页面，确认用途后保留/合并
  .kemai-artifacts/           # 私有不可变捕获工件；不得清理为临时缓存
  .local-audits/              # 私有审计证据；按保留策略归档
  .runtime/                   # 状态、日志、冷却、锁、PID 等运行产物
  .samples/                   # 私有 CSV/XLSX/JSON 样本与导出文件
```

目录数量以职责为准，不必机械添加空目录。`README-commerce.md`、`README-sync.md`、`README-sync-status.md`、`data-model-analysis.md`、`db-analysis-run.md`、`opencli-sync-design.md`、`kemai_product_tabs_strategy.md` 移入 `docs/legacy/` 或 `docs/architecture/`，保留原始日期/历史口径，不让旧 02:00 日批说明盖过新操作指引。`PROJECT-BRIEF.md` 可留根目录作导航，正文不应继续把旧进程快照表述为当前运行状态。

## 文件分类与处理

| 当前根目录文件 | 目标/动作 | 判定依据与注意事项 |
|---|---|---|
| `kemai_syncctl.py`、`kemai_pipeline_service.py` | 保留根入口或改薄包装；实现代码在 `kemai_pipeline/` | 外部命令和文档已引用，不能静默改路径；当前服务白名单只接受零售 |
| `kemai_source_gate.py`、`kemai_rate_limit.py` | 移入 `kemai_pipeline/` 的源控制模块，或保留兼容包装 | OpenCLI JS、旧脚本和新脚本均可能引用；共享锁/冷却文件路径不能因移动而分裂 |
| `kemai_sync.py`、`kemai_commerce_sync.py`、`kemai_live_sync.py`、`kemai_sections.py` 等旧写入器 | `legacy/`；先保留根目录薄包装或完成全部引用替换 | 仍用于迁移/回退和历史审计；`kemai_sync.py` 被多处 `from ... import`，直接移动会破坏导入 |
| `kemai_launch.py`、`kemai_followup_launch.py`、`kemai_catchup_launch.py`、`kemai_daily_launch.py`、`kemai_priority_launch.py`、`kemai_collection_launch.py`、`kemai_live_service.py`、`kemai_rate_guard.py` | `legacy/launchers/`，并加“旧体系，默认禁用”说明 | 需先查进程和定时触发；不要让整理目录重新启动旧 supervisor |
| 商品/采购/零售的旧补采与派生脚本，例如 `kemai_product_incremental.py`、`kemai_changed_tabs.py`、`kemai_purchase_detail_backfill.py`、`kemai_retail_lines_from_flows.py` | `legacy/jobs/`，迁移成功前保留可运行性 | 新体系尚未完全替代；移动后校验资源路径、CLI 参数与测试 |
| `kemai_integrity_report.py`、`kemai_product_coverage_audit.py`、`kemai_purchase_daily_audit.py`、`kemai_retail_daily_audit.py`、`kemai_commerce_reconcile.py` | `audits/` 或 `legacy/audits/` | 先确认只读与是否直连 MySQL；新审计逐条改用 Lovrabet 标准读取，不混称已迁移 |
| `schema*.sql`、`migrate_history_line_identity.sql` | `db/migrations/`，按序号/用途命名并写 README | 保留已执行记录，不重复执行；`schema_pipeline_probe.sql` 是隔离验收表，单列 `db/probe/` 也可 |
| `test_kemai_*.py` | `tests/python/`，或先保留根目录直到发现命令更新 | 迁移后 `python3 -m unittest discover` 必须仍能发现全部测试；导入和 fixture 路径同步修改 |
| `kemai_*.log`、`*.pid`、`*.lock`、`kemai_*_status.json`、`kemai_manual_checkpoint.json`、`kemai_history_keys_v*.done` | `.runtime/` 分类为 logs/state/locks；**先核对活动进程与代码引用** | 状态、冷却、检查点、锁、PID 可能有运行意义；`*.done` 不是可凭文件名删除的临时文件 |
| `kemai_*audit*.json`、`kemai_*reconciliation*.json`、`kemai_integrity*.json` 等一次性报告 | `.local-audits/archive/YYYY-MM-DD/`，正式结论仍在 `docs/` | 保留可复核证据、标注时间和生成命令；不能将审计快照写成实时状态 |
| `kemai_items_page1_2026-11-06.csv`、`kemai_item_archives_2026-11-07.xlsx`、商品/导出 probe JSON | `.samples/` 或 `.local-audits/samples/` | 可能含商品/交易数据；私有保存并记录 SHA-256，确认备份和引用后再移出根目录 |
| `draft_d459b63f_folder/` | 核对来源后归档到 `docs/drafts/` 或删除 | 内有 `draft.xml` 和决策 JSON，不能仅凭 `draft_` 前缀认定无用 |
| `__pycache__/`、`.pyc`、无活跃进程且已有持久状态的陈旧 `.pid`、构建可再生缓存 | 验证后删除 | `fresh-market-web/node_modules/` 可按锁文件重装时才清理；不属于根目录源码整理的必要步骤 |

`.lovrabet.json`、`.rabetbase.json` 可能是项目绑定配置。先确认是否含密钥、被 CLI 如何读取，再决定根目录保留或移入私有配置目录；不在日志/文档打印内容。现有 `.gitignore` 仅忽略 `.local-audits/`、`.kemai-artifacts/`、`__pycache__/`、字节码和 `.kemai*.lock`；新增 `.runtime/`、`.samples/` 等私有目录时同步更新忽略规则，并检查旧根目录样本是否已被版本控制。不要执行会删除未备份工件的 `git clean -fdx`。

## 执行步骤

1. **生成清单**：输出相对路径、大小、SHA-256、mtime、类别、是否被代码/文档引用、是否为活动运行状态的机器可读 manifest；敏感内容只记文件名/摘要，不打印值。记录根目录计数基线。
2. **确认运行状态**：查实际进程、系统计划任务、活动 capture、源冷却、锁持有者及正在写的日志。活动状态文件和锁先不搬；先为新旧代码引入统一 `project_paths`/配置入口，并做好旧位置兼容读取或一次性原子迁移。冷却状态不能因新路径而被绕过。
3. **先搬纯文档与可重建测试**：更新所有相对链接和测试发现路径，运行离线测试；不要在这一步动源数据或 DB。
4. **再搬 SQL 和审计源码**：保持执行顺序、迁移记录和只读功能。修正 `__file__` 派生根目录、工作目录、`PYTHONPATH`、`subprocess` 命令、Rabetbase 文件路径和文档示例。
5. **最后搬 legacy 脚本**：以兼容入口或统一命令包装过渡；全面搜索 `import`、`subprocess`、README、skill、OpenCLI 注册目录、定时器引用。每个旧入口要么能正常运行，要么明确退役并给替代命令；不保留会悄悄运行旧 MySQL 写入的误导入口。
6. **归档再清理运行产物**：将完成的日志、审计、样本移入私有目录；对可再生缓存和确证无效的 PID 删除。状态、冷却、不可变捕获、导出源文件、回执证据按保留策略处理，不因“根目录整洁”而删。
7. **最终验收**：根目录仅保留入口、绑定配置、主 README/BRIEF 与子目录；全量离线测试通过，所有 docs 相对链接可达，CLI 项目绑定正常，当前/旧脚本均能按预期定位文件。只读 `status`、`capabilities`、`history check --scope db` 通过；活动采集/限流恢复行为用测试验证。给出“移动/删除/保留”清单和前后文件数，报告未处理项。

## 必须避免的迁移事故

- **不要**把 `.kemai-artifacts/`、`.local-audits/`、Excel/CSV 样本简单判为临时文件。前两者包含恢复和审计证据；样本可能承载尚未入库的源数据。
- **不要**先移动 `kemai_sync.py` 再修 import；Python 同名包/模块解析会变化。`kemai_pipeline/` 是已选新包名。
- **不要**把锁或冷却状态改到新路径后让旧实例继续占用旧路径；这会造成双写或绕过限流。活动实例必须先安全退出或统一兼容。
- **不要**因为旧状态 JSON 看似过期就删除检查点；先证明 DB 控制表/原始状态与窗口证据已经承接。
- **不要**为了整理目录顺手启动全量、增量、supervisor 或改生产数据。目录整理的验收以离线/只读验证为主；任何必要迁移写入要单列步骤与回退证据。
