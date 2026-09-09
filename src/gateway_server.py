"""
UniHive MCP Gateway Server - 统一 MCP 网关
同时 serve MCP 接口和管理控制台。
"""
import argparse
import asyncio
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
from .utils.config_loader import load_config, validate_config
from .utils.log_config import configure_logging
from .core.normalizer import Normalizer
from .core.registry import register_tools_from_config, validate_specs
from .core.router import Router
from .api.upstream_client import UpstreamClient, UpstreamConfig
from .api.fuyao_client import FuyaoClient, FuyaoConfig
from .api.mootdx2_client import MooTDX2Client, MooTDX2Config
from .api.tdx_quant_client import TdxQuantClient
from .api.omni_client import OmniClient, OmniConfig
from .models.tdx_quant_config import TdxQuantConfig, TdxQuantSettings

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
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            return m.group(1)
    except Exception as e:
        logger.warning(f"failed to read gateway version from pyproject.toml: {e}")
    return "unknown"


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

    Config priority (first found wins):
      1. config/config.yaml (console section)
      2. config/upstreams.yaml (console section)
      3. Environment variables

    config.yaml fields:
      console.username
      console.password / console.password_hash
      console.session_secret
    """
    # Try config.yaml first
    username = None
    password = None
    password_hash = None
    secret = None

    # Load config.yaml if it exists
    config_yaml_path = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    if config_yaml_path.exists():
        try:
            with open(config_yaml_path, encoding="utf-8") as f:
                config_yaml = yaml.safe_load(f) or {}
            console_cfg = config_yaml.get("console", {})
            if console_cfg:
                username = console_cfg.get("username", "").strip() if console_cfg.get("username") else None
                password = console_cfg.get("password", "").strip() if console_cfg.get("password") else None
                password_hash = console_cfg.get("password_hash", "").strip() if console_cfg.get("password_hash") else None
                secret = console_cfg.get("session_secret", "").strip() if console_cfg.get("session_secret") else None
        except Exception as e:
            logger.warning(f"Failed to load config.yaml: {e}")

    # Fallback to upstreams.yaml config
    if not username and config:
        console_cfg = config.get("console", {})
        if console_cfg:
            username = console_cfg.get("username", "").strip() if console_cfg.get("username") else None
            password = console_cfg.get("password", "").strip() if console_cfg.get("password") else None
            password_hash = console_cfg.get("password_hash", "").strip() if console_cfg.get("password_hash") else None
            secret = console_cfg.get("session_secret", "").strip() if console_cfg.get("session_secret") else None

    # Final fallback: env vars (via dedicated helper so the env-only
    # behaviour is testable in isolation).
    if not username:
        env_cfg, env_reason = _console_auth_config_from_env()
        if env_cfg is not None:
            return env_cfg, env_reason
        # If env vars also missing/disabled, surface env reason only when
        # nothing was set anywhere — otherwise the config.yaml or
        # upstreams.yaml partial-config reason is more informative.
        if not (username or password or password_hash or secret):
            return None, env_reason
        # partial config from yaml
        if not username or not secret:
            return None, env_reason
        if not (password or password_hash):
            return None, env_reason

    # Either password or password_hash must be set
    has_password = bool(password)
    has_password_hash = bool(password_hash)
    if not (has_password or has_password_hash):
        return None, "no console auth configured; console is open"
    if not username or not secret:
        return None, "no console auth configured; console is open"

    # Build config dict - prefer password_hash, fallback to password
    cfg = {"username": username, "session_secret": secret}
    if password_hash:
        cfg["password_hash"] = password_hash
    else:
        cfg["password"] = password

    if len(secret) < 32:
        return None, (
            "session_secret must be at least 32 characters "
            "(setup_console_auth enforces this; refusing to enable with a "
            "weak secret)."
        )

    return cfg, "ok"


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
                # Merge: config.yaml overrides upstreams.yaml
                for key, value in config_yaml.items():
                    if key not in self.config:
                        self.config[key] = value
                    elif isinstance(self.config[key], dict) and isinstance(value, dict):
                        self.config[key].update(value)
                    else:
                        self.config[key] = value
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
        # health check loop 任务引用, stop() 时 cancel. HIGH 2.
        self._health_task: asyncio.Task | None = None
        # initialize() 并发保护: 两次 await initialize() 必须串行化,
        # 否则 cache / upstream 会被重复初始化 (HIGH #1)。
        self._init_lock = asyncio.Lock()
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

    async def initialize(self):
        # 双重检查锁: 第二次 fast path 直接返回; 拿到锁后再校验一次防 TOCTOU。
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._do_initialize()

    async def _do_initialize(self):
        logger.info("Initializing UniHive Gateway Server...")

        # console_api 是 set_omni_client 的目标。__init__ 里也 import 了
        # _ca, 那是给 set_config 用的; 这个 import 是给 _find_omni_client()
        # 之后的 _ca.set_omni_client() 用, 必须在 _do_initialize 局部可见。
        from .utils import console_api as _ca

        try:
            # 初始化缓存
            cache_cfg = self.config.get("cache", {})
            self.cache = Cache(CacheConfig(
                enabled=cache_cfg.get("enabled", True),
                db_path=cache_cfg.get("db_path", "logs/cache.db"),
            ))
            await self.cache.initialize()

            # 初始化上游客户端
            # 用 `or {}` 归一化, 防 YAML `upstreams: null` 让 .items() 抛 AttributeError (HIGH #2)
            for name, cfg in (self.config.get("upstreams") or {}).items():
                if not cfg.get("enabled", False):
                    continue

                if cfg.get("type") == "http":
                    # HTTP MCP 客户端
                    fuyao_cfg = FuyaoConfig(
                        name=name,
                        base_url=cfg["base_url"],
                        api_key=cfg.get("api_key", ""),
                        timeout_seconds=cfg.get("timeout_seconds", 30),
                        max_retry=cfg.get("retry", {}).get("max_attempts", 3),
                    )
                    client = FuyaoClient(fuyao_cfg)
                elif cfg.get("type") == "tdx_quant":
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
                    client = TdxQuantClient(tdx_quant_cfg)
                elif cfg.get("type") == "mootdx2":
                    # MooTDX2 客户端
                    from .models.mootdx2_config import MooTDX2Settings

                    # tdxdir 为空时让 mootdx2 自动探测 C:/new_tdx
                    mootdx2_settings = MooTDX2Settings(
                        market=cfg.get("market", "std"),
                        tdxdir=cfg.get("tdxdir", ""),
                    )
                    mootdx2_cfg = MooTDX2Config(
                        name=name,
                        market=cfg.get("market", "std"),
                        settings=mootdx2_settings,
                    )
                    client = MooTDX2Client(mootdx2_cfg)
                elif cfg.get("type") == "omni":
                    # OMNIDATA 板块数据客户端
                    omni_cfg = OmniConfig(
                        name=name,
                        db_path=cfg.get("db_path", "./data/board.db"),
                    )
                    client = OmniClient(omni_cfg)
                else:
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
                    client = UpstreamClient(upstream_cfg)

                try:
                    await asyncio.wait_for(client.start(), timeout=self._upstream_start_timeout)
                except asyncio.CancelledError:
                    # L2 (2026-09-07 5th-round audit): CancelledError 是 BaseException,
                    # 下面 except Exception 漏掉它。如果不把 client 加进
                    # self.upstreams, 进程退出时 stop() 拿不到这个 client,
                    # subprocess / httpx 连接泄漏。re-raise 让 cancellation
                    # 继续传播 (不要 swallow)。
                    logger.error(
                        f"[{name}] upstream start() cancelled, registering "
                        f"client for cleanup and propagating"
                    )
                    self.upstreams[name] = client
                    raise
                except asyncio.TimeoutError:
                    # 单 upstream 超时不应中断其他 upstream 初始化 (HIGH1).
                    # 仍记录到 self.upstreams (status 由 client 内部标记),
                    # 这样 get_server_status / health loop 仍能看到这个客户端.
                    logger.error(
                        f"[{name}] upstream start() timed out after "
                        f"{self._upstream_start_timeout}s, marking unavailable "
                        f"and continuing with other upstreams"
                    )
                    self.upstreams[name] = client
                    continue
                except Exception as e:
                    # 非超时异常 (FileNotFoundError / OSError / 等) 也会泄漏
                    # 子进程 / 连接 — 必须把 client 加进 self.upstreams, 让
                    # outer-except 的 cleanup / stop() 能调 client.stop() 关掉
                    # subprocess/httpx 连接 (HIGH #1, 2026-09-07 audit C1).
                    logger.error(
                        f"[{name}] upstream start() raised {type(e).__name__}: {e}; "
                        f"marking unavailable and continuing with other upstreams"
                    )
                    self.upstreams[name] = client
                    continue
                self.upstreams[name] = client
                logger.info(f"[{name}] {'Connected' if client.is_available else 'Failed'}")

            # Inject OmniClient into console_api
            _omni = self._find_omni_client()
            _ca.set_omni_client(_omni)

            # 初始化路由前先合并生成工具的 mapping（_load_all_tools 会写回顶层 config）
            tool_specs = self._load_all_tools()

            # 初始化路由
            self.router = Router(
                upstreams=self.upstreams,
                routing_config=self.config.get("routing", {}),
                upstream_tool_mapping=self.config.get("upstream_tool_mapping", {}),
            )

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
        - 仅当 ttl_key 配了 TTL、cache 启用、是在线数据源且非实时接口时才缓存。
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

            ttl = cache_strategy.ttl_for(self.config, ttl_key) if ttl_key else None
            cache_enabled = self.cache is not None and self.cache.enabled
            can_cache = (
                ttl is not None
                and cache_enabled
                and cache_strategy.is_cacheable_data_source(data_source_type)
                # realtime_quote 不再被 hardcoded 排除: 用户配的 TTL (哪怕短如
                # 10s 作为外部服务故障兜底) 都应被尊重。
            )
            # hit/miss 统计跟踪范围: cache 启用 + TTL 配了即跟踪 (即便因
            # data_source_type 不是 online 而 can_cache=False), 用于运维观测。
            # ttl_key 没配 TTL 或 cache 禁用则不跟踪, 避免运维误判缓存效果。
            tracks_stats = cache_enabled and ttl is not None

            key: str | None = None
            if can_cache:
                key = cache_strategy.cache_key(name, params)
                cached = await self.cache.get(key)
                if cached is not None:
                    # hops 不在缓存里；命中时给一个标记。不可变拷贝避免污染持久化对象。
                    self.cache.record_hit()
                    logger.info(f"[{name}] cache hit (key={key})")
                    return {**cached, "hops": cached.get("hops", []), "cache_hit": True}

            if tracks_stats and not can_cache:
                # tracks_stats 但 can_cache=False 路径 (例如 data_source != online,
                # 有 TTL 但 _is_cacheable_data_source 排除) 不走单飞, 每个请求
                # 各自路由, 各自计 miss 用于运维观测。
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
                if can_cache and key is not None and result.success and result.data is not None:
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
        if specs:
            strict = self.config.get("strict_registry", True)
            validate_specs(
                specs,
                self.config.get("routing", {}),
                self.config.get("upstream_tool_mapping", {}),
                strict=strict,
            )
            registered = register_tools_from_config(self, specs)
            logger.info(
                f"Registered {len(registered)} tools from config "
                f"(dangerous={any(s.get('dangerous') for s in specs)})"
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

    async def start(self):
        # M5 (2026-09-07 5th-round audit): initialize() 失败时重置 _running 并
        # 调 stop() 清 partial state, 允许同实例重试。Pre-fix: _running 在
        # initialize 之前设 True, 失败时留在 True, 重试 start() 会状态错位。
        self._running = True
        try:
            await self.initialize()
            self._health_task = asyncio.create_task(self._health_check_loop())
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
            self._health_task = asyncio.create_task(self._health_check_loop())
        except BaseException:
            await self.stop()
            raise

        # Console API handlers (delegates to console_api.py)
        from .utils import console_api as _ca

        async def _status_api(req: Request):
            return JSONResponse(_ca.get_upstream_status())

        async def _interfaces_api(req: Request):
            return JSONResponse(_ca.get_interfaces())

        async def _config_api(req: Request):
            return JSONResponse(_ca.load_config())

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
                Mount("/static", StaticFiles(directory="static", html=False)),
                Route("/", _index_route),
                Route("/console.html", _index_route),
                Route("/api/status", _status_api),
                *_ca.build_omni_routes(),
                Route("/api/interfaces", _interfaces_api),
                Route("/api/config", _config_api),
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
                _probe.bind((host, port))
            except OSError as e:
                _probe.close()
                if e.errno in (98, 10048) or "address already in use" in str(e).lower():
                    print(
                        f"error: cannot bind {host}:{port} — port already in use",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                raise
            _probe.close()

            await uvicorn.Server(config).serve()
        finally:
            await self.stop()

    async def _health_check_loop(self):
        while self._running:
            try:
                for name, client in self.upstreams.items():
                    if hasattr(client, 'health_check'):
                        try:
                            # MED3: 单 client.health_check() 加 timeout, 防止
                            # 卡死的 client 拖住整个 tick, 后续 client 都探不到。
                            await asyncio.wait_for(
                                client.health_check(),
                                timeout=self._health_check_timeout,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"[{name}] health_check timed out after "
                                f"{self._health_check_timeout}s, skipping this tick"
                            )
                        except Exception as e:
                            logger.warning(f"[{name}] health_check error: {e}")
                if self.cache:
                    await self.cache.cleanup_expired()
            except Exception as e:
                logger.error(f"Health check error: {e}")
            # 等 30s 或被 stop() 唤醒；之前 asyncio.sleep 不响应 stop,
            # 最坏等一个 tick 才退出，期间可能再调 cleanup_expired（已 dispose 的 engine）。
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=30)
                break  # stop() 唤醒了我们
            except asyncio.TimeoutError:
                continue

    async def stop(self):
        """优雅关闭：等待在途请求完成后关闭连接池"""
        self._running = False
        self._shutdown_event.set()  # 唤醒 health loop，立即退出而不是等下次 tick

        # Reset console_api state
        # _ca 在 stop() 局部 import, 与 _do_initialize / serve_http 各 import
        # 一次保持一致 (避免隐式依赖 __init__ 的局部变量)。
        from .utils import console_api as _ca
        _ca.set_omni_client(None)

        # HIGH 2 (2026-09-07 4th-round audit): 取消 health task 防止它跑
        # 在已 stop 的 client 上 — 若 health loop 正在 client.health_check()
        # 中, stop 后这个 health_check 仍跑在 dead client, 卡死或报错。
        # shield 避免 stop 自己被 cancel 时 health task 泄漏。
        health_task = getattr(self, "_health_task", None)
        if health_task is not None and not health_task.done():
            health_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(health_task), timeout=5.0
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            self._health_task = None

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

        # 关闭所有上游客户端 (L1, 2026-09-07 5th-round audit): 并行 gather
        # 替代顺序循环, 总关闭时间从 O(N) 降到 O(1)。
        if self.upstreams:
            await asyncio.gather(
                *[self._stop_one_upstream(name, client)
                  for name, client in list(self.upstreams.items())],
                return_exceptions=True,
            )

        # 关闭缓存
        if self.cache:
            try:
                await asyncio.wait_for(self.cache.close(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("cache close() timed out after 2s")
            self.cache = None

        # 重置状态以支持同实例 restart (HIGH B1, 2026-09-07 audit):
        # 不清掉的话, 第二次 initialize() 会因 _initialized=True 短路,
        # 用已经 stop 过的死 upstreams / router / mcp 路由 → 全程失败。
        # 生产路径通常 stop 后进程退出, 重置是无害的; stop/start 循环
        # (supervisor 重启 / 热重载配置) 则依赖这些归零。
        self.upstreams.clear()
        self.router = None
        self.mcp = None
        self._initialized = False

        logger.info("Gateway shutdown complete")

    async def _stop_one_upstream(self, name: str, client):
        try:
            await asyncio.wait_for(client.stop(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"[{name}] stop() timed out after 5s, force-exit")
        except Exception as e:
            logger.warning(f"[{name}] stop() raised: {e}")


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
