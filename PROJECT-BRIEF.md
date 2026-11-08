# Fresh Market 项目简报（2026-11-07 审计版）

> 运行状态后续更新：2026-11-08 01:10 CST 日常增量已按用户要求暂停；DeepSeek 接手执行以 [新交接指引](docs/kemai-deepseek-collection-handoff-2026-11-08.md) 为准。本文中的进程和数据数量是 11 月 7 日历史快照。

> 本文是对「科迈云帆 → Fresh Market」项目的完整认知快照，替代口头交接。
> 结论均带证据；凡未落盘可复核的，明确标「未验证」。
> 审计方式：只读代码/文档/状态文件 + `ps` + 只读 JSON 解析，未启动/停止进程，未访问科迈源站。
> 面向实施决策的结论与优先级见 [docs/kemai-sync-conclusion-2026-11-07.md](docs/kemai-sync-conclusion-2026-11-07.md)；本文保留较完整的资产与历史快照明细。

---

## 一、一句话定位

把科迈云帆 SaaS（`yunfan.kemai.com.cn`，租户 `205149`）的商品档案、采购单据、零售订单与销售流水，
以 **单采集进程目标 ≤0.5 源请求/秒** 的只读采集、**幂等 upsert + 日期检查点** 的方式同步进 MySQL `fresh-market`，
再经 Lovrabet 平台数据集暴露为给客户的只读「数据同步状态」页。

难点不在抓取，而在三件事：**限流识别（HTTP 200 也可能是限流）**、**完成 ≠ 连续的覆盖口径**、**明细/支付的补采债**。

---

## 二、系统架构

```
科迈 SaaS (yunfan.kemai.com.cn, tenant 205149)
  │
  ├─ ① OpenCLI 适配器  ~/.opencli/clis/kemai/*.js  （13 命令，浏览器+COOKIE 会话）
  │      列表 / 详情 / 异步 Excel 导出
  ├─ ② opencli browser <session> eval（不装适配器，直接读页面 Vue 内部接口）
  │      商品清单、商品主档、5 类主数据、14 类商品页签
  └─ ③ 异步导出任务 Excel
         历史已审核收货(按月)、零售订单 OD、零售流水、商品档案 → 仅用于总数核对/字段核对
  ▼
Python 编排层
  ├─ 源访问门面   kemai_sync.py / kemai_commerce_sync.py / kemai_sections.py
  ├─ 请求协调     kemai_source_gate.py + 适配器 source-gate.js（跨进程锁与 2s 开始间隔）
  ├─ 限流熔断     kemai_rate_limit.py（文件级全局冷却 1800s）
  ├─ 守护编排     kemai_launch → kemai_followup_launch → kemai_catchup_launch → kemai_daily_launch
  │               + kemai_priority_launch（单流优先续跑）
  ▼
MySQL fresh-market（31 张表）
  ├─ schema_v2.sql           17 表  商品域 + 采集控制
  ├─ schema_commerce.sql     11 表  采购域 + 零售域 + coverage_gaps + detail_state
  ├─ schema_sync_observability.sql 2 表 sync_stream_events / sync_stream_status
  └─ schema.sql               1 表  product_items（遗留 100 行平面导入）
  ▼
Lovrabet 平台数据集（app-21522951）
  ▼
只读自定义状态页 sync-status-page/（页面 1550 / 菜单 79821）
```

上图的 31 张表是本简报审计时的库表快照。后续本地 DDL 新增采购单据详情状态和旧日期对账待核两张表，现定义 33 张；尚未验证生产数据库已执行这两张表的迁移。

密码从不落盘：交互输入，或由 launcher 经匿名管道 `KEMAI_PASSWORD_FD` 传给子进程。

---

## 三、三层资产清单

### 3.1 Skill（只有 2 个，无第三份）

已核实 `~/.agents/skills`、`~/.claude/skills`、项目 `.codex/skills`（不存在）均无 kemai skill。

| Skill | 文件 | 触发场景 | 职责 |
|---|---|---|---|
| `kemai-opencli` | `SKILL.md`(16) / `references/commands.md`(23) / `references/rate-limit.md`(7) / `agents/openai.yaml`(4) | 挑选或修复源采集命令 | 源命令矩阵、限流处理、list/detail/Excel 覆盖差异 |
| `kemai-sync` | `SKILL.md`(14) / `references/runbook.md`(40) / `agents/openai.yaml`(4) | 规划/审计/运行同步 | 全量、增量、断点、缺口、恢复、客户可见状态 |

