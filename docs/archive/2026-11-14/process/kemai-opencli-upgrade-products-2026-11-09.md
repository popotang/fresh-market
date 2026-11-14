# OpenCLI 2.0.3 与商品对象标准 API 工作流交接

初稿：2026-11-09；最新验收：2026-11-10。升级、修复和两个商品小样本的真实写入/回读已完成，最新结论见第 9 节。主档、完整详情、空条码页签及 1 条既有供货记录通过；这不代表所有商品、所有页签已经补齐。

## 1. 本次完成什么

- CLI 和 daemon 已升级至 **2.0.3**。已连接的 Chrome 扩展原本就是 **1.0.27**，无需重新安装。`opencli doctor` 返回 `issues: []`。
- 22 个科迈源命令均已加载，`capabilities` 通过真实 CLI 调用验证；此静态检查不访问科迈业务接口。
- `OpenCLISource.call` 兼容旧版裸数组和新版 `{ok:true,data:[...]}`。错误包装不会被当作业务成功；HTTP 200 里的频繁操作信号仍触发共享冷却。新版 stdout 中的明确导出拒绝也能识别。
- 现有五条列表流和采购单据批量详情入口保留。采购订单/收货/退货列表继续使用 API，不切 Excel。
- 新增独立入口 `scripts/kemai_products.py`，用 OpenCLI 取商品数据，用 Lovrabet 标准接口同步自有库。没有导入旧 MySQL 写入器，没有使用 Custom SQL，没有增加数据表。
- `product_profile` 的真实主键是 **product_id**。查询、排序和回读使用真实主键；更新同时携带相同值的 `product_id` 与 CLI 必需的通用 `id` 路由参数。其他表默认 `id` 不变。
- 商品列表查询明确传 `params:{}`，不继承浏览器当前搜索框里的过滤条件。覆盖口径是“当前账号可见、API 默认范围”，不是跨权限、跨租户、所有状态的全局全集。

分层关系：

```text
OpenCLI product-list / product-detail / product-section
  → ProductWorkflow：列表清单、差异计划、持久化快照、限额、恢复、核验
  → Lovrabet data filter / create / batchCreate / update
  → products + product_profile + product_child_records
  → sync_stream_events + sync_stream_status
```

## 2. Kimi 先做什么

1. 保持单写入者。检查 `lsof .runtime/locks/.kemai-writer.lock`。不要并行启动商品和零售写入。
2. 小样本现已完成（第 9 节）。后续先扩大到 5 个商品；尚未实测的其他页签分别先做 1 个样本。
3. 原小样本 capture 为 `f0a36020759b4eabb0304fa2c61a388d`，首次被锁拦截，后经重新登录与两处标准接口修复，现为 complete；再次 resume 不发源/DB请求。
4. 原零售 capture `6c962398842944aba826ea13ed8b3ed3` 已有 **14 页**，检查时处于 `writing`。本次未删除、重建、改写该 capture；继续用原来的 resume 命令，不要重新捕获同一窗口。
5. 本次没有启用 supervisor，也没有解除零售原详情/支付的暂停状态。

在项目根目录执行：

```bash
python3 scripts/kemai_opencli_runtime.py check
opencli doctor

# 锁空闲后：最多 2 次源逻辑操作，1 商品详情 + 1 barcode 页签
python3 scripts/kemai_products.py --max-operations 2 resume \
  --capture f0a36020759b4eabb0304fa2c61a388d --limit 1

python3 scripts/kemai_products.py status \
  --capture f0a36020759b4eabb0304fa2c61a388d
```

如果被其他写入者拦截，等待它在批次边界停下；不要删除锁文件、不要强行杀死正在写入的进程。若这个示例 capture 不在另一台机器上，先运行下面的 `plan` 创建本机计划。

## 3. 按需补商品与页签

