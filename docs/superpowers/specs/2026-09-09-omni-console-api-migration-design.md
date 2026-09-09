# OMNI Console API 迁移设计 (2026-09-09)

## 背景与目标

`gateway_server.py` 当前直接实现了 4 个 OMNI 系列 HTTP 端点：
- `_omni_api` — 板块统计 + 最近同步日志
- `_omni_sync_api` — 触发同步（subprocess.run board_sync.py）
- `_omni_query_api` — 板块查询（分页 + 关键词搜成分股）
- `_omni_sector_stocks_api` — 单板块成分股

这违反了分层原则：**Gateway 只负责 MCP 路由 + 健康检查 + upstream status；Console 业务（同步、统计、查询、配置、接口列表）应归 console_api.py**。

类似端点（`_interfaces_api / _config_api / _health_api / _mcp_tools_api`）已经走 `console_api.py` 的纯函数模式，唯独 OMNI 系列从未归位。本次迁移让 4 个 OMNI 端点遵循同一约定。

次要目标（顺手解决）：
- **双 log 写入 bug**：当前每次同步写两行 `sector_sync_log`（gateway 一行 + board_sync 自己的 log_sync_start 一行）
- **`record_count` 永远为 0**：gateway 内联 subprocess 无法从 board_sync 拿回写入数
- **同步逻辑散落**：subprocess 编排 + SQL + source 白名单全在 gateway_server.py

## 目标架构

```
                  ┌─────────────────────────────────────────┐
                  │ gateway_server.py                       │
                  │  - MCP routing (/mcp)                   │
                  │  - Health (/health)                   │
                  │  - upstream status (/api/status)        │
                  │  - console HTML (/console.html)         │
                  │  - auth middleware                       │
                  │  - Route 注册: 把 console 路由委托给     │
                  │    _ca.build_routes(omni_client)        │
                  └────────────────┬────────────────────────┘
                                   │ 委托
                                   ▼
                  ┌─────────────────────────────────────────┐
                  │ console_api.py                          │
                  │  - get_omni_stats()                     │
                  │  - query_omni_sectors()                 │
                  │  - run_omni_sync()                      │
                  │  - get_omni_sector_stocks()             │
                  │  - build_routes() → list[Route]         │
                  │    (gateway 调一次拿到所有 OMNI Route)  │
                  │  - set_omni_client(client) setter       │
                  └────────────────┬────────────────────────┘
                                   │ 调用
                                   ▼
                  ┌─────────────────────────────────────────┐
                  │ OmniClient (src/api/omni_client.py)     │
                  │  - get_sector_stocks()      [已有]      │
                  │  - run_sync()                [新增]     │
                  │  - get_stats() / get_logs()  [新增]     │
                  │  - query_sectors()           [新增]     │
                  └────────────────┬────────────────────────┘
                                   │ 调用
                                   ▼
                  ┌─────────────────────────────────────────┐
                  │ board_sync.py                           │
                  │  - sync_fuyao_index()        [纯函数]   │
                  │  - sync_tqquant()            [纯函数]   │
                  │  - 删 log_sync_start/end                │
                  │  - main(): argparse → OmniClient.run_sync│
                  └─────────────────────────────────────────┘
```

## 改动清单

### 1. `src/api/board_sync.py`（业务函数净化）

**删除**:
- `log_sync_start()` / `log_sync_end()` 函数定义（L130-152）
- 函数所有调用点（L194, L205, L311, L316, L447, L508, L511, L516）

**改造**:
- `sync_fuyao_index(board_type)` 和 `sync_tqquant(board_type)` 改为返回 `dict`，不再写 log：
  ```python
  async def sync_fuyao_index(board_type: str = "all") -> dict:
      """Returns {"status": "success"|"failed", "message": str, "record_count": int}"""
  ```
- `main()` argparse 入口改为薄壳：
  ```python
  async def main():
      args = parser.parse_args()
      if args.status:
          # 打印 stats/logs (CLI 调试)
      else:
          omni = OmniClient(OmniConfig())
          result = await omni.run_sync(args.source, args.type)
          print(json.dumps(result, ensure_ascii=False))
          sys.exit(0 if result["status"] == "success" else 1)
  ```

