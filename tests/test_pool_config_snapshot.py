"""GatewayServer.get_pool_config_snapshot() + console_api.pool_config_api 测试。

Phase D (2026-09-27): 控制台配置信息页"全局池参数"卡片只读展示。
"""
from __future__ import annotations

import json

import pytest


# ========== get_pool_config_snapshot() ==========

class TestGetPoolConfigSnapshot:
    def _build_server(self, *, pool_config=None):
        from src.unihive.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server._pool_config = pool_config
        return server

    def test_returns_none_when_uninitialized(self):
        """网关未初始化时 (yaml 预检失败等), 返回 None — 控制台降级为 unavailable。"""
        from src.unihive.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        # 没设 _pool_config, getattr 兜底 None
        assert server.get_pool_config_snapshot() is None

    def test_returns_full_snapshot_with_sources(self):
        from src.unihive.core.pool_config import PoolConfig

        server = self._build_server(
            pool_config=PoolConfig(
                mootdx2_reader_limit=6,
                mootdx2_worker_threads=8,
                tdx_quant_worker_threads=12,
                sources={
                    "mootdx2_reader_limit": "env",
                    "mootdx2_worker_threads": "cfg",
                    "tdx_quant_worker_threads": "default",
                },
            ),
        )
        snap = server.get_pool_config_snapshot()
        assert snap is not None
        assert snap["mootdx2_reader_limit"] == 6
        assert snap["mootdx2_worker_threads"] == 8
        assert snap["tdx_quant_worker_threads"] == 12
        assert snap["sources"] == {
            "mootdx2_reader_limit": "env",
            "mootdx2_worker_threads": "cfg",
            "tdx_quant_worker_threads": "default",
        }

    def test_sources_dict_is_copy_not_alias(self):
        """调用方拿到的是 sources 副本, 改它不影响原始 dataclass。"""
        from src.unihive.core.pool_config import PoolConfig

        original = PoolConfig(
            mootdx2_reader_limit=4,
            mootdx2_worker_threads=8,
            tdx_quant_worker_threads=10,
            sources={"mootdx2_reader_limit": "cfg"},
        )
        server = self._build_server(pool_config=original)
        snap = server.get_pool_config_snapshot()
        snap["sources"]["mootdx2_reader_limit"] = "mutated"
        assert original.sources["mootdx2_reader_limit"] == "cfg"

    def test_snapshot_does_not_include_timestamp(self):
        """GatewayServer snapshot 不含 timestamp — handler 自行加 (与 runtime_stats_api 一致)。"""
        from src.unihive.core.pool_config import PoolConfig

        server = self._build_server(
            pool_config=PoolConfig(
                mootdx2_reader_limit=4,
                mootdx2_worker_threads=8,
                tdx_quant_worker_threads=10,
                sources={},
            ),
        )
        snap = server.get_pool_config_snapshot()
        assert "timestamp" not in snap


# ========== pool_config_api handler ==========

class TestPoolConfigApi:
    """直接调用 handler 函数 (不像真实 HTTP, 但走 Starlette JSONResponse 序列化路径)。"""

    @pytest.fixture(autouse=True)
    def _reset_gateway(self, monkeypatch):
        """每个用例前后复位 _gateway_server 单例, 避免互污染。"""
        import src.unihive.utils.console_api as ca

        monkeypatch.setattr(ca, "_gateway_server", None)
        yield
        monkeypatch.setattr(ca, "_gateway_server", None)

    async def test_returns_503_when_no_gateway(self):
        """控制台进程与网关分离的极端情形: 503 gateway unavailable。"""
        from starlette.responses import JSONResponse

        from src.unihive.utils.console_api import pool_config_api

        resp = await pool_config_api(None)  # type: ignore[arg-type]
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 503
        assert json.loads(resp.body) == {"error": "gateway unavailable"}

    async def test_returns_404_when_pool_config_missing(self):
        """网关已注入但 _pool_config 尚未装配 (极端启动期时序): 404。"""
        from starlette.responses import JSONResponse

        from src.unihive.gateway_server import GatewayServer
        from src.unihive.utils.console_api import pool_config_api

        server = GatewayServer.__new__(GatewayServer)
        server._pool_config = None

        import src.unihive.utils.console_api as ca
        ca._gateway_server = server

        resp = await pool_config_api(None)  # type: ignore[arg-type]
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 404
        assert json.loads(resp.body) == {"error": "pool_config_not_available"}

    async def test_returns_full_snapshot_with_timestamp(self):
        """happy path: 返回完整 snapshot + handler 加 timestamp。"""
        from starlette.responses import JSONResponse

        from src.unihive.core.pool_config import PoolConfig
        from src.unihive.gateway_server import GatewayServer
        from src.unihive.utils.console_api import pool_config_api

        server = GatewayServer.__new__(GatewayServer)
        server._pool_config = PoolConfig(
            mootdx2_reader_limit=4,
            mootdx2_worker_threads=8,
            tdx_quant_worker_threads=10,
            sources={
                "mootdx2_reader_limit": "default",
                "mootdx2_worker_threads": "cfg",
                "tdx_quant_worker_threads": "default",
            },
        )

        import src.unihive.utils.console_api as ca
        ca._gateway_server = server

        resp = await pool_config_api(None)  # type: ignore[arg-type]
        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["mootdx2_reader_limit"] == 4
        assert body["mootdx2_worker_threads"] == 8
        assert body["tdx_quant_worker_threads"] == 10
        assert body["sources"]["mootdx2_worker_threads"] == "cfg"
        assert "timestamp" in body
        assert isinstance(body["timestamp"], int)