"""Unit tests for src.tool_loader."""
from pathlib import Path

import pytest


def test_load_all_tools_empty_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}", encoding="utf-8")
    config = {"upstreams": {}, "tools": [], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    assert tools == []


def test_load_all_tools_manual_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools:\n  - name: manual_tool\n    routing: manual_tool\n", encoding="utf-8")
    config = {"upstreams": {}, "tools": [{"name": "manual_tool", "routing": "manual_tool"}], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    assert [t["name"] for t in tools] == ["manual_tool"]


def test_load_all_tools_merges_generated_file(tmp_path):
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools:\n  - name: m\n", encoding="utf-8")
    gen = tmp_path / "tools_tdx_tq_local.yaml"
    gen.write_text("tools:\n  - name: g\n", encoding="utf-8")
    config = {"upstreams": {}, "tools": [{"name": "m"}], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    names = {t["name"] for t in tools}
    assert names == {"m", "g"}


def test_load_all_tools_generated_writes_mapping_to_top_level(tmp_path):
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools: []", encoding="utf-8")
    gen = tmp_path / "tools_tdx_tq_local.yaml"
    gen.write_text(
        "tools:\n  - name: g\n    routing: g_route\n    upstream_tool_mapping:\n        tdx_tq_local: g_upstream\n",
        encoding="utf-8",
    )
    config = {"upstreams": {}, "tools": [], "upstream_tool_mapping": {}}
    load_all_tools(cfg, config)
    assert config["upstream_tool_mapping"]["g_route"] == {"tdx_tq_local": "g_upstream"}


def test_load_all_tools_missing_generated_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools:\n  - name: m\n", encoding="utf-8")
    config = {"upstreams": {}, "tools": [{"name": "m"}], "upstream_tool_mapping": {}}
    # no generated file exists anywhere in tmp_path
    tools = load_all_tools(cfg, config)
    assert [t["name"] for t in tools] == ["m"]


def test_load_all_tools_duplicate_name_generated_wins(tmp_path):
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools:\n  - name: dup\n    description: manual\n", encoding="utf-8")
    gen = tmp_path / "tools_tdx_tq_local.yaml"
    gen.write_text("tools:\n  - name: dup\n    description: generated\n", encoding="utf-8")
    config = {"upstreams": {}, "tools": [{"name": "dup", "description": "manual"}], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    by_name = {t["name"]: t for t in tools}
    assert by_name["dup"]["description"] == "generated"


def test_load_all_tools_searches_subdir_config(tmp_path):
    """If config_path is `config/upstreams.yaml`, generated file at `config/tools_tdx_tq_local.yaml` is also found."""
    from src.tool_loader import load_all_tools
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg = cfg_dir / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools: []", encoding="utf-8")
    gen = cfg_dir / "tools_tdx_tq_local.yaml"
    gen.write_text("tools:\n  - name: g\n", encoding="utf-8")
    config = {"upstreams": {}, "tools": [], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    assert [t["name"] for t in tools] == ["g"]


def test_get_cache_ttl_known_key():
    from src.tool_loader import get_cache_ttl
    config = {"cache": {"ttl": {"realtime_quote": 10}}}
    assert get_cache_ttl(config, "realtime_quote") == 10


def test_get_cache_ttl_unknown_key_returns_none():
    from src.tool_loader import get_cache_ttl
    config = {"cache": {"ttl": {"realtime_quote": 10}}}
    assert get_cache_ttl(config, "missing_key") is None


def test_get_cache_ttl_missing_cache_section_returns_none():
    from src.tool_loader import get_cache_ttl
    assert get_cache_ttl({}, "anything") is None


def test_get_cache_ttl_empty_key_returns_none():
    from src.tool_loader import get_cache_ttl
    config = {"cache": {"ttl": {"x": 5}}}
    assert get_cache_ttl(config, None) is None
    assert get_cache_ttl(config, "") is None


def test_derive_routing_chain_from_explicit_routing_config():
    """When YAML routing.X.chain is defined, return it verbatim."""
    from src.tool_loader import derive_routing_chain
    config = {
        "routing": {
            "get_minute_bar": {"chain": ["tokenwave_tdx", "tdx_local"]},
        },
        "upstream_tool_mapping": {
            "get_minute_bar": {"tokenwave_tdx": "x", "tdx_local": "y",
                               "fuyao_fund": "z"},  # noise — chain should win
        },
    }
    assert derive_routing_chain(config, "get_minute_bar") == [
        "tokenwave_tdx", "tdx_local",
    ]


def test_derive_routing_chain_falls_back_to_tool_mapping_keys():
    """When routing.X is missing, derive chain from upstream_tool_mapping keys (insertion order)."""
    from src.tool_loader import derive_routing_chain
    config = {
        "upstream_tool_mapping": {
            "search_stock": {"tdx_local": "a", "fuyao_meta": "b"},
        },
    }
    assert derive_routing_chain(config, "search_stock") == [
        "tdx_local", "fuyao_meta",
    ]


def test_derive_routing_chain_unknown_routing_key_returns_empty():
    from src.tool_loader import derive_routing_chain
    config = {
        "routing": {"get_minute_bar": {"chain": ["tokenwave_tdx"]}},
        "upstream_tool_mapping": {},
    }
    assert derive_routing_chain(config, "no_such_tool") == []


def test_derive_routing_chain_empty_when_nothing_defined():
    """Empty config — neither routing nor upstream_tool_mapping has the key."""
    from src.tool_loader import derive_routing_chain
    config = {"routing": {}, "upstream_tool_mapping": {}}
    assert derive_routing_chain(config, "anything") == []


def test_derive_routing_chain_routing_block_without_chain_field_returns_empty():
    """If routing.X exists but has no chain field, return empty (don't fall back to mapping)."""
    from src.tool_loader import derive_routing_chain
    config = {
        "routing": {"get_minute_bar": {"description": "分钟K线行情 (TDX)"}},
        "upstream_tool_mapping": {
            "get_minute_bar": {"tokenwave_tdx": "x"},
        },
    }
    # Spec decision: explicit routing block with no chain = no fallback.
    # The router (`src/router.py:_get_routing_chain`) treats empty chain as "no route",
    # so we match that contract here. Console callers see empty chain consistently.
    assert derive_routing_chain(config, "get_minute_bar") == []
