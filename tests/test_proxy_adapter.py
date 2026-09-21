"""ProxyAdapter 单元测试: tool/period 映射 + 参数名改写 + 返回 normalize."""
import pytest

from src.unihive.core.proxy_adapter import (
    Mootdx2ToTdxQuantAdapter,
    ProxyAdapterError,
    _normalize_stock_code_for_tdxquant,
)


class TestAdapterToolMapping:
    def setup_method(self):
        self.adapter = Mootdx2ToTdxQuantAdapter()

    @pytest.mark.parametrize(
        "mootdx2_tool,tdx_tool",
        [
            ("get_quote", "get_market_snapshot"),
            ("get_kline", "get_market_data"),
            ("get_index_kline", "get_market_data"),
            ("get_index_all", "get_market_data"),
            ("get_index_bars", "get_market_data"),
            ("get_minute_data", "get_market_data"),
        ],
    )
    def test_tool_map_covers_6_quote_tools(self, mootdx2_tool, tdx_tool):
        assert self.adapter.TOOL_MAP[mootdx2_tool] == tdx_tool

    def test_supports_returns_true_for_mapped_tools(self):
        assert self.adapter.supports("get_quote") is True
        assert self.adapter.supports("get_kline") is True

    def test_supports_returns_false_for_unmapped_tools(self):
        assert self.adapter.supports("get_trade") is False
        assert self.adapter.supports("get_trade_history") is False
        assert self.adapter.supports("get_finance") is False
        # get_batch_quote: tqcenter 快照仅单只 stock_code, 不代理批量行情
        assert self.adapter.supports("get_batch_quote") is False


class TestNormalizeStockCode:
    """_normalize_stock_code_for_tdxquant: mootdx2/TDX 代码 → THS 后缀格式。

    adapter 边界实际会收到两种格式:
    - registry Normalizer 把裸 6 位代码转成 TDX 前缀 (sh600519)
    - 客户端直接传 THS 格式 (600519.SH) 时 Normalizer 原样放行
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # 已是 THS 后缀格式 — 幂等透传 (不得产生 600519..SH 双点)
            ("600519.SH", "600519.SH"),
            ("000001.SZ", "000001.SZ"),
            ("430047.BJ", "430047.BJ"),
            ("600519.sh", "600519.SH"),  # 小写后缀大写化
            (" 600519.SH ", "600519.SH"),  # 去空白
            # TDX 前缀格式 — 前缀是交易所的权威来源
            ("sh600519", "600519.SH"),
            ("sz000001", "000001.SZ"),
            ("bj430047", "430047.BJ"),
            ("SH600519", "600519.SH"),
            # 指数: 前缀必须压过裸代码的数字启发式 (000001 裸代码是 SZ 平安银行,
            # 但 sh000001 是上证指数 → 000001.SH)
            ("sh000001", "000001.SH"),
            ("sh000300", "000300.SH"),
            ("sz399001", "399001.SZ"),
            # 裸 6 位代码 — 按真实号段推断 (扩展 Normalizer 的首字启发式)
            ("600519", "600519.SH"),
            ("000001", "000001.SZ"),
            ("300505", "300505.SZ"),
            ("688981", "688981.SH"),  # 科创板
            ("830799", "830799.BJ"),
            ("920819", "920819.BJ"),  # 北交 2024 新号段, 不得误判 SH
            # B 股
            ("900901", "900901.SH"),
            ("200002", "200002.SZ"),
            # 基金 / ETF / LOF
            ("510300", "510300.SH"),
            ("159915", "159915.SZ"),
            # 可转债
            ("113052", "113052.SH"),
            ("123039", "123039.SZ"),
            # 国债逆回购 (200/204 前两位冲突, 必须看 3 位号段)
            ("204001", "204001.SH"),
            ("131810", "131810.SZ"),
        ],
    )
    def test_normalize_codes(self, raw, expected):
        assert _normalize_stock_code_for_tdxquant(raw) == expected

    def test_internal_whitespace_stripped_like_core_normalizer(self):
        assert _normalize_stock_code_for_tdxquant("600 519") == "600519.SH"

    @pytest.mark.parametrize("raw", ["", None])
    def test_empty_passthrough(self, raw):
        assert _normalize_stock_code_for_tdxquant(raw) == raw

    def test_unrecognized_format_returned_unchanged(self):
        # 无法识别的格式不臆造交易所后缀, 原样返回
        assert _normalize_stock_code_for_tdxquant("ABC123") == "ABC123"


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

    def test_adapt_get_batch_quote_not_supported(self):
        """批量行情不走代理: tqcenter.get_market_snapshot 仅接受单只 stock_code。"""
        with pytest.raises(ProxyAdapterError):
            self.adapter.adapt(
                "get_batch_quote", {"codes": "600519.SH,000001.SZ"}
            )

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
            "get_kline", {"code": "600519.SH", "type": mootdx2_period, "limit": 10}
        )
        assert tool == "get_market_data"
        assert params["period"] == tdx_period
        assert params["stock_list"] == ["600519.SH"]
        # limit 应改名为 count (tdx_quant 用 count, sign 表示方向)
        assert params["count"] == 10

    def test_adapt_get_minute_data_keeps_today_window(self):
        """mootdx2 get_minute_data(code) → tdx_quant get_market_data(stock_list=[code], period='1m', count=240)."""
        tool, params, _normalizer = self.adapter.adapt(
            "get_minute_data", {"code": "600519.SH"}
        )
        assert tool == "get_market_data"
        assert params["stock_list"] == ["600519.SH"]
        assert params["period"] == "1m"
        assert params["count"] == 240

    def test_adapt_get_minute_data_respects_explicit_count(self):
        tool, params, _normalizer = self.adapter.adapt(
            "get_minute_data", {"code": "600519.SH", "count": 60}
        )
        assert params["count"] == 60

    def test_adapt_unknown_tool_raises(self):
        with pytest.raises(ProxyAdapterError) as exc:
            self.adapter.adapt("get_trade", {"code": "600519.SH"})
        assert "not supported" in str(exc.value).lower()

    def test_adapt_get_quote_accepts_tdx_prefix_form(self):
        """registry Normalizer 把裸代码转成 sh600519, adapter 必须改成后缀格式。"""
        tool, params, _normalizer = self.adapter.adapt(
            "get_quote", {"code": "sh600519"}
        )
        assert tool == "get_market_snapshot"
        assert params == {"stock_code": "600519.SH"}

    def test_adapt_get_kline_accepts_tdx_prefix_form(self):
        tool, params, _normalizer = self.adapter.adapt(
            "get_kline", {"code": "sz000001", "type": "day", "limit": 5}
        )
        assert tool == "get_market_data"
        assert params["stock_list"] == ["000001.SZ"]

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
            "get_kline", {"code": "600519.SH", "type": "day", "limit": 2}
        )
        out = normalizer(tdx_result)
        assert out == tdx_result
