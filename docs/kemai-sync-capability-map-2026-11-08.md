# 科迈数据能力清单与操作词典（2026-11-08）

> 2026-11-08 实施更新：公共内核与零售链路已按标准接口实现；验收证据、已启用范围和未完成项见[标准接口交付记录](kemai-standard-api-delivery-2026-11-08.md)，实际命令见[操作指引](kemai-pipeline-operations-2026-11-08.md)。下文历史规划/评审楼层保留，不应当作最新运行状态。

> 给操作人员看的当前事实。此文档不启动任务；完整技术证据见 [交付与验收记录](kemai-sync-delivery-2026-11-08.md)，目标分层与迁移顺序见 [同步体系目标架构](kemai-data-sync-target-architecture-2026-11-08.md)。截至 18:03，新旧常驻采集均已停止；状态页保留最后成功时间，不表示仍在自动更新。

## 先看懂四个动作

| 业务动作 | 脚本里常见的词 | 会访问科迈吗 | 会写本地业务数据吗 |
|---|---|---|---|
| 看还缺什么、将处理哪些日期或商品 | `plan`、`status`、`audit` | 否 | 否 |
| 现在执行一轮当日增量 | `once` | 是 | 是；稳定键 upsert，并写同步日志与临时游标 |
| 补指定历史日期或对象 | `start`、`full`、`reconcile start` | 是 | 是；各自按日期或对象断点续跑 |
| 每 30/60 分钟重复执行 | `supervise` / `kemai_live_service.py start` | 是 | 是；相当于定时调用零售 `once` |

`plan` 不是采集；`once` 不是第二遍历史扫描。零售 `once` 先抓**今天**的订单和销售流水，再从本地检查点推导尚未结算的过去日期，每轮最多追赶指定天数。今天是临时快照，只有过去日期通过日终总量核对后才推进日检查点。用户当前要求先具备按需执行能力，因此**没有常驻服务在跑**。

## 现有能力按业务目的对应

| 业务目的 | 当前入口 | 当前结论和边界 |
|---|---|---|
| 读取科迈原始列表、详情或 Excel 总数 | 13 条 `opencli kemai ...` 命令；商品清单/详情/14 类页签仍由 Python 脚本调用受控 `opencli browser` | OpenCLI 是**源端读取适配器**，不负责本地数据库的去重、补采断点或写入。源端导出命令会创建/复用科迈导出任务。 |
| 看历史是否一致 | `kemai_retail_daily_audit.py`、`kemai_purchase_daily_audit.py`、`kemai_product_coverage_audit.py`、`kemai_integrity_report.py` | 只读本地 DB、旧采集日志和已保存的源端总数；能查重复键、孤儿行、逐日数量和缺口，但不会现场重新请求科迈。采购列表的 `totalRecord` 不可靠，不能伪称有独立导出总数。 |
| 重新核对并补写旧日期 | `kemai_commerce_reconcile.py plan/start --kind KIND --from DAY --to DAY --rate 0.5` | `plan` 只读；`start` 对最多 7 天做**一轮**源端分页，比较新增/变化/源列表未见项，并 upsert 新旧行。未见项只标记待核，不删除业务数据；独立对账断点不回退日常断点。 |
| 补历史商品详情/页签 | `kemai_sync.py full/status`、`kemai_product_tabs_backfill.py plan/start/status` | 有对象级 `sync_objects` 断点。页签 `start` 必须指定一种页签和 `--limit 1..200`；Excel 只覆盖商品基础字段，不能代替 14 类页签。 |
| 补采购单据详情 | `kemai_purchase_detail_backfill.py plan/start --kind ... --from ... --to ... --limit N` | 按单据状态续跑；源端缺附属数组记 `partial`，保留原有子行，不冒充完整详情。 |
| 补零售原详情与支付 | `kemai_retail_detail_backfill.py plan/start` | 工具存在，但该类补采按此前用户指令仍暂停；销售流水映射的商品行不等于原始详情，支付尚未完整采集。 |
| 按需同步今天零售 | `kemai_live_sync.py plan/once --max-catchup-days 2 --rate 0.5` | `once` 已通过一次真实源与数据库核对；当日跨页重叠去重并记录，订单扫描后才出现的销售流水暂记 `partial`，下轮补齐。不是零售详情/支付补采。 |
| 按日期推进采购、商品和交易 | `kemai_commerce_sync.py incremental`、`kemai_product_incremental.py`、`kemai_changed_refresh.py`、`kemai_changed_tabs.py` 等；旧编排器 `kemai_daily_launch.py` | 各类脚本已有检查点和幂等写入，但旧日常编排器已暂停；采购与商品**尚未接入新的半小时零售轮询器**，不能称所有类别都有小时级增量。 |
| 给客户看同步新鲜度 | `sync_stream_status`、`sync_stream_events`、`sync_checkpoints`、`sync_coverage_gaps`；已发布的“数据同步状态”页 | 每类有最近尝试/成功时间、覆盖日期、错误和缺口。`covered_through` 是已核对的源业务日，不等于此刻最新；临时 `live:*` 的该字段为空。 |

## OpenCLI、Python、Lovrabet CLI 各负责什么

1. **OpenCLI → 读科迈。** 现有 13 条 `opencli kemai` 命令不包含统一的 `history check/fill` 或 `incremental sync`；这些是更高一层的作业。商品清单、详情和页签还没有全部封装成独立 `opencli kemai` 命令。
2. **Python 同步脚本 → 比较、校验、补写、断点与限流。** 当前采集写入直接使用 MySQL 的稳定键唯一约束、批量 upsert 和事务；这正是万级流水、逐日核对和失败续跑所需要的行为。源请求统一受本机 gate 的 0.5 次/秒上限和限流后 30 分钟冷却约束。
3. **Lovrabet CLI → 运行态独立回读。** 本轮已用 `lovrabet data filter` 从应用数据集回读三条 `live:*` 状态和同步事件，证明页面的数据接口有真实数据。当前采集脚本**没有**通过 Lovrabet CLI 写业务表，也没有一套 Lovrabet CLI 驱动的历史对账脚本。小范围修正可评估其写接口；大批量采集不应简单改成逐行 `data create/update`，否则会损失现有批量幂等与事务语义。

## 因而，是否已经达到目标？

**现有脚本能执行多种采集任务，但尚未达到目标分层。** 历史逐日审计、旧日期重核补写、对象级详情/页签补采、按需零售增量、数据库同步日志都存在；准确性仍受各源接口的可用字段与历史详情缺口约束。商品读取全面命令化、统一业务命令入口、采购/商品独立频率，以及由 Lovrabet CLI 承担数据中心批量幂等写入与回读的契约，尚未完成。

下一步宜做一个薄的业务命令入口，例如 `history check`、`history repair`、`incremental run`、`gaps show`、`sync status`，只编排现有经过验证的脚本，并在执行前显示日期、数据类别、是否访问科迈和最多请求预算。不要再让操作人员直接猜 `plan/once/start` 的含义。新入口应保留原脚本的稳定键、断点、冷却和失败日志，不重新实现采集逻辑。
