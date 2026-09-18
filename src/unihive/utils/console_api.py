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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import JSONResponse

import httpx
import yaml
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# 项目根目录 (src/unihive/utils/console_api.py 向上 4 层)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

CONSOLE_HTML_PATH = str(_PROJECT_ROOT / "console.html")
# 可经环境变量指向替代配置 (隔离实例/验证脚本用); 默认项目正式配置。
# 必须与 GatewayServer 启动时的 --config 指向同一文件, 否则 PUT 落盘与运行态脱节。
GATEWAY_CONFIG_PATH = os.getenv(
    "UNIHIVE_UPSTREAMS_CONFIG",
    str(_PROJECT_ROOT / "config" / "upstreams.yaml"),
)
CACHE_DB_PATH = str(_PROJECT_ROOT / "logs" / "cache.db")

# 从配置文件读取，避免硬编码
_GW_CFG: dict | None = None
_INJECTED: bool = False

# OmniClient instance (injected by gateway_server)
_OMNI_CLIENT: "OmniClient | None" = None

# OMNI 全量同步 in-flight 互斥: run_sync 最长约 300s 且写同一个 board.db,
# 并发 (双击/旧客户端超时重发) 会触发 SQLite locked 与重复入库。
_omni_sync_lock = asyncio.Lock()


def set_omni_client(client: "OmniClient | None") -> None:
    """GatewayServer injects OmniClient after initialization."""
    global _OMNI_CLIENT
    _OMNI_CLIENT = client


def _omni() -> "OmniClient | None":
    return _OMNI_CLIENT


_upstream_registry: dict[str, object] = {}


def set_upstream_registry(registry: dict[str, object]) -> None:
    """注入 gateway 的 upstreams dict。

    必须传**同一引用**而非拷贝: 热开关 (set_upstream_enabled) 通过
    pop/新 client 赋值实时改这个 dict, MCP Browser / metrics / 状态叠加
    都经 _get_upstream_client 解析, 快照拷贝会在禁用→重新启用后仍指向
    已 stop 的旧 client (对比 proxy_api 也是别名化 live dict)。
    """
    global _upstream_registry
    _upstream_registry = registry


