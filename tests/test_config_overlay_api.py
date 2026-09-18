"""config.db 覆盖层管理 API (2026-09-18 P1): list / 单行删除 / 一键恢复出厂。

- 服务函数: list_config_overlays / delete_config_overlay / reset_config_overlays;
- enabled 覆盖行删除后, 运行时按 YAML 出厂值热收敛 (_reconcile_enabled_overlays);
- HTTP handler: GET /api/config/overlays, DELETE /api/config/overlays/{path},
  POST /api/config/overlays/reset, 路由顺序静态 reset 在 {path} 之前。
"""
from __future__ import annotations

import inspect

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
    assert ca.get_fuyao_cache()["tools"] == {"t_base": 30}


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


# ---------------------------------------------------------------------------
# HTTP handlers
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, path_params: dict | None = None, body: object = None):
        self.path_params = path_params or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


async def test_list_handler_200_and_503(overlay_env):
    resp = await ca.config_overlays_list_api(_FakeRequest())
    assert resp.status_code == 200
    payload = resp.body.decode()
    assert '"revision":0' in payload.replace(" ", "")

    ca.set_config_store(None)
    resp = await ca.config_overlays_list_api(_FakeRequest())
    assert resp.status_code == 503


async def test_delete_handler_200(overlay_env):
    await overlay_env.upsert_overlay("fuyao_cache.tools.t_base", 60)
    await ca._refresh_overlay_snapshot()
    req = _FakeRequest(
        {"path": "fuyao_cache.tools.t_base"}, {"version": 1}
    )
    resp = await ca.config_overlay_reset_api(req)
    assert resp.status_code == 200
    assert await overlay_env.list_overlays() == []


async def test_delete_handler_409_conflict(overlay_env):
    await overlay_env.upsert_overlay("fuyao_cache.tools.t_base", 60)
    await ca._refresh_overlay_snapshot()
    req = _FakeRequest(
        {"path": "fuyao_cache.tools.t_base"}, {"version": 0}
    )
    resp = await ca.config_overlay_reset_api(req)
    assert resp.status_code == 409


async def test_delete_handler_400_bad_path(overlay_env):
    req = _FakeRequest({"path": "a/../b"}, {})
    resp = await ca.config_overlay_reset_api(req)
    assert resp.status_code == 400


async def test_reset_all_handler_200(overlay_env):
    await overlay_env.upsert_overlay("upstreams.alpha.enabled", False)
    await ca._refresh_overlay_snapshot()
    req = _FakeRequest(body={"version": 1})
    resp = await ca.config_overlays_reset_all_api(req)
    assert resp.status_code == 200
    assert await overlay_env.list_overlays() == []


async def test_reset_all_handler_no_body_ok(overlay_env):
    req = _FakeRequest(body=None)  # 无 body 时跳过乐观锁
    resp = await ca.config_overlays_reset_all_api(req)
    assert resp.status_code == 200


def test_routes_mounted_and_reset_before_param_route():
    """reset 静态路由必须在 {path} 参数路由之前, 否则 reset 会当成覆盖路径。"""
    from starlette.routing import Route

    routes = ca.build_upstream_routes()
    by_path: dict[str, set[str]] = {}
    for r in routes:
        if isinstance(r, Route):
            by_path.setdefault(r.path, set()).update(r.methods or ())
    assert "GET" in by_path["/api/config/overlays"]
    assert "POST" in by_path["/api/config/overlays/reset"]
    assert "DELETE" in by_path["/api/config/overlays/{path}"]
    ordered = [r.path for r in routes]
    assert ordered.index("/api/config/overlays/reset") < ordered.index(
        "/api/config/overlays/{path}"
    )

    # gateway_server 必须挂载本路由组
    from src.unihive.gateway_server import GatewayServer

    source = inspect.getsource(GatewayServer.serve_http)
    assert "build_upstream_routes()" in source
