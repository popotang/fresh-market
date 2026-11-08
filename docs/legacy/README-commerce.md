# 科迈采购与零售同步

> 2026-11-08 18:03 CST 状态更新：旧日常 supervisor 仍暂停；新小时级零售服务完成一次真实源手动验收与短暂试启后，按用户目标澄清已停止，当前没有常驻采集。最新验收见 [交付记录](../kemai-sync-delivery-2026-11-08.md)。以下历史运行记录中的“监督进程在运行”不代表当前状态。

先按业务动作找命令，可看 [能力清单与操作词典](../kemai-sync-capability-map-2026-11-08.md)；四层分工见 [同步体系目标架构](../kemai-data-sync-target-architecture-2026-11-08.md)，工作包与验收顺序见 [开发计划](../kemai-sync-development-plan-2026-11-08.md)。

## 已验证的源数据

2026-11-06 用 OpenCLI 已登录会话分别采样采购订单 2、采购收货单 2、采购退货单 1、零售订单 2、销售流水 2。采购详情还有 19 条商品明细、2 条适用机构、1 条审核记录；零售订单详情还有 4 条商品明细、2 条支付流水。采样数据已存数据库，原始响应在 `source_capture`。

源系统金额、数量、单价等整数采用 1/10000 精度，关系表保存换算后的 `DECIMAL`，`payload` 保留原值。源 ID 全部按字符串保存。源接口的 `totalRecord` 在有数据时仍报告 0，所以按实际返回行数翻页。

经额外只读核验：收货单的 `sourceId/sourceNo` 对应采购订单 `id/sheetNo`；销售流水的 `sheetId`、`id`、`receiptNo` 分别对应零售订单 ID、订单商品明细 ID、小票号。

## OpenCLI 指令

本项目的适配器源码在 `kemai_opencli/`，并已复制安装到 `~/.opencli/clis/kemai/`。下表列出采购、零售的 10 条只读指令和商品档案导出指令：

| 指令 | 粒度 |
| --- | --- |
| `purchase-orders` | 采购订单列表 |
| `purchase-receipts` | 采购收货单列表 |
| `purchase-returns` | 采购退货单列表 |
| `purchase-detail --kind order\|receipt\|return --id ID` | 采购单头、商品行、适用机构、审核记录 |
| `purchase-receive-history` | 历史已审核收货商品行，来自同一 SaaS 的“收货退货明细”报表 |
| `retail-orders` | 零售小票列表 |
| `retail-sales` | 逐商品销售流水 |
| `retail-order-detail --id ID --sale-time 'YYYY-MM-DD HH:mm:ss'` | 零售单头、商品行、支付流水 |
| `retail-sales-export-start --from DAY --to DAY` | 创建零售流水异步 Excel 导出任务，返回 `totalRows`、状态和下载地址 |
| `export-task-list --rows N` | 查询导出中心最近任务，用于轮询完成状态和下载文件 |
| `item-archives-export-start --snapshot DAY --expected-total N` | 创建或复用商品档案 Excel 导出任务 |

列表指令统一要求 `--from`、`--to`，支持 `--page`、`--rows`（最多 1000）；采购还支持 `--date-type`，默认制单时间。示例：

```bash
opencli kemai purchase-orders --from 2026-11-06 --to 2026-11-06 --page 1 --rows 20 -f json
opencli kemai retail-sales --from 2026-11-06 --to 2026-11-06 --page 1 --rows 20 -f json
opencli kemai retail-sales-export-start --from 2026-11-06 --to 2026-11-06 -f json
opencli kemai export-task-list --rows 20 -f json
opencli kemai purchase-detail --kind receipt --id SOURCE_ID -f json
```

## 数据表

`schema_commerce.sql` 包含采购、零售和同步缺口表；后续又增加了单据详情状态及旧日期对账待核表：

