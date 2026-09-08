# Handoff — gateway_server.py split + real LRU — 2026-09-08

## TL;DR

Refactor 1（拆分 `gateway_server.py`）**部分完成**：
- ✅ `src/core/cache_strategy.py` 创建（6 个纯函数）
- ✅ `src/core/mcp_factory.py` 创建（`NoSlashStarlette`、`install_capability_filter`、`make_mcp_path_canonicalizer`、`create_and_register_gateway_mcp` 工厂）
- ✅ `gateway_server.py` 已删除 `_NoSlashStarlette` / `_install_capability_filter` / `_make_mcp_path_canonicalitizer` 三个 block（约 90 行）
- ⏸ gateway_server.py 仍含 cache strategy helpers + `_create_and_register_gateway_mcp`，未切换到新模块
- ⏸ Refactor 2（真实 LRU）未开始

Import 测试 OK（gateway_server.py 仍可加载）。无 commit，未 commit 的修改在 working tree。

## 上下文

User 在审查 MCP 启动逻辑 / 缓存机制后，从 4 个改进建议中选了 #3（拆分 gateway_server.py）和 #4（真 LRU）。原计划见 `C:\Users\admin\.claude\plans\warm-imagining-quilt.md`。

新 session 接手前先 Read 此文件 + 该 plan，再开始改。

## 当前代码状态

### 已完成

**`src/core/cache_strategy.py`** (新文件，56 行)：
```python
cache_key(name, params) -> str                    # SHA256(name + json(params))
ttl_for(config, ttl_key) -> int | None             # config.cache.ttl[ttl_key]
is_cacheable_source(source) -> bool               # fuyao_* or "mootdx2"
is_fuyao_source(source) -> bool                   # startswith("fuyao_")
is_realtime_ttl(ttl_key) -> bool                  # ttl_key == "realtime_quote"
is_cacheable_data_source(data_source_type) -> bool  # data_source_type == "online"
```
均为 module-level 纯函数。`hashlib` / `json` 在文件顶部 import。

**`src/core/mcp_factory.py`** (新文件，142 行)：
- `class NoSlashStarlette(Starlette)` — 关 `redirect_slashes=True`
- `async install_capability_filter(mcp)` — patch `mcp._mcp_server.get_capabilities`
- `make_mcp_path_canonicalizer(app, mcp_path)` — ASGI 中间件 closure
- `async create_and_register_gateway_mcp(tool_specs, *, version, on_constructed) -> FastMCP`
  - 接受 `on_constructed: Callable[[FastMCP], None]` 回调（GatewayServer 在回调里 `self.mcp = mcp; self._register_tools(specs=tool_specs)`）
  - 三步串行：构造 FastMCP → 回调（注册工具）→ 装 filter
  - 保证 capability filter 必经路径（防止 refactor 跳过 — HIGH #3, 2026-09-07）

**`src/gateway_server.py`**：
- 已删除原来的 `_NoSlashStarlette` 类（约 11 行）
- 已删除原来的 `_install_capability_filter` 函数（约 56 行）
- 已删除原来的 `_make_mcp_path_canonicalizer` 函数（约 14 行）
- 这三个 block 原来在 lines 188-276，现在直接进入 `class GatewayServer:`

### 未完成（剩余工作）

1. **`src/gateway_server.py` 仍有 cache strategy helpers**（约 35 行，lines ~430-465）
   ```python
   def _cache_key(self, name, params): ...        # → cache_strategy.cache_key(name, params)
   def _ttl_for(self, ttl_key): ...                # → cache_strategy.ttl_for(self.config, ttl_key)
   @staticmethod _is_cacheable_source(source): ...
   @staticmethod _is_fuyao_source(source): ...
   @staticmethod _is_realtime_ttl(ttl_key): ...
   @staticmethod _is_cacheable_data_source(data_source_type): ...
   ```
   - `_is_cacheable_source`, `_is_fuyao_source`, `_is_realtime_ttl` 在文件中**实际未被使用**（grep 已确认）。可以直接删除；保留在 cache_strategy.py 里是给将来使用或测试用。
   - `_cache_key`, `_ttl_for`, `_is_cacheable_data_source` 在 `_execute_cached`（lines ~465-560 区域）被调用，需要改为：
     ```python
     from .core import cache_strategy
     ttl = cache_strategy.ttl_for(self.config, ttl_key) if ttl_key else None
     key = cache_strategy.cache_key(name, params)
     and cache_strategy.is_cacheable_data_source(data_source_type)
     ```

