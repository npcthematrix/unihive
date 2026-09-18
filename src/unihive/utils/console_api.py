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

from ..core.config_merge import apply_config_overlays
from ..storage.config_store import (
    RevisionConflict as _StoreRevisionConflict,
    validate_path as _validate_overlay_path,
)

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

# SQLite 配置薄覆盖层 (data/config.db), 由 GatewayServer._apply_db_config_overlays
# 装配后注入。同步读路径 (load_config 等) 不碰 DB, 只合并 _overlay_rows 镜像;
# 镜像在启动注入与每次写操作后刷新。None + 空镜像 = UNIHIVE_CONFIG_SOURCE=yaml
# 逃生模式, 此时所有配置写接口返回 503。
_config_store: Any = None
_overlay_rows: list = []
_overlay_revision: int = 0

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


def set_config_store(store: Any, rows: list | None = None, revision: int = 0) -> None:
    """注入 SQLite ConfigStore 与当前 overlay 行镜像 (Gateway 启动时)。

    rows 元素只需有 ``.path`` / ``.value`` (ConfigStore.OverlayRow)。
    传 ``store=None`` 进入 yaml 逃生模式 (UNIHIVE_CONFIG_SOURCE=yaml / stop)。
    """
    global _config_store, _overlay_rows, _overlay_revision
    _config_store = store
    _overlay_rows = list(rows or [])
    _overlay_revision = int(revision)


async def _refresh_overlay_snapshot() -> None:
    """写操作后重新拉全量行 + revision, 更新同步读路径镜像。"""
    global _overlay_rows, _overlay_revision
    if _config_store is None:
        return
    _overlay_rows = list(await _config_store.list_overlays())
    _overlay_revision = int(await _config_store.revision())


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