def _get_upstream_client(name: str) -> object | None:
    return _upstream_registry.get(name)


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
    """递归扫描 config 节点，对密钥类字段打码。返回深拷贝（不修改原对象）。"""
    import copy
    node = copy.deepcopy(node)
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if isinstance(v, str) and _is_secret_field(k):
                node[k] = mask_sensitive(v)
            elif isinstance(v, (dict, list)):
                # 必须把递归返回值回赋, 否则内层 mask 后的新 dict 被丢弃,
                # 外层仍指向原 dict (未 mask 的副本)
                node[k] = _mask_config_secrets(v)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, (dict, list)):
                node[i] = _mask_config_secrets(item)
    return node


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def load_config() -> dict:
    """加载并脱敏 config。复用 config_loader 解析 ${VAR} 占位符.

    每次现取现读盘 — 不复用 GatewayServer.set_config() 注入的 _GW_CFG,
    因为:
    - /api/config / /api/upstreams / /api/interfaces 的语义是「当前配置快照」,
      而非 GatewayServer 进程内经过 load_all_tools mutation 的运行时副本
      (mutation 写在 get_interfaces 自己内部, 不应泄漏到 /api/config)。
    - 测试 fixture 用 monkeypatch.setattr(GATEWAY_CONFIG_PATH, …) 时必须
      看到自己写入的 yaml, 否则断言拿不到期望的 upstream。

    不缓存: 见上面的「每次现取现读盘」理由。
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

    masked = _mask_config_secrets({**config})
    # 乐观锁 version: 用两 yaml 文件 size+mtime 算一个轻量 hash。
    # 客户端提交时附带 version, 后端 PUT 检测到不匹配 → 409。
    # 故意不带内容 hash: 内容大且每次脱敏都生成新 dict, 用文件元信息足够。
    masked["version"] = _compute_config_version()
    return masked


def _compute_config_version() -> str:
    """两 yaml 文件 (size, mtime) 的 sha256, 用于乐观锁。

    元信息变更即视为版本变更 — 与 GET / PUT 走的文件完全一致。
    """
    import hashlib

    files = [Path(GATEWAY_CONFIG_PATH), Path(GATEWAY_CONFIG_PATH).parent / "config.yaml"]
    parts: list[str] = []
    for p in files:
        try:
            stat = p.stat()
            parts.append(f"{p.name}:{stat.st_size}:{int(stat.st_mtime)}")
        except OSError:
            parts.append(f"{p.name}:missing")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# FUYAO cache 配置读写 (嵌套 mapping)
# ---------------------------------------------------------------------------


def get_fuyao_cache() -> dict:
    """读取 fuyao_cache 段, 不存在返回默认值。

    2026-09-15 重构后新 shape: ``{"tools": {...}, "version": "..."}``。
    读侧容错: 若 on-disk 仍是旧 shape (含 ``endpoints.<ep>.default``),
    flatten ``endpoints[*].override`` 成 ``tools`` 后返回, 旧 ``default`` 字段丢弃。
    """
    config = load_config()
    fc = config.get("fuyao_cache")
    if not isinstance(fc, dict):
        return {"tools": {}, "version": config.get("version", "")}
    # 旧 shape 容错: 有 endpoints 没 tools, flatten 一下
    if "endpoints" in fc and "tools" not in fc:
        tools: dict[str, int] = {}
        for ep_cfg in fc.get("endpoints", {}).values():
            if not isinstance(ep_cfg, dict):
                continue
            override = ep_cfg.get("override")
            if isinstance(override, dict):
                for k, v in override.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                        tools[str(k)] = int(v)
        return {"tools": tools, "version": config.get("version", "")}
    return fc  # 新格式直通


def save_fuyao_cache(payload: dict, *, expected_version: str | None = None) -> dict:
    """写入 fuyao_cache 段到 upstreams.yaml (新 shape: { tools: {...} })。

    - 只覆盖 fuyao_cache 子树, 其他顶层段 (upstreams / cache / tools / ...)
      原样保留
    - 启动期 config.yaml 已合并进 upstreams.yaml 的内容, 此处直接改 upstreams.yaml
    - expected_version 与当前文件 version 不一致 → 抛 VersionConflict
    """
    from pydantic import ValidationError

    from ..models.fuyao_cache import FuyaoCacheConfig

    try:
        new_cfg = FuyaoCacheConfig.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"fuyao_cache 校验失败: {e}") from e

    # 乐观锁
    current_version = _compute_config_version()
    if expected_version and expected_version != current_version:
        raise VersionConflict(
            f"config 版本不一致 (expected={expected_version}, current={current_version}); "
            "请刷新页面后重试"
        )

    config_path = Path(GATEWAY_CONFIG_PATH)
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    raw["fuyao_cache"] = {
        "tools": dict(new_cfg.tools),
    }
    # 用 default_flow_style=False 保持块格式; sort_keys=False 保留 key 顺序
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    # 失效 in-memory 缓存 (interfaces 30s cache / mcp-tools)
    _interfaces_cache.clear()
    _mcp_tools_cache.clear()
    # 触发 fuyao 客户端下次拿新 TTL: 不直接 reload (Gateway 进程内配置不动),
    # 让运维重启生效 — 与 CachePanel banner "需重启网关" 一致。

    return {
        "fuyao_cache": raw["fuyao_cache"],
        "version": _compute_config_version(),
    }


class VersionConflict(Exception):
    """PUT 时客户端 version 与服务端不一致。"""


# ---------------------------------------------------------------------------
# 上游启用/禁用热开关 (2026-09-18)
# ---------------------------------------------------------------------------

# GatewayServer 实例, _do_initialize 后注入、stop() 置 None。
# 仅 /api/config/upstreams/<name>/enabled 回调使用; stdio 模式下无路由可达。
_gateway_server: Any = None


def set_gateway_server(server: Any) -> None:
    """GatewayServer 初始化完成后注入自身。"""
    global _gateway_server
    _gateway_server = server


def _unquote_yaml_key(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


def rewrite_upstream_enabled(text: str, name: str, enabled: bool) -> str:
    """定向改写 upstreams.yaml 文本中某上游块的 ``enabled`` 标量。

    块定位: 恰好 2 空格缩进的 ``  <name>:`` 顶层 upstream 键, 区间到下一个
    同级键; ``enabled`` 锚定恰好 4 空格缩进, 不会误改 retry 等嵌套子段。
    块内无 enabled 时在块头后插入一行。刻意不整文件 yaml.safe_dump —— 那会
    冲垮 mapping 段的手工对齐列与全部注释。文件换行风格 (LF/CRLF) 原样保留;
    仅被改的那一行, 标量前规范化为单空格、注释间距保留原值。

    Raises:
        KeyError: 文本中找不到该 upstream 块
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    header_idx: int | None = None
    block_end = len(lines)
    for i, line in enumerate(lines):
        # 同级键: 恰好 2 空格 + 非空白, 形如 "  foo:" / "  foo:  # x"
        m = re.match(r"^  ([^\s:#][^:]*):\s*(?:#.*)?$", line)
        if not m:
            continue
        key = _unquote_yaml_key(m.group(1))
        if key == name:
            header_idx = i
            for j in range(i + 1, len(lines)):
                if re.match(r"^  \S", lines[j]):
                    block_end = j
                    break
            break
    if header_idx is None:
        raise KeyError(f"upstream block {name!r} not found in config")

    # 只换标量 token。注意 ": false" 中冒号后空格不可省 (YAML 里 "enabled:false"
    # 会被当成普通字符串标量), 故统一输出单个空格; 尾部行内注释原样保留。
    enabled_re = re.compile(r"^    enabled:\s*\S*([ \t]*)?(#.*)?$")
    scalar = "true" if enabled else "false"
    for i in range(header_idx + 1, block_end):
        m = enabled_re.match(lines[i])
        if m:
            gap, comment = m.group(1) or "", m.group(2)
            lines[i] = f"    enabled: {scalar}" + (f"{gap}{comment}" if comment else "")
            break
    else:
        lines.insert(header_idx + 1, f"    enabled: {scalar}")

    out = newline.join(lines)
    if text.endswith("\n"):
        out += newline
    return out


