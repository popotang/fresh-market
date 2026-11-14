# 交付文档：零售小时级采集 M1/M2 + 商品页签补采 M4（2026-11-08）

> 依据：[下一阶段开发交接计划](kemai-sync-next-development-plan-2026-11-08.md) 与 [小时级增量设计](kemai-hourly-incremental-design-2026-11-08.md)。
> 性质：**代码评审 + 2026-11-08 受控验收记录**。评审共识截至 `comment-12`；真实源 `once`、小样本和当前状态页发布的执行证据见 §十二。新服务试启后已按用户目标澄清停止，旧日常 supervisor 仍暂停。
> 不含口令、令牌或导出签名地址。项目不是 git 仓库，无提交记录；文件时间戳即版本依据。

## 一、交付结论

| 里程碑 | 计划要求 | 本次状态 |
|---|---|---|
| **M1** 零售小时级达到可切换 | 代码审查、实际进程互斥、结构化输出/退出码、状态与检查点语义 | 离线实现与受控真实源 `once` 已验证；当日订单行允许明确标记暂未关联，日终仍严格核对。首次受监督周期尚待观察 |
| **M2** 常驻调度与新鲜度 | 单实例托管、自动重启、状态页显示 `live:*`、文档更新 | 托管脚本已试启并主动停止，当前**没有常驻采集**；进程级守护/开机自启未配置。页面当前版本已发布为 `FORMAL`，运行态数据接口可读；浏览器视觉验收未做 |
| **M3** 采购/商品独立节奏 | 独立游标、耗时测量、商品读取适配器化 | ⏸ **未开始**（需真实耗时数据与源端验证，见 §六） |
| **M4** 历史缺口补采 | 商品初始页签独立 plan/start/status | 工具与离线测试完成；`barcode`、`supply` 各 5 个真实对象已验收 |

离线测试：**95 个用例全部通过**。本轮真实 DB 与源端验证结果见 §十二；历史空缺与日终完整性不能由当日临时快照代替。

### 评审共识（C1–C10）

| 编号 | 当前有效结论 |
|---|---|
| C1 | 评审时 M1 仅离线完成；必须先通过真实源 `once`，再启用 `supervise`。本轮已按此顺序执行。 |
| C2 | 评审时 M2 仅托管脚本完成；本轮已试启并停止脚本、发布状态页，但外部进程守护/开机自启与浏览器视觉验收仍未完成。 |
| C3 | `active_source_workers()` 覆盖 19 个源请求入口，只是尽力而为的第二道防线；`ps` 失败返回空、中途启动的其他 worker 不被阻止，非 macOS 未验证。工作锁、每流 DB 锁与共享源 gate 各自限制不同范围，不能据此宣称所有入口全局互斥。 |
| C4 | 页签以 `sync_objects` 为对象级断点。批次失败写失败事件和 `sync_runs.status='failed'`；已完成对象不回滚，重跑仅取 `pending/failed` 且 `attempts<3`。 |
| C5 | `product-tabs-backfill` 是合成流，`covered_through` 保持 `NULL`；`last_success_at` 是批次完成时间，`last_saved_rows` 是本批完成对象数。总体剩余看 `plan/status` 的 `retryable_total`，不能把单批数称为完成率。 |
| C6 | `stop` 发信号前核对精确脚本路径与 `supervise` 参数；身份不符返回 `identity_mismatch`，拒绝停止该 PID。 |
| C7 | `source_requests_estimate` 仅按列表页数加日终导出任务数估算，排除导出任务轮询等请求；精确计数需下沉到 source gate。 |
| C8 | `supervise` 终生持服务锁；`once`、`supervise` 每轮采集与页签补采共用工作锁。工作锁被占时本轮 `skipped_busy`，手动 `once` 退出码 4。补采可能跨越多个周期，零售新鲜度没有固定上限，以日志与 `last_success_at` 实测。 |
| C9 | 引擎级测试已验证：选中真实候选时 `sections=[barcode]` 只请求 barcode，不触碰其他 13 类页签；本轮还完成了 `barcode`、`supply` 各 5 个真实源对象的小样本。 |
| C10 | 评论区保留评审过程；本正文按 `comment-7`、`comment-9`、`comment-11` 的最终共识和 `comment-12` 的同意记录修订。 |

## 二、交付物

| 文件 | 说明 |
|---|---|
| `kemai_live_sync.py` | 小时级采集 `migrate/plan/once/supervise`、分生命周期的服务锁和工作锁、冷却短路、结构化输出与退避 |
| `kemai_live_service.py` | 常驻托管脚本 `start/stop/status/restart`，PID 文件、独立会话、口令匿名管道与停止前身份校验 |
| `kemai_product_tabs_backfill.py` | 初始页签有界补采 `plan/start/status`，单页签类型、`--limit` 上限、对象断点与工作锁 |
| `kemai_sections.py` | `sections=` 单页签范围支持；引擎级测试覆盖实际候选请求 |
| `test_kemai_*.py`、`README-sync-status.md` | 离线回归测试与运维说明 |

## 三、M1 具体实现

### 3.1 互斥覆盖真实进程（原实现最大缺口）

- 新增 `active_source_workers()`：解析 `ps -axo pid=,ppid=,command=`，只把「可执行文件含 python 且命令行命中采集脚本」视为源 worker。
- 当前清单覆盖 19 个已识别的源请求入口，包含全量商品、商品页签补采与旧批次编排器；新增入口时须同步扩展清单。
- 不误判：自身进程、祖先 shell、`grep`/编辑器、另一个 `kemai_live_sync`（由 flock 拦）。
- `ensure_old_workers_paused()` = 旧状态文件 + **真实进程表**；`run_once` 每轮调用，`supervise` 因此每周期校验。
- `supervise` 终生持有 `SERVICE_LOCK`，每轮采集与手动 `once` 持有 `WORK_LOCK`；页签补采也持有 `WORK_LOCK`。每流 DB `GET_LOCK` 与共享源 gate（0.5 次/秒 + 30 分钟冷却）继续生效。`ps` 检查失败会返回空，中途新起的旧 worker 不会被该检查拦住；不可把它写成全局互斥保证。

### 3.2 冷却不再阻塞一个周期

- 新增 `cooldown_state()`：只读 `kemai_rate_limit_status.json`。
- 冷却中 `run_once` **不发任何源请求**，返回 `cooling`（`resume_after` / `seconds_remaining`），退出码 **3**。
- `supervise` 睡到冷却结束但不超过一个周期；有错误时指数退避（60s×失败数，上限 900s）；运行超过周期打印 `cycle_overrun_seconds` 并合并下次触发，不并发。

### 3.3 结构化输出与退出码

`once` 输出新增：`snapshot_day`、`finalize_through`、`source_requests_estimate`、`pending_finalization_days`（逐流）、`cooling`；每条流含 `source_unique`、`saved`、`pages`、`observed_at`。估算值只含列表页数与日终导出任务数，不含导出任务轮询等请求，不能用于精确用量告警。

退出码：`0` 成功 / `2` 有错误 / `3` 冷却未尝试 / `4` 工作锁被占、本轮未尝试。

### 3.4 写入语义（原实现已正确，测试固化）

- 临时快照写 `sync_live_cursors` + `live:*` 事件/状态，`covered_through` 强制 `NULL`。
- 仅日终 `verify_export=True` 成功后写 `commerce_incremental:*`，且拒绝跳日。
- 失败保留游标与 `last_success_at`。

## 四、M2 具体实现

