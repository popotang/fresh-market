# M1 开发报告：零售小时级采集达到可切换状态（2026-11-08）

> 里程碑：M1（最高优先级）。范围：`kemai_live_sync.py`（零售订单 + 销售流水 + 订单行映射）。
> 本报告只覆盖**离线代码与测试**；真实源运行属于计划中的「受控切换阶段」，本文未执行。

## 一、结论

M1 的四条离线要求已实现并回归通过：

| 要求 | 状态 | 证据 |
|---|---|---|
| M1.1 `plan/once/supervise` 代码审查与离线回归 | ✅ | 21 个 live 用例 + 全量 45 个离线用例通过 |
| M1.2 互斥检查覆盖**实际进程状态** | ✅ | 新增 `active_source_workers()`，扫描真实进程表 |
| M1.3 结构化输出与退出码 | ✅ | `source_requests`、逐流 `source_unique/saved/pages`、`pending_finalization_days`、`cooling`；退出码 0/2/3 |
| M1.4 状态与检查点写入语义 | ✅ | 临时快照写 `sync_live_cursors` + `live:*` 事件且 `covered_through=NULL`；仅日终核对后推进 `commerce_incremental:*` |

真实源验收、常驻调度、页面发布**均未开始**，属后续阶段。

## 二、代码改动（`kemai_live_sync.py`，+152 行）

### M1.2 实际进程互斥

- 新增 `_ps_rows()` / `_ancestors()` / `active_source_workers()`：解析 `ps -axo pid=,ppid=,command=`，只把「可执行文件含 python 且命令行命中采集脚本」的进程视为源 worker。
- 覆盖脚本：`kemai_daily_launch`、`kemai_collection_launch`、`kemai_commerce_sync`、`kemai_purchase_detail_backfill`、`kemai_commerce_reconcile`、`kemai_retail_detail_backfill`、`kemai_product_incremental`、`kemai_changed_refresh`、`kemai_changed_tabs`、`kemai_masters`、`kemai_derive_suppliers`、`kemai_retail_lines_from_flows`。
- 明确**不**误判：自身进程、祖先 shell、`grep`/编辑器命令行、另一个 `kemai_live_sync`（后者由 flock 拦截）。
- `ensure_old_workers_paused()` 现在 = 旧状态文件检查 **+ 真实进程检查**；`run_once` 每轮调用，`supervise` 因此每周期都会重新校验。
- 保留原有三层：进程级 flock（`.kemai-live.lock`）→ 每流 DB `GET_LOCK` → 共享源 gate（0.5 次/秒 + 30 分钟冷却）。

### M1.3 冷却短路与结构化输出

- 新增 `cooldown_state()`：只读 `kemai_rate_limit_status.json`，不阻塞。
- `run_once()` 在**发出任何源请求前**检查冷却；命中即返回 `cooling`（含 `resume_after`、`seconds_remaining`）并置 `skipped`，**不再用 `honor_cooldown()` 睡死一个周期**，也不启动第二实例。
- 输出新增：`snapshot_day`、`finalize_through`、`source_requests`、`pending_finalization_days`（逐流）、`cooling`、逐流 `source_unique/saved/pages/observed_at`。
- 新增 `once_exit_code()`：`0` 成功、`2` 有错误、`3` 冷却未尝试；`once` 用它退出。
- 新增 `next_delay()` + `supervise` 改造：正常按周期；有错误指数退避（60s×失败次数，上限 900s）；冷却则睡到冷却结束但不超过一个周期；**运行超过周期时打印 `cycle_overrun_seconds` 并合并下一次触发**，不并发。
- `plan` 增加 `cooldown` 与 `active_source_workers`，保持 `source_requests=0 / db_writes=0`。

### M1.4 写入语义（原有实现已正确，本次加测试固化）

- 临时快照：写 `sync_live_cursors` + `live:*` 的 `sync_stream_events`/`sync_stream_status`，并显式 `covered_through=NULL`。
- 日终：仅 `verify_export=True` 成功后 `save_daily_checkpoint()`，且拒绝跳日（必须是旧检查点 +1 天）。
- 失败：`finish_failure` 保留 `last_success_at`，游标与日检查点都不动。

## 三、测试

```
python3 -m unittest -q test_kemai_live_sync.py                                 21 tests OK
python3 -m unittest -q test_kemai_live_sync.py test_kemai_commerce_safety.py \
    test_kemai_source_gate.py test_kemai_retail_detail_backfill.py            45 tests OK
```

新增覆盖的边界：

