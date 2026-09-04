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
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import httpx
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
    stats_path = Path("logs/cache_stats.json")
    if not cache_path.exists():
        return {"enabled": False, "error": "Cache DB not found", "timestamp": int(time.time())}
    try:
        size_bytes = cache_path.stat().st_size
        entries = None
        hits = None
        misses = None
        hit_rate = None
        try:
            import sqlite3
            conn = sqlite3.connect(CACHE_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM cache")
            entries = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM cache WHERE expires_at IS NOT NULL AND expires_at > ?", (int(time.time()),))
            live_entries = cursor.fetchone()[0]
            conn.close()
        except Exception:
            live_entries = None
        if stats_path.exists():
            try:
                with open(stats_path, encoding="utf-8") as f:
                    s = json.load(f)
                hits = int(s.get("hits", 0))
                misses = int(s.get("misses", 0))
                total = hits + misses
                hit_rate = (hits / total) if total > 0 else None
            except Exception:
                pass
        return {
            "enabled": True,
            "entries": entries,
            "live_entries": live_entries,
            "db_size_mb": round(size_bytes / (1024 * 1024), 3),
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "timestamp": int(time.time()),
        }
    except Exception as e:
        return {"enabled": False, "error": str(e), "timestamp": int(time.time())}


_probe_cache: dict[str, tuple[float, dict]] = {}
_PROBE_TTL_SECONDS = 5.0
_PROBE_ERROR_MAX_CHARS = 200


def _probe_upstream(name: str, cfg: dict) -> dict:
    """实际探测上游可用性和延迟，结果按名称缓存 5s。"""
    cached = _probe_cache.get(name)
    if cached is not None:
        ts, payload = cached
        if (time.monotonic() - ts) < _PROBE_TTL_SECONDS:
            return payload

    upstream_type = cfg.get("type", "")
    base_url = cfg.get("base_url", "")
    result = {
        "enabled": cfg.get("enabled", False),
        "type": upstream_type,
        "description": cfg.get("description", ""),
        "status": "unknown",
        "latency_ms": None,
        "last_error": None,
    }
    if upstream_type == "http" and base_url:
        health_url = base_url.rstrip("/") + "/health"
        try:
            t0 = time.monotonic()
            resp = httpx.get(health_url, timeout=5.0)
            result["latency_ms"] = round((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                result["status"] = "online"
            else:
                result["status"] = "degraded"
                result["last_error"] = f"HTTP {resp.status_code}"
        except httpx.TimeoutException:
            result["status"] = "offline"
            result["last_error"] = "连接超时 (5s)"
        except Exception as e:
            result["status"] = "offline"
            full = str(e)
            logger.warning(f"[{name}] upstream probe failed: {full}")
            result["last_error"] = full[:_PROBE_ERROR_MAX_CHARS]
    elif upstream_type == "http_jsonrpc" and base_url:
        rpc_url = base_url.rstrip("/") + "/"
        try:
            t0 = time.monotonic()
            resp = httpx.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "get_user_sector", "params": {}},
                timeout=5.0,
            )
            result["latency_ms"] = round((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                data: dict = {}
                try:
                    data = resp.json()
                except Exception:
                    pass
                rpc_err = data.get("error") if isinstance(data, dict) else None
                if rpc_err is not None:
                    result["status"] = "degraded"
                    msg = str(rpc_err)[:_PROBE_ERROR_MAX_CHARS]
                    result["last_error"] = f"RPC error: {msg}"
                else:
                    result["status"] = "online"
            else:
                result["status"] = "degraded"
                result["last_error"] = f"HTTP {resp.status_code}"
        except httpx.TimeoutException:
            result["status"] = "offline"
            result["last_error"] = "连接超时 (5s)"
        except Exception as e:
            result["status"] = "offline"
            full = str(e)
            logger.warning(f"[{name}] upstream probe failed: {full}")
            result["last_error"] = full[:_PROBE_ERROR_MAX_CHARS]
    else:
        result["status"] = "configured" if cfg.get("enabled") else "disabled"

    _probe_cache[name] = (time.monotonic(), result)
    return result


def get_upstream_status() -> dict:
    config = load_config()
    upstreams_status = {}
    summary = {"total": 0, "online": 0, "degraded": 0, "offline": 0, "disabled": 0, "configured": 0}
    for name, cfg in config.get("upstreams", {}).items():
        if not isinstance(cfg, dict):
            continue
        info = _probe_upstream(name, cfg)
        upstreams_status[name] = info
        summary["total"] += 1
        if not info.get("enabled", False):
            summary["disabled"] += 1
        else:
            bucket = info.get("status", "unknown")
            if bucket in summary:
                summary[bucket] += 1
            else:
                summary["configured"] += 1
    return {
        "timestamp": int(time.time()),
        "summary": summary,
        "upstreams": upstreams_status,
        "cache": get_cache_stats(),
    }


def _derive_source(spec: dict, upstream_tool_mapping: dict) -> str:
    routing = spec.get("routing") or spec.get("name")
    mapping = upstream_tool_mapping.get(routing) or {}
    sources = list(mapping.keys())
    if sources:
        return sources[0]
    return spec.get("source") or spec.get("upstream") or ""


def get_interfaces() -> dict:
    config_path = Path(GATEWAY_CONFIG_PATH)
    config = load_config()
    # Use tool_loader helpers for full data
    from .tool_loader import load_all_tools, get_cache_ttl, derive_routing_chain
    tools = load_all_tools(config_path, config)
    upstream_tool_mapping = config.get("upstream_tool_mapping", {}) or {}
    result = []
    for spec in tools:
        # routing_key → chain (what the router actually walks)
        routing_key = spec.get("routing")
        chain = derive_routing_chain(config, routing_key) if routing_key else []
        # Resolve TTL
        cache_ttl_key = spec.get("cache_ttl_key")
        cache_ttl_seconds = get_cache_ttl(config, cache_ttl_key) if cache_ttl_key else None
        # Full params with schema
        params = spec.get("params") or []
        result.append({
            "name": spec.get("name"),
            "description": spec.get("description", ""),
            "source": _derive_source(spec, upstream_tool_mapping),
            "dangerous": bool(spec.get("dangerous", False)),
            "cache_ttl_key": cache_ttl_key,
            "cache_ttl_seconds": cache_ttl_seconds,
            "routing": routing_key,
            "chain": chain,
            "params": params,
        })
    return {"timestamp": int(time.time()), "tools": result}


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
    server = ThreadingHTTPServer(("127.0.0.1", CONSOLE_PORT), ConsoleHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
