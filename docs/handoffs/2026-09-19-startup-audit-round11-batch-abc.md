# Round-11 启动逻辑审视 — A+B+C 全 7 项 (2026-09-19, ship 未提交)

> 接续 Round-10 (批 1/批 2)。本轮主会话自审 (qwen3.8-max subagent 配额 429,
> 2026-09-26 01:52 UTC 重置), 用户拍板 "A+B+C 全做"。
> TDD: `tests/test_startup_audit_round11.py` 先行 (19 tests), RED→GREEN。

## 台账 (7 项, 全部 ship)

### 批 A — installed 布局路径/打包

- **M1 — wheel 缺 seed 模板 YAML, installed 空库首启必挂**
  `pyproject.toml` force-include 只带了 console.html/static。installed 布局下
  `data/config.db` 不存在 → seed 读不到 6 个出厂 YAML → 空模板 →
  validate_config "No upstreams configured" 拒启; 预 seed 的库则因 bootstrap
  无 console 段而**静默关掉 console 鉴权**。
  修: force-include 补 `config/upstreams.yaml` + 5 个 `tools_*.yaml`
  (tdx_quant/mootdx2/omni/thsdk/ths_local)。**刻意不打** `config/config.yaml`
  (含真实 session_secret + PBKDF2 口令哈希, 0 个 `${VAR}` 占位) 与
  `config/proxy_settings.yaml` (本机代理设置, 缺失即默认 disabled)。
- **L2 — `tool_loader.load_all_tools` 第三候选锚 CWD**
  旧: `base_dir / f` → `base_dir/config/f` → `Path("config")/f` (锚 CWD)。
  从非 repo CWD 跑 yaml 模式/CLI 时可能误拾外来 `config/tools_*.yaml`
  (还会漏加载真正的生成工具)。删第三候选; 候选 1 已覆盖 config_path 同目录。
  `src/unihive/core/tool_loader.py`。
- **L4 — CLI `config lint/seed-db/upgrade-db` 的相对 --config 锚 CWD**
  与网关契约不一致。新增 `_resolve_config_path()` 走
  `resolve_project_path(raw, "config/upstreams.yaml")`, 三个子命令统一调用。
  `src/unihive/cli.py`。

### 批 B — 启动韧性

- **M2 — per-upstream 30s wait_for 超时后 UNKNOWN 孤儿永不自愈**
  mootdx2 `Quotes.factory` 重试预算可达 ~90s; 超时后 client 已登记但
  `start()` 被 cancel, 健康循环没起, 只能重启网关。同步阻塞在
  DaemonBlockingExecutor 的 worker 上**不可中断**, 不能简单等它。
  修法:
  - 两 client 各加 `_stopped` 生命周期位 + `async start_after_timeout()`
    (stopped 直接返回, 否则重入 start(); `_get_quotes` 锁+double-check
    保证重入廉价); `_start_health_loop()` 改统一入口 (stopped 守卫 + 判重,
    返回 task)。mootdx2 `src/unihive/api/mootdx2_client.py`,
    tdx `src/unihive/api/tdx_quant_client.py`。
  - tdx 新增 `threading.Lock` `_dll_lock` + `_dll_initialize`/`_dll_close`
    持锁包装: 僵尸 initialize 与 late recovery 重入、close 三方互斥,
    不并发打进同一 DLL。附带修掉旧 `_reconnect()` **直接在事件循环上**
    跑同步 `tq.initialize()` 的遗留冻结问题 (改 executor + 持锁)。
  - 网关侧 `GatewayServer._late_start_tasks: dict[str, set[Task]]` +
    `_spawn_late_start()` (仅挂有 `start_after_timeout` 的 client,
    task 名 `unihive-late-start-{name}`, done 自清) /
    `_late_start_once()` (CancelledError 重抛, 其余 warning) /
    `_cancel_late_starts(name=None)`。超时分支登记 client 后 spawn;
    `_detach_upstream_client_locked` (disable)、`stop()`、
    `_do_initialize` 失败清理三处先 cancel 再 stop。
    `src/unihive/gateway_server.py`。
