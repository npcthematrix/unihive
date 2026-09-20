# Round-12 启动逻辑审视 — 2 HIGH + 12 MED + 18 LOW 全量, 已落盘未提交

日期: 2026-09-19
专题: "请审视 UNIHIVE GATEWAY 的启动逻辑" (3 个并行审视 agent → 去重 32 项;
用户批准 **"全部 (含 18 LOW)"**, 严格 TDD: 先 RED 再 GREEN, **不 commit**)
状态: **全部 GREEN, 工作区未提交**。backend **1353 passed / 3 skipped**
(Round-12 净增 31 个落库计数 + 既有文件增补; 8 个新测试文件共 121 用例,
其中簇 H/I 31 个, 簇 A–G 90 个 + 对 5 个既有测试文件的增补)。

> 唯一明确不做项: 1 个 POSIX-only 的 SIGTERM 关停窗口加固
> (本机 Windows 单机部署, SIGTERM 语义不适用; 与 Round-8 declined 项同类)。

## 簇划分 (A–J)

| 簇 | 主题 | 测试文件 | 用例 |
|---|---|---|---|
| A | ps1 启动壳: stdio stdout 纯净 / 退出码 / CWD 锚定 / 编码解析 | test_startup_scripts_round12.py | 9 |
| B | healthcheck.py 重写为真实 /health 探活 | test_healthcheck_round12.py | 10 |
| C | H2: TTL=0 是「不缓存」不是「永久缓存」+ TTL 数值校验 | test_ttl_zero_round12.py | 20 |
| D | 运行时路径锚定项目根 + 日志目录不可写降级 | test_path_anchoring_round12.py | 6 |
| E | main 异常映射 + 数值配置校验 + 未知 upstream type fail-fast | test_startup_config_errors_round12.py | 22 |
| F | /login error 参数 HTML 转义 (反射 XSS) | (test_console_auth.py 增补) | — |
| G | 注册隔离 / 健康聚合 / 启动悬空引用 / 死代码 / 保留名 | test_startup_cluster_g_round12.py | 23 |
| H | 生命周期/并发: _stopped 守卫 + tqcenter import 入池 + stop 复位 | test_startup_cluster_h_round12.py | 18 |
| I | 入口杂项: PYTHONIOENCODING / version / stdio host-port 警告 / 超时状态映射 | test_startup_cluster_i_round12.py | 13 |
| J | 全量验证 + handoff + memory (本文) | — | — |

## 各簇要点

### 簇A — scripts/start_gateway.ps1 + _load_env.ps1

- **H1 stdio stdout 绝对干净**: banner/端口探测只在 http 模式执行; stdio 下
  stdout 是 MCP JSON-RPC 帧通道, 宿主侧任何杂写都会让握手失败。行为测试用
  真实 powershell.exe + 临时 PATH 上的 fake python.cmd 验证。
- **MED 退出码透传**: `exit $LASTEXITCODE` (supervisor/MCP 宿主靠退出码判
  重启); 宿主请用 `powershell -File` 而非 `-Command` (后者在
  $ErrorActionPreference=Stop 下会把子进程非零退出改写成 1)。
- **MED CWD 锚定**: `Set-Location (Resolve-Path "$PSScriptRoot/..")`,
  `python -m src.unihive...` 不再依赖宿主的工作目录。
- **LOW PYTHONIOENCODING**: python 启动**前** `$env:PYTHONIOENCODING="utf-8"`
  (解释器只在启动瞬间读它, 进程内设是假动作 — 见簇I)。
- **LOW _load_env.ps1**: `Get-Content -Encoding UTF8` (PS 5.1 对无 BOM 文件
  默认 GBK, .env 非 ASCII 值会被静默解错); 缺文件警告改走 stderr;
  无引号值剥离空白后行内注释 (`val # 备注` → `val`, `a#b` 保留);
  只剥成对的外层引号; 支持 `export ` 前缀; 文件保存为 UTF-8 BOM。

### 簇B — scripts/healthcheck.py

旧版 import 即崩 (`from upstream_client import ...` 相对导入错误), 且对
enabled 上游 pass 恒返 healthy。新版真实 `GET http://127.0.0.1:<port>/health`,
按 HTTP 状态码 + JSON 健康字段判退出码 (0/1), 端口可配, 超时不挂死。

### 簇C — TTL=0 永久缓存缺陷 (H2)

旧链路: `_execute_cached` 只判 `ttl is not None` → 0 过关; `Cache._set_impl`
用 `int(now + ttl) if ttl else None` → 0 落成 `expires_at=NULL`, 读侧
`if expires_at and ...` 永不判过期 = **永久缓存** (配置写 0 本意是不缓存)。
三层修复:
1. `cache_strategy.normalize_ttl` 纯函数: ≤0 / 非法类型 / bool → None;
2. `_execute_cached` 解析 TTL 后归一化;
3. `Cache.set` 防御: ttl ≤ 0 / 非数值 / bool 一律不写入。
YAML 模板可能绕过控制台 PUT 侧的 pydantic 门, 校验在系统边界都做了。

