"""FUYAO 工具级 TTL 覆盖的运行时命中: cache_strategy.fuyao_ttl_for。

配置段 ``fuyao_cache.tools`` 由 YAML 出厂模板/config.db 提供; 控制台编辑
端点已于 2026-09-21 下线, 运行时 TTL 机制保留。
"""
from __future__ import annotations

from src.unihive.core import cache_strategy


def _cfg_with(tools: dict) -> dict:
    return {
        "upstream_tool_mapping": {
            "t_fuyao_only": {"fuyao_a_share": "x"},
            "t_mixed": {"mootdx2": "x", "fuyao_a_share": "x"},
        },
        "fuyao_cache": {"tools": tools},
    }


def test_ttl_returns_tool_override_including_zero():
    cfg = _cfg_with({"t_fuyao_only": 60})
    assert cache_strategy.fuyao_ttl_for(cfg, "t_fuyao_only") == 60
    cfg0 = _cfg_with({"t_fuyao_only": 0})
    assert cache_strategy.fuyao_ttl_for(cfg0, "t_fuyao_only") == 0


def test_ttl_none_when_unmapped():
    assert cache_strategy.fuyao_ttl_for(_cfg_with({"other": 60}), "t_fuyao_only") is None
    assert cache_strategy.fuyao_ttl_for({}, "t_fuyao_only") is None


def test_ttl_none_for_mixed_chain():
    # 多源混合链不加 fuyao 工具级 TTL, 回退全局 ttl_key
    cfg = _cfg_with({"t_mixed": 60})
    assert cache_strategy.fuyao_ttl_for(cfg, "t_mixed") is None


def test_endpoint_gate_pure_fuyao_only():
    cfg = _cfg_with({})
    assert (
        cache_strategy.get_fuyao_endpoint_for_tool(cfg, "t_fuyao_only")
        == "fuyao_a_share"
    )
    assert cache_strategy.get_fuyao_endpoint_for_tool(cfg, "t_mixed") is None
    assert cache_strategy.get_fuyao_endpoint_for_tool(cfg, "missing") is None
