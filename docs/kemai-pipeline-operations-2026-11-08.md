# 科迈标准接口同步操作指引

本页是新内核的实际入口。实现与验收范围见 [交付记录](kemai-standard-api-delivery-2026-11-08.md)。在项目根目录执行以下命令；CLI 身份由现有登录配置提供，不把数据库密码或源 Token 写入命令。

## 四层职责

- `kemai_opencli/`：只访问科迈，返回原始数据、时间和查询范围，管理导出任务和下载文件。
- `kemai_pipeline/lovrabet_adapter.py` + `kemaiSyncGateway`：只通过 Lovrabet 标准数据接口和事务处理数据中心。新增用 `batchCreate`，不同值的更新用受控 `update` 循环；业务数据、原始捕获、批次回执同事务。
- `kemai_syncctl.py`：确定性编排，保存源工件，核对、分批、恢复、提交完整窗口。`kemai_pipeline_service.py` 可按 30/60 分钟触发，但运行脚本不等于已配置系统守护。
- `kemai-opencli` / `kemai-sync` Skill：根据能力、证据、预算与用户范围选择命令，解释缺口；不能自行修改回执、水位或解除暂停。

只有 `capabilities` 返回的 `accepted_streams` 可以写入新网关。商品源命令可用不代表商品对象队列、采购详情或 Excel 数据导入已迁移完成。旧 MySQL 脚本仍是迁移/回退实现。

## 查看状态与证据

```sh
python3 kemai_syncctl.py capabilities
python3 kemai_syncctl.py status --stream retail-sales
python3 kemai_syncctl.py gaps show --stream retail-sales
python3 kemai_syncctl.py history check --scope db --stream retail-sales --from 2026-11-01 --to 2026-11-07
python3 kemai_syncctl.py incremental plan --stream retail-sales --max-catchup-days 2
```

这些命令不请求科迈。`history check --scope db` 比较已存源计数和当前 DB，不能当作新源扫描或详情完整性证明。源端重新对比的独立 `--scope source` 命令尚未开放；`history fill` 会执行采集与入库，不应用它冒充只读操作。

`last_success_at` 是最近成功入库/核对的时间。窗口证据里的 `source_observed_at` 是源观测时间，`snapshot_cutoff` 是临时快照截止时间，`window_time_field` 是窗口时间字段。历史工件重放不能宣称刚刚读取了源站。`covered_through` 只是展示字段；新规划器使用 `sync_pipeline_windows` 的逐日证据推导连续覆盖，失败/临时事件不会扩大完整历史覆盖。

## 有界采集与恢复

```sh
# 当前上传日优先，再追赶最多两个缺失历史日；不会重放停机期间每个定时点
python3 kemai_syncctl.py incremental run --stream retail-sales --max-catchup-days 2 --max-source-operations 80

# 明确指定的历史窗口，单次 1..31 个已结束日期
python3 kemai_syncctl.py history plan --stream retail-sales --from 2026-11-06 --to 2026-11-06
python3 kemai_syncctl.py history fill --stream retail-sales --from 2026-11-06 --to 2026-11-06 --max-source-operations 80

# 用实际输出的 32 位 capture_id 替换占位符
python3 kemai_syncctl.py resume --capture-id CAPTURE_ID
# 源已采完时，可禁止任何新源调用，只恢复入库
python3 kemai_syncctl.py resume --capture-id CAPTURE_ID --max-source-operations 0
# 已无法继续的采集版本：释放它，保留已有业务行、回执与工件
python3 kemai_syncctl.py abandon --capture-id CAPTURE_ID
```

预算按 OpenCLI 逻辑操作计算，默认 200，上限 1000；不是浏览器全部 HTTP 请求数。导出命令内部可能有多次 API 请求。遇未知请求数输出 `null`，不写假精确数字。

源分页中断且未采完时，不稳定 offset 不能直接续页；旧 capture 标记 interrupted，下一轮新建 capture。源已完整落盘后，导出等待、DB 断连、CLI 超时均优先恢复同一 capture，先查服务器批次回执。导出创建结果不明时按采集编号匹配原任务；不能唯一匹配则保留错误，不能盲目重建导出。科迈可能明确拒绝 24 小时内重复导出并返回原任务号；此时只复用它明确指定的任务，记录 `server_24h_dedup`、导出创建时间和 `reused_export_total_and_capture`。这表示当前单轮 API 记录数与较早导出总数一致，不代表获得了新的导出快照。

`abandon` 不删除已经提交的业务行，也不标记窗口完整。它解决无法继续的尝试；后续新快照仍凭稳定源键幂等写入。已完成 capture 不允许 abandon。

## 旧数据承接与长停机

```sh
python3 kemai_syncctl.py history verify-legacy --stream retail-sales
python3 kemai_syncctl.py history adopt-legacy --stream retail-sales
```

第一条从旧日志提取源计数并通过标准数据 API 复核当前 DB；第二条写入逐日历史证据，不更新客户的“刚从源站同步”时间。需要匹配当前 `window_time_field`。未知基线只允许当前日临时快照，历史追赶会明确 blocked；先验证旧证据或按授权执行明确历史窗口，不能仅用最大日期跳过中间空洞。