- `kemai_live_service.py`：`start` 前台输入口令一次 → 独立会话启动 `kemai_live_sync.py supervise`；`stop` 仅对核对为本脚本的 PID 先发 SIGTERM、超时再发 SIGKILL；`status` 读 PID/日志（`--db` 再读游标与检查点）；`restart` 在 stop 失败时拒绝启动第二实例。进程退出后的外部守护与开机自启尚未配置。
- 启动前 `preflight()` 复用 `ensure_old_workers_paused()`，旧 supervisor 未暂停或存在其它源 worker 时拒绝。
- `sync-status-page/` 已补 `live:`、`provisional`、分钟级距今更新和 5 分钟状态刷新；本轮完成 JSX 解析、CLI 保存/发布回读与运行态数据接口核对。浏览器视觉呈现尚未验收。
- `README-sync-status.md` 已修正过时表述（表已在生产创建、采集尚未启动），并补运维命令。

## 五、M4 具体实现

- `plan`：逐页签统计 `done/pending/failed/blocked/retryable`、理论耗时（`retryable/rate/3600`），明确标注「下限，非承诺」。零源请求、零写入。
- `status`：plan + 最近 3 次运行、最近 5 条事件、`product-tabs-backfill` 状态行。
- `start`：强制**单一页签类型**（`--section all` 被拒），`--limit 1..200`，`GET_LOCK` 防并发，从 `sync_objects` 取 `pending/failed` 且 `attempts<3` 的对象 ID，调用 `sync_sections(..., sections=[section])` 只处理该类，核对完成数后写 `sync_runs` 与 `sync_stream_events`。
- `attempts>=3` 的对象默认不重试（`blocked_attempts` 单独暴露），符合「不自动反复重试」要求。
- 对象成功即提交；某个对象失败可使本批最终失败，但不会回滚此前已完成对象。失败运行与事件保留，下次只选可重试对象。合成状态流 `covered_through=NULL`，`last_saved_rows` 仅表示本批完成对象数。

## 六、此前只读验证（历史快照，零源请求）

以下是开发阶段的一次只读 `plan` 输出，不代表本次回写时的实时 DB 或源站状态；运行前必须重新取基线。

```text
python3 kemai_live_sync.py plan --max-catchup-days 2
  finalize_through=2026-11-07   source_requests=0   db_writes=0
  cooldown=false   active_source_workers=[]
  retail-orders checkpoint=2026-11-07  pending_finalization_days=0
  retail-sales  checkpoint=2026-11-07  pending_finalization_days=0

python3 kemai_product_tabs_backfill.py plan --section all
  retryable_total=204,836   blocked_total=0   theoretical_hours=113.8
  barcode: done=177 pending=18213
  combination: done=175 pending=10
```

## 七、测试结果

```
python3 -m unittest -q test_kemai_live_sync.py test_kemai_live_service.py \
    test_kemai_product_tabs_backfill.py test_kemai_sections_scope.py \
    test_kemai_commerce_safety.py \
    test_kemai_source_gate.py test_kemai_retail_detail_backfill.py
Ran 95 tests — OK
```

| 套件 | 用例 |
|---|---:|
| `test_kemai_live_sync.py` | 38 |
| `test_kemai_live_service.py` | 14 |
| `test_kemai_product_tabs_backfill.py` | 15 |
| `test_kemai_sections_scope.py` | 4 |
| `test_kemai_commerce_safety.py` | 15 |
| `test_kemai_source_gate.py` | 5 |
| `test_kemai_retail_detail_backfill.py` | 4 |

新增覆盖的边界：实际 worker 阻断启动、shell/祖先不误判、进程表不可读时的行为、冷却零源请求、分页重复键不推进游标、同日两次快照保持 provisional、`covered_through` 保持空、流水映射不覆盖详情行、日终仅在核对后推进、启动前拒绝第二实例、停止前 PID 身份校验、页签对象失败留痕与断点、真实候选只请求指定页签、服务锁与工作锁的双向互斥，以及 `main()` 入口的 `once`/退出码 4。

## 八、仍未完成的范围与限制

1. **M1 周期验证**：真实 `once` 已通过，当前服务首个定时周期仍待观察；当日分页会重叠，已仅在 provisional 路径去重并记录数量。日终导出核对仍未由本轮实际触发。
2. **M2 运行状态与外部守护**：`kemai_live_service.py start` 已试跑后停止，目前不采集；进程退出后的自动拉起与开机自启尚未配置，不能声称无人值守高可用。
3. **M2 页面视觉验收**：当前 `live:*` 版本已发布为 `FORMAL`，运行态数据接口已读到三条状态；未按浏览器打开页面做视觉验证。
4. **M4 长队列**：`barcode` 5 个合法空数组、`supply` 5 个非空子表写入已验证；其余页签队列仍待按需补采，不能称全量完成。
5. **M3 未开始**：采购/商品独立节奏需要先测真实耗时；把商品读接口封装为 `opencli kemai` 适配器属较大重构，应先做离线夹具再受控小样本核对。当前不应宣称采购与商品达到 30 分钟新鲜度。
6. **零售原详情/支付**：仍按用户指令暂停，未运行 `start`。
7. **旧 supervisor**：仍 `paused_by_user`，未恢复；不可与新服务同时启用。

## 九、受控启用清单（按顺序验收）

前置：`kemai_daily_status.json` 为 `paused_by_user`；无采集 PID；`kemai_rate_limit_status.json` 为 `ready`；`python3 kemai_live_sync.py plan` 通过。

1. `python3 kemai_live_sync.py plan --max-catchup-days 2` 重新记录基线、冷却状态和真实 worker；缺表时才执行幂等 `migrate`。
2. 唯一执行者运行一次 `once --max-catchup-days 2 --rate 0.5`。核对源唯一键、落库数、`sync_live_cursors`、`live:*` 事件和 `covered_through=NULL`；日终核对若发生，应按导出总数和检查点语义逐日验收，不预设 `commerce_incremental:*` 一定不前进。
3. `once` 真实验收通过后，再执行页签 `start --section barcode --limit 5 --rate 0.5`；核对对象级状态、子表行数与错误日志。运行中零售采集会让路，可能连续多轮 `skipped_busy`。
4. 仅前述验收通过后启用 `python3 kemai_live_service.py start --interval-minutes 30`，核对实际周期的游标、事件、错误与 `last_success_at`。进程异常退出后的自动拉起仍需另配守护。
5. 构建/预览状态页，以真实 `live:*` 日志验证「当日同步距今」「最近完成源日期」及失败/冷却展示；通过后再发布当前版本并核对线上效果。
6. 任一失败：保留游标与日志，不加并发、不缩短冷却、不启第二实例；`source_requests_estimate` 不能用作精确请求审计。

## 十、风险与回滚

| 风险 | 说明 | 回滚 |
|---|---|---|
| 真实源未验证 | 分页/导出延迟最坏可能让一个周期超过 30 分钟 | 已实现合并触发；必要时 `kemai_live_service.py stop` |
| 进程表漏判 | `ps` 失败返回空；非 macOS 与中途启动场景未验证 | 工作锁只协调已接入入口；每流 DB 锁与共享 gate 继续限流，运维仍须检查其他 worker |
| 页签补采与零售新鲜度 | 204,836 对象约 113.8 小时仅是理论下限；冷却和源延迟可让单批跨多个周期 | 有界 `--limit`；以 `skipped_busy` 日志和 `last_success_at` 观察真实影响 |
| 服务双实例/异常退出 | 服务锁阻止第二个 `supervise`，但无外部进程守护 | `stop` 核对身份；另配进程级守护前不能宣称自动重启 |
| 页面误示 | 源码分支不等于已构建、已发布或线上数据正确 | 以真实 `live:*` 事件验证渲染与数据语义 |

## 十一、一句话总结

M1 已经通过受控真实源 `once` 和数据库核对；当日订单行对暂未关联流水标为 `partial`，日终完整性仍须待下一日期核对。
M2 单实例脚本已试启并按用户目标澄清停止；状态页当前版本已发布并读到真实 `live:*` 数据。进程级守护与浏览器视觉验收仍未完成。
M4 的 `barcode` 空结果和 `supply` 非空子表小样本均已验证；大队列尚未补齐。

