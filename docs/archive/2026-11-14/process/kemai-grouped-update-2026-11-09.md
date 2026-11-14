# 商品行分组更新与批量核验（2026-11-09）

后续源读取优化已完成，Kimi 接续请同时阅读 [采购详情批量读取说明](kemai-opencli-batch-details-2026-11-09.md)：默认每 10 单一次 OpenCLI 调用，保留原有分组写入与核验。

本轮是在 [商品行恢复与计时方案](kemai-lines-only-recovery-and-performance-2026-11-09.md) 上继续减少数据中心调用。源读取、限速、快照和 capture 恢复方式不变。本轮没有启动采集、发布 BFF 或写入生产数据；开发开始时已有的小批次使用其启动时加载的旧代码，后续新进程才加载本轮实现。

## 本轮改动

`kemai_pipeline/child_batch.py` 实现单张单据子表的标准 API 批处理，`LovrabetAdapter.reconcile_children()` 提供适配器入口，`DetailWorkflow._write_children()` 接入。适用于商品行、分支和审批三张子表；LinesOnlyWorkflow 仍只选择商品行。

1. 沿用整单的完整预读，通过业务唯一键找到数据库 ID，不逐行再查。
2. 本地比较字段语义，忽略完全未变化的行。
3. 新增行用 `data batchCreate`；变化行以“变化字段集合 + 变化后的值”精确分组，调用 `data update {id:[...], ...patch}`。
4. 所有新增/更新结束后，用 `filter(document_id=...)` 完整分页读回一次，核对所有预期行、稳定 ID、字段内容以及多余旧行。跨多单的 IN 查询本轮未实现；单据内整批读取已经消除了逐行查询，且保留较小恢复范围。
5. 商品行回执只在核验和状态写入成功后生成。多余旧子行保留并报告 stale，整体详情仍 partial，日级 gap 不关闭。

单批最多 200 行/ID、编码后 48,000 字节；所有分组和容量检查在首个写入之前完成。没有新引入 Custom SQL、删后重建或 batchCreate 覆盖更新。

## 不确定写入与恢复

若任一写调用报错，立即停止后续组，不盲目重发。读回整单：全部预期字段已正确则接受已落库结果；否则抛 `UPSERT_OUTCOME_UNKNOWN`，保留源快照与原 capture。恢复后重新预读、只重做仍不一致的行。批处理不是跨多个请求的数据库事务，故部分落库是允许且显式可恢复的中间状态；不能据请求成功数声明完整。

这里继续依赖既有“同一时刻一个正式写入者”约束，不提供跨主机任意写入者之间的 CAS。不能并行启动另一个 runner。

## 性能统计

单据 timing 的 `child_changes.<table>` 新增：

- `desired_rows/existing_rows/created_rows/updated_rows/unchanged_rows`
- `update_groups`：不同 patch 的数量；`update_requests`：按大小拆分后的实际更新请求数
- `create_requests`：新增批次数
- `changed_fields`：每个变化字段涉及多少行，仅记录字段名和计数，不记录业务值

计时阶段变为 `children_grouped_update`、`children_batch_create`、`children_final_read`，替代原来的逐行 `children_update_verify` 与新增阶段内嵌核验。原始源读取和状态写入计时保持不变。运行 summary 中的 `child_changes` 汇集各单统计。

注意：若每行 `payload` 都有不同变化，即使 quantity 相同也不会合并为一个更新组，因为不同 payload 不能用共同 patch 覆盖。此时仍有逐行 update，但已去掉每行的前查和后查。不要承诺 103 行更新必然合成一次请求。

首轮旧数据：10 单、103 条变化商品行、359 次数据中心调用。按同一负载、不额外分页或重试估算：

- 103 个更新组仍各不同：约 10 次预读 + 103 次 update + 10 次整单核验 + 30 次状态处理 = 153 次；
- 有相同 patch 可合并时，update 次数进一步减少；
- 新增和变化混合时也共享最终子表核验，不额外调用旧 batch_create 的内嵌回读。

这只是调用结构估算，真实秒数由下一轮 timing 证明。源读取 9–10 秒的组成还没有进一步优化。

## Kimi 接续

继续使用 `scripts/kemai_lines_only.py resume`，沿用当前实际活动 capture，不能照抄早先文档的旧 capture ID。先让已经启动的批次完成或按已有规则安全停下，再启动新进程。首个新批次保持 10 单。

比较每单、每批：总秒数、data_center_requests、updated_rows、update_groups、update_requests、changed_fields，以及 source_read 和 children_grouped_update 耗时。区分缓存命中和首次源读取。若变化主要落在 payload 或固定字段格式上，先给出字段计数与脱敏原因分析，不能据此自行忽略这些字段。

本轮不需要 BFF 发布或修改 acceptance。完整回归：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_kemai_offline.py
git diff --check
```

验证结果：Python **398/398 通过**，含 12 个新增批处理用例；覆盖相同值分组、不同值不合并、不同 payload、混合新增/更新、200 ID 拆分、超大参数写前拒绝、超时已落库、部分写入停止、错误读回、作用域/重复键/数据库 ID 校验、多余行和空单据。JS 未改，本轮没有重复跑上一轮已通过的 Node 41 项。