def _read_yaml_config() -> dict:
    """读 YAML 出厂基线 (upstreams.yaml + config.yaml), 未脱敏、不含 overlay。

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

    return config


def load_config() -> dict:
    """当前生效配置快照: YAML 出厂基线 + SQLite overlay 镜像, 已脱敏。

    overlay 行镜像由 Gateway 启动注入、每次控制台写操作后刷新, 因此本函数
    保持同步、零 DB IO, get_upstream_status / get_interfaces 等同步调用方
    无需改成 async。乐观锁 version 在 overlay 模式下是 config.db 的整数
    revision; yaml 逃生模式 (store=None) 回落到文件元信息 hash。
    """
    config = _read_yaml_config()
    if _overlay_rows:
        config = apply_config_overlays(config, _overlay_rows)
    masked = _mask_config_secrets({**config})
    masked["version"] = (
        _overlay_revision if _config_store is not None else _compute_config_version()
    )
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
    """读取生效中的 fuyao 工具级 TTL: ``{"tools": {...}, "version": int}``。

    来源 = YAML 基线 fuyao_cache.tools 合并 data/config.db 覆盖行
    (经 load_config 镜像合并), 不直接读盘/读 DB。
    """
    config = load_config()
    fc = config.get("fuyao_cache")
    tools = dict(fc.get("tools") or {}) if isinstance(fc, dict) else {}
    return {"tools": tools, "version": config.get("version", 0)}


async def save_fuyao_cache(payload: dict, *, expected_version: int | str | None = None) -> dict:
    """把 fuyao 工具级 TTL 覆盖写入 data/config.db (事务化, 单 revision bump)。

    - payload: ``{"tools": {gateway 工具名: 秒数}}``, pydantic 校验;
    - 与 YAML 出厂值相同 (或出厂无此键且值缺失) 的行删除而非写入, overlay 只留差异;
    - 面板未提交的旧覆盖行 (tools 里已没有) 一并删除;
    - 成功后热改进程内 config, TTL 即时生效 (无需重启网关)。
    """
    from pydantic import ValidationError

    from ..models.fuyao_cache import FuyaoCacheConfig

    store = _require_store()
    try:
        new_cfg = FuyaoCacheConfig.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"fuyao_cache 校验失败: {e}") from e

    expected = _coerce_revision(expected_version)
    baseline_tools = dict(
        ((_read_yaml_config().get("fuyao_cache") or {}).get("tools") or {})
    )

    prefix = "fuyao_cache.tools."
    existing = await store.list_overlays(prefix=prefix)
    upserts: dict[str, int] = {}
    deletes: list[str] = []
    for tool, seconds in new_cfg.tools.items():
        path = prefix + tool
        _validate_overlay_path(path)
        base = baseline_tools.get(tool)
        same_as_baseline = (
            isinstance(base, int)
            and not isinstance(base, bool)
            and int(base) == int(seconds)
        )
        if same_as_baseline:
            # 恢复出厂: 删行 (行不存在时 apply_changes 是 no-op)
            deletes.append(path)
        else:
            upserts[path] = int(seconds)
    for row in existing:
        tool = row.path[len(prefix):]
        if tool not in new_cfg.tools:
            deletes.append(row.path)

    try:
        revision = await store.apply_changes(upserts, deletes, expected)
    except _StoreRevisionConflict as e:
        raise VersionConflict(
            f"config revision 不一致 (expected={e.expected}, actual={e.actual}); "
            "请刷新页面后重试"
        ) from e

    await _refresh_overlay_snapshot()

    # 热改进程内 config: 用 load_config() 算出的生效 tools 直接同步,
    # 保证 fuyao_ttl_for 下一次调用即读到新值。
    effective = load_config()
    effective_tools = (effective.get("fuyao_cache") or {}).get("tools") or {}
    _sync_runtime_fuyao_tools(effective_tools)

    _interfaces_cache.clear()
    _mcp_tools_cache.clear()

    return {"fuyao_cache": {"tools": dict(new_cfg.tools)}, "version": revision}


def _sync_runtime_fuyao_tools(tools: dict) -> None:
    """TTL 保存后热改 GatewayServer 注入的运行时 config (同一引用)。"""
    cfg = _GW_CFG if _INJECTED and isinstance(_GW_CFG, dict) else None
    if cfg is None:
        return
    fc = cfg.get("fuyao_cache")
    if not isinstance(fc, dict):
        fc = {}
        cfg["fuyao_cache"] = fc
    fc["tools"] = dict(tools)


class ConfigStoreUnavailable(RuntimeError):
    """overlay store 未注入 (UNIHIVE_CONFIG_SOURCE=yaml / 未启动) — 只读模式。"""


def _require_store() -> Any:
    if _config_store is None:
        raise ConfigStoreUnavailable(
            "配置覆盖层未启用 (UNIHIVE_CONFIG_SOURCE=yaml 或网关未初始化)"
        )
    return _config_store


def _coerce_revision(raw: int | str | None) -> int | None:
    """乐观锁 revision: None/空串跳过校验; 接受 int 与纯数字字符串。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise ValueError("version 必须是整数 revision")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and re.fullmatch(r"\d+", raw):
        return int(raw)
    raise ValueError(f"无法识别的 version: {raw!r}; 请刷新页面后重试")


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


async def save_upstream_enabled(
    name: str, enabled: bool, *, expected_version: int | str | None = None
) -> dict:
    """把某上游 enabled 以点路径覆盖持久化进 data/config.db。

    - 与出厂值相同 → 删除覆盖行 (恢复出厂, 升级时不挡基线新默认值);
    - 不同 → upsert ``upstreams.<name>.enabled``, 整数 revision 乐观锁;
    - 只落库; HTTP handler 随后调 GatewayServer.set_upstream_enabled()
      完成热增删 / 断连。
    """
    if not isinstance(enabled, bool):
        raise ValueError("'enabled' must be a boolean")
    store = _require_store()
    expected = _coerce_revision(expected_version)
    upstreams = _read_yaml_config().get("upstreams") or {}
    cfg = upstreams.get(name)
    if not isinstance(cfg, dict):
        raise KeyError(f"Unknown upstream: {name}")
    path = f"upstreams.{name}.enabled"
    _validate_overlay_path(path)
    factory_enabled = bool(cfg.get("enabled", False))
    try:
        if enabled == factory_enabled:
            revision = await store.delete_overlay(
                path, expected_revision=expected
            )
        else:
            revision = await store.upsert_overlay(
                path, enabled, expected_revision=expected
            )
    except _StoreRevisionConflict as e:
        raise VersionConflict(
            f"config revision 不一致 (expected={e.expected}, actual={e.actual}); "
            "请刷新页面后重试"
        ) from e

    await _refresh_overlay_snapshot()
    _interfaces_cache.clear()
    _mcp_tools_cache.clear()
    return {"version": revision}


