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

# 运行时资源根 (布局感知):
# - dev / editable: <repo>/src/unihive/utils/console_api.py → 向上 4 层 = repo 根
#   (标记: 该目录下有 console.html);
# - installed (非 editable wheel): site-packages/unihive/utils/console_api.py,
#   console.html + static/ 经 hatchling force-include 落在 site-packages (向上 3 层)。
# M1 (round 10 audit): 旧实现固定 4 parents, installed 下指向 venv 根 (多爬一层),
# 与网关侧 config_db.project_root() 不同根 → cache.db/config 读写 split-brain。
def resolve_runtime_root(file_path: Path) -> Path:
    """定位运行时资源根, dev 与 installed 两布局。以 console.html 为布局标记。"""
    f = Path(file_path).resolve()
    dev_root = f.parent.parent.parent.parent
    if (dev_root / "console.html").exists():
        return dev_root
    # installed: site-packages 根 (向上 3 层)
    return f.parent.parent.parent


_RUNTIME_ROOT = resolve_runtime_root(Path(__file__))
# 向后兼容别名 (旧消费方直接读 _PROJECT_ROOT)。
_PROJECT_ROOT = _RUNTIME_ROOT


def resolve_console_html_path(file_path: Path) -> Path:
    """L13 (round 9 audit): 定位 console.html, 兼容 dev 与 installed 布局。

    环境变量 UNIHIVE_CONSOLE_HTML 覆盖 (隔离实例)。
    """
    env = os.getenv("UNIHIVE_CONSOLE_HTML")
    if env:
        return Path(env)
    return resolve_runtime_root(file_path) / "console.html"


def resolve_static_dir(file_path: Path) -> Path:
    """H1 (round 10 audit): 定位 static/ 目录, 与 console.html 同一个运行时根。

    旧实现 gateway 挂载写死 ``_PROJECT_ROOT / 'static'`` (4 parents),
    installed 布局下目录不存在 → Starlette StaticFiles 构造期 raise, 网关拒启。
    环境变量 UNIHIVE_STATIC_DIR 覆盖。
    """
    env = os.getenv("UNIHIVE_STATIC_DIR")
    if env:
        return Path(env)
    return resolve_runtime_root(file_path) / "static"


STATIC_DIR = str(resolve_static_dir(Path(__file__)))
CONSOLE_HTML_PATH = str(resolve_console_html_path(Path(__file__)))
# 可经环境变量指向替代配置 (隔离实例/验证脚本用); 默认锚定运行时根, 与网关
# resolve_project_path / project_root 同根, 否则 PUT 落盘与运行态脱节。
GATEWAY_CONFIG_PATH = os.getenv(
    "UNIHIVE_UPSTREAMS_CONFIG",
    str(_RUNTIME_ROOT / "config" / "upstreams.yaml"),
)
CACHE_DB_PATH = str(_RUNTIME_ROOT / "logs" / "cache.db")

# 从配置文件读取，避免硬编码
_GW_CFG: dict | None = None
_INJECTED: bool = False

# SQLite 配置库 (data/config.db), 由 GatewayServer._load_db_config 装配后注入。
# 两种模式:
# - v2 实体模式 (_db_mode=True): DB 是配置事实源, 同步读路径直接用 gateway
#   注入的 live 装配 config (_GW_CFG, 同一引用), 写路径走实体 CRUD;
# - P1 薄覆盖层模式 (set_config_store 注入, 现仅测试/yaml 逃生模式使用):
#   YAML 是基线, 同步读路径合并 _overlay_rows 行镜像。
# None + 空镜像 = UNIHIVE_CONFIG_SOURCE=yaml 逃生模式, 配置写接口返回 503。
_config_store: Any = None
_db_mode: bool = False
_db_revision: int = 0
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
    """注入 P1 薄覆盖层 ConfigStore 与 overlay 行镜像 (测试/兼容入口)。

    rows 元素只需有 ``.path`` / ``.value`` (ConfigStore.OverlayRow)。
    传 ``store=None`` 进入 yaml 逃生模式 (stop/重置)。运行时网关改走
    :func:`set_config_db` (v2 实体模式); 两者互斥, 本函数会复位实体模式。
    """
    global _config_store, _overlay_rows, _overlay_revision, _db_mode, _db_revision
    _config_store = store
    _overlay_rows = list(rows or [])
    _overlay_revision = int(revision)
    _db_mode = False
    _db_revision = 0


def set_config_db(store: Any, revision: int = 0) -> None:
    """注入 v2 实体模式 ConfigStore (Gateway 启动时) 或复位 (store=None)。

    实体模式下 DB 是事实源: 同步读路径用 gateway 注入的 live 装配 config,
    写路径走 ``update_upstream_enabled`` / ``upsert_section`` 实体 CRUD。
    """
    global _config_store, _db_mode, _db_revision, _overlay_rows, _overlay_revision
    _config_store = store
    _db_mode = store is not None
    _db_revision = int(revision)
    # 实体模式不允许混入 P1 行镜像 (两套写路径互斥)。
    _overlay_rows = []
    _overlay_revision = int(revision)


def invalidate_config_caches() -> None:
    """实体热编辑/出厂更新后丢弃派生缓存 (interfaces 5s / mcp tools 30s)。

    与 5 个既有写站点同手法, 集中成一个入口供 GatewayServer 热编辑调用,
    避免新写路径漏清导致控制台/浏览器看到旧工具集。
    """
    _interfaces_cache.clear()
    _mcp_tools_cache.clear()


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


def _effective_runtime_config() -> dict | None:
    """v2 实体模式下 gateway 注入的 live 装配 config (同一引用), 否则 None。

    热开关/TTL 热改直接作用在这份 dict 上, 因此读它即读当前生效配置,
    零文件/DB IO; 未注入 (stdio 独立调用/单元测试) 时回落 None。
    """
    if _db_mode and _INJECTED and isinstance(_GW_CFG, dict):
        return _GW_CFG
    return None


def load_config() -> dict:
    """当前生效配置快照, 已脱敏, 带乐观锁 version。

    - v2 实体模式: gateway 启动时装配的 live config (库内实体 + config.yaml
      bootstrap) 即生效配置, 不回读 YAML。293 条 tool spec 由
      /api/interfaces 提供, /api/config 快照不重复携带 ``tools``。
      version 是 config.db 的整数 revision。
    - P1/yaml 模式: YAML 出厂基线 + overlay 行镜像, 零 DB IO; yaml 逃生
      模式 (store=None) version 回落到文件元信息 hash。
    """
    runtime = _effective_runtime_config()
    if runtime is not None:
        snapshot = {k: v for k, v in runtime.items() if k != "tools"}
        masked = _mask_config_secrets(snapshot)
        masked["version"] = _db_revision
        return masked

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
# FUYAO cache 运行时热同步
#
# fuyao_cache.tools 工具级 TTL 覆盖的编辑端点已于 2026-09-21 下线 (控制台
# 标签页移除); 配置段仍由 YAML 出厂模板/config.db 提供, 运行时经
# cache_strategy.fuyao_ttl_for 生效。
# ---------------------------------------------------------------------------


