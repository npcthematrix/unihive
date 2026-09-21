"""P1 薄覆盖层服务函数 (2026-09-18): list / 单行删除 / 一键恢复出厂。

v2 实体化后 HTTP 路由 (/api/config/overlays*) 与覆盖管理 UI 已退役
(2026-09-18 P2), 这层服务函数仅作为 yaml 逃生/兼容内部路径保留,
由 set_config_store 注入测试; 生产链路走实体表 (见 test_config_db_load_chain.py)。

- 服务函数: list_config_overlays / delete_config_overlay / reset_config_overlays;
- enabled 覆盖行删除后, 运行时按 YAML 出厂值热收敛 (_reconcile_enabled_overlays)。
"""
from __future__ import annotations

import pytest

from src.unihive.storage.config_store import ConfigStore
from src.unihive.utils import console_api as ca


YAML_BASE = (
    "upstreams:\n"
    "  alpha:\n"
    "    enabled: true\n"
    "    type: http\n"
    "  beta:\n"
    "    type: stdio\n"
    "fuyao_cache:\n"
    "  tools:\n"
    "    t_base: 30\n"
)


@pytest.fixture
async def overlay_env(tmp_path, monkeypatch):
    cfg_file = tmp_path / "upstreams.yaml"
    cfg_file.write_text(YAML_BASE, encoding="utf-8")
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(cfg_file))
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    ca.set_config_store(store, [], 0)
    yield store
    ca.set_config_store(None)
    ca.set_gateway_server(None)
    await store.close()


# ---------------------------------------------------------------------------
# 服务函数
# ---------------------------------------------------------------------------


async def test_list_reflects_mirror_after_refresh(overlay_env):
    assert ca.list_config_overlays() == {"revision": 0, "overlays": []}
    await overlay_env.upsert_overlay("upstreams.alpha.enabled", False)
    # 直接写库不经 console_api, 镜像需显式刷新 (生产写路径内部都会刷新)
    await ca._refresh_overlay_snapshot()
    listing = ca.list_config_overlays()
    assert listing["revision"] == 1
    assert len(listing["overlays"]) == 1
    row = listing["overlays"][0]
    assert row["path"] == "upstreams.alpha.enabled"
    assert row["value"] is False
    assert row["revision"] == 1
    assert isinstance(row["updated_at"], str) and row["updated_at"]


async def test_list_requires_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(tmp_path / "x.yaml"))
    ca.set_config_store(None)
    with pytest.raises(ca.ConfigStoreUnavailable):
        ca.list_config_overlays()


async def test_delete_single_overlay_bumps_revision(overlay_env):
    await overlay_env.upsert_overlay("fuyao_cache.tools.t_base", 60)
    await ca._refresh_overlay_snapshot()

    result = await ca.delete_config_overlay("fuyao_cache.tools.t_base")
    assert result["path"] == "fuyao_cache.tools.t_base"
    assert result["version"] == 2
    assert result["warnings"] == []
    assert await overlay_env.list_overlays() == []
    # 镜像同步: 生效值回落出厂 30
    effective = ca.load_config()
    assert (effective.get("fuyao_cache") or {}).get("tools") == {"t_base": 30}


async def test_delete_missing_is_noop_no_revision_bump(overlay_env):
    result = await ca.delete_config_overlay("fuyao_cache.tools.ghost")
    assert result["version"] == 0


async def test_delete_stale_revision_conflicts(overlay_env):
    await overlay_env.upsert_overlay("fuyao_cache.tools.t_base", 60)
    with pytest.raises(ca.VersionConflict):
        await ca.delete_config_overlay(
            "fuyao_cache.tools.t_base", expected_version=0
        )
    # 冲突回滚, 行仍在
    assert await overlay_env.get_overlay("fuyao_cache.tools.t_base") is not None


async def test_delete_rejects_bad_path(overlay_env):
    with pytest.raises(ValueError):
        await ca.delete_config_overlay("upstreams..enabled")


