"""index_registry 解析规则测试（2026-09-20 移除 INDEX_MARKET_MAP 后）。

核心约束：
- 显式后缀/前缀始终被信任（sz000001 = 深市股票，不再报"后缀冲突"）
- 裸 000xxx 是跨市场歧义码：默认股票（Reader 推断 sz），指数入口按 sh
- 399/899/880 裸码按前缀约定直接确定市场
"""
import pytest

from src.unihive.api.index_registry import (
    INDEX_KIND,
    STOCK_KIND,
    ParsedSymbol,
    parse_symbol,
    to_reader_symbol,
)


def _triple(raw: str, **kw) -> tuple[str, str | None, str]:
    p = parse_symbol(raw, **kw)
    return p.kind, p.market, to_reader_symbol(p)


class TestBareAmbiguous000:
    """裸 000xxx：同代码跨市场标的的消歧。"""

    def test_bare_000001_defaults_to_sz_stock(self):
        # 平安银行（深市股票），Reader.find_path 前缀推断 sz
        assert _triple("000001") == (STOCK_KIND, None, "000001")

    def test_bare_000001_index_intent_resolves_to_sh_index(self):
        # 上证指数
        assert _triple("000001", intent=INDEX_KIND) == (INDEX_KIND, "sh", "sh000001")

    def test_bare_000002_defaults_to_sz_stock(self):
        assert _triple("000002") == (STOCK_KIND, None, "000002")

    def test_bare_000300_in_daily_entry_is_stock(self):
        # 通用日线入口：不替用户猜指数，需要指数请显式 sh000300
        assert _triple("000300") == (STOCK_KIND, None, "000300")

    def test_bare_000300_index_intent_is_sh_index(self):
        assert _triple("000300", intent=INDEX_KIND) == (INDEX_KIND, "sh", "sh000300")


class TestExplicitSuffixAlwaysTrusted:
    """显式市场后缀/前缀不得再报"与映射表冲突"。"""

    def test_sz000001_prefix_is_stock(self):
        assert _triple("sz000001") == (STOCK_KIND, "sz", "sz000001")

    def test_sz000001_dot_suffix_is_stock(self):
        assert _triple("000001.SZ") == (STOCK_KIND, "sz", "sz000001")

    def test_sh000001_prefix_is_index(self):
        assert _triple("sh000001") == (INDEX_KIND, "sh", "sh000001")

    def test_sh000300_dot_suffix_is_index(self):
        assert _triple("000300.SH") == (INDEX_KIND, "sh", "sh000300")

    def test_suffix_case_insensitive(self):
        assert _triple("000001.sz") == (STOCK_KIND, "sz", "sz000001")

    def test_explicit_stock_in_index_intent_still_stock(self):
        # 指数工具入口传深市股票 → 调用方据此拒绝
        p = parse_symbol("sz000001", intent=INDEX_KIND)
        assert p.kind == STOCK_KIND and p.market == "sz"


class TestBareIndexPrefixes:
    """无歧义指数前缀。"""

    @pytest.mark.parametrize("code,market", [
        ("399001", "sz"), ("399006", "sz"), ("399300", "sz"),
        ("899050", "bj"),
        ("880001", "sh"),
    ])
    def test_bare_index_prefix(self, code, market):
        assert _triple(code) == (INDEX_KIND, market, f"{market}{code}")

    @pytest.mark.parametrize("code", ["600000", "688001", "300750", "002594", "430047"])
    def test_bare_stocks_leave_market_to_reader(self, code):
        assert _triple(code) == (STOCK_KIND, None, code)

    @pytest.mark.parametrize("code,market", [
        ("881001", "sh"),   # 881 新版板块指数
        ("930050", "sh"),   # 中证新号段 930
        ("931000", "sh"),
        ("932000", "sh"),   # 中证A500
    ])
    def test_bare_new_sh_index_prefixes(self, code, market):
        assert _triple(code) == (INDEX_KIND, market, f"{market}{code}")

    @pytest.mark.parametrize("code,reader_symbol", [
        ("sh932000", "sh932000"),
        ("932000.SH", "sh932000"),
        ("sh881001", "sh881001"),
    ])
    def test_explicit_new_sh_index_prefixes(self, code, reader_symbol):
        assert _triple(code) == (INDEX_KIND, "sh", reader_symbol)


class TestBJSymbols:
    """北交所：899 指数 / 430/8xx/920 股票，显式前缀始终信任。"""

    @pytest.mark.parametrize("code", ["bj899050", "899050.BJ"])
    def test_bj_index_explicit(self, code):
        assert _triple(code) == (INDEX_KIND, "bj", "bj899050")

    @pytest.mark.parametrize("code,reader_symbol", [
        ("bj430047", "bj430047"),
        ("bj920000", "bj920000"),
    ])
    def test_bj_stocks_explicit(self, code, reader_symbol):
        assert _triple(code) == (STOCK_KIND, "bj", reader_symbol)

    @pytest.mark.parametrize("code", ["430047", "830799", "870866", "920000"])
    def test_bare_bj_stocks_leave_market_to_reader(self, code):
        assert _triple(code) == (STOCK_KIND, None, code)

    def test_sh_b_share_is_stock(self):
        # 900xxx = 沪市 B 股，不是指数
        assert _triple("900901") == (STOCK_KIND, None, "900901")
        assert _triple("sh900901") == (STOCK_KIND, "sh", "sh900901")


class TestInvalidInput:
    @pytest.mark.parametrize("raw", [None, "", "  ", "abc", "60000", "6000001", "000001.XX"])
    def test_rejects(self, raw):
        with pytest.raises(ValueError):
            parse_symbol(raw)

    def test_bad_intent_rejected(self):
        with pytest.raises(ValueError):
            parse_symbol("000001", intent="future")


def test_parsed_symbol_is_frozen():
    p = parse_symbol("600000")
    with pytest.raises(Exception):
        p.kind = INDEX_KIND  # type: ignore[misc]
    assert isinstance(p, ParsedSymbol)
    assert parse_symbol(" sh600000 ").code == "600000"  # strip 空白
