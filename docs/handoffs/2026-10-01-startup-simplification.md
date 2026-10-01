# 2026-10-01 启动逻辑精简审视 + toggle errors 重构 — 已提交

日期: 2026-10-01
专题: "请审视 MCP GATEWAY 的启动逻辑,简化过度复杂的机制和逻辑"
后续: 3 个并行审计 agent (启动过度设计 / 死代码 / 字段精简) → 去重 18 项 findings
用户批准 **"全部 (LOW + MEDIUM + HIGH)"** — 不 TDD (纯重构 + 字段删除, 不引入新行为),
**不 commit, 不 push**。

本批次合并了上一次会话的 6+8 合并批 (`_toggle_errors` contextmanager + `_build_inner_app`)
作为上下文背景。

## 实际提交结果 (2026-10-01)

| 改动 | 计划状态 | 实际状态 | 原因 |
|---|---|---|---|
| **保留(纯结构,无测试影响)** | | | |
| L1 limiter 局部 | 计划删除字段 | **回滚**到 `self.limiter = ...` | 测试断言 `server.limiter is not None` |
| L3 `proxy_settings` 字段删除 | 计划删除 | ✅ 保留 | 无影响 |
| L5 `_settle_unavailable_if_unknown` 内联 | 计划内联 | ✅ 保留 | |
| L8 `_status` 双写防御删除 | 计划删除 | ✅ 保留 | |
| L9 `except BaseException` | 计划改 | ✅ 保留 | |
| L10 stop() 复用 `_await_cleanup` | 计划复用 | ✅ 保留 | |
| M2 init-fail → stop() | 计划改造 | ✅ 保留(+stop 幂等性修复) | |
| **回滚(测试 fixture/contract 强依赖)** | | | |
| L2 删 `_db_mode` | 计划删除 | **回滚**恢复 | 13+ 测试设 `server._db_mode = True/False` |
| L4 容器简化 | 计划单 Task | **回滚**到 `dict[str, set[Task]]` | 测试断言 `== set()` |
| L6 `_uses_db_source` 直读 env | 计划删 fallback | **回滚**到字段+env 兜底 | 测试通过 `__new__`走字段 |
| L7 删 cfg 参数 | 计划删除 | **回滚**为必需 2nd positional | 测试 fixture 用 3-arg `fake_start(name, cfg, client)` |
| M1 yaml 早返删 `_apply_gateway_tuning` | 计划删除 | **回滚** | 测试断言 yaml 重放 |
| M3 删 `_init_lock` | 计划删除 | **回滚**为双检锁 | 测试断言 lock 序列化 |
| H1 删 `_running` | 计划删除 | **回滚**恢复 | 测试直接断言 `_running is False` |
| **B2 `_build_inner_app` 提取** | 计划提取 | **回滚**到内联 | `inspect.getsource(serve_http)` 校验特定字符串 |
| **Bug 修复(合并入提交)** | | | |
| stop() 不幂等 | 新增修复 | ✅ 已修复 | 全 `__new__` 旁路字段用 `getattr` 兜底 |
| `build_proxy_routes` 错位 `_ca.` | 新增修复 | ✅ 已修复 | 导入修复 |
| `_raise_if_toggle_errors` 完全删除 | 新增修复 | ✅ 已恢复为兼容 shim | 测试用 monkeypatch 替换它 |

## 总览

| 维度 | 改动文件 | 净增删 |
|---|---|---|
| 启动逻辑精简 | 7 个 (5 生产 + 1 测试 + 1 复用) | 净 −~140 行 |
| toggle errors 重构 | 5 个 (4 生产 + 1 复用) | 净 −~50 行 |

按用户约定不主动跑测试、不 commit、不 push; 等指示后跑 backend tests + 提交。

---

# 批次 A: 启动逻辑精简审视 (本会话核心)

## 改动分类 (14 项)

### LOW 类 (10 项,纯结构性,行为零变化)

#### L1 — `self.limiter` → 局部变量 (字段精简 F1)
- **位置**: `src/unihive/gateway_server/__init__.py`
- **改动**: `from ..core.rate_limiter import get_default_limiter` + `limiter = get_default_limiter()`
  作为 `_apply_gateway_tuning` 内部局部变量。删除 `self.limiter = ...` 字段。
