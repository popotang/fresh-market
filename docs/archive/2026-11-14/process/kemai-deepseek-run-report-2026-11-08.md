# 科迈采集运行报告：2026-11-07 增量（DeepSeek 执行）

> 执行窗口：2026-11-08 01:25–03:43 CST ｜ 执行人：DeepSeek Harness
> 本报告是运行证据，不是实时读数；任何后续结论以 DB 检查点与 `.local-audits/` 新生成文件为准。
> 不含口令、令牌或导出签名地址。

## 一、执行摘要

| 项目 | 结果 |
|---|---|
| 2026-11-07 六条交易流 | **全部成功**，检查点均前进到 `2026-11-07` |
| 商品链路 | **全部成功**（主数据、清单增量、供应商、变化详情、变化页签） |
| 只读审计 | 零售订单/流水、采购订单、商品覆盖的最终文件均 `issues: []`；完整性快照的 `strict_failures` 为空，但生成于商品页签重试完成前，仍需一次收尾复核 |
| 2026-11-08 增量 | **未执行**，按计划等 2026-11-09 02:00 后 |
| 历史补采 | **未执行**，等用户逐批授权 |
| 零售详情/支付 | **保持暂停**，未运行 `start` |

## 二、逐流运行记录

| 流 | 源日期 | 命令 | 开始→结束 | 源唯一键数/核对 | DB 保存 | 检查点 | 覆盖级别 | 失败/重试 |
|---|---|---:|---|---:|---:|---|---|---|
| retail-orders | 2026-11-07 | `incremental --kind retail-orders --rate 0.5` | 01:29:39→01:34:31 (run 51) | 5,228（导出 `totalRows` 核对） | 5,228 单头 | 11-06→11-07 | `headers_only` | 无 |
| retail-sales | 2026-11-07 | `incremental --kind retail-sales` | 01:34:32→01:40:14 (run 52) | 20,222（21 页 + 导出总数核对） | 20,222 流水 | 11-06→11-07 | `full_list` | 无 |
| purchase-receive-history | 2026-11-07 | `incremental --kind purchase-receive-history` | 01:40:15→01:47:25 (run 53) | 519（DB 行数核对） | 519 行 | 11-06→11-07 | `approved_rows_only` | 无 |
| purchase-orders | 2026-11-07 | `incremental --kind purchase-orders`（修复版代码） | 01:50:09→01:55:54 (run 54) | 28（单轮唯一键） | 28 单头 | 11-06→11-07 | `full_detail` / `partial_detail` | 首轮失败（严格数组），修复后重跑成功 |
| purchase-receipts | 2026-11-07 | `incremental --kind purchase-receipts` | 01:55:55→02:02:49 (run 55) | 34（单轮唯一键） | 34 单头 | 11-06→11-07 | `partial_detail` | 同上 |
| purchase-returns | 2026-11-07 | `incremental --kind purchase-returns` | 02:02:51→02:05:57 (run 56) | 15（单轮唯一键） | 15 单头 | 11-06→11-07 | `partial_detail` | 同上 |
| retail 商品行映射 | 2026-11-07 | `kemai_retail_lines_from_flows --from 2026-11-07 --to 2026-11-07` | 01:34 段 | 零源请求 | 20,222 行映射 | — | `flow_mapped` | 无 |

采购列表 `totalRecord` 不可靠，只能声明「单轮唯一键数 = 原始捕获数 = 落库数」，**没有独立源总数**。

### 采购详情完整性（2026-11-07）

| 单据类型 | 单据数 | `success` | `partial`（源缺附属数组） | `failed` |
|---|---:|---:|---:|---:|
| order | 28 | 13 | 15（缺 `approves`） | 0 |
| receipt | 34 | 0 | 34（缺 `branchs`） | 0 |
| return | 15 | 0 | 15（缺 `branchs`） | 0 |

`partial` 单据保留原有子表行、未被清空，缺项写入 `sync_coverage_gaps`（`load_mode=partial_detail`，含脱敏样本 ID）。**不得把这些日期称为 `full_detail` 完整覆盖。**

## 三、商品链路

| 任务 | 结果 | 关键数字 |
|---|---|---|
| `kemai_masters.py` | 成功（第二次） | 机构/部门/类别/品牌/单位 5 类；首次因浏览器会话未登录失败 |
| `kemai_product_incremental.py` | 成功 (run 57, 02:09:43→02:18:14) | 源端 **18,390**；新增 **4**；变化 **138**；未变 18,248 |
| `kemai_derive_suppliers.py` | 成功 | 供应商派生 |
| `kemai_changed_refresh.py` | 成功（重跑 60/60） | 首次因 RDS 断连停在 58/142 |
| `kemai_changed_tabs.py` | 成功（142 商品 × 14 类页签全 complete） | 02:26:52→03:42:27，约 75 分钟 |

商品任务是**执行时快照**，不是「11 月 7 日 24:00 终态」，不得这样表述。

## 四、缺口台账（运行后）

`sync_coverage_gaps` 共 **690 条 pending**（运行前 686 条，因 11-07 新增一天缺口）：

| stream | pending |
|---|---:|
| purchase-orders | 310 |
| purchase-returns | 272 |
| purchase-receipts | 68 |
| retail-orders | 28 |
| purchase-receive-history | 11 |
| product-tabs | 1 |

