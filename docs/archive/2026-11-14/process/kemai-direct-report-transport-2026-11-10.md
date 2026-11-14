# 科迈报表直连只读传输：实现与交接

日期：2026-11-10。状态：**代码与离线测试完成；没有执行真实源端请求或数据中心写入。**

## 实际调用路径

| 用途 | 当前路径 | 约束 |
|---|---|---|
| 获取会话凭据 | `opencli browser eval --session kemai`，只读已有 `PC_TOKEN` | 凭据仅留在进程内存，不写报告或日志 |
| 列表采集、商品详情 | 原 OpenCLI `capture-page` / 商品命令 | 仍由 OpenCLI 共享源请求门禁保护 |
| 列表总量对账 | `kemai_pipeline/direct_read.py` 固定白名单直连 `queryStatisticalInfo`；异常时才考虑列表分页 | 每次 HTTP 请求独立持有共享源锁，≤0.5 次/秒；限流后共享冷却 30 分钟 |
| 已关闭的零售日窗独立总数校验 | 列表仍走 OpenCLI，统计总数由 Python 直连 | 先校验本轮 `query_scope` 与直连日期、时间类型和分支完全一致；不一致不封存 |
| 科迈导出任务创建 | 已在 OpenCLI 共享门禁及 Python source port 阻断 | 新建导出任务会改变科迈侧状态，不可调用；历史收货 Excel 新采集因此暂不能启动 |

`direct_read.py` 只接受代码内固定的七个报表列表/统计路径。不能由命令行传任意 URL。采购单/收货/退货采用制单日期；零售订单采用 `dateType=0`，零售流水采用上传时间 `timeType=1`。零售分支从 `source_contracts.json` 读取并显式提交。历史收货报表和让利报表虽可只读取统计总数，但时间字段尚未与库内数据证实一致。

本地标准 API 网关源码及生成的 BFF 文件已接受 `statistical_total_and_capture`，且只对已关闭的零售日窗放行：总数、上传时间、分支和数据库同范围计数必须一致。**此 BFF 变更尚未发布到数据中心**；当前生产网关仍按旧导出证据判断，因此不能启动这条新正式采集路径。部署须另行按现有标准 API 验收流程进行。

## 对账输出的解释

运行 `python3 scripts/kemai_total_reconcile.py --mode api --stream retail-orders --days 1 --dry-run` 可在零源请求下查看预算。实际 `--mode api` 使用共享锁；不再用一秒 `--interval` 充当限速。

- `match/mismatch` 只用于有可比时间字段与分支范围的零售订单及三条采购单据流；仍只是**行数比较**，不能证明对象键或详情一致。
- 零售流水源端按上传时间；库内也按 `source_created_at` 和同一分支计数。库内查询失败时报告为 `db_window_unavailable`，不得用销售时间 `sold_at` 冒充。
- 收货历史、让利报表报告为 `scope_unverified`；源端统计总数仅供参考。
- 源端返回零且缺少独立参数验证时报告 `params_unverified`；请求失败与限流不转换为零。
- 2026-11-10 实测零售报表当前接口对 10 月 14 日返回零，而库内保留当时的正式日窗；大跨度统计不能直接用作历史全量分母。详见 `docs/kemai-sync-completion-check-2026-11-10.md`。
- `source_operations` 是本进程发起的逻辑源请求次数，不是浏览器所有 HTTP 数量。

## 后续验收边界

直连列表尚未替换正式 `capture-page`。切换前需在只读小窗口比较直连与 OpenCLI 的 `query_scope`、原始主键集合、分页终止、统计总数与分支范围；尤其核实零售流水上传时间、采购 `queryType` 和零值窗口。不得用计数相同代替主键核对。历史收货需设计无需创建源端导出任务的只读采集及字段映射，才可恢复该流的新采集。

离线回归：`python3 scripts/test_kemai_offline.py` 600/600；`node --test tests/pipeline/*.test.mjs` 59/59。适配器 `manifest.json` 与 `source-gate.js` 已同步至本机 OpenCLI 安装目录。