```bash
# 只读本地数据中心，选择最前面的 5 个商品；plan 不读科迈、不写 DB
python3 scripts/kemai_products.py plan --limit 5 --section barcode

# 把 plan 输出的 capture_id 填入；run/resume 等价，都使用同一份断点
python3 scripts/kemai_products.py --max-operations 20 run --capture CAPTURE_ID --limit 5
python3 scripts/kemai_products.py --max-operations 20 resume --capture CAPTURE_ID --limit 5

# 指定源商品 ID；支持重复 --id
python3 scripts/kemai_products.py plan --id SOURCE_ITEM_ID \
  --section barcode --section supply --section retail_price

# 仅主档和完整详情 JSON
python3 scripts/kemai_products.py plan --limit 5 --detail-only

# 下一批：用上个 DB 计划的 next_after_id，不是源商品 ID
python3 scripts/kemai_products.py plan --after-id LAST_DB_ID --limit 5 --section stock

# 所有本机商品 capture 的摘要，不访问源或 DB
python3 scripts/kemai_products.py status
```

不指定 `--section` 时默认：`barcode,supply,retail_price,delivery_price,stock,scope`。
可显式选择全部 14 类页签：
`barcode,supply,retail_price,delivery_price,stock,scope,promotion,grading,pack,history_price,combination,mix,split,cut`。

这表示命令和工作流支持这些类型，**不表示 14 类都已完成真实验收**。未知响应结构会失败并保留断点，不会当成空页签成功。`history_price` 有独立分页预算，完整页签落盘后才入库。

`--limit` 是一次处理的商品对象数，1–100，默认 5。`--max-operations` 是本进程源逻辑调用上限，默认 250；一次详情/页签读取分别计数，历史价格每页也计数。它不是精确底层 HTTP 请求计数。

## 4. 发现新品、对比主档变化

源端没有已确认可靠的修改时间增量接口，因此先单轮取得当前可见清单，再按哈希筛选，不虚构时间游标。

```bash
# 单轮列表扫描；只保存源清单，不写业务 DB。每页最多 100 行
python3 scripts/kemai_products.py --max-operations 250 inventory --max-pages 200

# 以 inventory 输出的 capture_id 创建增量对象计划
# 对本机 DB 使用 $in 批量对比，选择新品、list_hash 变化、缺 detail_hash 的商品
python3 scripts/kemai_products.py plan --inventory INVENTORY_CAPTURE_ID --section barcode

# 分批执行，反复 resume 同一个对象计划直到 remaining_objects=0
python3 scripts/kemai_products.py --max-operations 20 run --capture OBJECT_CAPTURE_ID --limit 5
python3 scripts/kemai_products.py --max-operations 20 resume --capture OBJECT_CAPTURE_ID --limit 5

# 首次全量/明确需要刷新未变化对象时，覆盖整个清单
python3 scripts/kemai_products.py plan --inventory INVENTORY_CAPTURE_ID --all-objects
```

清单按 `totalRecord`、每页长度、唯一商品 ID 核对，不做第二轮业务扫描。不删除清单中未出现的本机商品。页码偏移可能变化：**未完成的 inventory 不能从旧页码接着算完整，必须新建扫描**；已经完成的清单则按固定 ID 队列续跑，不重扫列表。

详情和 profile 都回读成功，且清单与详情的 `version` 相同，才发布 `list_hash`，避免详情失败后被下一次增量误判为已同步。版本缺失/漂移时不发布比较哈希，留待下次清单重新识别。

**价格、库存变化不一定改变商品主档版本。** 主档增量计划只能解决主档变化；它不能替代独立的价格/库存定期刷新。页签刷新用第 3 节的 DB ID 游标分批计划，每次新计划重新读取所选页签。停机/冷却后先恢复未完成对象计划，再开启新一轮。不要拿旧清单无限循环充当新品发现。

## 5. 数据、幂等和覆盖口径

| 对象 | 实际存储与核验 |
|---|---|
| 主档 | `products` 按租户 + source_item_id 查找，回读验证；金额按源整数 /10000；缺失可选字段保留旧值 |
| 完整详情 | `product_profile` 按 product_id 标准更新，保留 entity JSON 和哈希 |
| 页签行 | `product_child_records`；按 product_id + section + source_key；新增分批 batchCreate，已有行标准 update，整体回读核对 |
| 每类同步日志 | `sync_stream_events`，stream 为 `product-object:detail` / `product-object:barcode` 等；同 capture/section 重试更新同一事件 |
| 最后同步时间 | `sync_stream_status` 的 last_attempt_at / last_success_at；**只表示所选对象批次**，coverage_level 固定 partial，不能显示为整类商品全集最新 |
| 源观察时间 | 页签 captured_at、私有快照及日志 details.source_observed_at；profile.updated_at 是数据库维护的更新时间，不作为源观察时间 |

