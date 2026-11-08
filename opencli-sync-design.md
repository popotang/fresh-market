# 科迈云帆 OpenCLI 抓取命令与同步设计

设计日期：2026-11-06。以下是**拟实现**的命令，不是当前已经安装的 `opencli kemai` 适配器。当前 OpenCLI 1.8.8 没有科迈站点命令；已观察到的接口和样本见 `data-model-analysis.md`。

## 能力边界

建好业务表后可以开始全量抓取，但可靠同步还需要登录态、接口重放验证、源 ID 映射、采集快照、断点和对账。不能把数据库建表等同于增量同步已成立。

当前商品列表 `/web/api/product/item/getListData` 的请求参数可见 `startGmtCreate/endGmtCreate` 和 `startApproveTime/endApproveTime`，**未验证最后修改时间筛选**。单品 `/web/api/product/item/doLoadEntity` 返回 `gmtModified` 和 `version`，但必须先知道 `itemId` 才能读取。因此无法仅凭一个 `--modified-since` 命令证明不会漏掉旧商品的修改。价格、库存、经营范围和子项也可能独立变化。

### 适配器策略门槛

- 目标是登录态下的未文档化 JSON 接口，维护契约按 `internal-unstable` 处理。
- 候选实现：`browser: true`，优先验证 `page.fetchJson()` 能否用当前 Chrome 登录态读取 `/web/api/...`；若接口依赖额外请求头或跨域规则，再验证 COOKIE 策略。实际认证来源和跨两个 `itemId` 的只读重放**尚未验证**，不能直接把浏览器抓包当作可重放契约。
- 若直接读取不可复用，退到自然页面操作和 `INTERCEPT`；保留原始响应与字段映射的版本记录。任何方式都不把 SaaS 密码、Cookie、Token 写进命令参数、日志或项目文件。
- 私人适配器放 `~/.opencli/clis/kemai/`。每条命令固定 `columns`，用 `-f json` 返回一条包含 `meta` 与 `items`/`payload` 的结构化记录，让空列表也有明确的 `totalRecord=0`。

## 原子读取命令

| 拟定命令 | 参数 | 返回 / 来源 |
| --- | --- | --- |
| `opencli kemai whoami` | `--expected-tenant` 可选 | 当前租户/登录身份/权限探测；在同步开始前确认不会写错租户 |
| `opencli kemai item-page` | `--page`, `--page-size 100`, `--created-from`, `--created-to`, `--approved-from`, `--approved-to` | `item/getListData`：`page,totalRecord,items[]`，保留 `itemId/code/version` 和完整列表原文 |
| `opencli kemai item-detail` | `--item-id` | `item/doLoadEntity`：157 字段主档样本所对应的 `entity`，含 `gmtModified/version` 和源 ID |
| `opencli kemai item-section` | `--item-id`, `--kind` | 已验证的单品详情接口；返回 `itemId,kind,payload`，空数组也是成功结果 |
| `opencli kemai master-page` | `--kind`, `--page`, `--page-size` | 机构、类别、部门、品牌、单位、课组、经营范围群组、做法等主数据列表 |
| `opencli kemai relation-page` | `--kind combination\|split\|mix`, `--page` | 组合/拆分/混售关系列表；优先批量列表，避免每商品逐个探测 |
| `opencli kemai relation-detail` | `--kind`, `--parent-item-id` | 父商品及子项，保留 `subItemId/combineRule/splitRule/sequence` |
| `opencli kemai sheet-page` | `--kind retail-price`, `--page` | 调零售价单列表；其他单据类型要逐项验证后再开放 |
| `opencli kemai sheet-detail` | `--kind retail-price`, `--sheet-id` | 调零售价单头、商品明细、机构范围；其他单据类型同上 |

`item-section --kind` 第一批支持：`supply`, `retail-price`, `delivery-price`, `packs`, `barcodes`, `grades`, `purchase-history`, `promotions`, `stock`, `scope`, `split`, `cut`。组合、混售、做法、群组、销售码等走已验证的批量接口或编辑页关联接口，不凭空假定每个 Tab 都存在同形的单品接口。未知 `kind`、无权限、登录失效、接口结构变化分别返回明确错误，不把异常当作空数组。

示例：

```bash
opencli kemai whoami -f json
opencli kemai item-page --page 1 --page-size 100 -f json
opencli kemai item-detail --item-id 2118191752054538304 -f json
opencli kemai item-section --item-id 2118191752054538304 --kind stock -f json
opencli kemai master-page --kind category --page 1 --page-size 100 -f json
opencli kemai sheet-detail --kind retail-price --sheet-id 2118511718498504724 -f json
```

