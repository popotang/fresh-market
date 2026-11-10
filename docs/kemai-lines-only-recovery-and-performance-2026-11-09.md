# 商品行补采：完整性修复、源快照与性能测量

2026-11-09，供 Kimi 接续。用户已停止导入进程；本轮只修改代码和离线验证，没有重新启动采集、调用科迈、写生产数据或发布 BFF。此前交接为 `/tmp/fresh-market-kemai-speedup-handoff-2026-11-09.md`。

## 1. 已实现

- `kemai_pipeline/lines_only_workflow.py`：plan 与 run 共用同一商品行证据规则，旧整体详情 success 不再导致 plan 错报 eligible=0。取消“至少有一行且版本一致即可跳过”。跳过必须具备 `lines_only` 回执、相同源版本、原始快照摘要验证，以及数据库商品行的键集合、行数、字段语义核对。失败状态、缺行、字段变化和多余旧行都不能凭存在性跳过。合法空数组可证明零行完成。
- `kemai_pipeline/line_evidence.py`：每单完整响应先以不可变摘要文件落盘，再写数据库。包括本轮不入库的分支、审批等数组。原始数据只进入权限受限的私有工件；日志和计时文件仅包含摘要、计数和耗时。
- 已知源版本相同时，可复用已保存快照；未知写入结果、状态写入失败或进程重启后，先核对数据库再恢复写入，不必再次请求源。缺失/损坏的工件会报错，不能当作完整证据。
- `kemai_pipeline/detail_workflow.py`：增加读取、写后回调及分项计时接缝。默认完整详情路径行为保持原有逻辑，新快照和回执由 LinesOnlyWorkflow 使用。
- 商品行核验完成可将本地 capture 标记 committed，含义仅为当前所选商品行通过；数据库的整体详情状态仍为 partial，分支/审批仍 deferred，日级 gap 不关闭。
- `scripts/kemai_lines_only.py`：正式的 plan/run/resume 操作入口，默认每次最多 10 单，上限 100 单；沿用共享 writer lock 和源侧 gate。
- `scripts/test_kemai_offline.py`：将默认 CaptureStore 路径、写锁和限流状态隔离到临时目录，防止全套测试再往真实工件目录留下测试 capture。测试自己的 fake source/center 仍由各用例负责。

## 2. 重要取舍

**旧数据不会自动获得新回执。** 没有完整证据的旧 success/partial 状态需要重新核对，可能产生一次有界源读取。不能为了保持“快速跳过”而把有行当成行已完整。先在小样本上衡量新增源调用数量，不要直接放开全部历史日窗。

复用缓存以父单的 `source_version` 为依据，因此日常流程应先刷新列表版本。若详情返回的版本与父单不同，保存响应后报 `DETAIL_VERSION_MISMATCH`，先刷新父单再继续；不能把旧版本成功状态写给新版本父单。版本为 null 时不能据此跨运行跳过或复用旧快照。

回执和完整响应目前在本地私有工件中，未扩生产表。迁机/接手须连同 `.kemai-artifacts/detail-evidence/` 和 capture 目录一起保留。没有这些工件时不会凭数据库中有行就跳过。后续若要多机运行，需要将这些证据迁移到有权限控制的持久存储。

本轮没有做多 ID 浏览器批读或数据中心 BFF 合单写入，也没有调整 0.5 次/秒上限与 30 分钟冷却。当前改动保证恢复可重用已抓数据，并提供提速决策所需计时；**尚无真实源性能提升百分比**。

## 3. Kimi 恢复操作

先确认没有另一个导入进程、孤立 OpenCLI 请求或其他持锁者。用户停止时日志最后是 KeyboardInterrupt；不要靠“最后一个成功日窗”判断正在处理的 capture。

本轮只读检查发现较新的本地 capture：

| capture | 流/日期 | 当时状态 |
|---|---|---|
| `fda1d3b325044f588505a713287be2a1` | purchase-detail:receipt / 2026-10-24 | capturing、local_only |
| `8702f36dcea548a0af86328cd39b406a` | purchase-detail:return / 2026-10-24 | capturing、local_only |

前者是停止时应优先核对的候选；运行前重新读取其 manifest 和 `/tmp/fm-lines-only-next16.log` 确认，不能仅靠 mtime 认定来源或估算耗时。本轮没有修改这两个真实 manifest。

在项目根目录执行，以下命令由后续接手者按用户允许的运行时机执行：