async def _after_overlay_write() -> None:
    """任意 overlay 写操作后的统一收尾: 刷新镜像 + fuyao TTL 热同步 + 清缓存。"""
    await _refresh_overlay_snapshot()
    effective = load_config()
    effective_tools = (effective.get("fuyao_cache") or {}).get("tools") or {}
    _sync_runtime_fuyao_tools(effective_tools)
    _interfaces_cache.clear()
    _mcp_tools_cache.clear()


async def _reconcile_enabled_overlays(paths: list[str]) -> list[str]:
    """覆盖行删除后, 对 ``upstreams.<name>.enabled`` 路径做运行时热收敛。

    行被删 = 恢复 YAML 出厂值, 据此调 GatewayServer.set_upstream_enabled。
    GatewayServer 未注入 (stdio) 时跳过并登记提示; 运行时热改失败不阻断
    请求 (行已落库, 重启后启动链按 overlay 收敛)。
    """
    warnings_list: list[str] = []
    server = _gateway_server
    for raw_path in paths:
        m = re.fullmatch(r"upstreams\.([A-Za-z0-9_-]+)\.enabled", raw_path)
        if m is None:
            continue
        if server is None:
            warnings_list.append(f"{raw_path}: 运行时未注入, 重启后生效")
            continue
        name = m.group(1)
        factory = bool(
            ((_read_yaml_config().get("upstreams") or {}).get(name) or {}).get(
                "enabled", False
            )
        )
        try:
            await server.set_upstream_enabled(name, factory)
        except Exception as e:  # noqa: BLE001 - 行已落库, 重启收敛
            logger.warning("enabled overlay reconcile failed for %s: %s", raw_path, e)
            warnings_list.append(f"{raw_path}: {type(e).__name__}: {e}"[:200])
    return warnings_list


def list_config_overlays() -> dict:
    """覆盖层全量行 (同步读镜像, 零 DB IO)。

    返回 ``{"revision": int, "overlays": [{path, value, revision, updated_at}]}``。
    """
    _require_store()
    return {
        "revision": _overlay_revision,
        "overlays": [
            {
                "path": row.path,
                "value": row.value,
                "revision": row.revision,
                "updated_at": row.updated_at,
            }
            for row in _overlay_rows
        ],
    }


async def delete_config_overlay(
    path: str, *, expected_version: int | str | None = None
) -> dict:
    """删除单行覆盖 (恢复该路径出厂值), 返回 ``{"path", "version", "warnings"}``。"""
    store = _require_store()
    _validate_overlay_path(path)
    expected = _coerce_revision(expected_version)
    try:
        revision = await store.delete_overlay(path, expected_revision=expected)
    except _StoreRevisionConflict as e:
        raise VersionConflict(
            f"config revision 不一致 (expected={e.expected}, actual={e.actual}); "
            "请刷新页面后重试"
        ) from e

    await _after_overlay_write()
    warnings_list = await _reconcile_enabled_overlays([path])
    return {"path": path, "version": revision, "warnings": warnings_list}


async def reset_config_overlays(
    *, expected_version: int | str | None = None
) -> dict:
    """清空全部覆盖行 (一键恢复出厂), 返回 ``{"version", "warnings"}``。"""
    store = _require_store()
    affected = [row.path for row in _overlay_rows]
    expected = _coerce_revision(expected_version)
    try:
        revision = await store.reset(expected_revision=expected)
    except _StoreRevisionConflict as e:
        raise VersionConflict(
            f"config revision 不一致 (expected={e.expected}, actual={e.actual}); "
            "请刷新页面后重试"
        ) from e

    await _after_overlay_write()
    warnings_list = await _reconcile_enabled_overlays(affected)
    return {"version": revision, "warnings": warnings_list}


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

    body: ``{"enabled": bool, "version": int}`` —— 先写 config.db overlay, 再调
    GatewayServer 热改 /mcp 发布集与运行时连接。

    状态码:
      200: 成功 (body 含 enabled/connected/tools_added/tools_removed/version;
           部分失败后的同态重试由运行时 reconcile 自愈, 同样返回 200)
      400: body 非法或 enabled 非布尔
      404: 上游不存在
      409: 乐观锁 version 不匹配
      500: 运行时热切换部分失败 (overlay 已落库, body 带 version; 原样重试可自愈)
      503: config.db overlay 未启用 (UNIHIVE_CONFIG_SOURCE=yaml)
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
        persisted = await save_upstream_enabled(
            name, enabled, expected_version=body.get("version")
        )
    except VersionConflict as e:
        return JSONResponse(
            {"error": "version_conflict", "message": str(e)},
            status_code=409,
        )
    except ConfigStoreUnavailable as e:
        return JSONResponse(
            {"error": "config_store_unavailable", "message": str(e)},
            status_code=503,
        )
    except KeyError:
        return JSONResponse(
            {"error": f"unknown upstream: {name}"},
            status_code=404,
        )
    except ValueError as e:
        # revision 格式非法 / 点路径含非法字符
        logger.warning("save_upstream_enabled(%r, %s) bad request: %s", name, enabled, e)
        msg = f"invalid request: {type(e).__name__}: {e}"[:300]
        return JSONResponse({"error": msg}, status_code=400)

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
        # overlay 已落库但热切换失败: 不回滚 (重启即收敛到目标态),
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


