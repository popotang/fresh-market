# 科迈采集交接：由 DeepSeek 主导补全量与两日增量

> 执行基线：2026-11-08 01:10 CST。本文是给 DeepSeek 的单一操作指引；所有数量都是历史快照，任何源请求前重新读进程、数据库检查点和限流状态。本文不含账号、口令、令牌或导出签名 URL。

## 任务与分工

DeepSeek 负责确定采集窗口、安排唯一的源请求执行者、检查每批结果并维护运行记录。Codex 负责按 DeepSeek 指派修复代码、执行离线测试和只读核验。**同一时刻只允许一个 Agent 跑科迈源请求**；另一个 Agent 可做离线代码和本库只读分析。不要由两个 Agent 分别启动 supervisor、OpenCLI 或历史 worker。

本次目标按顺序是：

1. **2026-11-08 02:00 CST 之后**追平 **2026-11-07** 的新增数据；它是目前尚未同步的最近完整源日。等待到 02:00 是既有日常任务用于避开源端延迟的时间门槛。
2. 在 **2026-11-09 02:00 CST 之后**追平 **2026-11-08** 的完整源日。11 月 8 日凌晨时，11 月 8 日数据仍在形成，不能称为已完成日。
3. 补首轮有意延后的历史详情与商品页签，并逐类标明能否达到真正完整覆盖。长任务按小批续跑，不阻塞上述两日增量。
4. 交付每条流的最后完成源日期、源行数、落库行数、核对方式、已知缺口和下一断点。只有通过审计后才考虑恢复自动日常任务。

## 当前停机点与不可变边界

- 用户要求先停日常增量。2026-11-08 **01:10:18 CST**，在 `watching` 且无在途子任务时向 PID `40069` 发送 `SIGTERM`；进程和采集子进程均已退出。`kemai_daily_status.json` 已原子标为 `paused_by_user`，`supervisor_pid=null`，`stopped_pid=40069`，保留 `last_complete_day=2026-11-06`。**不要因旧文档仍写“supervisor 正在运行”而重启它。**
- `kemai_rate_limit_status.json` 停机时为 `state=ready`；源操作前重读。共享 gate 规定逻辑源操作串行、相邻操作至少 2 秒，限流后冷却 **30 分钟**。用户设定上限为 **0.5 次源请求/秒**。HTTP 200 也可能返回 `code=1` 与“您的操作太频繁，请稍后再试”；HTTP 429 和页面弹窗同样是限流。
- 零售原订单详情/支付 worker 仍为 `paused_by_user`。本次“补剩余全量”应把它列为未完成项并先执行只读 `plan`；在用户明确解除该暂停前，**不得执行 `kemai_retail_detail_backfill.py start`**。
- 数据库口令只能由持有人在交互终端输入，或由既有 launcher 通过匿名文件描述符交给子进程。不要写入命令参数、文件、日志或 Agent 对话。当前 Agent 环境没有可复用的 `KEMAI_PASSWORD_FD`，此前无口令的 `mycli` 连接失败。
- 不使用 `kemai_commerce_sync.py full --restart`，也不把已完成的旧日期传给普通 `incremental --from`。它们现在会被代码拒绝；旧日期变更用独立 `kemai_commerce_reconcile.py`。

## 事实基线，先重新核验

2026-11-07 04:11 的本地完整性快照报告：商品 18,386、采购单头 17,089、已审核历史收货行 125,754、零售订单 127,173、销售流水 453,971。该快照中的零售订单商品行仍为映射前的 4 行；之后已有一次 453,971 行的本库映射输出，但**尚无落盘核验文件**，不得把它当成可独立复核的实时数。支付只有 2 行初始采样。

同一快照有 **686 条缺口记录，不是 686 条业务数据**：采购订单 309 个日期、收货 67 个日期、退货 271 个日期，历史已审核收货 11 个范围，零售订单 27 个有数据日期，商品页签 1 个初始全量范围。商品页签待处理约 **206,491 个对象**；此数也不是 206,491 个商品。18,386 商品的清单与基础详情已完成，初始 14 类页签仍未全覆盖。

