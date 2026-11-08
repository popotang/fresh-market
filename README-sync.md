# 科迈云帆商品首轮全量同步

## 已建表

`schema_v2.sql` 建立 17 张表：商品主表、主档 JSON、机构和商品字典、商品机构关联与子记录，以及原始响应、运行记录、检查点和逐对象状态。旧的 `product_items` 样本表未删除。

源系统商品 ID 用字符串保存；价格原始整数除以 10000 后写入 `DECIMAL`，原始响应仍完整保存在 `source_capture`。`products` 通过 `tenant_id + source_item_id` 幂等更新。

## 当前任务

后台进程 PID 存在 `kemai_full_run.pid`，输出在 `kemai_full_run.log`。进程只用 OpenCLI 的已登录浏览器会话读取接口；数据库密码由启动器经匿名管道传入，不写入脚本、参数或日志。

执行顺序：

1. 184 页商品列表，源端首轮总数 18,384；逐页提交并保存检查点。
2. 逐商品主档，存 `products`、`product_profile` 和原始响应。
3. 14 类商品详情页签，存原始响应和 `product_child_records`；供货资格、门店价格、库存、经营范围同时写入专用子表。组合和混售仅对页面实际支持的商品类型请求。
4. 对账全部待办完成后，才把 `sync_runs` 标为 `success`。

单路请求，主档与页签按当前配置平均最多 0.5 次/秒；出现 HTTP 429 或接口返回“操作太频繁”时全局冷却 30 分钟。401/403、数据不符或重试耗尽会停下并保留已提交检查点。浏览器登录态失效时，重新登录后可续跑。

## 查看与续跑

```bash
ps -p "$(cat kemai_full_run.pid)" -o pid,etime,stat,command
tail -n 30 kemai_full_run.log
python3 kemai_sync.py status
```

`status` 会提示输入数据库密码。后台进程退出后，重新登录云帆并运行 `python3 kemai_launch.py`；它不会重做已经标记完成的商品或页签。`kemai_masters.py` 已同步机构 2、部门 6、类别 801、品牌 1,816、单位 116；`kemai_derive_suppliers.py` 从商品列表中提取了 213 个被引用的供应商。

商品详情页签所用接口是页面当前使用的内部接口。接口或会话变化会使任务停在检查点；运行状态必须以数据库 `sync_runs` 和 `sync_objects` 为准，不能只看进程是否存在。