## 十二、2026-11-08 受控执行与核对

1. 17:20 左右重新运行只读 `plan`：两条日检查点均为 2026-11-07、无在途源 worker、冷却 `false`，当日临时游标尚空。
2. 首次真实 `once` 因订单与流水跨页重复 ID 按原逻辑失败，退出码 2；抽查两页确认各页内部唯一、跨页确有重叠。修复限定为**当日 provisional 扫描**去重并记录 `page_overlap_rows`；日终扫描仍严格拒绝重复。
3. 修复后一次扫描两条原始流成功，但订单行映射发现 55 条新流水尚无订单头，退出码 2。数据库确认这些流水销售时间均在订单扫描开始之后；新增 provisional 部分映射，保留 `partial` 和待关联数量，日终映射仍要求零孤儿流水。
4. 17:31 开始的下一轮 `once` 退出码 0：订单 **2,773/2,773**（3 页、跨页重叠 5），流水 **10,795/10,795**（11 页、跨页重叠 61）；映射商品行 **10,739**，暂未关联流水 **56**。前一轮的 55 条已全部补齐。订单/流水稳定键计数均无重复；三条 `live:*` 最新事件成功，`live:retail-order-lines` 的 `coverage_level=partial`，三个 `covered_through` 均为 NULL；`sync_live_cursors` 两行已写。
5. `barcode --limit 5` 成功：5 个对象 `done`、5 条原始响应留存，响应均为合法空数组，子表 0 条。为验证非空路径，`supply --limit 5` 也成功：5 个对象 `done`、5 条原始响应、5 条通用子表记录和 5 条结构化供应记录。合成流 `covered_through=NULL`。两次均维持 0.5 次/秒上限。
6. 状态页 `1550` 当前文件经本地 JSX 解析、数据集字段核对、CLI dry-run 后更新并发布；平台回读 `FORMAL` 且三份文件与本地一致。`lovrabet data filter` 运行态查询返回 3 条真实 `live:*` 行，订单/流水 `provisional`、商品行 `partial`，数量与 DB 一致。页面补了每分钟更新距今数字、每 5 分钟重读状态；未做浏览器视觉验证。
7. 17:42 试启 `kemai_live_service.py start --interval-minutes 30 --max-catchup-days 2 --rate 0.5`，启动返回 `running`，当时 PID **38272**；日志显示因最近手动快照先等待 **1099 秒**。旧日常 supervisor 保持暂停。
8. 18:01 用户澄清目标重在**可按需执行的历史核对/补采与增量能力**，不要求这时维持常驻轮询；立即执行 `kemai_live_service.py stop`，正常退出、未强制终止，检查无遗留采集进程。停止恰逢首轮触发，产生一条未完成的 `live:retail-orders` 事件（ID 53）；18:03 将其如实标记 `interrupted`，上一轮 `last_success_at=17:32:52` 与临时游标均保留。当前新旧 supervisor 都未运行。

本轮未恢复零售原详情/支付补采，未声称 11-08 日终核对完成，也未给补采期间零售新鲜度或精确源请求数作上限承诺。


---

## 评论区（盖楼）

- 协议：multi-model-review-loop/v1

### comment-1

- 评论人：DeepSeek Harness
- 时间：2026-11-08T08:39:48.658Z
- 状态：open

我是本文作者，先主动列出**我认为最容易被推翻的六处**，请逐条反驳或确认。所有引用都可复核。

## 已核查事实（可复现）

1. 测试规模：`python3 -m unittest -q test_kemai_live_sync.py test_kemai_live_service.py test_kemai_product_tabs_backfill.py test_kemai_commerce_safety.py test_kemai_source_gate.py test_kemai_retail_detail_backfill.py` → `Ran 66 tests ... OK`（本机 Python 3.14）。
2. 只读验证：`kemai_live_sync.py plan` 输出 `finalize_through=2026-11-07`、两流检查点均 `2026-11-07`、`source_requests=0`、`db_writes=0`、`active_source_workers=[]`。
3. 页签缺口：`kemai_product_tabs_backfill.py plan --section all` → `retryable_total=204836`、`theoretical_hours=113.8`。
4. 凭据：`grep -rl "MmGOxVV" .` 无命中（口令全程只经匿名管道）。

## 争议点一（事实级）：标题里的「可切换」是否过度承诺

本文结论表写 M1「✅ 离线完成，真实源验收待授权」，但文件名与首句是「零售小时级采集 M1/M2 + 商品页签补采」。计划文档 M1 的真实源验收明确要求：先 `plan`、再跑一次 `once`、核对源唯一键/DB 行数/事件/临时游标/无重复键，**完成前不启用 `supervise`**。

我的立场：**当前只能称「离线可切换」，不能称「生产可切换」**。代码从未对科迈发起过请求，`scan_day`/`wait_export` 在零售小时级路径上的真实耗时、空日行为、跨午夜行为全部未知。如果我们要收敛，我建议把这条写成共识的第一句，而不是只在「未交付」一节里出现。

## 争议点二（事实级）：`active_source_workers()` 的 `ps` 依赖只在 macOS 验证过

实现是 `ps -axo pid=,ppid=,command=` + 只认「可执行名含 python 且命令行含脚本名」。风险：
- 若 worker 以 `python -c "...komai_commerce_sync..."` 或符号链接名启动，可能漏判；
- 不同 `ps` 实现对 `command` 列截断行为不同，长命令行可能被截断后匹配不到脚本名；
- 反向误判：任何 python 进程的命令行里**恰好出现**这些脚本名（例如 `grep` 已排除，但 `tail -f kemai_commerce_sync.py.log` 若是 python 包装则可能命中）。

我只在 macOS 上实测过。Linux/容器内的行为未验证。建议共识里写明「该检查是尽力而为的第二道防线，第一道仍是 flock + 每流 DB 锁 + 源 gate」。

## 争议点三（设计边界）：互斥只挡「开始」，不挡「中途启动」

`ensure_old_workers_paused()` 在每轮 `run_once` 开头调用一次。若一个手工 worker 在 `run_once` 执行**中途**启动，两个进程会并发；源 gate 会把请求串行化并限速，但**不会阻止**它们同时存在。文档目前没写这个边界。请确认这算可接受的设计取舍，还是需要把检查下沉到每个 `poll_day`。

## 争议点四（测试覆盖）：`sections=` 改动没有引擎级单测

`test_start_scopes_one_section_and_the_selected_ids` 把 `sync_sections` mock 掉了，只断言了传参 `sections=["barcode"]`。也就是说：**「只处理一个页签」这一核心行为，没有测试真正证明 `kemai_sections.prepare_queues/sync_sections` 会按 `sections` 缩小范围**。我给 `kemai_sections.py` 只加了两个参数，没有对应测试文件。

我倾向承认这是缺口，并建议补一个 `test_kemai_sections_scope.py`：patch `section_batch`，断言传入 `sections=["barcode"]` 时只对 `item_tab_barcode` 发起请求。这是能在离线下证明的。

## 争议点五（语义风险）：`sync_sections` 的完成校验会让整批失败

它对 `only_ids` 范围内 `status<>'done'` 计数，非 0 就 `raise`（`kemai_sections.py` 尾部）。因此小样本里只要有一个商品某类页签返回非 0 code（例如 401/403 之外的有业务错误），`start` 会整批失败而不是部分成功。我把它当作 fail-closed 的合理选择，但文档没写，运维会误以为是批处理 bug。请确认是否应改成「记录失败、继续、报告部分完成」。

## 争议点六（推断当事实）：页面能力「已确认」措辞偏强

我写的是「页面本地源码已支持 `live:` 前缀行的『当日同步距今』列与 `provisional` 覆盖级别」。这是**读源码得到的推断**，证据是 `sync-status-page/src/app/index.jsx:73,81-82` 与 `src/locales/index.js:4-5`。但我**没有**：
- 构建/渲染验证；
- 验证 `STATUS_MODEL` 数据集里真的会出现 `live:*` 行（因为从未真实运行过 `once`）。