- **L1 — mootdx2 stop() 与僵尸 factory 线程竞态泄漏 Quotes**
  超时后 worker 线程仍可能在 `_quotes_lock` 内慢赋值; 旧 stop() 无锁读
  `self._quotes` 得 None 就返回, 晚到赋值的 Quotes 永不 close (TDX 连接泄漏)。
  修: stop() 先置 `_stopped=True`, Quotes 摘除+关闭整体在
  `_run_blocking` 里**持 `_quotes_lock`** 完成 (`_detach_and_close_quotes`),
  排队等僵尸线程释放锁后关掉晚到实例。
  测试用真 client + 持锁 sleep 0.3s 后赋值 fake quotes 的僵尸线程验证。

### 批 C — 收尾

- **L3 — `main()` 对已知启动失败裸打 traceback**
  旧只映射 ValueError→2 / KeyboardInterrupt→0。新增
  `except (OSError, sqlite3.Error, RuntimeError)` →
  stderr 单行 `error: failed to start UniHive gateway: <Type>: <msg>` +
  exit 1 (端口/文件/磁盘、config.db 持续 locked — aiosqlite 透传同一
  sqlite3 异常族、运行态自检 RuntimeError)。ValueError→2 语义不变。
- **L5 — 删除网关级死代码 `_health_check_loop` / `_probe_upstreams_once`**
  生产自 2026-09-11 perf 专题后从不调度, 全仓仅测试 create_task。
  删两方法 + `DEFAULT_HEALTH_CHECK_TIMEOUT` 常量 +
  `health_check_timeout` 构造参数/`_health_check_timeout` 属性
  (全仓无任何调用方)。同步删测试:
  `tests/test_gateway_startup_fixes.py::TestHealthCheckTimeout`、
  `tests/test_startup_fixes.py::TestHealthLoopShutdown` 及 3 处
  shutdown-chain 测试里 vestigial 的 `fake_loop` 赋值。

## 测试变更

- 新增 `tests/test_startup_audit_round11.py` — 19 tests:
  - M1 pyproject 文本断言 (6 mapping 必在, config.yaml/proxy_settings 必不在)
  - L2 兄弟文件加载/外来 CWD 文件忽略 + call-form 源码断言
  - L4 tmp CWD 跑 `_cmd_config_lint` rc 0 + 源码含 resolve_project_path
  - M2 网关 late-start: 超时恢复+自清 / 无 hook client 不挂任务 /
    detach cancel+stop / cancel_all shutdown; mootdx2 健康循环守卫判重 +
    start_after_timeout stopped 短路; tdx `_dll_lock` 真线程串行化
    (max_active==1) + 健康循环守卫判重 + 生命周期位源码断言
  - L1 僵尸线程晚赋值 → stop 后 fake_quotes.closed 且 `_quotes is None`
  - L3 OSError/sqlite3.OperationalError/RuntimeError → exit 1 +
    stderr 含 "error:" 与消息; ValueError 仍 exit 2
  - L5 `inspect.getsource(gateway_server)` 不含三个死符号
- 改 `tests/test_startup_audit_round9_batch_c.py` H1-b: `__new__` 裸构造
  client 补 `client._dll_lock = threading.Lock()` (新协作者, 测试本体
  "initialize 必须离线程" 的断言不变)。

## 验证 (2026-09-19)

- backend pytest (排除 3 个既有失败文件): **1128 passed, 2 skipped**
  (基线 1111 +19 新 −2 删除 = 1128, 对账一致)
- web vitest: **324 passed** (18 files)
- web `npm run build`: ✓ (本轮无 web 源码改动, console.html/static 产物
  字节级不变, git 无 churn)
- 未碰用户 mootdx2 未提交 L1 cache/single-flight 区 (147–470 行
  `_daily_l1/_top_board_l1/_unusual_l1`); diff hunk 仅在 131/638/751/821/841

## 状态

- **全部落盘未提交**, 等用户指令; 未 push。
- 工作树相关改动: pyproject.toml / cli.py / tool_loader.py /
  gateway_server.py / mootdx2_client.py / tdx_quant_client.py +
  3 测试改 + 1 测试新增。
- 历史 handoff 文档 (2026-09-11-mcp-perf / 2026-09-14-venv-python313-fix)
  对 `_health_check_loop` 的描述属历史记录, 不改。
