# 采购详情批量读取：Kimi 接续说明

后续 Runner 规则见 [选路与接续说明](kemai-runner-routing-2026-11-09.md)。用户最新决定：采购订单/收货/退货列表固定走列表 API，商品行详情按需批量补录；不把历史明细报表 Excel 当成采购列表替代。

业务验收范围和 Excel 后续取舍见 [商品行交接与结论复核](kemai-lines-only-handoff-2026-11-09.md)：先保证商品行内容可用，商品名称等目前保存在源 payload 中，不能只凭行数宣称满足展示/分析需要。

本轮将商品行补采的源读取改为「一批单据一次 OpenCLI 调用」。采购订单、收货、退货共用该路径。默认每批最多 10 张，复用浏览器连接与页面准备；科迈侧仍逐单只读，数据中心仍由原有标准 API 写入及核验。没有发布 BFF、改 acceptance、启动采集或恢复 supervisor。零售详情/支付的暂停不受影响。

## 已完成及验证边界

- 新命令 `opencli kemai purchase-detail-batch --request-file <绝对路径> -f json`。
- `scripts/kemai_lines_only.py` 默认 `--source-mode batch`；现有 run/resume 命令可继续使用。
- 每个 CLI 批次最多 10 张；`--max-documents 25` 会拆为最多 10、10、5 张，并跳过可复用快照。
- 已安装到 `~/.opencli/clis/kemai/`，安装时同时取得共享 writer 锁和 source 锁，检查文件一致。本轮只运行版本/help/离线检查，不访问真实源或数据中心。
- 安装备份：`~/.opencli/kemai-source-gate/adapter-backups/adapter-backup-dwdwxtyb`。
- 真实源性能尚待下一批验证，不能把离线测试说成线上提速验收。

基线：上一批 7 单 102.17 秒，其中 source_read 68.32 秒。新代码在页面已就绪时，将 10 单的 CLI 启动从 10 次降为 1 次，浏览器 evaluate 从至少 40 次降为约 13 次（准备 3 次 + 详情 10 次）。科迈详情接口仍调用 10 次；首次导航、组件未就绪会增加准备开销。实际秒数以新 timing 为准。

## Kimi 下一步

1. 先确认已有写入批次结束。不要启动第二个 writer，也不要终止正在写入的进程来加载新代码。
2. 在项目目录运行本地检查：

   ```bash
   python3 scripts/kemai_opencli_runtime.py check
   opencli kemai purchase-detail-batch --help
   ```

3. 沿用当前实际未完成的 capture，首次新进程保持 10 单：

   ```bash
   python3 scripts/kemai_lines_only.py resume \
     --capture-id <当前实际capture> --max-documents 10 --retry-failed \
     --source-mode batch
   ```

   若当前日窗已经 committed，按现有补采顺序对下一日窗 plan/run，不能为了测速重采已完成日窗：

   ```bash
   python3 scripts/kemai_lines_only.py plan --stream purchase-orders --day YYYY-MM-DD
   python3 scripts/kemai_lines_only.py run --stream purchase-orders --day YYYY-MM-DD \
     --max-documents 10 --retry-failed --source-mode batch
   ```

4. 检查 `result.state`、`verified`、`remainder`，报告商品行新增/更新/不变行数和所有耗时。exit 0 不代表整个历史已经补完。
5. 首批真实样本满足身份、字段、业务键、行数核验后，再按原有顺序继续。不要为了测速增加源频率或启动并行 writer。

上面的 capture 和日期必须由当前状态决定；本文件刻意不固定过去会话的旧 ID。`plan` 只读数据中心，不产生源请求，也不是新增审批要求。

## 限速、冷却与恢复

每张单都独立取得共享 source lease。上限仍为 0.5 个逻辑源操作/秒；当前 gate 在前次完成后保留至少配置的间隔。页面准备在首张单 lease 内执行，之后复用页面，但每次详情调用仍在同一个 evaluate 内检查页面地址、组件和限流弹窗。导航附带请求不计为精确 HTTP 次数。

HTTP 429、HTTP 200 的限流业务响应、限流弹窗都会立即停止本批后续单据。共享 gate 记录 30 分钟冷却；批量命令在已有冷却期间快速返回，不占着 CLI 等待 30 分钟。冷却记录已经存在时 Python 不重复延长它。必须等冷却截止后再 resume，不能反复探测。

完整响应逐单写入 capture 私有目录，先 fsync 文件，再原子发布并 fsync 目录，权限为目录 700 / 文件 600。进程失败时：

- 前面已经落盘的响应保留，Python 在失败返回后仍恢复这些响应并保存标准快照，再将 cooling/错误交给原有 capture 恢复流程。
- 第二张限流，第一张已有响应时，恢复只请求尚缺的第二张及后续单据。
- CLI 超时、退出、丢失 stdout 均以已落盘响应为准，不依赖最后一份 stdout 才恢复数据。
- 不完整的批次先保存源证据，暂停数据中心写入；下一次恢复完成这批缺失响应后再逐单写库。
- 每张单仍走原有幂等预读、batchCreate/分组 update、整单读回核验和成功回执。部分 DB 写入不确定时，下一次从已保存快照重新比较，仅补不一致项。
- 版本已知且相同才跨运行复用响应；版本变化会生成新 key。版本未知不复用上次响应，无法由未知版本快照证明完整覆盖，保持原有保守口径。
- 本地文件损坏、身份不一致、容量/契约错误要先调查，不应自动反复重试。请求已返回但还没落盘时遭硬中断，可能需要重读那一张；不能承诺这种窗口绝不重读。

