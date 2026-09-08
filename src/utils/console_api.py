"""
Console HTTP API — 纯函数，无 HTTP 框架依赖。
被 gateway_server 在 /api/* 路由下调用，也被原 console_server 直接 serve。
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import yaml
from cachetools import TTLCache

logger = logging.getLogger(__name__)

CONSOLE_HTML_PATH = "console.html"
GATEWAY_CONFIG_PATH = "config/upstreams.yaml"
CACHE_DB_PATH = "logs/cache.db"

# 从配置文件读取，避免硬编码
_GW_CFG: dict | None = None
_INJECTED: bool = False


def set_config(config: dict) -> None:
    """由 GatewayServer.__init__ 注入已加载的 config。

    注入后，_GW_CFG 会被标记为「外部传入」，不再被 _load_gateway_config 覆盖。
    这样 GatewayServer 和 console_api 共用同一份 config，避免双轨不一致。
    """
    global _GW_CFG, _INJECTED
    _GW_CFG = config
    _INJECTED = True


def _load_gateway_config() -> dict:
    global _GW_CFG
    if _INJECTED and _GW_CFG is not None:
        return _GW_CFG
    if _GW_CFG is None:
        try:
            from .config_loader import load_config as _load
            _GW_CFG = _load(Path(GATEWAY_CONFIG_PATH), strict_env=False) or {}
        except Exception:
            _GW_CFG = {}
    return _GW_CFG

def _gateway_port() -> int:
    return _load_gateway_config().get("gateway", {}).get("port", 18080)

def _gateway_host() -> str:
    return _load_gateway_config().get("gateway", {}).get("host", "127.0.0.1")


def gateway_http_port() -> int:
    """每次现取, 避免 set_config() 注入晚于模块级常量求值时的 stale 值。"""
    return _gateway_port()


def gateway_host() -> str:
    return _gateway_host()


def mcp_url() -> str:
    return f"http://{gateway_host()}:{gateway_http_port()}/mcp"

# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------

# 精确匹配（大小写不敏感）：字段名本身就是密钥词
_SECRET_EXACT: frozenset[str] = frozenset({
    "key", "token", "secret", "password", "credential",
    "apikey", "authorization", "auth",
})

# 后缀匹配（大小写不敏感）：字段名以 _key / _token 等收尾
_SECRET_SUFFIX = re.compile(r'_(key|token|secret|password|credential)s?$', re.IGNORECASE)


def _is_secret_field(name: str) -> bool:
    """判定字段名是否为密钥字段。

    使用精确匹配 + 后缀匹配, 避免子串匹配把 `keyword` / `token_count` /
    `tokenwave_tdx` 等无关字段误判为密钥。
    """
    if not name:
        return False
    if name.lower() in _SECRET_EXACT:
        return True
    return bool(_SECRET_SUFFIX.search(name))


def mask_sensitive(value: str, show_chars: int = 2) -> str:
    if not value or len(value) <= show_chars * 2:
        return "***"
    return value[:show_chars] + "***" + value[-show_chars:]


def _mask_config_secrets(node: Any) -> Any:
    """递归扫描 config 节点，对密钥类字段打码。原地修改。"""
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if isinstance(v, str) and _is_secret_field(k):
                node[k] = mask_sensitive(v)
            elif isinstance(v, (dict, list)):
                _mask_config_secrets(v)
    elif isinstance(node, list):
        for item in node:
            _mask_config_secrets(item)
    return node


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config() -> dict:
    """加载并脱敏 config。复用 config_loader 解析 ${VAR} 占位符.

    不缓存: tool_loader.load_all_tools 会回写 upstream_tool_mapping, 缓存会让
    写回跨调用泄漏到 /api/config 返回值。下游 get_interfaces 自己有 5s 缓存,
    这里没必要再缓存。
    """
    config_path = Path(GATEWAY_CONFIG_PATH)
    if not config_path.exists():
        return {}
    try:
        from .config_loader import load_config as _shared_load_config

        config = _shared_load_config(config_path, strict_env=False)

        # Merge config.yaml (gateway runtime config)
        config_yaml_path = config_path.parent / "config.yaml"
        if config_yaml_path.exists():
            try:
                config_yaml = _shared_load_config(str(config_yaml_path), strict_env=False)
                for key, value in config_yaml.items():
                    if key not in config:
                        config[key] = value
                    elif isinstance(config[key], dict) and isinstance(value, dict):
                        config[key].update(value)
                    else:
                        config[key] = value
            except Exception as e:
                logger.warning(f"Failed to load config.yaml: {e}")
    except Exception as e:
        logger.warning(f"Shared config loader failed ({e}), falling back to raw YAML")
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    return _mask_config_secrets({**config})


# ---------------------------------------------------------------------------
# Cache stats
# ---------------------------------------------------------------------------


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
            cursor.execute(
                "SELECT COUNT(*) FROM cache WHERE expires_at IS NOT NULL AND expires_at > ?",
                (int(time.time()),),
            )
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


# ---------------------------------------------------------------------------
# Upstream probing
# ---------------------------------------------------------------------------

_probe_cache: TTLCache = TTLCache(maxsize=256, ttl=5.0)
_PROBE_ERROR_MAX_CHARS = 200
_PROBE_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0)


def _probe_upstream(name: str, cfg: dict) -> dict:
    upstream_type = cfg.get("type", "")
    base_url = cfg.get("base_url", "")
    cache_key = (name, upstream_type, base_url)

    payload = _probe_cache.get(cache_key)
    if payload is not None:
        return payload

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
            resp = httpx.get(health_url, timeout=_PROBE_TIMEOUT)
            result["latency_ms"] = round((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                result["status"] = "online"
            else:
                result["status"] = "degraded"
        except httpx.TimeoutException:
            result["status"] = "offline"
            result["last_error"] = f"连接超时 ({_PROBE_TIMEOUT.connect}s connect)"
        except Exception as e:
            result["status"] = "offline"
            result["last_error"] = str(e)[:_PROBE_ERROR_MAX_CHARS]
    elif upstream_type == "http_jsonrpc" and base_url:
        try:
            t0 = time.monotonic()
            resp = httpx.post(base_url, json={"jsonrpc": "2.0", "method": "server.info", "id": 1}, timeout=_PROBE_TIMEOUT)
            result["latency_ms"] = round((time.monotonic() - t0) * 1000)
            data: dict = {}
            try:
                data = resp.json()
            except Exception:
                pass
            rpc_err = data.get("error") if isinstance(data, dict) else None
            if rpc_err is not None:
                result["status"] = "degraded"
                result["last_error"] = f"RPC error: {str(rpc_err)[:_PROBE_ERROR_MAX_CHARS]}"
            else:
                result["status"] = "online"
        except httpx.TimeoutException:
            result["status"] = "offline"
            result["last_error"] = f"连接超时 ({_PROBE_TIMEOUT.connect}s connect)"
        except Exception as e:
            result["status"] = "offline"
            result["last_error"] = str(e)[:_PROBE_ERROR_MAX_CHARS]
    else:
        result["status"] = "configured" if cfg.get("enabled") else "disabled"

    _probe_cache[cache_key] = result
    return result


_probe_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="upstream-probe")


def _shutdown_probe_executor() -> None:
    """进程退出时关掉 probe executor, 避免 worker 线程泄漏。"""
    try:
        _probe_executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


atexit.register(_shutdown_probe_executor)


def get_upstream_status() -> dict:
    config = load_config()
    upstreams_cfg = {
        name: cfg for name, cfg in config.get("upstreams", {}).items() if isinstance(cfg, dict)
    }
    futures = {name: _probe_executor.submit(_probe_upstream, name, cfg) for name, cfg in upstreams_cfg.items()}
    upstreams_status = {}
    summary = {"total": 0, "online": 0, "degraded": 0, "offline": 0, "disabled": 0, "configured": 0}
    for name, fut in futures.items():
        try:
            info = fut.result(timeout=_PROBE_TIMEOUT.connect + _PROBE_TIMEOUT.read + 1.0)
        except Exception as e:
            logger.warning(f"[{name}] probe executor raised: {e}")
            info = {
                "enabled": True,
                "type": "",
                "status": "offline",
                "latency_ms": None,
                "last_error": str(e)[:_PROBE_ERROR_MAX_CHARS],
                "description": "",
            }
        upstreams_status[name] = info
        summary["total"] += 1
        if not info.get("enabled", False):
            summary["disabled"] += 1
        else:
            bucket = info.get("status", "unknown")
            summary[bucket] = summary.get(bucket, 0) + 1
    return {
        "timestamp": int(time.time()),
        "summary": summary,
        "upstreams": upstreams_status,
        "cache": get_cache_stats(),
    }


# ---------------------------------------------------------------------------
# Interfaces / tool specs
# ---------------------------------------------------------------------------


def _derive_source(spec: dict, upstream_tool_mapping: dict) -> str:
    routing = spec.get("routing") or spec.get("name")
    mapping = upstream_tool_mapping.get(routing) or {}
    sources = list(mapping.keys())
    if sources:
        return sources[0]
    return spec.get("source") or spec.get("upstream") or ""


_interfaces_cache: TTLCache = TTLCache(maxsize=1, ttl=5.0)
_INTERFACES_CACHE_KEY = "interfaces"


def get_interfaces() -> dict:
    """返回工具清单 + routing chain + cache TTL。

    5s TTL 缓存: load_all_tools 要遍历 YAML + 跑 codegen, 每次 /api/interfaces
    都重跑是浪费。前端每几秒轮询时尤其明显。
    """
    cached = _interfaces_cache.get(_INTERFACES_CACHE_KEY)
    if cached is not None:
        return cached  # type: ignore[return-value]

    config_path = Path(GATEWAY_CONFIG_PATH)
    config = load_config()
    from ..core.tool_loader import load_all_tools, get_cache_ttl, derive_routing_chain

    tools = load_all_tools(config_path, config)
    upstream_tool_mapping = config.get("upstream_tool_mapping", {}) or {}
    result = []
    for spec in tools:
        routing_key = spec.get("routing")
        chain = derive_routing_chain(config, routing_key) if routing_key else []
        cache_ttl_key = spec.get("cache_ttl_key")
        # realtime_quote 是实时接口，不缓存，过滤掉缓存配置
        if cache_ttl_key == "realtime_quote":
            cache_ttl_key = None
            cache_ttl_seconds = None
        else:
            cache_ttl_seconds = get_cache_ttl(config, cache_ttl_key) if cache_ttl_key else None
        params = spec.get("params") or []
        result.append({
            "name": spec.get("name"),
            "description": spec.get("description", ""),
            "source": _derive_source(spec, upstream_tool_mapping),
            "dangerous": bool(spec.get("dangerous", False)),
            "cache_ttl_key": cache_ttl_key,
            "cache_ttl_seconds": cache_ttl_seconds,
            "data_source_type": spec.get("data_source_type"),
            "routing": routing_key,
            "chain": chain,
            "params": params,
        })
    out = {"timestamp": int(time.time()), "tools": result}
    _interfaces_cache[_INTERFACES_CACHE_KEY] = out
    return out


# ---------------------------------------------------------------------------
# MCP tools/list (raw from gateway)
# ---------------------------------------------------------------------------

_mcp_tools_cache: TTLCache = TTLCache(maxsize=1, ttl=30.0)
_MCP_TOOLS_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)


def _build_source_map() -> dict[str, str]:
    """从 load_all_tools 填充后的 config 构建 routing_key → upstream 的映射。"""
    config = load_config()
    # load_all_tools 会将 tools_*.yaml 中的 upstream_tool_mapping 合并进 config
    from ..core.tool_loader import load_all_tools
    from pathlib import Path
    load_all_tools(Path(GATEWAY_CONFIG_PATH), config)
    mapping = config.get("upstream_tool_mapping") or {}
    source_map = {}
    for routing_key, upstream_dict in mapping.items():
        if isinstance(upstream_dict, dict):
            for upstream_name in upstream_dict.keys():
                source_map[routing_key] = upstream_name
                break
    return source_map


def get_mcp_tools_list() -> dict:
    """调用网关的 MCP /mcp 端点，执行 JSON-RPC tools/list，返回原始工具清单。"""
    cached = _mcp_tools_cache.get("tools")
    if cached is not None:
        return cached  # type: ignore[return-value]

    # Use 127.0.0.1 directly to avoid DNS/port resolution issues when
    # console_api is imported by the gateway process itself (self-call).
    _mcp_url = f"http://127.0.0.1:{gateway_http_port()}/mcp"
    try:
        resp = httpx.post(
            _mcp_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            timeout=_MCP_TOOLS_TIMEOUT,
        )
        resp.raise_for_status()
        data: dict = resp.json()
    except Exception as e:
        _mcp_tools_cache["tools"] = {"error": str(e), "timestamp": int(time.time())}
        return {"error": str(e), "timestamp": int(time.time())}

    result: dict = data.get("result", {})
    raw_tools: list = result.get("tools", [])

    # 从 upstream_tool_mapping 查真实来源，查不到返回 unknown（配置必须完整）
    source_map = _build_source_map()

    tools_out = []
    for t in raw_tools:
        name = t.get("name") or ""
        input_schema = t.get("inputSchema") or {}
        # FastMCP/Pydantic schema 里的 properties 就是参数定义
        props: dict = input_schema.get("properties", {})
        required: list = input_schema.get("required", [])

        params = []
        for pname, pinfo in props.items():
            desc = pinfo.get("description", "")
            ptype = pinfo.get("type", "any")
            enum_vals = pinfo.get("enum")
            p = {
                "name": pname,
                "type": ptype,
                "required": pname in required,
                "description": desc,
            }
            if enum_vals:
                p["enum"] = enum_vals
            params.append(p)

        # SOURCE 100% 来自 upstream_tool_mapping；查不到时标 unknown
        source = source_map.get(name, "unknown")

        tools_out.append({
            "name": name,
            "description": t.get("description", "") or "",
            "source": source,
            "params": params,
        })

    out = {"timestamp": int(time.time()), "tools": tools_out}
    _mcp_tools_cache["tools"] = out
    return out


# ---------------------------------------------------------------------------
# Health check (for /health)
# ---------------------------------------------------------------------------

_gateway_health_cache: dict[str, tuple[float, bool]] = {}
_GATEWAY_HEALTH_CACHE_TTL = 1.0
_GATEWAY_HEALTH_TIMEOUT = 1.5


def is_gateway_reachable() -> bool:
    """快速探 gateway /mcp 端口是否在监听。1s 缓存。"""
    cached = _gateway_health_cache.get("gateway")
    if cached is not None:
        ts, val = cached
        if (time.monotonic() - ts) < _GATEWAY_HEALTH_CACHE_TTL:
            return val
    reachable = False
    try:
        with socket.create_connection(("127.0.0.1", gateway_http_port()), timeout=_GATEWAY_HEALTH_TIMEOUT):
            reachable = True
    except (OSError, socket.timeout):
        reachable = False
    _gateway_health_cache["gateway"] = (time.monotonic(), reachable)
    return reachable


def get_health() -> dict:
    reachable = is_gateway_reachable()
    return {
        "status": "ok" if reachable else "degraded",
        "mcp_url": mcp_url(),
        "gateway_reachable": reachable,
        "timestamp": int(time.time()),
    }
