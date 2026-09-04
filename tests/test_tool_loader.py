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


def test_derive_fallback_chain_single_upstream():
    from src.tool_loader import derive_fallback_chain
    config = {"upstreams": {"t1": {"priority": 1}}}
    assert derive_fallback_chain(config, "t1") == ["t1"]


def test_derive_fallback_chain_multiple_upstreams():
    from src.tool_loader import derive_fallback_chain
    config = {
        "upstreams": {
            "t1": {"priority": 1},
            "t2": {"priority": 2},
            "t3": {"priority": 3},
        }
    }
    assert derive_fallback_chain(config, "t1") == ["t1", "t2", "t3"]


def test_derive_fallback_chain_missing_upstream_returns_empty():
    from src.tool_loader import derive_fallback_chain
    config = {"upstreams": {"t1": {"priority": 1}}}
    assert derive_fallback_chain(config, "missing") == []
