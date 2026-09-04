"""
UniHive MCP Gateway Server - 统一 MCP 网关
"""
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import uvicorn
from fastmcp import FastMCP

from .cache import Cache, CacheConfig
from .config_loader import load_config, validate_config
from .normalizer import Normalizer
from .registry import register_tools_from_config, validate_specs
from .router import Router
from .upstream_client import UpstreamClient, UpstreamConfig, UpstreamStatus
from .rhths_client import RhthsClient, RhthsConfig
from .http_jsonrpc_client import HttpJsonRpcClient, HttpJsonRpcConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/gateway.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)


class GatewayServer:
    """UniHive MCP 网关 - 聚合多个上游 MCP Server"""

    def __init__(self, config_path: str = "config/upstreams.yaml", *, strict_env: bool = True):
        self.config_path = Path(config_path)
        self.config = load_config(config_path, strict_env=strict_env)
        errors = validate_config(self.config)
        if errors:
            for e in errors:
                logger.warning(f"Config validation: {e}")
        self.upstreams: dict[str, UpstreamClient | RhthsClient | HttpJsonRpcClient] = {}
        self.cache: Cache | None = None
        self.router: Router | None = None
        self.mcp: FastMCP | None = None
        self._running = False
        self.bearer_token: str = os.environ.get("GATEWAY_BEARER_TOKEN", "").strip()

    async def initialize(self):
        logger.info("Initializing UniHive Gateway Server...")

        # 初始化缓存
        cache_cfg = self.config.get("cache", {})
        self.cache = Cache(CacheConfig(
            enabled=cache_cfg.get("enabled", True),
            db_path=cache_cfg.get("db_path", "logs/cache.db"),
        ))
        await self.cache.initialize()

        # 初始化上游客户端
        for name, cfg in self.config.get("upstreams", {}).items():
            if not cfg.get("enabled", False):
                continue

            if cfg.get("type") == "http":
                # HTTP MCP 客户端
                rhths_cfg = RhthsConfig(
                    name=name,
                    base_url=cfg["base_url"],
                    api_key=cfg.get("api_key", ""),
                    timeout_seconds=cfg.get("timeout_seconds", 30),
                    max_retry=cfg.get("retry", {}).get("max_attempts", 3),
                )
                client = RhthsClient(rhths_cfg)
            elif cfg.get("type") == "http_jsonrpc":
                # 通用 HTTP JSON-RPC 客户端（如 TQ-Local 通达信本地服务）
                jsonrpc_cfg = HttpJsonRpcConfig(
                    name=name,
                    base_url=cfg["base_url"].rstrip("/") + "/",
                    timeout_seconds=cfg.get("timeout_seconds", 10),
                    max_retry=cfg.get("retry", {}).get("max_attempts", 3),
                )
                client = HttpJsonRpcClient(jsonrpc_cfg)
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

            await client.start()
            self.upstreams[name] = client
            logger.info(f"[{name}] {'Connected' if client.is_available else 'Failed'}")

        # 初始化路由
        self.router = Router(
            upstreams=self.upstreams,
            routing_config=self.config.get("routing", {}),
            upstream_tool_mapping=self.config.get("upstream_tool_mapping", {}),
        )

        # 初始化 MCP Server
        self.mcp = FastMCP("unihive")
        self._register_tools()

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

    async def _execute_cached(
        self,
        name: str,
        params: dict,
        route_key: str,
        ttl_key: str | None = None,
    ) -> dict:
        """路由 + 缓存包装。失败结果不缓存。"""
        async def _do_route() -> dict:
            result = await self.router.route(route_key, params)
            return Normalizer.to_gateway_response(
                success=result.success,
                data=result.data,
                error=result.error,
                source=result.source,
                hops=result.hops,
            )

        ttl = self._ttl_for(ttl_key) if ttl_key else None
        if not ttl or not self.cache:
            return await _do_route()

        key = self._cache_key(name, params)
        # 只缓存成功响应
        async def _factory() -> dict | None:
            resp = await _do_route()
            return resp if resp.get("success") else None

        cached, hit = await self.cache.get_or_set(key, ttl, _factory)
        if hit:
            logger.info(f"[{name}] cache hit (key={key})")
        if cached is None:
            return await _do_route()
        return cached

    def _register_tools(self):
        # 特殊工具：手写（带特殊逻辑或网关内部）
        self._register_search_stock()
        self._register_status_tools()

        # 通用工具：从 config.tools 自动注册
        specs = self.config.get("tools", []) or []
        if specs:
            strict = self.config.get("strict_registry", True)
            validate_specs(
                specs,
                self.config.get("routing", {}),
                self.config.get("upstream_tool_mapping", {}),
                strict=strict,
            )
            disable_dangerous = self.config.get("disable_dangerous", False)
            registered = register_tools_from_config(
                self, specs, disable_dangerous=disable_dangerous
            )
            logger.info(
                f"Registered {len(registered)} tools from config "
                f"(dangerous={any(s.get('dangerous') for s in specs)}, "
                f"disabled={disable_dangerous})"
            )

    def _register_search_stock(self):
        @self.mcp.tool()
        async def search_stock(keyword: str) -> dict:
            """搜索股票 (rhths_meta 优先, TDX 降级)"""
            if "rhths_meta" in self.upstreams:
                client = self.upstreams["rhths_meta"]
                if client.is_available:
                    result = await client.call_tool(
                        "get_meta_tickers_search",
                        {"q": keyword, "limit": 10},
                    )
                    if result.success:
                        return Normalizer.to_gateway_response(
                            success=True,
                            data=result.data,
                            source="rhths_meta",
                        )
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
            """健康检查"""
            return {"status": "healthy", "timestamp": int(time.time())}

    async def start(self):
        self._running = True
        await self.initialize()
        asyncio.create_task(self._health_check_loop())
        await self.mcp.run_stdio_async()

    async def serve_http(self, host: str = "127.0.0.1", port: int = 18080, mount_path: str = "/mcp"):
        """以 Streamable HTTP 模式启动 gateway（被 console_server 内部调用）"""
        self._running = True
        await self.initialize()
        asyncio.create_task(self._health_check_loop())
        if not self.bearer_token:
            raise RuntimeError(
                "GATEWAY_BEARER_TOKEN must be set for HTTP mode. "
                "Set it in .env or environment, or use --transport stdio."
            )
        from .auth_middleware import BearerTokenMiddleware
        raw_app = self.mcp.http_app(path=mount_path)
        mcp_app = BearerTokenMiddleware(raw_app, token=self.bearer_token)
        config = uvicorn.Config(mcp_app, host=host, port=port, log_level="info")
        await uvicorn.Server(config).serve()

    async def _health_check_loop(self):
        while self._running:
            try:
                for name, client in self.upstreams.items():
                    if hasattr(client, 'health_check'):
                        await client.health_check()
                if self.cache:
                    await self.cache.cleanup_expired()
            except Exception as e:
                logger.error(f"Health check error: {e}")
            await asyncio.sleep(30)

    async def stop(self):
        self._running = False
        for client in self.upstreams.values():
            await client.stop()
        if self.cache:
            await self.cache.close()


async def async_main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 18080):
    if sys.platform == "win32":
        os.environ["PYTHONIOENCODING"] = "utf-8"

    server = GatewayServer()
    if transport == "stdio":
        await server.start()
    elif transport == "http":
        await server.serve_http(host=host, port=port)
    else:
        raise ValueError(f"Unknown transport: {transport}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="UniHive MCP Gateway")
    parser.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="Transport mode (default: stdio). 'http' for Streamable HTTP server.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (http mode)")
    parser.add_argument("--port", type=int, default=18080, help="HTTP bind port (http mode)")
    args = parser.parse_args()
    asyncio.run(async_main(transport=args.transport, host=args.host, port=args.port))


if __name__ == "__main__":
    main()