- 新增商品行批次最多 100 条或 48KB。大 JSON 单行超限会提前失败。
- 已有历史页签键会按业务身份匹配复用，列表重排不按新下标盲插。身份不唯一则拒绝自动合并。
- 源端少行/空页签时，保留多余旧行并标 `partial`，报告 `retained_stale_rows`。没有自动删除或谎报与源完全一致。
- 旧 capture 不允许覆盖 DB 中观察时间更晚的商品/页签；需要建立新计划。
- 源快照先以私有文件持久化。DB 断连后同 capture 复用快照，并先查询已有键；写结果不明先回读，不能直接重放新增。
- 同 capture 已验证的对象/步骤不重读、不重复写；新的刷新周期必须创建新的计划。
- 价格、库存等旧的**结构化派生表**这次未刷新；当前入口写的是通用页签子记录。依赖旧派生表的页面不能据此宣称已经更新。
- 本入口不推进交易日级检查点、不关闭历史缺口台账，不修改原商品 `sync_objects` 的旧队列。新断点在 `.kemai-artifacts/product-objects/<capture>/`，同步结果进入上述 DB 日志表。

## 6. 限流、故障、恢复

- 共用现有源协调器，≤0.5 次/秒；业务错误/HTTP 429/页面限流触发 30 分钟共享冷却。
- 运行遇限流时保存 `cooling`，退出当前进程；冷却到期后 `resume`，不在后台悄悄重启。
- API 鉴权失效：人工恢复登录后续跑原 capture；不要重建已经保存的业务源快照。
- 缺失页签数组、身份不符、截断页、重复键、列表总数变化都会失败，不能冒充“空数据”。
- 数据库故障：保存本地断点并尽量记录 DB 失败事件；DB 不可用时日志写入也可能失败，恢复后补写 pending audit。
- 不包含常驻调度/自动选时器；这是可供调度器或 Skill 调用的受控命令。首先完成小样本，再由操作者决定批次、对象范围和更新节奏。

## 7. 2026-11-09 验证状态快照与回退

已完成：

- npm 官方源安装 2.0.3（原 npmmirror 持续 500），daemon 重启，扩展 1.0.27 重新连接。
- 适配器项目/运行时 **32 个文件哈希一致**，22 个命令静态加载验证。
- 商品、product_profile 非 id 主键、barcode 子记录的**真实标准 API 只读查询**成功。
- 最终离线回归：Python **462/462**、Node **52/52**。
- 离线测试覆盖两版 JSON、业务限流、主键映射、重复执行、DB 失败快照复用、页签重排、旧行保留、源身份错误、清单截断/重复、哈希篡改、旧快照防倒退、主档增量筛选及批次容量。

尚未完成：商品主档 + barcode 的真实写入回读小样本，以及其余页签真实契约验收。本次尝试时 PID 85166 正在恢复零售 capture，共享锁返回 `WriterBusy`；没有绕过锁。先按第 2 节交接再运行。文档数字是本次检查时的快照，不代表该外部任务后续状态。

回退只在所有写入/采集任务停止后执行：

```bash
npm install -g @jackwener/opencli@1.8.8 --registry=https://registry.npmjs.org
opencli daemon restart
python3 scripts/kemai_opencli_runtime.py check
```

新版项目解析器同时兼容 1.8.8，不需要把 Python 代码倒回旧版。CLI 升级前适配器/包版本备份：
`~/.opencli/upgrade-backups/20261109-232141/`。
适配器安装前逐文件备份：
`~/.opencli/kemai-source-gate/adapter-backups/adapter-backup-_89t5ht3/`。
不要覆盖/删除源门禁、冷却状态、capture、批次回执。没有做 DB schema 迁移，不需要回滚 DDL。

## 8. 2026-11-10 续验：锁已释放，源鉴权阻塞

用户确认让 Kimi 暂停后，已确认共享写入锁空闲，并续跑同一商品 capture。

