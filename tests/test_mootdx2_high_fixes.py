"""mootdx2_client HIGH 四项修复的 RED→GREEN 测试 (2026-09-19)。

mock 严格贴 mootdx2 1.4.2 / tdxpy 0.2.7 实测方法面与报文结构:

* StdQuotes 有 stocks(market=0|1) / stock_all() / quotes(symbol=[...]) /
  block(tofile=...) / batch_bars(symbols, frequency, start, offset, ...),
  **没有** stock / etf / income (旧测试凭空发明, 生产必 AttributeError)。
* get_security_quotes 报文行只有 market(int)/code/price/last_close/open/high/
  low/vol/amount, **没有** close / pct_chg / symbol — 必须由网关侧归一化。
* block() 默认走 BlockReader FLAT: blockname/block_type/code_index/code,
  code 是 7 位 "市场位(0=SZ/1=SH/2=BJ)+6位代码"; GROUP 模式才有 code_list。
* get_security_quotes 批次里只要含 market=2(北交所) tdxpy 直接返回 None,
  所以北证50 (889999) 必须与沪深指数分批查询。
* batch_bars 内部循环 quotes.bars(frequency=...), 频率码与 FREQ_MAP 同一空间
  (0=5min/1=15min/2=30min/3=60min/5=week/6=month/8=1min/9=day)。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import unittest.mock as mock

from src.unihive.api.mootdx2_client import (
    FREQ_MAP,
    MooTDX2Client,
    MooTDX2Config,
)

REPO = Path(__file__).resolve().parent.parent
YAML_PATH = REPO / "config" / "tools_mootdx2.yaml"


def make_client() -> MooTDX2Client:
    return MooTDX2Client(MooTDX2Config(name="test", market="std"))


def _bare(sym: str) -> str:
    """模拟 get_stock_markets 对 sh/sz 前缀的剥离 (北交所只能用裸码)。"""
    return sym[2:] if sym[:2].lower() in ("sh", "sz") else sym


class FakeStdQuotes:
    """贴 mootdx2 1.4.2 真实表面的假 Quotes (故意不提供 stock/etf/income)。"""

    def __init__(
        self,
        *,
        stocks_by_market: dict | None = None,
        quote_rows: list[dict] | None = None,
        bj_rows: list[dict] | None = None,
        block_df: pd.DataFrame | None = None,
        batch_result: dict | None = None,
    ):
        self._stocks = stocks_by_market or {}
        self._quote_rows = quote_rows or []
        self._bj_rows = bj_rows
        self._block_df = block_df if block_df is not None else pd.DataFrame()
        self._batch_result = batch_result or {}
        self.quotes_calls: list[list[str]] = []
        self.stocks_calls: list[int] = []
        self.block_calls: list[tuple] = []
        self.batch_calls: list[dict] = []

    # ---- 真实存在的方法 ----
    def stocks(self, market):
        self.stocks_calls.append(market)
        return pd.DataFrame(self._stocks.get(market, []))

    def quotes(self, symbol):
        syms = list(symbol)
        self.quotes_calls.append(syms)
        # tdxpy: 含北交所的批次直接返回 None (DeprecationWarning)
        if syms == ["889999"]:
            rows = self._bj_rows
        else:
            wanted = {_bare(s) for s in syms}
            rows = [r for r in self._quote_rows if r["code"] in wanted]
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def block(self, tofile="block.dat", **kwargs):
        self.block_calls.append(((tofile,), kwargs))
        return self._block_df

    def batch_bars(self, symbols, frequency=9, start=0, offset=800, **kwargs):
        self.batch_calls.append(
            {
                "symbols": list(symbols),
                "frequency": frequency,
                "start": start,
                "offset": offset,
            }
        )
        return self._batch_result


# ---------------------------------------------------------------------------
# H1: 五个工具调用不存在的 q.stock / q.etf / q.income
# ---------------------------------------------------------------------------


class TestH1StockCodesUsesRealApi:
    def test_uses_stocks_market_not_stock(self):
        """get_stock_codes 必须走 stocks(market=1/0) 双市场, 且只留 A 股代码。"""
        q = FakeStdQuotes(
            stocks_by_market={
                1: [
                    {"code": "600519", "name": "贵州茅台"},
                    {"code": "688981", "name": "中芯国际"},
                    {"code": "510300", "name": "沪深300ETF"},  # 基金, 剔除
                ],
                0: [
                    {"code": "000001", "name": "平安银行"},
                    {"code": "300750", "name": "宁德时代"},
                    {"code": "159001", "name": "货币ETF"},  # 基金, 剔除
                ],
            }
        )
        client = make_client()
        client._quotes = q

        data = client._get_stock_codes_sync(limit=100, prefix=False)

        assert data == ["600519", "688981", "000001", "300750"]
        assert q.stocks_calls == [1, 0]  # 沪先深后
        assert not hasattr(q, "stock"), "mock 不得提供生产环境不存在的 stock()"

    def test_prefix_and_limit(self):
        q = FakeStdQuotes(
            stocks_by_market={
                1: [{"code": "600519", "name": "贵州茅台"}, {"code": "600036", "name": "招商银行"}],
                0: [{"code": "000001", "name": "平安银行"}],
            }
        )
        client = make_client()
        client._quotes = q

        assert client._get_stock_codes_sync(limit=100, prefix=True) == [
            "sh600519",
            "sh600036",
            "sz000001",
        ]
        assert client._get_stock_codes_sync(limit=2, prefix=False) == ["600519", "600036"]


class TestH1EtfDerivedFromStocks:
    @pytest.fixture
    def quotes(self):
        return FakeStdQuotes(
            stocks_by_market={
                1: [
                    {"code": "510300", "name": "沪深300ETF"},
                    {"code": "560000", "name": "某LOF?"},
                    {"code": "588000", "name": "科创50ETF"},
                    {"code": "600519", "name": "贵州茅台"},  # 股票, 剔除
                ],
                0: [
                    {"code": "159001", "name": "货币ETF"},
                    {"code": "159003", "name": "黄金ETF"},
                    {"code": "161005", "name": "LOF基金"},  # 16 是 LOF, 非 ETF, 剔除
                    {"code": "000001", "name": "平安银行"},
                ],
            }
        )

    def test_etf_codes_filter_and_prefix(self, quotes):
        client = make_client()
        client._quotes = quotes

        assert client._get_etf_codes_sync(limit=100, prefix=False) == [
            "510300",
            "560000",
            "588000",
            "159001",
            "159003",
        ]
        assert client._get_etf_codes_sync(limit=100, prefix=True) == [
            "sh510300",
            "sh560000",
            "sh588000",
            "sz159001",
            "sz159003",
        ]
        assert not hasattr(quotes, "etf")

    def test_etf_list_single_exchange_contract(self, quotes):
        """YAML 合同: data=[{"code","name","exchange": "SH"/"SZ"}, ...]。"""
        client = make_client()
        client._quotes = quotes

        sh_list = client._get_etf_list_sync("sh", limit=100)
        assert sh_list == [
            {"code": "510300", "name": "沪深300ETF", "exchange": "SH"},
            {"code": "560000", "name": "某LOF?", "exchange": "SH"},
            {"code": "588000", "name": "科创50ETF", "exchange": "SH"},
        ]
        # mootdx2 stocks(market=2) 会抛异常, bj 必须显式判为不支持
        assert client._get_etf_list_sync("bj", limit=100) is None

    async def test_etf_list_bj_wrapper_no_data(self, quotes):
        client = make_client()
        client._quotes = quotes

        result = await client.get_etf_list(exchange="bj")

        # no_data 是正常业务态 (非错误), 沿用 _no_data_result 信封约定:
        # success=True + data=None + error_detail.error_type=no_data
        assert result.success is True
        assert result.data is None
        assert result.error_detail["error_type"] == "no_data"
        assert "沪深" in str(result.error)


class TestH1IncomeTakenOffline:
    async def test_get_income_unknown_tool(self):
        """mootdx2 无 income() 能力 (finance 是单股快照, 语义不符), 必须下线。"""
        client = make_client()
        assert not hasattr(client, "get_income")

        result = await client.call_tool("get_income", {"code": "600519"})
        assert result.success is False
        assert "Unknown tool" in str(result.error)

    def test_get_income_removed_from_yaml(self):
        """出厂 YAML seed 不得再声明该工具。"""
        text = YAML_PATH.read_text(encoding="utf-8")
        assert "name: get_income" not in text
        assert "mootdx2: get_income" not in text


class TestH1TopBoardUsesStocksAndRawQuoteFields:
    def test_top_board_scans_both_markets_and_normalizes(self):
        q = FakeStdQuotes(
            stocks_by_market={
                1: [{"code": "600000", "name": "浦发银行"}, {"code": "510300", "name": "ETF"}],
                0: [{"code": "000001", "name": "平安银行"}],
            },
            quote_rows=[
                {"market": 1, "code": "600000", "price": 10.99, "last_close": 10.0,
                 "open": 10.1, "high": 11.0, "low": 10.0, "vol": 1_000_000, "amount": 11_000_000},
                {"market": 0, "code": "000001", "price": 10.05, "last_close": 10.0,
                 "open": 10.0, "high": 10.1, "low": 9.99, "vol": 500_000, "amount": 5_000_000},
            ],
        )
        client = make_client()
        client._quotes = q

        result = client._stock_top_board_sync(
            sort_by="change_pct", direction="desc", limit=10, market="all"
        )

        assert [r["code"] for r in result] == ["600000", "000001"]  # 9.9% 在前
        assert result[0]["market"] == "sh"
        assert result[0]["close"] == 10.99
        assert result[0]["change_pct"] == pytest.approx(9.9, abs=0.01)
        assert q.stocks_calls == [1, 0]
        # quotes 必须收到带 sh/sz 前缀的代码 (裸 000001 会被路由成深市股票,
        # 无法表达上证指数; 这里前缀直传由 get_stock_markets 识别)
        sent = {s for call in q.quotes_calls for s in call}
        assert {"sh600000", "sz000001"} <= sent
        assert "510300" not in sent  # 基金不进 A 股排行榜
        assert not hasattr(q, "stock")

    def test_top_board_bj_market_empty(self):
        """stocks() 不覆盖北交所 → bj 过滤诚实地返回空, 不伪造。"""
        q = FakeStdQuotes(
            stocks_by_market={
                1: [{"code": "600000", "name": "浦发银行"}],
                0: [{"code": "000001", "name": "平安银行"}],
            },
            quote_rows=[
                {"market": 1, "code": "600000", "price": 10.99, "last_close": 10.0,
                 "high": 11.0, "low": 10.0, "vol": 1, "amount": 1},
                {"market": 0, "code": "000001", "price": 10.05, "last_close": 10.0,
                 "high": 10.1, "low": 9.99, "vol": 1, "amount": 1},
            ],
        )
        client = make_client()
        client._quotes = q

        assert client._stock_top_board_sync(market="bj") == []


# ---------------------------------------------------------------------------
# H2: stock_unusual 消费 block() FLAT 真实列
# ---------------------------------------------------------------------------


def _flat_block_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"blockname": "概念A", "block_type": 1, "code_index": 0, "code": "1600000"},
            {"blockname": "概念A", "block_type": 1, "code_index": 1, "code": "1600016"},
            {"blockname": "概念B", "block_type": 2, "code_index": 0, "code": "1600000"},
            {"blockname": "概念B", "block_type": 2, "code_index": 1, "code": "0000001"},
            {"blockname": "北交所概念", "block_type": 1, "code_index": 0, "code": "2430047"},
        ]
    )


def _unusual_quote_rows() -> list[dict]:
    return [
        {"market": 1, "code": "600000", "price": 10.99, "last_close": 10.0,
         "high": 11.0, "low": 10.0, "vol": 1_000_000, "amount": 11_000_000},
        {"market": 1, "code": "600016", "price": 8.00, "last_close": 8.88,
         "high": 8.9, "low": 7.99, "vol": 100_000, "amount": 800_000},
        {"market": 0, "code": "000001", "price": 10.05, "last_close": 10.0,
         "high": 10.1, "low": 9.99, "vol": 10_000, "amount": 100_000},
    ]


class TestH2UnusualFlatBlock:
    def test_block_called_flat_and_codes_extracted(self):
        q = FakeStdQuotes(block_df=_flat_block_df(), quote_rows=_unusual_quote_rows())
        client = make_client()
        client._quotes = q

        result = client._stock_unusual_sync(event_type="all")

        # block() 只认 tofile, block_type kwarg 会被吞进 **kwargs → 必须不带
        assert q.block_calls == [(("block.dat",), {})]
        # 600000 重复出现于两个板块只取一次; 430047 是北交所, 不进 quotes 批次
        sent = {s for call in q.quotes_calls for s in call}
        assert sent == {"sh600000", "sh600016", "sz000001"}
        # all: |pct|>=5 → 600000(+9.9) / 600016(-9.91) 留下, 000001(+0.5) 剔除
        by_code = {r["code"]: r for r in result}
        assert set(by_code) == {"600000", "600016"}
        assert by_code["600000"]["market"] == "sh"
        assert by_code["600000"]["event"] == "涨停"
        assert by_code["600016"]["event"] == "跌停"

    def test_event_filters_use_real_pct_field(self):
        q = FakeStdQuotes(block_df=_flat_block_df(), quote_rows=_unusual_quote_rows())
        client = make_client()
        client._quotes = q

        up = client._stock_unusual_sync(event_type="涨")
        assert [r["code"] for r in up] == ["600000"]
        down = client._stock_unusual_sync(event_type="跌")
        assert [r["code"] for r in down] == ["600016"]

    def test_time_filled_from_quote_server_time(self):
        """time 接报价包 server_time (快照时刻), 不再是恒空占位符。"""
        rows = [
            {"market": 1, "code": "600000", "price": 10.99, "last_close": 10.0,
             "high": 11.0, "low": 10.0, "vol": 1_000_000, "amount": 11_000_000,
             "server_time": "14:23:05.123"},
            {"market": 1, "code": "600016", "price": 8.00, "last_close": 8.88,
             "high": 8.9, "low": 7.99, "vol": 100_000, "amount": 800_000,
             "server_time": "14:23:05.123"},
        ]
        q = FakeStdQuotes(block_df=_flat_block_df(), quote_rows=rows)
        client = make_client()
        client._quotes = q

        result = client._stock_unusual_sync(event_type="all")

        assert {r["code"]: r["time"] for r in result} == {
            "600000": "14:23:05.123",
            "600016": "14:23:05.123",
        }

    def test_time_blank_when_server_time_all_zero(self):
        """非交易时段报文全 0 (mootdx2 格式化成 00:00:00.000) → 空串, 不给假时间。"""
        rows = [
            {"market": 1, "code": "600000", "price": 10.99, "last_close": 10.0,
             "high": 11.0, "low": 10.0, "vol": 1_000_000, "amount": 11_000_000,
             "server_time": "00:00:00.000"},
        ]
        q = FakeStdQuotes(block_df=_flat_block_df(), quote_rows=rows)
        client = make_client()
        client._quotes = q

        result = client._stock_unusual_sync(event_type="all")

        assert [r["time"] for r in result] == [""]

    def test_time_blank_when_server_time_column_absent(self):
        """旧报文无 server_time 列时保持空串 (向后兼容)。"""
        q = FakeStdQuotes(block_df=_flat_block_df(), quote_rows=_unusual_quote_rows())
        client = make_client()
        client._quotes = q

        result = client._stock_unusual_sync(event_type="all")

        assert {r["time"] for r in result} == {""}

    def test_zero_price_rows_excluded(self):
        """停牌/无报价行 (price=0 → -100%) 不得冒充跌停混进异动列表。"""
        rows = [
            {"market": 1, "code": "600000", "price": 0.0, "last_close": 10.0,
             "high": 0.0, "low": 0.0, "vol": 0, "amount": 0,
             "server_time": "09:18:00.003"},
            {"market": 1, "code": "600016", "price": 8.00, "last_close": 8.88,
             "high": 8.9, "low": 7.99, "vol": 100_000, "amount": 800_000,
             "server_time": "10:33:52.923"},
        ]
        q = FakeStdQuotes(block_df=_flat_block_df(), quote_rows=rows)
        client = make_client()
        client._quotes = q

        result = client._stock_unusual_sync(event_type="all")

        # 600000 是 -100% 的假跌停, 必须整条消失; 600016 的真跌停保留
        assert [(r["code"], r["event"]) for r in result] == [("600016", "跌停")]

    def test_empty_block_returns_empty_list(self):
        q = FakeStdQuotes(block_df=pd.DataFrame(), quote_rows=[])
        client = make_client()
        client._quotes = q
        assert client._stock_unusual_sync() == []
        assert q.quotes_calls == []


# ---------------------------------------------------------------------------
# H3: 指数概览 — 前缀直传 + 按 (market, code) 回贴 + 北交所分批
# ---------------------------------------------------------------------------


def _overview_rows() -> list[dict]:
    """故意打乱顺序: 旧实现按位置回贴会把名称全贴错。"""
    return [
        {"market": 1, "code": "000300", "price": 3800.0, "last_close": 3800.0},
        {"market": 0, "code": "399001", "price": 10000.0, "last_close": 10030.0},
        {"market": 1, "code": "000001", "price": 3100.0, "last_close": 3084.58},
        {"market": 0, "code": "399006", "price": 2000.0, "last_close": 1980.0},
        {"market": 1, "code": "000688", "price": 900.0, "last_close": 895.0},
    ]


class TestH3IndexOverview:
    def test_prefixed_request_split_bj_and_symbol_keyed_match(self):
        q = FakeStdQuotes(quote_rows=_overview_rows())  # bj_rows=None: 北交所无数据
        client = make_client()
        client._quotes = q

        result = client._index_overview_sync()

        # 沪深一批 (全部带前缀), 北证50 单独一批 (裸码, 走 BJ market)
        assert len(q.quotes_calls) == 2
        assert q.quotes_calls[0] == [
            "sh000001", "sz399001", "sz399006", "sh000688", "sh000300",
        ]
        assert q.quotes_calls[1] == ["889999"]
        # BJ 缺数据时只有 5 条, 顺序按静态指数表
        assert [(r["code"], r["market"]) for r in result] == [
            ("000001", "sh"),
            ("399001", "sz"),
            ("399006", "sz"),
            ("000688", "sh"),
            ("000300", "sh"),
        ]
        labels = {r["code"]: r["name"] for r in result}
        assert labels["000001"] == "上证指数"
        assert labels["399001"] == "深证成指"
        assert labels["000688"] == "科创50"
        assert labels["000300"] == "沪深300"
        # close/涨跌幅来自 price/last_close 真实字段
        sh = result[0]
        assert sh["close"] == 3100.0
        assert sh["change_pct"] == pytest.approx(0.5, abs=0.01)

    def test_short_response_does_not_shift_labels(self):
        """上游只回 2 行 (旧实现按位置 zipper 会把名称错贴给别的指数)。"""
        q = FakeStdQuotes(
            quote_rows=[
                {"market": 1, "code": "000300", "price": 3800.0, "last_close": 3800.0},
                {"market": 0, "code": "399006", "price": 2000.0, "last_close": 1980.0},
            ]
        )
        client = make_client()
        client._quotes = q

        result = client._index_overview_sync()

        assert {(r["code"], r["name"]) for r in result} == {
            ("000300", "沪深300"),
            ("399006", "创业板"),
        }

    def test_bj_row_included_when_available(self):
        q = FakeStdQuotes(
            quote_rows=_overview_rows(),
            bj_rows=[{"market": 2, "code": "889999", "price": 1000.0, "last_close": 1001.0}],
        )
        client = make_client()
        client._quotes = q

        result = client._index_overview_sync()

        bj = [r for r in result if r["code"] == "889999"]
        assert len(bj) == 1
        assert bj[0]["market"] == "bj"
        assert bj[0]["name"] == "北证50"


# ---------------------------------------------------------------------------
# H4: batch_bars 频率码必须与 FREQ_MAP 同空间
# ---------------------------------------------------------------------------


class TestH4BatchFreqMap:
    @pytest.mark.parametrize(
        "type_name,expected",
        [
            ("minute1", 8),
            ("minute5", 0),
            ("minute15", 1),
            ("minute30", 2),
            ("minute60", 3),
            ("day", 9),
            ("week", 5),
            ("month", 6),
            ("unknown", 9),  # 未知频率保守回落日线
        ],
    )
    def test_frequency_codes_match_freq_map(self, type_name, expected):
        q = FakeStdQuotes(
            batch_result={"600519": pd.DataFrame([{"close": 10.0}])}
        )
        client = make_client()
        client._quotes = q

        # 2026-09-19 二轮 M1: sync 收 wrapper 解析后的 bare list (不再收前缀字符串)
        client._get_batch_bars_sync(["600519"], type=type_name, limit=120)

        call = q.batch_calls[-1]
        assert call["frequency"] == expected
        assert call["symbols"] == ["600519"]
        assert call["offset"] == 120  # limit 必须映射到 offset (旧代码传 count 被忽略)

    def test_freq_map_batch_attr_removed(self):
        assert not hasattr(MooTDX2Client, "FREQ_MAP_BATCH")
        assert FREQ_MAP["minute1"] == 8  # 与 bars() 同一套 TDX category 码

    async def test_wrapper_rejects_limit_above_500_without_tdx_call(self):
        # 2026-09-19 二轮 M1: limit>500 不再静默截断, 直接 invalid_param 不打 TDX
        q = FakeStdQuotes(batch_result={"600519": pd.DataFrame([{"close": 10.0}])})
        client = make_client()
        client._quotes = q

        result = await client.batch_bars("600519", type="minute1", limit=600)

        assert result.success is False
        assert result.error_detail["error_type"] == "invalid_param"
        assert q.batch_calls == []

    def test_sync_hard_caps_offset_at_800(self):
        # wrapper 门控 500; sync 另保留 mootdx2 bars offset 硬上限 800 防御
        q = FakeStdQuotes(batch_result={"600519": pd.DataFrame([{"close": 10.0}])})
        client = make_client()
        client._quotes = q

        client._get_batch_bars_sync(["600519"], type="minute1", limit=1000)

        assert q.batch_calls[-1]["offset"] == 800


# ---------------------------------------------------------------------------
# 附带: get_quote 报文真实字段是 price 不是 close
# ---------------------------------------------------------------------------


class TestQuoteRawFields:
    def test_get_quote_reads_price(self):
        q = FakeStdQuotes(
            quote_rows=[
                {"market": 1, "code": "600519", "price": 1650.0, "last_close": 1640.0,
                 "open": 1645.0, "high": 1660.0, "low": 1640.0,
                 "vol": 1_000_000, "amount": 1_600_000_000},
            ]
        )
        client = make_client()
        client._quotes = q

        data = client._get_quote_sync("600519")

        assert data is not None
        assert data["close"] == 1650.0


# ---------------------------------------------------------------------------
# 今日分时: mootdx2 native API 缺方法 → tdxpy 兜底 (2026-10-14)
# ---------------------------------------------------------------------------


class TestMinuteDataTdxpyFallback:
    """`StdQuotes.minute` 最终调 `client.get_history_minute_time_data`, 而
    NativeTdxHqApi 没有实现它 → 必然 AttributeError。兜底走 tdxpy 直连,
    并把两条路径都归一成 `[{time, price, volume}]`。
    """

    @staticmethod
    def _client():
        from types import SimpleNamespace

        client = make_client()
        # 兜底需要配置里的行情服务器列表
        client.config.settings = SimpleNamespace(
            servers=[SimpleNamespace(host="1.2.3.4", port=7709)]
        )
        client._quotes = FakeStdQuotes()  # 无 minute 方法 → AttributeError
        return client

    def test_falls_back_to_tdxpy_and_normalizes(self, monkeypatch):
        import datetime as dt
        import tdxpy.hq as hq

        calls: dict = {}

        class FakeApi:
            def connect(self, host, port, time_out=None):
                calls["connect"] = (host, port)
                return self

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_history_minute_time_data(self, market, code, date):
                calls["args"] = (market, code, date)
                return [
                    {"price": 25.03, "vol": 13377},
                    {"price": 0.0, "vol": 0},      # 无成交 → 跳过, 但位置保留
                    {"price": 25.11, "vol": 6589},
                ]

        monkeypatch.setattr(hq, "TdxHq_API", FakeApi)

        records = self._client()._get_minute_data_sync("002273")

        assert calls["connect"] == ("1.2.3.4", 7709)
        assert calls["args"] == (
            0,
            "002273",
            dt.date.today().strftime("%Y%m%d"),
        )
        assert records == [
            {"time": "09:31:00", "price": 25.03, "volume": 13377.0},
            {"time": "09:33:00", "price": 25.11, "volume": 6589.0},
        ]

    def test_all_servers_failing_returns_empty(self, monkeypatch):
        import tdxpy.hq as hq

        class BoomApi:
            def connect(self, host, port, time_out=None):
                raise OSError("connection refused")

        monkeypatch.setattr(hq, "TdxHq_API", BoomApi)

        assert self._client()._get_minute_data_sync("002273") == []