商品初始页签仍有 **204,836 个 pending 或 failed 对象**（03:43 的 `product-coverage-2026-11-07.json`；`item_detail` 已全部 done）。此前的 206,824 是 142 个变化商品 × 14 类页签重试完成前的旧数。按 0.5 次/秒的请求理论下限约 114 小时，不是承诺工期。

## 五、失败与重试记录（全部保留证据）

| 时间 | 事件 | 处置 |
|---|---|---|
| 01:28 | `masters` / `item_incremental` 报「登录认证失败」（浏览器会话过期） | 用户重新登录后重跑成功 |
| 01:28–01:29 | 采购三条流报「缺 approves/branchs 数组」（旧严格校验） | 停滞；Codex 改为只强制 `details`、附属数组记 `partial` 后重跑成功（01:50–02:05） |
| 02:18–02:23 | RDS 瞬时断连（`InterfaceError(0,'')` / `Lost connection`），导致 `changed_refresh`、`changed_tabs` 与 4 条审计失败 | 业务任务及零售/采购/商品覆盖审计重跑成功；完整性快照仍需在商品页签完成后重跑 |

## 六、审计证据（`.local-audits/`，权限 700/600）

| 文件 | 结论 |
|---|---|
| `retail-orders-2026-11-07.json` | 99 天全部「导出总数或空日」核对，`issues: []` |
| `retail-sales-2026-11-07.json` | 98/99 天导出核对，`2026-10-11` 仍为单轮计数（已知），`issues: []` |
| `purchase-orders-2026-11-07.json` | 676 个已审日期，`issues: []` |
| `product-coverage-2026-11-07.json` | 源总数 18,390，`issues: []` |
| `integrity-2026-11-07.json` | 02:23 快照中重复键/孤儿的 `strict_failures` 均为空；早于 03:42 商品页签重试完成，不能充当最终完整性验收 |
| `retail-lines-*.json` | 零售流水→商品行映射核验 |
| `collection-status-run1/run2-2026-11-08.json` | 两轮编排状态快照 |
| `logs-run2-2026-11-08/` | 第二轮逐 job 日志快照 |

运行日志：`kemai_collection_logs/`（新增的源任务日志按 job + 源日期 + 尝试时间戳命名；首次运行的无后缀日志及独立重试日志也保留）。`kemai_collection_status.json` 仍是中途失败的编排器快照，商品重试成功由独立重试日志和 03:43 的最终商品覆盖审计证明，不能只看该状态文件判断最终结果。

**复核边界：**零售订单与流水的 11 月 7 日源总数分别在 01:29、01:34 左右取得，早于既定的 02:00 源端延迟门槛。现有日志证明当时导出总数与落库数相等，尚不能证明 02:00 后没有迟到记录。后续先做总量复核；只有总量变化时才走受控旧日对账。采购列表没有可靠的独立源总数，保留“单轮唯一键数 = 捕获数 = 落库数”的有限结论。

## 七、未完成 / 暂停项

1. **2026-11-08 增量**：在现有日批方案下，等 2026-11-09 02:00 后执行 `python3 kemai_collection_launch.py --stage batch2`。用户的新目标是每 30–60 分钟采集并按 DB 游标自动追赶；该命令只是新采集器上线前的过渡方案，详见 [小时级增量设计](kemai-hourly-incremental-design-2026-11-08.md)。
2. **历史采购详情补采**（309+68+272 个有数据日）：未开始，需用户授权；建议先 `plan` 再 1 天 `--limit 5` 小样本。
3. **历史已审核收货 11 个缺口窗**：需逐窗核可访问性。
4. **商品初始页签 204,836 个 pending 或 failed 对象**：需独立 `plan/start --section --limit` 入口，现有 `kemai_sync.py full` 会触碰商品清单断点，不建议直接用。
5. **零售订单原详情 + 支付**：按用户指令暂停，仅允许 `plan`。

## 八、恢复日常 supervisor 的前置条件

`kemai_daily_status.json` 仍为 `paused_by_user`、`supervisor_pid=null`、`last_complete_day=2026-11-06`。
手工运行只更新了 DB 检查点，**没有**写入 `days['2026-11-07']` 的 job 成功项。按交接要求，恢复前必须做一次**受控状态文件交接**：

1. 以 DB `sync_checkpoints` + `sync_stream_events` + 本报告为准，逐项确认六个流、商品任务、零售行映射均有成功证据；
2. 制定受控状态交接并核对商品链、六流和零售商品行的成功证据。直接让 supervisor 从旧状态“自然续跑”会重跑商品扫描，并可能从旧 `last_complete_day` 重做已映射日期；不能把它当成无成本恢复；
3. 重启 supervisor 时保持单实例，且必须在无在途任务、`state=ready` 时进行；
4. 不要在 11-08 数据仍在形成时（2026-11-09 02:00 前）提前恢复。

## 九、下次从哪里续

- 11-08 增量：`--stage batch2`（自动等 2026-11-09 02:00）。
- 采购 `partial` 单据：源端版本变化后或对历史窗口跑 `kemai_purchase_detail_backfill.py start --retry-failed`。
- 缺口补齐后，对应 `sync_coverage_gaps` 才会由 `verify_day` 判定 resolved；仅重取详情不会自动关缺口。
