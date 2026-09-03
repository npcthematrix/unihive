"""Normalizer 单元测试"""
import pytest

from src.normalizer import Normalizer


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
    ])
    def test_normalize(self, raw, expected):
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
