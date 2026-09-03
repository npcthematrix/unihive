"""
UniHive Console Server - 轻量管理控制台后端
独立进程运行，提供只读 HTTP API
"""
import json
import logging
import os
import sys
import time
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CONSOLE_PORT = 18080
GATEWAY_CONFIG_PATH = "config/upstreams.yaml"
CACHE_DB_PATH = "logs/cache.db"


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
    """获取缓存统计信息"""
    cache_path = Path(CACHE_DB_PATH)
    if not cache_path.exists():
        return {"enabled": False, "error": "Cache DB not found", "timestamp": int(time.time())}
    try:
        size_bytes = cache_path.stat().st_size

        # 尝试从缓存数据库获取更多统计信息
        entries = None
        hit_rate = None
        try:
            import sqlite3
            conn = sqlite3.connect(CACHE_DB_PATH)
            cursor = conn.cursor()

            # 获取条目数
            cursor.execute("SELECT COUNT(*) FROM cache_entries")
            entries = cursor.fetchone()[0]

            # 计算命中率 (如果有 hits 和 misses 列)
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cache_stats'")
            if cursor.fetchone():
                cursor.execute("SELECT hits, misses FROM cache_stats ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                if row and row[1] and (row[0] + row[1]) > 0:
                    hit_rate = row[0] / (row[0] + row[1])

            conn.close()
        except Exception:
            pass

        result = {
            "enabled": True,
            "db_size_mb": round(size_bytes / 1024 / 1024, 2),
            "entries": entries,
            "hit_rate": hit_rate,
            "timestamp": int(time.time())
        }
        return result
    except Exception as e:
        return {"enabled": False, "error": str(e), "timestamp": int(time.time())}


def get_router_tools() -> list:
    """获取完整路由工具列表 - 从 config.tools 动态推导 + 内置工具"""
    config_path = Path(GATEWAY_CONFIG_PATH)
    if not config_path.exists():
        return []
    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        return []

    routing = config.get("routing", {}) or {}
    mapping = config.get("upstream_tool_mapping", {}) or {}
    tools: list[dict] = [
        {"name": "get_server_status", "description": "网关状态", "params": [], "source": "gateway"},
        {"name": "get_health", "description": "健康检查", "params": [], "source": "gateway"},
        {"name": "search_stock", "description": "股票搜索 (rhths_meta/TDX)", "params": ["keyword"], "source": "rhths_meta/tdx_local"},
    ]
    for spec in (config.get("tools") or []):
        name = spec.get("name", "?")
        params = [p.get("name", "?") for p in spec.get("params", [])]
        source = _source_for(name, routing, mapping)
        entry = {
            "name": name,
            "description": spec.get("description", ""),
            "params": params,
            "source": source,
        }
        if spec.get("cache_ttl_key"):
            entry["cache_ttl_key"] = spec["cache_ttl_key"]
        if spec.get("dangerous"):
            entry["dangerous"] = True
        tools.append(entry)
    return tools


def _source_for(name: str, routing: dict, mapping: dict) -> str:
    if name in routing:
        return "/".join(routing[name].get("chain", [])) or "-"
    if name in mapping:
        return "/".join(mapping[name].keys()) or "-"
    return "-"


class ConsoleHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(f"{self.client_address[0]} - {format % args}")

    def send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_GET(self):
        path = self.path.strip("/")
        if path == "" or path == "index.html" or path == "/":
            self.serve_html()
        elif path == "api/status":
            self.handle_status()
        elif path == "api/interfaces":
            self.handle_interfaces()
        elif path == "api/config":
            self.handle_config()
        elif path == "api/health":
            self.send_json({"status": "ok", "timestamp": int(time.time())})
        else:
            self.send_json({"error": "Not found"}, 404)

    def serve_html(self):
        html_path = Path("console.html")
        if html_path.exists():
            with open(html_path, encoding="utf-8") as f:
                html = f.read()
        else:
            html = "<html><body><h1>UniHive Console</h1><p>Run: python -m src.console_server</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def handle_status(self):
        config = load_config()
        upstreams = config.get("upstreams", {})
        status = {"timestamp": int(time.time()), "upstreams": {}}
        for name, cfg in upstreams.items():
            status["upstreams"][name] = {
                "enabled": cfg.get("enabled", False),
                "type": cfg.get("type", "unknown"),
                "status": "enabled" if cfg.get("enabled") else "disabled",
            }
        status["cache"] = get_cache_stats()
        self.send_json(status)

    def handle_interfaces(self):
        tools = get_router_tools()
        self.send_json({"timestamp": int(time.time()), "tools": tools})

    def handle_config(self):
        config = load_config()
        simplified = {
            "upstreams": {},
            "routing": config.get("routing", {}),
            "cache": config.get("cache", {}),
            "cache_stats": get_cache_stats(),
        }
        for name, cfg in config.get("upstreams", {}).items():
            simplified["upstreams"][name] = {
                "enabled": cfg.get("enabled", False),
                "type": cfg.get("type", ""),
                "timeout_seconds": cfg.get("timeout_seconds", 30),
            }
        self.send_json(simplified)


def run_server(port: int = CONSOLE_PORT):
    os.chdir(Path(__file__).parent.parent)
    addr = ("127.0.0.1", port)
    server = HTTPServer(addr, ConsoleHandler)
    logger.info(f"Console server started at http://127.0.0.1:{port}")
    logger.info("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else CONSOLE_PORT
    run_server(port)
