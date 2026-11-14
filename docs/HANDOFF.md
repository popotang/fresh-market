# Fresh Market 项目移交手册

**版本**：handoff-2026-11-14  
**项目**：科迈云帆（Kemai）只读同步到 Lovrabet 数据中心（`app-21522951`）  
**当前状态口径**：本页只描述仓库代码和配置；运行状态、覆盖率、缺口必须以执行时的只读命令重新确认。

## 1. 先读什么

1. [`AGENTS.md`](../AGENTS.md)：最高优先级安全边界。科迈 SaaS 永远只读。
2. [`PLAN.md`](PLAN.md)：移交后分阶段执行方案和放行门槛。
3. [`ARCHITECTURE.md`](ARCHITECTURE.md)：当前分层、数据流、组件职责和放行边界。
4. [`OPERATIONS.md`](OPERATIONS.md)：接手、检查、计划、执行、恢复和停机步骤。
5. [`kemai-pipeline-operations-2026-11-08.md`](kemai-pipeline-operations-2026-11-08.md)：标准操作的详细历史参考；与本页冲突时以本页和代码为准。
6. [`kemai-standard-api-delivery-2026-11-08.md`](kemai-standard-api-delivery-2026-11-08.md)：已验证的标准接口证据。

历史计划、日报和一次性快照统一在 [`archive/2026-11-14/`](archive/2026-11-14/)；不应按实时资料使用。

## 2. 系统一句话

OpenCLI 通过受控只读适配器从科迈读取；`kemai_pipeline/` 保存不可变捕获、做确定性校验和分批；`LovrabetAdapter` 通过标准模型 API 读写自有数据中心；BFF、数据集和页面只读消费副本。AI/Skill 只选择范围和解释证据，不承担去重、事务或放行。

## 3. 当前放行矩阵

放行是 `kemai_pipeline/acceptance.json` 中的代码审查配置，不等同于“源端全量完成”。当前已具备真实闭环证据的流如下：

| 流 | 当前覆盖 | 可做的事 | 明确限制 |
|---|---|---|---|
| `retail-sales` | 列表 | 有界窗口、同 capture 恢复/重放、数据中心写入 | 不代表订单详情、支付或全部门店 |
| `retail-orders` | 列表 | 有界窗口、同 capture 恢复/重放 | `orderDetails`/`payFlows` 不随列表完整提供 |
| `purchase-orders` | 列表 | 采购订单小窗口闭环和重放 | 详情、分支、审批仍是独立覆盖 |
| `purchase-receipts` | 列表 | 采购收货小窗口闭环和重放 | 详情、分支、审批仍是独立覆盖 |
| `purchase-returns` | 列表 | 采购退货小窗口闭环和重放 | 详情、分支、审批仍是独立覆盖 |

以下范围当前不能按已完成处理：

- `purchase-receive-history`：历史工件存在哈希不一致和未完成的真实数据中心闭环，保持 no-go。
- `retail-promote-sharing`：能力表标记 `paused`；不要通过通用增量命令激活。
- 零售订单详情、支付：代码和能力表都标记暂停。
- 商品详情、14 类页签、库存/价格对象：有源适配器和部分本地数据，但不属于当前交易流 accepted 白名单。
- 常驻 supervisor、OS 开机自启、多机租约：仓库没有默认启用，也不能把前台脚本当成已部署服务。

查看当前代码口径：

```bash
python3 kemai_syncctl.py capabilities
cat kemai_pipeline/acceptance.json
```

## 4. 接手前置条件

接手人需要从系统所有者处单独取得并在本机配置：

- Lovrabet CLI 登录和 `app-21522951` 的读写权限（写入范围只限自有数据中心）。
- OpenCLI 的科迈只读登录态；不得把 Cookie、Token、密码或签名 URL 写入仓库、日志或文档。
- 需要执行源读取时，确认当前连接账号仍只覆盖 `source_contracts.json` 中的门店范围。
- 运行机上的 `.runtime/`、`.kemai-artifacts/`、`.local-audits/` 私有目录及其权限。

不把凭据写进脚本或交接文件；无凭据时仍可完成本地测试、配置检查和历史工件审计。

## 5. 第一次接手检查

```bash
# 不访问科迈源站；读取本地能力和数据中心状态
python3 kemai_syncctl.py capabilities
python3 kemai_syncctl.py status
python3 kemai_syncctl.py gaps show

# 只读核对一个明确窗口；把日期替换成实际要复核的已结束日
python3 kemai_syncctl.py history check --scope db \
  --stream retail-sales --from 2026-11-01 --to 2026-11-07

# 本地代码与依赖验证
python3 scripts/kemai_opencli_runtime.py check
python3 -m unittest discover -p 'test_kemai*.py'
node --test tests/pipeline/*.test.mjs
```

若要执行会访问科迈或写入数据中心的命令，先阅读 [`OPERATIONS.md`](OPERATIONS.md)，确认范围、预算、锁和回退条件；不要从历史文档复制旧 supervisor 命令。

## 6. 交接验收清单

- [ ] 接手人能说明科迈只读边界，并知道任何源端写请求必须拒绝。
- [ ] `capabilities`、`status`、`gaps show` 的输出已保存为本地审计文件，带生成时间。
- [ ] 数据中心 AppCode、租户和数据集映射与 `kemai_pipeline/datasets.json` 一致。
- [ ] `.runtime/`、`.kemai-artifacts/`、`.local-audits/` 已完整迁移并保持权限；私有源工件未提交。
- [ ] 当前没有第二个写入者；writer lock、流级事务锁和 source gate 可用。
- [ ] 只在明确的小窗口执行一次 plan，再由授权操作员执行 run；执行后完成回读和重放检查。
- [ ] 暂停流、未知覆盖、`partial` 状态和历史 no-go 范围已记录，不被包装成“全量完成”。
- [ ] 页面、BFF 和指标口径文档指向自有副本，不向科迈发起写请求。

## 7. 需要升级处理的情况

遇到以下任一情况，停止当前执行并保留 capture、回执和日志：源限流/认证失败、源筛选范围变化、分页未完成、写入结果未知、摘要冲突、数据中心数据集未注册、锁冲突、窗口计数不一致。由项目负责人决定是否以新的明确窗口重新计划；不要删除工件、改检查点或盲目重试。

业务纠正如果确实需要改变科迈侧数据，必须由数据所有者本人在科迈官方界面处理；本项目只能查询、导出和修正自有数据中心副本。