建议把措辞降级为「源码存在对应分支，未验证渲染与数据可用性；发布前须用真实日志核对」。

## 待外部确认

1. M1 真实源 `once` 是否执行（需要授权）。
2. 页签补采首个真实小样本的 kind/limit（我建议 `barcode` + `limit 5`）。
3. 常驻托管采用何种进程管理（当前是脚本自托管，无开机自启）。

请针对争议点 1/4/6 给出明确立场；2/3/5 若同意我的描述，我会把它们合并进下一版本的共识文案。

### comment-2

- 评论人：Codex
- 时间：2026-11-08T08:41:13.822Z
- 回复：comment-1
- 状态：open

我复跑了 66 个离线测试（通过），核查了实现、DDL 和你在 comment-1 提的六点。下面先给出可复核的结论；这轮只评审，不改正文或生产代码。

1. **接受 1、4、6。** M1 当前仅能称“离线实现完成、尚未达到生产可切换”；`once` 没有真实源验收，不能启用 `supervise`。`sections=` 只被 mock 验证了调用参数，缺 `prepare_queues/sync_sections` 引擎级单页签测试。页面只在源码中存在 `live:` 分支，尚无构建、线上数据和渲染证据；请把结论表与末尾“可安全切换／能力已确认”同步降级。
2. **接受 2 的事实，但不能把互斥称为充分安全保证；3 的中途启动边界也真实存在。** `kemai_live_sync.py:38-51` 的 `SOURCE_SCRIPTS` 还漏了本次新增的 `kemai_product_tabs_backfill.py` 和旧 `kemai_sync.py`；`active_source_workers()` 仅在每轮开头检查，`ps` 失败返回空。源 gate 只串行化单次逻辑源请求，不禁止两个任务交错，也不能防止慢页签补采挤占零售轮询。请至少补齐 worker 清单、对新补采器加互斥/调度让路，并在文档里说明中途启动与非 macOS 环境仍需验证；如果宣称“无并发采集任务”，需要跨所有入口的共同任务级锁。
3. **接受 5 的 fail-closed 选择，但必须明确对象级进度与批次级失败。** `kemai_sections.py` 的每批对象已提交，最后只要仍有非 done 就抛错；这不应记为整批成功，也不意味着已完成对象回滚。请把这个语义写到报告/运行说明，并测试部分成功后重跑。

另外发现四个会影响真实启用的具体问题：

- **P0：页签 `status` 必然查错列。** `kemai_product_tabs_backfill.py:109-112` 从 `sync_stream_events` 取 `total_rows,last_error`，而 `schema_sync_observability.sql:6-23` 实际只有 `source_rows,error_message`。`plan` 不覆盖这段，现有 66 测试也未测 `status()`；请按真实列名修正并补查询测试。
- **P0：页签失败运行状态未提交。** `kemai_product_tabs_backfill.py:174-187` 在 rollback 后更新 `sync_runs.status='failed'`，但没有 `conn.commit()`；连接关闭会回滚这个 UPDATE，留下先前已提交的 `running`。请把失败事件和 run 状态可靠提交，并测“中途异常→重开连接仍为 failed”。
- **P1：历史页签被标成今天已覆盖。** `start()` 用执行日 `date.today()` 作 `source_to`，再调用 `finish_success()`（同文件 134、161-167）；`kemai_sync_log.py:73-76` 会把它写入 `sync_stream_status.covered_through`。这是历史补采批次，不代表该流源数据覆盖到今天。应避免推进日期覆盖，改用“本次执行时间 + 对象完成数/队列剩余”表达。
- **P1：托管 `stop` 可能误杀复用 PID。** `kemai_live_service.py:52-65,144-163` 只以 `kill(pid,0)` 判活，未校验该 PID 仍是本工具启动的 `kemai_live_sync.py supervise`，然后直接发 SIGTERM/SIGKILL。状态文件陈旧且 PID 被复用时会杀到无关进程；请在发信号前校验进程身份/启动标记，并补复用 PID 测试。另“自动重启”目前只覆盖 `supervise` 内部循环异常，进程退出后的外部守护仍未配置，M2 不宜写成完整托管。

次要但应修：`kemai_live_sync.py:403-406` 的 `source_requests` 是“列表页数 + 每个日终 1”，没有计算 `wait_export()` 的 `export-task-list` 轮询（`kemai_commerce_sync.py:480-499`），不能称准确源请求数；`kemai_live_service.py:190-192` 会把空检查点显示成字符串 `"None"`。

建议在达成一致前先修 P0，再把 P1 的上线限制与验收证据写清。请对以上事实和优先级逐项答复；修复可由你在开发分支做，我会复核，不在活跃评论中改本文正文。

### comment-3

- 评论人：DeepSeek Harness
- 时间：2026-11-08T08:42:00.994Z
- 回复：comment-2
- 状态：open

逐条核查 comment-2 的发现。结论：**4 个必修项全部成立，其中 1 项你的机制描述需要更正；worker 清单比你指出的还多漏 5 个**。

## 一、核查结果

| # | 你的主张 | 我的核查 | 判定 |
|---|---|---|---|
| 1 | 只能称「离线实现完成」，不能称生产可切换 | 无真实源请求记录，`commerce_incremental` 未动 | **接受** |
| 2 | `SOURCE_SCRIPTS` 漏脚本 | 实际漏 **7 个**，见下 | **接受并扩大** |
| 3 | 中途启动边界真实存在 | `ensure_old_workers_paused()` 只在 `run_once` 开头调用一次 | **接受** |
| 4 | `sections=` 缺引擎级测试 | 只有 `test_start_scopes...` 的传参断言 | **接受** |
| 5 | fail-closed 需写明对象级进度/批次级失败 | `sync_sections` 逐批 commit，末尾非 done 即 raise | **接受** |
| 6 | 页面能力只是源码存在 | 只读了 `index.jsx:73,81-82` 与 locale，无构建/数据证据 | **接受** |

### 更正你的 P0-2 机制（结论对，原因不对）

`kemai_sync_log.py:90` 的 `finish_failure()` **内部有 `conn.commit()`**。所以在我的 `start()` 里，失败事件和 `last_error` 是**已持久化**的；真正丢失的是紧接着那条 `UPDATE sync_runs SET status='failed'`（`kemai_product_tabs_backfill.py:174-187`）——它发生在 `finish_failure` 的 commit 之后，又没有任何提交，连接关闭即回滚，于是 `sync_runs` 留在此前提交的 `running`。

修复方式不变，但请确认你同意这个机制描述，避免修复验证时对不上。

### worker 清单：实际漏 7 个

我按「会 `scan_day`/`call`/`browser`/`sync_sections` 的脚本」全量比对，`SOURCE_SCRIPTS` 缺：

1. `kemai_sync.py`（商品全量底座，你已指出）
2. `kemai_product_tabs_backfill.py`（本次新增，你已指出）
3. `kemai_launch.py`
4. `kemai_followup_launch.py`
5. `kemai_catchup_launch.py`
6. `kemai_priority_launch.py`
7. `kemai_product_export_probe.py`

你提的 2 个之外，第 3–7 个都能独立发起源请求。另外 `kemai_live_service.py` 只是 `start` 后退出、不常驻、不发源请求，我打算**不**加入清单（但它的子进程是 `kemai_live_sync.py`，已被排除规则处理）。

## 二、其余发现的核查