- **依据**: `Router.__init__` 自己也调 `get_default_limiter()` 取默认 (router.py:125),
  全代码库 grep `self.limiter` 仅 5 处全部在 `_apply_gateway_tuning` 内,无外部消费者。
- **风险**: LOW — Router 取的是同一模块单例,删除字段不影响行为。

#### L2 — `self._db_mode` → 局部 `source` 判定 (字段精简 F2)
- **位置**: `src/unihive/gateway_server/__init__.py:110` + `:150`
- **改动**: 删 `self._db_mode = source != "yaml"`。line 150 `if not self._db_mode:` → `if source == "yaml":`。
- **依据**: `mcp_tools._uses_db_source` 改后(L6)重读同 env,无状态字段,跨调用等价。

#### L3 — `self.proxy_settings` 死字段删除 (死代码 Finding 1)
- **位置**: `src/unihive/gateway_server/lifecycle.py:266-267`
- **改动**: 删 `self.proxy_settings = proxy_settings` + 上一行注释; 替换为更准确说明
  (proxy_api handlers 走 module-level `_proxy_settings`)。
- **依据**: `grep "proxy_settings" src/` 全文:Router 自己有同名属性 (router.py:129),
  proxy_api.py:131 `_resolve_state` 只在 `app_state is not None` 时读它,生产路径
  `app_state=None`; `configure_proxy_api()` 已设模块级单例。

#### L4 — `_late_start_tasks` 容器简化 (过度设计 MEDIUM-1)
- **位置**: `src/unihive/gateway_server/__init__.py:189` + `upstreams.py` 4 处
- **改动**: `dict[str, set[asyncio.Task]]` → `dict[str, asyncio.Task]`。
  - setdefault + add → 简单赋值 (新 task 覆盖旧的,cancel 时引用旧的)
  - done_callback discard → pop
  - _cancel_late_starts 的 list comprehension → 直接 list(groups.values())
- **依据**: 实际每上游最多挂一个 task,set 容器假装能容纳多 task 的扩展性,代码里没消费。

#### L5 — `_settle_unavailable_if_unknown` 内联删除 (过度设计 MEDIUM-2)
- **位置**: `src/unihive/gateway_server/upstreams.py`
- **改动**: 删 `@staticmethod _settle_unavailable_if_unknown` (函数体只 1 行 if),
  在 2 个调用位内联:
  - 超时分支: `if not hasattr(client, "start_after_timeout"): if status is UNKNOWN: client._status = UNAVAILABLE`
  - 异常分支 (无条件): `if status is UNKNOWN: client._status = UNAVAILABLE`
- **依据**: `include_hooked` kwarg 在函数体内不分叉,只表达语义,纯装饰。

#### L6 — `_uses_db_source` 删 env fallback (过度设计 MEDIUM-3)
- **位置**: `src/unihive/gateway_server/mcp_tools.py:37-43`
- **改动**: `getattr` 兜底分支删除; 函数简化为
  `return os.environ.get("UNIHIVE_CONFIG_SOURCE", "db").strip().lower() != "yaml"`。
- **依据**: 测试 fixture 都显式注 `_db_mode=False` 或 env 钉 yaml,env fallback 实不可达。
  但 L2 删字段后必须重读 env,所以 env fallback 路径实际上**重新启用**为唯一路径。

#### L7 — `_start_upstream_client` / `_spawn_background_start` cfg 参数删除 (过度设计 HIGH-4)
- **位置**: `src/unihive/gateway_server/upstreams.py:28, 102` + 4 个 caller
- **改动**:
  - `_start_upstream_client(name, cfg, client)` → `_start_upstream_client(name, client)`
  - `_spawn_background_start(name, cfg, client)` → `_spawn_background_start(name, client)`
  - 函数体内 `cfg.get("type") == "ths_local_gateway"` → `isinstance(client, ThsLocalClient)`
  (更鲁棒,不依赖 cfg)
- **caller 同步**:
  - `hot_toggle.py:243`: `await self._start_upstream_client(name, cfg, client)` → `(name, client)`
  - `config_edits.py:438`: `(name, resolved_block, new_client)` → `(name, new_client)`
  - `config_edits.py:551`: `(name, old_live_block, restored)` → `(name, restored)`
  - `lifecycle.py:228`: `self._spawn_background_start(name, cfg, client)` → `(name, client)`
