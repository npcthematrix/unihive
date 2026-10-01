"""港股代码解析 (hk_symbols) 单元测试。"""
import pytest

from src.unihive.api.hk_symbols import (
    HK_INDEX_MARKET,
    HK_STOCK_MARKET,
    hk_day_path_segments,
    parse_hk_index,
    parse_hk_stock,
)


class TestParseHkStock:
    @pytest.mark.parametrize("raw,expected", [
        ("00700.HK", "00700"),
        ("00700.hk", "00700"),
        ("hk00700", "00700"),
        ("HK00700", "00700"),
        ("700.HK", "00700"),     # 左侧补零
        ("hk5", "00005"),
        ("00700", "00700"),        # 裸 1~5 位即港股
        ("1", "00001"),
    ])
    def test_parses_individual_stock(self, raw, expected):
        hk = parse_hk_stock(raw)
        assert hk is not None
        assert hk.token == expected
        assert hk.market == HK_STOCK_MARKET
        assert hk.key == f"hk{expected}"
        assert hk.filename == f"31#{expected}.day"
        assert hk.is_index is False

    @pytest.mark.parametrize("raw", [
        "600519",       # 6 位裸码 = A 股，不抢
        "sh600036",
        "",
    ])
    def test_returns_none_for_non_hk(self, raw):
        assert parse_hk_stock(raw) is None

    def test_wrapped_alpha_is_value_error(self):
        # hkHSI 是港股指数而非个股
        with pytest.raises(ValueError):
            parse_hk_stock("hkHSI")

    def test_wrapped_overwide_number_is_value_error(self):
        with pytest.raises(ValueError):
            parse_hk_stock("123456.HK")


class TestParseHkIndex:
    @pytest.mark.parametrize("raw,token", [
        ("hkHSI", "HSI"),
        ("HSI.HK", "HSI"),
        ("hkhsi", "HSI"),        # 归一为大写
        ("hkCES120", "CES120"),
        ("CES120.HK", "CES120"),
        ("hkHZ5014", "HZ5014"),
    ])
    def test_parses_index(self, raw, token):
        hk = parse_hk_index(raw)
        assert hk is not None
        assert hk.token == token
        assert hk.market == HK_INDEX_MARKET
        assert hk.key == f"hk{token}"
        assert hk.filename == f"27#{token}.day"
        assert hk.is_index is True

    @pytest.mark.parametrize("raw", [
        "HSI",          # 无包装裸码不与 A 股抢
        "000001",
        "sh600000",
        "",
    ])
    def test_returns_none_without_wrapper(self, raw):
        assert parse_hk_index(raw) is None

    def test_wrapped_number_is_value_error(self):
        with pytest.raises(ValueError):
            parse_hk_index("00700.HK")

    @pytest.mark.parametrize("raw", ["hk1abc2", "hkTOOLONGSYMX"])  # 11 位超宽
    def test_illegal_token_is_value_error(self, raw):
        with pytest.raises(ValueError):
            parse_hk_index(raw)


def test_hk_day_path_segments():
    hk = parse_hk_stock("00700.HK")
    assert hk_day_path_segments(hk) == ("vipdoc", "ds", "lday", "31#00700.day")