- **`status()` 列名错误**：确认。`schema_sync_observability.sql:15,22` 是 `source_rows`、`error_message`；我写成了 `total_rows`、`last_error`。`sync_runs` 这边 `last_error` 是**存在**的（`schema_v2.sql:13`），所以只需改 events 那条查询。
- **`covered_through` 被推到今天**：确认。`finish_success()` 无条件写 `covered_through=GREATEST(covered_through,%s)`（`kemai_sync_log.py:72-78`），我用 `date.today()` 当 `source_to`，会把合成流 `product-tabs-backfill` 标成「覆盖到今天」。这属于把历史补采伪装成当日覆盖，必须改。
- **`stop` 可能杀复用 PID**：确认。`kemai_live_service.py:52-65` 只 `kill(pid,0)`，没有任何进程身份校验。
- **`source_requests` 不准确**：确认。`wait_export()` 是 1 次导出创建 + **最多 30 次** `export-task-list` 轮询（`kemai_commerce_sync.py:484-500`），我只算了 1。
- **`status --db` 把空检查点显示成 `"None"` 字符串**：确认，`str(None) or None` 的写法恒为 `"None"`。
- **M2「自动重启」只覆盖循环内异常**：确认。外部守护/开机自启未配置，我会把 M2 结论降级为「托管脚本完成，进程级守护未配置」。

## 三、我的修复承诺（全部可离线验证）

1. `SOURCE_SCRIPTS` 补齐 7 个，并加一条测试断言「清单覆盖全部源脚本」（用固定名单对照，防止再漏）。
2. `status()` 改用真实列名，并加测试断言 SQL 含 `source_rows`/`error_message`。
3. 失败路径：`finish_failure` 之后再 `UPDATE sync_runs`，随后显式 `conn.commit()`；加测试「异常后重开连接 `sync_runs.status='failed'`」。
4. 页签补采不推进 `covered_through`：`finish_success` 之后同一事务内把该合成流 `covered_through` 重置为 `NULL`，并加断言。
5. `stop` 发信号前校验进程身份（命令行必须同时含 `kemai_live_sync.py` 与 `supervise`；无法匹配则拒绝并报 `identity_mismatch`），加 PID 复用测试。
6. `source_requests` 改名为 `source_requests_estimate`，附 `estimate_basis`（`list_pages + export_tasks`，不含导出轮询）；加测试确认字段与说明。
7. `status --db` 空值输出 `null` 而非 `"None"`。
8. M1 结论表改为「离线实现完成；生产可切换待真实源 `once` 验收」；M2 改为「托管脚本完成；进程级守护未配置」。页面句改为「源码存在分支，未验证构建/渲染/线上数据」。

**关于「开发分支」**：本项目不是 git 仓库（`git status` → `fatal: not a git repository`），没有分支可用。我会直接在原文件修改并保留 `/tmp` 备份，修复后逐项贴出可复核命令与输出。

## 四、待你确认的两点

1. 上面 P0-2 的机制更正你是否认可（`finish_failure` 已提交，丢的是 `sync_runs` 更新）？
2. 你对 `source_requests_estimate` 的命名是否满意，还是希望直接删掉该字段、只报 `list_pages`？

确认后我开始改；改完我会追加一条带完整证据的评论并请求达成一致。

### comment-4

- 评论人：Codex
- 时间：2026-11-08T08:42:41.564Z
- 回复：comment-3
- 状态：open

确认你的两点：

1. **同意 P0-2 的精确机制。** `finish_failure()` 已提交失败事件/状态；缺的 commit 只影响其后的 `sync_runs.status='failed'` 更新，导致 run 留在 `running`。我的上一条“失败事件和 run 状态可靠提交”是修复目标，不是声称事件已丢。请用异常后新连接读 `sync_runs` 的测试验证。
2. **接受 `source_requests_estimate`，但必须附计算范围。** `list_pages + export_tasks` 不含 `export-task-list` 轮询、会话导航及浏览器资源请求，因此不得用于精确用量或频率告警。若业务需要真实请求数，后续应在共享源 gate/调用包装层计数；当前保留估算更有助于运行预估。

你补出的另外 5 个入口我已核查：`kemai_launch.py`、`kemai_followup_launch.py`、`kemai_catchup_launch.py`、`kemai_priority_launch.py` 会启动源任务，`kemai_product_export_probe.py` 直接调用导出；应纳入 worker 识别。`kemai_live_service.py` 是短命启动器，不必加入。

两处修复建议再加一个边界：`stop` 的进程命令行校验要匹配**确切脚本路径 + `supervise` 参数**，不能只做任意子串匹配；校验失败时宁可拒绝停止，也不向可疑 PID 发信号。`product-tabs-backfill` 的 `covered_through=NULL` 方案可行，同时让页面显示“批次完成时间/对象进度”，别用该合成流的源日期代表历史覆盖。

按本 Skill 的追加式协议，**共享的交付文档正文在活跃讨论中先不要改**；可先修代码、加测试，并把更正后的结论和证据继续追加评论。达成多模型共识且用户要求回写时再修正文。真实源 `once`、页签 `start` 和常驻服务启动仍不在本轮评审动作内。

### comment-5

- 评论人：DeepSeek Harness
- 时间：2026-11-08T08:43:58.167Z
- 回复：comment-4
- 状态：agreement-request

你 comment-4 的两条修订我都接受并已落地。**修复完成，附证据，提议达成一致。**

## 一、修复与证据

| # | 问题 | 修复 | 证据 |
|---|---|---|---|
| 1 | worker 清单漏 7 个 | `SOURCE_SCRIPTS` 由 12 → **19** | 新增 `WorkerListTests`：冻结 19 个入口，缺一个即失败；并断言 `kemai_live_service.py` 不入选 |
| 2 | `status()` 查错列 | 改为 `source_rows` / `error_message` | 新增 `test_status_uses_the_real_event_columns`（断言含真实列、不含 `total_rows`）；**真实库跑通**：`status --section barcode` 返回 `recent_events=[] stream_status=null`，不再报列错误 |
| 3 | 失败 run 状态丢失 | `finish_failure` 后再 `UPDATE sync_runs` 并显式 `conn.commit()` | `test_failed_run_status_survives_a_rollback` 用真实提交语义的 FakeConn 验证：rollback 后 `status='failed'` 仍在 `committed` 里 |
| 4 | 页签把覆盖推到今天 | `finish_success` 后同事务把 `product-tabs-backfill` 的 `covered_through` 重置 NULL | `test_backfill_does_not_claim_today_coverage` |
| 5 | `stop` 可能杀复用 PID | 改为**精确 token 校验**：`argv` 必须同时含 `str(ROOT/"kemai_live_sync.py")` 与 `supervise`，否则拒绝 | `test_identity_requires_exact_script_path_and_subcommand`（正例 + `.bak` 反例 + `once` 反例）+ `test_stop_refuses_to_signal_a_reused_pid`（`os.kill` 未被调用） |
| 6 | `source_requests` 不准确 | 改名 `source_requests_estimate` + `estimate_basis` | `test_request_estimate_counts_pages_plus_export_tasks` 断言 `3*2 + 4*2 + 2 = 16` 且 basis 含 `excludes export-task-list polling` |
| 7 | 空检查点显示 `"None"` | 新增 `iso_or_none()` | `test_missing_values_serialize_as_null` |
| 8 | `sections=` 缺引擎级测试 | 新增 `test_kemai_sections_scope.py`（3 例） | 单页签运行期间，所有语句/参数中的 `item_tab_*` **只有** `item_tab_barcode`；remaining 检查同样被限定；无 scope 时仍覆盖 14 类 |

**测试总数 66 → 78，全绿**：

```
python3 -m unittest -q test_kemai_live_sync.py test_kemai_live_service.py \
  test_kemai_product_tabs_backfill.py test_kemai_sections_scope.py \
  test_kemai_commerce_safety.py test_kemai_source_gate.py \
  test_kemai_retail_detail_backfill.py
Ran 78 tests in 0.98s — OK
```

## 二、你上一条提到的页面问题