async def config_overlays_list_api(req: "Request") -> "JSONResponse":
    """GET /api/config/overlays — 全量覆盖行 + 当前 revision。

    503: config.db overlay 未启用 (UNIHIVE_CONFIG_SOURCE=yaml)。
    """
    from starlette.responses import JSONResponse

    try:
        return JSONResponse(list_config_overlays())
    except ConfigStoreUnavailable as e:
        return JSONResponse(
            {"error": "config_store_unavailable", "message": str(e)}, status_code=503
        )


async def config_overlay_reset_api(req: "Request") -> "JSONResponse":
    """DELETE /api/config/overlays/{path} — 删除单行覆盖。

    body 可选 ``{"version": int}`` (乐观锁)。409 冲突 / 400 路径非法 /
    503 overlay 未启用; ``warnings`` 列出运行时热收敛失败但重启会收敛的路径。
    """
    from starlette.responses import JSONResponse

    path = req.path_params["path"]
    try:
        body = await req.json()
    except Exception:
        body = {}
    version = body.get("version") if isinstance(body, dict) else None
    try:
        result = await delete_config_overlay(path, expected_version=version)
    except VersionConflict as e:
        return JSONResponse(
            {"error": "version_conflict", "message": str(e)}, status_code=409
        )
    except ConfigStoreUnavailable as e:
        return JSONResponse(
            {"error": "config_store_unavailable", "message": str(e)}, status_code=503
        )
    except ValueError as e:
        return JSONResponse({"error": f"invalid request: {e}"[:300]}, status_code=400)
    return JSONResponse(result)


async def config_overlays_reset_all_api(req: "Request") -> "JSONResponse":
    """POST /api/config/overlays/reset — 一键清空全部覆盖行。

    body ``{"version": int}`` (乐观锁, 可空)。
    """
    from starlette.responses import JSONResponse

    try:
        body = await req.json()
    except Exception:
        body = {}
    version = body.get("version") if isinstance(body, dict) else None
    try:
        result = await reset_config_overlays(expected_version=version)
    except VersionConflict as e:
        return JSONResponse(
            {"error": "version_conflict", "message": str(e)}, status_code=409
        )
    except ConfigStoreUnavailable as e:
        return JSONResponse(
            {"error": "config_store_unavailable", "message": str(e)}, status_code=503
        )
    except ValueError as e:
        return JSONResponse({"error": f"invalid request: {e}"[:300]}, status_code=400)
    return JSONResponse(result)


def build_upstream_routes() -> list:
    """配置写路由 (挂载进 gateway_server)。路径参数用 {name}/{path} (Starlette 1.6)。"""
    from starlette.routing import Route

    return [
        Route(
            "/api/config/upstreams/{name}/enabled",
            upstream_enabled_api,
            methods=["PUT"],
        ),
        Route(
            "/api/config/overlays",
            config_overlays_list_api,
            methods=["GET"],
        ),
        # 静态路径必须排在 {path} 之前注册, 避免 "reset" 被当作 path 段
        Route(
            "/api/config/overlays/reset",
            config_overlays_reset_all_api,
            methods=["POST"],
        ),
        Route(
            "/api/config/overlays/{path}",
            config_overlay_reset_api,
            methods=["DELETE"],
        ),
    ]