接手第一步只读核对：

```bash
cd /Users/tangshuang/data/gitlab/yuntoo/biz-case/fresh-market
cat kemai_daily_status.json
cat kemai_rate_limit_status.json
cat kemai_retail_detail_status.json
ps -axo pid,ppid,stat,command | rg 'kemai_(daily_launch|commerce_sync|retail_detail_backfill|purchase_detail_backfill|commerce_reconcile|product_incremental|changed_refresh|changed_tabs|masters|derive_suppliers|retail_lines_from_flows)'
python3 -m unittest -q test_kemai_commerce_safety.py test_kemai_source_gate.py test_kemai_retail_detail_backfill.py
diff -rq kemai_opencli ~/.opencli/clis/kemai
```

`ps` 无匹配时退出码 1 是正常结果。当前离线套件为 24 个通过用例；测试路径必须隔离生产 gate/限流文件。检查 `sync_checkpoints`、`sync_stream_events` 和 `sync_coverage_gaps` 的实际 DB 值，以 DB 为断点准绳，不只看状态 JSON。建立私有 `.local-audits/`（权限 700，文件权限 600），保存带生成时间的审计结果；执行 [原操作指引的阶段 0 审计命令](kemai-next-sync-plan-and-runbook.md#阶段-0接手核验不访问源站)，重点补存零售流水→商品行 `--verify-only`、重复键和孤儿报告。

在开始新采购详情 worker 前，通过交互口令执行 `python3 kemai_commerce_sync.py migrate`，确认 `purchase_document_detail_state` 和 `sync_reconcile_missing` 两张新表存在；此命令不访问科迈。此前本地 DDL 定义 33 张表，生产库是否已迁移尚未核验。若表或权限不符，先修复迁移，再做源请求。

## 第一批：2026-11-07 增量

先重新读取六条 `commerce_incremental:<kind>` 检查点。对已提交到 11 月 7 日的流只审计、不重复扫描；其他流各自从检查点下一日执行。**不要用 `--kind all`**，它缺少单类别任务的完整数据库锁语义。所有源任务串行，任何失败保留原日期断点，先查日志，不能通过第二轮扫描掩盖异常。

商品任务按当前时点做一次快照/变更刷新，顺序与原日常 supervisor 一致：

```bash
python3 kemai_masters.py --rate 0.5
python3 kemai_product_incremental.py
python3 kemai_derive_suppliers.py
python3 kemai_changed_refresh.py
python3 kemai_changed_tabs.py
```

商品脚本读取**执行时的当前状态**，不是可还原的 11 月 7 日终态；记录采集时间，不能将其宣传为“11 月 7 日 24:00 商品快照”。若任一前置任务失败，停止依赖它的后续商品任务，保留已提交的对象状态。

六个交易流逐个执行下列命令；实际 `--from` 必须先按该流 DB 检查点确定，示例只适用于截止仍为 11 月 6 日的流：

```bash
python3 kemai_commerce_sync.py incremental --from 2026-11-07 --to 2026-11-07 --kind purchase-orders --rate 0.5
python3 kemai_commerce_sync.py incremental --from 2026-11-07 --to 2026-11-07 --kind purchase-receipts --rate 0.5
python3 kemai_commerce_sync.py incremental --from 2026-11-07 --to 2026-11-07 --kind purchase-returns --rate 0.5
python3 kemai_commerce_sync.py incremental --from 2026-11-07 --to 2026-11-07 --kind retail-orders --rate 0.5
python3 kemai_commerce_sync.py incremental --from 2026-11-07 --to 2026-11-07 --kind retail-sales --rate 0.5
python3 kemai_commerce_sync.py incremental --from 2026-11-07 --to 2026-11-07 --kind purchase-receive-history --rate 0.5
```

`retail-orders` 与 `retail-sales` 均成功后，运行本库映射，不增加科迈请求：

```bash
python3 kemai_retail_lines_from_flows.py --from 2026-11-07 --to 2026-11-07
python3 kemai_retail_lines_from_flows.py --from 2026-11-07 --to 2026-11-07 --verify-only
```

**2026-11-08 01:35 修正：**首轮真实响应证实 order 可缺 `approves`、receipt/return 可缺 `branchs`，原先“三类单据均要求三个数组”过严。当前代码只把 `details` 作为必需商品行；附属数组若缺失或为 `null`，保留该子表旧行，将单据记为 `partial`、日期记为 `partial_detail` 并留下 `sync_coverage_gaps`；数组存在但类型错误仍停止。详情缺少 `details`、源 ID 不符或其他结构错误仍使该流失败且检查点不前进。重跑采购三条失败流前先确认使用此修正版，并核对新状态与缺口，不能把部分覆盖写成 `full_detail`。

**本次运行观察：**DeepSeek 的 `batch1 --now` 在 11 月 8 日约 01:25 启动，早于本指引的 02:00 门槛。`retail-orders` 与 `retail-sales` 虽显示成功，11 月 7 日最终总量仍需在门槛后用已有导出/源总数证据核对；如有迟到数据，按受控旧日对账入口处理，不把重复列表扫描当作常规核对。`masters`、`item_incremental` 报“登录认证失败”，其依赖的商品任务跳过；这与采购数组问题不同，先确认合法会话恢复再续跑。三条采购流因原校验失败；状态文件仍显示原断点，续跑前须以 DB 检查点核实。`kemai_collection_logs/` 已纳入采购/零售审计日志搜索；后续编排器按源日期和尝试时间保存源任务日志，避免覆盖前次证据。已启动的编排器进程仍使用加载时的旧代码，必须等它结束后再让新进程加载修正版；不要并行启动第二个源执行者。

每个流完成后保存当天日志、源分页唯一键数、`source_capture` 键数和业务表行数。零售订单/流水再与 Excel 导出任务的 `totalRows` 核对；采购列表的 `totalRecord` 不可靠，只能声明“单轮唯一键数 = 原始捕获数 = 落库数”，不能伪称有独立源总数。零售商品行须逐条匹配销售流水，`coverage_level=flow_mapped`，不能标为原详情完整。

## 第二批：2026-11-08 增量

**最早 2026-11-09 02:00 CST 后**执行；重复上一批的商品任务与六个交易流，日期改成 `2026-11-08`，并先读每条流的最新 DB 检查点。若 11 月 7 日某流失败，必须从其下一未完成日向前补，不可直接跳到 11 月 8 日。`retail_order_lines` 的映射范围覆盖尚未映射的连续日期，随后分别只读核验 11 月 7 日和 11 月 8 日。

两批都通过后，用 `kemai_retail_daily_audit.py --include-incremental --strict`、`kemai_purchase_daily_audit.py --include-incremental --strict`、`kemai_integrity_report.py --strict` 和 `kemai_product_coverage_audit.py --strict` 落盘核对。审计输出要记录源日期、有数据天数、空日数、核对方法与生成时间。客户状态页的“最近完成源日期”不等于历史连续完整覆盖。

手工子任务会更新各自的数据库检查点和日志，**不会自动把 `kemai_daily_status.json` 中 11 月 7/8 日的 job 项写为成功**。在恢复日常 supervisor 前，DeepSeek 必须用 DB 审计结果与手工运行记录核对这两天，制定一次受控的状态文件交接：仍保留暂停状态，只有每个前置商品任务、六个交易流和本库零售商品行映射均有证据时，才可把对应日期记为完成。否则直接重启会重复跑商品源扫描或从旧 `last_complete_day` 重做本库映射；不允许只凭计划数手改成功状态。

## 第三批：补首轮剩余全量

按以下优先级做小批次，每一批都在 `state=ready`、无并行源进程且前一批审计通过后开始。11 月 9 日到来前，给 11 月 8 日增量留出窗口；历史 worker 不会自动为它让路。

| 优先级 | 范围与入口 | 完成定义 / 已知限制 |
| --- | --- | --- |
| 1 | 历史采购订单/收货/退货详情：先 `python3 kemai_purchase_detail_backfill.py plan --from DATE --to DATE --kind receipt`，再选 **1 天、`--limit 5 --rate 0.5`** 的 `start` 小样本；观察三类单据、多种状态，再按批扩大 | 单据级 `purchase_document_detail_state` 的 `success` 和三个数组行数与子表一致才算完整；源未返回附属数组的 `partial` 单据保留缺口，不自动重试，需源版本变化或显式 `--retry-failed`。该日全部已知单据和原缺口行数一致才关闭日期缺口。历史收货旧单可能因源端时间限制不可访问，此时只保留缺口。 |
| 2 | 已审核历史收货报表缺口：从 `sync_coverage_gaps` 的 11 个范围逐一对照现有 Excel、单据 ID 映射及可访问详情 | 报表只有已审核行，不能推出未审核历史完整。旧报表缺内部 ID 的行单独记录待匹配；源端无可访问入口时说明边界。避免重导已核对月份。 |
| 3 | 初始商品页签：先按 14 类逐项验证独立分页/Excel 是否具备完整字段、总数和稳定键；无可用批量接口时，基于 `sync_objects` 做**按页签类型、按对象数有界**的队列 worker | 旧 `kemai_sync.py full` 会先触碰商品清单分页断点，不宜直接用来补 206,491 个页签对象。先做独立 `plan/start --section --limit` 入口和失败重试审计，再逐类续跑。动态价格/库存保留采集时间，不能把跨多日数据称为同一时点快照。按 0.5/s 的请求理论下限约 115 小时，不是承诺完成时间。 |
| 暂停 | 零售订单原详情与支付：仅运行 `kemai_retail_detail_backfill.py plan`，保留 `paused_by_user` | 原详情、支付的 127,173 笔历史订单未完整采集，2 条支付只是采样；早期订单有“订单不存在/详情超时”案例。用户解除暂停前不运行 `start`，不把销售流水映射行冒充支付或原详情。 |

历史旧日期**变更对账**与首轮全量缺口不同：必要时用 `kemai_commerce_reconcile.py plan/start --from DATE --to DATE --kind KIND --rate 0.5`，每窗口最多 7 天，独立检查点。源列表未见的 ID 进入 `sync_reconcile_missing` 待核，不直接删除。不得用普通增量或 `full --restart` 重扫已完成日期。

## 每批停机线与交付格式

遇到业务码限流、HTTP 429、页面频繁操作弹窗，立即记录触发流/窗口和脱敏错误，**至少冷却 30 分钟**，期间不通过另一个 Agent、裸 `opencli browser` 或加并发绕过。源总数/落库数不符、数组结构异常、稳定键重复、孤儿、检查点倒退、数据库迁移失败时，停止该批后续请求，保留已提交断点及 `source_capture`；先修复再从未完成位置继续。一日只做一次成功的列表分页遍历，核对总量；失败重试要标记原因，不把重复遍历称为“第二轮核对”。

DeepSeek 每批在运行记录中填写：`执行人｜源流｜源日期/范围｜命令与代码版本｜开始/结束时间｜源请求数｜源唯一键数/导出 totalRows｜DB 保存数｜检查点前后｜覆盖级别｜限流/失败｜未解缺口｜下次从哪里续`。不写密码、令牌或签名下载地址。Codex 只在收到具体错误或审计差异后修代码并跑离线测试；修复期间唯一源执行者暂停相关流。最终给用户一张“已完整、部分覆盖、源端不可达、仍暂停”的数据流清单与验收证据，再由用户决定日常 supervisor 的恢复时点。

## 相关资产

- [原阶段计划及评论共识](kemai-next-sync-plan-and-runbook.md)
- [采购零售与增量命令](../README-commerce.md)
- [同步状态字段口径](../README-sync-status.md)
- [商品页签批量导出评估](../kemai_product_tabs_strategy.md)
- [源请求协调与限流](kemai-source-gate.md)
- `~/.codex/skills/kemai-sync/SKILL.md` 与 `~/.codex/skills/kemai-opencli/SKILL.md`：DeepSeek 接手前先读，再执行其引用的 runbook。