两者单向互引：opencli → `$kemai-sync`（断点/去重/新鲜度），sync → `$kemai-opencli`（源适配器行为）。

### 3.2 OpenCLI 命令（15 个文件 = 13 条命令 + utils.js + source-gate.js）

适配器源码双份：`~/.opencli/clis/kemai/`（**运行时生效**）与项目内 `kemai_opencli/`（源码副本）。2026-11-07 13:52 已将节流修复和此前仅在运行时存在的零售详情修复合并，相关文件当前一致。

| 命令 | 用途 | 关键参数 |
|---|---|---|
| `purchase-orders` | 采购订单列表 | from/to/page/rows/date-type |
| `purchase-receipts` | 采购收货单列表 | 同上 |
| `purchase-returns` | 采购退货单列表 | 同上 |
| `purchase-detail` | 采购单头+商品行+机构+审核 | `--kind order\|receipt\|return --id` |
| `purchase-receive-history` | 历史已审核收货商品行 | from/to/page/rows |
| `retail-orders` | 零售小票单头（`orderDetails`/`payFlows` 恒为 null） | from/to/page/rows |
| `retail-sales` | 逐商品销售流水（`sheetId`=订单 ID，`id`=行 ID） | from/to/page/rows |
| `retail-order-detail` | 零售单头+明细+支付（最慢最脆弱） | `--id --sale-time 'YYYY-MM-DD HH:mm:ss'` |
| `purchase-receive-history-export-start` | 建历史收货 Excel 导出任务 | from/to |
| `retail-orders-export-start` | 建零售订单 OD 导出任务（26 列单头） | from/to |
| `retail-sales-export-start` | 建零售流水导出任务（独立总数核对） | from/to |
| `item-archives-export-start` | 建/复用商品档案全量导出 | `--snapshot YYYY-MM-DD [--expected-total N]` |
| `export-task-list` | 查导出中心任务（轮询完成/失败） | `--rows`（默认 20） |

契约要点（opencli 1.8.8 源码实证）：
- 成功：stdout 直接打印适配器返回数组；**未写 `-f json` 且非 TTY 会自动降级 YAML**。
- 失败：stdout 空，错误信封走 **stderr YAML**（`ok:false, error.code, exitCode`）。
- `RATE_LIMIT` / `AUTH_REQUIRED` 等由 `CliError` 抛出时 exitCode=1；缺必填参数才是 2。
- 命令默认超时 **60s**，适配器无法用 `--timeout` 调整，只能 `OPENCLI_BROWSER_COMMAND_TIMEOUT` 环境变量。
- 13 条适配器命令已接入共享源操作租约，至少 2 秒开始间隔；裸调 `opencli kemai` 会自动受控。手工 `opencli browser` 需经 `kemai_source_gate.py run -- ...`。2026-11-07 15:08 已将旧 supervisor 安全切换到新进程；真实日增量结果待下一完整源日核对。

### 3.3 Python 脚本矩阵（按职责）

| 层 | 脚本 |
|---|---|
| 引擎核心 | `kemai_sync.py`(414) 商品全量底座 / `kemai_commerce_sync.py`(899) 六类交易流 / `kemai_sections.py`(282) 14 类页签 |
| 启动守护 | `kemai_launch.py` / `kemai_followup_launch.py` / `kemai_catchup_launch.py` / `kemai_daily_launch.py` / `kemai_priority_launch.py` / `kemai_rate_guard.py`（未接入） |
| 限流与协调 | `kemai_rate_limit.py` / `kemai_source_gate.py` / `kemai_opencli/source-gate.js` |
| 商品链路 | `kemai_masters.py` / `kemai_product_incremental.py` / `kemai_changed_refresh.py` / `kemai_changed_tabs.py` / `kemai_derive_suppliers.py` |
| 零售补采 | `kemai_retail_detail_backfill.py`(336，**暂停**) / `kemai_retail_lines_from_flows.py`(144，零源请求) |
| 观测审计 | `kemai_sync_log.py` / `kemai_sync_log_backfill.py` / `kemai_integrity_report.py` / `kemai_{purchase,retail}_daily_audit.py` / `kemai_product_coverage_audit.py` / `kemai_finalize_checkpoint.py` |
| 测试 | `test_kemai_retail_detail_backfill.py`（4 条）/ `test_kemai_source_gate.py`（5 条），均为离线用例 |