### 2. `src/api/omni_client.py`（业务统一入口）

**新增公共方法**（替代 `_tool_*` 内部 SQL）：
- `def get_stats() -> dict` — 板块数 + 股票数按 (source, board_type) 聚合
- `def get_recent_logs(limit: int = 10) -> list[dict]` — 读 sector_sync_log
- `def query_sectors(source="", board_type="", keyword="", page=1, page_size=50) -> dict` — 分页 + 关键词搜板块
- `async def run_sync(source, board_type="all") -> dict` — **同步编排唯一入口**：
    1. `log_id = _insert_running_log(source, board_type)` — 私有 DB helper
    2. `try: result = await asyncio.wait_for(_dispatch_sync(source, board_type), timeout=300)` — 按 source 分发, 5min 超时
    3. `except asyncio.TimeoutError: result = {"status": "failed", "message": "同步超时 (5分钟)", "record_count": 0}`
    4. `except Exception as e: result = {"status": "failed", "message": str(e)[:200], "record_count": 0}`
    5. `_update_log(log_id, result["status"], result["message"], result["record_count"])`
    6. 返回 `{"status", "message", "log_id", "record_count"}`
- `def get_sector_stocks(...) -> dict | None`（已有，本轮不改）

**调整**:
- `_tool_omni_get_sync_status` / `_tool_omni_list_sectors` / `_tool_omni_search_board` / `_tool_omni_get_sector_stocks` 内部改为调用上述公共方法（DRY）

### 3. `src/utils/console_api.py`（OMNI 业务函数 + Route 工厂）

**新增模块级状态**:
```python
_OMNI_CLIENT: OmniClient | None = None

def set_omni_client(client: OmniClient) -> None:
    """GatewayServer 在 initialize() 后注入 OmniClient 实例"""
    global _OMNI_CLIENT
    _OMNI_CLIENT = client

def _omni() -> OmniClient | None:
    return _OMNI_CLIENT
```

**新增业务函数**（纯函数, 返回 dict）:
- `def get_omni_stats() -> dict` — 调 `_omni().get_stats()` + `get_recent_logs()`
- `def query_omni_sectors(source, board_type, keyword, page, page_size) -> dict` — 调 `_omni().query_sectors(...)`
- `def run_omni_sync(source, board_type) -> dict` — 调 `_omni().run_sync(...)`，source 白名单在这里做（业务边界）
- `def get_omni_sector_stocks(sector_id, code, source) -> dict` — 调 `_omni().get_sector_stocks(...)`

**新增 Route 工厂**:
```python
def build_omni_routes() -> list:
    """返回所有 OMNI Route, gateway 直接挂载"""
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    
    async def omni_stats_api(req):
        return JSONResponse(get_omni_stats())
    async def omni_query_api(req):
        params = req.query_params
        return JSONResponse(query_omni_sectors(
            source=params.get("source", ""),
            board_type=params.get("board_type", ""),
            keyword=params.get("keyword", ""),
            page=int(params.get("page", 1)),
            page_size=int(params.get("page_size", 50)),
        ))
    async def omni_sync_api(req):
        body = await req.json()
        return JSONResponse(run_omni_sync(
            source=body.get("source", "all"),
            board_type=body.get("board_type", "all"),
        ))
    async def omni_sector_stocks_api(req):
        params = req.query_params
        return JSONResponse(get_omni_sector_stocks(
            sector_id=params.get("sector_id", ""),
            code=params.get("code", ""),
            source=params.get("source", ""),
        ))
    
    return [
        Route("/api/omni", omni_stats_api),
        Route("/api/omni/sync", omni_sync_api, methods=["POST"]),
        Route("/api/omni/query", omni_query_api),
        Route("/api/omni/sector-stocks", omni_sector_stocks_api),
    ]
```

### 4. `src/gateway_server.py`（瘦身）