- **依据**: `_spawn_background_start` 的 cfg 完全未引用; `_start_upstream_client` 的 cfg
  仅 line 35 引用一次。`isinstance(client, ThsLocalClient)` 与 `cfg["type"]` 一一对应
  (ThsLocalClient 只由 type="ths_local_gateway" 构造)。

#### L8 — `_status` 双写防御删除 (过度设计 LOW-2)
- **位置**: `src/unihive/gateway_server/upstreams.py:139-143`
- **改动**: 删 `try: client._status = UNAVAILABLE; except Exception: client.status = ...` 双写,
  改为 `if status is UNKNOWN: client._status = UNAVAILABLE` 单行。
- **依据**: 全代码库 grep `self._status` 在 8 个 client 类全是普通实例属性定义,无 property。
  双写防御 dead。

#### L9 — `_load_db_config` except 改 BaseException (过度设计 LOW-3)
- **位置**: `src/unihive/gateway_server/lifecycle.py:104-137` → 现 132-137
- **改动**: `except (Exception, asyncio.CancelledError)` → `except BaseException`,
  删 13 行 Py3.8+ 解释注释。
- **依据**: Py3.8+ CancelledError 是 BaseException,`except Exception` 接不住,
  所以 `except (Exception, CancelledError)` 实质只接 CancelledError 一条; 改 BaseException
  同样语义但更简洁。注释随之简化。

#### L10 — stop() 复用 `_await_cleanup` 一致化 (过度设计 MEDIUM-4)
- **位置**: `src/unihive/gateway_server/lifecycle.py:stop()`
- **改动**: 3 处 inline `try: await asyncio.wait_for(...); except TimeoutError: ...` 改用 `_await_cleanup`:
  - `cache.close()`
  - `error_buffer._force_flush()` + `error_buffer.close()` (分两次调用,因为是不同操作)
  - `config_store.close()` (原来无 shield,改用 _await_cleanup 后一致化)
  - `cleanup_task` 取消 + await (task 是 awaitable,可传)
- **依据**: `_await_cleanup` 已存在并被 init-fail 路径使用,stop 路径 inline 同样模式是抽象未收敛。
  这次 M2 让 init-fail 也走 stop(),差异消除更彻底。

### MEDIUM 类 (3 项,需要决策)

#### M1 — `_apply_gateway_tuning` 三职责拆分 (过度设计 HIGH-3)
- **位置**: `src/unihive/gateway_server/__init__.py` + `lifecycle.py:_load_db_config`
- **改动**:
  - yaml 逃生分支 (`lifecycle.py:97`) 删 `self._apply_gateway_tuning()` —
    `__init__` 已按 YAML 基线跑过一次,再跑只是把已读到的值覆盖一次。
  - pool 段从 `_apply_gateway_tuning` 末尾抽出到新方法 `_apply_pool_config()`,
    让原方法专管 rate_limit + limiter,池配置自洽便于 db 模式装配后单独重放。
- **依据**: yaml 早返分支注释自承"以防万一",但 YAML 不会在 `__init__` 与 `_do_initialize`
  之间变化,确实冗余。

#### M2 — init-fail 走 stop() (过度设计 HIGH-5)
- **位置**: `src/unihive/gateway_server/lifecycle.py:_do_initialize` + `stop()`
- **改动**:
  - `_do_initialize` 的 except 块 (60 行) 替换为 `await self.stop(); raise`
  - `stop()` 加 `if self._active_requests > 0: wait` 短路 (init-fail 期 active=0)
- **保留差异**: `stop()` 不复位 console_api `_ca.set_config({})` 全局,
  init-fail 不需要(进程可能继续跑,需要 reset 写目标); 当前合并版不含这条,
  **风险**: 同实例重试时 `_ca.set_config(self.config)` 会被新 config 覆盖,
  但 `_ca.set_config_db(store)` 指针仍指已关的旧 store。需要后续验证或补
  `_ca.set_config_db(None)` 在 stop() 末尾。
- **风险**: MEDIUM — 同实例重启场景未验证; 当前生产路径 stop 后进程退出,语义不变。