当前零售范围固定为连接账号已验证的门店 1001（源机构 ID 69028303），配置在 `kemai_pipeline/source_contracts.json`。不能把它宣称为商户全部机构覆盖；机构变化须明确迁移范围。新捕获清除已知的 UI 商品/会员等临时筛选，未知非空筛选或机构变化会拒绝入库。

零售流水源用 `timeType=1`，零售订单源用 `dateType=0`，两者都是上传时间 `gmtCreate`，落库字段 `source_created_at`。值 2 是当班日期。这两个页面参数不同，不能混用。销售时间 `sold_at` 用于业务分析；迟上传的旧销售由上传日期增量捕获。源站既有记录是否会在不改变上传时间的情况下被改动，不能由当前日期过滤保证；这种旧数据修订需指定历史复核范围。

## OpenCLI 源命令

```sh
opencli kemai query-scope --kind retail-sales -f json
opencli kemai capture-page --kind retail-sales --from 2026-11-07 --to 2026-11-07 --page 1 --rows 1000 -f json
opencli kemai product-list --page 1 --rows 100 -f json
opencli kemai product-detail --id SOURCE_ITEM_ID -f json
opencli kemai product-section --id SOURCE_ITEM_ID --section barcode -f json
opencli kemai master-list --kind units --page 1 --rows 100 -f json
opencli kemai retail-sales-export-start --from 2026-11-07 --to 2026-11-07 --capture-id CAPTURE_ID --time-type 1 -f json
opencli kemai export-task-list --rows 100 -f json
opencli kemai export-download --task-id TASK_ID --output /absolute/private/path/export.xlsx -f json
```

`capture-page` 支持零售流水、零售订单、采购订单/收货/退货、历史收货六种 kind。商品页签有 14 类，`history_price` 要显式遍历页码；其他页签不能凭一次空缺响应判完整。基础档案 kind 为 branches/departments/categories/brands/units。

导出下载按精确任务 ID、可信源域名、XLSX 文件头、大小上限与 SHA-256 校验，旁存私有元数据；不会在报告中保存签名下载 URL。零售 Excel 缺少可替代接口 ID 的稳定身份映射，目前用于独立总数；历史收货 Excel 的新内核映射仍待验证，不能直接批量创建后声称幂等或完整。

## 限流、锁与容量

- 所有源命令共用 gate，最低间隔 2 秒；HTTP 429、HTTP 200 的“操作太频繁”、页面弹窗均进入共享 30 分钟冷却。冷却期间不得反复探测。gate 不统计浏览器加载的全部资源请求。
- 新旧写入脚本共用本机 writer lock；服务共用单实例锁；服务端每条流在事务开始时获得控制行锁。不同机器的集中租约/fencing 尚未实现，当前按单机运行。
- 同一 capture 的 batch ID 由稳定序号决定，摘要单独校验；同 ID 不同内容拒绝。未知写入结果先查回执。
- 订单仅 `gmtOperation` 查询时间变化时采用明确的元数据等价规则，不重写业务值；完整捕获工件保留，回执另记 `metadata_only_unchanged`。其余字段仍精确比较。
- 未变化记录可只发源键和预期摘要；服务端同时验证业务行与原始捕获的摘要和日期。完整原始数据仍保留在本地不可变工件里。DB 行发生变化则拒绝旧观察，不伪装成功。
- 已验收的接口硬上限为 200 行、records JSON 64,000 UTF-8 字节。编排预留封装开销，用 48,000 字节预算；普通大记录实际每批可能只有约 19 行。不能把 200 行当成任何负载都适用的容量保证。
- 正式完整窗口还检查全部批次、唯一源键、业务行存在、DB 日期总数和独立导出总数。超出窗口预算或无法证明完整时不推进断点。

## 定时运行与回退

```sh
# 仅执行一次；不启用常驻服务
python3 kemai_pipeline_service.py --stream retail-sales --once
# 经当前会话授权后，才作为长驻前台进程运行
python3 kemai_pipeline_service.py --stream retail-sales --interval-minutes 30
```

本次开发未启用常驻服务、开机自启或发布页面。收到 SIGINT/SIGTERM 时服务通知当前子进程退出并保留断点；不会用宽泛 pkill 杀其他任务。零售详情/支付仍暂停。回退前先退出新执行者，确认锁释放及 capture 状态，再选旧脚本；不要同时运行两个体系或从新窗口直接改写旧 checkpoint。

## 开发验证

```sh
python3 scripts/build_kemai_gateway.py
python3 -m unittest discover -p 'test_kemai*.py'
node --test tests/pipeline/*.test.mjs
PYTHONPATH=. python3 scripts/verify_kemai_capture_db.py --capture-id CAPTURE_ID --report .local-audits/check.json
```

最后一条仅对已提交完整日做标准 API 的键/摘要复核，不请求源站。测试报告的脱敏汇总进入 docs，源工件、业务行样本和原始回执保留在被忽略的私有目录。