def save_upstream_enabled(
    name: str, enabled: bool, *, expected_version: str | None = None
) -> dict:
    """把某上游 enabled 持久化进 upstreams.yaml (保格式定向改写)。

    只落盘, 不动运行态 —— HTTP handler 随后调
    ``GatewayServer.set_upstream_enabled()`` 完成热增删/断连。
    """
    if not isinstance(enabled, bool):
        raise ValueError("'enabled' must be a boolean")

    current_version = _compute_config_version()
    if expected_version and expected_version != current_version:
        raise VersionConflict(
            f"config 版本不一致 (expected={expected_version}, current={current_version}); "
            "请刷新页面后重试"
        )

    path = Path(GATEWAY_CONFIG_PATH)
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}
    if name not in (raw.get("upstreams") or {}):
        raise KeyError(f"Unknown upstream: {name}")

    new_text = rewrite_upstream_enabled(text, name, enabled)

    # 落盘前语义复核: 目标位确实变成期望值
    verified = yaml.safe_load(new_text) or {}
    actual = bool((verified.get("upstreams") or {}).get(name, {}).get("enabled", False))
    if actual != enabled:
        raise RuntimeError(
            f"enabled rewrite verification failed for {name!r}: got {actual}"
        )

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(new_text)

    # GET 侧缓存失效 (config 视图 / mcp-tools)
    _interfaces_cache.clear()
    _mcp_tools_cache.clear()

    return {"version": _compute_config_version()}


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
        # 2026-09-17: 覆盖运行时 client 状态 (mootdx2/tdx_quant 等非 HTTP 上游)
        client = _get_upstream_client(name)
        if client is not None and hasattr(client, "is_available"):
            # client.is_available 反映真实运行时状态 (Connected/DEGRADED/Failed)
            info["status"] = "online" if client.is_available else "offline"
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

    # 快速失败, 不排队等待: 前端按钮在 running 期间本就禁用, 走到这里说明
    # 是并发/重放请求, 直接告知即可。
    if _omni_sync_lock.locked():
        return {
            "status": "failed",
            "message": "已有同步任务在执行, 请等待当前同步完成",
            "record_count": 0,
        }
    async with _omni_sync_lock:
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
        Route("/api/upstreams/<name>/metrics", upstream_metrics_api),
    ]


# Module-level handler for /api/upstreams/<name>/metrics (importable for testing)
async def upstream_metrics_api(req: "Request") -> "JSONResponse":
    """返回某个上游 client 的运行时指标 (目前仅 mootdx2 实现 get_metrics())。

    状态码:
      200: 正常返回 metrics dict
      404: 上游名未在 registry 注册
      501: 上游客户端未实现 get_metrics() (例如 tdx_quant / fuyao / omni)
      500: 读取 metrics 时内部异常
    """
    from starlette.responses import JSONResponse
    from starlette.requests import Request as StarletteRequest

    name = req.path_params["name"]
    client = _get_upstream_client(name)
    if client is None:
        return JSONResponse(
            {"error": f"upstream '{name}' not registered"},
            status_code=404,
        )
    getter = getattr(client, "get_metrics", None)
    if not callable(getter):
        return JSONResponse(
            {"error": f"upstream '{name}' does not expose metrics"},
            status_code=501,
        )
    try:
        metrics = getter()
    except Exception as e:
        logger.warning(f"upstream_metrics_api({name}) raised: {e}")
        return JSONResponse(
            {"error": str(e)[:200]},
            status_code=500,
        )
    return JSONResponse({
        "upstream": name,
        "metrics": metrics,
        "timestamp": int(time.time()),
    })


