# UniHive 网关 HTTP 化设计

**日期:** 2026-09-04
**状态:** 待实施
**目标:** 网关以 Streamable HTTP 为主对外提供服务，STDIO 作为备选

## 1. 背景

当前 `gateway_server.py` 仅支持 STDIO 传输（`run_stdio_async()`）。随着 MCP 协议演进和新客户端（如 Claude Desktop 新版、Cursor、各类 HTTP MCP 客户端）的普及，HTTP（特别是 Streamable HTTP）已成为主流传输方式。

**目标:** 单进程 `console_server` 在端口 18080 同时托管 MCP Streamable HTTP 接口、管理 API、控制台 HTML。STDIO 模式保留作为备选。

## 2. 设计决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| HTTP 传输协议 | Streamable HTTP | MCP 2025 规范主推，FastMCP 4.0 原生支持 |
| HTTP 端口 | 18080 | 与管理控制台合并 |
| 部署模型 | 单进程（console_server 内嵌 gateway） | 控制台和网关本就配套使用 |
| Web 框架 | FastAPI + uvicorn | FastMCP `http_app()` 返回 ASGI app，可挂载 |
| MCP HTTP 路径 | `/mcp` | MCP 官方推荐路径 |
| STDIO 备选入口 | `python -m src.gateway_server --stdio` | 保持兼容现有 Claude Desktop 等 stdio 客户端 |

## 3. 架构

### 之前
```
进程 A: console_server.py (端口 18080, http.server, 管理 API + 静态 HTML)
进程 B: gateway_server.py (stdio, FastMCP)
```

### 之后
```
进程 A: console_server.py (端口 18080, FastAPI + uvicorn)
   ├─ /mcp           → FastMCP http_app() (Streamable HTTP, 内嵌 GatewayServer)
   ├─ /api/status    → 管理 API（保留）
   ├─ /api/interfaces → 管理 API（保留）
   ├─ /api/config    → 管理 API（保留）
   └─ /              → console.html 静态文件

进程 B (备选): gateway_server.py --stdio (FastMCP stdio, 旧客户端)
```

## 4. 文件改动清单

### 修改

| 文件 | 改动 |
|------|------|
| `src/gateway_server.py` | 保留 stdio，新增 `run_http_async()`；`async_main()` 支持 `--stdio` / `--http` 参数 |
| `src/console_server.py` | 完全重写为 FastAPI；启动时内嵌 GatewayServer，挂载 `mcp.http_app()`；保留管理 API |
| `scripts/start_console.ps1` | 几乎不变（继续启动 console_server） |
| `scripts/start_gateway.ps1` | 改为 STDIO 备选模式提示（明确告知用户推荐用控制台 HTTP） |
| `console.html` | 接入指南 "2. 接入方式" 章节改写：HTTP 模式为主，STDIO 模式为备 |
| `pyproject.toml` | 新增 `fastapi>=0.110` 和 `uvicorn[standard]>=0.27` 依赖 |

### 新增

| 文件 | 改动 |
|------|------|
| （无） | 不引入新文件 |

## 5. 关键代码模式

### gateway_server.py 新增 HTTP 启动

```python
async def serve_http(host: str = "127.0.0.1", port: int = 18080, mount_path: str = "/mcp"):
    """以 Streamable HTTP 模式启动 gateway（被 console_server 内部调用）"""
    server = GatewayServer()
    await server.initialize()
    app = server.mcp.http_app(path=mount_path)
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()
```

### console_server.py FastAPI 重构

```python
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

app = FastAPI(title="UniHive Console")

# 启动时初始化 GatewayServer 并挂载其 MCP ASGI app
gateway: GatewayServer | None = None

@app.on_event("startup")
async def startup():
    global gateway
    gateway = GatewayServer()
    await gateway.initialize()
    # Mount FastMCP ASGI app at /mcp
    mcp_app = gateway.mcp.http_app(path="/mcp")
    app.mount("/mcp", mcp_app)

# 管理 API（保留现有逻辑）
@app.get("/api/status")
async def api_status(): ...

@app.get("/api/interfaces")
async def api_interfaces(): ...

@app.get("/api/config")
async def api_config(): ...

# 静态文件
@app.get("/")
async def root():
    return FileResponse("console.html")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18080)
```

