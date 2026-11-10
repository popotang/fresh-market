# 科迈源请求协调器

## 作用和边界

`kemai_source_gate.py` 用 `fcntl.flock` 为本机所有参与的采集进程提供一把锁。每次逻辑源操作先读取项目限流状态和上次操作的起止时间，满足冷却且距离上次操作结束至少 2 秒后取得租约；租约覆盖整个源操作。失败请求同样占用时间槽。OpenCLI 适配器的 `source-gate.js` 通过子进程取得同一租约；如果适配器识别到业务限流，会在释放租约前写入原有的 1,800 秒冷却状态。

默认共享状态位于 `~/.opencli/kemai-source-gate/`，只有时间、操作标签、进程号和非敏感路径配置，不含凭据或源响应。原 `kemai_rate_limit_status.json` 继续供状态页和现有工具读取。`python3 kemai_source_gate.py configure` 生成连接两者的本机配置。项目内 `kemai_opencli/` 是适配器源码；运行时副本在 `~/.opencli/clis/kemai/`。

协调器覆盖适配器中需要访问科迈的站点命令，以及项目 Python 中的商品/主数据 browser eval、页面打开和零售详情 browser eval。静态 `capabilities` 不访问源站，不申请租约。导出命令在同一租约内完成任务创建和轮询，内部多次 API 调用之间另留至少 2 秒。商品详情和页签批处理仍按每条内部 API 请求至少 2 秒执行。

**范围限制**：这是对项目逻辑源操作的全局协调，不逐条统计页面导航加载的静态资源和自动请求。直接手工调用 `opencli browser ...` 不会自动经过适配器；手工源调用应使用下述包装命令。已启动且尚未加载新 Python 代码的 supervisor 也不会自动使用新 browser eval 租约；必须安全切换进程后才能宣称所有日常链路都受保护。

## 使用

```bash
# 配置一次；不写入账号或密码
python3 kemai_source_gate.py configure

# 需要源访问的科迈适配器命令自动获得租约，直接使用原命令
opencli kemai retail-sales --from 2026-11-06 --to 2026-11-06 --page 1 --rows 1000 -f json

# 手工 browser eval/open 必须通过包装命令；不要包裹 opencli kemai，防止嵌套锁
python3 kemai_source_gate.py run -- opencli browser kemai_probe eval '/* expression */'

# 离线验证，不访问科迈或数据库
python3 -m unittest -v tests.python.test_kemai_source_gate tests.python.test_kemai_retail_detail_backfill
```

项目内适配器改动通过 `python3 scripts/kemai_opencli_runtime.py check` 比较项目与运行时 `.js/.json` 的 SHA-256，并报告缺失、额外和漂移文件。完整离线测试通过后，才显式执行 `python3 scripts/kemai_opencli_runtime.py install`；安装只同步项目声明的源码，保留 gate 配置，先备份运行时副本，再用 `opencli kemai --help` 和 `opencli kemai capabilities -f json` 做零源加载检查。`capabilities` 不打开浏览器、不申请源租约，也不代表已登录或源站已验收。未知额外文件、同路径或摘要不一致时停止安装，不自动清理运行时文件。

## 日常进程切换

切换前应确认 `kemai_daily_status.json` 为 `watching`、全部日任务无 `running`，并核对断点。停止旧 supervisor 后确认进程确实退出，再按既有安全方式启动一个新 supervisor。新进程加载商品 browser eval 的租约及销售流水映射步骤；核对下一完整源日的 `retail-order-lines` 事件和逐日行数。数据库密码继续由交互输入或匿名文件描述符传递，不放进命令行、日志或配置。零售详情补采保持暂停。

2026-11-07 15:08 CST：旧 PID 97002 在无在途子任务时退出，确认不再存在；新 PID 40069 启动并进入 `watching`，`last_complete_day` 仍为 2026-11-06，限流状态为 `ready`，零售详情仍为 `paused_by_user`。新进程启动时间晚于协调器及日常调度代码修改，已具备下一轮使用新门面的条件。**尚未进行真实科迈请求验证**；下一完整源日完成后，按上文核对商品行映射事件和源日期窗口。手工裸调 `opencli browser` 仍可绕开门面，须使用包装命令。