def _sync_runtime_fuyao_tools(tools: dict) -> None:
    """overlay 写入后热改 GatewayServer 注入的运行时 config (同一引用)。"""
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
    """持久化某上游 enabled 位。

    v2 实体模式: 直接更新 ``upstream`` 实体行 (DB 是事实源, 同值不 bump,
    未知上游 KeyError), 整数 revision 乐观锁。
    P1 薄覆盖层模式 (兼容/测试): 与出厂值相同 → 删除覆盖行 (恢复出厂),
    不同 → upsert ``upstreams.<name>.enabled``。
    两种模式都只落库; HTTP handler 随后调 GatewayServer.set_upstream_enabled()
    完成热增删 / 断连。
    """
    global _db_revision

    if not isinstance(enabled, bool):
        raise ValueError("'enabled' must be a boolean")
    store = _require_store()
    expected = _coerce_revision(expected_version)

    if _db_mode:
        try:
            revision = await store.update_upstream_enabled(
                name, bool(enabled), expected_revision=expected
            )
        except _StoreRevisionConflict as e:
            raise VersionConflict(
                f"config revision 不一致 (expected={e.expected}, actual={e.actual}); "
                "请刷新页面后重试"
            ) from e
        _db_revision = int(revision)
        _interfaces_cache.clear()
        _mcp_tools_cache.clear()
        return {"version": revision}

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


async def save_disable_dangerous(
    disabled: bool, *, expected_version: int | str | None = None
) -> dict:
    """持久化 dangerous kill switch (``disable_dangerous`` 标量 config_section)。

    db 实体模式: 作为顶层标量段入库 (db_assembler 装配时回铺顶层); 仅支持
    db 模式 —— yaml 逃生模式下该开关无实体可写。随后 HTTP handler 调
    GatewayServer.set_disable_dangerous() 完成热摘除/补回。
    """
    global _db_revision

    if not isinstance(disabled, bool):
        raise ValueError("'disabled' must be a boolean")
    store = _require_store()
    if not _db_mode:
        raise ConfigStoreUnavailable(
            "disable_dangerous kill switch 仅支持 config.db 实体模式 "
            "(UNIHIVE_CONFIG_SOURCE=yaml 逃生模式不可用)"
        )
    expected = _coerce_revision(expected_version)
    try:
        revision = await store.upsert_section(
            "disable_dangerous",
            bool(disabled),
            origin="user",
            expected_revision=expected,
        )
    except _StoreRevisionConflict as e:
        raise VersionConflict(
            f"config revision 不一致 (expected={e.expected}, actual={e.actual}); "
            "请刷新页面后重试"
        ) from e
    _db_revision = int(revision)
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


def _require_overlay_mode() -> None:
    """v2 实体模式下停用 P1 覆盖行接口 (config_overlay 表已与运行时解耦)。

    覆盖管理 UI 在 v2 切换时一并退役; 保留这层守卫避免旧前端/脚本误写
    一张运行时不再读取的表。
    """
    if _db_mode:
        raise ConfigStoreUnavailable(
            "v2 实体模式下覆盖行接口已停用, 配置事实源为实体表"
        )


def list_config_overlays() -> dict:
    """覆盖层全量行 (同步读镜像, 零 DB IO)。

    返回 ``{"revision": int, "overlays": [{path, value, revision, updated_at}]}``。
    """
    _require_overlay_mode()
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
    _require_overlay_mode()
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
    _require_overlay_mode()
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


def _cache_stats_path() -> Path:
    """cache_stats.json 锚定运行时根 (与 Cache._stats_path 的 project_root
    同根)。旧实现写死 CWD 相对路径, CWD 变化时读不到网关写入的统计 →
    命中率静默变 None (裂脑)。"""
    return _RUNTIME_ROOT / "logs" / "cache_stats.json"


def _query_cache_row_counts(cur, now_ts: int) -> dict:
    """条目聚合: 总数 / 活跃 / 过期 / 无过期行 / 最快到期 / 最旧写入。"""
    cur.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(CASE WHEN expires_at IS NOT NULL AND expires_at > :now
                              THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN expires_at IS NOT NULL AND expires_at <= :now
                              THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN expires_at IS NULL
                              THEN 1 ELSE 0 END), 0),
            MIN(CASE WHEN expires_at > :now THEN expires_at END),
            MIN(created_at)
        FROM cache
        """,
        {"now": now_ts},
    )
    (
        total,
        live,
        expired,
        null_expiry,
        nearest_expiry,
        oldest_created,
    ) = cur.fetchone()
    return {
        "entries": total,
        "live_entries": live,
        "expired_entries": expired,
        "null_expiry_entries": null_expiry,
        "nearest_expiry": nearest_expiry,
        "oldest_created": oldest_created,
    }


def get_cache_breakdown(limit: int = 20) -> list[dict]:
    """按工具 (cache key 的 ``name:`` 前缀) 聚合条目数, 降序 Top N。"""
    import sqlite3

    now_ts = int(time.time())
    conn = sqlite3.connect(CACHE_DB_PATH, timeout=5)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                CASE WHEN instr(key, ':') > 0
                     THEN substr(key, 1, instr(key, ':') - 1)
                     ELSE key END AS tool,
                COUNT(*) AS entries,
                COALESCE(SUM(CASE WHEN expires_at IS NOT NULL AND expires_at > :now
                                  THEN 1 ELSE 0 END), 0) AS live
            FROM cache
            GROUP BY tool
            ORDER BY entries DESC
            LIMIT :lim
            """,
            {"now": now_ts, "lim": limit},
        )
        return [
            {"tool": tool, "entries": entries, "live": live}
            for tool, entries, live in cur.fetchall()
        ]
    finally:
        conn.close()