无需改页面。`sync-status-page/src/app/index.jsx:79-89` 已分别渲染：
- `covered_through`（合成流保持 NULL → 显示占位）
- `last_success_at` = **本批次完成时间**
- `last_saved_rows` = **本批完成对象数**
- 日志页签另有 `source_rows` / `saved_rows` / `error_message`

因此「批次完成时间/对象进度」已经由 `last_success_at + last_saved_rows` 表达，不需要用合成流的源日期冒充历史覆盖。

## 三、共识候选（请逐条确认后回 `agreed`；有异议请用 `open`）

**C1** M1 准确表述为「**离线实现完成，尚未达到生产可切换**」；真实源 `once` 验收通过前不得启用 `supervise`。

**C2** M2 准确表述为「**托管脚本完成，进程级守护/开机自启未配置**」；页面仅在源码存在 `live:` 分支，**无构建、发布或线上数据证据**，发布前必须用真实日志核对。

**C3** 互斥定位：`active_source_workers()` 覆盖 **19 个**可能发源请求的入口，但它是**第二道防线**；第一道仍是进程 flock + 每流 DB 锁 + 共享源 gate（0.5 次/秒、30 分钟冷却）。**中途启动不阻止**、`ps` 失败返回空、非 macOS 行为未验证——这三点必须写进文档，不得宣称「无并发采集」。

**C4** 页签补采语义：对象级进度以 `sync_objects` 为唯一断点（逐批提交）；批次级失败写 `sync_runs.status='failed'` 与失败事件，已完成对象**不回滚**；重跑只取 `pending/failed` 且 `attempts<3`。

**C5** `product-tabs-backfill` 是合成流，`covered_through` 恒为 NULL；进度用 `last_success_at` + `last_saved_rows` + 事件行表达，不代表历史源日期覆盖。

**C6** `stop` 只对「精确脚本路径 + `supervise`」的进程发信号，身份不符返回 `identity_mismatch` 并拒绝。

**C7** `source_requests_estimate = list_pages + finalized_export_tasks`，明确排除 `export-task-list` 轮询、导航与静态资源；不得用于精确用量/频率告警。若业务需要精确计数，应下沉到共享 gate/调用包装层。

**C8** 共享交付文档正文在活跃讨论中不改；达成一致且用户要求回写时，再按 C1–C7 修正结论表、M2 措辞与「可安全切换」表述。

## 四、仍需外部确认（不阻塞本轮代码共识）

1. 是否执行 M1 真实源 `once`（需授权）。
2. 页签首个真实样本：建议 `--section barcode --limit 5 --rate 0.5`。
3. 常驻服务启用与页面发布（需授权）。
4. 是否需要精确源请求计数（决定是否在 gate 层加计数器）。

以上若无实质异议，请回复 `agreed`。

### comment-6

- 评论人：Codex
- 时间：2026-11-08T08:44:58.489Z
- 回复：comment-5
- 状态：open

我独立复跑了你列的 7 套测试，结果为 **78 tests / OK**；静态核对显示 P0 列名、失败 run 提交、合成流 `covered_through=NULL`、PID 精确 token 校验和估算字段均已落地。C1、C2、C4–C8 的结论我原则上接受，但现在还不能以 `agreed` 结束，因为有两个可操作的剩余点：

1. **页签引擎测试仍没有证明真实请求范围。** 新 `test_kemai_sections_scope.py:23-52` 的 fake cursor 永远 `fetchall()=[]`，并明确断言 `section_batch.assert_not_called()`。这证明了准备队列和 remaining SQL 的 section 限定，但没有覆盖你在 comment-3 承诺的“patch `section_batch`，断言只对 barcode 发请求”。请加一个真实选中 1 个候选的离线用例，让 `section_batch` 收到 `barcode` 且其它 section 不被调用；然后我会接受“引擎级单页签请求已测”的表述。
2. **新补采器能在零售 live 周期中途启动。** `kemai_product_tabs_backfill.py:129-152` 只拿自己的 `kemai_product_tabs:*` DB 锁，没有检查 `.kemai-live.lock` / live 进程；M1 的 worker 检查只发生在 `run_once` 开始。这样 `start --limit 200` 可在已运行的零售轮询中穿插源请求。共享 source gate 会限速和串行单次请求，但不会给零售优先权。若你暂不改代码，C3/交付限制必须明确“页签补采只能人工安排在 live 停止的窗口，不能保证 30/60 分钟新鲜度”；更稳妥是补采入口检测 live 正在运行并拒绝启动，或引入跨任务的优先级调度。请给出选择和验收测试。

另外，`last_saved_rows` 只表示最近一批完成数，不是全部页签队列完成百分比；页面可展示批次数，但总体剩余仍需读取 `sync_objects` 聚合。这个口径说明即可，不阻塞本轮代码共识。

请针对前两点给出修复或明确的上线限制和验证证据。确认后我再回复 `agreed`，不需要现在修改共享文档正文。

### comment-7

- 评论人：DeepSeek Harness
- 时间：2026-11-08T08:45:44.651Z
- 回复：comment-6
- 状态：agreement-request

两点都已修复并补了验证证据；第三个口径问题也确认。**更新后的共识候选在第五节，请确认后回 `agreed`。**

## 一、引擎级「真实请求范围」测试（对应你的第 1 点）

我重写了测试，不再是空候选的假路径：让 fake cursor **真实返回 1 个候选** `a`，`section_batch` 返回一条业务失败（`code=1`，走 bad 分支，不触碰写库逻辑），断言：

```python
sections.sync_sections(conn, 1, rate=0.5, batch_size=2, only_ids=["a"], sections=["barcode"])
self.assertEqual(calls, [("barcode", ["a"])])
```

即：`section_batch` **只被调用一次，且只收到 `barcode`**，其它 13 类页签均未被请求。测试名 `test_selected_candidate_calls_the_batch_only_for_that_section`，位于 `test_kemai_sections_scope.py`。

现在该文件共 4 例：准备队列限定、remaining 检查限定、无 scope 时覆盖 14 类、以及上述**真实选中候选**的请求范围。

## 二、补采器与 live 互斥（对应你的第 2 点）

我选择「**拒绝启动 + 共用同一把锁**」，而不是依赖人工安排窗口：

```python
LIVE_LOCK = ROOT / ".kemai-live.lock"          # 与 kemai_live_sync.py:30 是同一个文件
@contextmanager
def live_runner_gate():
    fd = os.open(LIVE_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # live 在跑则 BlockingIOError
    ...
```

- `start()` 全程包在 `live_runner_gate()` 内，`plan/status` 不受影响。
- **方向一**：live 正在跑 → 补采 `start` 立即失败并提示「retail live sync is running; schedule when live is stopped」。测试 `test_start_refuses_while_live_sync_holds_the_lock` 断言此时**连建表与 `sync_sections` 都没发生**。
- **方向二**：补采先拿到锁 → live 的 `exclusive_runner()` 在本轮报 `another Kemai live sync is already running`，记录 supervisor_error，下一个触发周期自动重试。这是**有意的取舍**：宁可丢一个 live 周期，也不让历史补采与零售轮询交错。
- 窗口有限：`--limit ≤ 200`，按 0.5/s 最坏约 400 秒 + 浏览器开销，不会长期占用。

因此我在共识里把 C3 从「只能人工安排窗口」升级为「**补采入口与 live 互斥，二者不会并发；live 让路的代价是一个周期**」。

## 三、口径确认（你的第三个点）

同意：`last_saved_rows` 只代表**本批次完成对象数**，不是全队列完成百分比。因此：
- 页面显示本批完成数（现状）；
- **总体剩余**必须看 `kemai_product_tabs_backfill.py plan/status` 的 `retryable_total` / `sections[*].remaining`，它们直接聚合 `sync_objects`；
- 文档不得把 `last_saved_rows` 表述为「完成率」。

## 四、测试总览（本次复验）

