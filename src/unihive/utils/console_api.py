"""
Console HTTP API — 纯函数，无 HTTP 框架依赖。
被 gateway_server 在 /api/* 路由下调用，也被原 console_server 直接 serve。
"""
from __future__ import annotations

import asyncio
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

# 项目根目录 (src/unihive/utils/console_api.py 向上 4 层)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

CONSOLE_HTML_PATH = str(_PROJECT_ROOT / "console.html")
GATEWAY_CONFIG_PATH = str(_PROJECT_ROOT / "config" / "upstreams.yaml")
CACHE_DB_PATH = str(_PROJECT_ROOT / "logs" / "cache.db")

# 从配置文件读取，避免硬编码
_GW_CFG: dict | None = None
_INJECTED: bool = False

# OmniClient instance (injected by gateway_server)
_OMNI_CLIENT: "OmniClient | None" = None


def set_omni_client(client: "OmniClient | None") -> None:
    """GatewayServer injects OmniClient after initialization."""
    global _OMNI_CLIENT
    _OMNI_CLIENT = client


def _omni() -> "OmniClient | None":
    return _OMNI_CLIENT


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

    HIGH-3 (2026-09-14 audit): 当 GatewayServer 已通过 ``set_config()``
    注入过 ``_GW_CFG`` 时, 优先复用注入的 config — 它已经跑过
    ``load_all_tools`` 的 mutation (回写了 upstream_tool_mapping), 与
    router / interfaces / mcp-tools-list 的视图一致; 否则每次重新读盘
    会拿到一份"未 mutation"的 mapping, 与运行态不一致。

    不缓存: mutation 是不可重入的 (load_all_tools 写回 upstream_tool_mapping),
    缓存会让 mutation 跨调用泄漏到 /api/config 返回值。下游 get_interfaces
    自己有 5s 缓存, 这里没必要再缓存。
    """
    # 1. 优先复用 GatewayServer 注入的 config (运行态 + mutation 完整)
    if _INJECTED and _GW_CFG is not None:
        return _mask_config_secrets({**_GW_CFG})

    # 2. Standalone 模式 (无 gateway server): 从盘上重新加载
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

import threading

_probe_cache: TTLCache = TTLCache(maxsize=256, ttl=5.0)
# LOW-5 (2026-09-14 audit): 同 key 并发折叠。get_upstream_status 给每个
# upstream 都 submit 一次 _probe_upstream, 启动瞬间 N 个 worker 同时探活
# 会触发上游 429。in-flight dict 让同 (name, type, base_url) 只跑一次,
# 其他 worker 等结果 (leader 写完 _probe_cache 后 set event)。Event + lock
# 跨线程同步 (follower 在不同 worker 里 wait)。
_probe_in_flight: dict[tuple, threading.Event] = {}
_probe_in_flight_lock = threading.Lock()
_PROBE_ERROR_MAX_CHARS = 200
_PROBE_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0)


def _probe_upstream(name: str, cfg: dict) -> dict:
    upstream_type = cfg.get("type", "")
    base_url = cfg.get("base_url", "")
    cache_key = (name, upstream_type, base_url)

    payload = _probe_cache.get(cache_key)
    if payload is not None:
        return payload

    # LOW-5: 同 key 已在跑 -> 等结果而非新起一个网络请求
    is_leader = False
    wait_event: threading.Event | None = None
    with _probe_in_flight_lock:
        existing = _probe_in_flight.get(cache_key)
        if existing is not None:
            wait_event = existing
        else:
            wait_event = threading.Event()
            _probe_in_flight[cache_key] = wait_event
            is_leader = True

    if not is_leader:
        # follower: 等 leader 完成, 取它写入 _probe_cache 的结果
        assert wait_event is not None
        wait_event.wait(timeout=_PROBE_TIMEOUT.connect + _PROBE_TIMEOUT.read + 2.0)
        cached_after = _probe_cache.get(cache_key)
        if cached_after is not None:
            return cached_after
        # leader 失败 / 超时: 返回兜底, 不让 follower 永远等
        return {
            "enabled": cfg.get("enabled", False),
            "type": upstream_type,
            "description": cfg.get("description", ""),
            "status": "unknown",
            "latency_ms": None,
            "last_error": "probe coalesce timeout",
        }

    # leader: 真正跑探活
    try:
        result = _do_probe(upstream_type, base_url, cfg)
        _probe_cache[cache_key] = result
        return result
    finally:
        # 唤醒 follower + 清理 in-flight
        with _probe_in_flight_lock:
            _probe_in_flight.pop(cache_key, None)
        wait_event.set()


def _do_probe(upstream_type: str, base_url: str, cfg: dict) -> dict:
    """执行单次 upstream 探活。LOW-5 抽出便于 leader/follower 共享。"""
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
    return result


_probe_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="upstream-probe")


def _shutdown_probe_executor() -> None:
    """进程退出时关掉 probe executor, 避免 worker 线程泄漏。

    LOW-1 (2026-09-14 audit): 之前挂在 atexit 上, 但 stdio transport
    下 FastMCP 可能拦截退出流程, atexit 不保证触发。GatewayServer.stop()
    现在显式调一次, 保证 worker 线程在正常退出路径下被关掉。
    """
    try:
        _probe_executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


# 保留 atexit 兜底: standalone 使用 (无 gateway_server) 仍依赖它。
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


# ---------------------------------------------------------------------------
# OMNI Board Sync API
# ---------------------------------------------------------------------------


def get_omni_stats() -> dict[str, Any]:
    """Get stats + recent logs."""
    omni = _omni()
    if omni is None:
        return {"status": "failed", "message": "OMNI 客户端未初始化", "stats": [], "recent_logs": []}
    return {
        "status": "ok",
        "stats": omni.get_stats(),
        "recent_logs": omni.get_recent_logs(limit=10),
    }


def query_omni_sectors(
    source: str = "",
    board_type: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """Query sectors with pagination."""
    omni = _omni()
    if omni is None:
        return {"status": "failed", "message": "OMNI 客户端未初始化", "sectors": [], "total": 0, "total_count": 0}
    return omni.query_sectors(source, board_type, keyword, page, page_size)


async def run_omni_sync(source: str = "all", board_type: str = "all") -> dict[str, Any]:
    """Trigger sync with source whitelist validation."""
    # Source whitelist (business boundary)
    allowed_sources = {"all", "fuyao", "TDX", "tdxquant", "tqquant"}
    if source not in allowed_sources:
        return {"status": "failed", "message": f"不支持的数据源: {source}", "record_count": 0}

    omni = _omni()
    if omni is None:
        return {"status": "failed", "message": "OMNI 客户端未初始化", "record_count": 0}

    return await omni.run_sync(source, board_type)


def get_omni_sector_stocks(
    sector_id: str = "",
    code: str = "",
    source: str = "",
) -> dict[str, Any]:
    """Get stocks in a sector."""
    omni = _omni()
    if omni is None:
        return {"status": "failed", "message": "OMNI 客户端未初始化"}

    if not sector_id and not code:
        return {"status": "failed", "message": "sector_id 或 code 必填"}

    try:
        result = omni.get_sector_stocks(
            sector_id=int(sector_id) if sector_id else None,
            sector_code=code or None,
            source=source or None,
        )
    except ValueError as e:
        return {"status": "failed", "message": str(e)}

    if result is None:
        return {"status": "failed", "message": f"未找到板块: code={code} source={source}"}
    return result


def build_omni_routes() -> list:
    """Build OMNI routes for gateway to mount."""
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    from starlette.requests import Request

    async def omni_stats_api(req: Request):
        return JSONResponse(get_omni_stats())

    async def omni_query_api(req: Request):
        params = req.query_params
        return JSONResponse(query_omni_sectors(
            source=params.get("source", ""),
            board_type=params.get("board_type", ""),
            keyword=params.get("keyword", ""),
            page=int(params.get("page", 1)),
            page_size=int(params.get("page_size", 50)),
        ))

    async def omni_sync_api(req: Request):
        body = await req.json()
        result = await run_omni_sync(
            source=body.get("source", "all"),
            board_type=body.get("board_type", "all"),
        )
        return JSONResponse(result)

    async def omni_sector_stocks_api(req: Request):
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