**删除**:
- `_omni_api` / `_omni_sync_api` / `_omni_query_api` / `_omni_sector_stocks_api` 4 个内联函数
- 4 个对应 `Route(...)` 注册调用
- 内联 `import sqlite3`, `import subprocess`, `from pathlib import Path`

**新增**:
- `initialize()` 末尾 `await self.upstreams` 构建完成后，调 `_ca.set_omni_client(_find_omni_client())`
- `stop()` 中调 `_ca.set_omni_client(None)` 重置
- `Routes` 列表合并 `_ca.build_omni_routes()` 的结果

**保留**:
- `_find_omni_client()` 辅助函数（gateway 负责"找实例"，console_api 负责"用实例"）

## 行为兼容性

| 端点 | schema 变化 |
|---|---|
| GET `/api/omni` | 不变 (`{stats, recent_logs}`) |
| GET `/api/omni/query` | 不变 (`{sectors, total, total_count, page, page_size}`) |
| POST `/api/omni/sync` | **新增 `record_count` 字段**（之前硬编码 0） |
| GET `/api/omni/sector-stocks` | 不变 |

**Board_sync CLI 兼容**: `python -m src.sync.board_sync --source fuyao` 仍可手动运行 — main() 改为调 `OmniClient.run_sync()`，CLI 路径与 HTTP 路径同源。

**source 白名单**: 从 gateway_server.py 移到 console_api.py `run_omni_sync()`。白名单保持 `{"all", "fuyao", "TDX", "tdxquant", "tqquant"}`。

## 风险与缓解

- **风险**: OmniClient.run_sync() 是异步长任务（最坏 5min）。如果中途网关重启，客户端会断连但 log_id 残留 status='running'。
    **缓解**: OmniClient.run_sync() 内部 `asyncio.wait_for(..., timeout=300)` 包裹，捕获 `TimeoutError` 后强制 UPDATE log 为 'failed'。**当前实际并发 sync ≤ 1**（前端 `_omniSyncInFlight` 守卫），多 session 场景下也只会有少量并发（≤ 3），不会撑爆事件循环。

- **风险**: `_OMNI_CLIENT` 是模块级全局状态，测试时多个 GatewayServer 实例会互相覆盖。
    **缓解**: console_api.py 沿用现有 `_GW_CFG` 全局模式，加 `_OMNI_CLIENT` 是同一套约定；后续如需测试隔离可改成 context-local。

## 测试计划

1. **手动 HTTP 验证**:
   - `GET /api/omni` → 返回 stats + logs
   - `GET /api/omni/query?source=tqquant&page=1` → 返回分页板块
   - `POST /api/omni/sync {"source": "fuyao"}` → 触发同步, 响应含 `record_count > 0`
   - `GET /api/omni/sector-stocks?sector_id=1` → 返回成分股列表

2. **日志验证**:
   - 触发一次同步, 查 `sector_sync_log`: 应只有 1 行（不是 2 行）

3. **CLI 验证**:
   - `python -m src.sync.board_sync --source fuyao` → 仍然能跑, 输出 JSON

4. **回归验证**:
   - console.html 板块查询 / 同步管理 / 数据统计 三 tab 全部正常
   - 暗色模式反馈颜色不变
   - 板块行点击成分股弹窗不变

## 明确不做

- 不动 `_omni_api` / `_omni_query_api` 的 raw SQL — 本次只搬位置（gateway → console_api），逻辑委托给 OmniClient 的新方法
- 不动 TDX sync 串行 await / FUYAO 0.3s sleep（用户已确认限流必要）
- 不动 console.html / console.js / console.css
- 不动 auth middleware / 健康检查 / 其他无关端点

## 验收

- [ ] gateway_server.py 不再 import sqlite3 / subprocess 用于 OMNI
- [ ] 4 个 OMNI Route 通过 `console_api.build_omni_routes()` 注册
- [ ] `OmniClient.run_sync()` 是 sync 逻辑唯一入口 (HTTP / CLI 共用)
- [ ] 双 log bug 修复: 一次同步只写 1 行 sector_sync_log
- [ ] `record_count` 字段真实反映本次同步写入数
- [ ] 所有现有 HTTP 测试通过