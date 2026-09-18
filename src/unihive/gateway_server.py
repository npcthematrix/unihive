"""
UniHive MCP Gateway Server - 统一 MCP 网关
同时 serve MCP 接口和管理控制台。
"""
import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import socket
import sys
import time
from pathlib import Path

import yaml

import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .storage.cache import Cache, CacheConfig
from .storage.config_store import ConfigStore
from .core.config_merge import apply_config_overlays
from .utils.config_loader import (
    load_config,
    merge_config_overlay,
    validate_config,
)
from .utils.log_config import configure_logging
from .core.normalizer import Normalizer
from .core.registry import register_tool, register_tools_from_config, validate_specs
from .core.router import Router
from .core.proxy_settings import ProxySettings
from .api.upstream_client import UpstreamClient, UpstreamConfig
from .api.fuyao_client import FuyaoClient, FuyaoConfig
from .api.mootdx2_client import MooTDX2Client, MooTDX2Config
from .api.tdx_quant_client import TdxQuantClient
from .api.omni_client import OmniClient, OmniConfig
from .api.thsdk_client import ThsdkClient
from .models.tdx_quant_config import TdxQuantConfig, TdxQuantSettings
from .models.thsdk_config import ThsdkConfig

logger = logging.getLogger(__name__)


# 同步日志里可能带回栈/路径/token, 写库前脱敏.
# 规则: Windows 绝对路径 → [PATH], 长 token (eyJ/sk-/hex32+) → [TOKEN], 单行超 500 字符截断.
import re as _re

# 非 raw 字符串: \\ 在 regex 里就是字面反斜杠
_PATH_RE = _re.compile("[A-Za-z]:[\\\\/](?:[^\\s\\/:|*?\"<>]+[\\\\/])+[^\\s\\/:|*?\"<>]+")
_TOKEN_RE = _re.compile(r"\b(?:eyJ[A-Za-z0-9_.-]{20,}|sk-[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b")


def _sanitize_sync_message(msg: str) -> str:
    if not msg:
        return ""
    s = _PATH_RE.sub("[PATH]", msg)
    s = _TOKEN_RE.sub("[TOKEN]", s)
    if len(s) > 500:
        s = s[:497] + "..."
    return s


def _read_gateway_version() -> str:
    """读取 pyproject.toml 里的版本号，给 FastMCP 当 serverInfo.version。

    不依赖 importlib.metadata (开发环境不一定 pip install -e .),
    直接读源码根的 pyproject.toml。读不到时退回到 'unknown'，避免启动崩。
    """
    try:
        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            return m.group(1)
    except Exception as e:
        logger.warning(f"failed to read gateway version from pyproject.toml: {e}")
    return "unknown"


def _parse_mootdx2_servers(raw_servers: list) -> list:
    """解析 upstreams.yaml mootdx2.servers → ServerConfig 列表。

    mootdx2 的 server= 参数内部用 ipaddress.ip_address 校验, 只接受 IP
    字面量 (不支持域名), 这里在系统边界提前 fail fast, 避免启动后首次
    请求才报 "Server 格式错误"。

    Raises:
        ValueError: host 非 IP 字面量或 port 越界。
    """
    import ipaddress

    from .models.mootdx2_config import ServerConfig

    servers: list[ServerConfig] = []
    for idx, item in enumerate(raw_servers):
        host = str(item.get("host", "")).strip()
        try:
            ipaddress.ip_address(host)
        except ValueError as e:
            raise ValueError(
                f"upstreams.mootdx2.servers[{idx}].host='{host}' 非法: "
                f"mootdx2 只接受 IP 字面量, 不支持域名 ({e})"
            ) from e
        port = int(item.get("port", 7709))
        if not 1 <= port <= 65535:
            raise ValueError(
                f"upstreams.mootdx2.servers[{idx}].port={port} 越界 (1-65535)"
            )
        servers.append(ServerConfig(host=host, port=port))
    return servers


GATEWAY_VERSION = _read_gateway_version()


def _console_auth_config_from_env() -> tuple[dict | None, str]:
    """Read console auth config from UNIHIVE_CONSOLE_* env vars only.

    Returns ``(cfg, reason)`` where cfg is None when auth is disabled.
    Empty string is treated as unset (opt-in via setting them).
    Whitespace around values is stripped.
    """
    username = os.getenv("UNIHIVE_CONSOLE_USER", "").strip() or None
    password = os.getenv("UNIHIVE_CONSOLE_PASSWORD", "").strip() or None
    secret = os.getenv("UNIHIVE_CONSOLE_SESSION_SECRET", "").strip() or None

    # Three empty => no env vars set at all
    if not (username or password or secret):
        return None, "disabled: no UNIHIVE_CONSOLE_* env vars set"

    # Partial => tell user which var(s) are missing
    missing = []
    if not username:
        missing.append("UNIHIVE_CONSOLE_USER")
    if not password:
        missing.append("UNIHIVE_CONSOLE_PASSWORD")
    if not secret:
        missing.append("UNIHIVE_CONSOLE_SESSION_SECRET")
    if missing:
        return None, f"disabled: missing env var(s): {', '.join(missing)}"

    if len(secret) < 32:
        return None, (
            f"disabled: UNIHIVE_CONSOLE_SESSION_SECRET must be at least "
            f"32 characters (got {len(secret)})"
        )

    return {"username": username, "password": password, "session_secret": secret}, "ok"


def _get_console_auth_config(config: dict | None = None) -> tuple[dict | None, str]:
    """Read console auth config from config.yaml, upstreams.yaml or env vars.

    Returns (cfg, reason) where cfg is None when auth is disabled. Caller
    logs ``reason`` at WARNING level and continues without auth; when cfg is
    not None, caller passes it to ``setup_console_auth`` on the inner app.

    Config priority (first source with a non-empty console section wins):
      1. config/config.yaml (console section)
      2. config/upstreams.yaml (console section) — passed in via ``config``
      3. Environment variables (UNIHIVE_CONSOLE_USER / _PASSWORD / _SESSION_SECRET)

    Each source is parsed independently: if a source declares a console
    section, we use *that* source's values verbatim — we don't merge across
    sources (that previously caused partial-config footguns where username
    came from yaml and password came from env, leaving setup_console_auth
    with no way to validate). The reason returned tells the caller exactly
    which field(s) are missing when the source is partial.

    config.yaml fields:
      console.username
      console.password / console.password_hash
      console.session_secret
    """

    def _extract_console(console_cfg: dict) -> dict:
        """Return normalized console sub-config (each field None if unset)."""
        return {
            "username": (console_cfg.get("username") or "").strip() or None,
            "password": (console_cfg.get("password") or "").strip() or None,
            "password_hash": (console_cfg.get("password_hash") or "").strip() or None,
            "secret": (console_cfg.get("session_secret") or "").strip() or None,
        }

    def _missing_fields_message(fields: dict, source: str) -> str:
        missing = [k for k, v in fields.items() if not v]
        return f"partial console config from {source}: missing {', '.join(missing)}"

    def _build_cfg(fields: dict) -> dict:
        # Prefer password_hash over plaintext password.
        cfg = {"username": fields["username"], "session_secret": fields["secret"]}
        if fields["password_hash"]:
            cfg["password_hash"] = fields["password_hash"]
        else:
            cfg["password"] = fields["password"]
        return cfg

    # 1. config/config.yaml (standalone, runtime console config)
    config_yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"
    if config_yaml_path.exists():
        try:
            with open(config_yaml_path, encoding="utf-8") as f:
                config_yaml = yaml.safe_load(f) or {}
            console_cfg = config_yaml.get("console", {})
            if console_cfg:
                fields = _extract_console(console_cfg)
                if any(fields.values()):
                    has_creds = bool(fields["password"] or fields["password_hash"])
                    if not has_creds or not fields["username"] or not fields["secret"]:
                        return None, _missing_fields_message(fields, "config/config.yaml")
                    if len(fields["secret"]) < 32:
                        return None, (
                            "config/config.yaml: session_secret must be at least "
                            "32 characters"
                        )
                    return _build_cfg(fields), "ok"
        except Exception as e:
            logger.warning(f"Failed to load config.yaml: {e}")

    # 2. config/upstreams.yaml (passed in as ``config``).
    if config:
        console_cfg = config.get("console", {})
        if console_cfg:
            fields = _extract_console(console_cfg)
            if any(fields.values()):
                has_creds = bool(fields["password"] or fields["password_hash"])
                if not has_creds or not fields["username"] or not fields["secret"]:
                    return None, _missing_fields_message(fields, "config/upstreams.yaml")
                if len(fields["secret"]) < 32:
                    return None, (
                        "config/upstreams.yaml: session_secret must be at least "
                        "32 characters"
                    )
                return _build_cfg(fields), "ok"

    # 3. Environment variables — helper handles partial-config + length checks.
    return _console_auth_config_from_env()