2. **`_create_and_register_gateway_mcp` 仍在 `GatewayServer` 类里**（约 20 行）
   - 改为：
     ```python
     from .core.mcp_factory import create_and_register_gateway_mcp

     async def _create_and_register_gateway_mcp(self, tool_specs):
         def _on_constructed(mcp):
             self.mcp = mcp
             self._register_tools(specs=tool_specs)
         return await create_and_register_gateway_mcp(
             tool_specs,
             version=GATEWAY_VERSION,
             on_constructed=_on_constructed,
         )
     ```

3. **`src/storage/cache.py` LRU 改造**：
   - `_set_impl` 写入时同时设 `last_access_at = int(time.time())`
   - `_get_impl` 命中后 `asyncio.create_task(_touch(key))` fire-and-forget 更新 `last_access_at`
   - `_cleanup_impl` 淘汰 SQL: `ORDER BY last_access_at ASC, created_at ASC LIMIT :n`（原 `created_at ASC`）
   - `initialize()` 加幂等 `ALTER TABLE cache ADD COLUMN last_access_at INTEGER`：
     ```python
     cols = (await session.execute(text("PRAGMA table_info(cache)"))).fetchall()
     if not any(c[1] == "last_access_at" for c in cols):
         await session.execute(text("ALTER TABLE cache ADD COLUMN last_access_at INTEGER"))
     ```
   - 新增 inner `_touch(key)` async 函数 + 异常吞掉（不影响主路径）

4. **测试更新**：
   - `tests/test_capability_filter.py`：import 改为 `from src.core.mcp_factory import install_capability_filter`（或对应新名）
   - `tests/test_cache_policy.py::TestCleanupExpiredLru`：原 FIFO 断言改为 LRU 断言
     - 流程：写 k00-k19 → `cache.get(k00)` + `cache.get(k01)` 触发 touch → 触发 cleanup（max_entries=10）
     - 断言 k00/k01 保留，k02-k10 被淘汰

## 关键代码位置

| 文件 | 状态 | 行数 |
|------|------|------|
| `src/core/cache_strategy.py` | 新增 | 56 |
| `src/core/mcp_factory.py` | 新增 | 142 |
| `src/gateway_server.py` | 部分改 | 仍约 1050 行（从 1168 减到 ~1050） |
| `src/storage/cache.py` | 未改 | 442 |
| `tests/test_capability_filter.py` | 未改 | 路径更新即可 |
| `tests/test_cache_policy.py` | 未改 | LRU 断言改写 |

## 恢复步骤（新新 session）

```bash
# 1. 读 handoff
cat docs/handoffs/2026-09-08-gateway-split-and-lru.md
cat C:/Users/admin/.claude/plans/warm-imagining-quilt.md  # 完整 plan

# 2. 验证当前 import OK
cd D:/fintech_workspace/unihive
python -c "from src.gateway_server import GatewayServer; print('OK')"

# 3. 继续 Refactor 1 收尾
#    - 删 gateway_server.py 中 cache strategy helpers
#    - 改 _execute_cached 调用 cache_strategy.xxx
#    - 改 _create_and_register_gateway_mcp 用工厂
#    - 跑: pytest tests/test_capability_filter.py tests/test_cache_policy.py tests/test_startup_fixes.py -v

# 4. Refactor 2: 真 LRU
#    - 改 src/storage/cache.py (last_access_at 列 + touch + 新淘汰 SQL)
#    - 改 tests/test_cache_policy.py (TestCleanupExpiredLru)
#    - 跑: pytest tests/test_cache_policy.py tests/test_cache.py -v

# 5. 全部跑: pytest tests/ -v (除已知非阻塞 fail)
# 6. commit: chore(refactor): split gateway_server.py + real LRU eviction in cache
```

## 已知陷阱

- `cache_strategy.is_realtime_ttl` 在 gateway_server.py 中实际未被调用 — 但已在新模块保留。是否删？保留还是删取决于是否做 dead-code 审计。**建议保留**（公开 API 风格 + 便于测试）。
- `Cache._touch` 用 `asyncio.create_task` fire-and-forget：cache close 时 in-flight touch 会被 engine dispose 取消；最坏损失一次 timestamp，行为可接受。如果发现 close 后仍有 dangling task 在 warning，可加 `self._pending_touches: set[Task] = set()` + `add_done_callback(discard)` 模式。
- `_create_and_register_gateway_mcp` 改写后，`_register_tools(specs=tool_specs)` 仍由 GatewayServer 自己调（通过回调），便于后续往 `self.mcp` 加其他 tool。
- 不要把 `from .core import cache_strategy` 写在文件顶部 — Python import 顺序规则要求它在 `from .models.tdx_quant_config` 之后即可。

## 不在本 handoff 范围

- Refactor 2 中 `Cache.lock` 改 RWLock（LOW, deferred）
- 其他 audit 留下的 LOW（upstream 0.5s sleep heuristic, `console_api.set_config()` double-call）