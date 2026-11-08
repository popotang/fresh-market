# Legacy 脚本（默认禁用）

这里保留旧 MySQL 写入器、supervisor、补采与只读审计脚本，用于迁移核对和回退参考；**不是当前推荐入口，不要启动旧 supervisor 或全量/增量采集**。正式操作使用根目录 `kemai_syncctl.py`，标准写入只走 Lovrabet CLI / BFF / 标准模型 API。

历史脚本内部仍使用旧的绝对模块名。需要离线检查或受控维护时，从项目根目录以模块方式调用，例如：

```bash
python3 -m legacy.kemai_commerce_sync --help
python3 -m legacy.kemai_retail_daily_audit --help
```

运行状态、日志、锁和 PID 统一放在 `.runtime/`；历史审计证据放在 `.local-audits/`。旧文档中的根目录命令是当时快照，不代表当前推荐路径。零售原详情/支付继续暂停。