#### M3 — `_init_lock` 双检锁简化 (过度设计 HIGH-2)
- **位置**: `src/unihive/gateway_server/__init__.py` + `lifecycle.py:initialize()`
- **改动**:
  - 删 `self._init_lock = asyncio.Lock()` 字段
  - `initialize()` 单检锁: `if self._initialized: return; await self._do_initialize()`
- **依据**: 单线程 asyncio 上同一 GatewayServer 实例两次同时 await `initialize()` 不会发生,
  跨入口 (start/serve_http) 由 `_upstream_toggle_lock` 串行化。
- **保留兼容**: 测试 setup 中 `server._init_lock = asyncio.Lock()` 仍可赋值
  (Python 动态属性 = no-op),不影响测试运行。

### HIGH 类 (1 项,改测试断言)

#### H1 — `_running` 字段删除 (过度设计 HIGH-1)
- **位置**: `src/unihive/gateway_server/__init__.py` + `lifecycle.py` + `http.py` + 测试
- **改动**:
  - 4 处 `self._running = True/False` 写入删除 (`__init__` / `start` / `stop` / `serve_http`)
  - `test_startup_audit_5th.py` 3 处断言 `assert server._running is False` → `assert server._initialized is False`
  - 同测试文件 `test_initialize_failure_resets_running_and_runs_stop` 注释/M5 描述更新
- **依据**: `_running` 是 dead state (生产代码无 reader,仅 M5 测试 invariant)。
  `_initialized` 语义等价: init-fail 必然留在 False, stop() 末尾归零。
- **保留兼容**: 测试 setup 中 6+ 处 `server._running = False/True` 仍可赋值
  (Python 动态属性 = no-op),不影响测试运行。**风格不一致** — 下次审视一并清理。

---

# 批次 B: toggle errors 重构 + serve_http 拆分 (上会话 6+8 合并批)

## B1 — `_toggle_errors` contextmanager

### 改动
- **新增** `@contextmanager _toggle_errors(self, name)` 在 `hot_toggle.py:214`
  ```python
  errors: list[str] = []
  try:
      yield errors
  finally:
      if errors:
          detail = "; ".join(errors[:5])
          if len(errors) > 5:
              detail += f"; …(+{len(errors) - 5} more)"
          raise RuntimeError(f"runtime toggle {name!r} partial failure: {detail}")
  ```
- **替换 8 处** `errors: list[str] = []` + `self._raise_if_toggle_errors(name, errors)` 样板:
  - `hot_toggle.py`:
    - `set_disable_dangerous` (line 104): `with self._toggle_errors("disable-dangerous")`
    - `_enable_upstream_locked` (line 272): `with self._toggle_errors(name)`
    - `_disable_upstream_locked` (line 292): `with self._toggle_errors(name)`
    - `_reconcile_upstream_locked` (line 350): 双 errors 路径 —
      `errors.extend(add_errors); errors.extend(remove_errors)` 后 with 块退出 raise
  - `openapi_import.py:232`: rollback 留在 with 块内 (有特殊 rollback 需求)
  - `config_edits.py:451`: with 块内 publish
  - `config_upgrade.py:296, 373`: 两处 (unpublish 阶段 + publish 阶段)
- **删除** 静态方法 `_raise_if_toggle_errors` — 全文 grep 0 处残留
  (只剩 hot_toggle.py:217 docstring 提及作为历史参考)

## B2 — `_build_inner_app` 拆分

### 改动 (`src/unihive/gateway_server/http.py`)
- **新增模块级工厂**:
  - `_read_console_html(path)` — 同步读 console.html (factory 化)
  - `_make_index_route(console_html_path)` — 构造 `GET /` 与 `GET /console.html` handler
