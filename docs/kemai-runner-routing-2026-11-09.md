# Runner 选路与 Kimi 接续（2026-11-09）

## 最新用户决定

**采购订单、采购收货、采购退货的列表固定使用列表分页接口。** 不因数据量大、停机时间长而改用 Excel，也不再为这三类列表开发导出适配。需要商品行时才使用现有批量详情命令。`routing_policy.json` 的 `api_only_streams` 固化此决定；对这三类流指定 `--strategy excel` 会返回 blocked。

「收货/退货历史明细报表」是另一条流 `purchase-receive-history`，对应另一张历史明细表。其 Excel 功能保留，不能用它冒充采购列表或采购单据详情。

## 已落地代码

- OpenCLI `capabilities` 新增 `source_transports` 元数据，分别列出列表、导出任务、下载和批量详情命令。没有把路由或数据中心写入搬进 OpenCLI。
- `kemai_pipeline/routing.py` 根据流能力、必需字段、策略、窗口扫描量、停机积压、已验证映射及验收状态给出方法和理由。
- `history plan/fill` 和 `incremental plan/run` 接入选择结果。执行时重新读取本地契约；不能通过修改计划里的 verified/accepted 字段放行。
- 已有服务端 capture 优先按原方法恢复，不能因为本次阈值不同换通道。已下载并通过哈希核验的 Excel 直接复用，不再轮询源任务或重新下载。
- 导出提交结果未知时保留 capture，下一次按原 capture 找回精确任务，不再次提交。限流共用 30 分钟冷却，不切换 Excel/API 绕过冷却。
- 新 capture 保存 `routing`，完成窗口的 evidence 同时带上路由决定，随既有 commit_window 协议提交。真实服务端落库/页面展示本轮未执行验证。
- 默认增量只同步基础数据，已有详情缺口保留为 on_demand。明确指定 `--required-field quantity` 等商品行字段时，Runner 先完成基础日窗，再调用 LinesOnlyWorkflow，以每轮最多 10 单补行。分支/审批仍不自动加入。`--include-details` 为显式恢复原完整详情缺口调度的入口。

当前采购商品行补采继续使用 [批量详情接续文档](kemai-opencli-batch-details-2026-11-09.md) 的 `scripts/kemai_lines_only.py resume`。不要为了启用选路器，把已进行的补采切成一个新 capture。

## 查询选择结果

以下命令只读本地配置和工件，不访问源站、浏览器或数据中心：

```bash
python3 kemai_syncctl.py route --stream purchase-receipts --estimated-rows 20000
python3 kemai_syncctl.py route --stream purchase-receipts --required-field quantity
python3 kemai_syncctl.py route --stream purchase-receive-history --estimated-rows 20000
python3 kemai_syncctl.py route --stream retail-sales --strategy excel
```

对应结果：采购列表为 `api_only/user_policy_api_only`；商品行需求为 `needs_detail`；收货历史推荐候选 `export_for_data` 但仍因 `stream_acceptance_required` 阻止执行；零售 Excel 强制数据导入因 `export_count_only` 阻止执行。`route` 的非 selected 结果退出码为 2，表示需要补充路径或存在限制，不表示已经开始同步。

## 计划与执行入口

```bash
# 采购列表，明确按列表接口；plan 不抓源、不写数据。
python3 kemai_syncctl.py history plan --stream purchase-receipts \
  --from 2026-10-23 --to 2026-10-23

# 全局基础增量；缺口和最后连续完成日期仍来自 DB 覆盖证据。
python3 kemai_syncctl.py incremental plan --stream purchase-receipts

# 经当前任务范围允许后执行；不与已有 writer 并行。
python3 kemai_syncctl.py incremental run --stream purchase-receipts

# 明确需要商品行：基础日窗提交成功后才补商品行。
python3 kemai_syncctl.py history fill --stream purchase-receipts \
  --from YYYY-MM-DD --to YYYY-MM-DD --required-field quantity
```

上述执行例子不是本轮已运行的命令。`required-field` 使用规范化字段名；名称、条码等目前只在 payload 中，不接受 `item_name` 作为已存在的结构化列。结果 `requested_scope=lines_only` 和 `base_window_committed` 用来区分基础列表和商品行完成度，不关闭日级附属详情缺口。

## 其余流的选择规则

先判断导出字段、目标时间范围/状态、映射和验收是否满足，再比较成本。当前实际能力：