# ---------------------------------------------------------------------------
# MCP 浏览器 —— 原样展示 "本身即 MCP server" 的上游的 tools/list
# ---------------------------------------------------------------------------
# 这里刻意不做任何归一化: 不重命名、不平铺 params、不套 model_dump()。
# 对照 gateway_server._mcp_tools_api (它会把工具压成 {name, description,
# source, params}), 那套东西是给 "网关聚合视图" 用的, 不是给这里用的。
# 控制台 MCP 浏览器要展示的就是上游到底发了什么。


def get_mcp_browser_upstreams() -> dict:
    """列出 "自身即 MCP server" 的已注册上游。

    判据是 client 上有没有 list_tools_raw() —— 这比查配置 type 更可靠:
    mootdx2 / tdx_quant / thsdk / omni 的 list_tools() 是返回 [] 的桩或
    硬编码列表, 它们没有这个方法, 自然被排除。
    """
    upstreams: list[dict] = []
    for name, client in _upstream_registry.items():
        if not callable(getattr(client, "list_tools_raw", None)):
            continue
        status = getattr(client, "status", None)
        upstreams.append({
            "name": name,
            "type": type(client).__name__,
            # UpstreamStatus 是普通 Enum (非 str-Enum), 直接塞进 JSONResponse
            # 会 TypeError → 500, 必须取 .value。
            "status": getattr(status, "value", str(status)) if status is not None else "unknown",
            "available": bool(getattr(client, "is_available", False)),
        })
    upstreams.sort(key=lambda u: u["name"])
    return {"timestamp": int(time.time()), "upstreams": upstreams}


async def fetch_upstream_raw_tools(upstream: str) -> tuple[int, dict]:
    """拉取某个上游的原始 tools/list, 返回 (http_status, body)。

    状态码:
      200: 拉取成功 (含 "上游合法地返回 0 个工具" —— 见 ok 字段)
      400: 上游已注册但不是 MCP server
      404: 上游名未在 registry 注册
      502: 上游请求失败 (连不上 / 超时 / HTTP 错误 / JSON-RPC 错误 / 响应畸形)
    """
    client = _get_upstream_client(upstream)
    if client is None:
        return 404, {
            "error": f"upstream '{upstream}' not registered",
            "upstream": upstream,
        }

    fetch = getattr(client, "list_tools_raw", None)
    if not callable(fetch):
        # 注意: 同类情形下 upstream_metrics_api 用的是 501。这里刻意选 400 ——
        # 语义是 "这个请求对这类资源不适用", 与 "网关不支持该功能" 不同。
        # 统一之前请先想清楚, 别顺手改成 501。
        return 400, {
            "error": f"upstream '{upstream}' is not an MCP server",
            "upstream": upstream,
        }

    result = await fetch()
    if not result.ok:
        return 502, {
            "error": f"上游请求失败: {result.error}",
            "errorKind": result.error_kind,
            "upstream": upstream,
        }

    return 200, {
        "upstream": upstream,
        "timestamp": int(time.time()),
        "ok": True,
        "error": None,
        "errorKind": None,
        "pages": result.pages,
        "truncated": result.truncated,
        "truncatedReason": result.truncated_reason,
        "count": len(result.tools),
        "bytes": result.total_bytes,
        # 原样透传, 一个字段都不动。
        "tools": result.tools,
    }


async def mcp_browser_upstreams_api(req: "Request") -> "JSONResponse":
    """GET /api/mcp-browser/upstreams"""
    from starlette.responses import JSONResponse

    return JSONResponse(get_mcp_browser_upstreams())


async def mcp_browser_tools_api(req: "Request") -> "JSONResponse":
    """GET /api/mcp-browser/<upstream>/tools —— 原始 tools/list, 原样返回。

    上游失败刻意返回 502 而不是 200+ok:false: 前端 axios 实例
    (web/src/api/client.ts) 只对 5xx 自动重试一次, 且 errorMessage() 已经会
    从 body 里取 .error 渲染。这样白捡一次瞬时容错与正确的错误呈现, 前端零
    新增管道。代价是硬失败要等约 2× 上游超时才浮出来 —— 对本地控制台可接受。
    想 "简化" 成 200 之前请先读这段。
    """
    from starlette.responses import JSONResponse

    upstream = req.path_params["upstream"]
    status_code, body = await fetch_upstream_raw_tools(upstream)
    return JSONResponse(body, status_code=status_code)


