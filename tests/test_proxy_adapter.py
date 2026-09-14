"""ProxyAdapter 单元测试: tool/period 映射 + 参数名改写 + 返回 normalize."""
import pytest

from src.unihive.core.proxy_adapter import (
    Mootdx2ToTdxQuantAdapter,
    ProxyAdapterError,
)


class TestAdapterToolMapping:
    def setup_method(self):
        self.adapter = Mootdx2ToTdxQuantAdapter()

    @pytest.mark.parametrize(
        "mootdx2_tool,tdx_tool",
        [
            ("get_quote", "get_market_snapshot"),
            ("get_batch_quote", "get_security_snapshot"),
            ("get_kline", "get_market_data"),
            ("get_index_kline", "get_market_data"),
            ("get_index_all", "get_market_data"),
            ("get_index_bars", "get_market_data"),
            ("get_minute_data", "get_minute_time_data"),
        ],
    )
    def test_tool_map_covers_7_quote_tools(self, mootdx2_tool, tdx_tool):
        assert self.adapter.TOOL_MAP[mootdx2_tool] == tdx_tool

    def test_supports_returns_true_for_mapped_tools(self):
        assert self.adapter.supports("get_quote") is True
        assert self.adapter.supports("get_kline") is True

    def test_supports_returns_false_for_unmapped_tools(self):
        assert self.adapter.supports("get_trade") is False
        assert self.adapter.supports("get_trade_history") is False
        assert self.adapter.supports("get_finance") is False


class TestAdaptParams:
    def setup_method(self):
        self.adapter = Mootdx2ToTdxQuantAdapter()

    def test_adapt_get_quote_rewrites_code_to_stock_code(self):
        """mootdx2 get_quote(code) → tdx_quant get_market_snapshot(stock_code)."""
        tool, params, _normalizer = self.adapter.adapt(
            "get_quote", {"code": "600519.SH"}
        )
        assert tool == "get_market_snapshot"
        assert params == {"stock_code": "600519.SH"}

    def test_adapt_get_batch_quote_splits_codes_to_stock_list(self):
        """mootdx2 get_batch_quote(codes='600519.SH,000001.SZ') → tdx_quant stock_list=['600519.SH','000001.SZ']."""
        tool, params, _normalizer = self.adapter.adapt(
            "get_batch_quote", {"codes": "600519.SH,000001.SZ"}
        )
        assert tool == "get_security_snapshot"
        assert params["stock_list"] == ["600519.SH", "000001.SZ"]

    @pytest.mark.parametrize(
        "mootdx2_period,tdx_period",
        [
            ("day", "1d"),
            ("week", "1w"),
            ("month", "1mon"),
            ("minute1", "1m"),
            ("minute5", "5m"),
            ("minute15", "15m"),
            ("minute30", "30m"),
            ("minute60", "1h"),
        ],
    )
    def test_adapt_get_kline_period_mapping(self, mootdx2_period, tdx_period):
        tool, params, _normalizer = self.adapter.adapt(
            "get_kline", {"code": "600519.SH", "period": mootdx2_period, "limit": 10}
        )
        assert tool == "get_market_data"
        assert params["period"] == tdx_period
        assert params["stock_list"] == ["600519.SH"]
        # limit 应改名为 count (tdx_quant 用 count, sign 表示方向)
        assert params["count"] == 10

    def test_adapt_get_minute_data_keeps_today_window(self):
        tool, params, _normalizer = self.adapter.adapt(
            "get_minute_data", {"code": "600519.SH"}
        )
        assert tool == "get_minute_time_data"
        assert params["stock_code"] == "600519.SH"

    def test_adapt_unknown_tool_raises(self):
        with pytest.raises(ProxyAdapterError) as exc:
            self.adapter.adapt("get_trade", {"code": "600519.SH"})
        assert "not supported" in str(exc.value).lower()


class TestResponseNormalize:
    def setup_method(self):
        self.adapter = Mootdx2ToTdxQuantAdapter()

    def test_normalize_get_market_snapshot_returns_unwrapped_dict(self):
        """tdx_quant get_market_snapshot 返回单 dict, normalizer 透传."""
        tdx_result = {"Now": 1850.5, "LastClose": 1840.0, "Open": 1845.0}
        _tool, _params, normalizer = self.adapter.adapt(
            "get_quote", {"code": "600519.SH"}
        )
        out = normalizer(tdx_result)
        # 默认 normalizer 透传; 字段映射留给下游 (或后续按需扩展)
        assert out == tdx_result

    def test_normalize_get_market_data_returns_list(self):
        tdx_result = [
            {"time": "2026-01-01", "open": 1845.0, "close": 1850.0},
            {"time": "2026-01-02", "open": 1850.0, "close": 1860.0},
        ]
        _tool, _params, normalizer = self.adapter.adapt(
            "get_kline", {"code": "600519.SH", "period": "day", "limit": 2}
        )
        out = normalizer(tdx_result)
        assert out == tdx_result