| 数据 | 列表 | Excel 数据导入 | 详情 |
|---|---|---|---|
| 采购订单/收货/退货 | 用户指定固定使用 | 不用于这些列表 | 已有批量详情，按需补录 |
| 收货/退货历史明细报表 | 已有 | 解析/映射已有；正式流仍未验收 | 不等于采购商品行表 |
| 零售订单/流水 | 已有 | 当前只验证了导出总数核对，不能作为数据替代 | 原详情/支付仍暂停 |
| 商品及页签 | 独立对象工作流 | 不受本轮日窗路由直接控制 | 保持现有边界 |

对具备同一目标数据契约的通道，默认策略是：扫描窗口达到 5,000 行优先考虑 Excel；已知小窗口用 API；数量未知且积压不少于 3 天时考虑 Excel。采购三条列表的用户固定策略优先于这些阈值。

`--estimated-rows` 指每个日窗预计需要扫描的总行数，`--missing-rows` 只是缺口提示。缺 1 行不代表列表接口能只取那 1 行；源与 DB 总数相等也不代表内容一致，因此不会按差值从某页直接续扫或宣布完成。历史窗口仍逐日核验，本轮没有将多个日期合成一个不可区分覆盖的大文件。

默认增量使用最近 7 天内已验证日窗行数的中位数估算后续扫描量，无额外源探测。估计仅用于成本选择，不作为本次真实总数或同步成功证据；无近期证据时保留 unknown。计划输出 `estimate_basis` 和 `routing_summary`。

阈值在 `kemai_pipeline/routing_policy.json`。可选 `.kemai-artifacts/routing-costs.json` 为按流组织的成本观察：

```json
{
  "purchase-receive-history": {
    "scope": "same_window",
    "window_time_field": "approved_at",
    "measured_at": "2026-11-09T12:00:00+08:00",
    "samples": 3,
    "api_page_seconds": 5,
    "export_fixed_seconds": 30,
    "export_row_seconds": 0.0001
  }
}
```

此处是字段格式示例，不是真实测量，本轮没有写入这组值。观察至少 3 次、7 天内、同流同时间口径才采用；成本预计至少节省 20% 才选 Excel。过期/非法数据忽略。现有 timing 口径不同，尚未自动拟合这份成本文件，不能将它写成已完成的自学习功能。

## Excel 离线实证与尚未完成的衔接

新增 `scripts/kemai_excel_compare.py`，只解析本地 Excel 和带哈希的已保存采购单头工件，输出脱敏计数：

```bash
python3 scripts/kemai_excel_compare.py \
  --xlsx .samples/purchase-receive-history-2026-11-08.xlsx
```

结果见 [本地对比报告](kemai-excel-local-comparison-2026-11-09.json)：352 行、28 单；28 单全部唯一匹配，机构/供应商/仓库及单据金额一致。名称、编码、条码、采购单位、数量、金额、税额各 352 行有值；规格 120 行有值。

但这 28 单缺同批详情行对照，不能证明内部行 ID、每行数量/金额/单位缩放或跨来源幂等合并。报告始终不授予入库验收。**本轮没有将 Excel 写入 purchase_document_lines，没有虚构内部行 ID，也没有完成 Excel→采购商品行的关联入库。** 用户已决定采购列表走 API，这项跨表补录可独立按需推进，不能混在“路由已完成”里宣称也已完成。

## 验证与上线边界

本轮无源采集、无生产 DB 写入、无 BFF 发布、无 supervisor 启动、未改 acceptance。OpenCLI manifest 已在取得共享 writer/source 锁后安装，保留备份。离线覆盖包括阈值、必需字段、固定 API 策略、暂停/冷却、旧 capture 固定恢复、成本数据过期、导出结果未知恢复、Excel 工件复用、基础先于详情、旧页签/同步回归。

最终验证：Python **434/434**、Node **47/47** 通过；源码 AST/空白检查及 `git diff --check` 通过，runtime 与仓库文件一致。三条采购列表分别以 1,000,000 行、31 天积压作静态验证，均返回 `api_only / user_policy_api_only`，源请求与 DB 写入均为 0。测试日志在本机 `/tmp/kemai-routing-tests.log`、`/tmp/kemai-routing-node-tests.log`。

运行回归：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_kemai_offline.py
node --test tests/pipeline/*.test.mjs
git diff --check
```

不需要重启或发布 BFF；新 Runner 进程加载本地改动。当前采集是否继续，由 Kimi 沿用既有实际 capture 和单 writer 规则处理。
