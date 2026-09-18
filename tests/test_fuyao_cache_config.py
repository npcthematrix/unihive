"""FUYAO 工具级 TTL 覆盖: pydantic 模型 + save/get (config.db overlay) + cache_strategy 命中。

2026-09-18 P1: 持久化从 YAML endpoints 模型改为 config.db 薄 overlay
(fuyao_cache.tools.<tool> 点路径, 全局整数 revision 乐观锁)。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.unihive.core import cache_strategy
from src.unihive.models.fuyao_cache import FuyaoCacheConfig
from src.unihive.storage.config_store import ConfigStore
from src.unihive.utils import console_api as ca


# ---------------------------------------------------------------------------
# Pydantic schema
# ---------------------------------------------------------------------------


class TestFuyaoCacheConfigSchema:
    def test_default_is_empty_tools(self):
        cfg = FuyaoCacheConfig()
        assert cfg.tools == {}
        assert cfg.is_empty() is True
        assert cfg.override_count() == 0

    def test_tools_map_accepted(self):
        cfg = FuyaoCacheConfig(tools={"get_a_share_history": 60, "get_fund_nav": 0})
        assert cfg.override_count() == 2
        assert cfg.is_empty() is False

    @pytest.mark.parametrize("bad", [-1, -60])
    def test_negative_ttl_rejected(self, bad):
        with pytest.raises(ValidationError):
            FuyaoCacheConfig(tools={"t": bad})

    def test_bool_rejected(self):
        # bool 是 int 子类 — 必须显式挡掉, 否则 True 会被当成 1 秒
        with pytest.raises(ValidationError):
            FuyaoCacheConfig(tools={"t": True})  # type: ignore[dict-item]

    @pytest.mark.parametrize("bad", ["60", 1.5, None])
    def test_non_int_ttl_rejected(self, bad):
        with pytest.raises(ValidationError):
            FuyaoCacheConfig(tools={"t": bad})

    def test_empty_tool_name_rejected(self):
        with pytest.raises(ValidationError):
            FuyaoCacheConfig(tools={"": 60})

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            FuyaoCacheConfig(tools={"t": 60}, endpoints={})  # type: ignore[call-arg]

    def test_zero_means_explicit_no_cache(self):
        cfg = FuyaoCacheConfig(tools={"t": 0})
        assert cfg.is_empty() is False
        assert cfg.override_count() == 1


# ---------------------------------------------------------------------------
# console_api save/get — config.db overlay 语义
# ---------------------------------------------------------------------------

YAML_BASE = (
    "upstreams:\n"
    "  fuyao_a_share:\n"
    "    enabled: true\n"
    "    type: http\n"
    "upstream_tool_mapping:\n"
    "  t_fuyao_only:\n"
    "    fuyao_a_share: t_fuyao_only\n"
    "  t_mixed:\n"
    "    mootdx2: t_mixed\n"
    "    fuyao_a_share: t_mixed\n"
)


@pytest.fixture
async def fuyao_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "upstreams.yaml"
    cfg_file.write_text(YAML_BASE, encoding="utf-8")
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(cfg_file))
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    ca.set_config_store(store, [], 0)
    yield store
    ca.set_config_store(None)
    await store.close()


async def test_save_creates_overlay_rows_single_revision(fuyao_env):
    result = await ca.save_fuyao_cache({"tools": {"t_fuyao_only": 60}})
    assert result == {
        "fuyao_cache": {"tools": {"t_fuyao_only": 60}},
        "version": 1,
    }
    assert await fuyao_env.revision() == 1
    row = await fuyao_env.get_overlay("fuyao_cache.tools.t_fuyao_only")
    assert row is not None and row.value == 60


async def test_get_reflects_overlays_with_int_version(fuyao_env):
    assert ca.get_fuyao_cache() == {"tools": {}, "version": 0}
    await ca.save_fuyao_cache({"tools": {"t_fuyao_only": 0}})
    got = ca.get_fuyao_cache()
    assert got["tools"] == {"t_fuyao_only": 0}
    assert isinstance(got["version"], int) and got["version"] == 1


async def test_dropped_tool_row_is_deleted(fuyao_env):
    await ca.save_fuyao_cache({"tools": {"t_fuyao_only": 60, "other": 10}})
    await ca.save_fuyao_cache({"tools": {"t_fuyao_only": 60}})
    rows = {r.path for r in await fuyao_env.list_overlays(prefix="fuyao_cache.tools.")}
    assert rows == {"fuyao_cache.tools.t_fuyao_only"}


async def test_value_equal_to_yaml_baseline_deletes_row(tmp_path, monkeypatch):
    yaml_text = YAML_BASE + "fuyao_cache:\n  tools:\n    t_fuyao_only: 30\n"
    cfg_file = tmp_path / "upstreams.yaml"
    cfg_file.write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(cfg_file))
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    ca.set_config_store(store, [], 0)
    try:
        # 出厂 30: 改成 60 落覆盖, 再改回 30 行被删, 生效值回落 30
        await ca.save_fuyao_cache({"tools": {"t_fuyao_only": 60}})
        await ca.save_fuyao_cache({"tools": {"t_fuyao_only": 30}})
        assert await store.list_overlays() == []
        assert ca.get_fuyao_cache()["tools"] == {"t_fuyao_only": 30}
    finally:
        ca.set_config_store(None)
        await store.close()


async def test_save_stale_revision_raises_conflict(fuyao_env):
    with pytest.raises(ca.VersionConflict):
        await ca.save_fuyao_cache({"tools": {"t_fuyao_only": 60}}, expected_version=99)
    # 事务回滚, 不留行
    assert await fuyao_env.list_overlays() == []


async def test_numeric_string_version_accepted(fuyao_env):
    result = await ca.save_fuyao_cache(
        {"tools": {"t_fuyao_only": 60}}, expected_version="0"
    )
    assert result["version"] == 1


async def test_save_validates_payload(fuyao_env):
    with pytest.raises(ValueError):
        await ca.save_fuyao_cache({"tools": {"t": -5}})
    with pytest.raises(ValueError):
        await ca.save_fuyao_cache({"tools": {"t": "60"}})


async def test_save_without_store_raises_unavailable(tmp_path, monkeypatch):
    cfg_file = tmp_path / "upstreams.yaml"
    cfg_file.write_text(YAML_BASE, encoding="utf-8")
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(cfg_file))
    ca.set_config_store(None)
    with pytest.raises(ca.ConfigStoreUnavailable):
        await ca.save_fuyao_cache({"tools": {"t_fuyao_only": 60}})


async def test_save_hot_updates_injected_runtime_config(fuyao_env, monkeypatch):
    runtime = {"cache": {"ttl": {}}}
    monkeypatch.setattr(ca, "_GW_CFG", runtime)
    monkeypatch.setattr(ca, "_INJECTED", True)
    await ca.save_fuyao_cache({"tools": {"t_fuyao_only": 60, "other": 0}})
    assert runtime["fuyao_cache"]["tools"] == {"t_fuyao_only": 60, "other": 0}
    # 清空保存 → 运行时也清空
    await ca.save_fuyao_cache({"tools": {}})
    assert runtime["fuyao_cache"]["tools"] == {}


async def test_yaml_baseline_file_never_written(fuyao_env, tmp_path):
    import time

    cfg_file = tmp_path / "upstreams.yaml"
    before = cfg_file.stat().st_mtime_ns
    time.sleep(0.01)
    await ca.save_fuyao_cache({"tools": {"t_fuyao_only": 60}})
    assert cfg_file.stat().st_mtime_ns == before


# ---------------------------------------------------------------------------
# cache_strategy.fuyao_ttl_for — 新 shape 命中路径
# ---------------------------------------------------------------------------


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