def build_mcp_browser_routes() -> list:
    """控制台 MCP 浏览器的 /api/* 路由 (挂载进 gateway_server)。

    路径参数必须用 {upstream}。Starlette 1.6 不认 <upstream> 那种语法 ——
    它不报错, 只是编译出 params={} 于是永远匹配不上 (实测)。仓库里
    build_omni_routes 的 "/api/upstreams/<name>/metrics" 就是这么一条
    死路由, 靠 gateway_server 里 {name} 的那条才真正工作。
    """
    from starlette.routing import Route

    return [
        Route("/api/mcp-browser/upstreams", mcp_browser_upstreams_api),
        Route("/api/mcp-browser/{upstream}/tools", mcp_browser_tools_api),
    ]


# ---------------------------------------------------------------------------
# 上游启用/禁用热开关 HTTP 层 (2026-09-18)
# ---------------------------------------------------------------------------


def _validate_toggle_enabled_body(body: object) -> tuple[bool | None, str | None]:
    """校验热开关请求体。成功返 (enabled, None), 失败返 (None, error_msg)。"""
    if not isinstance(body, dict):
        return None, "body must be a JSON object"
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        return None, "'enabled' must be a boolean (true/false)"
    return enabled, None


async def upstream_enabled_api(req: "Request") -> "JSONResponse":
    """PUT /api/config/upstreams/{name}/enabled

    body: ``{"enabled": bool, "version": str}`` —— 先持久化 yaml, 再调
    GatewayServer 热改 /mcp 发布集与运行时连接。

    状态码:
      200: 成功 (body 含 enabled/connected/tools_added/tools_removed/version;
           部分失败后的同态重试由运行时 reconcile 自愈, 同样返回 200)
      400: body 非法或 enabled 非布尔
      404: 上游不存在
      409: 乐观锁 version 不匹配
      500: 运行时热切换部分失败 (yaml 已落盘, body 带 version; 原样重试可自愈)
      503: GatewayServer 未注入 (stdio 模式不可达, 防御性)
    """
    from starlette.responses import JSONResponse

    name = req.path_params["name"]
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    enabled, body_error = _validate_toggle_enabled_body(body)
    if enabled is None:
        return JSONResponse({"error": body_error}, status_code=400)

    try:
        # yaml 读/解析/校验/写是阻塞文件 IO, 放线程池避免卡住事件循环
        # (全量 safe_load + 写盘在慢盘/杀软扫描时可达数十毫秒)。
        persisted = await asyncio.to_thread(
            save_upstream_enabled,
            name,
            enabled,
            expected_version=body.get("version"),
        )
    except VersionConflict as e:
        return JSONResponse(
            {"error": "version_conflict", "message": str(e)},
            status_code=409,
        )
    except KeyError:
        return JSONResponse(
            {"error": f"unknown upstream: {name}"},
            status_code=404,
        )
    except (ValueError, yaml.YAMLError, OSError, RuntimeError) as e:
        # 写后解析复核失败 / 配置块定位歧义 / IO 错误: 保持 JSON 错误契约,
        # 不落 500 纯文本 (前端 errorMessage 才能解析)。
        logger.exception("save_upstream_enabled(%r, %s) failed", name, enabled)
        msg = f"config persist failed: {type(e).__name__}: {e}"[:300]
        return JSONResponse({"error": msg}, status_code=500)

    server = _gateway_server
    if server is None:
        return JSONResponse(
            {"error": "gateway runtime not available", "version": persisted["version"]},
            status_code=503,
        )

    try:
        runtime = await server.set_upstream_enabled(name, enabled)
    except KeyError:
        # 磁盘有但运行态 config 没有 (极端双轨), 按未知上游处理
        return JSONResponse(
            {"error": f"unknown upstream: {name}", "version": persisted["version"]},
            status_code=404,
        )
    except Exception as e:
        # yaml 已落盘但热切换失败: 不回滚磁盘 (重启即收敛到目标态),
        # 返回 500 + 新 version, 让前端提示用户可重试或重启。
        logger.exception(f"set_upstream_enabled({name!r}, {enabled}) failed")
        return JSONResponse(
            {
                "error": f"runtime toggle failed: {type(e).__name__}: {e}"[:300],
                "version": persisted["version"],
            },
            status_code=500,
        )

    return JSONResponse({**runtime, "version": persisted["version"]})


def build_upstream_routes() -> list:
    """上游热开关路由 (挂载进 gateway_server)。路径参数用 {name} (Starlette 1.6)。"""
    from starlette.routing import Route

    return [
        Route(
            "/api/config/upstreams/{name}/enabled",
            upstream_enabled_api,
            methods=["PUT"],
        ),
    ]
