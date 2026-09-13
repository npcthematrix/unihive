"""
共享测试 fixtures
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# 把项目根加入 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def gateway_server_minimal():
    """返回一个 GatewayServer.__new__ 出来的实例, 已注入 __init__ 里初始化
    的 runtime 字段 (shutdown event, requests lock, active count)。

    测试想直接构造一个 server (跳过真实 config 加载) 但仍需要 _execute_cached
    / stop() / health loop 等依赖这些字段时, 用这个 fixture 替代手工赋值。
    """
    from src.unihive.gateway_server import GatewayServer

    server = GatewayServer.__new__(GatewayServer)
    server._shutdown_event = asyncio.Event()
    server._active_requests = 0
    server._requests_lock = asyncio.Lock()
    server._in_flight_requests = {}
    server._health_task = None
    server._running = False
    server._initialized = False
    return server


@pytest.fixture
def config_path(tmp_path) -> Path:
    """返回临时 config 路径，方便各测试用"""
    return tmp_path / "upstreams.yaml"


@pytest_asyncio.fixture
async def temp_cache(tmp_path):
    """返回一个已初始化的临时 Cache 实例"""
    from src.unihive.storage.cache import Cache, CacheConfig

    cfg = CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db"))
    cache = Cache(cfg)
    await cache.initialize()
    try:
        yield cache
    finally:
        await cache.close()


@pytest.fixture
def mock_upstream(monkeypatch):
    """返回一个 mock UpstreamClient 工厂"""
    from src.unihive.api.upstream_client import ToolResult, UpstreamStatus

    class _Mock:
        def __init__(self, name, tools=None, results=None):
            self.name = name
            self._tools = tools or []
            self._results = results or {}
            self._status = UpstreamStatus.HEALTHY
            self.calls = []

        @property
        def status(self):
            return self._status

        @property
        def is_available(self):
            return self._status == UpstreamStatus.HEALTHY

        async def call_tool(self, tool_name, arguments):
            self.calls.append((tool_name, arguments))
            if tool_name in self._results:
                r = self._results[tool_name]
                if callable(r):
                    return r(arguments)
                return ToolResult(success=True, data=r, source=self.name)
            return ToolResult(success=False, error="not mocked", source=self.name)

        async def list_tools(self):
            return [{"name": t, "description": t} for t in self._tools]

        async def start(self):
            self._status = UpstreamStatus.HEALTHY
            return True

        async def stop(self):
            self._status = UpstreamStatus.UNAVAILABLE

    return _Mock
