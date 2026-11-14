# Fresh Market 项目简报

**更新时间**：2026-11-14
**用途**：移交前的当前摘要；历史快照已归档到 [`docs/archive/2026-11-14/`](docs/archive/2026-11-14/)。

## 定位

项目把科迈云帆的只读数据复制到 Lovrabet 自有数据中心 `app-21522951`，再提供同步状态、经营指标和只读页面。科迈是 source of truth，Lovrabet 是可重建副本。

## 当前状态

- `retail-sales`、`retail-orders`、`purchase-orders`、`purchase-receipts`、`purchase-returns` 已有标准接口和真实小窗口闭环证据，当前均为列表覆盖。
- `purchase-receive-history` 保持 no-go；`retail-promote-sharing`、零售订单详情/支付保持暂停。
- 商品详情、页签、库存/价格对象具备部分源适配器或本地数据，但不等同于全量验收。
- 经营指标 v1.0 已统一由只读 `businessMetricGateway` 提供，口径见 [`docs/kemai-global-business-metrics-v1.md`](docs/kemai-global-business-metrics-v1.md)。
- 常驻调度、多机租约和页面发布不属于默认启用能力；当前按一次性、有界操作交接。

运行态、覆盖率、缺口和最近成功时间都必须重新执行 `kemai_syncctl.py status`、`gaps show` 和明确范围的 `history check --scope db`，不能引用本摘要或归档数字当作实时值。

## 核心边界

1. 科迈侧只允许列表、详情、导出和统计读取；不得发送任何写请求或点击写入按钮。
2. 自有数据中心业务写入只走 Lovrabet 标准模型 API；SQL 仅用于结构迁移和只读计算。
3. 单机同一时刻只允许一个正式写入者；源请求共享上限 0.5 次/秒，限流后冷却 30 分钟。
4. 未完成分页、未知写入结果、摘要冲突、数据集未注册或范围漂移时停止并保留证据。
5. `partial`、列表成功、入库时间和展示最大日期都不能被表述为详情完整、源实时或连续覆盖。

## 移交顺序

1. 阅读 [`docs/HANDOFF.md`](docs/HANDOFF.md)。
2. 阅读 [`docs/PLAN.md`](docs/PLAN.md)。
3. 阅读 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。
4. 按 [`docs/OPERATIONS.md`](docs/OPERATIONS.md) 完成只读基线和离线测试。
5. 确认私有目录、数据集映射、运行时 OpenCLI 副本和权限；再决定是否执行明确的小窗口。
