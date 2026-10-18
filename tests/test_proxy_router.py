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


class TestProxyCoversBatchBars:
    """batch_bars 已纳入 TOOL_MAP: 代理启用时走 tdx_quant 的多标的 get_market_data。"""

    @pytest.mark.asyncio
    async def test_batch_bars_routes_to_tdx_with_multi_code(self, tmp_yaml):
        pd = pytest.importorskip("pandas")
        tmp_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        proxy = ProxySettings(tmp_yaml)
        idx = pd.DatetimeIndex(["2026-10-14"])
        payload = {
            "Close": pd.DataFrame(
                {"002273.SZ": [20.46], "600519.SH": [1505.0]}, index=idx
            ),
            "Volume": pd.DataFrame(
                {"002273.SZ": [28422624], "600519.SH": [2657276]}, index=idx
            ),
        }
        mootdx2 = FakeMooTDX2()
        tdx = FakeTdxQuant(payload=payload)
        router = Router(
            upstreams={"mootdx2": mootdx2, "tdx_quant": tdx},
            routing_config={"batch_bars": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={},
            proxy_settings=proxy,
        )
        result = await router.route(
            "batch_bars", {"codes": "002273,600519", "type": "day", "limit": 5}
        )

        assert result.success is True
        assert result.source == "tdx_quant"
        assert tdx.calls == [
            (
                "get_market_data",
                {
                    "stock_list": ["002273.SZ", "600519.SH"],
                    "period": "1d",
                    "count": 5,
                },
            )
        ]
        assert mootdx2.calls == [], "一次多标的调用即可, 不应回落"
        # 对外合同: {裸代码: [记录]}, 成交量按日线口径 股→手
        assert set(result.data) == {"002273", "600519"}
        assert result.data["002273"][0]["vol"] == 284226.24
        assert result.data["600519"][0]["close"] == 1505.0

    @pytest.mark.asyncio
    async def test_batch_bars_empty_tdx_payload_falls_back(self, tmp_yaml):
        """tqcenter 返回空 → 归一化后 {} → 回落 mootdx2 链, 不当作成功。"""
        tmp_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        proxy = ProxySettings(tmp_yaml)
        mootdx2 = FakeMooTDX2(payload={"600519": [{"date": "2026-10-14"}]})
        tdx = FakeTdxQuant(payload={})
        router = Router(
            upstreams={"mootdx2": mootdx2, "tdx_quant": tdx},
            routing_config={"batch_bars": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={},
            proxy_settings=proxy,
        )
        result = await router.route(
            "batch_bars", {"codes": "600519", "type": "day", "limit": 1}
        )

        assert result.success is True
        assert result.source == "mootdx2"
        assert len(mootdx2.calls) == 1


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


class TestProxyMinuteDataAdaptation:
    """proxy 链路的 get_minute_data: 当日窗口参数 + {字段: DataFrame} → 分时记录。

    2026-10-13: 旧实现只传 count=240 且 normalizer 是 identity, 于是拿到的
    是「截止 now 的最近 240 根」(会跨日) 并且把 8 张 DataFrame 原样漏给调用方。
    """

    def _router(self, tmp_yaml, tdx):
        tmp_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        return Router(
            upstreams={"mootdx2": FakeMooTDX2(), "tdx_quant": tdx},
            routing_config={"get_minute_data": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={"get_minute_data": {"mootdx2": "get_minute"}},
            proxy_settings=ProxySettings(tmp_yaml),
        )

    @pytest.mark.asyncio
    async def test_normalizes_field_frames_into_minute_records(self, tmp_yaml):
        pd = pytest.importorskip("pandas")
        from datetime import datetime

        idx = pd.DatetimeIndex(["2026-10-13 09:31:00", "2026-10-13 09:32:00"])
        tdx = FakeTdxQuant(payload={
            "Open": pd.DataFrame({"002273.SZ": [25.0, 25.1]}, index=idx),
            "Close": pd.DataFrame({"002273.SZ": [25.03, 25.11]}, index=idx),
            # tqcenter 给「股」, 归一化后应是合同口径「手」(÷100)
            "Volume": pd.DataFrame({"002273.SZ": [1337700, 658900]}, index=idx),
        })
        router = self._router(tmp_yaml, tdx)

        result = await router.route("get_minute_data", {"code": "002273"})

        assert result.success is True
        assert result.source == "tdx_quant"
        assert result.data == [
            {"time": "09:31:00", "price": 25.03, "volume": 13377.0},
            {"time": "09:32:00", "price": 25.11, "volume": 6589.0},
        ]
        day = datetime.now().strftime("%Y%m%d")
        assert tdx.calls == [(
            "get_market_data",
            {
                "stock_list": ["002273.SZ"],
                "period": "1m",
                "start_time": f"{day}000000",
                "end_time": f"{day}235959",
                "count": 0,
            },
        )]

    @pytest.mark.asyncio
    async def test_empty_payload_falls_back_to_chain(self, tmp_yaml):
        """TQ 本次范围无数据 (归一化后为空) → 当作 miss 回落原 chain。

        分时尤其关键: TQ 终端本地分钟数据缺失时, 由链上的 mootdx2/tdxpy
        兜底取当日分时, 而不是就此终结成 no_data。

        注: FakeTdxQuant 的 ``payload or {...}`` 兜底会把 ``{}`` 换成默认
        payload, 故用「有键但值退化为 None」的形状表达 tqcenter 的空结果。
        """
        tdx = FakeTdxQuant(payload={"Close": None})
        mootdx2 = FakeMooTDX2(payload=[
            {"time": "09:31:00", "price": 25.03, "volume": 13377.0},
        ])
        tmp_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
        router = Router(
            upstreams={"mootdx2": mootdx2, "tdx_quant": tdx},
            routing_config={"get_minute_data": {"chain": ["mootdx2"]}},
            upstream_tool_mapping={"get_minute_data": {"mootdx2": "get_minute_data"}},
            proxy_settings=ProxySettings(tmp_yaml),
        )

        result = await router.route("get_minute_data", {"code": "002273"})

        assert result.success is True
        assert result.source == "mootdx2"
        assert len(mootdx2.calls) == 1
        assert result.data == [
            {"time": "09:31:00", "price": 25.03, "volume": 13377.0},
        ]