---

## 四、数据边界（截至 2026-11-07）

### 4.1 已落库数量（`kemai_integrity_final_2026-11-07.json`，04:11 生成）

| 表 | 行数 |
|---|---|
| `retail_orders`（零售单头） | **127,173** |
| `retail_sale_flows`（逐商品流水） | **453,971** |
| `retail_order_lines`（订单商品行） | **4** ← 该报告生成时；映射在此之后才跑，见 §六 |
| `retail_payments`（支付） | **2**（仅采样） |
| `products` / `product_profile` | 18,386 / 18,386 |
| `product_child_records` | 14,624 |
| `purchase_documents`（三分合计） | **17,089** = 订单 11,361 + 收货 3,557 + 退货 2,171 |
| `purchase_document_lines` | 23,819 |
| `purchase_receipt_history_lines` | **125,754** |
| `source_capture`（原始 JSON） | **774,976** |

自洽校验：`source_capture` 774,976 = 各流捕获数之和，精确相等；零售流水 453,971 = 26 天导出总数 431,019 + 2026-10-11 单轮 22,952，精确相等。
质量：10 组稳定键重复全 0、11 组孤儿全 0。

### 4.2 覆盖真相：完成天数 ≠ 有数据天数 ≠ 粒度完整

这是本项目**最容易被误读**的地方，对外表述须同时说明核对天数、实际有数据天数和覆盖粒度。

| 源 | 核对完成天数 | **实际有数据天数** | 空日 | 覆盖粒度 |
|---|---|---|---|---|
| 零售订单头 | 98 | **27**（2026-10-11..11-06） | 71 | `headers_only` |
| 销售流水 | 98 | **27** | 71 | `full_list` |
| 采购订单头 | 675 | **310** | 365 | `headers_only` |
| 采购收货单 | 91 | **71**（2026-08-08 起） | 20 | `headers_only` |
| 采购退货单 | 310 | **279** | 31 | `headers_only` |
| 历史已审核收货行 | 67 窗口 | 全窗口 | 11 月 | `approved_rows_only` |
| 商品主档/详情 | 1 次快照 | 18,386 | — | `full_catalog` |
| 商品 14 类页签 | 仅变化商品 | 17 个商品 | — | `changed_only` |

机制原因：`sync_stream_status.covered_through` 用 `GREATEST(covered_through, %s)` 单调取最大（`kemai_sync_log.py:71`）——
它只能证明「到某日已核对完一个窗口」，**永远不能证明更早日期连续完整**，也不代表覆盖粒度完整（后者看 `coverage_level`）。

### 4.3 缺口台账：686 条，全部 pending

| stream | 条数 | 缺什么 |
|---|---|---|
| `retail-orders` | 27（逐日） | `order_details`、`payment_flows`（覆盖全部 127,173 单） |
| `purchase-orders` | 309 | `document_lines`、`branch_allocations`、`approval_history` |
| `purchase-returns` | 271 | 同上 |
| `purchase-receipts` | 67 | 同上 |
| `purchase-receive-history` | 11（月窗） | 内部单据 ID、完整单头/明细 |
| `product-tabs` | 1（initial-full） | 13 类页签，206,491 个对象 |

### 4.4 不可补 / 难补

1. **支付**：仅 2 条采样，补采按用户指令暂停。
2. **零售订单详情**：0 笔完整详情；127,173 笔待补，0.5/s 理论下限 **70.7 小时**（非完成时间承诺）。首笔 2026-10-11 已失败。
3. **OD Excel**：26 个单头字段，无商品行、无支付；缓存里 `orderDetails`/`payFlows` 为 null。
4. **流水/订单 Excel**：53 个展示列但无稳定源主键，只能做总量对账。
5. **历史收货报表**：只含已审核；95 天以前未审核收货单源端无可用接口。
6. **商品页签**：206,491 对象，约 115 小时下限；价格/库存是动态数据，遍历必然跨时点。
7. **采购 `totalRecord` 恒为 0**：三类流 `independent_export_total_days = 0`，只能靠唯一键计数 + 原始捕获一一对应自证。
8. **日常增量按检查点向前推进，不自动回扫**：`kemai_commerce_sync.py incremental` 单独运行且不传 `--from` 时才默认回看 30 天。采购无已验证的“最后修改时间”筛选，已完成日期上的旧单变更可能漏。

