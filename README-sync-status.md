# 科迈数据同步状态

需要区分“看计划”“实际采一轮”“历史补采”和“启动定时任务时”，先看 [能力清单与操作词典](docs/kemai-sync-capability-map-2026-11-08.md)。

## 显示口径

`sync_stream_events` 按数据类别和源日期窗口保留每次尝试、结果、源行数、落库行数、核对方式、失败原因。`sync_stream_status` 每类数据一行，供客户查看最近尝试状态、最近成功时间和已覆盖的源业务日期。`sync_runs` 继续保留整次任务记录，`sync_checkpoints` 继续决定断点；新表不取代去重键或采集队列。

“最近完成源日期”指对应来源日期已完成核对，不代表更早日期连续完整；需逐日查看日志和覆盖缺口。“最近成功同步”指完成采集和检查点提交的时间。两者都不能等同于商品、库存或价格在此刻仍未变化。状态中以 `coverage_level` 和 `coverage_note` 明确标出仅有订单头、仅有已审核明细、仅刷新变化商品页签等缺口。`last_success_at` 在后续尝试失败时保留，`last_attempt_status` 和 `last_error` 单独显示失败。

## 首次回填

11 月 6 日交易流增量已完成，首次使用 `python3 kemai_sync_log_backfill.py --apply` 从已提交检查点、成功运行和审计报告回填最近一次状态。该脚本不访问科迈；回填事件的 `mode=reconstructed`，使用原运行时间，不伪造一次新同步。商品清单使用最近成功扫描，基本详情与变化商品页签使用原始捕获时间。回填时基础档案先标为 `not_scheduled`；2026-11-07 已用每秒最多 0.5 次请求单轮核对机构、部门、类别、品牌和单位。供应商从商品清单的引用关系派生，只能代表被商品引用的供应商。

## 后续更新

小时级零售采集的本地实现见 [设计与运行指引](docs/kemai-hourly-incremental-design-2026-11-08.md)，M1/M2/M4 交付说明见 [本次交付文档](docs/kemai-sync-delivery-2026-11-08.md)。`sync_live_cursors` 保存当天临时快照进度，`live:retail-orders`、`live:retail-sales` 和 `live:retail-order-lines` 在现有状态/事件表中另列。临时状态的 `covered_through` 为空，页面以“当日同步距今”显示新鲜度；日终核对后才推进原日期检查点。

**当前状态（2026-11-08 18:03 北京时间快照）**：零售当日真实源 `once` 已通过，`sync_live_cursors` 的订单和流水两行已更新；订单商品行当时有 56 条新流水尚无订单头，状态明确标为 `partial`，前一轮的 55 条暂缺已在下一轮补齐。新小时级服务曾试启，现已按用户目标澄清停止；旧日常 supervisor 保持 `paused_by_user`，当前没有常驻采集。停止时的未完成事件已记为 `interrupted`，上一轮成功时间保留。进程退出后的外部守护/开机自启尚未配置，实时状态请用 `kemai_live_service.py status --db` 核对。

## 运维命令（只读或需交互口令）

```bash
# 小时级采集（只读计划 / 单次 / 常驻）
python3 kemai_live_sync.py plan --max-catchup-days 2
python3 kemai_live_sync.py once --max-catchup-days 2 --rate 0.5
python3 kemai_live_service.py start --interval-minutes 30     # 口令只在前台输入一次
python3 kemai_live_service.py status                          # 加 --db 可读游标（再输一次口令）
python3 kemai_live_service.py stop

# 商品初始页签补采（有界、单页签类型、复用 sync_objects 断点）
python3 kemai_product_tabs_backfill.py plan --section all
python3 kemai_product_tabs_backfill.py start --section barcode --limit 5 --rate 0.5
python3 kemai_product_tabs_backfill.py status --section all
```

新服务与页签补采共用工作锁：补采占用时服务本轮记 `skipped_busy` 并在下轮重试；补采可能跨越多个周期，零售新鲜度没有固定上限。`kemai_live_service.py start` 还会检查真实进程表（`ps`）与旧状态文件；`ps` 失败或其他 worker 中途启动时不能保证所有采集入口互斥。源 gate 继续控制每秒最多 0.5 次请求和限流后的 30 分钟冷却。`source_requests_estimate` 不是精确源请求数。

`kemai_commerce_sync.py` 每个日期窗口写入开始、成功或失败事件，并在成功时与日期检查点一同提交状态。零售订单标为 `headers_only`；采购历史首轮详情缺口仍由 `sync_coverage_gaps` 保留。`kemai_product_incremental.py` 记录商品清单扫描；`kemai_changed_refresh.py` 和 `kemai_changed_tabs.py` 分别记录变化商品详情与页签刷新。`kemai_masters.py` 每日做基础档案快照，`kemai_derive_suppliers.py` 刷新商品引用的供应商。所有源请求仍遵守每秒最多 0.5 次、限流冷却 30 分钟；记录同步状态不会增加科迈请求。

2026-11-07 的零售商品行本库映射事件为 `retail-order-lines`，覆盖级别 `flow_mapped`；它证明 453,971 条已采销售流水均有订单商品行，不代表详情接口已逐单采集。`retail-payments` 的首次历史详情尝试失败并已暂停，2 条支付仍为初始采样。后续若执行详情补采，只有某天每笔订单都有可访问的完整详情数组，才将该日缺口标记为解决；异常单据保留失败状态，页面用“最近完成源日期”避免误示历史连续完整。

## 系统页面

“同步管理 → 数据同步状态”是只读自定义页面（页面 ID `1550`，菜单 ID `79821`），源码在 `sync-status-page/`，当前版本已发布于 `https://app-21522951.app.lovrabet.com/915a1df83d014014b61c8810ef47e7fb`。第一个页签展示各数据类别的最近状态和覆盖缺口，第二个页签分页展示同步日志；页面每分钟更新「当日同步距今」，每 5 分钟重读本库状态，也可手动刷新，不会触发科迈采集。平台回读为 `FORMAL`，运行态数据接口已读到 3 条真实 `live:*` 状态；浏览器视觉验收尚未执行。

平台自动生成的“数据更新状态”与“同步日志”标准列表页分别为 `1036167`、`1036171`。由于平台推送时会把新增和批量删除按钮恢复为默认值，这两个菜单已隐藏（菜单 ID `79817`、`79819`）；业务用户只使用只读自定义页面。隐藏菜单不等于撤销数据集的写权限，后续若开放给更多角色，仍应检查角色权限。
