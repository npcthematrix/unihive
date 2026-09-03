# UniHive 网关 HTTP 化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 UniHive 网关改造成以 Streamable HTTP 为主（端口 18080，路径 `/mcp`），STDIO 模式保留作为备选入口。

**Architecture:** 单进程 `console_server.py` 用 FastAPI + uvicorn 同时托管 MCP HTTP（FastMCP `http_app()` 挂载到 `/mcp`）、管理 API（`/api/*`）、静态 HTML（`/`）。`gateway_server.py` 新增 HTTP 启动能力，STDIO 路径完全保留。

**Tech Stack:** FastAPI 0.110+, uvicorn 0.27+, FastMCP 4.0（已有）

---

## 文件改动清单

```
修改：
  src/gateway_server.py        — 新增 HTTP transport 支持
  src/console_server.py        — 完全重写为 FastAPI
  scripts/start_console.ps1    — 微调（提示默认 HTTP 模式）
  scripts/start_gateway.ps1    — 改为 STDIO 备选启动
  console.html                 — 接入指南 "2. 接入方式" 章节改写
  pyproject.toml               — 新增 fastapi / uvicorn 依赖
```

---

## Task 1: 添加依赖与基础结构验证

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 检查 pyproject.toml 当前依赖**

读取 `pyproject.toml`，在 `[project]` 块的 `dependencies` 列表中找到合适位置添加：

```toml
    "fastmcp>=4.0.0",
    "sqlalchemy>=2.0.0",
    "aiosqlite>=0.19.0",
```

（实际格式以文件现状为准）

- [ ] **Step 2: 添加 fastapi 和 uvicorn**

在 `dependencies` 中新增两行（保持字母/字母+数字顺序或现有风格）：

```toml
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
```

- [ ] **Step 3: 安装新依赖**

```bash
cd /d/fintech_workspace/unihive
pip install -e .
```

预期：pip 成功安装 fastapi 和 uvicorn，无冲突。

- [ ] **Step 4: 验证导入**

```bash
python -c "import fastapi, uvicorn; print(fastapi.__version__, uvicorn.__version__)"
```

预期：输出版本号，无 ImportError。

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml
git commit -m "chore(deps): add fastapi and uvicorn for HTTP gateway"
```

---

## Task 2: gateway_server.py 新增 HTTP transport 支持

**Files:**
- Modify: `src/gateway_server.py`

- [ ] **Step 1: 阅读当前文件确认 start() 方法**

确认 `start()` 方法在 line 222-226：

```python
    async def start(self):
        self._running = True
        await self.initialize()
        asyncio.create_task(self._health_check_loop())
        await self.mcp.run_stdio_async()
```

- [ ] **Step 2: 新增 HTTP 服务方法**

在 `start()` 方法之后插入新方法 `serve_http()`：

```python
    async def serve_http(self, host: str = "127.0.0.1", port: int = 18080, mount_path: str = "/mcp"):
        """以 Streamable HTTP 模式启动 gateway（被 console_server 内部调用）"""
        self._running = True
        await self.initialize()
        asyncio.create_task(self._health_check_loop())
        mcp_app = self.mcp.http_app(path=mount_path)
        import uvicorn
        config = uvicorn.Config(mcp_app, host=host, port=port, log_level="info")
        await uvicorn.Server(config).serve()
```

- [ ] **Step 3: 修改 async_main 支持 --stdio / --http 参数**

把现有的 `async_main`：

```python
async def async_main():
    import os
    if sys.platform == "win32":
        os.environ["PYTHONIOENCODING"] = "utf-8"

    server = GatewayServer()
    await server.start()
```

替换为：

```python
async def async_main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 18080):
    import os
    if sys.platform == "win32":
        os.environ["PYTHONIOENCODING"] = "utf-8"

    server = GatewayServer()
    if transport == "stdio":
        await server.start()
    elif transport == "http":
        await server.serve_http(host=host, port=port)
    else:
        raise ValueError(f"Unknown transport: {transport}")
```

- [ ] **Step 4: 修改 main 解析命令行参数**

把现有的 `main`：

```python
def main():
    asyncio.run(async_main())
```

替换为：

```python
def main():
    import argparse
    parser = argparse.ArgumentParser(description="UniHive MCP Gateway")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="Transport mode (default: stdio). 'http' for Streamable HTTP server.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (http mode)")
    parser.add_argument("--port", type=int, default=18080, help="HTTP bind port (http mode)")
    args = parser.parse_args()
    asyncio.run(async_main(transport=args.transport, host=args.host, port=args.port))
