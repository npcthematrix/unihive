"""Router + ProxySettings 集成测试."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from src.unihive.api.upstream_client import ToolResult, UpstreamStatus
from src.unihive.core.proxy_settings import ProxySettings
from src.unihive.core.router import Router


class FakeMooTDX2:
    def __init__(self, name="mootdx2", status=UpstreamStatus.HEALTHY, payload=None):
        self.name = name
        self._status = status
        self._payload = payload or {"code": "600519.SH", "close": 1850.0}
        self.calls: list[tuple[str, dict]] = []

    @property
    def status(self):
        return self._status

    @property
    def is_available(self):
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    async def call_tool(self, tool, params):
        self.calls.append((tool, params))
        return ToolResult(success=True, data=self._payload, source=self.name)


class FakeTdxQuant:
    def __init__(self, name="tdx_quant", status=UpstreamStatus.HEALTHY, payload=None):
        self.name = name
        self._status = status
        self._payload = payload or {"Now": 1850.5, "LastClose": 1840.0}
        self.calls: list[tuple[str, dict]] = []

    @property
    def status(self):
        return self._status

    @property
    def is_available(self):
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    async def call_tool(self, tool, params):
        self.calls.append((tool, params))
        return ToolResult(success=True, data=self._payload, source=self.name)


@pytest.fixture
def tmp_yaml(tmp_path):
    p = tmp_path / "proxy_settings.yaml"
    p.write_text("proxy:\n  enabled: false\n", encoding="utf-8")
    return p


class TestProxyDisabled:
    """proxy.enabled=False → 走原 chain, 不调 tdx_quant."""

    @pytest.mark.asyncio
    async def test_proxy_disabled_routes_to_mootdx2(self, tmp_yaml):
        proxy = ProxySettings(tmp_yaml)
        mootdx2 = FakeMooTDX2()
        tdx = FakeTdxQuant()
        router = Router(
            upstreams={"mootdx2": mootdx2, "tdx_quant": tdx},
            routing_config={"get_quote": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={},
            proxy_settings=proxy,
        )
        result = await router.route("get_quote", {"code": "600519.SH"})
        assert result.success is True
        assert result.source == "mootdx2"
        assert len(mootdx2.calls) == 1
        assert tdx.calls == []


class TestProxyEnabledAndTdxHealthy:
    """proxy.enabled=True + tdx 健康 → 优先 tdx_quant."""

    @pytest.mark.asyncio
    async def test_proxy_enabled_routes_to_tdx_when_available(self, tmp_yaml):
        tmp_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        proxy = ProxySettings(tmp_yaml)
        mootdx2 = FakeMooTDX2()
        tdx = FakeTdxQuant()
        router = Router(
            upstreams={"mootdx2": mootdx2, "tdx_quant": tdx},
            routing_config={"get_quote": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={},
            proxy_settings=proxy,
        )
        result = await router.route("get_quote", {"code": "600519.SH"})
        assert result.success is True
        assert result.source == "tdx_quant"
        assert len(tdx.calls) == 1
        assert tdx.calls[0][0] == "get_market_snapshot"
        assert tdx.calls[0][1] == {"stock_code": "600519.SH"}
        assert mootdx2.calls == [], "未降级, mootdx2 不应被调"


class TestProxyEnabledButTdxUnavailable:
    """proxy.enabled=True + tdx UNAVAILABLE → 降级 mootdx2 (永远降级)."""

    @pytest.mark.asyncio
    async def test_proxy_enabled_falls_back_to_mootdx2_when_tdx_down(self, tmp_yaml):
        tmp_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        proxy = ProxySettings(tmp_yaml)
        mootdx2 = FakeMooTDX2()
        tdx = FakeTdxQuant(status=UpstreamStatus.UNAVAILABLE)
        router = Router(
            upstreams={"mootdx2": mootdx2, "tdx_quant": tdx},
            routing_config={"get_quote": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={},
            proxy_settings=proxy,
        )
        result = await router.route("get_quote", {"code": "600519.SH"})
        assert result.success is True
        assert result.source == "mootdx2"
        assert tdx.calls == [], "tdx 不可用, 不应被尝试"
        assert len(mootdx2.calls) == 1


class TestProxyEnabledTdxFailsAtRuntime:
    """proxy.enabled=True + tdx HEALTHY 但 call_tool 失败 → 降级 mootdx2."""

    @pytest.mark.asyncio
    async def test_proxy_enabled_falls_back_when_tdx_call_fails(self, tmp_yaml):
        tmp_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        proxy = ProxySettings(tmp_yaml)
        mootdx2 = FakeMooTDX2()
        tdx = FakeTdxQuant()
        # 让 tdx call_tool 返回 success=False
        tdx.call_tool = AsyncMock(
            return_value=ToolResult(
                success=False, error="TQ DLL not loaded", source="tdx_quant"
            )
        )
        router = Router(
            upstreams={"mootdx2": mootdx2, "tdx_quant": tdx},
            routing_config={"get_quote": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={},
            proxy_settings=proxy,
        )
        result = await router.route("get_quote", {"code": "600519.SH"})
        assert result.success is True
        assert result.source == "mootdx2"
        assert len(mootdx2.calls) == 1


class TestProxyWarnPassthrough:
    """proxy 成功分支（tdx_quant 命中）的 warn 也要透传到 RouteResult。"""

    @pytest.mark.asyncio
    async def test_proxy_success_carries_warn(self, tmp_yaml):
        tmp_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        proxy = ProxySettings(tmp_yaml)
        mootdx2 = FakeMooTDX2()
        tdx = FakeTdxQuant()
        tdx.call_tool = AsyncMock(
            return_value=ToolResult(
                success=True,
                data={"Now": 100.0},
                source="tdx_quant",
                error_detail={"warn": "部分字段降级", "recoverable": True},
            )
        )
        router = Router(
            upstreams={"mootdx2": mootdx2, "tdx_quant": tdx},
            routing_config={"get_quote": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={},
            proxy_settings=proxy,
        )
        result = await router.route("get_quote", {"code": "600519.SH"})
        assert result.success is True
        assert result.source == "tdx_quant"
        assert result.warn == "部分字段降级"


class TestProxyDoesNotAffectUnmappedTools:
    """未在 TOOL_MAP 的工具 (如 get_trade) 不被代理, 走原 chain."""

    @pytest.mark.asyncio
    async def test_proxy_does_not_affect_trade_tools(self, tmp_yaml):
        tmp_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        proxy = ProxySettings(tmp_yaml)
        mootdx2 = FakeMooTDX2()
        tdx = FakeTdxQuant()
        router = Router(
            upstreams={"mootdx2": mootdx2, "tdx_quant": tdx},
            routing_config={"get_trade": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={},
            proxy_settings=proxy,
        )
        result = await router.route("get_trade", {"code": "600519.SH"})
        assert result.success is True
        assert result.source == "mootdx2"
        assert tdx.calls == [], "get_trade 不在 TOOL_MAP, 不应走 tdx"


class TestProxyDisabledByDefaultWhenNoSettings:
    """Router 构造时未传 proxy_settings → 默认禁用 (向后兼容)."""

    @pytest.mark.asyncio
    async def test_router_without_proxy_settings_works(self):
        mootdx2 = FakeMooTDX2()
        tdx = FakeTdxQuant()
        router = Router(
            upstreams={"mootdx2": mootdx2, "tdx_quant": tdx},
            routing_config={"get_quote": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={},
        )
        result = await router.route("get_quote", {"code": "600519.SH"})
        assert result.success is True
        assert result.source == "mootdx2"
        assert tdx.calls == []