| 表 | 主键 / 关系 |
| --- | --- |
| `purchase_documents` | 采购订单、收货、退货共用单头；`document_kind + source_document_id` 唯一 |
| `purchase_document_lines` | 采购商品明细；`document_id` 关联单头，`source_item_id` 关联现有商品源 ID |
| `purchase_document_branches` | 单据适用机构；源机构 ID 关联 `branches` |
| `purchase_document_approvals` | 单据审核记录 |
| `retail_orders` | 小票单头；`source_order_id` 唯一 |
| `retail_order_lines` | 小票商品明细；关联小票、商品 |
| `retail_payments` | 小票支付流水；关联小票 |
| `retail_sale_flows` | 零售逐商品流水，独立采集，`source_order_id` 可关联小票，`source_flow_id` 可关联订单商品行 |
| `purchase_receipt_history_lines` | 历史已审核收货报表行，保存报表特有字段；以单据 ID、行序号、商品 ID 生成稳定行键去重 |
| `purchase_document_detail_state` | 每张采购单的详情完成状态、数组行数、源版本/哈希和失败原因；`document_id` 主外键 |
| `sync_reconcile_missing` | 旧日期单轮对账时源列表未见的 ID，仅标记待核，不删除业务行 |

采购单头上的 `source_supplier_id`、`source_branch_id`、`source_receive_branch_id` 可关联已有 `suppliers`、`branches`；商品行的 `source_item_id` 关联 `products.source_item_id`。源系统有些收货、退货单没有上游单据 ID，应保留空值。跨表逻辑关联要同时带 `tenant_id`，避免不同租户源 ID 冲突。`schema_commerce.sql` 只对内部父子表使用物理外键；既有主数据的源 ID 使用逻辑关联，避免采集顺序导致外键写入失败。

## 采集模式与频率

`kemai_commerce_sync.py` 调用上述 OpenCLI 指令写入 MySQL；密码交互输入，不写入脚本、命令参数或日志。当前默认最多每秒 0.5 次采集调用，按自然日拆分；历史已审核收货首轮按月 Excel 导出。详情按单据读取；同一源 ID 更新覆盖，原始响应和结构化表保留哈希。每个日期或导出月份完成后写检查点；全量任务可按检查点续跑。

零售销售流水数据量大，采用“单轮分页采集 + 科迈异步导出任务 `totalRows` 核对 + 每批 1,000 条 MySQL upsert”。2026-10-11 的单轮结果和导出任务均为 22,952 条；导出文件约 34 秒生成。Excel 有 53 个业务展示列，但没有 `source_flow_id`、`source_order_id`、`source_item_id` 等稳定源主键，因此只用于总量对账，不直接替代 API 原始数据入库。

```bash
python3 kemai_commerce_sync.py sample --to 2026-11-06 --sample-limit 2
python3 kemai_commerce_sync.py full --from 2026-01-01 --to 2026-11-06
python3 kemai_commerce_sync.py incremental --to 2026-11-06 --lookback-days 30
```

采样模式只取每种类型首 2 条，不写“全量完成”检查点。`--max-pages N` 可做有限页试跑，运行状态标记为 `partial`，同样不写完成检查点。全量按日续跑；`full --restart` 已禁用，旧日期使用独立 `kemai_commerce_reconcile.py`。`kemai_commerce_sync.py incremental` **未指定 `--from` 时**仍以最近 30 天计算起点，但若与现有增量检查点重叠会拒绝执行；当前日常 supervisor 显式传入各流检查点的下一天，因此仍按未完成日期向前推进。采购页面当前只有制单、审核等业务日期筛选，未验证“最后修改时间”筛选；独立对账可发现旧日期新增或变化，源列表未见的记录只标记待核。采购历史详情由 `kemai_purchase_detail_backfill.py` 按单据状态补采，不能靠重扫列表补齐。

2026-11-07 新增的代码入口如下；本次开发只做离线测试，尚未执行源采集或生产数据库迁移：

```bash
# 只读计划；plan 不建表、不访问科迈
python3 kemai_purchase_detail_backfill.py plan --from 2026-11-01 --to 2026-11-01 --kind receipt
python3 kemai_commerce_reconcile.py plan --from 2026-11-01 --to 2026-11-01 --kind purchase-orders

# DB 建表，不访问科迈；需在持有数据库口令的交互终端执行
python3 kemai_commerce_sync.py migrate

# 小窗口源采集示例：日常单实例状态和限流状态核实后才可执行
python3 kemai_purchase_detail_backfill.py start --from 2026-11-01 --to 2026-11-01 --kind receipt --limit 20 --rate 0.5
python3 kemai_commerce_reconcile.py start --from 2026-11-01 --to 2026-11-01 --kind purchase-orders --rate 0.5
```