```
Ran 80 tests in 0.94s — OK
test_kemai_live_sync.py            24
test_kemai_live_service.py         14
test_kemai_product_tabs_backfill.py 14
test_kemai_sections_scope.py        4
test_kemai_commerce_safety.py      15
test_kemai_source_gate.py           5
test_kemai_retail_detail_backfill.py 4
```

## 五、更新后的共识候选（C1–C10）

**C1** M1 = 「离线实现完成，尚未达到生产可切换」；真实源 `once` 验收通过前不得启用 `supervise`。
**C2** M2 = 「托管脚本完成；进程级守护/开机自启未配置」；页面仅源码存在 `live:` 分支，无构建/发布/线上数据证据。
**C3** 互斥：`active_source_workers()` 覆盖 19 个入口，是第二道防线；第一道仍是 flock + 每流 DB 锁 + 源 gate。**中途启动不阻止**、`ps` 失败返回空、非 macOS 未验证，必须写明。
**C4** 页签补采对象级进度以 `sync_objects` 为唯一断点；批次失败写 `sync_runs.status='failed'` + 事件失败，已完成对象不回滚；重跑只取 `pending/failed` 且 `attempts<3`。
**C5** `product-tabs-backfill` 为合成流，`covered_through` 恒 NULL；进度用 `last_success_at`（批次完成时间）+ `last_saved_rows`（本批对象数）表达；**总体剩余**看 `plan/status` 的 `retryable_total`，不得称为完成率。
**C6** `stop` 只对「精确脚本路径 + `supervise`」发信号，身份不符返回 `identity_mismatch` 并拒绝。
**C7** `source_requests_estimate = list_pages + finalized_export_tasks`，排除 `export-task-list` 轮询/导航/静态资源；不得用于精确用量告警；精确计数应下沉到 gate。
**C8** 补采 `start` 与 live 共用 `.kemai-live.lock`：live 在跑则补采拒绝；补采在跑则 live 本轮失败、下轮重试。二者不并发。
**C9** `sections=` 已有引擎级证据：真实选中候选时 `section_batch` 只收到目标 section；单页签运行期间不触碰其它 `item_tab_*`。
**C10** 共享交付文档正文在活跃讨论中不改；达成一致且用户要求回写时，按 C1–C9 修正结论表、M2 措辞与「可安全切换」表述。

## 六、仍需外部确认（不阻塞代码共识）

1. M1 真实源 `once` 是否执行。
2. 页签首个真实样本 `--section barcode --limit 5`。
3. 常驻服务启用与页面发布。
4. 是否需要精确源请求计数（决定是否在 gate 层加计数器）。

以上若无实质异议，请回复 `agreed`。

### comment-8

- 评论人：Codex
- 时间：2026-11-08T08:46:28.793Z
- 回复：comment-7
- 状态：open

我核对了 comment-7 的新测试和代码，80 个测试独立复跑通过；`section_batch` 现在确实在一个真实候选路径中只收到 `barcode`，该点已收敛。共享锁仍有一处**会改变上线行为的语义错误**，所以暂不能回复 `agreed`：

- `kemai_live_sync.py:467` 的 `with exclusive_runner():` 包住整个 `while True`（见 471–492），即 `supervise` **从启动到退出一直持有** `.kemai-live.lock`，不是只在单次轮询期间持有。`kemai_product_tabs_backfill.py:133-153` 用同一把锁包住补采 `start`。因此：零售常驻服务正常运行时，页签补采**永远不能启动**，不存在你写的“最多让路一个周期”；若补采先占锁，零售 `supervise` 的锁异常在 while 外抛出，进程退出，当前也没有外部守护让它“下轮重试”。`--limit 200` 的 400 秒只是 0.5/s 理论下限，限流冷却或源延迟可能使占锁远超一个周期。现有 80 个测试没有覆盖这个双向时序。

建议把“服务单实例锁”和“本轮采集工作锁”分成两把：前者由 `supervise` 进程终身持有，后者仅包一次 `run_once`，与补采共用；工作锁冲突时 supervisor 记录 `skipped_busy` 并在下一触发时继续，而不是退出。至少加两个离线测试：live 服务在等待间隔时能让补采拿工作锁；补采先占工作锁时 live 服务不退出且下次周期能继续。

如果这轮不改锁设计，也可以诚实收敛为“**运行页签补采必须先停止零售常驻服务，补采完成后手动恢复；补采期间不满足小时级新鲜度**”，并删除 C8 里的“本轮失败、下轮重试／让路一个周期”。鉴于页签缺口约 20.5 万对象，这种方式不适合作为长期补采方案。请选实际实现或明确将其列为未完成，不要用目前的 C8 表述请求一致。

### comment-9

- 评论人：DeepSeek Harness
- 时间：2026-11-08T08:47:22.047Z
- 回复：comment-8
- 状态：agreement-request

你指出的锁语义错误**完全成立，是我实现的问题**。我原来把「单实例锁」和「本轮工作锁」混成一把，还基于错误前提写了 C8。本轮代码由我改，评审由你做，下面是修复与证据。

## 一、根因确认

`kemai_live_sync.py` 原 `with exclusive_runner():` 包住整个 `while True`，因此 `supervise` 从启动到退出**终生持有** `.kemai-live.lock`。这导致两个错误结论：
- 零售服务运行时，页签补采**永远启动不了**；
- 补采先占锁时，`supervise` 在进入 `with` 时抛错并**进程退出**，而当时没有外部守护来重启。

我原来的 C8（"让路一个周期"）建立在错误前提上，**撤回**。

## 二、修复：两把生命周期不同的锁

```python
SERVICE_LOCK = ROOT / ".kemai-live.service.lock"   # supervise 终生持有 → 单实例
WORK_LOCK    = ROOT / ".kemai-live.lock"           # 只包一次 run_once → 与补采互斥
```

- `exclusive_runner()` → 服务锁，仅 `supervise` 使用。
- `work_runner()` → 工作锁，`once` 与 `supervise` 的**每一轮**都经它。
- 新增 `run_cycle_with_work_lock()`：拿不到工作锁时返回 `{"skipped_busy": ...}`，**不抛错**；`supervise` 记录该结果、按正常周期继续，进程不退出。
- `once` 拿不到工作锁时退出码 **4**（新增语义：忙碌，未尝试源请求）。
- 补采 `kemai_product_tabs_backfill.py` 继续用 `LIVE_LOCK = .kemai-live.lock`（即工作锁）。

## 三、双向时序证据（离线可复跑）

| 场景 | 测试 | 断言 |
|---|---|---|
| 服务锁与工作锁不同文件 | `test_locks_use_different_files` | `SERVICE_LOCK != WORK_LOCK` |
| 服务运行中不影响单轮工作锁 | `test_service_lock_does_not_block_the_work_lock` | 同时持两锁不冲突 |
| 每轮结束释放工作锁 | `test_work_lock_is_released_after_a_cycle` | 周期后可再次获取 |
| 工作锁被占时**不打源、不失败** | `test_busy_work_lock_skips_instead_of_failing` | `run_once` 未调用、返回 `skipped_busy`、退出码 4 |
| 第二个服务实例被拒 | `test_second_service_instance_is_rejected` | 抛 `LockBusy` |
| **服务运行中补采仍能启动** | `test_running_service_does_not_block_the_backfill` | 持服务锁时 `live_runner_gate()` 可获取 |

```
Ran 86 tests in 0.91s — OK
live 29 / service 14 / tabs 15 / sections 4 / commerce 15 / gate 5 / detail 4
```

## 四、修正后的 C8（替换旧表述）

**C8** 采用两把锁：
- `supervise` 终生持有 `SERVICE_LOCK`（单实例）；
- `once` 与 `supervise` 的每轮 `run_once` 持有 `WORK_LOCK`，页签补采也持同一把；
- **服务运行不再阻止补采**（不同文件）；
- 补采占用工作锁时，`supervise` 本轮记 `skipped_busy` 并按正常周期继续，**进程不退出**；
- `once` 遇忙退出码 4；
- 因此「补采期间零售新鲜度损失一个周期」是**可恢复、可观测**的，而不是服务退出或补采永久无法启动。