## 6. 接入指南更新内容（console.html 中 "2. 接入方式"）

### 2.1 HTTP 模式（推荐）

控制台默认以 Streamable HTTP 模式启动。只需启动 `start_console.ps1`，MCP 接口在 `http://127.0.0.1:18080/mcp` 提供。

**Claude Desktop / Cursor 配置：**

```json
{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}
```

**Python SDK 示例：**

```python
from fastmcp.client import Client

async with Client("http://127.0.0.1:18080/mcp") as client:
    tools = await client.list_tools()
    result = await client.call_tool("get_realtime_quote", {"symbol": "600000"})
```

### 2.2 STDIO 模式（备选）

用于不支持 HTTP MCP 的旧客户端，或本地脚本场景：

```json
{
  "mcpServers": {
    "unihive": {
      "command": "python",
      "args": ["-m", "src.gateway_server", "--stdio"],
      "cwd": "D:/fintech_workspace/unihive"
    }
  }
}
```

## 7. 错误处理

| 场景 | 表现 |
|------|------|
| 启动时上游连接失败 | GatewayServer.initialize() 仍启动（标记为不可用），/mcp 可正常调用其他上游 |
| 端口 18080 被占用 | uvicorn 启动失败，记录日志，进程退出 |
| /mcp 路径收到非 MCP 请求 | FastMCP 自动返回 4xx，不影响其他路由 |
| console.html 不存在 | 根路径返回 404 |

## 8. 测试策略

由于这是底层架构变更，验证通过手动测试 + 集成测试：

1. **HTTP MCP 验证（核心）：**
   - 启动 `start_console.ps1`
   - 用 curl POST 到 `/mcp` 调用 `initialize` 方法
   - 用 curl POST 调用一个工具（如 `get_server_status`）
   - 验证返回 JSON 符合 MCP 规范

2. **管理 API 兼容性：**
   - curl `/api/status`、`/api/interfaces`、`/api/config` 验证返回值与之前一致

3. **静态 HTML：**
   - curl `/` 验证返回 console.html

4. **STDIO 备选：**
   - 单独运行 `python -m src.gateway_server --stdio`
   - 用 stdin 发送 MCP initialize 请求，验证响应

5. **接入指南文档：**
   - 浏览器访问控制台，验证 "2. 接入方式" 章节代码示例可复制

## 9. 风险

| 风险 | 缓解 |
|------|------|
| 单进程耦合：控制台崩 = 网关崩 | 接受；两者本配套；后续可拆 |
| uvicorn 新依赖 | pyproject.toml 显式声明 |
| console_server 重写风险大 | 分阶段：先重构 console，验证 /api/* 后再加 /mcp |
| 现有 /api/interfaces 可能受 tools list 变化影响 | 复用 GatewayServer 的 tools 数据源 |
| 文档/代码一致性 | console.html 接入指南和实际配置必须严格对应 |

## 10. 验收清单

- [ ] `pip install -e .` 安装成功，fastapi/uvicorn 在依赖中
- [ ] `start_console.ps1` 启动后，18080 端口可访问
- [ ] `curl http://127.0.0.1:18080/api/status` 返回 200 + 正常 JSON
- [ ] `curl http://127.0.0.1:18080/api/interfaces` 返回 200 + 工具列表
- [ ] `curl http://127.0.0.1:18080/api/config` 返回 200 + 配置 JSON
- [ ] `curl http://127.0.0.1:18080/` 返回 console.html 内容
- [ ] `curl -X POST http://127.0.0.1:18080/mcp -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"initialize",...}'` 返回 MCP initialize 响应
- [ ] `python -m src.gateway_server --stdio` 仍可独立以 STDIO 模式启动
- [ ] console.html "接入指南" 标签页 "2.1 HTTP 模式" 示例与实际 URL 一致
- [ ] console.html "2.2 STDIO 模式" 示例可复制粘贴运行

## 11. 不做的事

- 不做反向代理（nginx 等）单端口方案
- 不做 HTTPS（仅 localhost 开发场景）
- 不做认证（仅本机访问）
- 不做负载均衡/多实例
- 不拆分 GatewayServer 和 console_server 进程
