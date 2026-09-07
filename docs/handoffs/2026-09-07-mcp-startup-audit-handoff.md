# MCP Startup Audit Handoff — 2026-09-07

## TL;DR

5 项 audit 修复全部完成且测试 GREEN，但**没 commit**，藏在 `git stash@{0}` 里。
新 session 第一件事是 `git stash pop` 把它们拿出来跑测试，OK 后 commit。

## 上下文

第一轮 MCP startup audit（2026-09-07 上午）已修 HIGH #3 capability filter、HIGH #4 console_auth wiring、C1 非 timeout 异常处理；M1（init 失败重置 router/mcp）和 M2（capability filter 工厂化）延后。

第三轮 audit 又发现：
- **HIGH B1**：`stop()` 不重置 `_initialized` / `upstreams` / `router` / `mcp`，同实例 start→stop→start 会路由到死客户端
- **MED A1** = M1：`_do_initialize` cleanup 块也不重置 `router` / `mcp`
- **MED A2** = M2：抽 `_create_and_register_gateway_mcp` 工厂，强制 capability filter 必经路径
- **MED B2a**：2 个 `TestGatewayShutdownChain` 测试 mock 缺 `.lifespan` 属性
- **MED B2b**：TestStartupBanner 用 `capfd` 而后续 TestPortInUse 用 `capsys`，configure_logging 装的 sys.stderr StreamHandler 写 closed file。改 capfd→capsys + 加 autouse fixture 清 root handlers
- **LOW B3**：cleanup 块 `except (asyncio.TimeoutError, Exception)` 冗余（TimeoutError 已是 Exception 子类）

## 当前状态

```bash
$ git status --short
 M .claude/settings.local.json
 M .env.example
 M config/upstreams.yaml
 M console.html
 D logs/cache.db-shm
 D logs/cache.db-wal
 M logs/cache_stats.json
 M src/console_api.py
 M src/gateway_server.py         ← 5 项修复的代码改动
 M tests/test_gateway_startup_batch3.py   ← B2a mock 修复 + B2b capsys/autouse
 M tests/test_startup_fixes.py            ← B1 + A1 + A2 的测试
?? config/config.yaml
?? cookies.txt
?? docs/handoffs/              ← 本文件目录
?? docs/superpowers/
?? skills/
?? src/console_auth.py
?? tests/test_capability_filter.py        ← HIGH #3 测试（untracked）
?? tests/test_console_auth.py
?? tests/test_console_auth_env.py
?? tests/test_mcp_http_compliance.py
?? tests/test_startup_init_failure.py     ← C1 测试（untracked）

$ git stash list
stash@{0}: WIP on main: ffd6c9d feat(mootdx2): add Reader-based offline K-line tools
```

**注意**：`git stash show -p stash@{0} -- <file>` 路径过滤在这 git 版本不输出 patch。
`git stash show -p stash@{0} | git apply` 或 `git stash pop` 才有效。

## 恢复步骤

```bash
# 1. 应用 stash (会报 "kept" 警告但内容已落盘; 也可走 git apply 路径)
git stash show -p stash@{0} | git apply
# 冲突则:
#   git checkout --theirs src/gateway_server.py tests/test_startup_fixes.py tests/test_gateway_startup_batch3.py
#   git stash pop

# 2. 跑 audit 测试 (期望 40 通过 + 1 skip Windows SIGINT)
python -m pytest tests/test_startup_init_failure.py tests/test_gateway_startup_batch3.py tests/test_startup_fixes.py tests/test_capability_filter.py tests/test_mcp_http_compliance.py -v

# 3. 通过后 commit
git add src/gateway_server.py tests/test_startup_fixes.py tests/test_gateway_startup_batch3.py tests/test_startup_init_failure.py tests/test_capability_filter.py tests/test_mcp_http_compliance.py tests/test_console_auth.py tests/test_console_auth_env.py src/console_auth.py src/console_api.py config/upstreams.yaml config/config.yaml docs/handoffs/2026-09-07-mcp-startup-audit-handoff.md
git commit -m "fix(startup): apply 3rd-round audit fixes (B1, A1, A2, B2a/b, B3)

HIGH B1: stop() resets _initialized / upstreams / router / mcp
  → same-instance start()→stop()→start() works instead of routing
    to dead clients
MED A1: _do_initialize cleanup also resets router / mcp (M1 from 2nd audit)
MED A2: extract _create_and_register_gateway_mcp factory (M2 from 2nd audit)
  → bundles FastMCP construction + _register_tools + capability filter
    install so future refactors can't bypass the filter
MED B2a: TestGatewayShutdownChain mocks return FakeMcpApp with .lifespan
MED B2b: TestStartupBanner capfd→capsys + autouse fixture clears root
         logging handlers between tests (configure_logging sys.stderr
         StreamHandler wrote to closed file across capsys/capfd transition)
LOW B3: drop redundant except (asyncio.TimeoutError, Exception) clauses
        (TimeoutError already Exception subclass)

40 tests pass + 1 skipped (Windows SIGINT)."

# 4. 清 stash (commit 后 stash 仍在, drop)
git stash drop stash@{0}
```

## 关键代码位置（应用后查这里）

**`src/gateway_server.py`**：
- `_install_capability_filter` 函数：模块顶部 `_get_console_auth_config` 下方
- `stop()` 末尾：cache close 之后、log "Gateway shutdown complete" 之前，加 4 行 reset
- `_do_initialize` cleanup 块：`raise` 之前加 `self.router = None; self.mcp = None`
- `_create_and_register_gateway_mcp` 方法：在 `_load_all_tools` 之前
- `_do_initialize` MCP 构造部分改成 `await self._create_and_register_gateway_mcp(tool_specs)`

**`tests/test_startup_fixes.py`**：3 个新测试类在文件末尾（autouse fixture 之前）：
- `TestCreateAndRegisterGatewayMcp` (A2)
- `TestInitializeFailureResetsRouterMcp` (A1)
- `TestStopResetsStateForRestart` (B1, 3 个测试)

**`tests/test_gateway_startup_batch3.py`**：
- 顶部 import `logging`，加 `_reset_root_logging_handlers` autouse fixture
- `TestStartupBanner.test_async_main_logs_startup_banner` 把 `capfd` 换成 `capsys`

## 已知非阻塞问题（不在本批）

1. `tests/test_console_auth_env.py` 引用不存在的 `_console_auth_config_from_env`（应为 `_get_console_auth_config`，函数被改过名时没同步测试）。这是预存 break，不是本批改动引入。
2. `tests/test_cache_policy.py` / `tests/test_console_auth.py` 各有 1-2 个失败，与本批 audit 无关，是其他已存在的问题。新 session 不要去碰它们，除非用户单独要求。

## 5 项改动快速参考

| # | 严重度 | 文件 | 改了什么 |
|---|---|---|---|
| B1 | HIGH | gateway_server.py | stop() 末尾 +4 行 reset state |
| B2a | MED | test_startup_fixes.py | 2 个 mock 加 FakeMcpApp 包装 |
| B2b | MED | test_gateway_startup_batch3.py | capfd→capsys + autouse logging reset |
| A1 | MED | gateway_server.py | cleanup 块 reset router/mcp + 简化 except |
| A2 | MED | gateway_server.py | 加工厂 + 调用点改工厂 |
| B3 | LOW | gateway_server.py | cleanup 块 except 简化（与 A1 同一处） |
