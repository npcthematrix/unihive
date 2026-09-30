"""Normalizer 单元测试"""
import pytest

from src.unihive.core.normalizer import Normalizer


class TestNormalizeCode:
    @pytest.mark.parametrize("raw,expected", [
        ("600519", "sh600519"),     # 上海 6 开头
        ("000001", "sz000001"),     # 深圳 0 开头
        ("300750", "sz300750"),     # 深圳 3 开头 (创业板)
        ("830799", "bj830799"),     # 北京 8 开头
        ("430047", "bj430047"),     # 北京 4 开头
        ("sh600519", "sh600519"),   # 已有前缀
        ("sz000001", "sz000001"),
        ("bj830799", "bj830799"),
        # 注意: Normalizer 不改写已有 prefix 的大小写，调用方需统一输入
        ("  600519  ", "sh600519"), # 去除空白
        ("", ""),                   # 空字符串
        # 港股：.HK 后缀（THS/fuyao 格式，大小写不敏感）
        ("00700.HK", "hk00700"),
        ("00005.hk", "hk00005"),
        # 港股：hk 前缀
        ("hk00700", "hk00700"),
        ("HK00700", "hk00700"),
        # 港股：不足 5 位左侧补零
        ("700.HK", "hk00700"),
        ("hk5", "hk00005"),
        # 港股：1~5 位裸码
        ("00700", "hk00700"),
        ("1", "hk00001"),
    ])
    def test_normalize(self, raw, expected):
        assert Normalizer.normalize_code(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        # 6 位裸码仍按 A 股号段，不被港股规则吞掉
        ("600519", "sh600519"),
        ("000001", "sz000001"),
        # 非法/超宽代码原样返回
        ("abc", "abc"),
        ("1234567", "1234567"),
        ("hk007000", "hk007000"),   # 6 位超宽不匹配港股
        ("00700.XX", "00700.XX"),
    ])
    def test_normalize_non_hk_unchanged(self, raw, expected):
        assert Normalizer.normalize_code(raw) == expected


class TestToGatewayResponse:
    def test_success(self):
        r = Normalizer.to_gateway_response(
            success=True, data={"x": 1}, source="tdx_local"
        )
        assert r["success"] is True
        assert r["data"] == {"x": 1}
        assert r["source"] == "tdx_local"
        assert r["error"] is None
        assert r["hops"] == []

    def test_failure(self):
        r = Normalizer.to_gateway_response(
            success=False, error="boom", hops=[]
        )
        assert r["success"] is False
        assert r["error"] == "boom"
        assert r["hops"] == []

    def test_hops_default_to_empty_list(self):
        r = Normalizer.to_gateway_response(success=True, data=1)
        assert r["hops"] == []

    def test_hops_pass_through(self):
        hops = [{"source": "a", "tool": "x"}]
        r = Normalizer.to_gateway_response(success=True, data=1, hops=hops)
        assert r["hops"] is hops

    def test_warn_absent_by_default(self):
        # 旧消费者看到的字段集合不变：无提示时信封里没有 warn 键
        r = Normalizer.to_gateway_response(success=True, data=1)
        assert "warn" not in r

    def test_warn_included_when_provided(self):
        r = Normalizer.to_gateway_response(
            success=True, data=1, warn="代码 000001 为跨市场歧义码"
        )
        assert r["warn"] == "代码 000001 为跨市场歧义码"