```

- [ ] **Step 5: 验证 STDIO 模式仍可独立启动**

```bash
cd /d/fintech_workspace/unihive
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | python -m src.gateway_server --stdio 2>&1 | head -c 500
```

预期：看到 MCP initialize 响应（包含 serverInfo、capabilities 等字段）。

按 Ctrl+C 退出（如果响应后没自动退出）。

- [ ] **Step 6: 验证 HTTP 模式可启动**

```bash
cd /d/fintech_workspace/unihive
timeout 5 python -m src.gateway_server --http --port 18090 2>&1 | head -c 500 || true
```

预期：日志显示 "Uvicorn running on http://127.0.0.1:18090"，5 秒后被 timeout 杀掉。

- [ ] **Step 7: 提交**

```bash
git add src/gateway_server.py
git commit -m "feat(gateway): add Streamable HTTP transport via FastMCP http_app"
```

---

## Task 3: console_server.py 完全重写为 FastAPI

**Files:**
- Modify: `src/console_server.py`

- [ ] **Step 1: 阅读现有 console_server.py 全文**

确认现有 `/api/status`、`/api/interfaces`、`/api/config` 的返回结构（特别是数据来源 — 直读 yaml/db 还是调用 GatewayServer）。**重要的：保留完全相同的返回结构。**

- [ ] **Step 2: 用 FastAPI 重写整个文件**

把 `src/console_server.py` 整体替换为以下内容（已合并原管理 API + FastMCP 挂载）：

```python
"""
UniHive Console Server - 统一入口
单进程同时托管：
  - MCP Streamable HTTP 接口（/mcp）
  - 管理 API（/api/status, /api/interfaces, /api/config）
  - 控制台静态 HTML（/）
"""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import uvicorn
import yaml
from fastapi import FastAPI
from fastapi.responses import FileResponse

# 让 console_server 可作为 gateway 的内嵌组件
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gateway_server import GatewayServer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CONSOLE_HOST = "127.0.0.1"
CONSOLE_PORT = 18080
MCP_PATH = "/mcp"
GATEWAY_CONFIG_PATH = "config/upstreams.yaml"
CACHE_DB_PATH = "logs/cache.db"
CONSOLE_HTML_PATH = "console.html"


def mask_sensitive(value: str, show_chars: int = 2) -> str:
    if not value or len(value) <= show_chars * 2:
        return "***"
    return value[:show_chars] + "***" + value[-show_chars:]


def load_config() -> dict:
    config_path = Path(GATEWAY_CONFIG_PATH)
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    for name, upstream in config.get("upstreams", {}).items():
        if "api_key" in upstream and upstream["api_key"]:
            upstream["api_key"] = mask_sensitive(upstream["api_key"])
        if "env" in upstream:
            for key, val in upstream["env"].items():
                if any(x in key.upper() for x in ["TOKEN", "KEY", "SECRET"]):
                    upstream["env"][key] = mask_sensitive(val)
    return config