```bash
# 只读查看当前日窗仍需核对多少单；不会请求科迈或写 capture。
python3 scripts/kemai_lines_only.py plan \
  --stream purchase-receipts --day 2026-10-24 --retry-failed

# 沿用经确认的本地 capture，第一轮最多 10 单。
python3 scripts/kemai_lines_only.py resume \
  --capture-id fda1d3b325044f588505a713287be2a1 \
  --max-documents 10 --retry-failed

# 仅当没有该日待恢复的 capture 时，新建有界运行。
python3 scripts/kemai_lines_only.py run \
  --stream purchase-receipts --day 2026-10-24 \
  --max-documents 10 --retry-failed
```

`resume` 在共享锁内更新本次单据预算并沿用原 capture；拒绝 server-controlled 或 method 不符的 capture。`interrupted` 需要调查原因，不自动改回 capturing。旧 capture 的 eligible_snapshot 仅为历史证据，恢复会按当前数据库和新版商品行规则重新筛选该日单据；原先被错误跳过的单据可能重新进入待处理集合。

`limit_reached` 表示本轮预算结束，继续同 capture；cooling 保持原 capture 等共享冷却解除；`incomplete` 必须检查缺 details、版本未知、多余旧子行等原因。命令退出码 0 代表执行返回了结构化结果，不等于整个历史日或整体详情已经完整，应检查 result.state/verified/remainder/verification_scope。

不要用两个 runner 同时处理同一日窗，不重启旧 supervisor，不直接恢复 `/tmp` 脚本的 16 天长批次。新入口在每次有界运行结束后释放锁，便于调度器安排日常增量；自动优先级调度本轮尚未实现。

## 4. 性能采样和证据位置

- 完整响应：`.kemai-artifacts/detail-evidence/<tenant-hash>/<kind>/<document-hash>/<sha256>.json`。
- 当前快照指针：同目录 `latest.json`；商品行回执：`lines-verified.json`。
- 单据尝试计时：`.kemai-artifacts/<capture-id>/timings/<uuid>.json`。
- 每次运行汇总：同目录 `summary-<uuid>.json`。保留失败尝试及各次恢复，不覆盖以前结果。

每单计时包含 source_read、snapshot_read/write、children_pre_read、children_batch_create_verify、children_update_verify、children_final_read、state_write_verify、receipt_write；运行汇总另包含最终 lines_verify。`source_read` 包含 OpenCLI 进程/浏览器与源请求总体耗时，当前不能再细分 Node 启动和 HTTP。数据中心分项包含对应 CLI 总耗时；批量写入阶段包含其内部读回。

`source_operations` 来自源适配器逻辑命令计数，`source_attempts` 表示调用尝试；二者都不能当作精确 HTTP 数，`source_http_requests` 明确为 null。`data_center_requests` 是适配器 CLI 调用计数，也不是后端 SQL 条数。计时的 run 总耗时不包含前置 plan/begin 候选查询；比较端到端速度还需记录整个进程 wall time。

建议首轮 10 单，核对结果后累计 20–30 单即可做初次判断，分开统计首次读取与 cache_hit：

1. 每单总耗时均值、P50/P95、源/数据库各阶段占比。
2. 每单数据中心调用数、源逻辑调用数、新增/修复行数及失败原因。
3. 源读取占比大，再做小批多 ID、逐单落盘和限流中止；数据库占比大，再做标准 API BFF 合单事务与统一回执。
4. 不用删除核验、提升源并发或放宽完整性条件换取性能。已经拿到的完整响应可供后续离线补分支/审批，但当前尚无对应快照导入子命令。

## 5. 验证记录

离线用例覆盖：旧失败状态只有部分行、旧 success 无证明、零行完成、字段/行损坏修复、源版本变化/未知、快照篡改、完整数组保存、写入未知/状态写失败恢复、落盘失败拒绝写库、冷却恢复、多余旧行、缺 details、计时输出，以及新 CLI 的只读 plan、同 capture 恢复和拒绝 server capture。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_kemai_offline.py
node --test tests/pipeline/*.test.mjs
git diff --check
```

最终独立验证：隔离后的 Python 全量 **386/386 通过**；Node **41/41 通过**；`git diff --check` 通过。Python 最后一轮包含 plan 与 run 共用商品行证据规则的回归；Node 源码本轮未改。全程未调用真实源或写入生产数据；用户已停的进程保持停止。

## 后续分组更新

商品行已有行的逐行读改写已进一步优化，当前实现、性能字段和接续方式见 [分组更新说明](kemai-grouped-update-2026-11-09.md)。本篇 386 项是前一轮基线；后续基线为 398 项。
