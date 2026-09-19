"""mootdx2_client 新一轮审视 (3 HIGH / 4 MED / 7 LOW) 的 RED→GREEN 测试。

日期: 2026-09-19
事实基线全部对照 mootdx2 1.4.2 / tdxpy 0.2.7 实测方法面 (venv 3.13.15):

* quotes(symbol=[...]) 报文行只有 market(0=SZ/1=SH/2=BJ)/code/price/last_close/
  open/high/low/vol/amount, 无 symbol/close/pct_chg; get_stock_markets 显式
  识别 sh/sz 前缀, **不识别 bj 前缀** (bj430047 会被错路由到 SH); BJ 裸码
  4/8/920 开头能路由到 market=2, 但批次中混入 market=2 时 tdxpy 整批返 None,
  即 BJ 只能单只独立批次。
* Customize.search(group=True) 返回 DataFrame, 列 blockname(中文名)/
  block_type(.blk 文件名 stem)/stock_count/code_list; 旧代码 for entry in
  DataFrame 迭代出列名字符串, 中文名解析整条链路是死的。
* 未配置 tdxdir 时 Path(None) 抛 TypeError → 对外 internal_error, 合同要求
  tdx_not_installed / tdx_custom_sector_unavailable / sector_not_found。
* Reader.block(symbol='') 无参走 T0002/hq_cache/.dat, 该文件不存在时恒返
  None — search_stock 的 block 分支在生产是死路, 实际走 board.db。
* board.db.sector_stocks 无拼音列; 920xxx 是北交所新号段。
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config

REPO = Path(__file__).resolve().parent.parent
YAML_PATH = REPO / "config" / "tools_mootdx2.yaml"


def make_client(settings=None) -> MooTDX2Client:
    return MooTDX2Client(MooTDX2Config(name="test", market="std", settings=settings))


def _yaml_block(name: str) -> str:
    text = YAML_PATH.read_text(encoding="utf-8")
    marker = f"  - name: {name}\n"
    start = text.index(marker)
    rest = text[start + len(marker):]
    end = rest.find("\n  - name: ")
    return rest if end == -1 else rest[:end]


def _classify_bare(bare: str) -> str:
    """模拟 tdxpy get_stock_market 对裸码的市场推断 (只覆盖用例需要的前缀)。"""
    if bare.startswith("920") or bare[0] in ("4", "8"):
        return "bj"
    if bare[:2] in ("60", "68", "51", "56", "58") or bare[0] in ("5", "6", "9"):
        return "sh"
    return "sz"


def _parse_token(token: str) -> tuple[str, str]:
    t = token.strip().lower()
    if t.endswith((".sh", ".sz", ".bj")):
        return t[-2:], t[:-3]
    if t[:2] in ("sh", "sz", "bj"):
        return t[:2], t[2:]
    return _classify_bare(t), t


class FakeQuotes:
    """贴 tdxpy 真实行为: 混 BJ 批次整批空; 单只 BJ 裸码可返回。"""

    def __init__(
        self,
        rows: list[dict] | None = None,
        *,
        stocks_by_market: dict | None = None,
        block_df: pd.DataFrame | None = None,
        batch_bars_result: dict | None = None,
        bars_rows: list[dict] | None = None,
        minute_rows: list[dict] | None = None,
        minutes_rows: list[dict] | None = None,
        quotes_exc: Exception | None = None,
    ):
        self._rows = {(int(r["market"]), str(r["code"])): dict(r) for r in (rows or [])}
        self._stocks = stocks_by_market or {}
        self._block_df = block_df if block_df is not None else pd.DataFrame()
        self._batch_bars = batch_bars_result
        self._bars_rows = bars_rows or []
        self._minute_rows = minute_rows or []
        self._minutes_rows = minutes_rows or []
        self._quotes_exc = quotes_exc
        self.quotes_calls: list[list[str]] = []
        self.bars_calls: list[dict] = []
        self.batch_bars_calls: list[dict] = []
        self.minutes_calls: list[dict] = []
        self.minute_calls: list[str] = []

    def quotes(self, symbol):
        syms = list(symbol)
        self.quotes_calls.append(syms)
        if self._quotes_exc is not None:
            raise self._quotes_exc
        parsed = [_parse_token(s) for s in syms]
        has_bj = any(m == "bj" for m, _ in parsed)
        if has_bj and len(parsed) > 1:
            # tdxpy: 批次含 market=2 时整批返 None
            return pd.DataFrame()
        mid = {"sz": 0, "sh": 1, "bj": 2}
        out = [self._rows[(mid[m], c)] for m, c in parsed if (mid[m], c) in self._rows]
        return pd.DataFrame(out) if out else pd.DataFrame()

    def stocks(self, market):
        return pd.DataFrame(self._stocks.get(market, []))

    def stock_all(self):
        rows = self._stocks.get(1, []) + self._stocks.get(0, [])
        return pd.DataFrame(rows)

    def stock_count(self, market):
        return len(self._stocks.get(market, []))

    def block(self, tofile="block.dat", **kwargs):
        return self._block_df

    def bars(self, symbol, frequency, offset):
        self.bars_calls.append({"symbol": symbol, "frequency": frequency, "offset": offset})
        return pd.DataFrame(self._bars_rows)

    def batch_bars(self, symbols, frequency=9, start=0, offset=800, **kwargs):
        self.batch_bars_calls.append(
            {"symbols": list(symbols), "frequency": frequency, "offset": offset}
        )
        return self._batch_bars

    def minute(self, symbol):
        self.minute_calls.append(symbol)
        return pd.DataFrame(self._minute_rows)

    def minutes(self, symbol, date):
        self.minutes_calls.append({"symbol": symbol, "date": date})
        return pd.DataFrame(self._minutes_rows)


class FakeReader:
    def __init__(self, *, block_value=None, daily_df=None, minute_df=None):
        self._block = block_value
        self._daily = daily_df
        self._minute = minute_df
        self.block_calls: list[dict] = []

    def block(self, symbol="", group=False, **kwargs):
        self.block_calls.append({"symbol": symbol, "group": group})
        return self._block

    def daily(self, symbol, adjust=None):
        return self._daily

    def minute(self, symbol):
        return self._minute


def _quote_row(market: int, code: str, price: float, last_close: float, vol: int = 1000) -> dict:
    return {
        "market": market, "code": code, "price": price, "last_close": last_close,
        "open": last_close, "high": price, "low": last_close, "vol": vol,
        "amount": price * vol,
    }


# ---------------------------------------------------------------------------
# H1: get_batch_quote — >50 门控 / BJ 隔离分批 / 报文归一化
# ---------------------------------------------------------------------------


class TestH1BatchQuote:
    @pytest.mark.parametrize("n,ok", [(49, True), (50, True), (51, False), (60, False)])
    async def test_limit_50_enforced(self, n, ok):
        q = FakeQuotes()
        client = make_client()
        client._quotes = q
        codes = ",".join(f"6000{i:02d}" for i in range(n))

        result = await client.get_batch_quote(codes)

        if ok:
            assert result.success is True
            assert q.quotes_calls  # 合法请求确实发出
        else:
            assert result.success is False
            assert result.error_detail["error_type"] == "invalid_param"
            assert q.quotes_calls == []

    async def test_empty_codes_invalid_param(self):
        client = make_client()
        client._quotes = FakeQuotes()
        result = await client.get_batch_quote("  , ")
        assert result.success is False
        assert result.error_detail["error_type"] == "invalid_param"

    async def test_malformed_code_invalid_symbol(self):
        client = make_client()
        client._quotes = FakeQuotes()
        result = await client.get_batch_quote("abc,600000")
        assert result.success is False
        assert result.error_detail["error_type"] == "invalid_symbol"

    async def test_bj_split_into_isolated_batches_and_rows_annotated(self):
        rows = [
            _quote_row(1, "600000", 10.99, 10.0),
            _quote_row(0, "000001", 10.05, 10.0),
            _quote_row(2, "430047", 5.0, 4.8, vol=100),
            _quote_row(2, "830799", 8.0, 8.2, vol=200),
        ]
        q = FakeQuotes(rows)
        client = make_client()
        client._quotes = q

        result = await client.get_batch_quote(
            "sh600000,sz000001,bj430047,bj830799"
        )

        assert result.success is True
        # 沪深一批 (前缀直传), 两只 BJ 各一批裸码 (混批 tdxpy 整批返 None)
        assert len(q.quotes_calls) == 3
        assert q.quotes_calls[0] == ["sh600000", "sz000001"]
        assert ["430047"] in q.quotes_calls
        assert ["830799"] in q.quotes_calls
        by_symbol = {r["symbol"]: r for r in result.data}
        assert set(by_symbol) == {"sh600000", "sz000001", "bj430047", "bj830799"}
        # 报文无 symbol/close/pct_chg, 网关必须补出
        assert by_symbol["sh600000"]["close"] == 10.99
        assert by_symbol["sh600000"]["pct_chg"] == pytest.approx(9.9, abs=0.01)
        assert by_symbol["bj430047"]["pct_chg"] == pytest.approx(
            round((5.0 - 4.8) / 4.8 * 100, 2), abs=0.01
        )

    async def test_bj_failure_does_not_poison_sh_sz_with_warn(self):
        """BJ 批次空时沪深数据仍返回, error_detail 给 warn。"""
        rows = [_quote_row(1, "600000", 10.99, 10.0), _quote_row(0, "000001", 10.05, 10.0)]
        q = FakeQuotes(rows)  # 无 BJ 行情
        client = make_client()
        client._quotes = q

        result = await client.get_batch_quote("600000,000001,430047")

        assert result.success is True
        assert len(result.data) == 2
        assert "北交" in result.error_detail.get("warn", "") or "bj" in result.error_detail.get("warn", "").lower()

    async def test_all_empty_is_no_data(self):
        client = make_client()
        client._quotes = FakeQuotes(rows=[])
        result = await client.get_batch_quote("600000")
        assert result.success is True
        assert result.data is None
        assert result.error_detail["error_type"] == "no_data"


class TestH1SingleQuote:
    async def test_prefixed_index_goes_to_sh_market(self):
        """sh000001 (上证指数) 旧实现剥前缀裸发 → 被路由成深市平安银行。"""
        q = FakeQuotes([
            _quote_row(1, "000001", 3100.0, 3084.58),
            _quote_row(0, "000001", 10.05, 10.0),
        ])
        client = make_client()
        client._quotes = q

        data = client._get_quote_sync("sh000001")

        assert q.quotes_calls == [["sh000001"]]
        assert data["symbol"] == "sh000001"
        assert data["close"] == 3100.0
        assert "pct_chg" in data and "volume" in data

    async def test_bare_a_share_gets_prefixed_token(self):
        q = FakeQuotes([_quote_row(1, "600519", 1650.0, 1640.0)])
        client = make_client()
        client._quotes = q

        data = client._get_quote_sync("600519")

        assert q.quotes_calls == [["sh600519"]]
        assert data["close"] == 1650.0

    async def test_bj_single_quote_uses_bare_token(self):
        """bj 前缀不被 tdxpy 识别 (会错路由 SH), BJ 必须裸码单只请求。"""
        q = FakeQuotes([_quote_row(2, "430047", 5.0, 4.8)])
        client = make_client()
        client._quotes = q

        data = client._get_quote_sync("bj430047")

        assert q.quotes_calls == [["430047"]]
        assert data["symbol"] == "bj430047"
        assert data["close"] == 5.0


# ---------------------------------------------------------------------------
# H2: get_custom_sector_stocks 中文名解析 (DataFrame iterrows)
# ---------------------------------------------------------------------------


def _install_fake_customize(monkeypatch, grouped: pd.DataFrame):
    mod = types.ModuleType("mootdx2.tools.customize")

    class FakeCustomize:
        def __init__(self, tdxdir=None):
            self.tdxdir = tdxdir

        def search(self, group=False):
            assert group is True
            return grouped

    mod.Customize = FakeCustomize
    monkeypatch.setitem(sys.modules, "mootdx2.tools.customize", mod)


def _make_sector_client(tmp_path, blk_files: dict[str, str]):
    tdxdir = tmp_path / "new_tdx"
    blocknew = tdxdir / "T0002" / "blocknew"
    blocknew.mkdir(parents=True)
    for stem, content in blk_files.items():
        (blocknew / f"{stem}.blk").write_text(content, encoding="gbk")
    settings = SimpleNamespace(tdxdir=str(tdxdir))
    return make_client(settings=settings)


def _grouped_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"blockname": "我的持仓", "block_type": "pos", "stock_count": 2, "code_list": []},
        {"blockname": "科技龙头", "block_type": "tech", "stock_count": 1, "code_list": []},
    ])


class TestH2CustomSectorNameLookup:
    async def test_chinese_name_resolves_via_dataframe(self, tmp_path, monkeypatch):
        client = _make_sector_client(tmp_path, {
            "pos": "1600000\n0000001\n",
            "tech": "2430047\n",
        })
        _install_fake_customize(monkeypatch, _grouped_df())

        result = await client.get_custom_sector_stocks("我的持仓")

        assert result.success is True
        assert result.data == [
            {"code": "600000", "market": "sh"},
            {"code": "000001", "market": "sz"},
        ]

    async def test_blk_stem_still_accepted(self, tmp_path, monkeypatch):
        client = _make_sector_client(tmp_path, {"pos": "1600000\n"})
        _install_fake_customize(monkeypatch, _grouped_df())

        result = await client.get_custom_sector_stocks("pos")

        assert result.success is True
        assert result.data == [{"code": "600000", "market": "sh"}]

    async def test_unknown_sector_is_sector_not_found(self, tmp_path, monkeypatch):
        client = _make_sector_client(tmp_path, {"pos": "1600000\n"})
        _install_fake_customize(monkeypatch, _grouped_df())

        result = await client.get_custom_sector_stocks("不存在的板块")

        assert result.success is False
        assert result.error_detail["error_type"] == "sector_not_found"

    async def test_sector_list_shows_chinese_names(self, tmp_path, monkeypatch):
        client = _make_sector_client(tmp_path, {
            "pos": "1600000\n0000001\n",
            "tech": "2430047\n",
        })
        _install_fake_customize(monkeypatch, _grouped_df())

        result = await client.get_custom_sector_list()

        assert result.success is True
        names = {r["sector_name"]: r for r in result.data}
        assert set(names) == {"我的持仓", "科技龙头"}
        assert names["我的持仓"]["stock_count"] == 2
        assert names["我的持仓"]["blk_file"] == "pos"


# ---------------------------------------------------------------------------
# H3: tdxdir 缺失 → tdx_not_installed (不再 TypeError/internal_error)
# ---------------------------------------------------------------------------


class TestH3TdxdirGuards:
    async def test_sector_list_without_tdxdir(self):
        client = make_client()  # settings=None → _get_tdxdir() None
        result = await client.get_custom_sector_list()
        assert result.success is False
        assert result.error_detail["error_type"] == "tdx_not_installed"

    async def test_sector_stocks_without_tdxdir(self):
        client = make_client()
        result = await client.get_custom_sector_stocks("我的持仓")
        assert result.success is False
        assert result.error_detail["error_type"] == "tdx_not_installed"

    async def test_missing_blocknew_dir_unavailable(self, tmp_path):
        tdxdir = tmp_path / "new_tdx"
        tdxdir.mkdir()
        client = make_client(SimpleNamespace(tdxdir=str(tdxdir)))

        list_res = await client.get_custom_sector_list()
        assert list_res.success is False
        assert list_res.error_detail["error_type"] == "tdx_custom_sector_unavailable"

        stocks_res = await client.get_custom_sector_stocks("我的持仓")
        assert stocks_res.success is False
        assert stocks_res.error_detail["error_type"] == "tdx_custom_sector_unavailable"


# ---------------------------------------------------------------------------
# M1: batch_bars ≤20 只 / limit 1-500, 违约返 invalid_param (不静默截断)
# ---------------------------------------------------------------------------


class TestM1BatchBarsValidation:
    async def test_more_than_20_codes_rejected(self):
        q = FakeQuotes()
        client = make_client()
        client._quotes = q
        codes = ",".join(f"6000{i:02d}" for i in range(21))

        result = await client.batch_bars(codes)

        assert result.success is False
        assert result.error_detail["error_type"] == "invalid_param"
        assert q.batch_bars_calls == []

    @pytest.mark.parametrize("limit", [0, -1, 501, 1000])
    async def test_limit_out_of_range_rejected(self, limit):
        q = FakeQuotes(batch_bars_result={"600000": pd.DataFrame([{"close": 10.0}])})
        client = make_client()
        client._quotes = q

        result = await client.batch_bars("600000,000001", limit=limit)

        assert result.success is False
        assert result.error_detail["error_type"] == "invalid_param"
        assert q.batch_bars_calls == []

    async def test_valid_request_strips_prefixes_and_passes_offset(self):
        q = FakeQuotes(batch_bars_result={
            "600000": pd.DataFrame([{"close": 10.0}]),
            "000001": pd.DataFrame([{"close": 12.0}]),
        })
        client = make_client()
        client._quotes = q

        result = await client.batch_bars("sh600000,sz000001", type="day", limit=300)

        assert result.success is True
        call = q.batch_bars_calls[-1]
        assert call["symbols"] == ["600000", "000001"]
        assert call["offset"] == 300


# ---------------------------------------------------------------------------
# M2: get_workday / get_workday_range — 默认值 / 门控 / 日期校验 / 节假日诚实
# ---------------------------------------------------------------------------


class TestM2Workday:
    def test_sync_known_window(self):
        client = make_client()
        # 2026-09-19 是周六; 往前 3 个工作日 = 9/16(三) 9/17(四) 9/18(五)
        assert client._get_workday_sync("20260919", 3) == [
            "20260916", "20260917", "20260918"
        ]

    async def test_default_date_is_today(self):
        client = make_client()
        result = await client.get_workday(count=1)
        assert result.success is True
        assert isinstance(result.data, list) and result.data

    @pytest.mark.parametrize("count", [0, -1, 366])
    async def test_count_out_of_range_invalid_param(self, count):
        client = make_client()
        result = await client.get_workday(date="20260918", count=count)
        assert result.success is False
        assert result.error_detail["error_type"] == "invalid_param"

    async def test_bad_date_invalid_param(self):
        client = make_client()
        result = await client.get_workday(date="not-a-date")
        assert result.success is False
        assert result.error_detail["error_type"] == "invalid_param"

    def test_range_known_window(self):
        client = make_client()
        # 2026-09-14(一) ~ 2026-09-18(五)
        assert client._get_workday_range_sync("20260914", "20260918") == [
            "20260914", "20260915", "20260916", "20260917", "20260918"
        ]

    async def test_range_defaults_and_reversed_rejected(self):
        client = make_client()
        ok = await client.get_workday_range(start="20260914", end="20260918")
        assert ok.success is True and len(ok.data) == 5

        defaults = await client.get_workday_range()
        assert defaults.success is True  # 省略时默认当天

        bad = await client.get_workday_range(start="20260918", end="20260914")
        assert bad.success is False
        assert bad.error_detail["error_type"] == "invalid_param"

    def test_yaml_honestly_documents_no_holiday_calendar(self):
        for tool in ("get_workday", "get_workday_range"):
            block = _yaml_block(tool)
            assert "节假日" in block, f"{tool} 合同必须诚实声明未剔除法定节假日"


# ---------------------------------------------------------------------------
# M3: get_stock_info 异常透明 + BJ 跳过在线快照
# ---------------------------------------------------------------------------


class TestM3StockInfo:
    async def test_total_outage_propagates_as_error(self):
        q = FakeQuotes(quotes_exc=RuntimeError("TDX connection lost"))
        reader = FakeReader(daily_df=None, minute_df=None)
        client = make_client()
        client._quotes = q
        client._reader = reader

        result = await client.get_stock_info("600000", market="sh")

        assert result.success is False
        assert result.error_detail["error_type"] == "internal_error"

    async def test_none_quote_frame_does_not_crash(self):
        q = FakeQuotes(rows=[])  # quotes() 返空 DataFrame
        reader = FakeReader(daily_df=None, minute_df=None)
        client = make_client()
        client._quotes = q
        client._reader = reader

        result = await client.get_stock_info("600000", market="sh")

        # 三源皆空但无异常 → no_data, 不是 AttributeError 被吞后伪装成功
        assert result.success is True
        assert result.data is None

    async def test_bj_skips_online_quote_but_keeps_offline(self):
        q = FakeQuotes(quotes_exc=AssertionError("BJ quotes 不应被调用"))
        daily = pd.DataFrame([{"date": "2026-09-18", "close": 5.0}])
        reader = FakeReader(daily_df=daily, minute_df=None)
        client = make_client()
        client._quotes = q
        client._reader = reader

        result = await client.get_stock_info("430047", market="bj")

        assert result.success is True
        assert result.data["quote"] is None
        assert len(result.data["kline"]) == 1
        assert q.quotes_calls == []

    async def test_invalid_market_rejected(self):
        client = make_client()
        result = await client.get_stock_info("600000", market="hk")
        assert result.success is False
        assert result.error_detail["error_type"] == "invalid_param"


# ---------------------------------------------------------------------------
# M4: search_stock 路径锚定 / 连接关闭 / 920 号段 / 拼音诚实
# ---------------------------------------------------------------------------


def _make_board_db(path: Path, rows: list[tuple[str, str]]):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE sector_stocks ("
        "sector_id TEXT, stock_code TEXT, stock_name TEXT, rank INTEGER, "
        "UNIQUE(sector_id, stock_code))"
    )
    for i, (code, name) in enumerate(rows):
        conn.execute(
            "INSERT INTO sector_stocks VALUES (?, ?, ?, ?)",
            (f"s{i}", code, name, i),
        )
    conn.commit()
    conn.close()


class TestM4SearchStock:
    async def test_sqlite_path_anchored_and_920_segment_bj(self, tmp_path, monkeypatch):
        db_path = tmp_path / "board.db"
        _make_board_db(db_path, [
            ("600519", "贵州茅台"),
            ("000001", "平安银行"),
            ("430047", "诺思兰德"),
            ("920819", "北交新股"),
        ])
        monkeypatch.setattr(
            "src.unihive.api.mootdx2_client.resolve_project_path",
            lambda p, default: db_path,
        )
        reader = FakeReader(block_value=None)  # 生产实测: 无参 block() 恒 None
        client = make_client()
        client._reader = reader

        result = await client.search_stock("")

        assert result.success is True
        by_code = {r["code"]: r["market"] for r in result.data}
        assert by_code == {
            "600519": "sh",
            "000001": "sz",
            "430047": "bj",
            "920819": "bj",
        }

    async def test_reader_block_dead_path_falls_back_to_sqlite(self, tmp_path, monkeypatch):
        db_path = tmp_path / "board.db"
        _make_board_db(db_path, [("600519", "贵州茅台")])
        monkeypatch.setattr(
            "src.unihive.api.mootdx2_client.resolve_project_path",
            lambda p, default: db_path,
        )
        reader = FakeReader(block_value=None)
        client = make_client(SimpleNamespace(tdxdir=str(tmp_path)))
        client._reader = reader

        result = await client.search_stock("600519")

        assert result.success is True
        assert result.data[0]["code"] == "600519"
        assert reader.block_calls[0]["symbol"] == ""

    async def test_conn_closed_even_on_query_error(self, tmp_path, monkeypatch):
        closed: list[bool] = []
        db_path = tmp_path / "board.db"
        db_path.touch()  # 绕过 exists() 门控, 强制走到 connect

        class BoomConn:
            def execute(self, *a, **k):
                raise RuntimeError("boom")

            def close(self):
                closed.append(True)

        fake_sqlite3 = types.ModuleType("sqlite3")
        fake_sqlite3.connect = lambda *a, **k: BoomConn()
        monkeypatch.setitem(sys.modules, "sqlite3", fake_sqlite3)
        monkeypatch.setattr(
            "src.unihive.api.mootdx2_client.resolve_project_path",
            lambda p, default: db_path,
        )

        client = make_client()
        assert client._search_stock_from_sqlite("whatever") == []
        assert closed == [True]

    def test_yaml_does_not_claim_pinyin(self):
        block = _yaml_block("search_stock_local")
        assert "拼音" not in block, "board.db 无拼音列, 合同不得声称支持拼音匹配"


# ---------------------------------------------------------------------------
# LOW 项
# ---------------------------------------------------------------------------


class TestLowMinuteDataCounter:
    async def test_get_minute_data_increments_counter(self):
        client = make_client()
        client._quotes = FakeQuotes(minute_rows=[])
        before = client._metrics["total_requests"]

        await client.get_minute_data("600000")

        assert client._metrics["total_requests"] == before + 1


class TestLowAtr:
    async def test_empty_kline_is_no_data_not_empty_dict(self):
        client = make_client()
        client._quotes = FakeQuotes(bars_rows=[])

        result = await client.indicator_atr("600000")

        assert result.success is True
        assert result.data is None
        assert result.error_detail["error_type"] == "no_data"

    @pytest.mark.parametrize("limit", [0, -5, 1001])
    async def test_limit_out_of_range_invalid_param(self, limit):
        client = make_client()
        client._quotes = FakeQuotes()
        result = await client.indicator_atr("600000", limit=limit)
        assert result.success is False
        assert result.error_detail["error_type"] == "invalid_param"


class TestLowTopBoard:
    async def test_bj_market_explicit_no_data(self):
        client = make_client()
        client._quotes = FakeQuotes()

        result = await client.stock_top_board(market="bj")

        assert result.success is True
        assert result.data is None
        assert result.error_detail["error_type"] == "no_data"
        assert "北交" in result.error_detail["message"]

    async def test_unsupported_sort_warns(self):
        q = FakeQuotes(
            stocks_by_market={
                1: [{"code": "600000", "name": "浦发银行"}],
                0: [{"code": "000001", "name": "平安银行"}],
            },
            rows=[
                _quote_row(1, "600000", 10.99, 10.0),
                _quote_row(0, "000001", 10.05, 10.0),
            ],
        )
        client = make_client()
        client._quotes = q

        result = await client.stock_top_board(sort_by="turnover", limit=5)

        assert result.success is True
        assert result.error_detail.get("warn")

        # L1 缓存命中后 warn 仍在
        cached = await client.stock_top_board(sort_by="turnover", limit=5)
        assert cached.error_detail.get("warn")

    async def test_supported_sort_no_warn(self):
        q = FakeQuotes(
            stocks_by_market={1: [{"code": "600000", "name": "浦发银行"}]},
            rows=[_quote_row(1, "600000", 10.99, 10.0)],
        )
        client = make_client()
        client._quotes = q

        result = await client.stock_top_board(sort_by="change_pct", limit=5)
        assert result.success is True
        assert not result.error_detail


class TestLowIndexAllContract:
    def test_yaml_no_constituents_claim_and_historical_ttl(self):
        block = _yaml_block("get_index_all")
        assert "成分股" not in block, "q.index 只返指数 K 线, 不返成分股"
        assert "cache_ttl_key: historical" in block

    def test_yaml_atr_historical_ttl(self):
        assert "cache_ttl_key: historical" in _yaml_block("indicator_atr")


class TestLowDailyMetaTuple:
    def test_sync_returns_records_and_meta_tuple(self):
        idx = pd.to_datetime(["2026-09-17", "2026-09-18"])
        daily = pd.DataFrame(
            {"open": [1.0, 2.0], "high": [1.1, 2.1], "low": [0.9, 1.9],
             "close": [1.05, 2.05], "vol": [10, 20]},
            index=idx,
        )
        client = make_client()
        client._reader = FakeReader(daily_df=daily)

        records, meta = client._get_daily_sync("600000")

        assert isinstance(records, list) and len(records) == 2
        assert records[0]["date"] == "2026-09-17"
        assert meta["kind"] == "stock"
        assert not hasattr(client, "_last_daily_meta")

    async def test_disabled_l1_path_does_not_raise_unbound_local(self):
        """cachetools ImportError 路径: parsed_for_key 未定义不应 UnboundLocalError。"""
        client = make_client()
        client._DAILY_L1_ENABLED = False
        client._reader_semaphore = asyncio.Semaphore(4)
        client._reader = FakeReader(daily_df=None)

        result = await client.get_daily("600000")

        assert result.success is True
        assert result.data is None  # no_data, 不是 500/internal_error


class TestLowUnusualLabels:
    @staticmethod
    def _client(rows):
        block_df = pd.DataFrame([
            {"blockname": "A", "block_type": 1, "code_index": 0, "code": "1600000"},
            {"blockname": "A", "block_type": 1, "code_index": 1, "code": "1600016"},
            {"blockname": "A", "block_type": 1, "code_index": 2, "code": "0000001"},
        ])
        q = FakeQuotes(block_df=block_df, rows=rows)
        client = make_client()
        client._quotes = q
        return client

    def test_shrink_volume_event_labeled_shrink(self):
        client = self._client([
            _quote_row(1, "600000", 10.6, 10.0, vol=10),    # +6%, 地量
            _quote_row(1, "600016", 9.4, 10.0, vol=20),     # -6%
            _quote_row(0, "000001", 10.7, 10.0, vol=1000),  # +7%, 放量
        ])
        result = client._stock_unusual_sync(event_type="缩量")
        assert len(result) == 1
        assert result[0]["code"] == "600000"
        assert result[0]["event"] == "缩量上涨"

    def test_swell_volume_event_labeled_swell(self):
        client = self._client([
            _quote_row(1, "600000", 10.6, 10.0, vol=10),
            _quote_row(1, "600016", 9.4, 10.0, vol=20),
            _quote_row(0, "000001", 10.7, 10.0, vol=1000),
        ])
        result = client._stock_unusual_sync(event_type="放量")
        assert [(r["code"], r["event"]) for r in result] == [("000001", "放量上涨")]

    def test_all_event_does_not_claim_volume(self):
        client = self._client([
            _quote_row(1, "600000", 10.6, 10.0, vol=10),
            _quote_row(1, "600016", 9.4, 10.0, vol=20),
        ])
        result = client._stock_unusual_sync(event_type="all")
        labels = {r["code"]: r["event"] for r in result}
        assert labels == {"600000": "上涨", "600016": "下跌"}


class TestLowMinutesDefaultDate:
    def test_sync_defaults_to_today(self):
        import datetime
        q = FakeQuotes(minutes_rows=[{"close": 10.0}])
        client = make_client()
        client._quotes = q

        client._get_minutes_sync("600000")

        assert q.minutes_calls[0]["date"] == datetime.date.today().strftime("%Y%m%d")

    def test_yaml_no_demo_date(self):
        block = _yaml_block("get_minutes")
        assert "20191023" not in block


class TestLowListLimitsAndDispatch:
    @pytest.mark.parametrize("method,kwargs", [
        ("get_stock_codes", {"limit": 0}),
        ("get_etf_codes", {"limit": -1}),
        ("get_etf_list", {"exchange": "sh", "limit": 0}),
    ])
    async def test_non_positive_limit_invalid_param(self, method, kwargs):
        client = make_client()
        client._quotes = FakeQuotes()
        result = await getattr(client, method)(**kwargs)
        assert result.success is False
        assert result.error_detail["error_type"] == "invalid_param"

    async def test_call_tool_aliases_still_dispatch(self):
        client = make_client()
        client._reader = FakeReader(daily_df=None)
        # get_daily_local → get_daily (L1 miss → 离线无数据 no_data), 验证别名表仍在
        result = await client.call_tool("get_daily_local", {"code": "600000"})
        assert result.success is True

        unknown = await client.call_tool("does_not_exist", {})
        assert unknown.success is False
        assert "Unknown tool" in str(unknown.error)