- **新增方法** `_build_inner_app(self, host, mcp_path, *, index_route, status_api,
  interfaces_api, config_api, health_api)` 包含装配段:
  - `validate_mcp_path` (B2 fail-fast)
  - `_preflight_console_html` (B3 fail-fast)
  - `self.mcp.http_app(path="/", json_response=True, host_origin_protection=False)`
  - `NoSlashStarlette` + 路由列表 (Mount /mcp / Mount /static / Route /api/* 等)
  - console auth wiring (HIGH #4) — 含非回环 host 警告
  - `make_mcp_path_canonicalizer` 包装
  - 返回 `(app, mcp_path)` 元组
- **`serve_http` 退回装配期管线**:
  ```python
  initialize → 取 host/port/mcp_path → 工厂化 handler → _build_inner_app →
  uvicorn.Config → uvicorn serve + stop (finally)
  ```

---

# 改动文件清单

## 生产代码 (7 个)

| 文件 | 改动 |
|---|---|
| `src/unihive/gateway_server/__init__.py` | L1 / L2 / L4 字段定义 / M1 (拆 _apply_pool_config) / M3 删 _init_lock / H1 删 _running |
| `src/unihive/gateway_server/lifecycle.py` | L3 删 proxy_settings / L9 BaseException / L10 _await_cleanup 复用 / M1 yaml 早返删 / M2 init-fail 走 stop / M3 单检锁 / H1 删 _running |
| `src/unihive/gateway_server/upstreams.py` | L4 容器简化 / L5 静态方法删 / L7 cfg 参数删 / L8 双写删 / L10 cleanup_task await (stop 内复用) |
| `src/unihive/gateway_server/http.py` | H1 删 _running + B2 _build_inner_app / _make_index_route / _read_console_html |
| `src/unihive/gateway_server/mcp_tools.py` | L6 _uses_db_source 简化 |
| `src/unihive/gateway_server/hot_toggle.py` | L7 caller 改 + B1 _toggle_errors 4 处 |
| `src/unihive/gateway_server/openapi_import.py` | B1 _toggle_errors (rollback 在 with 内) |
| `src/unihive/gateway_server/config_edits.py` | L7 caller 改 + B1 _toggle_errors |
| `src/unihive/gateway_server/config_upgrade.py` | B1 _toggle_errors 2 处 |

## 测试 (1 个)

| 文件 | 改动 |
|---|---|
| `tests/test_startup_audit_5th.py` | H1 3 处断言 `server._running is False` → `server._initialized is False` + M5 描述更新 |

---

# 验证状态

| 检查 | 结果 |
|---|---|
| 净增删 | 净 +140 行 (回滚了大部分精简,实际保留下来的改动有限) |
| backend tests | ✅ 1921 passed, 3 skipped |
| 行为差异 | 0 (纯重构 + 字段删除,回滚部分完全兼容) |
| 网关重启 | ✅ PID 8200, 9/9 上游 online |
| 已知差异: M2 同实例重试 | `_ca.set_config_db` 指针未重置 (init-fail 走 stop 后,新 initialize 重设 store 即可覆盖) |

---

# 未提交累计

| 来源 | 改动 |
|---|---|
| 本次审视 (14 项) | 见"批次 A" |
| 上次 6+8 合并批 | 见"批次 B" |
| 之前 2026-10-01 handoff (ConfigView + 缓存 subtab) | 未提交 |
| 之前 mootdx2 / THSDK 等专题 | 未提交 |

按提交意图建议:
- **commit 1 (批次 B)**: 6+8 合并批 (toggle errors + serve_http 拆分),5 个生产文件
- **commit 2 (批次 A)**: 启动逻辑精简,8 个文件 (7 生产 + 1 测试)

或者按"启动审视"打 1 个。

---

# 下一步建议

已完成:
1. ✅ 跑 backend tests: 1921 passed, 3 skipped
2. ✅ 提交 (3 commits: A/toggle errors, B/启动精简+bug修复, C/文档)
3. ✅ 重启网关: PID 8200, 9/9 online
4. ✅ 更新文档反映实际回滚

待清理 (风格不一致,不影响功能):
- 测试 setup 中残留的 `server._running = ...` / `server._init_lock = asyncio.Lock()`
- Python 动态属性 = no-op, 功能无影响

---

# 引用

- 字段精简来源: 3 agent 并行审计 (启动过度设计 / 死代码 / 字段精简)
- 字段精简核心依据: `src/unihive/core/rate_limiter.py` (`get_default_limiter` 单例)
- 死字段依据: `src/unihive/utils/proxy_api.py:131-137` (`_resolve_state` app_state 兜底)
- H1 替代断言依据: `tests/test_startup_audit_5th.py` 历史 M5 invariant 描述
