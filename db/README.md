# 数据库定义与迁移

- 本目录根部的 `schema*.sql` 仍被旧迁移命令读取，是当前代码引用的 DDL 真源。
- `migrations/` 保存一次性、已执行或隔离验收用 DDL；执行前先核对生产迁移记录，不得因文件存在就重复执行。
- `schema_purchase_list_hash.sql` 对应采购列表摘要列迁移；采购正式网关尚未因此自动放行。
- 业务行写入不使用这些 SQL 文件，统一走 Lovrabet 标准模型 API。
