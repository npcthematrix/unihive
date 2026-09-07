"""
UniHive MCP Gateway Server - 统一 MCP 网关
同时 serve MCP 接口和管理控制台。
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .cache import Cache, CacheConfig
from .config_loader import load_config, validate_config
from .log_config import configure_logging
from .normalizer import Normalizer
from .registry import register_tools_from_config, validate_specs
from .router import Router
from .upstream_client import UpstreamClient, UpstreamConfig
from .fuyao_client import FuyaoClient, FuyaoConfig
from .tokenwave_tdx_client import TokenWaveTdxClient, TokenWaveTdxConfig
from .mootdx2_client import MooTDX2Client, MooTDX2Config
from .tdx_quant_client import TdxQuantClient
from .tdx_quant_config import TdxQuantConfig, TdxQuantSettings

logger = logging.getLogger(__name__)


class GatewayServer:
    """UniHive MCP 网关 - 聚合多个上游 MCP Server"""

    DEFAULT_UPSTREAM_START_TIMEOUT = 30.0
    DEFAULT_HEALTH_CHECK_TIMEOUT = 10.0

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
        self.config = load_config(config_path, strict_env=strict_env)
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
        from . import console_api as _ca
        _ca.set_config(self.config)
        self.upstreams: dict[str, UpstreamClient | FuyaoClient | TokenWaveTdxClient | MooTDX2Client | TdxQuantClient] = {}
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
                elif cfg.get("type") == "python":
                    # TokenWave TDX 客户端
                    tokenwave_cfg = TokenWaveTdxConfig(
                        name=name,
                        mode=cfg.get("mode", "auto"),
                    )
                    client = TokenWaveTdxClient(tokenwave_cfg)
                elif cfg.get("type") == "mootdx2":
                    # MooTDX2 客户端
                    mootdx2_cfg = MooTDX2Config(
                        name=name,
                        market=cfg.get("market", "std"),
                    )
                    client = MooTDX2Client(mootdx2_cfg)
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
                self.upstreams[name] = client
                logger.info(f"[{name}] {'Connected' if client.is_available else 'Failed'}")

            # 初始化路由前先合并生成工具的 mapping（_load_all_tools 会写回顶层 config）
            tool_specs = self._load_all_tools()

            # 初始化路由
            self.router = Router(
                upstreams=self.upstreams,
                routing_config=self.config.get("routing", {}),
                upstream_tool_mapping=self.config.get("upstream_tool_mapping", {}),
            )

            # 初始化 MCP Server（传入已加载的 specs，避免重复 _load_all_tools() 调用）
            self.mcp = FastMCP("unihive")
            self._register_tools(specs=tool_specs)

        except BaseException:
            # 初始化失败，清理已分配资源，防止重复初始化
            for client in self.upstreams.values():
                try:
                    await asyncio.wait_for(client.stop(), timeout=2.0)
                except (asyncio.TimeoutError, Exception):
                    pass
            self.upstreams.clear()
            if self.cache:
                try:
                    await asyncio.wait_for(self.cache.close(), timeout=2.0)
                except (asyncio.TimeoutError, Exception):
                    pass
                self.cache = None
            raise

        self._initialized = True
        logger.info(f"Gateway initialized with {len(self.upstreams)} upstreams")

    def _cache_key(self, name: str, params: dict) -> str:
        """生成稳定的缓存 key。"""
        payload = json.dumps(params, sort_keys=True, default=str, ensure_ascii=False)
        h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        return f"{name}:{h}"

    def _ttl_for(self, ttl_key: str) -> int | None:
        """从 config.cache.ttl 查 TTL（秒）；未配置返回 None。"""
        ttl = self.config.get("cache", {}).get("ttl", {})
        return ttl.get(ttl_key)

    @staticmethod
    def _is_fuyao_source(source: str | None) -> bool:
        """缓存策略：仅缓存 FUYAO 上游响应。TDX/TQ-Local/TokenWave 本地数据不缓存。"""
        return bool(source) and source.startswith("fuyao_")

    async def _execute_cached(
        self,
        name: str,
        params: dict,
        route_key: str,
        ttl_key: str | None = None,
    ) -> dict:
        """路由 + 缓存包装。

        缓存策略：
        - 仅当 ttl_key 在 config.cache.ttl 中配了 TTL、cache 启用、且上游是 fuyao_*
          时才缓存。
        - 命中路径：直接返回缓存的响应，附 cache_hit=True。
        - 失效路径：调一次路由；失败响应也只算一次，不双倍路由。
        - hops 是请求级元数据，不进缓存。
        """
        ttl = self._ttl_for(ttl_key) if ttl_key else None
        can_cache = ttl is not None and self.cache is not None and self.cache.enabled

        key: str | None = None
        if can_cache:
            key = self._cache_key(name, params)
            cached = await self.cache.get(key)
            if cached is not None:
                # hops 不在缓存里；命中时给一个标记。不可变拷贝避免污染持久化对象。
                self.cache.record_hit()
                logger.info(f"[{name}] cache hit (key={key})")
                return {**cached, "hops": cached.get("hops", []), "cache_hit": True}
            # miss：无论后续 FUYAO gate 是否通过，都算一次 miss
            self.cache.record_miss()

        # 在途请求计数
        async with self._requests_lock:
            self._active_requests += 1
        try:
            result = await self.router.route(route_key, params)
        finally:
            async with self._requests_lock:
                self._active_requests -= 1
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

        if (can_cache and key is not None and result.success
                and result.data is not None
                and self._is_fuyao_source(result.source)):
            # 缓存里只存稳定数据：hops（请求级）入空，cache_hit 在 setdefault 之前写
            await self.cache.set(key, {**resp, "hops": []}, ttl=ttl)

        # 标记 miss，让消费者区分"缓存端点本次未命中"与"无缓存端点"
        resp.setdefault("cache_hit", False)

        return resp

    def _load_all_tools(self) -> list[dict]:
        """Delegate to tool_loader.load_all_tools for YAML merge."""
        from .tool_loader import load_all_tools
        return load_all_tools(self.config_path, self.config)

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
            status = {
                "gateway": "healthy",
                "upstreams": {},
                "timestamp": int(time.time()),
            }
            for name, client in self.upstreams.items():
                status["upstreams"][name] = {
                    "status": client.status.value,
                    "available": client.is_available,
                }
            return status

        @self.mcp.tool()
        async def get_health() -> dict:
            """健康检查 — 聚合上游状态, 不再硬编码 healthy."""
            return self.aggregate_health(self.upstreams)

    @classmethod
    def aggregate_health(
        cls, upstreams: dict
    ) -> dict:
        """LOW8: 聚合 upstream 状态为整体 health.

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

    async def start(self):
        self._running = True
        await self.initialize()
        asyncio.create_task(self._health_check_loop())
        # 注册 SIGINT/SIGTERM handler: stdio transport 模式下, 父进程 (Claude
        # Desktop 等) 关停会发 SIGTERM, Python 默认 handler 不通知 asyncio,
        # 我们的 stop() 不会被调用, stdio MCP 子进程泄漏。
        # Windows 的 asyncio 完全不支持 add_signal_handler (会抛 NotImplementedError),
        # 那里只能靠父进程行为 + finally 兜底; Unix 上正常注册。
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM, self._shutdown_event.set)
            loop.add_signal_handler(signal.SIGINT, self._shutdown_event.set)
        try:
            await self.mcp.run_stdio_async()
        finally:
            # 任何退出路径都跑 cleanup, 包括 KeyboardInterrupt / SIGTERM
            await self.stop()

    async def serve_http(self, host: str = None, port: int = None, mcp_path: str = None):
        """HTTP 模式: 同时 serve MCP 接口和 Console 管理 UI."""
        gw_cfg = self.config.get("gateway", {})
        host = host if host is not None else gw_cfg.get("host", "127.0.0.1")
        port = port if port is not None else gw_cfg.get("port", 18080)
        mcp_path = mcp_path if mcp_path is not None else gw_cfg.get("mcp_path", "/mcp")
        self._running = True
        await self.initialize()
        asyncio.create_task(self._health_check_loop())

        # Console API handlers (delegates to console_api.py)
        from . import console_api as _ca

        async def _status_api(req: Request):
            return JSONResponse(_ca.get_upstream_status())

        async def _interfaces_api(req: Request):
            return JSONResponse(_ca.get_interfaces())

        async def _config_api(req: Request):
            return JSONResponse(_ca.load_config())

        async def _health_api(req: Request):
            return JSONResponse(_ca.get_health())

        async def _mcp_tools_api(req: Request):
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
        mcp_app = self.mcp.http_app(path="/")

        # Combined router: MCP at /mcp, Console API at /api/*, static at /
        # lifespan=mcp_app.lifespan 必须显式传, 否则 FastMCP 的
        # StreamableHTTPSessionManager task group 不会启动, 任何 MCP 请求
        # 都会在 handler 里抛 "task group is not initialized"。
        app = Starlette(
            lifespan=mcp_app.lifespan,
            routes=[
                Mount(mcp_path, app=mcp_app),
                Route("/", _index_route),
                Route("/api/status", _status_api),
                Route("/api/interfaces", _interfaces_api),
                Route("/api/config", _config_api),
                Route("/health", _health_api),
                Route("/api/mcp-tools-list", _mcp_tools_api),
            ]
        )
        log_level = self.config.get("logging", {}).get("level", "info").lower()
        config = uvicorn.Config(app, host=host, port=port, log_level=log_level)

        # 端口可用性预检: uvicorn 绑定失败只 log + 退出 3, 不 raise OSError,
        # 拿不到干净错误。预检给一个明确消息 + 退出码 2。
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

        try:
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

        # 关闭所有上游客户端
        for name, client in self.upstreams.items():
            try:
                await asyncio.wait_for(client.stop(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"[{name}] stop() timed out after 5s, force-exit")
            except Exception as e:
                logger.warning(f"[{name}] stop() raised: {e}")

        # 关闭缓存
        if self.cache:
            try:
                await asyncio.wait_for(self.cache.close(), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("cache close() timed out after 2s")
            self.cache = None

        logger.info("Gateway shutdown complete")


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