这些示例仅说明目标接口形状；运行前需创建、验证适配器。读取命令不连接 MySQL，不承担水位推进或跨表事务。

## 同步编排命令

用独立的 `kemai-sync` 执行器编排上述读取能力、转换字段并写 MySQL。它可以在实现后注册成 OpenCLI external CLI，但不应把 18,383 商品的循环、MySQL 写入和重试塞进单个站点适配器的 `func`。人工排查可逐条调用 OpenCLI 命令；批量同步应复用同一已认证连接/提取逻辑，避免为数万次请求反复启动 CLI 进程。拟定调用：

```bash
kemai-sync full --resume --db-dsn-env FRESH_MARKET_DSN
kemai-sync incremental --overlap-hours 24 --db-dsn-env FRESH_MARKET_DSN
kemai-sync reconcile --scope catalog --db-dsn-env FRESH_MARKET_DSN
kemai-sync reconcile --scope detail --shard 0/7 --db-dsn-env FRESH_MARKET_DSN
```

`--db-dsn-env` 只指定环境变量名；数据库密码由外部密钥管理注入，不放在命令行。若需统一使用 `opencli` 前缀，可将执行器注册为 external CLI，届时是 `opencli kemai-sync ...`，并不是当前已存在的命令。

### 全量 `full`

1. `whoami` 核租户和权限；读取机构、类别、部门、品牌、单位、供应商等基础主数据。供应商接口本轮尚未核验，须先补证据。
2. 执行 `item-page`，以 100 条分页，当前规模约 **184 页**。记录每页请求、行数、`totalRecord`、源 ID 集合和响应哈希；优先验证稳定排序。发现页间重复或总数变化时重扫相关页，不推进完成状态。
3. 按 `itemId` 执行 `item-detail`；映射 `products/product_profile`。再用 `item-section` 和批量关系/单据命令采集子表。能用完整的批量列表时，不做 14 个 Tab × 全部商品的盲目轮询。
4. 每个实体先写 `source_capture`，再按源 ID 幂等 upsert 目标表；子表按本次完整响应更新。分页、单品、关联分阶段存 checkpoint，失败可从精确位置续跑。
5. 全部完成后对账：源端 `totalRecord`、去重 `itemId` 数、主表数、子表数、错误数。任何一阶段未完整，不标记全量成功，也不根据缺失列表删除商品。

若直接对 18,383 商品调用 1 个主档 + 14 个详情接口，理论上约 **27.6 万次请求**（另加列表和字典），因此要先找批量接口、分阶段执行并限速；实际调用量以接口验证后的覆盖表为准。

### 增量 `incremental`

1. **快增量**：分别用已观察到的建档/审核时间过滤，按上次成功水位回退重叠窗口（示例 24 小时）抓新建和新审核商品。用 `(tenantId,itemId)` upsert；只有本批所有页、详情、写入和对账成功才推进对应水位。
2. **每日目录对账**：全扫约 184 个商品列表页，比较 `itemId` 集合、`version` 和列表字段哈希。变化或新 ID 再抓 `item-detail`；只把 `version` 当候选变化信号，因为尚未证明它涵盖所有子项变化。
3. **详情轮询**：对供货资格、门店价格、库存、经营范围、包装/条码和关系按业务时效单独排程。价格单据与商品关系有独立的分页列表；库存是当前状态，按指定周期重读。没有可靠变更接口的子项做轮转深扫，例如每天扫 1/7，七天覆盖一次；敏感或高频业务可以提高频率。
4. **删除/停用**：只有完整目录重扫后仍缺失的 ID 才进入待确认集合；复核后软删除或更新状态。不要因分页漂移、权限变化、请求失败立刻删除。
5. **水位与错误隔离**：商品、价格单据、库存、字典、关系各有独立水位和失败队列。401/403 停止并报警；429 限速退避；5xx 有上限重试。日志记录 ID、接口、时间、次数和错误码，不记录认证材料或完整敏感响应。

严格的准实时增量仍取决于科迈是否提供修改时间过滤、变更日志或 webhook。当前观察到的接口不足以证明它成立；以上方案通过目录全扫与详情轮询保证**有界的最终一致性**，延迟由轮询周期决定。

## 落地顺序

先验证 `item-page` 的页码、总数、稳定排序与两个时间筛选，再验证 `item-detail` 对两个不同商品的认证与字段单位；随后实现 `master-page` 和高价值详情接口。通过小批量 100 商品端到端对账后，再启动全量与增量编排。现有 `product_items` 只有 100 条 CSV 样本且缺 `source_item_id`，应先回填源 ID 并迁移到新模型。同步控制至少还需要运行记录、checkpoint 和错误队列；它们是操作表，不包含在前述 12 张核心业务表中。