def get_cache_stats() -> dict:
    cache_path = Path(CACHE_DB_PATH)
    if not cache_path.exists():
        return {"enabled": False, "error": "Cache DB not found", "timestamp": int(time.time())}
    try:
        size_bytes = cache_path.stat().st_size
        entries = None
        hit_rate = None
        try:
            import sqlite3
            conn = sqlite3.connect(CACHE_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM cache_entries")
            entries = cursor.fetchone()[0]
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cache_stats'")
            if cursor.fetchone():
                cursor.execute("SELECT hits, misses FROM cache_stats ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                if row and row[1] and (row[0] + row[1]) > 0:
                    hit_rate = row[0] / (row[0] + row[1])
            conn.close()
        except Exception:
            pass
        return {
            "enabled": True,
            "entries": entries,
            "db_size_mb": round(size_bytes / (1024 * 1024), 3),
            "hit_rate": hit_rate,
            "timestamp": int(time.time()),
        }
    except Exception as e:
        return {"enabled": False, "error": str(e), "timestamp": int(time.time())}


def get_upstream_status() -> dict:
    config = load_config()
    upstreams_status = {}
    for name, cfg in config.get("upstreams", {}).items():
        upstreams_status[name] = {
            "enabled": cfg.get("enabled", False),
            "type": cfg.get("type", "-"),
        }
    return {
        "timestamp": int(time.time()),
        "upstreams": upstreams_status,
        "cache": get_cache_stats(),
    }


def get_interfaces() -> dict:
    """从 gateway 的 tools spec 列表获取工具元数据（保持原 /api/interfaces 结构）"""
    # 复用 gateway 进程内的注册信息；如果不可用，降级为读 config tools 列表
    global _gateway_state
    if _gateway_state and _gateway_state.get("specs"):
        tools = []
        for spec in _gateway_state["specs"]:
            tools.append({
                "name": spec.get("name"),
                "description": spec.get("description", ""),
                "source": spec.get("source", spec.get("upstream", "")),
                "params": [p.get("name") if isinstance(p, dict) else p for p in spec.get("params", [])],
                "dangerous": spec.get("dangerous", False),
                "cache_ttl_key": spec.get("cache_ttl_key"),
            })
    else:
        config = load_config()
        tools = []
        for spec in config.get("tools", []):
            tools.append({
                "name": spec.get("name"),
                "description": spec.get("description", ""),
                "source": spec.get("source", spec.get("upstream", "")),
                "params": [p.get("name") if isinstance(p, dict) else p for p in spec.get("params", [])],
                "dangerous": spec.get("dangerous", False),
                "cache_ttl_key": spec.get("cache_ttl_key"),
            })
    return {"timestamp": int(time.time()), "tools": tools}


# 全局 gateway 状态（console_server 启动时由 lifespan 填充）
_gateway_state: dict = {}


def create_app() -> FastAPI:
    app = FastAPI(title="UniHive Console", version="1.0.0")

    @app.on_event("startup")
    async def on_startup():
        """启动时初始化 GatewayServer 并挂载其 MCP ASGI app"""
        global _gateway_state
        logger.info("Initializing gateway for HTTP mode...")
        gateway = GatewayServer()
        await gateway.initialize()
        mcp_app = gateway.mcp.http_app(path=MCP_PATH)
        app.mount(MCP_PATH, mcp_app)
        _gateway_state = {
            "gateway": gateway,
            "specs": gateway.config.get("tools", []),
        }
        # 后台 health check
        asyncio.create_task(gateway._health_check_loop())
        logger.info(f"Gateway mounted at {MCP_PATH}")

    @app.on_event("shutdown")
    async def on_shutdown():
        global _gateway_state
        gw = _gateway_state.get("gateway")
        if gw:
            await gw.stop()

    @app.get("/")
    async def root():
        if Path(CONSOLE_HTML_PATH).exists():
            return FileResponse(CONSOLE_HTML_PATH, media_type="text/html")
        return {"error": "console.html not found"}

    @app.get("/api/status")
    async def api_status():
        return get_upstream_status()

    @app.get("/api/interfaces")
    async def api_interfaces():
        return get_interfaces()

    @app.get("/api/config")
    async def api_config():
        return load_config()

    @app.get("/health")
    async def health():
        return {"status": "ok", "timestamp": int(time.time())}

    return app


app = create_app()


def main():
    if sys.platform == "win32":
        os.environ["PYTHONIOENCODING"] = "utf-8"
    logger.info(f"Starting UniHive console on http://{CONSOLE_HOST}:{CONSOLE_PORT}")
    uvicorn.run(app, host=CONSOLE_HOST, port=CONSOLE_PORT, log_level="info")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 停掉旧 console_server 进程**

```bash
# 找 PID 并杀掉（如果还在跑）
netstat -ano | grep 18080 | grep LISTENING
# 杀掉对应 PID
```

- [ ] **Step 4: 启动新 console_server**

```bash
cd /d/fintech_workspace/unihive
python -m src.console_server 2>&1 &
SERVER_PID=$!
sleep 5
echo "Server PID: $SERVER_PID"
```

预期：日志显示 "Starting UniHive console on http://127.0.0.1:18080" 和 "Gateway mounted at /mcp"。

- [ ] **Step 5: 验证管理 API**

```bash
curl -s http://127.0.0.1:18080/api/status | head -c 200
curl -s http://127.0.0.1:18080/api/interfaces | python -c "import json,sys; d=json.load(sys.stdin); print(f\"tools: {len(d['tools'])}\")"
curl -s http://127.0.0.1:18080/api/config | head -c 100
curl -s -o /dev/null -w "HTML: %{http_code}\n" http://127.0.0.1:18080/
```

预期：所有 API 返回 200，HTML 返回 200，工具数与之前一致（约 87）。

- [ ] **Step 6: 验证 MCP /mcp 端点**

```bash
curl -s -X POST http://127.0.0.1:18080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl-test","version":"1.0"}}}' | head -c 500
```

预期：返回 MCP initialize 响应，包含 `result.serverInfo.name: "unihive"` 和 `result.capabilities`。

- [ ] **Step 7: 杀掉测试进程**

```bash
kill $SERVER_PID 2>/dev/null || true
# 或通过 netstat 找 PID
```

- [ ] **Step 8: 提交**

```bash
git add src/console_server.py
git commit -m "refactor(console): rewrite as FastAPI with embedded MCP HTTP gateway"
```

---

## Task 4: 更新接入指南的 "2. 接入方式" 章节

**Files:**
- Modify: `console.html`

- [ ] **Step 1: 定位当前 "2. 接入方式" 章节**

在 `console.html` 中找到：

```html
                    <h3 id="sec-integration">2. 接入方式</h3>

                    <h4>2.1 Stdio 模式（Claude Desktop / Cursor）</h4>
                    <p>在客户端的 MCP 配置中添加：</p>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>{
  "mcpServers": {
    "unihive": {
      "command": "python",
      "args": ["-m", "mcp_data_gateway.gateway_server"],
      "cwd": "D:/fintech_workspace/unihive"
    }
  }
}</pre>
                    </div>

                    <h4>2.2 HTTP 模式（自定义客户端）</h4>
                    <p>控制台 HTTP 服务暴露了接口元信息查询端点，可用于发现可用工具：</p>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>curl http://127.0.0.1:18080/api/interfaces</pre>
                    </div>
```

- [ ] **Step 2: 替换为新的章节（HTTP 为主，STDIO 为备）**

把上述整段替换为：

```html
                    <h3 id="sec-integration">2. 接入方式</h3>

                    <h4>2.1 HTTP 模式（推荐）</h4>
                    <p>网关以 <strong>Streamable HTTP</strong> 模式对外提供服务，默认监听 <code>http://127.0.0.1:18080/mcp</code>。只需运行 <code>.\scripts\start_console.ps1</code> 启动控制台，网关即同时可用。</p>

                    <p><strong>Claude Desktop / Cursor 配置：</strong></p>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>{
  "mcpServers": {
    "unihive": {
      "url": "http://127.0.0.1:18080/mcp"
    }
  }
}</pre>
                    </div>

                    <p><strong>Python SDK 示例：</strong></p>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>import asyncio
from fastmcp.client import Client

async def main():
    async with Client("http://127.0.0.1:18080/mcp") as client:
        tools = await client.list_tools()
        result = await client.call_tool("get_realtime_quote", {"symbol": "600000"})
        print(result.data)

asyncio.run(main())</pre>
                    </div>

                    <h4>2.2 STDIO 模式（备选）</h4>
                    <p>用于不支持 HTTP MCP 的旧客户端，或本地脚本场景。需要单独启动 gateway 进程：</p>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>{
  "mcpServers": {
    "unihive": {
      "command": "python",
      "args": ["-m", "src.gateway_server", "--stdio"],
      "cwd": "D:/fintech_workspace/unihive"
    }
  }
}</pre>
                    </div>

                    <p>或命令行单独启动：</p>
                    <div class="code-block">
                        <button class="copy-btn">复制</button>
                        <pre>python -m src.gateway_server --stdio</pre>
                    </div>
```

- [ ] **Step 3: 验证控制台 HTML 仍可加载**

```bash
cd /d/fintech_workspace/unihive
python -m src.console_server 2>&1 &
SERVER_PID=$!
sleep 4
curl -s http://127.0.0.1:18080/ | grep -c "2.1 HTTP 模式"
kill $SERVER_PID 2>/dev/null || true
```

预期：grep 返回 1（找到了新章节）。

- [ ] **Step 4: 提交**

```bash
git add console.html
git commit -m "docs(console): update onboarding guide with HTTP-first, STDIO fallback"
```

---

## Task 5: 更新启动脚本

**Files:**
- Modify: `scripts/start_console.ps1`
- Modify: `scripts/start_gateway.ps1`

- [ ] **Step 1: 修改 start_console.ps1 的提示语**

定位到：

```powershell
Write-Host "Starting console server on http://127.0.0.1:18080" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
```

替换为：

```powershell
Write-Host "Starting console + HTTP gateway on http://127.0.0.1:18080" -ForegroundColor Green
Write-Host "  - 控制台 HTML:        http://127.0.0.1:18080/" -ForegroundColor Cyan
Write-Host "  - MCP Streamable HTTP: http://127.0.0.1:18080/mcp" -ForegroundColor Cyan
Write-Host "  - 管理 API:            http://127.0.0.1:18080/api/*" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
```

- [ ] **Step 2: 修改 start_gateway.ps1 的提示语**

定位到：

```powershell
Write-Host "Starting UniHive gateway (stdio mode)..." -ForegroundColor Green
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
```

替换为：

```powershell
Write-Host "Starting UniHive gateway (STDIO mode - 备选模式)..." -ForegroundColor Green
Write-Host "推荐使用 start_console.ps1 以 HTTP 模式启动网关" -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
```

- [ ] **Step 3: 验证脚本可执行（dry-run）**

```bash
cd /d/fintech_workspace/unihive
cat scripts/start_console.ps1 | grep "MCP Streamable"
cat scripts/start_gateway.ps1 | grep "备选"
```

预期：两个 grep 都能找到对应字符串。

- [ ] **Step 4: 提交**

```bash
git add scripts/start_console.ps1 scripts/start_gateway.ps1
git commit -m "docs(scripts): clarify console is HTTP-primary, gateway is stdio fallback"
```

---

## Task 6: 最终验收

**Files:**
- 无（验证）

- [ ] **Step 1: 启动服务**

```bash
cd /d/fintech_workspace/unihive
python -m src.console_server 2>&1 &
SERVER_PID=$!
sleep 5
echo "PID: $SERVER_PID"
```

预期：日志显示 console 启动 + "Gateway mounted at /mcp"。

- [ ] **Step 2: 验证 4 个核心端点**

```bash
echo "=== / ==="
curl -s -o /dev/null -w "HTTP=%{http_code}\n" http://127.0.0.1:18080/

echo "=== /api/status ==="
curl -s http://127.0.0.1:18080/api/status | python -c "import json,sys; d=json.load(sys.stdin); print(f\"upstreams: {len(d['upstreams'])}, cache.enabled: {d['cache']['enabled']}\")"

echo "=== /api/interfaces ==="
curl -s http://127.0.0.1:18080/api/interfaces | python -c "import json,sys; d=json.load(sys.stdin); print(f\"tools: {len(d['tools'])}\")"

echo "=== /api/config ==="
curl -s -o /dev/null -w "HTTP=%{http_code}\n" http://127.0.0.1:18080/api/config

echo "=== /mcp (initialize) ==="
curl -s -X POST http://127.0.0.1:18080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"verify","version":"1.0"}}}' | head -c 200
```

预期：
- / → 200
- /api/status → upstreams 数量正确（6），cache.enabled = True
- /api/interfaces → tools 数量正确（约 87）
- /api/config → 200
- /mcp → 返回含 serverInfo 的 JSON-RPC 响应

- [ ] **Step 3: 验证 STDIO 备选**

```bash
cd /d/fintech_workspace/unihive
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"stdio-test","version":"1.0"}}}' | timeout 5 python -m src.gateway_server --stdio 2>&1 | head -c 500
```

预期：返回 MCP initialize 响应。

- [ ] **Step 4: 验证接入指南浏览器展示**

浏览器打开 http://127.0.0.1:18080/console.html → "接入指南" → "2. 接入方式" 章节：
- 应看到 "2.1 HTTP 模式（推荐）" 在前
- 应看到 "2.2 STDIO 模式（备选）" 在后
- 代码示例可复制

- [ ] **Step 5: 清理测试进程**

```bash
kill $SERVER_PID 2>/dev/null || true
# 若失败：
netstat -ano | grep 18080 | grep LISTENING
```

---

## 自审结果

**1. Spec 覆盖度:**
- HTTP transport ✓ (Task 2)
- 端口 18080 + 合并 ✓ (Task 3)
- FastAPI/Starlette ✓ (Task 3)
- /mcp 路径 ✓ (Task 3)
- STDIO 备选 ✓ (Task 2, Task 4)
- 接入指南更新 ✓ (Task 4)
- 启动脚本更新 ✓ (Task 5)
- 验收清单 ✓ (Task 6)

**2. 占位符扫描:** 无 TBD/TODO/"implement later"。

**3. 类型/命名一致性:**
- `serve_http()` 在 Task 2 定义
- `MCP_PATH = "/mcp"` 在 Task 3 常量定义和使用
- `_gateway_state` 全局 dict 在 Task 3 唯一定义
- console.html 章节标题：`2.1 HTTP 模式（推荐）`、`2.2 STDIO 模式（备选）` 在 Task 4 文档化