批次预算按单据预留，绝不把 10 单当作 1 个源操作。`source_operations` 在批次失败时是保守预留数，可能大于实际发起数；看 `source_count_basis`，不要当作精确 HTTP 请求计数。

## 分阶段计时与证据

入口汇总：`.kemai-artifacts/<capture>/timings/summary-*.json`。

新增 `source_batches`：每个 CLI 批次都有 `cli_calls`、`journal_hits`、`reserved_source_operations`、`cli_wall_seconds` 和 `batch_reports`。

| 字段 | 含义 |
|---|---|
| `cli_wall_seconds` | Python 启动 CLI 至进程返回的总耗时 |
| `cli_setup_and_teardown_seconds` | CLI 总耗时减去 handler 耗时的差值，包含加载、桥接初始化、输出和退出；不是单独测得的 Chrome 启动时间 |
| `batch_reports[].command_seconds` | 批次 handler 总耗时 |
| `records[].gate_wait_seconds` | 启动 gate 子进程及等待共享锁/限速的时间 |
| `records[].page_prepare_seconds` | 页面定位、必要导航及组件准备，仅首次出现 |
| `records[].browser_detail_seconds` | 一次详情 evaluate 往返的全部时间 |
| `records[].api_method_seconds` | 浏览器内 `loadEntityApi` 方法从调用到完成；包含页面封装/拦截器，不能直接叫纯 HTTP 网络耗时 |
| `records[].browser_bridge_and_checks_seconds` | evaluate 总耗时减接口方法耗时的差值，包含桥接、检查、序列化等 |
| `records[].artifact_write_seconds` | 完整响应的持久落盘耗时 |

接口方法耗时包含在 browser_detail 内；CLI handler 包含 gate 等子阶段，不能相加重复计算。批次 timing 中的 records 按请求顺序记录，不输出商品内容或凭据。批量预读耗时会记在触发该批次的第一张单上，**不要用该张单的 total_seconds 当作单张源 API 延迟**；比较批次 `source_batches` 和整体 summary。

私有恢复文件：

- `<capture>/source-batches/request-*.json`：源请求清单，1..10 个 `{id,key}`。
- `<capture>/source-batches/<key>.json`：kind、id、key、完整 `entity_json`、SHA-256、实际观察时间和阶段计时。
- `<capture>/source-batches/batch-timing-*.json`：包括失败批次的脱敏汇总。
- 原有 `detail-evidence/...` 快照与 `lines-verified.json` 回执继续保留。

key 由编排层按 kind、源 ID、版本生成；响应文件已有时 CLI 拒绝覆盖。编排层先验证并复用已取得的响应，仅将缺失项传给 CLI。不要直接重复执行旧 request 文件；恢复应使用上面的 resume 命令。

新鲜度、详情完整度、源日期覆盖的既有语义不变；商品行完整仍不等于分支/审批完整，不关闭日级 gap。

## 回退与验证

若真实批量命令存在兼容问题，先保留错误和源 artifacts。等当前进程退出，再沿用同一 capture：

```bash
python3 scripts/kemai_lines_only.py resume \
  --capture-id <当前实际capture> --max-documents 10 --retry-failed --source-mode single
```

这会恢复逐单 OpenCLI 读取，仍保留共享限速、冷却及已导入的标准快照。若进程被硬杀、原始 batch journal 尚未导入标准快照，single 回退可能重读该部分；正常 batch resume 可恢复 journal。无需 BFF 发布、清库、改检查点或回退分组更新代码。

离线验证命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_kemai_offline.py
node --test tests/pipeline/*.test.mjs
git diff --check
```

本轮覆盖：一次进程多单、一批一次准备、每单 lease、批次拆分、预算/重复 ID 拒绝、完整响应身份与摘要验证、HTTP 200 业务限流、HTTP 429、UI 限流、页面切换、冷却快速返回、超时后的部分恢复、未知/变化版本、失败写库后的零源重放和 single 回退。没有进行真实源请求或浏览器视觉验证。

验证结果：Python **413/413**、Node **47/47** 通过；`git diff --check`、新增 Python 文件 AST/空白检查通过；已安装命令 help 正常，runtime 文件与源码一致。完整测试输出保存在本机 `/tmp/kemai-batch-python-tests.log` 和 `/tmp/kemai-batch-node-tests.log`，临时日志不作为长期运行证据。

源码入口：`kemai_opencli/purchase-detail-batch.js`、`purchase-batch-reader.js`、`utils.js`、`source-gate.js`；Python 侧 `kemai_pipeline/batch_source.py`、`lines_only_workflow.py`、`source.py`、`source_gate.py` 和 `scripts/kemai_lines_only.py`。数据中心分组更新详见 [上一轮说明](kemai-grouped-update-2026-11-09.md)。
