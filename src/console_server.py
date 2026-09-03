"""
UniHive Console Server - 轻量管理控制台后端
独立进程运行，提供只读 HTTP API

端口分配：
  - 18080: 本控制台（管理 API + 静态 HTML）
  - 18081: MCP Streamable HTTP 网关（由 start_gateway.ps1 或 start_all.ps1 单独启动）
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
GATEWAY_HTTP_PORT = 18081
MCP_URL = f"http://127.0.0.1:{GATEWAY_HTTP_PORT}/mcp"
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


class ConsoleHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.end_headers()
        self.wfile.write(html_bytes)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            html_path = Path(CONSOLE_HTML_PATH)
            if html_path.exists():
                self._send_html(html_path.read_bytes())
            else:
                self._send_json({"error": "console.html not found"}, 404)
        elif path == "/api/status":
            self._send_json(get_upstream_status())
        elif path == "/api/interfaces":
            self._send_json(get_interfaces())
        elif path == "/api/config":
            self._send_json(load_config())
        elif path == "/health":
            self._send_json({"status": "ok", "mcp_url": MCP_URL, "timestamp": int(time.time())})
        else:
            self._send_json({"error": "not found", "path": path}, 404)


def main():
    if sys.platform == "win32":
        os.environ["PYTHONIOENCODING"] = "utf-8"
    logger.info(f"Console listening on http://127.0.0.1:{CONSOLE_PORT}")
    logger.info(f"MCP HTTP gateway expected at {MCP_URL} (separate process)")
    server = HTTPServer(("127.0.0.1", CONSOLE_PORT), ConsoleHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