采购详情写入前要求商品行 `details` 为数组；附属 `branchs`/`approves` 未返回时不删除旧子行，单据和日期分别标为 `partial`/`partial_detail` 并保留缺口，返回数组但类型异常仍失败。合法空数组记为 0 行。零售详情/支付继续要求两个数组完整，重复补采时对过期详情行回退至已采销售流水或清理详情独有行；零售历史补采仍保持暂停。

## 全量、追平与每日任务

`kemai_followup_launch.py` 的首轮任务已完成至 2026-11-05；`kemai_catchup_launch.py` 的追平任务已完成至 2026-11-06。限流保护启用后严格串行执行“商品清单增量 → 变更商品详情 → 变更商品页签 → 各采购/零售流”，任何时刻只允许一个科迈请求源；每条流写入独立日志、数据库 `sync_runs` 和日期检查点；失败最多自动重试两次，续跑时按已完成日期跳过。查看状态：

当前普通采购、零售和历史导出流均按最多每秒 0.5 次采集调用运行，并由监督进程串行执行；所有列表每页请求 1000 行，每个日期窗口只分页扫描一轮。零售流水和零售订单与导出任务总数核对；其他列表以单轮分页的唯一源 ID 数为当日总数，入库后与 `source_capture` 已捕获键数核对。

零售流水可用 `python3 kemai_retail_daily_audit.py --strict`，零售订单可用 `python3 kemai_retail_daily_audit.py --kind retail-orders --strict`，只读核对各自已完成检查点内的每一天：合并普通与优先续跑日志中的导出总数/保存数，按 `sold_at` 与数据库逐日计数比较，并列出仍只有单轮扫描数、缺少独立导出总数的日期。命令不会向科迈发请求。加 `--summary --output 文件名.json` 可在终端简要显示并保存完整审计。

采购订单、收货单、退货单可分别用 `python3 kemai_purchase_daily_audit.py --kind purchase-orders --strict`、`--kind purchase-receipts --strict`、`--kind purchase-returns --strict` 只读核对已完成日期的单轮扫描数、捕获数、数据库 `source_created_at` 日计数、业务单据与原始捕获的一一对应关系及检查点。采购列表的 `totalRecord` 不可靠，审计会明确列出没有独立导出总数的非空日期；收货单初始可访问窗口从 2026-08-08 起，早期已审核商品行另见历史 Excel 对账报告。同样可加 `--summary --output 文件名.json`。

追平或每日增量完成后，上述零售和采购审计命令可加 `--include-incremental`，将核对范围扩展到最新已提交的增量日期；若尚无增量检查点，命令会报错，不会把全量截止日误当作追平完成日。

全局熔断器同时检查 HTTP 429、接口业务码/消息、OpenCLI 异常和页面“操作太频繁/稍后再试”弹窗；事件追加到 `kemai_rate_limit.log`，当前冷却状态写入 `kemai_rate_limit_status.json`。任何一次命中都立即停止新请求并统一冷却 30 分钟，冷却后只重试原批次一次；所有新请求在发送前共同检查该状态。导出任务中心的轮询间隔为 2 秒，单次最多 10 次，批处理脚本在导出完成前每 10 秒查询一次。

首次全量采用“快速可用层”：历史收货按月创建 `purchaseInDetail` Excel 导出，以“单据号+明细序号+商品编码”幂等入库；零售订单用单轮大分页保留源订单 ID，并用 `OD` Excel 任务总数核对。零售订单的追平和日增量也按此方式保存单头，并逐日核对导出总数，避免数千笔订单逐单请求阻塞串行同步。历史收货内部单据 ID/完整原单详情，以及零售订单详情接口独有字段和支付流水，写入 `sync_coverage_gaps` 作为待补任务；零售商品行由 `retail_sale_flows` 采集并映射到 `retail_order_lines`。采购类增量仍读取单据完整详情。零售详情和支付的后续补采使用单独的 `kemai_retail_detail_backfill.py plan/start --from ... --to ...`；旧 `kemai_commerce_sync.py incremental --retail-order-details` 会重扫订单日期窗口，不用于历史详情补缺。只有详情响应包含商品与支付数组且当日全部订单采集成功，才将该日缺口标记为已解决。

