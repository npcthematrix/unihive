"""P1 golden 等价性: 空 overlay 时 effective config/specs 与纯 YAML 路径完全一致。

设计文档 §9 P1 验收: "空 overlay 时 effective dict/specs 与现状 YAML 路径
golden 全量一致 (289 工具、mapping、TTL), 不一致不许合并"。
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from unihive.core.config_merge import apply_config_overlays
from unihive.core.tool_loader import load_all_tools
from unihive.storage.config_store import ConfigStore
from unihive.utils.config_loader import load_config, merge_config_overlay
from unihive.gateway_server import GatewayServer

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
UPSTREAMS_YAML = CONFIG_DIR / "upstreams.yaml"


def _baseline_config() -> dict:
    config = load_config(str(UPSTREAMS_YAML), strict_env=False)
    config_yaml = CONFIG_DIR / "config.yaml"
    if config_yaml.exists():
        merge_config_overlay(config, load_config(str(config_yaml), strict_env=False))
    return config


def test_empty_overlay_config_is_deep_equal():
    baseline = _baseline_config()
    merged = apply_config_overlays(baseline, [])
    assert merged == baseline


def test_empty_overlay_does_not_mutate_baseline():
    baseline = _baseline_config()
    merged = apply_config_overlays(baseline, [])
    merged["cache"]["ttl"]["historical"] = 999999
    assert baseline["cache"]["ttl"]["historical"] != 999999


def test_empty_overlay_specs_golden_289():
    baseline = _baseline_config()

    specs_yaml = load_all_tools(UPSTREAMS_YAML, copy.deepcopy(baseline))
    specs_overlay = load_all_tools(
        UPSTREAMS_YAML, apply_config_overlays(baseline, [])
    )

    assert len(specs_yaml) == 289
    assert [s["name"] for s in specs_overlay] == [s["name"] for s in specs_yaml]
    # 全字段 (含 routing/params/mapping/ttl key) 一致, 不只是名字
    assert specs_overlay == specs_yaml


@pytest.mark.asyncio
async def test_empty_real_store_roundtrip_matches_yaml(tmp_path):
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    rows = await store.list_overlays()
    assert rows == []
    baseline = _baseline_config()
    assert apply_config_overlays(baseline, rows) == baseline
    await store.close()


@pytest.mark.asyncio
async def test_gateway_skips_db_when_source_yaml(tmp_path, monkeypatch):
    # autouse fixture 已把 UNIHIVE_CONFIG_SOURCE 钉成 yaml; 再显式声明确认。
    monkeypatch.setenv("UNIHIVE_CONFIG_SOURCE", "yaml")
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", str(tmp_path / "config.db"))
    server = GatewayServer(str(UPSTREAMS_YAML), strict_env=False,
                           strict_validation=False)
    try:
        await server._load_db_config()
        assert server.config_store is None
    finally:
        await server.stop()