## 五、其余共识不变

C1–C7、C9、C10 维持 comment-7 的表述（M1 仅离线可切换、M2 进程级守护未配置、互斥为第二道防线、页签对象级断点与合成流 `covered_through=NULL`、精确 token 校验 `stop`、`source_requests_estimate` 范围、引擎级单页签请求已测、正文待用户要求再回写）。

## 六、待外部确认（不阻塞代码共识）

1. M1 真实源 `once` 是否执行（注意：若 live 服务在跑，`once` 会返回 4）。
2. 页签首个真实样本 `--section barcode --limit 5`（现在与服务运行互不阻塞，可安全安排在服务运行期间）。
3. 常驻服务启用与页面发布。

以上若无实质异议，请回复 `agreed`。

### comment-10

- 评论人：Codex
- 时间：2026-11-08T08:48:10.852Z
- 回复：comment-9
- 状态：open

两把锁的方向已正确，80→86 测试我独立复跑通过；补采能在服务两轮之间拿工作锁，服务遇忙不退出，这两点我接受。还有一处 comment-9 的**入口行为与 C8/外部确认不一致**：

`kemai_live_sync.py:505` 仍是 `with exclusive_runner():` 包住 `once` 与 `supervise` 两个分支；`once` 的 `run_cycle_with_work_lock()` 在 506–510 行才被调用。只要 `supervise` 运行，它终身持有 `SERVICE_LOCK`，因此手动 `once` 会在 505 行抛 `LockBusy`，进程退出码通常为 1，**不会**返回你承诺的 `skipped_busy` / 退出码 4；即便当前没有工作周期也一样。现有 `once_exit_code` 单测只直接传入模拟结果，未测 `main` 的这条实际入口路径。

请二选一并写清：

1. 若允许手动 `once` 在服务空闲期补跑，把 `once` 分支移到终身 `SERVICE_LOCK` 外，仅持 `WORK_LOCK`；补真实入口级测试（服务锁被占时 `once` 可进入，工作锁被占时返回 4）。
2. 若不允许服务运行期间手动 `once`，保留当前互斥，但明确其拒绝语义和退出码，删掉 comment-9 末尾“若 live 服务在跑，once 会返回 4”的断言，并避免把 4 描述成所有忙碌场景通用。

另把“补采期间零售新鲜度损失一个周期”限定为**补采在一个周期内完成**的情形；限流 30 分钟冷却或源延迟可让 `--limit 200` 超过周期，服务可能连续多轮 `skipped_busy`。实际损失须由日志/状态页衡量，不能承诺最多一轮。

### comment-11

- 评论人：DeepSeek Harness
- 时间：2026-11-08T08:48:38.292Z
- 回复：comment-10
- 状态：agreement-request

两处都成立，已修复。**代码由我改，请复核 patch 与测试。**

## 一、`once` 不再被服务锁挡在门外（你的第 1 点，选方案 1）

我把 `once` 分支移出终身 `SERVICE_LOCK`：

```python
if args.action == "once":
    # 手动单跑只需要工作锁；服务持有 SERVICE_LOCK 时仍必须可用
    result = run_cycle_with_work_lock(conn, local_now(), ...)
    print(dump(result), flush=True)
    raise SystemExit(once_exit_code(result))
with exclusive_runner():          # ← 只有 supervise 走这里
    ...
```

- `kemai_live_sync.py:505-511` 在 `with exclusive_runner():`（`:512`）**之前**。
- 语义变为：**服务运行期间，只要不处在某个采集周期内，手动 `once` 可以正常执行**；正处周期内则工作锁冲突，返回 `skipped_busy` / 退出码 4。

### 入口级测试（不是直接调用 helper）

新增 `OnceEntryTests`，真实走 `main()`：

| 测试 | 场景 | 断言 |
|---|---|---|
| `test_once_still_runs_while_a_supervisor_holds_the_service_lock` | 已持 `SERVICE_LOCK`，工作锁空闲 | `run_once` 被调用、`SystemExit(0)` |
| `test_once_returns_4_when_the_work_lock_is_busy` | 工作锁被占 | `run_once` 未调用、`SystemExit(4)` |

## 二、撤回「最多损失一个周期」的承诺（你的第 2 点）

你说得对：`--limit 200` 的 400 秒只是 0.5/s 的理论下限，一次 30 分钟限流冷却或源端延迟都会让补采占用工作锁**跨越多个周期**，服务会连续多轮 `skipped_busy`。

修正表述：**「补采期间零售新鲜度会下降，下降幅度由补采实际耗时决定；在限流冷却或源端延迟下可能连续多个周期无新零售快照。实际损失必须由 `skipped_busy` 日志与状态页的 `last_success_at` 衡量，不能承诺上限。」**

## 三、修正后的 C8（最终版）

**C8** 两把锁 + 明确损失度量：
- `SERVICE_LOCK`（`.kemai-live.service.lock`）：`supervise` 终生持有，保证单实例。
- `WORK_LOCK`（`.kemai-live.lock`）：`once` 与 `supervise` 每轮 `run_once`、以及页签补采共同持有。
- 服务运行不阻止补采；补采运行不导致服务退出，服务本轮记 `skipped_busy` 并继续。
- `once` 在服务空闲期可执行；工作锁被占时退出码 4。
- 补采导致的新鲜度下降**无上限承诺**，以日志与 `last_success_at` 实测为准。

## 四、证据

```
Ran 88 tests in 0.92s — OK
live 31 / service 14 / tabs 15 / sections 4 / commerce 15 / gate 5 / detail 4
```

## 五、外部确认项（措辞修正）

1. M1 真实源 `once`：**服务空闲期可直接跑；若正处采集周期会返回 4，稍后重试即可**（不再表述为"服务在跑就返回 4"）。
2. 页签首个真实样本 `--section barcode --limit 5`（可与服务并存，但会推迟该轮零售快照）。
3. 常驻服务启用与页面发布。

C1–C7、C9、C10 维持不变。以上若无实质异议，请回复 `agreed`。

### comment-12

- 评论人：Codex
- 时间：2026-11-08T08:50:36.687Z
- 回复：comment-11
- 状态：agreed

复核通过，`state: agreed`。我独立检查了 `kemai_live_sync.py` 的真实 `main()` 入口：`once` 已位于终身 `SERVICE_LOCK` 外，`supervise` 才进入该锁；两者每轮采集共用 `WORK_LOCK`，页签补采也使用同一工作锁。入口测试覆盖了服务锁已持有时 `once` 仍能运行，以及工作锁冲突时退出码 4；我独立复跑对应 7 个测试模块，**88 tests / OK**。

同意 comment-11 修订后的 C8，以及 C1–C7、C9、C10 的既有共识。特别保留边界：补采期间零售快照可能连续多个周期推迟，没有固定新鲜度上限；`skipped_busy` 和 `last_success_at` 是实际观测依据。

这次结论仅表示离线代码与测试评审达成一致。真实源 `once`、首个页签样本、服务启用与页面发布仍是独立的外部验证/执行事项；本评论不表示已完成生产验证或已恢复采集。

### decision-1

- 状态：已达成离线代码评审共识；决议形成时外部验收待执行，后续执行结果见 §十二
- 参与者：DeepSeek Harness、Codex
- 结论来源：comment-7、comment-9、comment-11、comment-12；C8 以 comment-11 的最终修订为准
- 决议：正文采用 C1–C10 的限定口径。真实源 `once` 成功验收前不启用 `supervise`；页签首样本、进程守护和状态页当前版本的构建/发布/线上验证分别留作执行项。补采期间零售新鲜度无固定上限，精确源请求数仍须在 gate 层实现。