```bash
cat kemai_followup_status.json
tail -n 20 kemai_followup_supervisor.log
tail -n 20 kemai_followup_purchase-receipts.log
tail -n 20 kemai_followup_retail-sales.log
tail -n 20 kemai_followup_item_incremental.log
```

商品清单与基础详情可用 `python3 kemai_product_coverage_audit.py --strict --summary --output 商品审计.json` 核对最近一次成功扫描的源总数、主档、详情、原始捕获和最近变化商品页签，并报告初始页签待补数。全部全量与追平结束后，用 `python3 kemai_integrity_report.py --strict --summary --output 完整性审计.json` 生成只读核验报告；完整文件包含业务表/原始捕获计数、重复稳定键、孤儿明细、各流检查点、对象队列状态和明确记录的覆盖缺口。严格模式发现重复键或孤儿记录时返回非零状态。

`kemai_daily_launch.py` 是后续日增量监督进程。它等待首轮追平成功，在本机时间凌晨 2 点以后处理前一完整自然日；各采购、收货、零售流读取自己的 `commerce_incremental:*` 检查点，从下一天续跑，商品清单及变更详情/页签按目标日只跑一次。运行状态见 `kemai_daily_status.json` 和 `kemai_daily_supervisor.log`。数据库密码仅通过启动时的继承文件描述符交给进程，不写入状态文件；本机重启后需手动重新运行 `python3 kemai_daily_launch.py`。单轮失败会记录在状态中，下一轮从已提交检查点续跑。

按数据类别对客户展示的同步状态和逐窗口日志分别落在 `sync_stream_status`、`sync_stream_events`；两者记录最近成功时间、源日期覆盖、数量核对方式和已知缺口。旧 `sync_runs`、`sync_checkpoints` 继续负责原有任务与断点。详细口径及回填方式见 `README-sync-status.md`。

历史行键迁移时使用过的收货优先续跑进程已结束。收货首轮与历史已审核明细已完成至 2026-11-05；同种数据流使用 MySQL 命名锁防止并发写入，监督队列续跑时按已完成日期检查点跳过。

全量任务的 `--to` 固定为 2026-11-05。`kemai_catchup_launch.py` 是一次性追平监督进程：等待全部全量任务结束后再串行采集 2026-11-06 至运行时前一完整自然日；各流还会读取自己的增量检查点，避免重复日期扫描。商品清单会在全量结束后再扫描一次，覆盖 11 月 6 日晚间可能产生的变化，只刷新新增或变化商品的详情与页签。状态在 `kemai_catchup_status.json`，每条流的追平日志为 `kemai_followup_catchup_<kind>.log`。追平后由 `kemai_daily_launch.py` 继续每日增量。

历史范围经过源系统查询探测：采购订单及已审核收货明细从 2025-01-01 开始，采购退货从 2026-01-01 开始，零售订单和销售流水从 2026-08-01 开始；更早查询未发现记录。首次全量的已审核收货使用按月 Excel 导出，行身份采用“单据号+明细序号+商品编码”；导出未包含的内部单据 ID 和完整原单详情记入 `sync_coverage_gaps`，由后续增量回填。历史报表只覆盖已审核状态；源系统未提供 95 天以前未审核收货单的可用列表接口，因此这类记录不能宣称已经覆盖。

2026-11-06 的历史收货核验发现 API 序号 `16` 与 Excel 序号 `16.0` 原先生成不同稳定键。已将序号规范化为同一整数表示，并批量去除 400 组 API/Excel 重复。至 2026-11-05 的逐日和 Excel 窗口源总量为 125,197，业务表和原始捕获均为 125,197；连续窗口与限制说明见 `kemai_history_reconciliation_2026-11-06.json`。