---

## 五、运行态（2026-11-07 11:40 实测）

| 项 | 状态 |
|---|---|
| 日常守护 | **PID 97002 运行中**，`kemai_daily_launch.py --supervise`，启动 09:47:31，`watching`，`last_complete_day=2026-11-06` |
| 追平/首轮/优先 | 均已完成并退出（catchup success、followup success to 2026-11-05） |
| 零售详情补采 | **已停**，`paused_by_user`，`source_worker_running=false` |
| 限流 | `state=ready`，冷却 1800s，当前无冷却 |
| OpenCLI | 1.8.8（可升 2.0.2）；浏览器 daemon PID 60725 运行中；登录态需人工确认 |
| ⚠️ 代码新鲜度 | daily supervisor 09:47 启动，**早于** `kemai_commerce_sync.py`(10:35) / `kemai_daily_launch.py`(10:18) 的最后修改 → **仍未加载「销售流水→订单商品行」新步骤**；启用前必须安全重启，且不得与旧进程并发 |

状态文件权威性：`kemai_daily_status.json`（supervisor PID）> `kemai_manual_checkpoint.json`（人工快照，PID 已漂移）。
**最权威断点在 DB `sync_checkpoints`**，不是任何 JSON。

2026-11-07 13:52 补记：跨进程源操作协调器已写入项目并安装到运行时适配器，8 个离线测试通过；PID 97002 未重启，旧 Python browser eval 路径尚未加载。切换步骤见 [源请求协调器说明](docs/kemai-source-gate.md)。

2026-11-07 15:08 补记：旧 PID 97002 已退出，新 PID 40069 在无在途任务时启动并进入 `watching`；断点仍为 2026-11-06，限流为 `ready`，零售详情仍暂停。9 个离线测试通过；下一完整源日仍需核对真实运行结果。

---

## 六、交接文档（2026-11-07 handoff）纠偏

| # | 交接原文 | 实际 | 证据 |
|---|---|---|---|
| 1 | commands.md 未写 `item-archives-export-start` / `retail-order-detail` 限制 | **已过时**。`commands.md:19`、`:14` 均已写；两 reference mtime 10:39 晚于适配器 10:22 | 文件 mtime |
| 2 | 两份 skill 的 `quick_validate` 已通过 | **属实，且已复验**。脚本位于 `~/.codex/skills/.system/skill-creator/scripts/quick_validate.py`，两份 skill 均返回 `Skill is valid!` | `uv run --with pyyaml python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py ~/.codex/skills/kemai-opencli`；`kemai-sync` 同法 |
| 3 | 「已从流水幂等映射出 453,971 条 retail_order_lines」 | **已做过数据库只读核验，但尚无落盘核验报告**。此前 `--verify-only` 返回 `verified_rows=453971`、`source_requests=0`、`db_writes=0`；04:11 的 integrity 报告是映射前快照，不能代表当前行数。需再次核验并保存输出，供独立复核 | `kemai_retail_lines_from_flows.py --from 2026-08-01 --to 2026-11-06 --verify-only`；旧 `kemai_integrity_final_2026-11-07.json` |
| 4 | 每日进程 PID 97002 | ✅ 正确（与 handoff 一致），但 `kemai_manual_checkpoint.json` 里记的是 49273，已漂移 | `ps` + JSON |
| 5 | 详情补采失败原因「订单不存在」 | 状态文件里是 **「零售详情等待超时」**；且该文案来自**旧实现路径**（现走 browser eval，错误文案已变）。两种失败语义必须分开 | `kemai_retail_detail_status.json:last_error` |
| 6 | 日常进程尚未加载新代码 | ✅ **仍成立**，且必须重启才能启用行映射步骤 | 进程 09:47 vs 代码 10:35 |

