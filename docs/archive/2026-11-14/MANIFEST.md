# 归档清单

**整理日期**：2026-11-14  
**原则**：只移动和重分类，不删除历史内容；当前入口改由 `docs/HANDOFF.md`、`PLAN.md`、`ARCHITECTURE.md`、`OPERATIONS.md` 统一承接。

## 数量

- `process/`：32 份过程、计划、日报、接续和一次性报告（含 2 份草稿 XML/JSON）。
- `evidence/`：6 份脱敏验证/覆盖/关系证据。
- `design/`：2 份已实施指标语义层设计过程稿。
- 合计：40 个归档文件（不含本目录 README 和清单）。
- `reports/archive/2026-11-14/`：4 份 HTML 展示报告快照。

## 分类口径

| 类型 | 处理 | 原因 |
|---|---|---|
| 阶段计划、日报、Agent 接续、开发报告 | 移入 `process/` | 有追溯价值，但不应被当作当前执行入口 |
| 标准接口、Excel、代码基线、覆盖/关系快照 | 移入 `evidence/` | 保留历史验收证据；当前状态仍需重新核查 |
| 已实施指标 spec/plan | 移入 `design/` | 实现已落地，当前口径以代码、SQL、BFF、测试和指标文档为准 |
| 业务 HTML 报告 | 移入 `reports/archive/` | 展示稿/快照与技术运行手册分离 |
| `docs/legacy/` | 原位置保留 | 已明确标记为旧实现参考，避免与当前架构混用 |

## 当前保留在 `docs/` 根目录的资料

- `HANDOFF.md`、`PLAN.md`、`ARCHITECTURE.md`、`OPERATIONS.md`、`DOCUMENTATION-INDEX.md`
- 标准操作详细参考、源请求协调器说明、能力清单
- BI 页面、经营指标 v1.0、数据集工作台参考

所有移动均保留原文件内容；文件内部引用如属于历史快照，按归档时点理解。