async def test_reset_clears_all(overlay_env):
    fake = _FakeGatewayServer()
    ca.set_gateway_server(fake)
    await overlay_env.upsert_overlay("upstreams.alpha.enabled", False)
    await overlay_env.upsert_overlay("fuyao_cache.tools.t_base", 60)
    await ca._refresh_overlay_snapshot()

    # 两次独立 upsert 各 bump 一次 → 当前 revision=2, reset 再 bump → 3
    result = await ca.reset_config_overlays()
    assert result["version"] == 3
    assert result["warnings"] == []
    assert await overlay_env.list_overlays() == []
    # alpha 恢复出厂启用并触发热收敛
    assert fake.calls == [("alpha", True)]
    assert ca.load_config()["upstreams"]["alpha"]["enabled"] is True


async def test_reset_empty_is_noop(overlay_env):
    result = await ca.reset_config_overlays()
    assert result["version"] == 0


async def test_reset_stale_revision_conflicts(overlay_env):
    await overlay_env.upsert_overlay("upstreams.alpha.enabled", False)
    with pytest.raises(ca.VersionConflict):
        await ca.reset_config_overlays(expected_version=5)
    assert len(await overlay_env.list_overlays()) == 1


# ---------------------------------------------------------------------------
# enabled 覆盖删除 → 运行时热收敛
# ---------------------------------------------------------------------------


class _FakeGatewayServer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    async def set_upstream_enabled(self, name: str, enabled: bool) -> dict:
        self.calls.append((name, enabled))
        return {"name": name, "enabled": enabled, "changed": True}


async def test_enabled_overlay_delete_reconciles_to_factory(overlay_env):
    fake = _FakeGatewayServer()
    ca.set_gateway_server(fake)
    # alpha 出厂 enabled=true; 覆盖为 false 后删除, 应收敛回 true
    await overlay_env.upsert_overlay("upstreams.alpha.enabled", False)
    await ca._refresh_overlay_snapshot()

    result = await ca.delete_config_overlay("upstreams.alpha.enabled")
    assert fake.calls == [("alpha", True)]
    assert result["warnings"] == []


async def test_non_enabled_overlay_delete_does_not_touch_runtime(overlay_env):
    fake = _FakeGatewayServer()
    ca.set_gateway_server(fake)
    await overlay_env.upsert_overlay("fuyao_cache.tools.t_base", 60)
    await ca._refresh_overlay_snapshot()

    await ca.delete_config_overlay("fuyao_cache.tools.t_base")
    assert fake.calls == []


async def test_reset_reconciles_all_enabled_paths(overlay_env):
    fake = _FakeGatewayServer()
    ca.set_gateway_server(fake)
    await overlay_env.upsert_overlay("upstreams.alpha.enabled", False)
    await ca._refresh_overlay_snapshot()

    result = await ca.reset_config_overlays()
    assert fake.calls == [("alpha", True)]
    assert result["warnings"] == []


async def test_reconcile_without_server_records_warning(overlay_env):
    await overlay_env.upsert_overlay("upstreams.alpha.enabled", False)
    await ca._refresh_overlay_snapshot()
    ca.set_gateway_server(None)

    result = await ca.delete_config_overlay("upstreams.alpha.enabled")
    assert len(result["warnings"]) == 1
    assert "重启后生效" in result["warnings"][0]


async def test_reconcile_runtime_failure_does_not_block(overlay_env):
    class _BoomServer:
        async def set_upstream_enabled(self, name: str, enabled: bool) -> dict:
            raise RuntimeError("provider down")

    ca.set_gateway_server(_BoomServer())
    await overlay_env.upsert_overlay("upstreams.alpha.enabled", False)
    await ca._refresh_overlay_snapshot()

    result = await ca.delete_config_overlay("upstreams.alpha.enabled")
    # 行已删, 版本已 bump, 仅在 warnings 里报告
    assert result["version"] == 2
    assert len(result["warnings"]) == 1
    assert "RuntimeError" in result["warnings"][0]
    assert await overlay_env.list_overlays() == []


