# Fresh Market 操作手册

**版本**：operations-2026-11-14  
**执行原则**：科迈 SaaS 永远只读；允许的写入只发生在本地/自有 Lovrabet 数据中心副本。

## 1. 操作前检查

```bash
# 仓库根目录执行
cd /Users/tangshuang/data/gitlab/yuntoo/biz-case/fresh-market
python3 kemai_syncctl.py capabilities
python3 kemai_syncctl.py status
python3 kemai_syncctl.py gaps show
python3 scripts/kemai_opencli_runtime.py check
```

确认：目标流在 `acceptance.json`，范围是明确的已结束日期，源 gate 未冷却，没有其他写入者，私有工件目录可写，数据集映射不是 pending。任何一项不满足就停止。

## 2. 只读核查

这些命令不会访问科迈源站，也不会写数据中心业务行：

```bash
python3 kemai_syncctl.py capabilities
python3 kemai_syncctl.py status --stream retail-sales
python3 kemai_syncctl.py gaps show --stream retail-sales
python3 kemai_syncctl.py history check --scope db \
  --stream retail-sales --from 2026-11-01 --to 2026-11-07
python3 kemai_syncctl.py incremental plan \
  --stream retail-sales --max-catchup-days 2
```

`history check --scope db` 只比较已存捕获证据和数据中心事实；不是现场源端对账。`plan` 只输出范围、任务、预算和阻断原因。

## 3. 有界执行

先执行 plan，再由明确的授权操作员执行 run。不要把历史文档中的旧 supervisor 命令直接复制到生产。

```bash
# 当前上传日 + 最多两个已结束追赶日；预算按逻辑源操作计算
python3 kemai_syncctl.py incremental run \
  --stream retail-sales --max-catchup-days 2 --max-source-operations 80

# 指定单日窗口（最多 31 个已结束日期）
python3 kemai_syncctl.py history plan \
  --stream retail-sales --from 2026-11-06 --to 2026-11-06
python3 kemai_syncctl.py history fill \
  --stream retail-sales --from 2026-11-06 --to 2026-11-06 \
  --max-source-operations 80
```

执行命令会读取科迈并可能写入自有数据中心；它不会写科迈。源请求共享上限为 0.5 次/秒，触发限流后静默 30 分钟。`--max-source-operations` 是逻辑源操作预算，不是浏览器 HTTP 请求数。

当前自动编排禁止新建源端导出任务；`*-export-start` 不能作为同步写入的替代路径。导出只可在明确的只读核对流程中使用，并保留任务身份和脱敏元数据。

## 4. 恢复与放弃

```bash
# 仅使用执行输出中返回的原 capture_id
python3 kemai_syncctl.py resume --capture-id CAPTURE_ID

# 源已完整落盘，只恢复数据中心提交，不发新源请求
python3 kemai_syncctl.py resume --capture-id CAPTURE_ID --max-source-operations 0

# 无法继续的尝试：保留业务行、回执和工件，下一次使用新 capture
python3 kemai_syncctl.py abandon --capture-id CAPTURE_ID
```

- 分页未完成：放弃当前尝试，下一轮新建 capture，不能拼接两次快照。
- 源已完整但网络/数据中心超时：先查批次回执和读回，再沿原 capture 恢复。
- 写入结果未知：禁止盲目重试；先核对同一 batch ID、摘要和业务行。
- 不删除 `.kemai-artifacts/`，不改 coverage/checkpoint 行来“修正”状态。

## 5. 历史证据承接

```bash
python3 kemai_syncctl.py history verify-legacy --stream retail-sales
python3 kemai_syncctl.py history adopt-legacy --stream retail-sales
```

`verify-legacy` 只读核验旧证据；`adopt-legacy` 会写自有数据中心的历史证据，但不会伪造“刚从源站同步”。未知历史基线不能触发全量重扫，也不能仅凭最大日期跳过中间缺口。

## 6. 暂停和拒绝范围

以下请求应直接阻断并保留证据：

- 修改、删除、审批、作废、导入或上传科迈数据。
- 对暂停流使用 `run`，或将列表覆盖描述成详情/支付完整。
- 未经放行向 `purchase-receive-history`、`retail-promote-sharing`、零售详情/支付发起采集。
- 使用裸 SQL、任意 `batchCreate`、旧 MySQL 写入器或并发 supervisor 绕过标准网关。
- 发现租户、门店、日期字段、查询筛选或数据集映射漂移。

## 7. 本地验证和发布前检查

```bash
python3 -m unittest discover -p 'test_kemai*.py'
node --test tests/pipeline/*.test.mjs
python3 scripts/build_kemai_gateway.py
python3 scripts/build_business_metric_gateway.py
python3 scripts/verify_business_metrics.py
```

前两项是离线回归；构建只更新本地生成物。真实源读取和数据中心写入必须单独记录范围、源观察时间、回读结果、重放结果和未覆盖项。

## 8. 停止条件与升级

出现限流、认证失效、筛选变化、分页中断、摘要冲突、窗口计数不一致、批次回执未知、锁冲突或标准 API 失败时：

1. 停止新增源请求和重试。
2. 保存命令输出、capture ID、错误类别和私有工件路径；不要保存密码、Token、签名 URL 或原始业务明细。
3. 只读确认 `status`、`gaps show` 和对应 capture manifest。
4. 由负责人决定重新计划的明确窗口；不回拨已完成检查点。

如果业务确需更正科迈数据，请由数据所有者本人在科迈官方界面操作；本项目不代为执行。
