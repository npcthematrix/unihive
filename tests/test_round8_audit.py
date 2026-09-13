"""Round 8 (2026-09-14) startup audit — MED-1/2/3 tests.

MED-1: router 无 routing chain 时错误消息引导去 upstreams.yaml
MED-2: MooTDX2Client.start() 跑轻量连通性探测，失败 DEGRADED
MED-3: OmniClient DB 缺失 → UNAVAILABLE (而非 DEGRADED)
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
from src.unihive.api.omni_client import OmniClient
from src.unihive.api.upstream_client import UpstreamStatus


# ========== MED-1: router 错误消息 ==========

class TestRouterNoChainErrorMessage:
    """MED-1: routing chain 为空时, 错误消息要引导排查 upstreams.yaml."""

    def _make_router(self, tool_name: str = "missing_tool"):
        """构造 Router 实例, 让 chain / fallback 都为空."""
        from src.unihive.core.router import Router

        router = Router.__new__(Router)
        # 不调 __init__, 只填 route() 早期返回路径访问的字段
        router.upstreams = {}
        # MED-1 测试目标: routing_config 和 upstream_tool_mapping 都为空,
        # 强制走 line 119-128 的 "No routing chain" 错误分支。
        router.routing_config = {}
        router.upstream_tool_mapping = {}
        router._per_upstream_timeout = Router.DEFAULT_PER_UPSTREAM_TIMEOUT
        return router

    def test_no_chain_error_mentions_upstreams_yaml(self):
        """错误消息包含 'upstreams.yaml' 排查指引."""
        from src.unihive.core.router import Router
        router = self._make_router()
        result = asyncio.run(router.route("missing_tool", {}))
        assert not result.success
        assert "upstreams.yaml" in result.error
        assert "routing" in result.error

    def test_no_chain_error_mentions_tool_name(self):
        """错误消息包含具体的 tool 名称."""
        from src.unihive.core.router import Router
        router = self._make_router()
        result = asyncio.run(router.route("my_special_tool", {}))
        assert not result.success
        assert "my_special_tool" in result.error


# ========== MED-2: MooTDX2 start() 探测 ==========

class TestMooTDX2StartProbe:
    """MED-2: start() 跑轻量探测, 不同结果对应不同 status."""

    def _make_client(self):
        config = MooTDX2Config(name="test_tdx", market="std")
        return MooTDX2Client(config)

    @pytest.mark.asyncio
    async def test_start_probe_success_sets_healthy(self):
        """探测拿到非空 DataFrame → HEALTHY."""
        client = self._make_client()
        mock_df = pd.DataFrame([{"symbol": "000001", "close": 10.0}])
        mock_quotes = MagicMock()
        mock_quotes.quotes.return_value = mock_df

        with patch.object(client, "_get_quotes", return_value=mock_quotes):
            await client.start()

        assert client.status == UpstreamStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_start_probe_empty_sets_degraded(self):
        """探测返回空 DataFrame → DEGRADED (而非 HEALTY)."""
        client = self._make_client()
        mock_quotes = MagicMock()
        mock_quotes.quotes.return_value = pd.DataFrame()  # 空

        with patch.object(client, "_get_quotes", return_value=mock_quotes):
            await client.start()

        assert client.status == UpstreamStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_start_probe_timeout_sets_degraded(self):
        """探测 2.5s 超时 → DEGRADED, 不阻塞启动."""
        client = self._make_client()
        mock_quotes = MagicMock()

        def slow_probe():
            import time
            time.sleep(5.0)  # 触发 2.5s 超时
            return pd.DataFrame([{"symbol": "x"}])

        mock_quotes.quotes.side_effect = slow_probe

        with patch.object(client, "_get_quotes", return_value=mock_quotes):
            # 不应阻塞 5s
            await asyncio.wait_for(client.start(), timeout=4.0)

        assert client.status == UpstreamStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_start_probe_exception_sets_degraded(self):
        """探测抛异常 (TDX 服务不可达 / mootdx2 库报错) → DEGRADED."""
        client = self._make_client()
        mock_quotes = MagicMock()
        mock_quotes.quotes.side_effect = ConnectionError("TDX unreachable")

        with patch.object(client, "_get_quotes", return_value=mock_quotes):
            await client.start()

        assert client.status == UpstreamStatus.DEGRADED


# ========== MED-3: OmniClient DB 缺失 → UNAVAILABLE ==========

class TestOmniStartStatus:
    """MED-3: DB 缺失 → UNAVAILABLE, 让 router 真正跳过 fallback."""

    def _make_client(self, db_path: Path) -> OmniClient:
        """构造 OmniClient 不走 __init__, 直接填必要字段."""
        client = OmniClient.__new__(OmniClient)
        client._db_path = str(db_path)
        client._status = UpstreamStatus.UNKNOWN
        client._metrics = {"total_requests": 0, "total_errors": 0}
        return client

    @pytest.mark.asyncio
    async def test_db_exists_sets_healthy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "board.db"
            db_file.touch()  # 创建空文件
            client = self._make_client(db_file)
            await client.start()
            assert client.status == UpstreamStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_db_missing_sets_unavailable(self):
        """MED-3: DB 缺失 → UNAVAILABLE (而非 DEGRADED)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "nonexistent.db"
            assert not db_file.exists()  # 确认缺失
            client = self._make_client(db_file)
            await client.start()
            assert client.status == UpstreamStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_db_missing_logs_actionable_hint(self, caplog):
        """DB 缺失时 log 包含 'python -m src.unihive.sync.board_sync' 提示."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "nonexistent.db"
            client = self._make_client(db_file)
            with caplog.at_level(logging.WARNING, logger="src.unihive.api.omni_client"):
                await client.start()
            assert "python -m src.unihive.sync.board_sync" in caplog.text

    @pytest.mark.asyncio
    async def test_db_missing_is_available_false(self):
        """MED-3 关联: UNAVAILABLE 应让 is_available=False, router 跳过 fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "nonexistent.db"
            client = self._make_client(db_file)
            await client.start()
            assert client.is_available is False