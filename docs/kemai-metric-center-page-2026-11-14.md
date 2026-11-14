# 全局经营指标页面（2026-11-14）

## 页面

- 页面名称：全局经营指标
- pageId：`1570`
- 状态：`DRAFT`，尚未发布
- 菜单：`经营报告 / 全局经营指标`（menuId `79860`）
- 页面尺寸：桌面优先，兼容 760px 以下单列布局

地址：

- 最新保存预览：`https://app.lovrabet.com/app/app-21522951/pages/e4333ec2deaa47dba436c87bd08ff2f9`
- 页面编辑器：`https://app.lovrabet.com/app/app-21522951/ide/1570`
- 发布后运行态：`https://app-21522951.app.lovrabet.com/e4333ec2deaa47dba436c87bd08ff2f9`（当前未发布）

## 本地源码

`.rabetbase/custom-pages/metric-center/`

| 文件 | 作用 |
|---|---|
| `src/app/index.jsx` | 页面状态、筛选、域切换、BFF 查询与布局 |
| `src/app/index.css` | 页面视觉与响应式样式，只使用 Ant Design CSS 变量 |
| `src/components/Chart.jsx` | ECharts 生命周期、ResizeObserver、减少动效适配 |
| `src/components/CoverageStrip.jsx` | 成本、库存、促销、支付可信度横条 |
| `src/components/MetricDrawer.jsx` | 指标定义、公式、来源和限制抽屉 |
| `src/components/MetricTable.jsx` | 全量指标字典表格 |
| `src/lib/format.js` | 金额、比率、数量、单位格式化 |
| `src/locales/index.js` | 中文词包 |

## 数据契约

统一调用 Backend Function：

```js
client.bff.execute({
  scriptName: "businessMetricGateway",
  params: {
    action: "query",
    groupCode,
    startDate,
    endDate,
    dimension,
    branchId,
  },
});
```

- 初始化：并行调用 `catalog` 与 `health`，以 `latest_biz_date` 作为默认结束日。
- 总览：销售 branch、库存 branch、采购 day、营销 promotion；销售趋势和品类在后台补齐。
- 销售：day + category。
- 库存：branch。
- 采购：day + supplier。
- 营销：promotion。
- 指标字典：直接使用 `catalog.metrics`，当前值取各域默认维度缓存。
- 所有请求只读；未加载成功的域按区域降级，不阻断其他区域。

## 体验

- 顶部吸顶控制台：时间范围、自定义日期、门店、刷新、口径入口。
- 数据可信度横条：成本覆盖、库存快照、促销关联、支付事实；点击可打开指标定义。
- 非对称总览：净销售额主指标 + 趋势 + 客单价/订单/会员/毛利指标带。
- 跨域快照：库存风险、采购净收货、营销优惠，可点击进入对应域。
- 域内图表：销售趋势与品类、库存风险与可售天数、采购趋势与供应商、活动让利。
- 指标字典：46 个指标，支持搜索、按域筛选、点击查看公式与限制。
- 加载骨架、空态、错误重试、部分失败提示。
- 数字使用等宽数字；状态色使用 Ant Design 语义 token。
- `prefers-reduced-motion` 下关闭 ECharts 动画并移除交互过渡。
- 所有静态文案走词包，不硬编码中文。

## 验证

- `rabetbase page create --page-dir ... --dry-run` 通过。
- 页面创建成功，平台 `codeContent` 与本地 8 个文件逐一比对一致。
- 页面状态为 DRAFT，`formalVersion=0`，未发布。
- 浏览器工具当前无 Lovrabet 登录态，无法自动截图；需要在已登录浏览器中打开预览链接做最终视觉验收。
- 数据查询依赖已发布 `businessMetricGateway`，页面不自行计算公式。
- 未对科迈执行任何写操作。

## 后续

1. 在已登录浏览器预览桌面与移动布局。
2. 根据实际观感调整密度、图表和词条。
3. 确认后执行 `page custom-publish` 发布为 `FORMAL`。