**建议补证（零源请求、零写）**：
```bash
python3 kemai_retail_lines_from_flows.py --from 2026-08-01 --to 2026-11-06 --verify-only > kemai_retail_lines_verify.json  # 只读核验并保存输出
python3 kemai_integrity_report.py --strict --summary --output kemai_integrity_verify.json
```

---

## 七、风险 Top（按影响排序）

**P0**
1. **新协调器运行验证待完成**：13 条运行时适配器命令和新 daily supervisor 的 Python 路径已接入文件锁，离线并发验证通过；下一完整源日仍需核对实际日志与商品行映射结果。手工裸调 `opencli browser` 不自动受控，必须经协调器包装。
2. **零售详情/支付未形成历史完整覆盖**：已有 4 条原详情商品行和 2 条支付采样，但无法支持完整的支付方式、收款对账或详情独有字段分析。
3. **商品页签 206,491 对象 + 采购明细 647 个有数据日**未排期：相关分析只能给单头/快照级。
4. **状态文件与实现漂移**：manual checkpoint PID、detail `last_error` 旧文案、限流文件人工字段无代码写入者。

**P1**
5. `sync_coverage_gaps` 只增不减：采购类 gap 即使增量已取全详情也永不 resolve（只有 3 处 resolve 调用）。
6. `item_detail`/`item_tab_*` 对象 `attempts>=3` 后不再被当前任务选中；商品变化触发的增量路径会重置尝试次数，但未变化对象缺少专门的重试入口。
7. `kemai_commerce_sync.py` 不清理残留 `sync_runs.status='running'`（`kemai_sync.py` 有，commerce 没有）。
8. 商品删除/停用无任何处理，与设计文档不符。
9. 适配器双副本容易再次漂移：本次已合并并安装此前缺少的「重置 entity / 3s→10s / 空实体早退」修复；项目不是 git 仓库，无版本历史，后续改动仍须核对两份代码。
10. 限流正则仍散落在适配器多个文件，漏改一处可能使某条命令不识别限流；共享门面已能接收适配器识别到的限流信号，但响应识别尚未全部收敛到单一实现。
11. 现有 9 条离线用例覆盖节流互斥、冷却信号和零售详情关键分支；`run()` 状态机、`plan()` 算术、写库路径、守护进程与商品链路仍缺测试。

**P2**
12. 绝对路径硬编码在 skill 文档（不可移植）。
13. 快照数字无 as-of + 复核命令，下一轮同步即过时。
14. 平台子应用 `models.generated.ts` 仍是 27 个模型，缺 11-07 新增的 3 张表；重新 `api pull` 前不要在新表上建页面。
15. `opencli-sync-design.md` 仍是"拟实现"口径，与实际实现 11 处不一致（item-page/item-section/whoami/`kemai-sync` 执行器/1/7 轮转/删除软删均未实现）。

---

## 八、30 秒速查

```bash
# 运行态
ps -p "$(python3 -c "import json;print(json.load(open('kemai_daily_status.json'))['supervisor_pid'])")" -o pid,etime,command
cat kemai_daily_status.json kemai_rate_limit_status.json kemai_retail_detail_status.json

# 只读审计（都不访问源站）
python3 kemai_retail_daily_audit.py --kind retail-orders --strict --summary --output a.json
python3 kemai_retail_daily_audit.py --strict --summary --output b.json
python3 kemai_purchase_daily_audit.py --kind purchase-orders --strict --summary --output c.json
python3 kemai_product_coverage_audit.py --strict --summary --output d.json
python3 kemai_integrity_report.py --strict --summary --output e.json
python3 kemai_retail_lines_from_flows.py --from 2026-08-01 --to 2026-11-06 --verify-only
python3 kemai_retail_detail_backfill.py plan --from 2026-08-01 --to 2026-11-06 --rate 0.5
python3 -m unittest -v test_kemai_retail_detail_backfill.py

# 单条源命令（手工调用不会自动限速，务必自己控 0.5/s）
opencli kemai retail-sales --from 2026-11-06 --to 2026-11-06 --page 1 --rows 1000 -f json
```

**禁止**：在 daily supervisor 运行时并发跑采集；立即重试限流；把签名 `sourceUrl` / 凭据写进日志或文档；把 `covered_through` 说成"历史连续完整"。
