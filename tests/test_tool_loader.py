"""Unit tests for src.tool_loader."""
from pathlib import Path

import pytest


def test_load_all_tools_empty_config(tmp_path):
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}", encoding="utf-8")
    config = {"upstreams": {}, "tools": [], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    assert tools == []


def test_load_all_tools_manual_only(tmp_path):
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


def test_load_all_tools_missing_generated_is_noop(tmp_path):
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
