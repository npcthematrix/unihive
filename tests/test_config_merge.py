"""core.config_merge 纯函数测试: 点路径 overlay → 深合并 dict。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from unihive.core.config_merge import apply_config_overlays, overlay_tree


def row(path, value):
    return SimpleNamespace(path=path, value=value)


def test_empty_rows_returns_deep_equal_copy():
    baseline = {"upstreams": {"a": {"enabled": True}}, "cache": {"ttl": {}}}
    merged = apply_config_overlays(baseline, [])
    assert merged == baseline
    # 深拷贝: 改返回值不污染基线
    merged["upstreams"]["a"]["enabled"] = False
    assert baseline["upstreams"]["a"]["enabled"] is True


def test_scalar_leaf_override():
    baseline = {"upstreams": {"fuyao": {"enabled": True, "url": "x"}}}
    merged = apply_config_overlays(
        baseline, [row("upstreams.fuyao.enabled", False)]
    )
    assert merged["upstreams"]["fuyao"] == {"enabled": False, "url": "x"}
    assert baseline["upstreams"]["fuyao"]["enabled"] is True  # 基线不变


def test_creates_missing_intermediate_and_top_level():
    merged = apply_config_overlays(
        {"a": 1}, [row("fuyao_cache.tools.get_x", 60)]
    )
    assert merged == {
        "a": 1,
        "fuyao_cache": {"tools": {"get_x": 60}},
    }


def test_nested_dict_merge_preserves_siblings():
    baseline = {"fuyao_cache": {"tools": {"get_x": 10, "get_y": 20}}}
    merged = apply_config_overlays(
        baseline, [row("fuyao_cache.tools.get_x", 60)]
    )
    assert merged["fuyao_cache"]["tools"] == {"get_x": 60, "get_y": 20}


def test_list_value_replaces_not_merges():
    baseline = {"x": {"chain": ["a", "b"]}}
    merged = apply_config_overlays(baseline, [row("x.chain", ["c"])])
    assert merged["x"]["chain"] == ["c"]


def test_overlay_value_can_be_object():
    merged = apply_config_overlays(
        {}, [row("upstreams.x.extra", {"timeout": 5, "retries": 2})]
    )
    assert merged["upstreams"]["x"]["extra"] == {"timeout": 5, "retries": 2}


def test_path_conflict_with_non_dict_intermediate_raises():
    # baseline 在中间段是标量, overlay 却想继续下钻
    with pytest.raises(ValueError, match="conflict"):
        apply_config_overlays(
            {"upstreams": {"fuyao": False}},
            [row("upstreams.fuyao.enabled", False)],
        )


def test_overlay_tree_conflict_between_rows_raises():
    # 两行自身打架: 一行把 a.b 当叶子, 另一行把 a.b.c 当路径
    with pytest.raises(ValueError, match="conflict"):
        overlay_tree(
            [row("a.b", 1), row("a.b.c", 2)]
        )


def test_rows_sorted_deterministic_regardless_of_input_order():
    baseline = {"fuyao_cache": {"tools": {}}}
    r1 = apply_config_overlays(
        baseline,
        [
            row("fuyao_cache.tools.z", 1),
            row("fuyao_cache.tools.a", 2),
            row("fuyao_cache.tools.m", 3),
        ],
    )
    r2 = apply_config_overlays(
        baseline,
        [
            row("fuyao_cache.tools.m", 3),
            row("fuyao_cache.tools.z", 1),
            row("fuyao_cache.tools.a", 2),
        ],
    )
    assert r1 == r2


def test_later_row_wins_for_same_path():
    merged = apply_config_overlays(
        {}, [row("a.b", 1), row("a.b", 2)]
    )
    assert merged["a"]["b"] == 2