- 本轮执行了 1 次工作流详情调用和 1 次限定同商品的诊断调用，科迈返回 `登录认证失败`。这是源端鉴权失败，不是限流，也不是标准 API 落库失败。
- 尚未保存成功的商品源快照，未写入商品主档、profile 或条码子记录；只记录失败状态/同步日志。
- capture `f0a36020759b4eabb0304fa2c61a388d` 保留，阶段为 `auth_required`。没有重建清单、重启零售任务或触碰其 14 页快照。
- 新增 `SourceAuthenticationRequired` 分类，兼容 OpenCLI 2.x JSON 错误信封；错误文案固定，不把原始错误栈、URL、业务内容写入公开日志。鉴权失败直接退出，等待人工重新登录。
- 新增鉴权错误与业务字段不误报测试，商品工作流与兼容测试合计 30 项通过。

人工在 Chrome 重新登录后，继续第 2 节同一 capture 的 `resume`，完成真实写入/回读后，再重复一次 `resume` 验证无重复源请求及业务写入。**商品真实写入验收仍未通过，不得把本节理解为已补齐商品数据。**

本次额外修复了日志核验：`sync_stream_status.updated_at` 为服务端维护字段，不再提交本机值并要求完全相等；仍严格核验 last_attempt_at、状态、计数、事件关联等字段。鉴权失败事件及状态已通过真实标准 API 回读确认；没有因此写商品业务数据。Python 全量回归 464 项通过，最后的日志字段修复另经商品/兼容相关 30 项回归及真实日志回读验证。


## 9. 2026-11-10 重新登录后的最终小样本验收

本节取代第 7、8 节的“尚未验收”当前状态，保留它们用于追溯。没有启动全量补采或常驻服务。

| capture | 验证范围 | 结果 |
|---|---|---|
| `f0a36020759b4eabb0304fa2c61a388d` | 1 个商品主档、完整 profile、barcode | complete；条码源 0 行、DB 0 行，正确验证空集合 |
| `50d44cd0f26d4d6ca2b2977c2a3addcf` | 另 1 个商品主档、完整 profile、supply | complete；供货源 1 行、DB 1 行，既有子记录标准更新及哈希回读通过 |

两份 capture 均再次执行 `--max-operations 0 resume`：**源操作 0、数据中心请求 0**。随后独立读取 DB，确认两个商品各只有 1 条主档和 1 条 profile；业务字段、完整详情内容/哈希一致；页签行数、哈希及键唯一性一致；两份 capture 各有 2 条成功事件，没有因重试新增重复事件。

实测发现并修复：

1. `lovrabet data update` 的 CLI 前置校验强制要求 `id`，即使数据集主键是 `product_id`。更新现在同时提供同值的通用 `id` 与真实 `product_id`；查询/排序/唯一性仍按真实主键。
2. `product_profile.updated_at` 与 `sync_stream_status.updated_at` 都是服务端维护字段。调用方不再提交和比较本机值；严格核验内容/哈希等业务字段。源观察时间保存在快照、对象步骤和事件 details 中。
3. 详情数据写入遇失败后复用了已保存快照，没有重复抓取同一份详情；已成功的步骤在同 capture 中不重写。

最终验证：Python 离线回归 **464/464**；两份真实 DB 验收及无操作重放通过。Node 代码本轮未改变，沿用此前 **52/52** 结果。

私有可复核证据：

- `.kemai-artifacts/product-objects/acceptance-2026-11-10.json`：独立 DB 回读结果摘要及未验证边界。
- 各 capture 下 `manifest.json`、商品 ID 子目录的 `detail.json` / `barcode.json` / `supply.json`：源快照、哈希和阶段耗时。
- 第二个 capture 下 `acceptance-before-replay.json` 保留首次成功执行指标：37.671 秒、2 次源逻辑操作、22 次数据中心请求（含日志/核验）；重放后的主 manifest 显示 0 操作。

仍然明确保留的边界：**非空条码写入、新增子记录 batchCreate、其余页签、整份 inventory 清单扫描尚未获得本次真实验收**。供货样本为既有行更新，不能冒称新增路径也已实测。上述路径有离线测试；首次上线仍按 1 个样本 → 5 个商品逐步扩大。结构化派生表仍未在此入口刷新。

Kimi 可从下面的受控计划继续已测供货路径；先看 plan 输出，使用其新 capture，保持单写入者：

```bash
python3 scripts/kemai_products.py plan --limit 5 --section supply
python3 scripts/kemai_products.py --max-operations 10 run --capture NEW_CAPTURE_ID --limit 5
# 若遇冷却/断连，用同一个 NEW_CAPTURE_ID resume，不重新 plan
```