def get_cache_stats() -> dict:
    cache_path = Path(CACHE_DB_PATH)
    stats_path = _cache_stats_path()
    if not cache_path.exists():
        return {"enabled": False, "error": "Cache DB not found", "timestamp": int(time.time())}
    try:
        size_bytes = cache_path.stat().st_size
        row_counts: dict = {
            "entries": None,
            "live_entries": None,
            "expired_entries": None,
            "null_expiry_entries": None,
            "nearest_expiry": None,
            "oldest_created": None,
        }
        freelist_count = None
        page_count = None
        page_size = None
        try:
            import sqlite3

            conn = sqlite3.connect(CACHE_DB_PATH, timeout=5)
            try:
                cursor = conn.cursor()
                row_counts = _query_cache_row_counts(cursor, int(time.time()))
                # 存储健康度: freelist 是 DELETE 后未回收的死页, VACUUM 可回收
                cursor.execute("PRAGMA freelist_count")
                freelist_count = cursor.fetchone()[0]
                cursor.execute("PRAGMA page_count")
                page_count = cursor.fetchone()[0]
                cursor.execute("PRAGMA page_size")
                page_size = cursor.fetchone()[0]
            finally:
                conn.close()
        except Exception:
            pass
        # WAL 增量页: checkpoint 前先落 cache.db-wal
        wal_path = cache_path.with_name(cache_path.name + "-wal")
        wal_size_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        hits = None
        misses = None
        hit_rate = None
        stats_updated_at = None
        stats_pid = None
        if stats_path.exists():
            try:
                with open(stats_path, encoding="utf-8") as f:
                    s = json.load(f)
                hits = int(s.get("hits", 0))
                misses = int(s.get("misses", 0))
                total = hits + misses
                hit_rate = (hits / total) if total > 0 else None
                stats_updated_at = s.get("updated_at")
                stats_pid = s.get("pid")
            except Exception:
                pass
        return {
            "enabled": True,
            **row_counts,
            "db_size_mb": round(size_bytes / (1024 * 1024), 3),
            "wal_size_bytes": wal_size_bytes,
            "freelist_count": freelist_count,
            "page_count": page_count,
            "page_size": page_size,
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "stats_updated_at": stats_updated_at,
            "stats_pid": stats_pid,
            "timestamp": int(time.time()),
        }
    except Exception as e:
        return {"enabled": False, "error": str(e), "timestamp": int(time.time())}


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def _live_cache() -> Any:
    """网关活实例上的 Cache (经注入的 gateway_server 解析)。"""
    server = _gateway_server
    return getattr(server, "cache", None) if server is not None else None


async def run_cache_cleanup() -> int:
    """立即触发过期条目清理, 返回删除条数。"""
    cache = _live_cache()
    if cache is None:
        raise RuntimeError("live cache unavailable")
    return int(await cache.cleanup_expired())


async def run_cache_clear() -> bool:
    """清空全部缓存条目。"""
    cache = _live_cache()
    if cache is None:
        raise RuntimeError("live cache unavailable")
    return bool(await cache.clear())


def _vacuum_cache_sync(db_path: str) -> None:
    """VACUUM 回收死页 (sqlite3 直连)。

    VACUUM 不能在事务内执行: isolation_level=None 走 autocommit;
    先 wal_checkpoint(TRUNCATE) 把 WAL 收回主库再 VACUUM。
    """
    import sqlite3

    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.isolation_level = None
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
    finally:
        conn.close()


async def run_cache_vacuum() -> dict:
    """整理缓存数据库, 返回新的文件大小。"""
    if not Path(CACHE_DB_PATH).exists():
        raise RuntimeError("cache db not found")
    await asyncio.to_thread(_vacuum_cache_sync, CACHE_DB_PATH)
    size_bytes = Path(CACHE_DB_PATH).stat().st_size
    wal_path = Path(CACHE_DB_PATH + "-wal")
    return {
        "db_size_mb": round(size_bytes / (1024 * 1024), 3),
        "wal_size_bytes": wal_path.stat().st_size if wal_path.exists() else 0,
    }


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


# L9 (round 9 audit): probe executor 改惰性单例 (仿 blocking_pool)。
# 旧实现在模块级 eager 构造 + _shutdown_probe_executor 不置 None →
# stop() 关掉后 get_upstream_status 仍向已关闭的 executor submit, 抛
# RuntimeError; 同进程 restart 后状态页直接崩。惰性 getter 关后置 None,
# 下次取重建。
_probe_executor: ThreadPoolExecutor | None = None


def _get_probe_executor() -> ThreadPoolExecutor:
    """返回进程内共享的 probe executor (惰性创建, 关后可重建)。"""
    global _probe_executor
    if _probe_executor is None:
        _probe_executor = ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="upstream-probe"
        )
    return _probe_executor


def _shutdown_probe_executor() -> None:
    """进程退出 / GatewayServer.stop() 时关掉 probe executor, 避免 worker 线程泄漏。

    LOW-1 (2026-09-14 audit): 之前挂在 atexit 上, 但 stdio transport
    下 FastMCP 可能拦截退出流程, atexit 不保证触发。GatewayServer.stop()
    现在显式调一次, 保证 worker 线程在正常退出路径下被关掉。
    L9 (round 9 audit): 关后置 None, 让 _get_probe_executor 下次惰性重建,
    支持同进程 stop/start 循环。
    """
    global _probe_executor
    if _probe_executor is not None:
        try:
            _probe_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _probe_executor = None


# 保留 atexit 兜底: standalone 使用 (无 gateway_server) 仍依赖它。
atexit.register(_shutdown_probe_executor)


