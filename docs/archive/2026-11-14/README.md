# 归档说明（2026-11-14）

本目录保存 Fresh Market 在移交前整理出的阶段计划、日报、一次性验证快照和设计过程文件。

- 这些文件是历史证据，不是当前运行状态、当前覆盖率或当前放行清单。
- 当前入口以仓库根目录 [`README.md`](../../../README.md)、[`PROJECT-BRIEF.md`](../../../PROJECT-BRIEF.md) 和 `docs/HANDOFF.md`、`docs/ARCHITECTURE.md`、`docs/OPERATIONS.md` 为准。
- 运行前必须重新执行 `python3 kemai_syncctl.py status`、`gaps show` 和所需范围的 `history check --scope db`。
- `.local-audits/`、`.kemai-artifacts/` 和 `.runtime/` 中的私有证据不随本归档公开；文档只保留脱敏索引。

## 分类

| 目录 | 内容 | 使用方式 |
|---|---|---|
| `process/` | 开发计划、接续说明、日报、清理报告、一次性运行报告 | 需要追溯决策或历史上下文时查阅 |
| `evidence/` | 脱敏 JSON 验证和覆盖快照 | 复核历史验收结论；不能替代实时检查 |
| `design/` | 已实施语义层的设计稿和执行计划 | 了解指标口径变更来源；当前实现以代码和指标口径文档为准 |
