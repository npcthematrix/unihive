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

    def test_adapt_get_minute_data_pins_today_window(self):
        """mootdx2 get_minute_data(code) → tdx_quant get_market_data(当日窗口)。

        分时 = 今日, 不能是「最近 240 根」(会跨到前一交易日)。count<=0 才走
        区间模式; 时间串必须是 tqcenter 只认的紧凑格式 (YYYYMMDDHHMMSS),
        传 "YYYY-MM-DD HH:MM:SS" 会 ValueError。
        """
        import datetime as dt

        tool, params, _normalizer = self.adapter.adapt(
            "get_minute_data", {"code": "600519.SH"}
        )
        day = dt.datetime.now().strftime("%Y%m%d")
        assert tool == "get_market_data"
        assert params["stock_list"] == ["600519.SH"]
        assert params["period"] == "1m"
        assert params["start_time"] == f"{day}000000"
        assert params["end_time"] == f"{day}235959"
        assert params["count"] <= 0

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

    def test_normalize_get_minute_data_converts_field_frames_to_records(self):
        """tdx_quant 的 {字段: DataFrame} 必须转成分时合同 [{time, price, volume}]。

        成交量按 tqcenter 的「股」→ 合同「手」换算 (÷100), 与 mootdx2/tdxpy
        兜底路径同口径。
        """
        pd = pytest.importorskip("pandas")
        idx = pd.DatetimeIndex(["2026-10-13 09:31:00", "2026-10-13 09:32:00"])
        payload = {
            "Open": pd.DataFrame({"002273.SZ": [25.0, 25.1]}, index=idx),
            "Close": pd.DataFrame({"002273.SZ": [25.03, 25.11]}, index=idx),
            "Volume": pd.DataFrame({"002273.SZ": [1337700, 658900]}, index=idx),
        }
        _tool, _params, normalizer = self.adapter.adapt(
            "get_minute_data", {"code": "002273.SZ"}
        )
        assert normalizer(payload) == [
            {"time": "09:31:00", "price": 25.03, "volume": 13377.0},
            {"time": "09:32:00", "price": 25.11, "volume": 6589.0},
        ]

    def test_normalize_get_minute_data_accepts_series_and_skips_nan(self):
        """Series 形态同样吃; Close=NaN 的分钟跳过; 空/None → []。"""
        pd = pytest.importorskip("pandas")
        idx = pd.DatetimeIndex(["2026-10-13 09:31:00", "2026-10-13 09:32:00"])
        payload = {
            "Close": pd.Series([25.03, float("nan")], index=idx),
            "Volume": pd.Series([1337700, 0], index=idx),
        }
        _tool, _params, normalizer = self.adapter.adapt(
            "get_minute_data", {"code": "002273.SZ"}
        )
        assert normalizer(payload) == [
            {"time": "09:31:00", "price": 25.03, "volume": 13377.0},
        ]
        assert normalizer({}) == []
        assert normalizer(None) == []

    def test_normalize_get_minute_data_is_idempotent_for_records(self):
        """形状变化 (已归一化成记录列表) 时不得二次转换。"""
        records = [{"time": "09:31:00", "price": 25.03, "volume": 13377.0}]
        _tool, _params, normalizer = self.adapter.adapt(
            "get_minute_data", {"code": "002273.SZ"}
        )
        assert normalizer(records) == records

    @pytest.mark.parametrize(
        "mootdx2_tool,params",
        [
            ("get_kline", {"code": "600519.SH", "type": "day", "limit": 2}),
            ("get_index_kline", {"code": "sh000001", "type": "day", "limit": 2}),
            ("get_index_all", {"code": "sh000001", "type": "day", "limit": 2}),
            ("get_index_bars", {"symbol": "sh000001", "frequency": 9, "offset": 2}),
        ],
    )
    def test_normalize_kline_tools_convert_field_frames(self, mootdx2_tool, params):
        """4 个 K线工具的 {字段: DataFrame} 必须转成 [{date, open, ..., volume}]。

        日线周期按 tqcenter 口径换算: 成交量 股→手 (÷100), 成交额 万元→元
        (×10000), 与 mootdx2 在线 bars 路径对齐。
        """
        pd = pytest.importorskip("pandas")
        idx = pd.DatetimeIndex(["2026-10-13 00:00:00", "2026-10-14 00:00:00"])
        payload = {
            "Open": pd.DataFrame({"X": [21.0, 21.1]}, index=idx),
            "High": pd.DataFrame({"X": [21.5, 21.4]}, index=idx),
            "Low": pd.DataFrame({"X": [20.8, 21.0]}, index=idx),
            "Close": pd.DataFrame({"X": [21.04, 21.2]}, index=idx),
            "Volume": pd.DataFrame({"X": [43681500, 100]}, index=idx),
            "Amount": pd.DataFrame({"X": [91615.2, 2.1]}, index=idx),
        }
        _tool, _params, normalizer = self.adapter.adapt(mootdx2_tool, params)

        assert normalizer(payload) == [
            {"date": "2026-10-13", "datetime": "2026-10-13 00:00:00", "open": 21.0,
             "year": 2026, "month": 10, "day": 13, "hour": 0, "minute": 0,
             "high": 21.5, "low": 20.8, "close": 21.04, "vol": 436815.0,
             "amount": 916152000.0, "volume": 436815.0},
            {"date": "2026-10-14", "datetime": "2026-10-14 00:00:00", "open": 21.1,
             "year": 2026, "month": 10, "day": 14, "hour": 0, "minute": 0,
             "high": 21.4, "low": 21.0, "close": 21.2, "vol": 1.0,
             "amount": 21000.0, "volume": 1.0},
        ]

    def test_normalize_kline_minute_period_keeps_shares(self):
        """分钟线不换算成交量 (mootdx2 分钟线也是「股」), 只换算成交额。"""
        pd = pytest.importorskip("pandas")
        idx = pd.DatetimeIndex(["2026-09-18 09:31:00"])
        payload = {
            "Close": pd.DataFrame({"X": [24.91]}, index=idx),
            "Volume": pd.DataFrame({"X": [1063000]}, index=idx),
            "Amount": pd.DataFrame({"X": [2648.25]}, index=idx),
        }
        _tool, params, normalizer = self.adapter.adapt(
            "get_kline", {"code": "002273.SZ", "type": "minute1", "limit": 1}
        )
        assert params["period"] == "1m"
        assert normalizer(payload) == [
            {"date": "2026-09-18", "datetime": "2026-09-18 09:31:00",
             "year": 2026, "month": 9, "day": 18, "hour": 9, "minute": 31,
             "close": 24.91, "vol": 1063000.0, "amount": 26482500.0,
             "volume": 1063000.0},
        ]

    def test_normalize_kline_skips_nan_and_empty(self):
        """close=NaN 的根跳过; 空/None → [] (上层转 no_data 业务态)。"""
        pd = pytest.importorskip("pandas")
        idx = pd.DatetimeIndex(["2026-10-13"])
        _tool, _params, normalizer = self.adapter.adapt(
            "get_kline", {"code": "600519.SH", "type": "day", "limit": 1}
        )
        assert normalizer({"Close": pd.Series([float("nan")], index=idx)}) == []
        assert normalizer({}) == []
        assert normalizer(None) == []
