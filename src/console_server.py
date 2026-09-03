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

# 全局 gateway 状态（在 lifespan 中填充）
_gateway_state: dict = {}


def mask_sensitive(value: str, show_chars: int = 2) -> str:
    if not value or len(value) <= show_chars * 2:
        return "***"
    return value[:show_chars] + "***" + value[-show_chars:]


def load_config() -> dict:
    config_path = Path(GATEWAY_CONFIG_PATH)
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    for name, upstream in config.get("upstreams", {}).items():
        if isinstance(upstream, dict):
            if "api_key" in upstream and upstream["api_key"]:
                upstream["api_key"] = mask_sensitive(upstream["api_key"])
            if "env" in upstream and isinstance(upstream["env"], dict):
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
        if not isinstance(cfg, dict):
            continue
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
    """从 gateway 注册的 specs 拉工具元数据；fallback 到读 config"""
    if _gateway_state.get("specs"):
        tools = []
        for spec in _gateway_state["specs"]:
            tools.append({
                "name": spec.get("name"),
                "description": spec.get("description", ""),
                "source": spec.get("source") or spec.get("upstream", ""),
                "dangerous": bool(spec.get("dangerous", False)),
                "cache_ttl_key": spec.get("cache_ttl_key"),
                "params": [
                    p.get("name") if isinstance(p, dict) else p
                    for p in (spec.get("params") or [])
                ],
            })
    else:
        config = load_config()
        tools = []
        for spec in config.get("tools", []) or []:
            tools.append({
                "name": spec.get("name"),
                "description": spec.get("description", ""),
                "source": spec.get("source") or spec.get("upstream", ""),
                "dangerous": bool(spec.get("dangerous", False)),
                "cache_ttl_key": spec.get("cache_ttl_key"),
                "params": [
                    p.get("name") if isinstance(p, dict) else p
                    for p in (spec.get("params") or [])
                ],
            })
    return {"timestamp": int(time.time()), "tools": tools}


def create_app() -> FastAPI:
    # 延迟 import 以避免在 create_app 时强依赖 gateway 模块
    from src.gateway_server import GatewayServer
    from fastmcp import FastMCP

    global _gateway_state
    logger.info("Initializing gateway for HTTP mode...")
    gateway = GatewayServer()
    # 预创建 FastMCP 实例，注册 tools（同步阻塞调用以保证 lifespan 一致）
    # 这样构造 http_app 时 session manager 已被 lifespan 正确初始化。
    import asyncio as _asyncio
    gateway.mcp = FastMCP("unihive")
    _asyncio.run(gateway.initialize())
    mcp_app = gateway.mcp.http_app(path="/mcp")
    _gateway_state = {
        "gateway": gateway,
        "specs": gateway.config.get("tools", []) or [],
    }

    # 把 MCP 的 lifespan 透传给 FastAPI，确保 StreamableHTTP session manager
    # 在 ASGI lifespan 阶段被正确初始化。
    app = FastAPI(title="UniHive Console", version="1.0.0", lifespan=mcp_app.lifespan)

    # FastMCP 内部路由固定为 /mcp。Starlette Mount 会剥离 /mcp 前缀，
    # 因此 mount 到 /mcp 后完整访问路径为 /mcp/mcp。
    # 在 module 级预先 mount（在添加任何 FastAPI route 之前），
    # 确保 mount 的匹配优先于 FastAPI 的兜底 404。
    app.mount(MCP_PATH, mcp_app)

    @app.on_event("startup")
    async def on_startup():
        """FastAPI 启动钩子：启动 health check 循环"""
        global _gateway_state
        gw = _gateway_state["gateway"]
        asyncio.create_task(gw._health_check_loop())
        logger.info(
            f"Gateway MCP endpoint at {MCP_PATH}/mcp "
            f"({len(_gateway_state['specs'])} tools)"
        )

    @app.on_event("shutdown")
    async def on_shutdown():
        gw = _gateway_state.get("gateway")
        if gw:
            await gw.stop()

    @app.get("/", include_in_schema=False)
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