### 簇D — 路径锚定 + 日志降级

- `configure_logging` 的 `logs/gateway.log` 相对路径以前按 CWD 解析 →
  从别的目录启动会另建 logs/, 与已锚定项目根的 cache.db/config.db 裂脑;
  现在统一走 `core.config_db.project_root()`。
- 日志目录创建/打开失败以前 OSError 拒启 — 日志是可观测设施, 不该阻塞
  进程启动: 降级 stderr-only (stdio 应急也只挂 stderr, stdout 仍归 JSON-RPC),
  并 root.error 明说降级原因。
- `cache_stats.json` 同样锚定项目根。

### 簇E — 启动期 fail-fast 带字段名

- 未知 `upstream.type` 以前静默退化成空 command 的 stdio 客户端, 延迟到调用时
  才以费解错误爆炸 → 构造期 ValueError + 合法类型表
  (http_jsonrpc/python/npx 已下线; stdio 必须给 package/command)。
- `cache.max_entries` / `operation_timeout` / `gateway.rate_limit` 数值字段:
  裸 `int()/float()` 改 `coerce_int_field/coerce_float_field` (gateway_server/_helpers.py),
  报错带点路径字段名 + 实际值/类型 + 边界; bool 当 1 的坑也挡掉。
- `fuyao_cache.tools` 非数值 → `fuyao_ttl_for` 带字段名 ValueError,
  `_execute_cached` 捕获后降级为不缓存 (不拖垮调用)。
- `main()` 捕 `yaml.YAMLError` / `KeyError` → 干净消息 + **退出码 2**
  (配置错), 不再裸奔 traceback。

### 簇F — /login 反射 XSS

`login_get` 把 query 参数 `error` 直接插进 HTML。改 `html.escape(error, quote=True)`
(quote=True 同时挡属性注入向量)。

### 簇G — 注册隔离 / 健康诚实 / 悬空引用

- **单 spec 注册失败不连坐**: register_tools_from_config 对单个 spec  try/except,
  失败 log + skip (如撞保留名被 FastMCP 拒, 不再拖垮其余 288 个工具)。
- `RESERVED_TOOL_NAMES` 下沉 `core.registry`; `validate_specs` 启动门拦截保留名,
  config lint 经 T001 自动覆盖; `search_stock` 从保留名移除 (旧手写工具已删,
  现存的是正规注册的上游工具)。
- 启动门 `validate_specs(known_upstreams=...)`: routing mapping/chain 引用未配置
  上游 = 配置错误, 不再等运行时才报 Unknown upstream。
- `aggregate_health(expected=...)`: 配置启用但未登记 (构造/启动失败) 的上游报
  **"unregistered"**; 启用集合非空而登记为空 → unhealthy, 不再谎称 healthy;
  `get_server_status.gateway` 复用同一聚合结果。
- Router 对悬空上游的 hop 消息说明 "not running (disabled/failed to start)"。
- `validate_config` 上游类型表与 `_construct_upstream_client` 构造器对齐。

### 簇H — 生命周期/竞态

- **mootdx2 `_stopped` 双检**: `_get_reader`/`_get_quotes` 在锁外检查后、拿到
  `_quotes_lock` 后再检一次 — 旧窗口里 caller 通过锁外检查后阻塞在锁上,
  stop() 正好 detach/close 了 Quotes, 醒来会重新 factory 出一个不关的 TDX 连接。
  `_probe_with_existing_quotes` 起手 `if self._stopped: return False`;
  start() 复位 `_stopped=False`。
- **tqcenter import 整体入阻塞池** (tdx_quant): 旧实现只把 `tq.initialize`
  放进 executor, `sys.path.insert` + `import tqcenter` 仍在事件循环线程
  (import 期间持 import 锁, 可阻塞 loop 数百 ms)。新 `_dll_import_and_initialize`
  在 worker 线程、`_dll_lock` 内完成 path+import+initialize 全序列, 与
  `_dll_close`/重连串行; FileNotFoundError/ModuleNotFoundError/ErrorId='12'/
  同名策略语义不变。测试用真实 tmp tqcenter.py 记录**模块体执行线程**,
  断言 import 本身不在主线程 (先 RED 后 GREEN)。
- **阻塞池关停不丢排队任务**: `shutdown_blocking_executor` 改
  `shutdown(wait=False, cancel_futures=False)` — 排在僵尸 factory 后面的
  stop detach/close ("最终必执行" 契约) 必须能 drain; daemon worker +
  不注册 atexit join 保证不拖进程退出。
- **init 失败清理抗取消**: `_do_initialize` 外层改捕
  `(Exception, asyncio.CancelledError)`, 每步清理经新助手
  `_await_cleanup(asyncio.shield(aw), timeout=2.0)` — cancel 清理过程不再跳过
  后续步骤 (late-start 取消 / per-client stop / 清理任务 / cache.close /
  config_store.close), 清完仍 re-raise。
