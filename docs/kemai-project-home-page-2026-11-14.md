# 项目首页（经营数据系统说明）

- 页面名称：项目首页
- pageId：`1571`
- 菜单：根级「项目首页」，menuId `79861`，sort `0`
- 状态：`FORMAL`，已发布；正式版本 `10`
- pageCode：`a6d0ec07e08e484aa3440f2b408ec049`

地址：

- 最新保存预览：`https://app.lovrabet.com/app/app-21522951/pages/a6d0ec07e08e484aa3440f2b408ec049`
- 页面编辑器：`https://app.lovrabet.com/app/app-21522951/ide/1571`
- 发布后运行态：`https://app-21522951.app.lovrabet.com/a6d0ec07e08e484aa3440f2b408ec049`

## 页面要解决的问题

让第一次进入项目的人在一屏内理解：

1. 数据从哪里来，经过什么链路，最后服务谁；
2. 现有系统不是概念图，哪些能力已经可点击使用；
3. 为什么有些数字是估算、为什么库存不能当实时全量；
4. 从哪里开始看经营数据。

## 页面结构

1. **首屏**
   - 标题：一张小票，如何变成明天的经营动作
   - 数据底座实时状态：最新营业日、统一指标数、成本覆盖、库存快照覆盖
   - 主入口：全局经营指标；次入口：老板驾驶舱、店长作战台

2. **业务数据流转与人员使用**
   - 直接展示「改造后业务数据流转与人员使用」静态图。
   - 图片包含超市收银、科迈云帆 SaaS、私有化数据中心、云兔 AI 原生系统底座及四类使用者。
   - 图片为页面内自包含 SVG 资源，不依赖外部图片 URL；窄屏可横向查看。

3. **项目里已经有什么**
   - 业务数据集：47
   - 数据集关系：59
   - 统一指标：由 `businessMetricGateway` catalog 实时返回
   - 核心应用入口：老板驾驶舱、店长作战台、全局经营指标

4. **系统边界**
   - 不回写科迈
   - 估算毛利不是财务结算
   - 库存不是实时全量
   - 覆盖不足直接标注

5. **行动入口**
   - 先看全局指标
   - 进入店长作战台
   - 进入老板驾驶舱

## 数据与实现

- 只调用 `businessMetricGateway` 的 `catalog` 与 `health`，不直接查表。
- 任一状态服务失败时显示“数据状态待确认”，不阻断页面阅读。
- 页面跳转使用 `navigate("/<pageCode>", { onePage: true })`。
- 界面静态文案与图片替代文本走词包 `src/locales/index.js`；图内文字属于静态图内容。
- 颜色只使用 Ant Design CSS 变量，适配亮/暗主题。
- 无外部图片、无未登记依赖；图标来自 `@ant-design/icons@5`。
- 响应式：1100px 以下两列，720px 以下单列；流程连接线和四类使用者在移动端纵向排列。
- `prefers-reduced-motion` 下关闭状态点呼吸动画。

## 本地源码

`.rabetbase/custom-pages/project-home/`

- `src/app/index.jsx`
- `src/app/index.css`
- `src/assets/business-flow.js`
- `src/lib/format.js`
- `src/locales/index.js`

## 验证

- `page create --dry-run` 与本次 `page custom-update --dry-run` 均通过。
- 本次修改 `src/app/index.jsx`、`src/app/index.css`、`src/locales/index.js`，新增 `src/assets/business-flow.js`；数据调用与 `src/lib/format.js` 保持不变。
- 平台 `codeContent` 与本地 5 个文件逐一比对一致。
- 原「业务数据怎么流动」和「同一套底座，服务四类人」两个 Card 区块已删除，替换为一张自包含静态图；其他区块保持白底素色样式。
- 图片加载、旧区块移除及无横向溢出验收通过；图片尺寸为 2000 × 965。
- 已检查桌面与 390px 手机离线截图；手机端图片可横向查看，不撑破页面。
- 已发布为 `FORMAL`，正式版本 `10`；发布后 5 个文件再次与本地逐一比对一致。
- 浏览器工具无 Lovrabet 登录态，未做运行态自动截图；需在已登录浏览器做最终视觉验收。
- 未对科迈执行任何写操作。