所有日期窗口只扫描一轮，不做第二次、第三次重复翻页。零售销售流水与零售订单用异步导出的 `totalRows` 做独立总量核对；其他流用唯一源键的扫描数与落库数核对。校验不一致就停在该日，不写完成检查点。所有业务表用源 ID 或稳定业务键的唯一约束与 upsert 防重复。

截至 2026-11-07 的追平扫描，商品主档与基本详情各覆盖 18,386 条，最近变化的 17 个商品的 14 类页签已刷新；初始页签队列仍有 206,491 个待处理对象。商品档案 Excel 实测为 18,386 行，可以核对清单总数，但没有 14 类页签完整数据；详见 `kemai_product_tabs_strategy.md`。未完成页签、零售订单详情接口独有字段与支付流水记录在 `sync_coverage_gaps`，保留断点待后续补齐。

2026-11-07 补充核验：`retail_orders` 有 127,173 条，`retail_sale_flows` 有 453,971 条，原 `retail_order_lines`/`retail_payments` 仅为 4/2 条采样。所有销售流水均关联到订单，采样的 4 条商品行 ID、数量、金额、税额与销售流水一致；已用 `kemai_retail_lines_from_flows.py` 将 453,971 条销售流水幂等映射到订单商品行，`source_kind` 标识 `sales_flow` 或原始 `detail_api`。此举不增加科迈请求，但不能替代详情接口独有字段。`OD` Excel 仅有 26 个单头字段，不含商品行或支付流水；订单头缓存的 `payFlows`/`orderDetails` 为 JSON null。支付仍仅 2 条采样。

`kemai_retail_detail_backfill.py` 提供显式 `plan`（仅本库统计）和 `start`（需要后续明确启动）的有界详情/支付补采；每笔成功、失败记在 `retail_order_detail_state`，每天的完整或部分覆盖记在 `sync_stream_events`，最多每秒 0.5 个源请求，限流冷却 30 分钟，日常同步运行时让路。2026-11-07 的计划检查显示 127,173 笔待补，理论最短 70.7 小时；首笔 10 月 11 日历史订单在列表存在而详情返回“订单不存在”，已记录为失败。按用户指示，支付补采进程已停止，尚未开始全量执行。命令和恢复口径见 `/Users/tangshuang/.codex/skills/kemai-opencli/` 与 `/Users/tangshuang/.codex/skills/kemai-sync/`。

2026-11-07 的最终只读核验 `kemai_integrity_final_2026-11-07.json` 显示，商品、采购、历史收货、零售订单及流水的稳定键重复数和关联孤儿数均为 0。按天审计覆盖至 2026-11-06：零售订单 98 天均通过导出总数或空日核对；零售流水 98 天中 97 天有导出总数或空日核对，2026-10-11 仍是单轮扫描计数核对。采购三类按数据库与原始捕获核对，源列表 `totalRecord` 不可靠，不能宣称取得独立导出总数。历史已审核收货累计 125,754 行，与源窗口及原始捕获相符。每日监督进程在本机运行，凌晨 2 点后处理前一完整自然日；状态见 `kemai_daily_status.json`。

2026-11-07 13:52 增加跨进程源请求协调：`kemai_source_gate.py` 与 `kemai_opencli/source-gate.js` 共用本机文件锁，逻辑源操作在前一次结束后至少等待 2 秒，并继承原有 30 分钟限流冷却；13 条运行时 OpenCLI 适配器命令已安装并通过离线加载检查。商品与零售详情的 Python browser eval 入口也已改，但当时运行的旧 supervisor 尚未重启。手工 `opencli browser` 调用须通过协调器包装；部署与边界见 `docs/kemai-source-gate.md`。

2026-11-07 15:08 已在无在途任务时将日常 supervisor 从 PID 97002 切换至 PID 40069。新进程进入 `watching`，截止源日期仍为 2026-11-06，限流状态 `ready`，零售详情补采仍暂停。代码加载与进程状态已核对；下一完整源日还需核对实际同步日志及 `retail-order-lines` 事件。
