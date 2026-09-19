"""
UniHive MCP Gateway Server - 统一 MCP 网关
同时 serve MCP 接口和管理控制台。
"""
import argparse
import asyncio
import contextlib
import copy
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
from .core.config_db import resolve_project_path
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

#: 手写/状态工具名: 实体工具不得改名为这些名字 (会与
#: _register_status_tools / search_stock 的手写注册撞车, 且这些名字
#: 在 tool 实体表里本就不存在)。
RESERVED_TOOL_NAMES = frozenset({"get_server_status", "get_health", "search_stock"})


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

    # H4 (round 9 audit): env 路径就地 hash 明文 password → password_hash。
    # setup_console_auth 现在拒绝明文 (要 PBKDF2 password_hash), 旧实现直接
    # 塞明文 → serve_http 路由装配段崩。就地 hash 后 env 路径与 YAML 路径
    # 同态, 不再把明文留进程内存 (hash 是被 setup 持久化的对象)。
    from .console_auth import hash_password

    return {
        "username": username,
        "password_hash": hash_password(password),
        "session_secret": secret,
    }, "ok"


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
        # L3 (round 10 audit): 相对 config_path 锚定项目根, 与从任意 CWD 启动
        # 无关。旧实现 Path(relative) 锚定 CWD → 从别的工作目录启动时找不到
        # upstreams.yaml, db 模式空库首启会拿不到出厂 YAML 模板而 seed 空配置;
        # 绝对路径原样保留 (resolve_project_path 第三语义)。
        self.config_path = Path(
            resolve_project_path(config_path, "config/upstreams.yaml")
        )
        self._strict_env = strict_env

        # v2 (2026-09-18): 配置事实源默认是 data/config.db 实体表;
        # UNIHIVE_CONFIG_SOURCE=yaml 回到纯 YAML 出厂模式 (故障逃生通道);
        # UNIHIVE_CONFIG_DB 可覆盖库路径。db 模式下, 这里的 YAML 加载只是
        # 预检基线, _do_initialize 的 _load_db_config() 会用库内实体装配结果
        # 整体替换 self.config。
        source = os.environ.get("UNIHIVE_CONFIG_SOURCE", "db").strip().lower()
        # L1 (round 9 audit): 任何拼写错误 (如 "deeb") 旧实现静默当作 db 模式,
        # 用户以为逃生到了 yaml 却进了 db。fail-closed: 仅接受 db/yaml/空。
        if source not in ("", "db", "yaml"):
            raise ValueError(
                f"UNIHIVE_CONFIG_SOURCE must be 'db' or 'yaml' (got {source!r})"
            )
        self._db_mode = source != "yaml"
        # M10: 库路径锚定项目根, 从任意 CWD 启动都指向同一个 config.db,
        # 杜绝在 CWD 下静默生成空库重 seed 的裂脑; 绝对路径原样保留。
        from .core.config_db import resolve_config_db_path

        self._db_path = str(
            resolve_config_db_path(os.environ.get("UNIHIVE_CONFIG_DB"))
        )

        # YAML 预检基线。db 模式下真正的事实源是 config.db, YAML 损坏不得阻止
        # 健康库启动 (M9): 预检失败只告警并以空 dict 占位, _load_db_config()
        # 会用库内实体整体替换; 空库 seed 时坏模板仍在装配门 fail-closed (H4)。
        # yaml 逃生模式保持原严格语义: 加载/校验失败直接抛出。
        try:
            # Load upstreams.yaml
            # L3 (round 10 audit): 必须用锚定后的 self.config_path 而非原始
            # config_path 参数 — 后者在从非 repo 根启动时锚定 CWD, yaml 逃生
            # 模式下直接 FileNotFoundError; db 模式预检基线也会静默落空,
            # 空库首启拿不到出厂模板。
            self.config = load_config(
                str(self.config_path), strict_env=strict_env
            )

            # Load config.yaml and merge (config.yaml takes precedence for overlapping keys)
            config_yaml_path = self.config_path.parent / "config.yaml"
            if config_yaml_path.exists():
                try:
                    config_yaml = load_config(
                        str(config_yaml_path), strict_env=strict_env
                    )
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
        except Exception:
            if not self._db_mode:
                raise
            logger.warning(
                "YAML config preflight failed; continuing in db mode — "
                "config.db is authoritative (an empty DB will still fail "
                "closed when seeding from this template)",
                exc_info=True,
            )
            self.config = {}
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
        # publish_mode=hidden 的工具名 (仅 db 模式有内容): 不发布,
        # 上游热开关与启动注册都要跳过, 防止 backer 变动时被重新放出。
        self._db_hidden_tools: set[str] = set()
        # v2 配置实体库 (data/config.db), _do_initialize 早期装配;
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
        # gateway 调参 (per_upstream_timeout + 限流器): 先按 YAML 基线应用;
        # db 模式 _load_db_config 装配出库内配置后会再跑一次。
        self._apply_gateway_tuning()

    def _apply_gateway_tuning(self) -> None:
        """从当前 self.config 应用 gateway 段调参 (per_upstream_timeout + QPS)。

        __init__ 按 YAML 基线跑一次; db 模式下库内实体装配完成后再跑一次,
        让控制台对 gateway 调参段的修改无需重启即可被 router/limiter 读到。
        """
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

    async def _load_db_config(self) -> None:
        """以 data/config.db 实体表为事实源装配 self.config (v2, 2026-09-18)。

        YAML (upstreams.yaml + tools_*.yaml) 只是出厂模板: 空库自动从模板
        解构 seed, 之后运行时只读库内 sections / upstream / tool 实体,
        不再回读 YAML; config.yaml 仅贡献 bootstrap 子集 (console 整段 +
        gateway host/port/mcp_path, 永不入库)。设计见
        docs/design/2026-09-18-sqlite-config-full-database.md。

        - ``UNIHIVE_CONFIG_SOURCE=yaml`` 完全绕过 DB (故障逃生通道);
        - ``UNIHIVE_CONFIG_DB`` 覆盖默认路径 data/config.db;
        - 必须在任何 cache/client 构造之前执行: upstream.enabled 决定是否
          建连。装配结果 resolve env 后若过不了 validate_config, 直接
          fail-closed, 避免在错误配置上悄悄建连。
        """
        from .core.config_db import ensure_seeded, load_template_from_store
        from .core.db_assembler import assemble_config, merge_bootstrap
        from .core.template_normalize import load_raw_config
        from .utils import console_api as _ca
        from .utils.config_loader import resolve_env_vars

        if not self._uses_db_source():
            logger.info("UNIHIVE_CONFIG_SOURCE=yaml, 配置事实源=YAML 文件 (无 SQLite)")
            _ca.set_config_db(None)
            return

        store = ConfigStore(self._db_path)
        # M4 (round 9 audit): store.initialize() 纳入 try —— 自身失败 (库路径不可
        # 写 / 磁盘满) 时旧实现在 try 外抛, except 接不到, store.close() 不被
        # 调 → SQLAlchemy engine 泄漏。这里赋值在 try 前, except 安全调 close。
        try:
            await store.initialize()
            # 空库: 从未解析 env 的 YAML 模板解构 seed (明文密钥 fail-closed)。
            await ensure_seeded(store, self.config_path)
            template = await load_template_from_store(store)
            self._db_hidden_tools = {
                t.name for t in template.tools if t.publish_mode == "hidden"
            }
            assembled = assemble_config(template)
            # bootstrap 只从 config.yaml 合 console + 监听键; 其余段以库为准,
            # 不被文件旧值盖住 (浅合并, 与 yaml 加载链同语义)。
            config_yaml_raw = load_raw_config(self.config_path.parent / "config.yaml")
            merge_bootstrap(assembled, config_yaml_raw)
            # M6 (round 9 audit): strict 下也跳过 disabled 上游块的 ${VAR} 解析,
            # 与 validate_config 同语义 —— 禁用上游不要求环境变量齐备,
            # 否则控制台禁用 fuyao 但环境无其 key 时网关起不来 (热改却容忍)。
            resolved, missing = resolve_env_vars(
                assembled, strict=self._strict_env, skip_disabled_upstreams=True
            )
            if missing and not self._strict_env:
                logger.warning("Missing env vars (kept as placeholders): %s", missing)
            errors = validate_config(resolved)
            if errors:
                raise ValueError(
                    f"DB config validation failed ({len(errors)} error(s)): "
                    + "; ".join(errors)
                )
        except Exception:
            await store.close()
            raise

        self.config_store = store
        self.config = resolved
        # 库内 gateway 调参段可能与 YAML 预检基线不同, 重放一次。
        self._apply_gateway_tuning()
        revision = await store.revision()
        # 与 console_api 共享同一 live dict: 热开关/TTL 热改两边即时可见。
        _ca.set_config(resolved)
        _ca.set_config_db(store, revision)
        logger.info(
            "Loaded config from %s: %d upstreams, %d tools, revision=%d, template=%s",
            self._db_path,
            len(resolved.get("upstreams") or {}),
            len(resolved.get("tools") or []),
            revision,
            (await store.template_version_seeded() or "")[:12],
        )

    async def _do_initialize(self):
        logger.info("Initializing UniHive Gateway Server...")

        # console_api 是 set_omni_client 的目标。__init__ 里也 import 了
        # _ca, 那是给 set_config 用的; 这个 import 是给 _find_omni_client()
        # 之后的 _ca.set_omni_client() 用, 必须在 _do_initialize 局部可见。
        from .utils import console_api as _ca

        try:
            # v2 配置实体库: 必须在 cache/client 构造前完成装配,
            # upstream.enabled 决定上游是否建连。
            await self._load_db_config()

            # 初始化缓存
            cache_cfg = self.config.get("cache", {})
            self.cache = Cache(CacheConfig(
                enabled=cache_cfg.get("enabled", True),
                db_path=str(resolve_project_path(
                    cache_cfg.get("db_path"), "logs/cache.db"
                )),
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
                # H2 (round 9 audit): 单个上游构造/启动抛错 (如 mootdx2 非法
                # servers、tdx_quant 缺 DLL) 不得让整个网关拒启 —— 与
                # _start_upstream_client 已有的 "失败也登记为不可用" 语义一致,
                # 这里把构造也纳入 try, 该上游跳过 + log, 其余照常启动。
                # CancelledError (BaseException) 仍传播, 不被吞。
                try:
                    client = self._construct_upstream_client(name, cfg)
                    await self._start_upstream_client(name, cfg, client)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        "Failed to construct/start upstream %r (skipping, "
                        "other upstreams continue): %s",
                        name,
                        e,
                    )

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

            # 全量 spec (db 模式来自库内实体; yaml 模式此处合入生成文件并
            # 回填顶层 mapping), 存全量供热开关重算发布集。
            tool_specs = self._load_all_tools()
            # 存全量 spec 供热开关重算发布集; validate/注册逻辑不变。
            self.tool_specs = tool_specs

            # 2026-09-14: ProxySettings — 读 config/proxy_settings.yaml,
            # 控制 mootdx2 → tdx_quant 透明代理. yaml 不存在时自动 fallback
            # 到默认 (enabled=False), 不阻断 gateway 启动.
            proxy_settings = ProxySettings(
                path=str(resolve_project_path(
                    None, "config/proxy_settings.yaml"
                )),
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
            # M4 (round 9 audit): 取消半建的 cache cleanup task, 否则它引用
            # 即将关闭的 cache 且重试时会泄漏第二个 task。
            cleanup_task = getattr(self, "_cache_cleanup_task", None)
            if cleanup_task is not None:
                cleanup_task.cancel()
                try:
                    await cleanup_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._cache_cleanup_task = None
            if self.cache:
                try:
                    await asyncio.wait_for(self.cache.close(), timeout=2.0)
                except Exception:
                    pass
                self.cache = None
            # M4: v2 配置库 engine 也须 dispose, 否则直接 await initialize()
            # 的嵌入宿主/同实例重试会泄漏第二个 SQLAlchemy engine。
            config_store = getattr(self, "config_store", None)
            if config_store is not None:
                try:
                    await config_store.close()
                except Exception:
                    pass
                self.config_store = None
            # M4: 复位 console_api 写目标全局, 不再指向已死 server 的 store/config。
            _ca.set_config_db(None)
            _ca.set_config({})
            _ca.set_gateway_server(None)
            _ca.set_upstream_registry({})
            _ca.set_omni_client(None)
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
                # L3 (round 9 audit): 透传 tdx_remote_port, 否则 YAML 里配的非
                # 默认端口被静默丢弃, 运行时永远连默认 17709。
                tdx_remote_port=int(cfg.get("tdx_remote_port", 17709)),
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
                db_path=str(resolve_project_path(
                    cfg.get("db_path"), "./data/board.db"
                )),
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

        持久化 (config.db overlay) 由调用方 console_api 先完成, 本方法只改运行态
        self.config (与 console_api 共享同一 dict) 与已发布工具集。
        """
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        async with self._upstream_toggle_lock:
            # H3: cfg 必须锁内从 live config 现读。锁外捕获的 cfg 可能在等锁期间
            # 被 apply_upstream_edit/出厂升级整块替换成孤儿 dict —— 在孤儿上翻
            # enabled 会返回 200 但 live 真值未变, 且按旧连接块建 client。
            upstreams_cfg = self.config.get("upstreams") or {}
            if name not in upstreams_cfg:
                raise KeyError(f"Unknown upstream: {name}")
            cfg = upstreams_cfg[name]

            current = bool(cfg.get("enabled", False))
            if current == enabled:
                # 状态已一致: 常规零动作 no-op; 同时承担上次部分失败后的运行态自愈
                return await self._reconcile_upstream_locked(name, cfg, enabled)

            cfg["enabled"] = bool(enabled)
            if enabled:
                return await self._enable_upstream_locked(name, cfg)
            return await self._disable_upstream_locked(name)

    # ------------------------------------------------------------------
    # P3: 单工具实体热编辑 (config.db → lint 门 → 热重注册 → 失败回滚)
    # ------------------------------------------------------------------

    async def apply_tool_edit(
        self,
        name: str,
        patch: dict,
        *,
        confirm_dangerous: bool = False,
        expected_revision: int | None = None,
    ) -> dict:
        """编辑一个工具实体并立即热生效到对外 /mcp。

        流程 (设计 §6, docs/design/2026-09-18-sqlite-config-full-database.md):

        1. 字段白名单/形态校验 + 描述层/契约层分层: 触及 name/routing/
           requires_env/mapping 必须 ``confirm_dangerous=True``;
        2. 本地构造候选实体 → 装配出候选运行时 config (与启动同一条
           assemble → bootstrap → resolve env 链) → ``lint_config`` 全量
           静态校验, 有 error 抛 :class:`LintRejected`, 运行态/库均不动;
        3. toggle 锁内先切运行态 (config/tool_specs/router/hidden 集),
           再做 provider 换工具 (remove 旧名 → register 新 spec);
        4. 注册成功后写库 (乐观锁); 注册或写库任一步失败, 运行态与 provider
           全部回滚到编辑前, 异常原样抛给 HTTP 层;
        5. publish_mode=hidden / requires_env 门控 / 全 backer 禁用的候选
           只摘除不注册 (行保留, 可恢复)。

        Returns:
            ``{changed, name, revision, published, previously_published,
            contract_fields}``; 净效果无变化时 no-op 短路 (不含
            contract_fields 键), 不 bump revision、不碰 provider。
        """
        from .core.config_lint import ERROR as LINT_ERROR
        from .core.config_lint import LintRejected, lint_config
        from .core.entity_edit import (
            DangerousConfirmationRequired,
            apply_tool_patch,
            contract_fields_in_patch,
        )
        from .core.tool_loader import filter_specs_by_env
        from .utils import console_api as _ca

        store = self.config_store
        if store is None:
            raise RuntimeError(
                "tool editing requires the config.db entity source "
                "(UNIHIVE_CONFIG_SOURCE=db); current source has no store"
            )
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        entity = await store.get_tool(name)
        if entity is None:
            raise KeyError(name)

        contract_fields = sorted(contract_fields_in_patch(patch))
        if contract_fields and not confirm_dangerous:
            raise DangerousConfirmationRequired(contract_fields)

        # bundle 与其 revision 在同一快照读出 (plan 时刻): 即使调用方省略
        # expected_revision, 也以该 revision 为乐观锁基准 — plan 之后若有别的
        # 写入落库, 锁内落库必撞 RevisionConflict 并整笔回滚, 杜绝"锁外旧
        # bundle 构造候选 → 覆盖并发编辑" (H5)。
        bundle, plan_revision = await store.read_bundle()
        existing_names = {row["name"] for row in bundle["tools"]}
        updated = apply_tool_patch(entity, patch, existing_names=existing_names)
        old_name, new_name = name, updated["name"]
        if new_name != old_name and new_name in RESERVED_TOOL_NAMES:
            raise ValueError(
                f"tool name {new_name!r} is reserved for gateway built-in tools"
            )

        if ConfigStore._tool_entity_unchanged(entity, updated):
            currently_published = old_name in self._published_tools
            return {
                "changed": False,
                "name": old_name,
                "revision": await store.revision(),
                "published": currently_published,
                "previously_published": currently_published,
            }

        candidate, candidate_specs, candidate_hidden = self._build_tool_edit_candidate(
            bundle, old_name, updated
        )
        lint_errors = [
            issue
            for issue in lint_config(candidate, candidate_specs)
            if issue.severity == LINT_ERROR
        ]
        if lint_errors:
            raise LintRejected(lint_errors)

        async with self._upstream_toggle_lock:
            # 运行态快照 (全是引用替换, 无需深拷贝: 候选 config 是独立新 dict,
            # 回滚只是把引用换回来)。
            old_config = self.config
            old_specs = list(self.tool_specs)
            old_hidden = set(self._db_hidden_tools)
            previously_published = old_name in self._published_tools

            # 1) 切运行态: live config / env 门控 spec / hidden 集 / router。
            self.config = candidate
            _ca.set_config(candidate)
            self.tool_specs = candidate_specs
            self._db_hidden_tools = candidate_hidden
            if self.router is not None:
                self.router.routing_config = candidate.get("routing", {})
                self.router.upstream_tool_mapping = candidate.get(
                    "upstream_tool_mapping", {}
                )
                self.router.replace_tool_specs(candidate_specs)

            # 2) provider 换工具: 先摘旧名 (KeyError 幂等), 再注册候选。
            if previously_published:
                try:
                    self.mcp.local_provider.remove_tool(old_name)
                except KeyError:
                    logger.warning(
                        "tool edit: %r not on local provider (idempotent)",
                        old_name,
                    )
                self._published_tools.discard(old_name)

            new_spec = next(
                (s for s in candidate_specs if s["name"] == new_name), None
            )
            will_publish = new_name in {
                s["name"] for s in self._publishable_specs(candidate_specs)
            }
            published = False
            if new_spec is not None and will_publish:
                try:
                    register_tool(self, new_spec)
                except Exception as e:
                    self._rollback_tool_edit_locked(
                        old_config=old_config,
                        old_specs=old_specs,
                        old_hidden=old_hidden,
                        old_name=old_name,
                        new_name=new_name,
                        candidate_published=False,
                        previously_published=previously_published,
                    )
                    raise RuntimeError(f"re-register failed: {e}") from e
                self._published_tools.add(new_name)
                published = True

            # 3) 写库 (乐观锁)。失败 → 运行态 + provider 全量回滚。
            # expected 缺省钉住 plan 时刻 revision (非锁内重读, 锁内重读会
            # 让校验恒通过, 并发编辑被旧候选物理覆盖)。
            expected = (
                plan_revision
                if expected_revision is None
                else expected_revision
            )
            try:
                final_name, revision = await store.update_tool(
                    old_name, patch, expected_revision=expected
                )
            except Exception:
                self._rollback_tool_edit_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    old_name=old_name,
                    new_name=new_name,
                    candidate_published=published,
                    previously_published=previously_published,
                )
                raise

            # 4) 成功: console_api 共享同一 store 对象, 刷 revision 与缓存。
            _ca.set_config_db(store, revision)
            _ca.invalidate_config_caches()
            logger.info(
                "Tool %r edited (final=%r, rev=%d, published=%s, contract=%s)",
                old_name,
                final_name,
                revision,
                published,
                contract_fields or "-",
            )
            return {
                "changed": True,
                "name": final_name,
                "revision": revision,
                "published": published,
                "previously_published": previously_published,
                "contract_fields": contract_fields,
            }

    def _build_tool_edit_candidate(
        self, bundle: dict, old_name: str, updated: dict
    ) -> tuple[dict, list[dict], set[str]]:
        """把单个候选实体行换入 bundle, 走与启动同构的装配链产出候选运行态。

        返回 ``(resolved_config, env 门控后 specs, hidden 工具名集)``。
        不做 IO 写; bootstrap 仅在正常启动路径 (有 config_path) 时合并,
        __new__ 旁路 __init__ 的测试场景自动跳过。
        """
        from .core.db_assembler import (
            assemble_config,
            entities_from_json,
            merge_bootstrap,
        )
        from .core.template_normalize import load_raw_config
        from .core.tool_loader import filter_specs_by_env
        from .utils.config_loader import resolve_env_vars

        row_keys = (
            "name",
            "routing",
            "description",
            "cache_ttl_key",
            "requires_env",
            "publish_mode",
            "mapping",
            "spec",
            "source_file",
            "origin",
        )
        cand_bundle = copy.deepcopy(bundle)
        for idx, row in enumerate(cand_bundle.get("tools") or []):
            if row["name"] == old_name:
                cand_bundle["tools"][idx] = {
                    key: updated.get(key) for key in row_keys
                }
                break

        template = entities_from_json(cand_bundle)
        candidate = assemble_config(template)
        config_path = getattr(self, "config_path", None)
        if config_path is not None:
            merge_bootstrap(
                candidate, load_raw_config(config_path.parent / "config.yaml")
            )
        candidate, _missing = resolve_env_vars(
            candidate, strict=bool(getattr(self, "_strict_env", False))
        )
        candidate_specs = filter_specs_by_env(list(candidate.get("tools") or []))
        hidden = {
            tool.name for tool in template.tools if tool.publish_mode == "hidden"
        }
        return candidate, candidate_specs, hidden

    def _rollback_tool_edit_locked(
        self,
        *,
        old_config: dict,
        old_specs: list[dict],
        old_hidden: set[str],
        old_name: str,
        new_name: str,
        candidate_published: bool,
        previously_published: bool,
    ) -> None:
        """热编辑失败回滚: provider 摘候选 → 运行态复位 → 补发旧工具。

        补发旧工具若再失败只记录不抛 (原异常正在传播), 集合按 provider
        真实状态保留, 后续上游 reconcile 可自愈。
        """
        from .utils import console_api as _ca

        if candidate_published:
            try:
                self.mcp.local_provider.remove_tool(new_name)
            except KeyError:
                pass
            self._published_tools.discard(new_name)

        self.config = old_config
        _ca.set_config(old_config)
        self.tool_specs = old_specs
        self._db_hidden_tools = old_hidden
        if self.router is not None:
            self.router.routing_config = old_config.get("routing", {})
            self.router.upstream_tool_mapping = old_config.get(
                "upstream_tool_mapping", {}
            )
            self.router.replace_tool_specs(old_specs)

        if previously_published:
            old_spec = next(
                (s for s in old_specs if s["name"] == old_name), None
            )
            if old_spec is not None:
                try:
                    register_tool(self, old_spec)
                    self._published_tools.add(old_name)
                except Exception:
                    logger.exception(
                        "tool edit rollback: failed to re-register %r", old_name
                    )
                    self._published_tools.discard(old_name)

    async def apply_upstream_edit(
        self,
        name: str,
        config: dict,
        *,
        type: str | None = None,
        confirm_dangerous: bool = False,
        expected_revision: int | None = None,
    ) -> dict:
        """整体替换上游连接块 (可选改类型), 热生效。

        连接块（base_url / api_key / servers / 超时等）是上游建连契约,
        一律按契约层处理: 必须 ``confirm_dangerous=True``。明文密钥字段
        (非 ``${VAR}`` 占位) 在触碰运行态前 fail-closed 拒绝。

        顺序 (全部破坏性动作集中在 toggle 锁内):

        1. 锁外静态门: 形态校验 → 明文密钥扫描 → ``${VAR}`` 解析 (仅启用上游
           要求变量齐备; 库内永远只存占位) → 候选 config 全量 lint;
        2. 锁内, 启用且有变更: **先构造新 client** (纯构造失败 = 零损伤),
           启动登记新 client 后再停旧 client (先接后断, 无"无 client"空窗),
           换 live 块, 差集自愈重放;
        3. **落库最后做**: RevisionConflict 或发布失败 → 运行态整体回滚
           (摘新 client、复原 live 块、按旧块重建等价 client);
        4. 禁用上游: 只换 live 块 (保留 ``${VAR}`` 占位) + 落库, 不碰 client。

        建连本身失败 (超时/不可用) 按启动语义登记不可用 client、工具仍发布,
        不回滚 (与开机"配了但连不上"同态, connected=False 提示)。

        Returns:
            ``{changed, name, revision, enabled, connected, contract_fields}``。
        """
        from .core.config_lint import ERROR as LINT_ERROR
        from .core.config_lint import LintRejected, lint_config
        from .core.entity_edit import (
            DangerousConfirmationRequired,
            validate_upstream_config,
        )
        from .core.template_normalize import find_plaintext_secrets_in
        from .utils import console_api as _ca
        from .utils.config_loader import resolve_env_vars

        store = self.config_store
        if store is None:
            raise RuntimeError(
                "upstream editing requires the config.db entity source "
                "(UNIHIVE_CONFIG_SOURCE=db); current source has no store"
            )
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        bundle, plan_revision = await store.read_bundle()
        entity = next(
            (u for u in bundle.get("upstreams") or [] if u["name"] == name), None
        )
        if entity is None:
            raise KeyError(name)

        contract_fields = ["upstream.config"]
        new_type = type or entity["type"]
        if type is not None and type != entity["type"]:
            contract_fields.append("upstream.type")
        if not confirm_dangerous:
            raise DangerousConfirmationRequired(contract_fields)

        # 边界形态 + 明文密钥: 任何运行态/库变更之前先拒 (存储层还会再守)。
        clean_config = validate_upstream_config(config)
        leaked = find_plaintext_secrets_in(clean_config, f"upstreams.{name}")
        if leaked:
            raise ValueError(
                "Refusing to write plaintext secret field(s): " + ", ".join(leaked)
            )

        enabled = bool(entity["enabled"])
        changed = new_type != entity["type"] or self._canonical_config(
            entity.get("config") or {}
        ) != self._canonical_config(clean_config)

        # live 块形态; ${VAR} 只在运行态解析 —— 库内事实源永远保留占位 (H2)。
        candidate_block = {
            **copy.deepcopy(clean_config),
            "enabled": enabled,
            "type": new_type,
        }
        resolved_block, missing_env = resolve_env_vars(candidate_block, strict=False)
        live_block = resolved_block if enabled else candidate_block

        # lint 门: 只替换本块的候选完整 config 跑全量静态校验 (M7)。
        candidate_config = copy.deepcopy(self.config)
        candidate_config.setdefault("upstreams", {})[name] = live_block
        lint_errors = [
            issue
            for issue in lint_config(candidate_config, list(self.tool_specs))
            if issue.severity == LINT_ERROR
        ]
        if lint_errors:
            raise LintRejected(lint_errors)

        if not changed:
            # 仍然过一遍乐观锁: 过期 revision 的 no-op 也要 409; 等价写入不 bump、
            # 不翻 origin、不重连。
            revision = await store.update_upstream_config(
                name,
                clean_config,
                type=type,
                expected_revision=(
                    plan_revision if expected_revision is None else expected_revision
                ),
            )
            _ca.set_config_db(store, revision)
            _ca.invalidate_config_caches()
            current_client = self.upstreams.get(name)
            connected = bool(
                current_client is not None
                and getattr(current_client, "is_available", False)
            )
            return {
                "changed": False,
                "name": name,
                "revision": revision,
                "enabled": enabled,
                "connected": connected,
                "contract_fields": contract_fields,
            }

        # 启用上游但新块引用的环境变量缺失 = 新 client 必败 (如确定性 401),
        # 在任何状态变更前拒绝 (H2)。
        if enabled and missing_env:
            raise ValueError(
                "upstream config references env var(s) not set in the gateway "
                f"environment: {', '.join(missing_env)}; set them before applying "
                "(the stored block keeps ${VAR} placeholders)"
            )

        expected = plan_revision if expected_revision is None else expected_revision
        connected = False
        async with self._upstream_toggle_lock:
            old_live_block = (self.config.get("upstreams") or {}).get(name)
            old_client = self.upstreams.get(name)
            new_client = None
            if enabled:
                # 纯构造先于一切状态变更: 构造异常时旧 client/live/库全部不动。
                new_client = self._construct_upstream_client(name, resolved_block)

            self.config.setdefault("upstreams", {})[name] = live_block

            if enabled:
                old_client_stopped = False
                try:
                    # 先接后断: 新 client 启动并登记到同名槽位后再停旧 client。
                    connected = await self._start_upstream_client(
                        name, resolved_block, new_client
                    )
                    if isinstance(new_client, OmniClient):
                        _ca.set_omni_client(new_client)
                    elif isinstance(old_client, OmniClient):
                        _ca.set_omni_client(None)
                    if old_client is not None and old_client is not new_client:
                        await self._stop_one_upstream(name, old_client)
                        # M2 (round 9 audit): 标记旧 client 已停, 供回滚决定是
                        # 复用 old 还是重建 restored (见 _rollback_upstream_edit_locked)。
                        old_client_stopped = True
                    # 新连接块可能引入新 backer 可发布的工具, 重放一次差集自愈。
                    errors: list[str] = []
                    to_add, _ = self._toggle_tool_diff_locked(name)
                    self._publish_tools_locked(to_add, errors)
                    self._raise_if_toggle_errors(name, errors)
                except BaseException:  # noqa: BLE001 - 含 CancelledError, 回滚后重抛
                    await self._rollback_upstream_edit_locked(
                        name=name,
                        old_live_block=old_live_block,
                        old_client=old_client,
                        new_client=new_client,
                        old_client_stopped=old_client_stopped,
                    )
                    raise
            else:
                connected = False

            try:
                revision = await store.update_upstream_config(
                    name,
                    clean_config,
                    type=type,
                    expected_revision=expected,
                )
            except BaseException:  # noqa: BLE001 - 落库失败 (含 409), 运行态回滚
                # 落库失败路径: 成功段已把 old 停掉 (old_client_stopped=True 由
                # 成功路径设, 失败点之后的 flag 在此 try 外不可见, 用成功语义:
                # 若走到落库, 前面重连成功则 old 已停)。
                await self._rollback_upstream_edit_locked(
                    name=name,
                    old_live_block=old_live_block,
                    old_client=old_client,
                    new_client=new_client,
                    old_client_stopped=enabled,
                )
                raise

            _ca.set_config_db(store, revision)
            _ca.invalidate_config_caches()

        logger.info(
            "Upstream %r connection block edited (rev=%d, changed=%s, enabled=%s)",
            name,
            revision,
            changed,
            enabled,
        )
        return {
            "changed": True,
            "name": name,
            "revision": revision,
            "enabled": enabled,
            "connected": connected,
            "contract_fields": contract_fields,
        }

    async def _rollback_upstream_edit_locked(
        self,
        *,
        name: str,
        old_live_block: dict | None,
        old_client,
        new_client,
        old_client_stopped: bool = False,
    ) -> None:
        """apply_upstream_edit 运行态回滚 (调用方持 toggle 锁)。

        摘除已登记的新 client → live 块复原 → 旧 client 处置:
        - 旧 client **未被停** (cancel 发生在 start new 阶段, old 仍活): 重新
          登记回槽, 不另建 restored —— 否则 old 成无引用孤儿 + restored 双活
          (M2, round 9 audit);
        - 旧 client **已被停** (重连成功后落库/发布失败): 按旧 live 块重建等价
          client 接替 (old 已死, 不能复用)。
        复原失败只记录 (原异常正在传播), 漂移留待 reconcile 自愈。
        """
        from .utils import console_api as _ca

        if new_client is not None and self.upstreams.get(name) is new_client:
            self.upstreams.pop(name, None)
            await self._stop_one_upstream(name, new_client)

        if old_live_block is not None:
            self.config.setdefault("upstreams", {})[name] = old_live_block

        if old_client is None or old_client is new_client:
            return

        if not old_client_stopped:
            # M2: old 仍活 (其 stop 从未执行/完成) → 重新登记, 杜绝孤儿 + 重复构造。
            if self.upstreams.get(name) is not old_client:
                self.upstreams[name] = old_client
            if isinstance(old_client, OmniClient):
                _ca.set_omni_client(old_client)
            logger.warning(
                "upstream %r edit rolled back; old client still alive, re-registered",
                name,
            )
            return

        # old 已停 → 按旧 live 块重建等价 client 接替。
        try:
            restored = self._construct_upstream_client(name, old_live_block)
            await self._start_upstream_client(
                name, old_live_block, restored
            )
            if isinstance(restored, OmniClient):
                _ca.set_omni_client(restored)
            logger.warning(
                "upstream %r edit rolled back after runtime failure; "
                "client rebuilt from previous block",
                name,
            )
        except Exception:  # noqa: BLE001 - 复原失败不掩盖原异常
            logger.exception(
                "upstream %r edit rollback: previous client restore failed",
                name,
            )

    # ------------------------------------------------------------------
    # P3: 出厂模板更新 (三方合并 dry-run / 热 apply + 回滚)
    # ------------------------------------------------------------------

    @staticmethod
    def _canonical_config(value: object) -> str:
        """连接块/spec 等价比较的规范 JSON (env 已解析, 纯本地比较用)。"""
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)

    async def _prepare_config_upgrade(self, resolutions: dict[str, str] | None) -> dict:
        """出厂更新计划 (纯读): old 快照 + cur 实体 ↔ 新出厂 YAML 三方合并,

        装配候选运行态并跑全量 lint。不写库、不碰运行态/provider。
        """
        from .core import config_db as _cdb
        from .core.config_lint import ERROR as LINT_ERROR
        from .core.config_lint import WARNING as LINT_WARNING
        from .core.config_lint import lint_config

        store = self.config_store
        if store is None:
            raise RuntimeError(
                "factory upgrade requires the config.db entity source "
                "(UNIHIVE_CONFIG_SOURCE=db); current source has no store"
            )
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        resolutions = _cdb.validate_resolutions(resolutions)
        snapshot = await store.template_snapshot()
        if snapshot is None:
            raise RuntimeError(
                "config.db has no factory template snapshot; seed first"
            )
        old_version, old_bundle = snapshot
        # bundle 与 revision 同快照: apply 缺省 expected 时钉住此值 (H5)。
        cur_bundle, plan_revision = await store.read_bundle()

        config_path = getattr(self, "config_path", None)
        new_version, new_bundle, result = _cdb.plan_config_upgrade(
            old_bundle,
            cur_bundle,
            config_path=config_path,
            resolutions=resolutions,
        )
        candidate, candidate_specs, candidate_hidden = (
            _cdb.assemble_runtime_candidate(
                result.bundle,
                config_path=config_path,
                strict_env=bool(getattr(self, "_strict_env", False)),
            )
        )
        issues = lint_config(candidate, candidate_specs)
        lint_errors = [i for i in issues if i.severity == LINT_ERROR]
        lint_warnings = [i for i in issues if i.severity == LINT_WARNING]
        summary = result.summary()
        changed = bool(summary["added"] or summary["removed"] or summary["updated"])
        return {
            "resolutions": resolutions,
            "revision": plan_revision,
            "old_version": old_version,
            "new_version": new_version,
            "new_bundle": new_bundle,
            "result": result,
            "summary": summary,
            "changed": changed,
            "candidate": candidate,
            "candidate_specs": candidate_specs,
            "candidate_hidden": candidate_hidden,
            "lint_errors": lint_errors,
            "lint_warnings": lint_warnings,
        }

    @staticmethod
    def _upgrade_issue_json(issue) -> dict:
        return {
            "severity": issue.severity,
            "code": issue.code,
            "message": issue.message,
        }

    def _upgrade_tool_impact(
        self,
        old_specs: list[dict],
        candidate_specs: list[dict],
        candidate_config: dict,
        candidate_hidden: set[str],
    ) -> tuple[set[str], set[str], set[str]]:
        """全局工具发布差集 → (to_add, to_remove, republish)。

        republish = 新旧都应发布但 spec 内容变化的工具 (先摘后注)。
        wanted 按候选 config 的 enabled 链 + 候选 hidden 集计算, 不依赖
        self.config 已切换 (dry-run 也要能算)。
        """
        from .core.tool_loader import filter_specs_by_enabled

        wanted = {
            spec["name"]
            for spec in filter_specs_by_enabled(candidate_specs, candidate_config)
            if spec.get("name") not in candidate_hidden
        }
        old_by_name = {s["name"]: s for s in old_specs}
        new_by_name = {s["name"]: s for s in candidate_specs}
        old_published = set(getattr(self, "_published_tools", set()))
        republish = {
            name
            for name in old_published & wanted
            if self._canonical_config(old_by_name.get(name))
            != self._canonical_config(new_by_name.get(name))
        }
        to_remove = (old_published - wanted) | republish
        to_add = (wanted - old_published) | republish
        return to_add, to_remove, republish

    def _upgrade_upstream_impact(
        self, candidate_config: dict
    ) -> tuple[list[str], list[str], list[str]]:
        """据 live 已解析连接块与已挂载 client, 算 (attach, reconnect, detach)。"""
        old_blocks = self.config.get("upstreams") or {}
        new_blocks = candidate_config.get("upstreams") or {}
        attach: list[str] = []
        reconnect: list[str] = []
        detach: list[str] = []
        for name in sorted(set(old_blocks) | set(new_blocks)):
            old_block = old_blocks.get(name)
            new_block = new_blocks.get(name)
            attached = name in self.upstreams
            if not (new_block and new_block.get("enabled")):
                if attached:
                    detach.append(name)
                continue
            if not attached:
                attach.append(name)
            elif self._canonical_config(old_block) != self._canonical_config(
                new_block
            ):
                reconnect.append(name)
        return attach, reconnect, detach

    async def preview_config_upgrade(
        self, resolutions: dict[str, str] | None = None
    ) -> dict:
        """出厂更新 dry-run: 台账 + 冲突三向值 + lint + 运行态影响面, 零写入。"""
        plan = await self._prepare_config_upgrade(resolutions)
        result = plan["result"]
        old_specs = list(getattr(self, "tool_specs", []))
        to_add, to_remove, republish = self._upgrade_tool_impact(
            old_specs,
            plan["candidate_specs"],
            plan["candidate"],
            plan["candidate_hidden"],
        )
        attach, reconnect, detach = self._upgrade_upstream_impact(plan["candidate"])
        return {
            "changed": plan["changed"],
            "revision": plan["revision"],
            "old_version": plan["old_version"],
            "new_version": plan["new_version"],
            "template_changed": plan["old_version"] != plan["new_version"],
            "summary": plan["summary"],
            "sections": [c.to_json() for c in result.sections],
            "upstreams_changes": [c.to_json() for c in result.upstreams],
            "tools_changes": [c.to_json() for c in result.tools],
            "conflicts": [c.to_json() for c in result.conflicts],
            "resolutions_applied": sorted(plan["resolutions"]),
            "lint_errors": [self._upgrade_issue_json(i) for i in plan["lint_errors"]],
            "lint_warnings": [
                self._upgrade_issue_json(i) for i in plan["lint_warnings"]
            ],
            "impact": {
                "tools_added": sorted(to_add - republish),
                "tools_removed": sorted(to_remove - republish),
                "tools_republished": sorted(republish),
                "upstreams_attach": attach,
                "upstreams_reconnect": reconnect,
                "upstreams_detach": detach,
            },
        }

    async def apply_config_upgrade(
        self,
        *,
        resolutions: dict[str, str] | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        """热应用出厂更新: 合并 → lint 门 → 备份 → 运行态切换 → 单事务落库。

        - 仍有未裁决字段冲突 → ValueError (HTTP 400), 什么都不动;
        - lint error → LintRejected (HTTP 422), 什么都不动;
        - 无出厂变化 → no-op;
        - 运行态: live config/specs/hidden/router 引用替换, 按连接块差集
          detach/attach 上游 (建连失败仅警告, 与开机"配了但连不上"同态),
          按发布差集摘除/重发/重注册工具;
        - provider 切换或落库失败 → 运行态/已挂 client 尽力回滚, 库不动;
        - 落库同时把 config_template 快照前推到新出厂 bundle (下次合并基线)。
        """
        import asyncio

        from .core import config_db as _cdb
        from .core.config_lint import LintRejected
        from .core.config_upgrade import section_origins, unresolved_conflicts
        from .utils import console_api as _ca

        plan = await self._prepare_config_upgrade(resolutions)
        result = plan["result"]
        pending = unresolved_conflicts(result, plan["resolutions"])
        if pending:
            keys = ", ".join(c.key for c in pending[:10])
            raise ValueError(
                f"{len(pending)} unresolved field conflict(s): {keys}; "
                "resolve each with keep_cur/accept_new before applying"
            )
        if plan["lint_errors"]:
            raise LintRejected(plan["lint_errors"])
        if not plan["changed"]:
            return {
                "changed": False,
                "revision": plan["revision"],
                "old_version": plan["old_version"],
                "new_version": plan["new_version"],
                "summary": plan["summary"],
            }

        store = self.config_store
        cur_bundle = await store.load_bundle()
        seeded_version = await store.template_version_seeded()
        backup_dir = Path(store.db_path).parent / "backups"
        # 备份是升级回滚的唯一凭据: 任何状态切换前先落盘。
        backup_path = await asyncio.to_thread(
            _cdb.write_upgrade_backup,
            backup_dir,
            cur_bundle,
            revision=plan["revision"],
            template_version_seeded=seeded_version,
        )

        candidate = plan["candidate"]
        candidate_specs = plan["candidate_specs"]
        candidate_hidden = plan["candidate_hidden"]

        async with self._upstream_toggle_lock:
            old_config = self.config
            old_specs = list(self.tool_specs)
            old_hidden = set(self._db_hidden_tools)
            old_blocks = old_config.get("upstreams") or {}
            new_blocks = candidate.get("upstreams") or {}

            # 影响面必须在引用切换前算: 差集基于当前 live 块与已挂 client。
            to_add, to_remove, republish = self._upgrade_tool_impact(
                old_specs, candidate_specs, candidate, candidate_hidden
            )
            attach_names, reconnect_names, detach_names = (
                self._upgrade_upstream_impact(candidate)
            )

            # 1) 运行态引用切换 (独立新 dict, 回滚只是换回引用)。
            self.config = candidate
            _ca.set_config(candidate)
            self.tool_specs = candidate_specs
            self._db_hidden_tools = candidate_hidden
            if self.router is not None:
                self.router.routing_config = candidate.get("routing", {})
                self.router.upstream_tool_mapping = candidate.get(
                    "upstream_tool_mapping", {}
                )
                self.router.replace_tool_specs(candidate_specs)

            warnings: list[str] = []
            attached: list[str] = []
            reconnected: list[str] = []
            detached: list[str] = []
            errors: list[str] = []

            # 2) 先摘工具 (M8): 任何 client 断开前, 失去 backer / spec 变更的
            #    工具必须先从 provider 摘除, 杜绝切换窗内新调用被路由到正在
            #    关闭的 client。此时 client 全部未动, 回滚只需补注册。
            removed = self._unpublish_tools_locked(to_remove, errors)
            if errors:
                await self._rollback_config_upgrade_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    to_add=set(),
                    to_remove=to_remove,
                    switched_upstreams=[],
                    old_blocks=old_blocks,
                )
                self._raise_if_toggle_errors("factory-upgrade", errors)

            # 3) 上游 client 差集收敛 (先断后连; 失败只警告, 不阻断升级)。
            for name in sorted(set(detach_names) | set(reconnect_names)):
                if name in self.upstreams:
                    await self._detach_upstream_client_locked(name)
                    if name in detach_names:
                        detached.append(name)
            for name in sorted(set(attach_names) | set(reconnect_names)):
                new_block = new_blocks.get(name)
                try:
                    _client, connected = await self._attach_upstream_client_locked(
                        name, new_block
                    )
                except asyncio.CancelledError:
                    # M3 (round 9 audit): CancelledError 是 BaseException, 旧
                    # ``except Exception`` 接不住 → 直接逃出锁, live config/specs
                    # 已换、部分 client 已 detach, DB 未变 → 长期半迁移。回滚后重抛。
                    (reconnected if name in old_blocks else attached).append(name)
                    await self._rollback_config_upgrade_locked(
                        old_config=old_config,
                        old_specs=old_specs,
                        old_hidden=old_hidden,
                        to_add=to_add,
                        to_remove=to_remove,
                        switched_upstreams=attached + reconnected + detached,
                        old_blocks=old_blocks,
                    )
                    raise
                except Exception as e:  # noqa: BLE001 - 建连失败按开机语义降级
                    warnings.append(
                        f"upstream {name}: attach failed: "
                        f"{type(e).__name__}: {e}"
                    )
                    logger.exception("factory upgrade: attach upstream %r failed", name)
                    # 仍记入切换清单: 失败的 client 可能已登记, 回滚路径要尝试复原。
                    (reconnected if name in old_blocks else attached).append(name)
                    continue
                if name in reconnect_names or name in old_blocks:
                    reconnected.append(name)
                else:
                    attached.append(name)
                if not connected:
                    warnings.append(
                        f"upstream {name}: configured but not connected "
                        "(tools stay published; same semantics as startup)"
                    )

            # 4) 新 client 就位后再发布新工具/重发布 (单工具失败收集, 必须全
            #    成功才落库; 失败 → client 与 provider 整体回滚)。
            added = self._publish_tools_locked(to_add, errors)
            if errors:
                await self._rollback_config_upgrade_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    to_add=to_add,
                    to_remove=to_remove,
                    switched_upstreams=attached + reconnected + detached,
                    old_blocks=old_blocks,
                )
                self._raise_if_toggle_errors("factory-upgrade", errors)

            # 4) 单事务落库 + 快照前推 (乐观锁)。失败 → 运行态尽力回滚。
            # expected 缺省钉住 plan 时刻 revision: plan→apply 之间若有并发
            # 实体写入, 全表重写必须失败回滚, 不得物理抹掉并发改动 (H5)。
            expected = (
                plan["revision"]
                if expected_revision is None
                else expected_revision
            )
            try:
                revision = await store.apply_upgrade_bundle(
                    plan["new_version"],
                    plan["new_bundle"],
                    result.bundle,
                    section_origins=section_origins(
                        plan["new_bundle"], result.bundle
                    ),
                    expected_revision=expected,
                )
            except BaseException:  # noqa: BLE001 - M3: 含 CancelledError, 回滚后重抛
                await self._rollback_config_upgrade_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    to_add=to_add,
                    to_remove=to_remove,
                    switched_upstreams=attached + reconnected + detached,
                    old_blocks=old_blocks,
                )
                raise

        _ca.set_config_db(store, revision)
        _ca.invalidate_config_caches()
        logger.info(
            "Factory upgrade applied: template %s -> %s, rev=%d, "
            "tools +%d/-%s/republish %d, upstreams attached=%s reconnected=%s "
            "detached=%s, backup=%s",
            plan["old_version"][:12],
            plan["new_version"][:12],
            revision,
            len(added),
            len(removed),
            len(republish),
            attached,
            reconnected,
            detached,
            backup_path,
        )
        return {
            "changed": True,
            "revision": revision,
            "old_version": plan["old_version"],
            "new_version": plan["new_version"],
            "summary": plan["summary"],
            "sections": [c.to_json() for c in result.sections],
            "upstreams_changes": [c.to_json() for c in result.upstreams],
            "tools_changes": [c.to_json() for c in result.tools],
            "resolutions_applied": sorted(plan["resolutions"]),
            "lint_warnings": [
                self._upgrade_issue_json(i) for i in plan["lint_warnings"]
            ],
            "tools_added": sorted(set(added) - republish),
            "tools_removed": sorted(set(removed) - republish),
            "tools_republished": sorted(republish),
            "upstreams_attached": attached,
            "upstreams_reconnected": reconnected,
            "upstreams_detached": detached,
            "warnings": warnings,
            "backup_path": str(backup_path),
        }

    async def _rollback_config_upgrade_locked(
        self,
        *,
        old_config: dict,
        old_specs: list[dict],
        old_hidden: set[str],
        to_add: set[str],
        to_remove: set[str],
        switched_upstreams: list[str],
        old_blocks: dict,
    ) -> None:
        """升级失败回滚 (正向顺序的逆序): 摘新工具 → client 复原 → 补注册
        旧工具 → 运行态引用复位。

        与正向 "先摘工具 → client 切换 → 再发布" 严格互逆: 旧 client 复原
        之后才补注册旧工具, 杜绝补回的工具瞬间路由到已关闭 client。所有
        补发/重连均尽力而为, 失败只记录 (原异常正在传播); 复位后
        _published_tools 以编辑前快照为准, 漂移留待上游 reconcile 自愈。
        """
        from .utils import console_api as _ca

        for name in sorted(to_add):
            try:
                self.mcp.local_provider.remove_tool(name)
            except KeyError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("upgrade rollback: remove %r failed", name)
            self._published_tools.discard(name)

        for name in switched_upstreams:
            old_block = old_blocks.get(name)
            try:
                if name in self.upstreams:
                    await self._detach_upstream_client_locked(name)
                if old_block and old_block.get("enabled"):
                    await self._attach_upstream_client_locked(name, old_block)
            except Exception:  # noqa: BLE001
                logger.exception("upgrade rollback: restore upstream %r failed", name)

        old_by_name = {s["name"]: s for s in old_specs}
        for name in sorted(to_remove):
            spec = old_by_name.get(name)
            if spec is None:
                continue
            try:
                register_tool(self, spec)
                self._published_tools.add(name)
            except Exception:  # noqa: BLE001
                logger.exception("upgrade rollback: re-register %r failed", name)
                self._published_tools.discard(name)

        self.config = old_config
        _ca.set_config(old_config)
        self.tool_specs = old_specs
        self._db_hidden_tools = old_hidden
        if self.router is not None:
            self.router.routing_config = old_config.get("routing", {})
            self.router.upstream_tool_mapping = old_config.get(
                "upstream_tool_mapping", {}
            )
            self.router.replace_tool_specs(old_specs)

    # ------------------------------------------------------------------
    # P4: FQGate OpenAPI 实时导入 (origin='user' 工具行热注册)
    # ------------------------------------------------------------------

    async def _prepare_openapi_import(
        self, upstream_name: str, selected_names: list[str] | None
    ) -> dict:
        """OpenAPI 导入计划 (纯读): 活拉 OpenAPI + tools/list → 转换 → 差异 →
        候选运行态 → lint → 发布差集 → YAML 片段。不写库、不碰运行态/provider。

        - 上游未在 config 注册 → KeyError (HTTP 404);
        - 已配置但当前未挂载 client (禁用/启动建连失败) → RuntimeError (503);
        - client 不具备 OpenAPI 导入能力 (非 MCP 网关型) → ValueError (400);
        - 上游拉取异常 (OpenApiFetchError / tools/list !ok) → 原样抛 (502)。
        """
        from .core import config_db as _cdb
        from .core import openapi_import as _oi
        from .core.config_lint import ERROR as LINT_ERROR
        from .core.config_lint import WARNING as LINT_WARNING
        from .core.config_lint import lint_config

        store = self.config_store
        if store is None:
            raise RuntimeError(
                "OpenAPI import requires the config.db entity source "
                "(UNIHIVE_CONFIG_SOURCE=db); current source has no store"
            )
        if self.mcp is None or not getattr(self, "tool_specs", None):
            raise RuntimeError("Gateway not initialized")

        client = self.upstreams.get(upstream_name)
        if client is None:
            if upstream_name not in (self.config.get("upstreams") or {}):
                raise KeyError(upstream_name)
            raise RuntimeError(
                f"upstream {upstream_name!r} is configured but not connected "
                "(disabled or startup attach failed)"
            )
        fetch_openapi = getattr(client, "fetch_openapi", None)
        list_tools_raw = getattr(client, "list_tools_raw", None)
        if not callable(fetch_openapi) or not callable(list_tools_raw):
            raise ValueError(
                f"upstream {upstream_name!r} does not support OpenAPI import "
                "(client has no fetch_openapi/list_tools_raw)"
            )

        openapi_doc = await fetch_openapi()
        raw = await list_tools_raw()
        if not getattr(raw, "ok", False):
            raise RuntimeError(
                f"upstream {upstream_name!r} tools/list failed: "
                f"{getattr(raw, 'error', None)}"
            )

        entities, unmapped = _oi.convert_openapi_tools(
            openapi_doc, list(raw.tools or [])
        )
        # bundle 与 revision 同快照: apply 缺省 expected 时钉住此值 (H5)。
        cur_bundle, plan_revision = await store.read_bundle()
        plan = _oi.plan_openapi_import(
            cur_bundle,
            entities,
            upstream_label=upstream_name,
            unmapped=unmapped,
        )
        selected_set = set(selected_names) if selected_names is not None else None
        # change_entities 对不在 added/changed 集的名字抛 ValueError
        selected = plan.change_entities(selected_set)
        merged_bundle = _oi.merge_imported_tools(cur_bundle, selected)
        final_names = [e["name"] for e in selected]
        # 落库行取合并后的实体: 已存在行带保留的 position/门控列 (store 还会
        # 再保一次, 这里保证候选运行态与落库行同源)。
        upsert_rows = [
            row for row in merged_bundle["tools"] if row["name"] in set(final_names)
        ]

        config_path = getattr(self, "config_path", None)
        candidate, candidate_specs, candidate_hidden = (
            _cdb.assemble_runtime_candidate(
                merged_bundle,
                config_path=config_path,
                strict_env=bool(getattr(self, "_strict_env", False)),
            )
        )
        issues = lint_config(candidate, candidate_specs)
        to_add, to_remove, republish = self._upgrade_tool_impact(
            list(self.tool_specs),
            candidate_specs,
            candidate,
            candidate_hidden,
        )
        fragment = _oi.render_yaml_fragment(selected)
        return {
            "revision": plan_revision,
            "plan": plan,
            "unmapped": plan.unmapped,
            "selected": final_names,
            "upsert_rows": upsert_rows,
            "cur_bundle": cur_bundle,
            "candidate": candidate,
            "candidate_specs": candidate_specs,
            "candidate_hidden": candidate_hidden,
            "lint_errors": [i for i in issues if i.severity == LINT_ERROR],
            "lint_warnings": [i for i in issues if i.severity == LINT_WARNING],
            "to_add": to_add,
            "to_remove": to_remove,
            "republish": republish,
            "yaml_fragment": fragment,
        }

    async def preview_openapi_import(
        self,
        *,
        upstream: str = "THS-LOCAL-GATEWAY",
        tools: list[str] | None = None,
    ) -> dict:
        """OpenAPI 导入 dry-run: 差异台账 + lint + 发布影响面 + YAML 片段, 零写入。"""
        prep = await self._prepare_openapi_import(upstream, tools)
        plan = prep["plan"]
        return {
            "changed": bool(prep["selected"]),
            "revision": prep["revision"],
            "upstream": upstream,
            "summary": plan.summary(),
            "changes": [c.to_json() for c in plan.changes],
            "unmapped": [u.to_json() for u in prep["unmapped"]],
            "removed": list(plan.removed),
            "selected": prep["selected"],
            "lint_errors": [self._upgrade_issue_json(i) for i in prep["lint_errors"]],
            "lint_warnings": [
                self._upgrade_issue_json(i) for i in prep["lint_warnings"]
            ],
            "impact": {
                "tools_added": sorted(prep["to_add"] - prep["republish"]),
                "tools_removed": sorted(prep["to_remove"] - prep["republish"]),
                "tools_republished": sorted(prep["republish"]),
                # 导入只动工具契约, 永不重连/断开上游
                "upstreams_attach": [],
                "upstreams_reconnect": [],
                "upstreams_detach": [],
            },
            "yaml_fragment": prep["yaml_fragment"],
        }

    async def apply_openapi_import(
        self,
        *,
        upstream: str = "THS-LOCAL-GATEWAY",
        tools: list[str] | None = None,
        confirm_dangerous: bool = False,
        expected_revision: int | None = None,
    ) -> dict:
        """热应用 OpenAPI 导入: lint 门 → 强确认 → 备份 → 运行态切换 →
        provider 摘/注 (只 add/republish, **永不删除**) → 单事务 upsert_tools。

        provider 切换或落库失败 → 运行态/provider 尽力回滚, 库不动;
        ``plan.removed`` 仅作顾问信息展示, apply 路径不删任何工具行。
        """
        from .core import config_db as _cdb
        from .core.config_lint import LintRejected
        from .core.entity_edit import DangerousConfirmationRequired
        from .utils import console_api as _ca

        prep = await self._prepare_openapi_import(upstream, tools)
        if prep["lint_errors"]:
            raise LintRejected(prep["lint_errors"])

        plan = prep["plan"]
        selected = prep["selected"]
        if not selected:
            return {
                "changed": False,
                "revision": prep["revision"],
                "upstream": upstream,
                "summary": plan.summary(),
            }

        # 导入即新增/改写工具契约 (params/mapping/routing), 一律契约层强确认。
        if not confirm_dangerous:
            raise DangerousConfirmationRequired(
                [f"tool:{name}:contract" for name in selected]
            )

        store = self.config_store
        seeded_version = await store.template_version_seeded()
        backup_dir = Path(store.db_path).parent / "backups"
        backup_path = await asyncio.to_thread(
            _cdb.write_upgrade_backup,
            backup_dir,
            prep["cur_bundle"],
            revision=prep["revision"],
            template_version_seeded=seeded_version,
        )

        candidate = prep["candidate"]
        candidate_specs = prep["candidate_specs"]
        candidate_hidden = prep["candidate_hidden"]
        to_add, to_remove, republish = (
            prep["to_add"], prep["to_remove"], prep["republish"]
        )

        async with self._upstream_toggle_lock:
            old_config = self.config
            old_specs = list(self.tool_specs)
            old_hidden = set(self._db_hidden_tools)

            # 1) 运行态引用切换 (独立新 dict, 回滚只是换回引用)。
            self.config = candidate
            _ca.set_config(candidate)
            self.tool_specs = candidate_specs
            self._db_hidden_tools = candidate_hidden
            if self.router is not None:
                self.router.routing_config = candidate.get("routing", {})
                self.router.upstream_tool_mapping = candidate.get(
                    "upstream_tool_mapping", {}
                )
                self.router.replace_tool_specs(candidate_specs)

            # 2) provider: 契约变化的先摘, 新增/重注的后发; 无删除语义。
            errors: list[str] = []
            removed = self._unpublish_tools_locked(to_remove, errors)
            added = self._publish_tools_locked(to_add, errors)
            if errors:
                await self._rollback_config_upgrade_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    to_add=to_add,
                    to_remove=to_remove,
                    switched_upstreams=[],
                    old_blocks={},
                )
                self._raise_if_toggle_errors("openapi-import", errors)

            # 3) 单事务批量落库 (乐观锁; origin 恒 user, 门控列 store 保留)。
            # expected 缺省钉住 prep 时刻 revision, plan→apply 之间的并发
            # 写入必须让导入失败回滚, 不得被批量 upsert 静默冲掉 (H5)。
            expected = (
                prep["revision"]
                if expected_revision is None
                else expected_revision
            )
            try:
                revision = await store.upsert_tools(
                    prep["upsert_rows"], expected_revision=expected
                )
            except Exception:
                await self._rollback_config_upgrade_locked(
                    old_config=old_config,
                    old_specs=old_specs,
                    old_hidden=old_hidden,
                    to_add=to_add,
                    to_remove=to_remove,
                    switched_upstreams=[],
                    old_blocks={},
                )
                raise

        _ca.set_config_db(store, revision)
        _ca.invalidate_config_caches()
        logger.info(
            "OpenAPI import applied from %s: rev=%d, tools +%s/republish %s, "
            "unmapped=%d, backup=%s",
            upstream,
            revision,
            sorted(set(added) - republish),
            sorted(republish),
            len(prep["unmapped"]),
            backup_path,
        )
        return {
            "changed": True,
            "revision": revision,
            "upstream": upstream,
            "summary": plan.summary(),
            "changes": [c.to_json() for c in plan.changes],
            "unmapped": [u.to_json() for u in prep["unmapped"]],
            "removed": list(plan.removed),
            "selected": selected,
            "lint_warnings": [
                self._upgrade_issue_json(i) for i in prep["lint_warnings"]
            ],
            "tools_added": sorted(set(added) - republish),
            "tools_removed": sorted(set(removed) - republish),
            "tools_republished": sorted(republish),
            "upstreams_attached": [],
            "upstreams_reconnected": [],
            "upstreams_detached": [],
            "warnings": [],
            "backup_path": str(backup_path),
            "yaml_fragment": prep["yaml_fragment"],
        }

    # ------------------------------------------------------------------
    # 热开关内部步骤 (调用方须持 _upstream_toggle_lock)
    # ------------------------------------------------------------------

    def _publishable_specs(self, specs: list[dict] | None) -> list[dict]:
        """当前真正应发布到对外 /mcp 的 spec: env 门控后再过

        ``filter_specs_by_enabled`` (多 backer: 至少一个启用才保留) 与
        ``publish_mode=hidden`` 下架集。启动注册、上游热开关差集、单工具
        热编辑三处共用同一判定, 保证 hidden 工具不会从任何路径漏回。
        """
        from .core.tool_loader import filter_specs_by_enabled

        if not specs:
            return []
        # getattr 兜底: __new__ 旁路 __init__ 的热开关测试不构造该属性。
        hidden = getattr(self, "_db_hidden_tools", None) or set()
        return [
            spec
            for spec in filter_specs_by_enabled(specs, self.config)
            if spec.get("name") not in hidden
        ]

    def _toggle_tool_diff_locked(self, name: str) -> tuple[set[str], set[str]]:
        """计算单个上游背书工具的发布差集 → (to_add, to_remove)。

        全局期望集经 filter_specs_by_enabled (多 backer 语义), 再与
        backing_upstreams 含 name 的工具取交 —— 自愈重放时只碰本上游的
        工具, 不顺手摘补其他上游的漂移。
        """
        from .core.tool_loader import backing_upstreams

        wanted = {s["name"] for s in self._publishable_specs(self.tool_specs)}
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
        """全量工具 spec (运行时 env 门控后)。

        db 模式: 实体装配时 290 条已在 config['tools'], 这里只补运行时
        requires_env 门控 (与 yaml 模式 load_all_tools 默认行为一致),
        不再回读任何 tools_*.yaml。
        yaml 模式: 委托 tool_loader 合并内联 + 生成文件。
        """
        from .core.tool_loader import filter_specs_by_env, load_all_tools

        if self._uses_db_source():
            return filter_specs_by_env(list(self.config.get("tools") or []))
        return load_all_tools(self.config_path, self.config)

    def _uses_db_source(self) -> bool:
        """配置事实源是否为 config.db。__init__ 正常路径读实例属性;
        __new__ 旁路 __init__ 的测试回落环境变量 (autouse fixture 钉 yaml)。"""
        flag = getattr(self, "_db_mode", None)
        if flag is not None:
            return bool(flag)
        return os.environ.get("UNIHIVE_CONFIG_SOURCE", "db").strip().lower() != "yaml"

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
            # 发布集 = 至少一个启用 backer 且未被 publish_mode=hidden 下架。
            # 热开关见 set_upstream_enabled(), 启动语义与其差集计算一致。
            publishable = self._publishable_specs(specs)
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
        # L8 (round 9 audit): stop() 末尾 set _shutdown_event 唤醒旧 watcher,
        # 但从不 clear → 同实例 stdio restart 时新 watcher `await event.wait()`
        # 立即返回, cancel 当前 main_task → run_stdio_async 没跑就被杀。
        # 建 watcher 前清掉, 让残留 set 不影响重启。
        self._shutdown_event.clear()
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
        self._running = True
        # M5 (2026-09-07 5th-round audit): initialize() 失败时重置 _running 并
        # 调 stop() 清 partial state, 允许同实例重试。
        try:
            await self.initialize()
        except BaseException:
            await self.stop()
            raise
        # M5 (round 9 audit): host/port/mcp_path 默认值必须在 initialize() 之后
        # 从 self.config 取。M9 路径下 __init__ 预检失败时 self.config={} (或无
        # gateway 段); 旧实现在 init 前快照 → 静默回落 127.0.0.1:18080/mcp,
        # DB/config.yaml 改的端口永不生效。显式参数 (CLI) 仍优先。
        gw_cfg = self.config.get("gateway", {})
        host = host if host is not None else gw_cfg.get("host", "127.0.0.1")
        port = port if port is not None else gw_cfg.get("port", 18080)
        mcp_path = mcp_path if mcp_path is not None else gw_cfg.get("mcp_path", "/mcp")

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
            # M3: 非法 JSON 体 → 400 (旧实现裸 await req.json() 直接 500);
            # fuyao_cache 缺失/null 不再被 ``or {}`` 静默当成清空。
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"error": "invalid JSON body"}, status_code=400)
            try:
                payload, expected_version = _ca.parse_fuyao_cache_put_body(body)
            except ValueError as e:
                return JSONResponse(
                    {"error": "validation_failed", "message": str(e)},
                    status_code=400,
                )
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
        # H4 (round 9 audit): 路由装配段 (mcp.http_app / NoSlashStarlette /
        # setup_console_auth) 抛错时旧实现绕过 stop() → 半建 inner_app/mcp
        # 泄漏。装配段纳入 try, 崩溃也 stop()。上面的 handler 定义只声明闭包,
        # 不会抛, 留在 try 外。
        try:
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
                    # H1 (round 10 audit): 用布局感知的 _ca.STATIC_DIR (dev=repo/static,
                    # installed=site-packages/static 经 wheel force-include 落地);
                    # 旧实现写死 4-parents 目录, 非 editable wheel 安装下目录不存在,
                    # StaticFiles 构造期即 raise → 网关拒启。
                    Mount("/static", StaticFiles(directory=_ca.STATIC_DIR, html=False)),
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
                # M10: 传 mcp_path 让配置的 MCP 端点免鉴权 (不再写死 /mcp)。
                setup_console_auth(inner_app, auth_cfg, mcp_path=mcp_path)
                logger.info("Console auth enabled (UNIHIVE_CONSOLE_* set)")
            else:
                logger.warning(f"Console auth disabled: {auth_reason}")

            app = make_mcp_path_canonicalizer(inner_app, mcp_path)
            log_level = self.config.get("logging", {}).get("level", "info").lower()
            # L12 (round 9 audit): uvicorn 默认 timeout_graceful_shutdown=None
            # (无硬上限), 长闲连接会拖住 SIGTERM 远超 stop() 的 max_wait=10s。
            # 与 stop() 的 max_wait 对齐, 让 uvicorn 在同一预算内强收连接。
            config = uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level=log_level,
                timeout_graceful_shutdown=10,
            )
        except BaseException:
            await self.stop()
            raise

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
        # v2: 摘掉 config.db 写目标与实体模式标记, 支持同实例 stop/start
        _ca.set_config_db(None)
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
        self._db_hidden_tools = set()
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