class GatewayServer:
    """UniHive MCP 网关 - 聚合多个上游 MCP Server"""

    DEFAULT_UPSTREAM_START_TIMEOUT = 30.0
    DEFAULT_HEALTH_CHECK_TIMEOUT = 10.0
    DEFAULT_REQUEST_TIMEOUT = 30.0  # 单 request router.route() 总超时 (MED 3)

    def __init__(
        self,
        config_path: str = "config/upstreams.yaml",
        *,
        strict_env: bool = True,
        strict_validation: bool = True,
        upstream_start_timeout: float | None = None,
        health_check_timeout: float | None = None,
    ):
        self.config_path = Path(config_path)

        # Load upstreams.yaml
        self.config = load_config(config_path, strict_env=strict_env)

        # Load config.yaml and merge (config.yaml takes precedence for overlapping keys)
        config_yaml_path = self.config_path.parent / "config.yaml"
        if config_yaml_path.exists():
            try:
                config_yaml = load_config(str(config_yaml_path), strict_env=strict_env)
                # Merge: config.yaml overrides upstreams.yaml (浅层合并,
                # 与 `unihive config lint` 共用 merge_config_overlay)
                merge_config_overlay(self.config, config_yaml)
            except Exception as e:
                logger.warning(f"Failed to load config.yaml: {e}")
        errors = validate_config(self.config)
        if errors:
            for e in errors:
                logger.warning(f"Config validation: {e}")
            if strict_validation:
                raise ValueError(
                    f"Config validation failed ({len(errors)} error(s)): "
                    + "; ".join(errors)
                )
        # 与 console_api 共用同一份 config，避免双轨不一致
        from .utils import console_api as _ca
        _ca.set_config(self.config)
        self.upstreams: dict[str, UpstreamClient | FuyaoClient | MooTDX2Client | TdxQuantClient] = {}
        self.cache: Cache | None = None
        self.router: Router | None = None
        self.mcp: FastMCP | None = None
        # 上游启用/禁用热开关 (2026-09-18):
        # tool_specs 存全量 spec (validate 对全部生效, 与 enabled 无关);
        # _published_tools 是当前真正发布到对外 /mcp 的 spec 工具名集合
        # (不含 get_server_status/get_health 手写工具), 热增删以此为真来源;
        # toggle 锁串行化并发的启用/禁用操作。
        self.tool_specs: list[dict] = []
        self._published_tools: set[str] = set()
        # SQLite 薄覆盖层 (data/config.db), _do_initialize 早期装配;
        # None 表示 UNIHIVE_CONFIG_SOURCE=yaml 逃生模式或尚未初始化。
        self.config_store: ConfigStore | None = None
        self._upstream_toggle_lock = asyncio.Lock()
        self._running = False
        # initialize() 幂等位 + stop() 取消 health loop 的事件。
        # 之前 initialize 在 start/serve_http 都调一次, 部分 client (stdio UpstreamClient
        # 有保护, Fuyao 没) 重入会状态混乱。health loop 的 asyncio.sleep(30)
        # 也不响应 stop, 要等下一次 tick, 期间可能再调 cache.cleanup_expired。
        self._initialized = False
        self._shutdown_event = asyncio.Event()
        # 在途请求计数 + 优雅关闭
        self._active_requests = 0
        self._requests_lock = asyncio.Lock()
        # _execute_cached 的 per-key 单飞 dict: HIGH 1 (2026-09-07
        # 4th-round audit), 同 key 并发 miss 只触发一次 router.route().
        self._in_flight_requests: dict[str, asyncio.Future] = {}
        # initialize() 并发保护: 两次 await initialize() 必须串行化,
        # 否则 cache / upstream 会被重复初始化 (HIGH #1)。
        self._init_lock = asyncio.Lock()
        # 周期 cache 清理任务 (2026-09-15 MCP 二次审视 M4):
        # cleanup_expired() 函数已存在但从未调度, 过期条目只能等 max_entries
        # 命中 LRU 才清。stop() 时取消。
        self._cache_cleanup_task: asyncio.Task | None = None
        # 单个 upstream 启动超时, 防止某个卡死的 upstream 让整个 init 跟着挂 (MED #4)。
        self._upstream_start_timeout = (
            upstream_start_timeout
            if upstream_start_timeout is not None
            else self.DEFAULT_UPSTREAM_START_TIMEOUT
        )
        # 单个 client.health_check() 超时 (MED3): 防止某个 client 探活卡死
        # 把 health loop 整个 tick 拖住, 后续 client 都探不到。
        self._health_check_timeout = (
            health_check_timeout
            if health_check_timeout is not None
            else self.DEFAULT_HEALTH_CHECK_TIMEOUT
        )
        # 从 gateway 配置读取 per_upstream_timeout
        gateway_cfg = self.config.get("gateway", {})
        self._per_upstream_timeout = gateway_cfg.get("per_upstream_timeout", self.DEFAULT_REQUEST_TIMEOUT)
        # 初始化限流器：从配置读取每个上游的 QPS 限制
        from .core.rate_limiter import get_default_limiter
        self.limiter = get_default_limiter()
        rate_cfg = gateway_cfg.get("rate_limit", {})
        for upstream_name, params in rate_cfg.items():
            if isinstance(params, dict):
                self.limiter.configure(
                    upstream_name,
                    rate=float(params.get("rate", 20.0)),
                    capacity=float(params.get("capacity", 40.0)),
                )
            elif isinstance(params, (int, float)):
                self.limiter.configure(upstream_name, rate=float(params))

    async def initialize(self):
        # 双重检查锁: 第二次 fast path 直接返回; 拿到锁后再校验一次防 TOCTOU。
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._do_initialize()

    async def _apply_db_config_overlays(self) -> None:
        """加载 data/config.db 薄覆盖层并合并进 self.config。

        YAML (upstreams.yaml + config.yaml) 是出厂基线; SQLite 只存本机
        差异 (upstreams.<n>.enabled / fuyao_cache.tools.<tool> ...),
        设计见 docs/design/2026-09-18-sqlite-config-overlay.md。

        - ``UNIHIVE_CONFIG_SOURCE=yaml`` 完全绕过 DB (故障逃生通道);
        - ``UNIHIVE_CONFIG_DB`` 覆盖默认路径 data/config.db;
        - 必须在任何 cache/client 构造之前执行: enabled 覆盖决定是否连上游。
          合并后配置若过不了 validate_config, 直接 fail-closed, 避免在错误
          配置上悄悄建连。
        """
        from .utils import console_api as _ca

        source = os.environ.get("UNIHIVE_CONFIG_SOURCE", "db").strip().lower()
        if source == "yaml":
            logger.info("UNIHIVE_CONFIG_SOURCE=yaml, 跳过 SQLite 配置覆盖层")
            _ca.set_config_store(None)
            return
        db_path = os.environ.get("UNIHIVE_CONFIG_DB", "data/config.db")
        store = ConfigStore(db_path)
        await store.initialize()
        self.config_store = store
        rows = await store.list_overlays()
        revision = await store.revision()
        # 同步读路径 (load_config / get_interfaces ...) 用行镜像合并, 不走 DB;
        # 控制台写路径直接用这个 store 实例。
        _ca.set_config_store(store, rows, revision)
        if not rows:
            return
        merged = apply_config_overlays(self.config, rows)
        errors = validate_config(merged)
        if errors:
            await store.close()
            self.config_store = None
            _ca.set_config_store(None)
            raise ValueError(
                f"Config overlay validation failed ({len(errors)} error(s)): "
                + "; ".join(errors)
            )
        self.config = merged
        _ca.set_config(self.config)
        logger.info(
            "Applied %d SQLite config overlay row(s) (revision=%d, db=%s)",
            len(rows), await store.revision(), db_path,
        )

    async def _do_initialize(self):
        logger.info("Initializing UniHive Gateway Server...")

        # console_api 是 set_omni_client 的目标。__init__ 里也 import 了
        # _ca, 那是给 set_config 用的; 这个 import 是给 _find_omni_client()
        # 之后的 _ca.set_omni_client() 用, 必须在 _do_initialize 局部可见。
        from .utils import console_api as _ca

        try:
            # SQLite 配置薄覆盖层: 必须在 cache/client 构造前合并,
            # enabled 覆盖决定上游是否建连。
            await self._apply_db_config_overlays()

            # 初始化缓存
            cache_cfg = self.config.get("cache", {})
            self.cache = Cache(CacheConfig(
                enabled=cache_cfg.get("enabled", True),
                db_path=cache_cfg.get("db_path", "logs/cache.db"),
                max_entries=int(cache_cfg.get("max_entries", 50000)),
                operation_timeout=float(cache_cfg.get("operation_timeout", 3.0)),
            ))
            await self.cache.initialize()

            # 启动周期 cache 清理任务 (2026-09-15 MCP 二次审视 M4):
            # 间隔 5 min, 等价于把 LRU 上界从「50000 条随时混过期」
            # 收紧到「过期条目最迟 5 min 内回收」。任务在 stop() 取消。
            self._cache_cleanup_task = asyncio.create_task(
                self._cache_cleanup_loop(),
                name="unihive-cache-cleanup",
            )

            # 初始化上游客户端
            # 用 `or {}` 归一化, 防 YAML `upstreams: null` 让 .items() 抛 AttributeError (HIGH #2)
            # 构造/启动拆成 _construct_upstream_client / _start_upstream_client,
            # 供上游启用/禁用热开关复用 (2026-09-18)。
            for name, cfg in (self.config.get("upstreams") or {}).items():
                if not cfg.get("enabled", False):
                    continue
                client = self._construct_upstream_client(name, cfg)
                await self._start_upstream_client(name, cfg, client)

            # 2026-09-15: 让 console_api 能查到已注册的 upstream client,
            # 服务 /api/upstreams/<name>/metrics 端点。必须在装配循环结束、
            # HTTP server 启动前注入 (HTTP server 启动前不会有请求, 安全)。
            _ca.set_upstream_registry(self.upstreams)
            # 2026-09-18: 注入 server 自身, 供 /api/config/upstreams/<name>/enabled
            # 热开关调用 set_upstream_enabled()。stdio 模式下 console_api 无路由,
            # 注入无副作用。
            _ca.set_gateway_server(self)

            # Inject OmniClient into console_api
            _omni = self._find_omni_client()
            _ca.set_omni_client(_omni)

            # 初始化路由前先合并生成工具的 mapping（_load_all_tools 会写回顶层 config）
            tool_specs = self._load_all_tools()
            # 存全量 spec 供热开关重算发布集; validate/注册逻辑不变。
            self.tool_specs = tool_specs

            # 2026-09-14: ProxySettings — 读 config/proxy_settings.yaml,
            # 控制 mootdx2 → tdx_quant 透明代理. yaml 不存在时自动 fallback
            # 到默认 (enabled=False), 不阻断 gateway 启动.
            proxy_settings = ProxySettings(
                path="config/proxy_settings.yaml",
            )

            # 初始化路由
            self.router = Router(
                upstreams=self.upstreams,
                routing_config=self.config.get("routing", {}),
                upstream_tool_mapping=self.config.get("upstream_tool_mapping", {}),
                per_upstream_timeout=getattr(self, "_per_upstream_timeout", None),
                proxy_settings=proxy_settings,
                tool_specs=tool_specs,
            )

            # Store proxy_settings on self for proxy_api handlers to mutate later
            self.proxy_settings = proxy_settings

            # Configure proxy_api with module-level state (so it doesn't need app.state)
            from .utils.proxy_api import configure_proxy_api
            configure_proxy_api(proxy_settings, self.upstreams)

            # 初始化 MCP Server（传入已加载的 specs，避免重复 _load_all_tools() 调用）
            # 经 _create_and_register_gateway_mcp 工厂走, FastMCP 构造 + tools
            # 注册 + capability filter 安装一气呵成, 防止未来 refactor 跳过 filter。
            await self._create_and_register_gateway_mcp(tool_specs)

        except Exception:
            # LOW6: 改为 except Exception, 不再吃 CancelledError / KeyboardInterrupt /
            # SystemExit. CancelledError 自 Py3.8 起是 BaseException 子类
            # (而非 Exception), 它应直接传播让 asyncio.run() 处理取消;
            # KeyboardInterrupt 同理. 这里只清理真正的 init 失败。
            for client in self.upstreams.values():
                try:
                    await asyncio.wait_for(client.stop(), timeout=2.0)
                except Exception:
                    pass
            self.upstreams.clear()
            if self.cache:
                try:
                    await asyncio.wait_for(self.cache.close(), timeout=2.0)
                except Exception:
                    pass
                self.cache = None
            # M1 (2026-09-07 audit): 半构造的 router / mcp 也归零, 否则后续
            # get_server_status / 路由调用拿到指向未完全初始化对象的引用。
            self.router = None
            self.mcp = None
            raise

        self._initialized = True
        logger.info(f"Gateway initialized with {len(self.upstreams)} upstreams")

    def _construct_upstream_client(self, name: str, cfg: dict):
        """按 cfg.type 构造 (但不启动) 一个上游 client。

        启动循环与热启用共用, 避免两处 client 构造参数漂移。
        """
        if cfg.get("type") == "http":
            # HTTP MCP 客户端
            fuyao_cfg = FuyaoConfig(
                name=name,
                base_url=cfg["base_url"],
                api_key=cfg.get("api_key", ""),
                timeout_seconds=cfg.get("timeout_seconds", 30),
                max_retry=cfg.get("retry", {}).get("max_attempts", 3),
            )
            return FuyaoClient(fuyao_cfg)
        if cfg.get("type") == "tdx_quant":
            # TdxQuant 进程内客户端
            tdx_quant_settings = TdxQuantSettings(
                tdx_root=cfg.get("tdx_root", ""),
                strategy_id=cfg.get("strategy_id", ""),
                health_check_interval_sec=cfg.get("health_check_interval_sec", 60),
                call_timeout_sec=cfg.get("call_timeout_sec", 10),
                reconnect_threshold=cfg.get("reconnect_threshold", 3),
                unavailable_threshold=cfg.get("unavailable_threshold", 10),
            )
            tdx_quant_cfg = TdxQuantConfig(
                name=name,
                market=cfg.get("market", "std"),
                settings=tdx_quant_settings,
            )
            return TdxQuantClient(tdx_quant_cfg)
        if cfg.get("type") == "mootdx2":
            # MooTDX2 客户端
            from .models.mootdx2_config import MooTDX2Settings

            # tdxdir 为空时让 mootdx2 自动探测 C:/new_tdx
            pinned_servers = _parse_mootdx2_servers(cfg.get("servers") or [])
            mootdx2_settings = MooTDX2Settings(
                market=cfg.get("market", "std"),
                tdxdir=cfg.get("tdxdir", ""),
                servers=pinned_servers,
                # 指定了服务器就关闭 bestip 全网测速, 固定连配置的 IP
                auto_select_fastest_server=not pinned_servers,
                connect_timeout_seconds=cfg.get("connect_timeout_seconds", 3),
                read_timeout_seconds=cfg.get("read_timeout_seconds", 5),
            )
            mootdx2_cfg = MooTDX2Config(
                name=name,
                market=cfg.get("market", "std"),
                settings=mootdx2_settings,
            )
            return MooTDX2Client(mootdx2_cfg)
        if cfg.get("type") == "omni":
            # OMNIDATA 板块数据客户端
            omni_cfg = OmniConfig(
                name=name,
                db_path=cfg.get("db_path", "./data/board.db"),
            )
            return OmniClient(omni_cfg)
        if cfg.get("type") == "thsdk":
            # THSDK（社区 thsdk 包）客户端；凭证只从环境变量读
            thsdk_cfg = ThsdkConfig.from_dict(name, cfg)
            return ThsdkClient(thsdk_cfg)
        if cfg.get("type") == "ths_local_gateway":
            # THS-LOCAL-GATEWAY HTTP JSON-RPC MCP 客户端
            from .api.ths_local_gateway_client import ThsLocalClient, ThsLocalConfig

            return ThsLocalClient(
                ThsLocalConfig(
                    name=name,
                    base_url=cfg.get("base_url", ""),
                    timeout_seconds=cfg.get("timeout_seconds", 30),
                    connect_timeout_seconds=cfg.get("connect_timeout_seconds", 5.0),
                )
            )
        # stdio MCP 客户端
        upstream_cfg = UpstreamConfig(
            name=name,
            enabled=True,
            package=cfg.get("package", ""),
            command=cfg.get("command", []),
            env=cfg.get("env", {}),
            timeout_seconds=cfg.get("timeout_seconds", 30),
            max_retry=cfg.get("retry", {}).get("max_attempts", 3),
            backoff_base=cfg.get("retry", {}).get("backoff_base", 2),
        )
        return UpstreamClient(upstream_cfg)

    async def _start_upstream_client(self, name: str, cfg: dict, client) -> bool:
        """启动已构造的 client 并登记进 self.upstreams, 返回 is_available。

        复刻原启动循环语义: 单 client 超时/异常不抛出 (CancelledError 除外),
        client 仍登记进 self.upstreams 供健康检查与 stop() 清理; ths_local_gateway
        保留原特例 (不走 wait_for 超时)。
        """
        if cfg.get("type") == "ths_local_gateway":
            try:
                await client.start()
            except Exception as e:
                logger.error(f"[{name}] Failed to start: {e}")
            self.upstreams[name] = client
            connected = bool(client.is_available)
            logger.info(f"[{name}] {'Connected' if connected else 'Failed'}")
            return connected

        try:
            await asyncio.wait_for(
                client.start(), timeout=self._upstream_start_timeout
            )
        except asyncio.CancelledError:
            # CancelledError 是 BaseException, 下面 except Exception 接不住。
            # 先登记 client 防 subprocess / httpx 连接泄漏, 再让取消继续传播。
            logger.error(
                f"[{name}] upstream start() cancelled, registering "
                f"client for cleanup and propagating"
            )
            self.upstreams[name] = client
            raise
        except asyncio.TimeoutError:
            # 单 upstream 超时不中断其他 upstream; 仍登记 (状态由 client 内部标记),
            # get_server_status / health loop / stop() 都能看到这个客户端。
            logger.error(
                f"[{name}] upstream start() timed out after "
                f"{self._upstream_start_timeout}s, marking unavailable"
            )
            self.upstreams[name] = client
            return False
        except Exception as e:
            # 非超时异常 (FileNotFoundError / OSError / 等) 同样登记 client,
            # 让 stop() 能关掉 subprocess/httpx 连接。
            logger.error(
                f"[{name}] upstream start() raised {type(e).__name__}: {e}; "
                f"marking unavailable"
            )
            self.upstreams[name] = client
            return False
        self.upstreams[name] = client
        connected = bool(client.is_available)
        logger.info(f"[{name}] {'Connected' if connected else 'Failed'}")
        return connected

    async def set_upstream_enabled(self, name: str, enabled: bool) -> dict:
        """热切换单个上游启用态, 实时增删对外 /mcp 工具并连接/断开 client。

        - 禁用: 先把该上游参与的、失去全部启用 backer 的工具从 /mcp 摘除,
          再从 self.upstreams pop 并 stop() —— 先摘工具后断连, 杜绝新请求
          落到正在关闭的 client。
        - 启用: 先构造+启动 client 登记进 self.upstreams, 再把新获得启用
          backer 的工具发布到 /mcp。发布只取决于 enabled 而非连接健康,
          与启动时"配了但连不上工具仍注册"同态; connected=False 由前端提示。
        - 多 backer 工具只在全部 backer 禁用时才隐藏 (filter_specs_by_enabled)。
        - 单工具发布/摘除失败不中断整批: 错误收集后继续, 末尾抛 RuntimeError
          (yaml 已落盘, HTTP 层返 500 带 version); 下次同态点击走 reconcile
          自愈补完, 不再被 no-op 短路。

        持久化 (upstreams.yaml) 由调用方 console_api 先完成, 本方法只改运行态
        self.config (与 console_api 共享同一 dict) 与已发布工具集。
        """
        upstreams_cfg = self.config.get("upstreams") or {}
        if name not in upstreams_cfg:
            raise KeyError(f"Unknown upstream: {name}")
        cfg = upstreams_cfg[name]

        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        async with self._upstream_toggle_lock:
            current = bool(cfg.get("enabled", False))
            if current == enabled:
                # 状态已一致: 常规零动作 no-op; 同时承担上次部分失败后的运行态自愈
                return await self._reconcile_upstream_locked(name, cfg, enabled)

            cfg["enabled"] = bool(enabled)
            if enabled:
                return await self._enable_upstream_locked(name, cfg)
            return await self._disable_upstream_locked(name)

    # ------------------------------------------------------------------
    # 热开关内部步骤 (调用方须持 _upstream_toggle_lock)
    # ------------------------------------------------------------------

    def _toggle_tool_diff_locked(self, name: str) -> tuple[set[str], set[str]]:
        """计算单个上游背书工具的发布差集 → (to_add, to_remove)。

        全局期望集经 filter_specs_by_enabled (多 backer 语义), 再与
        backing_upstreams 含 name 的工具取交 —— 自愈重放时只碰本上游的
        工具, 不顺手摘补其他上游的漂移。
        """
        from .core.tool_loader import backing_upstreams, filter_specs_by_enabled

        wanted = {
            s["name"] for s in filter_specs_by_enabled(self.tool_specs, self.config)
        }
        affected: set[str] = set()
        for spec in self.tool_specs:
            routing_key = spec.get("routing") or spec["name"]
            if name in backing_upstreams(routing_key, self.config):
                affected.add(spec["name"])
        to_add = (wanted & affected) - self._published_tools
        to_remove = (self._published_tools & affected) - wanted
        return to_add, to_remove

    def _publish_tools_locked(self, tool_names: set[str], errors: list[str]) -> list[str]:
        """逐个发布, 单工具失败收集进 errors 后继续 (不中断整批)。"""
        spec_by_name = {s["name"]: s for s in self.tool_specs}
        added: list[str] = []
        for tool_name in sorted(tool_names):
            spec = spec_by_name.get(tool_name)
            if spec is None:
                continue
            try:
                register_tool(self, spec)
            except Exception as e:  # noqa: BLE001 — 收集后继续, 末尾统一抛
                errors.append(f"publish {tool_name}: {type(e).__name__}: {e}")
                logger.exception("toggle: publish tool %r failed", tool_name)
                continue
            self._published_tools.add(tool_name)
            added.append(tool_name)
        return added

    def _unpublish_tools_locked(
        self, tool_names: set[str], errors: list[str]
    ) -> list[str]:
        """逐个摘除, KeyError (provider 已无此工具) 幂等视为成功;
        其他异常收集后继续, 且不把该工具移出 _published_tools (集合仍反映
        provider 真实状态, 下次 reconcile 重试)。"""
        removed: list[str] = []
        for tool_name in sorted(tool_names):
            try:
                self.mcp.local_provider.remove_tool(tool_name)
            except KeyError:
                logger.warning(
                    "toggle: tool %r not on local provider (idempotent)", tool_name
                )
            except Exception as e:  # noqa: BLE001
                errors.append(f"unpublish {tool_name}: {type(e).__name__}: {e}")
                logger.exception("toggle: remove tool %r failed", tool_name)
                continue
            self._published_tools.discard(tool_name)
            removed.append(tool_name)
        return removed

    @staticmethod
    def _raise_if_toggle_errors(name: str, errors: list[str]) -> None:
        if errors:
            detail = "; ".join(errors[:5])
            if len(errors) > 5:
                detail += f"; …(+{len(errors) - 5} more)"
            raise RuntimeError(f"runtime toggle {name!r} partial failure: {detail}")

    async def _attach_upstream_client_locked(self, name: str, cfg: dict):
        """构造+启动 client 并切换 console_api 的 omni 指针。返回 client, connected。"""
        client = self._construct_upstream_client(name, cfg)
        connected = await self._start_upstream_client(name, cfg, client)
        if isinstance(client, OmniClient):
            from .utils import console_api as _ca

            _ca.set_omni_client(client)
        return client, connected

    async def _detach_upstream_client_locked(self, name: str) -> None:
        """pop + stop client, 并清 console_api 的 omni 指针。先摘工具后断连。

        _stop_one_upstream 内部已吞掉超时/异常, client 一律按已脱离处理。
        在途请求不做 drain: HTTP client 关池错误转成 ToolResult(success=False)
        由 Router 回落下一 backer (人工低频管理动作, 可接受)。
        """
        client = self.upstreams.pop(name, None)
        if client is not None:
            await self._stop_one_upstream(name, client)
        # console_api 单独持有的 OmniClient 指针也要清, 否则禁用后
        # /api/omni* (含 POST sync) 仍打在已停 client 上, 与启动时
        # enabled:false 注入 None 的语义对齐。
        if isinstance(client, OmniClient):
            from .utils import console_api as _ca

            _ca.set_omni_client(None)

    async def _enable_upstream_locked(self, name: str, cfg: dict) -> dict:
        errors: list[str] = []
        _, connected = await self._attach_upstream_client_locked(name, cfg)
        to_add, _ = self._toggle_tool_diff_locked(name)
        tools_added = self._publish_tools_locked(to_add, errors)
        self._raise_if_toggle_errors(name, errors)
        logger.info(
            "Upstream %s enabled: tools_added=%d connected=%s",
            name,
            len(tools_added),
            connected,
        )
        return {
            "name": name,
            "enabled": True,
            "connected": connected,
            "tools_added": tools_added,
            "tools_removed": [],
            "changed": True,
        }

    async def _disable_upstream_locked(self, name: str) -> dict:
        errors: list[str] = []
        _, to_remove = self._toggle_tool_diff_locked(name)
        tools_removed = self._unpublish_tools_locked(to_remove, errors)
        if errors:
            # 有工具没摘下来: 保留 client, 让残存发布的工具仍可服务;
            # 下次同态点击 (reconcile) 重试摘除, 避免半残废状态。
            self._raise_if_toggle_errors(name, errors)
        await self._detach_upstream_client_locked(name)
        logger.info(
            "Upstream %s disabled: tools_removed=%d", name, len(tools_removed)
        )
        return {
            "name": name,
            "enabled": False,
            "connected": False,
            "tools_added": [],
            "tools_removed": tools_removed,
            "changed": True,
        }

    async def _reconcile_upstream_locked(
        self, name: str, cfg: dict, enabled: bool
    ) -> dict:
        """请求态与 cfg 已一致时的收敛入口。

        健康态是零动作纯 no-op (changed=False); 若上次切换在中途失败
        (HTTP 500/yaml 已落盘), 这里补做漂移: 缺发布的工具补发、应消失的
        工具补摘、缺失/残留的 client 补连/补断, 让前端的同态重试真正自愈。
        """
        errors: list[str] = []
        to_add, to_remove = self._toggle_tool_diff_locked(name)
        existing = self.upstreams.get(name)
        client_attached = False

        if enabled:
            if existing is None:
                existing, _ = await self._attach_upstream_client_locked(name, cfg)
                client_attached = True
            connected = bool(existing is not None and existing.is_available)
            tools_added = self._publish_tools_locked(to_add, errors)
            tools_removed = self._unpublish_tools_locked(to_remove, errors)
        else:
            tools_added = self._publish_tools_locked(to_add, errors)
            tools_removed = self._unpublish_tools_locked(to_remove, errors)
            if existing is not None and not errors:
                await self._detach_upstream_client_locked(name)
                client_attached = True
            connected = False

        self._raise_if_toggle_errors(name, errors)
        healed = bool(tools_added or tools_removed or client_attached)
        if healed:
            logger.info(
                "Upstream %s reconcile: added=%d removed=%d client_attached=%s",
                name,
                len(tools_added),
                len(tools_removed),
                client_attached,
            )
        return {
            "name": name,
            "enabled": enabled,
            "connected": connected,
            "tools_added": tools_added,
            "tools_removed": tools_removed,
            "changed": False if not healed else True,
        }

    async def _execute_cached(
        self,
        name: str,
        params: dict,
        route_key: str,
        ttl_key: str | None = None,
        data_source_type: str = "online",
    ) -> dict:
        """路由 + 缓存包装。

        缓存策略：
        - 只缓存"远程 HTTP 同花顺源"(fuyao_*) 的非实时响应。实时行情不缓存；
          本地终端/本地库（mootdx2/tdx_quant/thsdk/omni）数据访问本身够快，
          再套缓存反而多余。
        - 读缓存/单飞前提：该工具路由候选链上存在 fuyao_* 源；实际写缓存还要
          求本次确实命中 fuyao（本地源优先时不写，避免本地结果污染 fuyao 缓存）。
        - 命中路径：直接返回缓存的响应，附 cache_hit=True。
        - 失效路径：调一次路由；失败响应也只算一次，不双倍路由。
        - hops 是请求级元数据，不进缓存。

        M2 (2026-09-07 5th-round audit): _active_requests 计数挪到外层
        (整个 _execute_cached 入口 + 出口), waiter 在 await single-flight
        future 时也算在 _active_requests 里, stop() 优雅关闭会等它们。
        Pre-fix: 计数只在 _route_and_respond 里, waiter 不计。
        """
        async with self._requests_lock:
            self._active_requests += 1
        try:
            # 缓存策略判定下沉到 src.core.cache_strategy 纯函数, 见 plan
            # warm-imagining-quilt.md (Refactor 1)。本地 import 避免模块
            # 顶层循环依赖。
            from .core import cache_strategy

            # FUYAO 嵌套 TTL 优先于全局 cache.ttl[ttl_key] — 前端可按 endpoint
            # / 工具粒度覆盖。若 fuyao_cache 未配置, 回退到 ttl_key 全局。
            fuyao_ttl = cache_strategy.fuyao_ttl_for(self.config, name)
            ttl = fuyao_ttl if fuyao_ttl is not None else (
                cache_strategy.ttl_for(self.config, ttl_key) if ttl_key else None
            )
            cache_enabled = self.cache is not None and self.cache.enabled
            # 候选路由链（与 Router.route 解析顺序一致）：显式 routing.chain
            # 优先，否则从 upstream_tool_mapping 推导。
            routing_cfg = (self.config.get("routing") or {}).get(route_key)
            if isinstance(routing_cfg, dict) and "chain" in routing_cfg:
                chain = list(routing_cfg.get("chain") or [])
            else:
                chain = list(
                    (self.config.get("upstream_tool_mapping") or {})
                    .get(route_key, {})
                    .keys()
                )
            # 是否走缓存读/单飞：有 TTL、非实时、链上存在远程 fuyao 源。
            can_read_cache = (
                ttl is not None
                and cache_enabled
                and not cache_strategy.is_realtime_ttl(ttl_key)
                and cache_strategy.chain_has_remote_http(chain)
            )
            # 向后兼容：原变量名在下方逻辑中代表"本次走缓存路径"。
            can_cache = can_read_cache
            # hit/miss 统计跟踪范围与读缓存前提一致。
            tracks_stats = can_read_cache

            key: str | None = None
            if can_cache:
                key = cache_strategy.cache_key(name, params)
                cached = await self.cache.get(key)
                if cached is not None:
                    # LOW-4 (2026-09-14 audit): hops 不进缓存 (写时清空),
                    # 命中时给一个 cache sentinel hop 让消费者看到响应走了
                    # 缓存而非未配置 routing chain。不可变拷贝避免污染持久化对象。
                    self.cache.record_hit()
                    # DEBUG 而非 INFO: 缓存命中是常态, 每请求刷 INFO 会让 FileHandler
                    # 在事件循环线程同步写盘; 100+ QPS 时放大成可观察的 latency。
                    logger.debug(f"[{name}] cache hit (key={key})")
                    return {
                        **cached,
                        "hops": [
                            {
                                "source": "cache",
                                "tool_name": name,
                                "duration_ms": 0,
                                "success": True,
                                "error": None,
                            }
                        ],
                        "cache_hit": True,
                    }

            if tracks_stats and not can_cache:
                # 计 miss 但不走单飞：例如实时 key、链上无远程 fuyao 源。
                # 每个请求各自路由, 用于运维观测命中率。
                self.cache.record_miss()

            # 路由 + 缓存写入的内部闭包 (不再管 _active_requests)
            async def _route_and_respond() -> dict:
                # MED 3 (2026-09-07 4th-round audit): 总超时, 防止上游
                # (特别是 MooTDX2 同步库走 run_in_executor, 0 个内部 timeout)
                # 卡死整个 request。MooTDX2 call_tool 内部无 timeout, executor
                # 任务挂死会拖到 stop() 等不到 _active_requests==0。
                result = await asyncio.wait_for(
                    self.router.route(route_key, params),
                    timeout=self.DEFAULT_REQUEST_TIMEOUT,
                )
                # hops 是请求级诊断信息，留在响应里随返回消费者；
                # 进缓存的副本中我们设 []，避免历史 hops 被复用。
                logger.debug(
                    f"[{name}] route result: success={result.success} "
                    f"source={result.source} hops={[h.source for h in result.hops]}"
                )
                resp = Normalizer.to_gateway_response(
                    success=result.success,
                    data=result.data,
                    error=result.error,
                    source=result.source,
                    hops=[h.source for h in result.hops],  # 真实 hops 留给消费者
                )
                # 仅当本次实际命中远程 fuyao 源才写缓存：若链上本地源优先并成功，
                # 不能把本地结果按 fuyao 的 key 缓存，否则后续 fuyao 直查会误命中。
                if (
                    can_cache
                    and key is not None
                    and result.success
                    and result.data is not None
                    and cache_strategy.is_fuyao_source(result.source)
                ):
                    # 缓存里只存稳定数据：hops（请求级）入空，cache_hit 在 setdefault 之前写
                    await self.cache.set(key, {**resp, "hops": []}, ttl=ttl)
                # 标记 miss，让消费者区分"缓存端点本次未命中"与"无缓存端点"
                resp.setdefault("cache_hit", False)
                return resp

            # HIGH 1 (2026-09-07 4th-round audit): cache miss 后到 route 之间
            # 必须 per-key single-flight, 否则同 key 并发 miss 让 router 被调
            # N 次、上游负载翻倍。_execute_cached 走 cache.get + cache.set
            # 直接路径, 这一段没有 in-flight 保护, 必须在 gateway 层补。
            # waiter 复用 leader 的 future, leader 失败则 waiter 升级为
            # 新 leader 自己再试一次 (fallback 行为).
            if can_cache and key is not None:
                existing = self._in_flight_requests.get(key)
                if existing is not None and not existing.done():
                    try:
                        cached_resp = await existing
                        return {
                            **cached_resp,
                            "hops": cached_resp.get("hops", []),
                            "cache_hit": False,
                        }
                    except BaseException:
                        pass  # leader 失败, 自己升级为新 leader
                # leader: 跑路由 + 写缓存, 失败要传播给 waiter
                future = asyncio.get_running_loop().create_future()
                self._in_flight_requests[key] = future
                # miss 只对发起者记一次, waiter 复用 leader 的 future 不重复计
                self.cache.record_miss()
                try:
                    resp = await _route_and_respond()
                    future.set_result(resp)
                    return resp
                except BaseException as exc:
                    future.set_exception(exc)
                    raise
                finally:
                    # 先 set_result/set_exception 再 pop, waiter 拿到结果时
                    # in_flight 已清理, 下次同 key 是新 leader.
                    self._in_flight_requests.pop(key, None)

            # can_cache=False 走无单飞直接路径
            return await _route_and_respond()
        finally:
            async with self._requests_lock:
                self._active_requests -= 1

    def _load_all_tools(self) -> list[dict]:
        """Delegate to tool_loader.load_all_tools for YAML merge."""
        from .core.tool_loader import load_all_tools
        return load_all_tools(self.config_path, self.config)

    async def _create_and_register_gateway_mcp(
        self, tool_specs: list[dict] | None = None
    ) -> FastMCP:
        """Single entry point for constructing the gateway FastMCP server.

        M2 (2026-09-07 audit) + 2026-09-08 Refactor 1: 三步（构造 + 注册工具 +
        capability filter 安装）全部委派给 ``src.core.mcp_factory.create_and_register_gateway_mcp``，
        防止未来 refactor 在 _do_initialize 之外构造裸 FastMCP 时漏掉 capability
        filter (HIGH #3, 2026-09-07 audit)。
        """
        from .core.mcp_factory import create_and_register_gateway_mcp

        def _on_constructed(mcp: FastMCP) -> None:
            # version 显式传 GATEWAY_VERSION, 避免 FastMCP 默认 fallback 到框架
            # 版本 (4.0.3), 让 client 把 framework 升级误判成 server 升级。
            self.mcp = mcp
            self._register_tools(specs=tool_specs)

        return await create_and_register_gateway_mcp(
            tool_specs,
            version=GATEWAY_VERSION,
            on_constructed=_on_constructed,
        )

    def _register_tools(self, specs: list[dict] | None = None):
        # 特殊工具：手写（带特殊逻辑或网关内部）
        self._register_status_tools()

        # 通用工具：合并手工 tools 与生成的 tools_tdx_quant.yaml
        if specs is None:
            specs = self._load_all_tools()
            self.tool_specs = specs
        if specs:
            # validate 对全量 spec 生效 —— 配置写错与上游是否启用无关,
            # 禁用上游不能把 routing/mapping 配置错误静默藏起来。
            strict = self.config.get("strict_registry", True)
            validate_specs(
                specs,
                self.config.get("routing", {}),
                self.config.get("upstream_tool_mapping", {}),
                strict=strict,
            )
            # 发布集按上游 enabled 过滤: 全部 backer 禁用的工具不进对外 /mcp。
            # 热开关见 set_upstream_enabled(), 启动语义与其差集计算一致。
            from .core.tool_loader import filter_specs_by_enabled

            publishable = filter_specs_by_enabled(specs, self.config)
            registered = register_tools_from_config(self, publishable)
            self._published_tools = set(registered)
            hidden = len(specs) - len(registered)
            logger.info(
                f"Registered {len(registered)} tools from config "
                f"(dangerous={any(s.get('dangerous') for s in publishable)}, "
                f"hidden_by_disabled_upstream={hidden})"
            )

    def _register_search_stock(self):
        @self.mcp.tool()
        async def search_stock(keyword: str) -> dict:
            """【按关键词模糊搜索】输入股票名称或代码片段 (如 '茅台'/'600000'), 返回 Top 10 匹配股票. 适合用户问"某只股票"时调用. 全量列表请用 meta_tickers_list.

            走 routing 系统：fuyao_meta (HTTP, 可缓存) 优先；不可用时降级到 tdx_local (本地, 不缓存)。
            """
            return await self._execute_cached(
                "search_stock",
                {"keyword": keyword},
                route_key="search_stock",
                ttl_key="search",
            )

    def _register_status_tools(self):
        @self.mcp.tool()
        async def get_server_status() -> dict:
            """获取网关状态"""
            return {
                "gateway": "healthy",
                "upstreams": self._build_upstream_summary(self.upstreams),
                "timestamp": int(time.time()),
            }

        @self.mcp.tool()
        async def get_health() -> dict:
            """健康检查 — 聚合上游状态, 不再硬编码 healthy."""
            return self.aggregate_health(self.upstreams)

    @staticmethod
    def _build_upstream_summary(upstreams: dict) -> dict:
        """L5 (2026-09-07 5th-round audit): 共享 upstream → {status, available}
        收集, get_server_status 与 aggregate_health 都用。"""
        return {
            name: {
                "status": client.status.value,
                "available": client.is_available,
            }
            for name, client in upstreams.items()
        }

    @staticmethod
    def aggregate_health(upstreams: dict) -> dict:
        """LOW8 + L4 (2026-09-07 5th-round audit): 聚合 upstream 状态为整体
        health. 改 staticmethod (不用 cls)。

        - 无 upstream: healthy (无状态可报告)
        - 全 healthy: healthy
        - 全 unavailable: unhealthy
        - 其他 (有 degraded / 部分 unavailable): degraded
        """
        upstream_states = {
            name: client.status.value
            for name, client in upstreams.items()
        }
        if not upstream_states:
            overall = "healthy"
        elif all(s == "healthy" for s in upstream_states.values()):
            overall = "healthy"
        elif all(s == "unavailable" for s in upstream_states.values()):
            overall = "unhealthy"
        else:
            overall = "degraded"
        return {
            "status": overall,
            "upstreams": upstream_states,
            "timestamp": int(time.time()),
        }

    def _find_omni_client(self) -> "OmniClient | None":
        """从 self.upstreams 里找到 OmniClient 实例 (按类型, 不依赖配置里的 name)

        提升到类方法是为了让 ``_do_initialize`` 也能调。原先是 ``serve_http``
        内部的嵌套函数, commit 399b18c 把调用挪进 ``_do_initialize`` 时忘了
        同步上提, 启动会 AttributeError。
        """
        for client in self.upstreams.values():
            if isinstance(client, OmniClient):
                return client
        return None

    async def _health_check_loop(self) -> None:
        """异步 health check 循环: 每个 tick 并行探活所有 upstream,
        慢 client 不会阻塞其它 client 的探活 (每个 client.health_check()
        单独包 asyncio.wait_for(timeout=_health_check_timeout))。

        测试 fixture 用 asyncio.create_task(server._health_check_loop()) 启动,
        通过 server._shutdown_event.set() 立刻唤醒。生产中该方法被 stop() 在
        同一 event 上 set 之后 0 延迟退出。

        顺序: 立刻探活一次, 然后等下一个 tick (30s) 或 shutdown 信号 —
        启动后第一次探活不应等满 30s, 否则刚连上 gateway 的 client 在第一个
        tick 内健康状态不会被记录。
        """
        while self._running:
            # 先探活一次 (进入循环立刻跑, 不等 30s tick)
            await self._probe_upstreams_once()
            try:
                # 等下一个 tick (30s) 或 shutdown 信号
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=30.0,
                )
                # 被 set() 唤醒 → 退出
                return
            except asyncio.TimeoutError:
                # 30s tick 到了, 继续下一轮 (再次先探活再等)
                continue

    async def _probe_upstreams_once(self) -> None:
        """并行调用每个 upstream 的 health_check(), 慢/挂的 client 不会
        阻塞其它 client 的探活 (每个调用单独包 asyncio.wait_for)。"""
        if not self.upstreams:
            return

        async def _probe_one(client) -> None:
            try:
                await asyncio.wait_for(
                    client.health_check(),
                    timeout=self._health_check_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[health_check] {getattr(client, 'name', '?')} timed out "
                    f"after {self._health_check_timeout}s"
                )
            except Exception as e:
                logger.warning(
                    f"[health_check] {getattr(client, 'name', '?')} raised: {e}"
                )

        # 并行执行: 慢/挂的 client 不会拖住其它 client
        await asyncio.gather(
            *(_probe_one(c) for c in self.upstreams.values()),
            return_exceptions=False,
        )

    async def start(self):
        # M5 (2026-09-07 5th-round audit): initialize() 失败时重置 _running 并
        # 调 stop() 清 partial state, 允许同实例重试。Pre-fix: _running 在
        # initialize 之前设 True, 失败时留在 True, 重试 start() 会状态错位。
        self._running = True
        try:
            await self.initialize()
        except BaseException:
            await self.stop()
            raise
        # 注册 SIGINT/SIGTERM handler: stdio transport 模式下, 父进程 (Claude
        # Desktop 等) 关停会发 SIGTERM, Python 默认 handler 不通知 asyncio,
        # 我们的 stop() 不会被调用, stdio MCP 子进程泄漏。
        # Windows 的 asyncio 完全不支持 add_signal_handler (会抛 NotImplementedError),
        # 那里只能靠父进程行为 + finally 兜底; Unix 上正常注册。
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM, self._shutdown_event.set)
            loop.add_signal_handler(signal.SIGINT, self._shutdown_event.set)
        # M4 (2026-09-07 5th-round audit): signal handler 只 set _shutdown_event,
        # 但 run_stdio_async 不 watch 它 (MCP stdio 默认依赖 stdin close 触发关闭)。
        # 之前的注释说 "handler 修了泄漏" 是误导 — 实际上 stop() 仍然不会被调,
        # 子进程要等 stdin 关才退出, 期间 health_task / upstreams / cache 全部泄漏。
        # 加 _signal_watcher: 等 _shutdown_event, 触发后 cancel 当前 main_task,
        # 让 run_stdio_async 抛 CancelledError, outer finally 调 stop()。
        main_task = asyncio.current_task()

        async def _signal_watcher():
            await self._shutdown_event.wait()
            if main_task is not None and not main_task.done():
                main_task.cancel()

        _watcher = asyncio.create_task(_signal_watcher())
        try:
            try:
                await self.mcp.run_stdio_async()
            finally:
                _watcher.cancel()
                try:
                    await _watcher
                except (asyncio.CancelledError,):
                    pass
        finally:
            # 任何退出路径都跑 cleanup, 包括 KeyboardInterrupt / SIGTERM /
            # _signal_watcher 触发的 cancel。
            await self.stop()

    async def serve_http(self, host: str = None, port: int = None, mcp_path: str = None):
        """HTTP 模式: 同时 serve MCP 接口和 Console 管理 UI."""
        gw_cfg = self.config.get("gateway", {})
        host = host if host is not None else gw_cfg.get("host", "127.0.0.1")
        port = port if port is not None else gw_cfg.get("port", 18080)
        mcp_path = mcp_path if mcp_path is not None else gw_cfg.get("mcp_path", "/mcp")
        self._running = True
        # M5 (2026-09-07 5th-round audit): initialize() 失败时重置 _running 并
        # 调 stop() 清 partial state, 允许同实例重试。
        try:
            await self.initialize()
        except BaseException:
            await self.stop()
            raise

        # Console API handlers (delegates to console_api.py)
        from .utils import console_api as _ca
        from .utils.proxy_api import build_proxy_routes

        def _proxy_routes():
            return build_proxy_routes()

        async def _status_api(req: Request):
            return JSONResponse(_ca.get_upstream_status())

        async def _interfaces_api(req: Request):
            return JSONResponse(_ca.get_interfaces())

        async def _config_api(req: Request):
            return JSONResponse(_ca.load_config())

        async def _fuyao_cache_get_api(req: Request):
            return JSONResponse(_ca.get_fuyao_cache())

        async def _fuyao_cache_put_api(req: Request):
            body = await req.json()
            payload = body.get("fuyao_cache") or {}
            expected_version = body.get("version")
            try:
                result = await _ca.save_fuyao_cache(
                    payload,
                    expected_version=expected_version,
                )
            except _ca.ConfigStoreUnavailable as e:
                return JSONResponse(
                    {"error": "config_store_unavailable", "message": str(e)},
                    status_code=503,
                )
            except _ca.VersionConflict as e:
                return JSONResponse(
                    {"error": "version_conflict", "message": str(e)},
                    status_code=409,
                )
            except ValueError as e:
                return JSONResponse(
                    {"error": "validation_failed", "message": str(e)},
                    status_code=400,
                )
            return JSONResponse(result)

        async def _health_api(req: Request):
            return JSONResponse(_ca.get_health())

        async def _mcp_tools_api(req: Request):
            # L3 (2026-09-07 5th-round audit): mcp 未初始化时返回 503,
            # 避免 AttributeError。实际不可达 (serve_http 必先 await init),
            # 但加 guard 让 handler 更鲁棒。
            if self.mcp is None:
                return JSONResponse(
                    {"error": "gateway not initialized"}, status_code=503
                )
            raw = await self.mcp.list_tools()
            # 构建 routing_key → source 映射（从 upstream_tool_mapping 查，查不到返回 unknown）
            source_map = _ca._build_source_map()
            tools_out = []
            for ft in raw:
                d = ft.model_dump()
                name = d.get("name") or ""
                params_schema = d.get("parameters") or {}
                props = params_schema.get("properties", {})
                required = params_schema.get("required", [])
                params = []
                for pname, pinfo in props.items():
                    desc = pinfo.get("description", "")
                    ptype = pinfo.get("type", "any")
                    enum_vals = pinfo.get("enum")
                    p = {"name": pname, "type": ptype, "required": pname in required, "description": desc}
                    if enum_vals:
                        p["enum"] = enum_vals
                    params.append(p)
                source = source_map.get(name, "unknown")
                tools_out.append({"name": name, "description": d.get("description") or "", "source": source, "params": params})
            body = json.dumps({"timestamp": int(time.time()), "tools": tools_out}, indent=2, ensure_ascii=False)
            return Response(body, media_type="application/json")

        def _read_file(path: str):
            p = Path(path)
            return p.read_bytes() if p.exists() else None

        async def _index_route(req: Request):
            html = _read_file(_ca.CONSOLE_HTML_PATH)
            if html is None:
                return JSONResponse({"error": "console.html not found"}, status_code=404)
            return Response(html, media_type="text/html")

        # MCP ASGI app — path="/" 让 inner 路由在 root,
        # 由 Mount("/mcp", ...) 接管 URL 前缀。否则 inner 路由在 /mcp/ 会被
        # Mount 剥离前缀后变成 404 (外 /mcp/ → 内 /, 路由在 /mcp/ 不匹配)。
        # 显式传 allowed_hosts / allowed_origins / host_origin_protection,
        # 不依赖 FastMCP 4.x 的默认值 (默认开, 但版本升级可能改),
        # 始终满足 MCP 2025-11-25 spec 对 DNS-rebinding 防护的 MUST 要求。
        mcp_app = self.mcp.http_app(
            path="/",
            allowed_hosts=["127.0.0.1:*", "localhost:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
            host_origin_protection=True,
        )

        # Mount("/mcp", ...) 对子路径 /mcp/foo 会剥前缀成 /foo,
        # 对完全相等的 /mcp 则不剥, 透传 /mcp 给 inner app, 但 mcp_app
        # 的 route 挂在 "/" 上, 不匹配会 404。
        # 用 make_mcp_path_canonicalizer 把 /mcp 改写成 /mcp/, 让 Mount 能命中。
        # 用 NoSlashStarlette 关掉 Starlette 默认的 redirect_slashes=True,
        # 否则 POST /mcp → 307 → /mcp/, 多数 MCP 客户端不会自动跟 POST 307。
        # 来自 src.core.mcp_factory (Refactor 1, 2026-09-08)。
        from .core.mcp_factory import NoSlashStarlette, make_mcp_path_canonicalizer

        inner_app = NoSlashStarlette(
            lifespan=mcp_app.lifespan,
            routes=[
                Mount(mcp_path, app=mcp_app),
                Mount("/static", StaticFiles(directory=str(_ca._PROJECT_ROOT / "static"), html=False)),
                Route("/", _index_route),
                Route("/console.html", _index_route),
                Route("/api/status", _status_api),
                *_ca.build_omni_routes(),
                *_ca.build_mcp_browser_routes(),
                Route("/api/upstreams/{name}/metrics", _ca.upstream_metrics_api),
                *_proxy_routes(),
                Route("/api/interfaces", _interfaces_api),
                Route("/api/config", _config_api),
                Route("/api/config/fuyao-cache", _fuyao_cache_get_api),
                Route("/api/config/fuyao-cache", _fuyao_cache_put_api, methods=["PUT"]),
                # 2026-09-18: 上游启用/禁用热开关 (鉴权随 /api/* 中间件继承)
                *_ca.build_upstream_routes(),
                Route("/health", _health_api),
                Route("/api/mcp-tools-list", _mcp_tools_api),
            ]
        )

        # HIGH #4 (console needs login): wire console_auth into inner_app when
        # console config is set in YAML or all three UNIHIVE_CONSOLE_* env vars.
        # ConsoleAuthMiddleware exempts /mcp, /health, /login, /logout, /static —
        # so MCP clients and health probes keep working. If not configured,
        # leave the console open and log why (backward compatible; opt-in auth).
        auth_cfg, auth_reason = _get_console_auth_config(self.config)
        if auth_cfg is not None:
            from .console_auth import setup_console_auth
            setup_console_auth(inner_app, auth_cfg)
            logger.info("Console auth enabled (UNIHIVE_CONSOLE_* set)")
        else:
            logger.warning(f"Console auth disabled: {auth_reason}")

        app = make_mcp_path_canonicalizer(inner_app, mcp_path)
        log_level = self.config.get("logging", {}).get("level", "info").lower()
        config = uvicorn.Config(app, host=host, port=port, log_level=log_level)

        # 端口可用性预检: uvicorn 绑定失败只 log + 退出 3, 不 raise OSError,
        # 拿不到干净错误。预检给一个明确消息 + 退出码 2。
        #
        # TOCTOU 评估 (MED5): _probe.close() → uvicorn serve() 之间存在
        # 亚毫秒窗口, 另一进程理论上能抢端口。考虑到:
        #   1) 单用户本地 dev 场景, 端口竞争源少;
        #   2) 即便 uvicorn 二次 bind 失败, 它会进自己的 retry/backoff,
        #      我们已有 serve_http 的 finally cleanup (停止 health loop /
        #      upstreams), 不至于泄漏资源;
        #   3) 加 retry loop 或 SO_REUSEADDR 反而会盖掉真实的端口冲突信号
        #      (SO_REUSEADDR 在 Linux 上允许 bind 已被 LISTEN 但已 CLOSE 的端口,
        #       在 Windows 上行为不同)。
        # 结论: 接受 TOCTOU 风险, 维持现有预检作为友好错误信号。
        #
        # H1 (2026-09-07 5th-round audit): port probe + sys.exit(2) 必须
        # 在 outer try/finally 里面, 否则端口冲突时 stop() 不跑,
        # health_task / upstreams / cache 全部泄漏。
        try:
            _probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                try:
                    _probe.bind((host, port))
                except OSError as e:
                    if e.errno in (98, 10048) or "address already in use" in str(e).lower():
                        print(
                            f"error: cannot bind {host}:{port} — port already in use",
                            file=sys.stderr,
                        )
                        sys.exit(2)
                    raise
            finally:
                # Always release the probe fd regardless of bind outcome
                # (including non-errno-98 OSError paths that re-raise).
                _probe.close()

            await uvicorn.Server(config).serve()
        finally:
            await self.stop()

    async def stop(self):
        """优雅关闭：等待在途请求完成后关闭连接池"""
        self._running = False
        self._shutdown_event.set()  # 唤醒 stdio signal watcher

        # Reset console_api state
        # _ca 在 stop() 局部 import, 与 _do_initialize / serve_http 各 import
        # 一次保持一致 (避免隐式依赖 __init__ 的局部变量)。
        from .utils import console_api as _ca
        _ca.set_omni_client(None)
        # 2026-09-18: 摘掉热开关回调目标, 防止 stop 后 /api 仍调到死 server
        _ca.set_gateway_server(None)
        # P1: 摘掉 config.db 写目标与 overlay 镜像, 支持同实例 stop/start
        _ca.set_config_store(None)
        # LOW-1 (2026-09-14 audit): 显式关掉 probe executor, 不依赖 atexit
        # (stdio transport 下 atexit 不保证触发)。
        _ca._shutdown_probe_executor()

        # 等待在途请求完成（最多 10 秒）
        wait_start = time.time()
        max_wait = 10.0
        while True:
            async with self._requests_lock:
                active = self._active_requests
            if active == 0:
                logger.info("All in-flight requests completed, proceeding with shutdown")
                break
            if time.time() - wait_start > max_wait:
                logger.warning(f"Timeout waiting for {active} in-flight requests, force-exit")
                break
            logger.info(f"Waiting for {active} in-flight requests to complete...")
            await asyncio.sleep(0.5)

        # 与热开关互斥: 否则禁用/启用可能在 gather 快照后才 pop/登记 client,
        # 新 client 被最后的 upstreams.clear() 丢弃而未 stop (httpx/子进程泄漏)。
        # 锁内协程若在关停止于 _start_upstream_client, 等其结束后一并 gather。
        # 与热开关互斥到 upstreams 清空: 禁用/启用可能在 gather 快照后才
        # pop/登记 client, 新 client 若不被 gather 覆盖就会被 clear() 丢弃
        # 而未 stop (httpx/子进程泄漏)。锁内 toggle 若止于 _start, 此处一并关。
        toggle_lock = getattr(self, "_upstream_toggle_lock", None)
        async with toggle_lock if toggle_lock is not None else contextlib.nullcontext():
            # 关闭所有上游客户端 (L1, 2026-09-07 5th-round audit): 并行 gather
            # 替代顺序循环, 总关闭时间从 O(N) 降到 O(1)。
            if self.upstreams:
                await asyncio.gather(
                    *[self._stop_one_upstream(name, client)
                      for name, client in list(self.upstreams.items())],
                    return_exceptions=True,
                )

            # 关闭阻塞上游调用专用线程池 (2026-09-15 MCP 二次审视 M2):
            # 在 upstream 客户端之后关闭, 避免它们还在 await run_in_executor 时被强杀。
            # shutdown_blocking_executor 是模块级单例, 显式调用避免 atexit 依赖 (stdio 下不可靠)。
            from .core.blocking_pool import shutdown_blocking_executor
            shutdown_blocking_executor()

            # 关闭缓存
            if self.cache:
                try:
                    await asyncio.wait_for(self.cache.close(), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning("cache close() timed out after 2s")
                self.cache = None

            # 关闭 SQLite 配置覆盖层连接池
            # getattr 兜底: 测试用 __new__ 跳过 __init__ 时字段不存在
            config_store = getattr(self, "config_store", None)
            if config_store is not None:
                await config_store.close()
                self.config_store = None

            # 取消周期 cache 清理任务 (2026-09-15 MCP 二次审视 M4)。
            # cache.close() 之后做, 确保最后一次 tick 不会与 close 抢锁。
            # 用 getattr 兜底: 测试用 __new__ 跳过 __init__ 时字段不存在。
            cleanup_task = getattr(self, "_cache_cleanup_task", None)
            if cleanup_task is not None:
                cleanup_task.cancel()
                try:
                    await cleanup_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"cache cleanup task ended with error: {e}")
                self._cache_cleanup_task = None

            # 重置状态以支持同实例 restart (HIGH B1, 2026-09-07 audit):
            # 不清掉的话, 第二次 initialize() 会因 _initialized=True 短路,
            # 用已经 stop 过的死 upstreams / router / mcp 路由 → 全程失败。
            # 生产路径通常 stop 后进程退出, 重置是无害的; stop/start 循环
            # (supervisor 重启 / 热重载配置) 则依赖这些归零。
            self.upstreams.clear()
        self.router = None
        self.mcp = None
        # 热开关状态一并归零, 支持同实例 stop/start 循环
        self._published_tools = set()
        self.tool_specs = []
        self._initialized = False

        # M1 (2026-09-15 MCP 二次审视): 重置全局限流器单例，支持 stop/start 循环
        from .core.rate_limiter import reset_default_limiter
        reset_default_limiter()

        # L1: 重置 proxy_api 模块级全局状态
        from .utils.proxy_api import reset_proxy_api
        reset_proxy_api()

        logger.info("Gateway shutdown complete")

    async def _stop_one_upstream(self, name: str, client):
        try:
            await asyncio.wait_for(client.stop(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"[{name}] stop() timed out after 5s, force-exit")
        except Exception as e:
            logger.warning(f"[{name}] stop() raised: {e}")

    # 2026-09-15 MCP 二次审视 M4: 周期 cache 清理。间隔 5 min — 在清理频率
    # 与 CPU 开销间取中。Cache.cleanup_expired() 内部已包 operation_timeout,
    # 不会因一次清理卡住 loop。
    _CACHE_CLEANUP_INTERVAL_SEC = 300

    async def _cache_cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._CACHE_CLEANUP_INTERVAL_SEC)
                if self.cache is None:
                    return
                try:
                    removed = await self.cache.cleanup_expired()
                    if removed:
                        logger.debug(f"Cache cleanup removed {removed} expired/LRU entries")
                except Exception as e:
                    logger.warning(f"Cache cleanup tick failed: {e}")
        except asyncio.CancelledError:
            return


async def async_main(transport: str = "stdio", host: str | None = None, port: int | None = None, config_path: str | None = None):
    if sys.platform == "win32":
        os.environ["PYTHONIOENCODING"] = "utf-8"

    configure_logging(transport)

    cfg_path = config_path or "config/upstreams.yaml"
    server = GatewayServer(config_path=cfg_path)
    logger.info(
        f"unihive starting: transport={transport} config={cfg_path} "
        f"upstreams={len(server.config.get('upstreams') or {})}"
    )
    if transport == "stdio":
        await server.start()
    elif transport == "http":
        await server.serve_http(host=host, port=port)
    else:
        raise ValueError(f"Unknown transport: {transport}")


def _build_arg_parser() -> "argparse.ArgumentParser":
    parser = argparse.ArgumentParser(description="UniHive MCP Gateway")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="http",
        help="Transport mode (default: http). 'stdio' for stdio transport.",
    )
    parser.add_argument("--host", default=None, help="HTTP bind host (default: from config)")
    parser.add_argument("--port", type=int, default=None, help="HTTP bind port (default: from config)")
    parser.add_argument(
        "--config", default=None,
        help="Path to upstreams.yaml (default: config/upstreams.yaml)",
    )
    return parser


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()
    try:
        asyncio.run(async_main(
            transport=args.transport,
            host=args.host,
            port=args.port,
            config_path=args.config,
        ))
    except ValueError as e:
        # 配置校验失败 / 未知 transport: 给一个干净消息 + 退出码 2
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        # stdio 模式父进程关停时正常路径, 不打 traceback
        sys.exit(0)


if __name__ == "__main__":
    main()
