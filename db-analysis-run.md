# app-21522951 数据库全表分析运行记录

- 数据库连接 ID：`10510`，库名 `fresh-market`；`db test` 已通过。
- 首次差异刷新 trace ID：`39fa327e-09bf-46ea-96bc-5b5fdc3bffc6`，状态 `SUCCESS`。
- 全表事实：18 张物理表，0 个已覆盖 Dataset；18 张均为 `NEW_TABLE`。
- 本地批次方案：10 张 + 8 张，严格串行。

## 第一批

- plan ID：`4e76177d-3d5b-4da6-b68a-508b30440965`
- 表：`branches,brands,categories,departments,product_branch_prices,product_branch_scopes,product_branch_stocks,product_child_records,product_items,product_profile`
- 终态：`SUCCESS`，完成耗时约 837 秒。

## 第二批

- 表：`product_supply_qualifications,products,source_capture,suppliers,sync_checkpoints,sync_objects,sync_runs,units`
- plan ID：`307ea62e-023a-40f9-97a3-c2c2de4be82e`。第一批成功后启动；终态 `SUCCESS`，完成耗时约 744 秒。

## 完成口径

两批到终态后，再做一次异步差异刷新并读取完整 `db diff --all`。只有目标 `toAnalyzeTables` 清空、子应用 `api pull` 得到模型且类型检查和构建通过，才报告分析完成。ER 图：[查看应用关系图](https://app.lovrabet.com/app/app-21522951/data/er?dbId=10510)。

## 最终核验（2026-11-06）

- 最终差异刷新 trace ID：`57cd5544-76eb-482c-b7de-3c8bcc3beeba`，状态 `SUCCESS`。
- `db diff --all`：`toAnalyzeTables=[]`，无新增、修改或删除差异。
- `db tables`：物理表 18、Dataset 覆盖 18，全部为 `ALREADY_ANALYZED`。
- `fresh-market-web` 执行 `api pull`：18 个 Dataset 模型，`needsProjectUpgrade=false`；`npm run typecheck` 和 `npm run build` 通过。
- 平台自动建立 8 条 Dataset 关系；关系审计为 `ok=3`、`warning=5`、`error=0`。5 条 warning 都指向商品子表关联的目标展示字段是技术性 `products.id`；关系本身存在，页面展示时建议改用 `products.item_name`，尚未修改平台关系。
- `sync_objects` 与 `sync_checkpoints` 的物理主键是复合键，平台模型列表只标出 `tenant_id` 为主键；这些控制表不宜直接作为普通业务 CRUD 页面使用。
- 商品到类别、品牌、部门、供应商、单位以源系统字符串 ID 保存；后续已建立平台逻辑关系，页面关联查询仍需同时核对租户键。

## 采购与零售增量分析（2026-11-06）

- 新建 `schema_commerce.sql` 中 8 张业务表；初次差异刷新 trace `c998da5a-cd97-4685-a95a-7a48630e3132` 成功，差异恰为 8 张 `NEW_TABLE`。
- 单批增量分析 plan `d47e89c7-e0c7-4e1e-819a-9481bd03d99d`，8 张目标表终态 `SUCCESS`，平台耗时约 893 秒。
- 最终差异刷新 trace `2eefde14-5b52-4e62-a444-e11ba2ddc407` 成功；`db diff --all` 的 `toAnalyzeTables=[]`，`db tables` 显示物理表 26、Dataset 覆盖 26。
- 平台自动识别 5 条新表内部外键关系；基于源 ID 的真实样本关联，另建立 11 条到商品、供应商、机构和零售订单的逻辑关系。
- 关系审计：共 29 条，`ok=24`、`warning=5`、`error=0`；5 条 warning 都是此前商品子表关系使用技术性 ID 作为显示字段，采购与零售新关系无 warning。
- `fresh-market-web` 已执行 `api pull`，当前 26 个 Dataset 模型；类型检查和构建通过。

## 历史收货补表（2026-11-06）

- 同一 SaaS 的“收货退货明细”提供“历史已审核”查询，故新增 `purchase_receipt_history_lines`，保存报表特有的历史收货行字段；报表 `id` 经验证是单据 ID，完整单头和明细可再用详情接口取得。
- 差异刷新 trace `105ddfd7-0110-451f-a029-9234dc0f7ee8` 后仅此表为 `NEW_TABLE`；增量分析 plan `ef5bf6c8-58ce-4302-9cec-241f39c6ec20` 为 `SUCCESS`。
- 最终差异刷新 trace `8e44185c-8592-4c7e-af73-ed3fcecbf3d2` 成功；27 张物理表和 27 个 Dataset 全覆盖，`toAnalyzeTables=[]`。
- 历史收货行到商品、供应商、机构及采购单据新增 4 条关系；关系审计共 33 条，`ok=28`、`warning=5`、`error=0`。5 条 warning 均来自旧商品关系。
- 子应用已拉取 27 个模型，类型检查与构建通过。
- 报表 ID 粒度纠正：原先把报表 `id` 当行 ID，已迁移为 `source_document_id`，新增按单据 ID、行序号、商品 ID 计算的 `source_line_key`。迁移前的两条探索性样本已清除并重新采集；修正后的单表差异分析 plan `482152c8-bd90-472b-a574-5806e2129692` 已 `SUCCESS`。最终差异刷新 trace `d50e3fd5-9b2f-4c0c-867a-14f88fc61316` 成功，差异清零，子应用 27 个 Dataset 模型重新拉取并通过类型检查、构建。