| 边界 | 用例 |
|---|---|
| 实际 worker 进程阻断启动 | `test_real_worker_process_blocks_start` |
| shell/grep/自身/祖先不误判 | `test_shell_wrapper_grep_and_self_are_not_workers`、`test_ancestors_are_skipped` |
| 进程表不可读时不误报冲突 | `test_unreadable_process_table_does_not_claim_conflict` |
| 冷却期零源请求、退出码 3 | `test_cooling_returns_without_any_source_request` |
| 分页重复键 → 游标不前进 | `test_duplicate_page_key_aborts_without_cursor_advance` |
| 同日两次快照保持 provisional | `test_two_same_day_snapshots_stay_provisional` |
| 临时快照不推进日检查点 | `test_current_day_does_not_advance_final_checkpoint` |
| `live:*` 的 `covered_through` 保持空 | `test_provisional_snapshot_keeps_live_covered_through_empty` |
| 日终仅在核对后推进 | `test_finalized_day_advances_only_after_verified_save` |
| 临时快照不创建 Excel 导出 | `test_provisional_sale_snapshot_avoids_excel_export_task` |
| 销售流水映射不覆盖原详情行 | `test_flow_mapping_never_overwrites_detail_api_rows` |
| 退出码与退避/合并 | `test_exit_codes_are_explicit`、`test_next_delay_merges_overrun_and_backs_off` |
| 02:00 是结算门槛而非首次采集门槛 | `test_02_is_finalization_gate_not_first_poll_gate` |
| 长停机合并为有界日追赶 | `test_long_outage_coalesces_to_bounded_daily_catchup` |

另有真实 DB 只读验证（**零源请求**）：

```bash
python3 kemai_live_sync.py plan --max-catchup-days 2
# {"finalize_through":"2026-11-07","source_requests":0,"db_writes":0,
#  "cooldown":{"cooling":false},"active_source_workers":[],
#  "streams":{"retail-orders":{"daily_checkpoint":"2026-11-07","pending_finalization_days":0},...}}
```

## 四、仍未覆盖 / 剩余风险

1. **真实源未运行**：`once` 从未对科迈发起过请求，实际耗时、分页稳定性、导出任务延迟均未知。
2. **空日未实测**：源日为 0 行时，日终导出任务是否返回 `totalRows=0` 需真实窗口验证。
3. **跨午夜长周期**：逻辑上 `now` 每周期重算，但「23:59 开始、00:10 结束」的组合未真实验证。
4. **数据库断线恢复**：`supervise` 依赖 `conn.ping(reconnect=True)`；断线发生在 `sync_day` 中途时游标不前进（已测异常路径），但重连后的锁语义未真实演练。
5. **`observed_through_utc` 语义**：记录的是快照**开始**时刻，源端仍按整日筛选，**不是**「数据已更新到该时刻」的证明；页面文案必须与此一致（M2 项）。
6. **未接入常驻托管**（M2）：当前 `supervise` 是前台命令，无自动重启、无开机拉起。
7. **页面未发布**：`sync-status-page/` 本地源码已能显示 `live:*`，线上未发布。
8. **采购/商品未接入**（M3）；历史缺口补采未开始（M4）；零售原详情/支付仍暂停。

## 五、受控切换清单（真实源验收，需授权后执行）

前置：`kemai_daily_status.json` 仍为 `paused_by_user`；无任何采集 PID；`kemai_rate_limit_status.json` 为 `ready`。

1. `python3 kemai_live_sync.py migrate`（幂等，确认 `sync_live_cursors`）。
2. `python3 kemai_live_sync.py plan --max-catchup-days 2`，记录基线。
3. **唯一执行者**运行一次 `once --max-catchup-days 2 --rate 0.5`（`--max-catchup-days 0` 不合法，最小 1）。
4. 核对：`source_unique` = `saved`；`sync_live_cursors` 两行已写；`live:*` 的 `sync_stream_events` 成功且 `sync_stream_status.covered_through` 为 **NULL**；`commerce_incremental:*` **未**前进；无重复稳定键。
5. 同日再跑一次 `once`：业务行数不增加，`covered_through` 仍为 NULL。
6. 两项都通过后，才讨论 `supervise --interval-minutes 30` 的托管方式（M2）。
7. 任一失败：保留游标，先查日志，不加并发、不缩短冷却。

## 六、下一步建议

- 先完成上表的第 3–5 步真实源验收（一次 `once`，约几分钟，2 条流当天快照）。
- 验收通过后再做 M2（托管 + 页面发布），否则页面会展示一个从未真实运行过的状态源。