def get_upstream_status() -> dict:
    config = load_config()
    upstreams_cfg = {
        name: cfg for name, cfg in config.get("upstreams", {}).items() if isinstance(cfg, dict)
    }
    futures = {name: _get_probe_executor().submit(_probe_upstream, name, cfg) for name, cfg in upstreams_cfg.items()}
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
        "pid": os.getpid(),
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

    5s TTL 缓存: yaml 模式下 load_all_tools 要遍历 YAML + 跑 codegen,
    每次 /api/interfaces 都重跑是浪费; db 模式只读 live 装配 config。
    前端每几秒轮询时尤其明显。
    """
    cached = _interfaces_cache.get(_INTERFACES_CACHE_KEY)
    if cached is not None:
        return cached  # type: ignore[return-value]

    config_path = Path(GATEWAY_CONFIG_PATH)
    runtime = _effective_runtime_config()
    from ..core.tool_loader import (
        filter_specs_by_env,
        get_cache_ttl,
        derive_routing_chain,
        load_all_tools,
    )

    if runtime is not None:
        # v2 实体模式: 293 条 spec 已在装配 config 中, 只补运行时 env 门控,
        # 不回读 tools_*.yaml; 用未脱敏 live config 算 TTL/chain。
        config = runtime
        tools = filter_specs_by_env(list(runtime.get("tools") or []))
    else:
        config = load_config()
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
    """构建 routing_key → upstream 的映射。

    v2 实体模式: mapping 已在装配 config 中按工具行重建, 直接读 live config;
    yaml/P1 模式: load_all_tools 会把 tools_*.yaml 中的 mapping 合并进 config。
    """
    runtime = _effective_runtime_config()
    if runtime is not None:
        mapping = runtime.get("upstream_tool_mapping") or {}
    else:
        config = load_config()
        from pathlib import Path

        from ..core.tool_loader import load_all_tools

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


async def _read_json_object(req: "Request"):
    """边界解析 POST JSON 体。

    返回 ``(obj, None)`` 或 ``(None, JSONResponse)``:
    坏 JSON / 非对象 (数组、字符串、数字、null) 一律 400,
    不让 ``body.get`` 的 AttributeError 以 500 漏出去。
    """
    from starlette.responses import JSONResponse

    try:
        body = await req.json()
    except Exception:
        return None, JSONResponse({"error": "请求体必须是 JSON"}, status_code=400)
    if not isinstance(body, dict):
        return None, JSONResponse(
            {"error": "请求体必须是 JSON 对象"}, status_code=400
        )
    return body, None


# 查询参数整数上限: 超大值 (如 999999999999999999999) 过了 int() 后会在
# sqlite 绑定阶段溢出 int64 变成 500; 在边界直接 400。2^31-1 对任何分页/
# 页码场景都绰绰有余。
_MAX_POSITIVE_INT_PARAM = 2_147_483_647


def _positive_int_param(params, key: str, default: int):
    """解析正整数查询参数。返回 ``(value, None)`` 或 ``(None, JSONResponse)``。"""
    from starlette.responses import JSONResponse

    raw = params.get(key)
    if raw is None:
        return default, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, JSONResponse(
            {"error": f"{key} 必须是正整数"}, status_code=400
        )
    if value < 1 or value > _MAX_POSITIVE_INT_PARAM:
        return None, JSONResponse(
            {
                "error": (
                    f"{key} 必须是 1..{_MAX_POSITIVE_INT_PARAM} 之间的正整数"
                )
            },
            status_code=400,
        )
    return value, None


def build_omni_routes() -> list:
    """Build OMNI routes for gateway to mount."""
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    from starlette.requests import Request

    async def omni_stats_api(req: Request):
        return JSONResponse(get_omni_stats())

    async def omni_query_api(req: Request):
        params = req.query_params
        page, bad = _positive_int_param(params, "page", 1)
        if bad is not None:
            return bad
        page_size, bad = _positive_int_param(params, "page_size", 50)
        if bad is not None:
            return bad
        return JSONResponse(query_omni_sectors(
            source=params.get("source", ""),
            board_type=params.get("board_type", ""),
            keyword=params.get("keyword", ""),
            page=page,
            page_size=page_size,
        ))

    async def omni_sync_api(req: Request):
        body, bad = await _read_json_object(req)
        if bad is not None:
            return bad
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
        # L5 (round 9 audit): 删除死路由 "/api/upstreams/<name>/metrics"
        # (Flask 语法 <name>, Starlette 1.6 编译出 params={} 永不匹配)。
        # 真正工作的是 gateway_server 里 {name} 那条; handler 保留供其挂载。
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
    except Exception:
        # 细节 (可能含内部路径/连接串) 只进日志, 对外固定错误码。
        logger.exception("upstream_metrics_api(%s) raised", name)
        return JSONResponse(
            {"error": "metrics_read_failed"},
            status_code=500,
        )
    return JSONResponse({
        "upstream": name,
        "metrics": metrics,
        "timestamp": int(time.time()),
    })


async def upstream_l1_reset_api(req: "Request") -> "JSONResponse":
    """POST /api/upstreams/<name>/l1/reset — 手动清空上游 L1 内存缓存。

    状态码语义同 upstream_metrics_api: 200 已清 / 404 上游未注册 /
    501 客户端未实现 reset_l1_caches() / 500 内部异常。
    """
    from starlette.responses import JSONResponse

    name = req.path_params["name"]
    client = _get_upstream_client(name)
    if client is None:
        return JSONResponse(
            {"error": f"upstream '{name}' not registered"},
            status_code=404,
        )
    reset = getattr(client, "reset_l1_caches", None)
    if not callable(reset):
        return JSONResponse(
            {"error": f"upstream '{name}' does not expose L1 reset"},
            status_code=501,
        )
    try:
        result = reset()
    except Exception:
        # 细节只进日志, 对外固定错误码
        logger.exception("upstream_l1_reset_api(%s) raised", name)
        return JSONResponse(
            {"error": "l1_reset_failed"},
            status_code=500,
        )
    return JSONResponse({
        "upstream": name,
        "result": result,
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
    except Exception:
        # overlay 已落库但热切换失败: 不回滚 (重启即收敛到目标态),
        # 返回 500 + 新 version, 让前端提示用户可重试或重启。
        # 异常文本可能含内部路径/连接细节, 只进日志, 对外固定错误码。
        logger.exception("set_upstream_enabled(%r, %s) failed", name, enabled)
        return JSONResponse(
            {
                "error": "runtime_toggle_failed",
                "version": persisted["version"],
            },
            status_code=500,
        )

    return JSONResponse({**runtime, "version": persisted["version"]})


async def disable_dangerous_api(req: "Request") -> "JSONResponse":
    """PUT /api/config/disable-dangerous

    body: ``{"disabled": bool, "version": int}`` —— 先把标量段写 config.db,
    再调 GatewayServer 热摘除/补回全部 dangerous 工具。

    状态码:
      200: 成功 (body 含 disabled/tools_added/tools_removed/version)
      400: body 非法或 disabled 非布尔
      409: 乐观锁 version 不匹配
      500: 运行时热切换部分失败 (已落库, body 带 version; 重试可自愈)
      503: config.db 实体模式未启用 / GatewayServer 未注入
    """
    from starlette.responses import JSONResponse

    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    disabled = body.get("disabled")
    if not isinstance(disabled, bool):
        return JSONResponse(
            {"error": "'disabled' must be a boolean (true/false)"},
            status_code=400,
        )

    try:
        persisted = await save_disable_dangerous(
            disabled, expected_version=body.get("version")
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
    except ValueError as e:
        logger.warning("save_disable_dangerous(%s) bad request: %s", disabled, e)
        msg = f"invalid request: {type(e).__name__}: {e}"[:300]
        return JSONResponse({"error": msg}, status_code=400)

    server = _gateway_server
    if server is None:
        return JSONResponse(
            {"error": "gateway runtime not available", "version": persisted["version"]},
            status_code=503,
        )

    try:
        runtime = await server.set_disable_dangerous(disabled)
    except Exception:
        # 已落库但热切换失败: 不回滚 (重启即收敛), 对外固定错误码。
        logger.exception("set_disable_dangerous(%s) failed", disabled)
        return JSONResponse(
            {"error": "runtime_toggle_failed", "version": persisted["version"]},
            status_code=500,
        )

    return JSONResponse({**runtime, "version": persisted["version"]})


async def config_entities_api(req: "Request") -> "JSONResponse":
    """GET /api/config/entities — v2 实体表只读浏览摘要。

    db 模式: revision / 出厂模板版本与导入时间 / 每个 upstream 实体行的
    origin+updated_at / section 列表 / 工具按 origin 聚合计数。
    yaml 逃生模式: 200 + ``{"source": "yaml"}`` (前端只渲染提示横幅)。
    """
    from starlette.responses import JSONResponse

    if not _db_mode or _config_store is None:
        return JSONResponse({"source": "yaml"})
    try:
        summary = await _config_store.entities_summary()
    except Exception:  # noqa: BLE001 - 只读浏览接口, 错误不外泄堆栈/路径
        logger.exception("config entities summary failed")
        return JSONResponse({"error": "entities_load_failed"}, status_code=500)
    return JSONResponse({"source": "db", **summary})


async def config_tool_detail_api(req: "Request") -> "JSONResponse":
    """GET /api/config/tools/{name} — 单个工具实体详情 (编辑表单数据源)。

    200: ``{"source":"db","revision":N,"tool":{...完整行...}}``
    200: ``{"source":"yaml"}`` (逃生模式, 前端不打开编辑入口)
    404: 工具名不存在
    """
    from starlette.responses import JSONResponse

    if not _db_mode or _config_store is None:
        return JSONResponse({"source": "yaml"})
    name = req.path_params["name"]
    try:
        tool = await _config_store.get_tool(name)
        revision = await _config_store.revision()
    except Exception:  # noqa: BLE001 - 不外泄堆栈/内部路径或 SQL
        logger.exception("config tool detail failed: %s", name)
        return JSONResponse({"error": "tool_load_failed"}, status_code=500)
    if tool is None:
        return JSONResponse({"error": f"unknown tool: {name}"}, status_code=404)
    return JSONResponse({"source": "db", "revision": revision, "tool": tool})


async def config_upstream_detail_api(req: "Request") -> "JSONResponse":
    """GET /api/config/upstreams/{name} — 单个上游实体详情 (编辑表单数据源)。

    与 /api/config 快照不同, 这里回 config_json **实体原值** (密钥是
    ``${VAR}`` 占位符, 不脱敏) — 连接块整体替换编辑必须基于实体值,
    否则会把脱敏串当明文密钥写回并被写入门拒绝。

    200: ``{"source":"db","revision":N,"upstream":{...完整行...}}``
    200: ``{"source":"yaml"}`` (逃生模式, 前端不打开编辑入口)
    404: 上游名不存在
    """
    from starlette.responses import JSONResponse

    if not _db_mode or _config_store is None:
        return JSONResponse({"source": "yaml"})
    name = req.path_params["name"]
    try:
        upstream = await _config_store.get_upstream(name)
        revision = await _config_store.revision()
    except Exception:  # noqa: BLE001 - 不外泄堆栈/内部路径或 SQL
        logger.exception("config upstream detail failed: %s", name)
        return JSONResponse({"error": "upstream_load_failed"}, status_code=500)
    if upstream is None:
        return JSONResponse({"error": f"unknown upstream: {name}"}, status_code=404)
    return JSONResponse({"source": "db", "revision": revision, "upstream": upstream})


def _parse_revision(body: dict):
    """从请求体取可选乐观锁 revision; 返回 (revision, error_response)。"""
    from starlette.responses import JSONResponse

    revision = body.get("revision")
    if revision is None:
        return None, None
    if isinstance(revision, bool) or not isinstance(revision, int):
        return None, JSONResponse(
            {"error": "revision must be an integer when provided"}, status_code=400
        )
    return revision, None


def _entity_edit_error_response(exc: Exception) -> "JSONResponse":
    """热编辑异常 → HTTP 状态码映射 (tool / upstream 两个写接口共用)。"""
    from starlette.responses import JSONResponse

    from ..core.config_lint import LintRejected
    from ..core.entity_edit import DangerousConfirmationRequired

    if isinstance(exc, DangerousConfirmationRequired):
        return JSONResponse(
            {
                "error": "dangerous_confirmation_required",
                "message": str(exc),
                "contract_fields": list(exc.contract_fields),
            },
            status_code=400,
        )
    if isinstance(exc, LintRejected):
        return JSONResponse(
            {
                "error": "lint_rejected",
                "message": str(exc),
                "issues": [
                    {
                        "severity": issue.severity,
                        "code": issue.code,
                        "message": issue.message,
                    }
                    for issue in exc.issues
                ],
            },
            status_code=422,
        )
    if isinstance(exc, _StoreRevisionConflict):
        return JSONResponse(
            {
                "error": "revision_conflict",
                "message": str(exc),
                "expected": exc.expected,
                "actual": exc.actual,
            },
            status_code=409,
        )
    if isinstance(exc, KeyError):
        return JSONResponse({"error": f"unknown entity: {exc}"[:300]}, status_code=404)
    if isinstance(exc, ValueError):
        # EntityEditError / LintRejected 之外的形态错误 (明文密钥等)。
        logger.warning("entity edit rejected: %s: %s", type(exc).__name__, exc)
        return JSONResponse(
            {"error": f"invalid request: {type(exc).__name__}: {exc}"[:300]},
            status_code=400,
        )
    if isinstance(exc, RuntimeError) and "config.db" in str(exc):
        return JSONResponse(
            {"error": "config_store_unavailable", "message": str(exc)},
            status_code=503,
        )
    logger.exception("entity edit failed")
    # M2: 热路径 500 (运行态切换/回滚失败等) 带上库内当前 revision, 前端据此
    # 判断是否需要整表刷新 —— 此时库通常未变, 但调用方持有的可能是旧快照。
    envelope = {"error": f"runtime failure: {type(exc).__name__}: {exc}"[:300]}
    if _db_mode:
        envelope["revision"] = _db_revision
    return JSONResponse(envelope, status_code=500)


async def config_tool_update_api(req: "Request") -> "JSONResponse":
    """PUT /api/config/tools/{name} — 单工具白名单编辑 + 热重注册。

    body: ``{"patch": {字段白名单}, "confirm_dangerous"?: bool,
    "revision"?: int}``

    状态码:
      200: 成功 (body 为 apply_tool_edit 结果; changed=false 为 no-op)
      400: JSON/body 非法, 或契约层字段缺 confirm (带 contract_fields)
      404: 工具不存在
      409: revision 过期
      422: lint 门拒绝 (body.issues 列出全部 error)
      503: yaml 逃生模式 / GatewayServer 未注入
    """
    from starlette.responses import JSONResponse

    name = req.path_params["name"]
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    patch = body.get("patch")
    if not isinstance(patch, dict) or not patch:
        return JSONResponse(
            {"error": "patch must be a non-empty object of whitelisted fields"},
            status_code=400,
        )
    revision, error_response = _parse_revision(body)
    if error_response is not None:
        return error_response
    if not _db_mode or _config_store is None:
        return JSONResponse(
            {"error": "config_store_unavailable (UNIHIVE_CONFIG_SOURCE=yaml)"},
            status_code=503,
        )
    server = _gateway_server
    if server is None:
        return JSONResponse(
            {"error": "gateway runtime not available (stdio mode)"}, status_code=503
        )

    try:
        result = await server.apply_tool_edit(
            name,
            patch,
            confirm_dangerous=bool(body.get("confirm_dangerous")),
            expected_revision=revision,
        )
    except Exception as e:  # noqa: BLE001 - 统一映射见 _entity_edit_error_response
        return _entity_edit_error_response(e)
    return JSONResponse(result)


async def config_upstream_update_api(req: "Request") -> "JSONResponse":
    """PUT /api/config/upstreams/{name} — 整体替换上游连接块 (热重连)。

    body: ``{"config": {异型连接块, 不含 enabled/type}, "type"?: str,
    "confirm_dangerous": bool, "revision"?: int}``。连接块是建连契约,
    本接口始终要求确认; 明文密钥字段 400 拒绝。
    """
    from starlette.responses import JSONResponse

    name = req.path_params["name"]
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    config = body.get("config")
    if not isinstance(config, dict):
        return JSONResponse(
            {"error": "config must be an object of connection settings"},
            status_code=400,
        )
    upstream_type = body.get("type")
    if upstream_type is not None and (
        not isinstance(upstream_type, str) or not upstream_type
    ):
        return JSONResponse(
            {"error": "type must be a non-empty string when provided"},
            status_code=400,
        )
    revision, error_response = _parse_revision(body)
    if error_response is not None:
        return error_response
    if not _db_mode or _config_store is None:
        return JSONResponse(
            {"error": "config_store_unavailable (UNIHIVE_CONFIG_SOURCE=yaml)"},
            status_code=503,
        )
    server = _gateway_server
    if server is None:
        return JSONResponse(
            {"error": "gateway runtime not available (stdio mode)"}, status_code=503
        )

    try:
        result = await server.apply_upstream_edit(
            name,
            config,
            type=upstream_type,
            confirm_dangerous=bool(body.get("confirm_dangerous")),
            expected_revision=revision,
        )
    except Exception as e:  # noqa: BLE001 - 统一映射见 _entity_edit_error_response
        return _entity_edit_error_response(e)
    return JSONResponse(result)


async def config_template_status_api(req: "Request") -> "JSONResponse":
    """GET /api/config/template-status — 出厂 YAML 模板相对库内快照是否已变化。

    只给信号 (hash 比对), **不**自动升级: 是否吸收出厂更新由用户在
    「出厂更新中心」审阅字段级 diff 后决定 (设计决策④)。

    200: db 模式 ``{source, revision, seeded_version, current_version,
    template_changed, factory_counts}``; YAML 重建失败 (语法/明文密钥)
    时 200 带 ``build_error``, 由面板提示; yaml 逃生模式回 ``{source:"yaml"}``。
    """
    from starlette.responses import JSONResponse

    if not _db_mode or _config_store is None:
        return JSONResponse({"source": "yaml"})

    def _build_factory() -> tuple[str, int, int, int]:
        from ..core.config_db import build_template
        from ..core.template_normalize import template_version as _tv

        template = build_template(GATEWAY_CONFIG_PATH)
        return (
            _tv(template),
            len(template.upstreams),
            len(template.tools),
            len(template.sections),
        )

    try:
        revision = await _config_store.revision()
        seeded_version = await _config_store.template_version_seeded()
        current_version, n_upstreams, n_tools, n_sections = await asyncio.to_thread(
            _build_factory
        )
    except Exception as e:  # noqa: BLE001 - 不外泄堆栈, 面板内展示
        logger.exception("template-status: factory template build failed")
        return JSONResponse(
            {
                "source": "db",
                "revision": locals().get("revision"),
                "seeded_version": locals().get("seeded_version"),
                "current_version": None,
                "template_changed": False,
                "build_error": f"{type(e).__name__}: {e}"[:300],
            }
        )
    return JSONResponse(
        {
            "source": "db",
            "revision": revision,
            "seeded_version": seeded_version,
            "current_version": current_version,
            "template_changed": seeded_version != current_version,
            "factory_counts": {
                "upstreams": n_upstreams,
                "tools": n_tools,
                "sections": n_sections,
            },
        }
    )


def _parse_resolutions(body: dict):
    """从请求体取 resolutions 并交核心层校验形态; 返回 (dict, error_response)。"""
    from starlette.responses import JSONResponse

    resolutions = body.get("resolutions")
    if resolutions is None:
        return {}, None
    if not isinstance(resolutions, dict):
        return None, JSONResponse(
            {"error": "resolutions must be an object keyed by conflict path"},
            status_code=400,
        )
    try:
        from ..core.config_db import validate_resolutions

        return validate_resolutions(resolutions), None
    except ValueError as e:
        return None, JSONResponse({"error": str(e)[:300]}, status_code=400)


async def config_upgrade_dry_run_api(req: "Request") -> "JSONResponse":
    """POST /api/config/upgrade/dry-run — 出厂更新字段级三方合并预览 (零写入)。

    body: ``{"resolutions"?: {"<kind>:<name>:<path>": "keep_cur"|"accept_new"}}``

    返回 server.preview_config_upgrade 结果: 逐实体台账 + 冲突三向值
    (old/cur/new) + lint + 运行态影响面 (热增删/重注册工具、重连上游)。
    """
    from starlette.responses import JSONResponse

    try:
        body = await req.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    resolutions, error_response = _parse_resolutions(body)
    if error_response is not None:
        return error_response
    if not _db_mode or _config_store is None:
        return JSONResponse(
            {"error": "config_store_unavailable (UNIHIVE_CONFIG_SOURCE=yaml)"},
            status_code=503,
        )
    server = _gateway_server
    if server is None:
        return JSONResponse(
            {"error": "gateway runtime not available (stdio mode)"}, status_code=503
        )
    try:
        result = await server.preview_config_upgrade(resolutions)
    except Exception as e:  # noqa: BLE001 - 统一映射见 _entity_edit_error_response
        return _entity_edit_error_response(e)
    return JSONResponse(result)


async def config_upgrade_apply_api(req: "Request") -> "JSONResponse":
    """POST /api/config/upgrade/apply — 热应用出厂更新 (需显式确认)。

    body: ``{"confirm": true, "resolutions"?: {...}, "revision"?: int}``。
    未裁决冲突 400、lint 不过 422、revision 过期 409; 成功体附带备份文件路径。
    """
    from starlette.responses import JSONResponse

    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    if body.get("confirm") is not True:
        return JSONResponse(
            {
                "error": "dangerous_confirmation_required",
                "message": (
                    "factory upgrade rewrites config entities and hot re-registers "
                    "tools; resend with {\"confirm\": true} after reviewing the dry-run"
                ),
            },
            status_code=400,
        )
    resolutions, error_response = _parse_resolutions(body)
    if error_response is not None:
        return error_response
    revision, error_response = _parse_revision(body)
    if error_response is not None:
        return error_response
    if not _db_mode or _config_store is None:
        return JSONResponse(
            {"error": "config_store_unavailable (UNIHIVE_CONFIG_SOURCE=yaml)"},
            status_code=503,
        )
    server = _gateway_server
    if server is None:
        return JSONResponse(
            {"error": "gateway runtime not available (stdio mode)"}, status_code=503
        )
    try:
        result = await server.apply_config_upgrade(
            resolutions=resolutions,
            expected_revision=revision,
        )
    except Exception as e:  # noqa: BLE001 - 统一映射见 _entity_edit_error_response
        return _entity_edit_error_response(e)
    return JSONResponse(result)


async def config_upgrade_backup_api(req: "Request") -> "JSONResponse":
    """GET /api/config/upgrade/backup/latest — 下载最近一份升级前 JSON 备份。

    备份目录固定为 config.db 同级 ``backups/`` (不从请求取路径, 杜绝目录穿越);
    无备份 404。
    """
    from starlette.responses import FileResponse, JSONResponse

    if not _db_mode or _config_store is None:
        return JSONResponse(
            {"error": "config_store_unavailable (UNIHIVE_CONFIG_SOURCE=yaml)"},
            status_code=503,
        )
    backup_dir = Path(str(_config_store.db_path)).parent / "backups"
    try:
        files = sorted(
            backup_dir.glob("config-upgrade-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        files = []
    if not files:
        return JSONResponse({"error": "no upgrade backup found"}, status_code=404)
    latest = files[0]
    return FileResponse(
        str(latest), media_type="application/json", filename=latest.name
    )


# ---------------------------------------------------------------------------
# P4: FQGate OpenAPI 实时导入 (活拉上游 openapi.json + tools/list → origin=user)
# ---------------------------------------------------------------------------

DEFAULT_IMPORT_UPSTREAM = "THS-LOCAL-GATEWAY"


def _parse_import_tools(body: dict):
    """校验 import body 的 upstream/tools 白名单 → (kwargs, error_response)。"""
    from starlette.responses import JSONResponse

    upstream = body.get("upstream", DEFAULT_IMPORT_UPSTREAM)
    if not isinstance(upstream, str) or not upstream.strip():
        return None, JSONResponse(
            {"error": "upstream must be a non-empty string"}, status_code=400
        )
    tools = body.get("tools")
    if tools is None:
        selected: list[str] | None = None
    elif (
        not isinstance(tools, list)
        or not tools
        or not all(isinstance(t, str) and t.strip() for t in tools)
    ):
        return None, JSONResponse(
            {"error": "tools must be a non-empty array of non-empty strings"},
            status_code=400,
        )
    else:
        if len(set(tools)) != len(tools):
            return None, JSONResponse(
                {"error": "tools must not contain duplicates"}, status_code=400
            )
        selected = tools
    return {"upstream": upstream, "tools": selected}, None


def _openapi_import_error_response(exc: Exception) -> "JSONResponse":
    """OpenAPI 导入专用状态码: 上游拉取 502 / 未连接未初始化 503, 其余走通用映射。"""
    from starlette.responses import JSONResponse

    from ..api.ths_local_gateway_client import OpenApiFetchError

    message = str(exc)
    if isinstance(exc, OpenApiFetchError):
        return JSONResponse(
            {"error": "openapi_fetch_failed", "message": message[:300]},
            status_code=502,
        )
    if isinstance(exc, RuntimeError) and (
        "Gateway not initialized" in message
        or "not connected" in message
        or "tools/list failed" in message
    ):
        # tools/list 失败是上游侧问题 → 502; 本地未就绪/未连接 → 503。
        status = 502 if "tools/list failed" in message else 503
        error_code = "upstream_unavailable" if status == 502 else "gateway_unavailable"
        return JSONResponse(
            {"error": error_code, "message": message[:300]}, status_code=status
        )
    return _entity_edit_error_response(exc)


async def config_import_preview_api(req: "Request") -> "JSONResponse":
    """POST /api/config/import/preview — 活拉上游 OpenAPI 生成导入计划 (零写入)。

    body: ``{"upstream"?: "THS-LOCAL-GATEWAY", "tools"?: ["fqgate_...", ...]}``。
    返回 server.preview_openapi_import 结果: added/changed/unmapped/removed
    台账 + lint + 发布影响面 + 可评审的 YAML 片段。

    状态码: 200 / 400 body 或白名单非法 / 404 上游未注册 / 422 lint /
    502 上游拉取失败 / 503 yaml 逃生模式、网关未注入、上游未连接。
    """
    from starlette.responses import JSONResponse

    try:
        body = await req.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    kwargs, error_response = _parse_import_tools(body)
    if error_response is not None:
        return error_response
    if not _db_mode or _config_store is None:
        return JSONResponse(
            {"error": "config_store_unavailable (UNIHIVE_CONFIG_SOURCE=yaml)"},
            status_code=503,
        )
    server = _gateway_server
    if server is None:
        return JSONResponse(
            {"error": "gateway runtime not available (stdio mode)"}, status_code=503
        )
    try:
        result = await server.preview_openapi_import(**kwargs)
    except Exception as e:  # noqa: BLE001 - 映射见 _openapi_import_error_response
        return _openapi_import_error_response(e)
    return JSONResponse(result)


async def config_import_apply_api(req: "Request") -> "JSONResponse":
    """POST /api/config/import/apply — 热应用 OpenAPI 导入 (契约层强确认)。

    body: ``{"upstream"?: ..., "tools"?: [...], "confirm_dangerous": true,
    "revision"?: int}``。导入工具以 origin='user' 单事务写 config.db 并即时
    热注册; provider 切换失败/乐观锁冲突 → 运行态回滚, 库不动, 映射 500/409。
    """
    from starlette.responses import JSONResponse

    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    if body.get("confirm_dangerous") is not True:
        return JSONResponse(
            {
                "error": "dangerous_confirmation_required",
                "message": (
                    "OpenAPI import rewrites tool contract entities "
                    "(params/mapping/routing) and hot re-registers tools; "
                    'resend with {"confirm_dangerous": true} after reviewing '
                    "the preview and its YAML fragment"
                ),
            },
            status_code=400,
        )
    kwargs, error_response = _parse_import_tools(body)
    if error_response is not None:
        return error_response
    revision, error_response = _parse_revision(body)
    if error_response is not None:
        return error_response
    if not _db_mode or _config_store is None:
        return JSONResponse(
            {"error": "config_store_unavailable (UNIHIVE_CONFIG_SOURCE=yaml)"},
            status_code=503,
        )
    server = _gateway_server
    if server is None:
        return JSONResponse(
            {"error": "gateway runtime not available (stdio mode)"}, status_code=503
        )
    try:
        result = await server.apply_openapi_import(
            upstream=kwargs["upstream"],
            tools=kwargs["tools"],
            confirm_dangerous=True,
            expected_revision=revision,
        )
    except Exception as e:  # noqa: BLE001 - 映射见 _openapi_import_error_response
        return _openapi_import_error_response(e)
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
        # dangerous kill switch (顶层标量段, 热批量摘除/补回)
        Route(
            "/api/config/disable-dangerous",
            disable_dangerous_api,
            methods=["PUT"],
        ),
        # P3: 单实体编辑 (工具白名单热编辑 + 上游连接块热重连)
        Route(
            "/api/config/tools/{name}",
            config_tool_detail_api,
            methods=["GET"],
        ),
        Route(
            "/api/config/tools/{name}",
            config_tool_update_api,
            methods=["PUT"],
        ),
        Route(
            "/api/config/upstreams/{name}",
            config_upstream_detail_api,
            methods=["GET"],
        ),
        Route(
            "/api/config/upstreams/{name}",
            config_upstream_update_api,
            methods=["PUT"],
        ),
        # P3: 出厂更新中心 (模板 hash 信号 / dry-run 三方合并 / 热 apply / 备份)
        Route(
            "/api/config/template-status",
            config_template_status_api,
            methods=["GET"],
        ),
        Route(
            "/api/config/upgrade/dry-run",
            config_upgrade_dry_run_api,
            methods=["POST"],
        ),
        Route(
            "/api/config/upgrade/apply",
            config_upgrade_apply_api,
            methods=["POST"],
        ),
        Route(
            "/api/config/upgrade/backup/latest",
            config_upgrade_backup_api,
            methods=["GET"],
        ),
        # P4: FQGate OpenAPI 实时导入 (差异预览 / 热应用 origin='user' 工具行)
        Route(
            "/api/config/import/preview",
            config_import_preview_api,
            methods=["POST"],
        ),
        Route(
            "/api/config/import/apply",
            config_import_apply_api,
            methods=["POST"],
        ),
        # v2: 实体表只读浏览 (P1 /api/config/overlays 路由随覆盖管理 UI 一并退役)
        Route(
            "/api/config/entities",
            config_entities_api,
            methods=["GET"],
        ),
    ]


# ---------------------------------------------------------------------------
# Cache monitor / management HTTP handlers
# ---------------------------------------------------------------------------


async def cache_tool_breakdown_api(req: "Request") -> "JSONResponse":
    """GET /api/cache/tool-breakdown — 各工具缓存条目 Top N (?limit=, 默认20)。"""
    from starlette.responses import JSONResponse

    value, error_resp = _positive_int_param(req.query_params, "limit", 20)
    if error_resp is not None:
        return error_resp
    try:
        rows = await asyncio.to_thread(get_cache_breakdown, value)
    except Exception:
        logger.exception("cache_tool_breakdown_api raised")
        return JSONResponse({"error": "breakdown_read_failed"}, status_code=500)
    return JSONResponse({"tools": rows, "timestamp": int(time.time())})


async def cache_cleanup_api(req: "Request") -> "JSONResponse":
    """POST /api/cache/cleanup — 立即清理过期条目, 返回删除条数。"""
    from starlette.responses import JSONResponse

    try:
        deleted = await run_cache_cleanup()
    except RuntimeError:
        return JSONResponse({"error": "live cache unavailable"}, status_code=503)
    except Exception:
        logger.exception("cache_cleanup_api raised")
        return JSONResponse({"error": "cleanup_failed"}, status_code=500)
    return JSONResponse({"deleted": deleted, "timestamp": int(time.time())})


async def cache_clear_api(req: "Request") -> "JSONResponse":
    """POST /api/cache/clear — 清空全部缓存 (强确认: body {"confirm": true})。"""
    from starlette.responses import JSONResponse

    body, bad_request = await _read_json_object(req)
    if bad_request is not None:
        return bad_request
    if body.get("confirm") is not True:
        return JSONResponse(
            {"error": "需 confirm: true 强确认"}, status_code=400
        )
    try:
        await run_cache_clear()
    except RuntimeError:
        return JSONResponse({"error": "live cache unavailable"}, status_code=503)
    except Exception:
        logger.exception("cache_clear_api raised")
        return JSONResponse({"error": "clear_failed"}, status_code=500)
    return JSONResponse({"cleared": True, "timestamp": int(time.time())})


async def cache_vacuum_api(req: "Request") -> "JSONResponse":
    """POST /api/cache/vacuum — VACUUM 回收死页, 返回新的 DB 大小。"""
    from starlette.responses import JSONResponse

    try:
        result = await run_cache_vacuum()
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except Exception:
        logger.exception("cache_vacuum_api raised")
        return JSONResponse({"error": "vacuum_failed"}, status_code=500)
    return JSONResponse({**result, "timestamp": int(time.time())})


def build_cache_routes() -> list:
    """缓存监控 + 管理路由 (挂载进 gateway_server http app)。"""
    from starlette.routing import Route

    return [
        Route("/api/cache/tool-breakdown", cache_tool_breakdown_api),
        Route("/api/cache/cleanup", cache_cleanup_api, methods=["POST"]),
        Route("/api/cache/clear", cache_clear_api, methods=["POST"]),
        Route("/api/cache/vacuum", cache_vacuum_api, methods=["POST"]),
        Route(
            "/api/upstreams/{name}/l1/reset",
            upstream_l1_reset_api,
            methods=["POST"],
        ),
    ]