- **stop() 加固**: config_store.close() 异常只 warning 不炸 stop,
  `self.config_store=None` 无条件执行; reset 尾防御性 `getattr` 清空
  `_in_flight_requests` single-flight 表。
- yaml 逃生模式的 `_load_db_config` 早返回路径补 `_apply_gateway_tuning()`
  (db 路径本来就有, yaml 路径漏了 → per_upstream_timeout/rate_limit 不生效)。
- **omni.run_sync 的 `_ensure_db` 入 to_thread**: mkdir + sqlite connect/DDL
  不再占事件循环 (缺库时 __init__ 不物化, run_sync 写前自建)。

### 簇I — 入口杂项

- **删除假的运行时 `os.environ["PYTHONIOENCODING"]`**: 该变量解释器只在启动
  瞬间读, 进程内设对自身无效。生效两道: 簇A 的 ps1 启动前 env + 新
  `_configure_stdio_utf8()` (facade re-export, 实现在 gateway_server/_helpers.py):
  对 stdout/stdin `reconfigure(encoding="utf-8")`, 无该方法/ValueError/OSError
  跳过 — 覆盖 console script `unihive` 与 MCP 宿主直接 `python -m` 路径。
- **stdio 显式传 host/port 告 warning**: 以前静默忽略
  ("transport=stdio ignores host/port (got host=%r, port=%r)")。
- **版本号读取**: `_read_gateway_version` 改为 `importlib.metadata.version`
  优先 (wheel 安装的规范来源), PackageNotFoundError 才退回解析 pyproject;
  pyproject 解析用 `_PROJECT_TABLE_RE` 锚定 `[project]` 表切片 (旧实现在全文
  匹配首个 `version=`, 未来 `[tool.*]` 表带 version 且排在前面会读错版本),
  再读不到 "unknown"。
- **启动超时状态映射**: wait_for 超时时, **无** `start_after_timeout` 钩子的
  client (thsdk/fuyao/omni/stdio) — 其内部 `except Exception` 接不住
  BaseException 系的 CancelledError, 状态永远停在初始 UNKNOWN —— 现在诚实落
  **UNAVAILABLE**; 有钩子的 mootdx2/tdx_quant 保留 UNKNOWN, 等阻塞调用返回后
  后台自愈自行定状态, 不提前判死。
- 结构约束: facade __init__.py 有 450 行软预算 (test_gateway_package_layout),
  `_configure_stdio_utf8` 放 _helpers.py 并 re-export, facade 收至 450。

## 改动文件

生产 (20):
`scripts/{start_gateway.ps1,_load_env.ps1,healthcheck.py}`,
`src/unihive/api/{mootdx2_client,tdx_quant_client,omni_client}.py`,
`src/unihive/console_auth.py`,
`src/unihive/core/{blocking_pool,cache_strategy,registry,router}.py`,
`src/unihive/gateway_server/{__init__,_helpers,lifecycle,upstreams,mcp_tools,cached_calls}.py`,
`src/unihive/storage/cache.py`,
`src/unihive/utils/{config_loader,log_config}.py`。

测试 (13): 8 个新文件 (上表) + 增补
`tests/{test_config_lint,test_config_loader,test_console_auth,test_gateway_package_layout,test_startup_audit_round9_batch_c}.py`。

diff 规模: 25 文件, +898 / -206 (不含新测试文件的未跟踪行)。

## 验证

- backend 全量:
  `.venv/Scripts/python.exe -m pytest --ignore=tests/test_get_daily_l1_observability.py
  --ignore=tests/test_mootdx2_p0_perf.py --ignore=tests/test_fuyao_cache_config.py
  -q` → **1353 passed, 3 skipped** (90s)。
  三个 ignore 同既有约定 (standalone/env-gated 套件)。
- TDD: 每个行为修复先写 RED 测试确认旧行为挂, 再改生产码转 GREEN
  (簇H tqcenter import 线程、簇C TTL=0 永久缓存等均验证过真实 RED)。
- **web 未触碰** (本批无 static/console.html/web/ 改动; PUT 侧 TTL pydantic
  门是既有代码), 按约定 vitest / `npm run build` 本批跳过。
- 不开浏览器、不点页面 (既有约定)。
- venv Python 3.13.15 实测。

## 备注 / 后续

- **未 commit** (用户明确 "严格 TDD, 不 commit"); 同工作区还压着
  mootdx2_client.py 的既有未提交改动 (L1 缓存专题 + 两轮 mootdx2 审计,
  见 2026-09-19 mootdx2 handoff), 提交时注意按专题拆分。
- YAML 仍是 seed/模板: 本批对 YAML 出厂模板的任何改动不自动迁移已存在的
  data/config.db (与既有约定一致)。
- Round-8/9 declined 硬化项 (cookie Secure/SameSite、登录限流、0.0.0.0 bind、
  start/stop 幂等等) 本轮不翻案。
- `_sanitize_sync_message` 生产侧仍无调用方 (god-module 拆分时记录的清理项),
  保留不删。
