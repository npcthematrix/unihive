"""v2 实体浏览: ConfigStore.entities_summary + GET /api/config/entities。

控制台「配置库」面板的数据源 — 只读实体行元数据 (origin/updated_at),
不读 config_json/spec_json 大列; yaml 逃生模式回 source=yaml。
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from starlette.routing import Route

from src.unihive.core.config_db import seed_database
from src.unihive.storage.config_store import ConfigStore
from src.unihive.utils import console_api as ca

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAMS_YAML = REPO_ROOT / "config" / "upstreams.yaml"


class _FakeRequest:
    def __init__(self, path_params: dict | None = None):
        self.path_params = path_params or {}


@pytest.fixture
def seeded_store(tmp_path):
    db_path = tmp_path / "config.db"
    store = ConfigStore(db_path)
    return store, db_path


async def test_entities_summary_after_seed(seeded_store):
    store, _ = seeded_store
    await store.initialize()
    revision = await seed_database(store, UPSTREAMS_YAML)
    assert revision == 0

    summary = await store.entities_summary()
    assert summary["revision"] == 0
    assert isinstance(summary["template_version"], str) and summary["template_version"]
    assert isinstance(summary["template_imported_at"], str)
    assert len(summary["upstreams"]) == 8
    assert {u["name"] for u in summary["upstreams"]} >= {
        "mootdx2",
        "fuyao_ashare",
        "thsdk",
    }
    for upstream in summary["upstreams"]:
        assert set(upstream) == {"name", "type", "enabled", "origin", "updated_at"}
        assert upstream["origin"] == "builtin"
        assert isinstance(upstream["enabled"], bool)
    assert summary["tools"]["total"] == 218
    assert sum(summary["tools"]["by_origin"].values()) == 218
    # 出厂 seed 的段与上游 origin 均为 builtin
    assert summary["sections"], "expected seeded sections (cache/logging/gateway/...)"
    assert all(s["origin"] == "builtin" for s in summary["sections"])
    await store.close()


async def test_entities_summary_reflects_user_writes(seeded_store):
    store, _ = seeded_store
    await store.initialize()
    await seed_database(store, UPSTREAMS_YAML)
    await store.update_upstream_enabled("mootdx2", False)
    await store.upsert_section(
        "fuyao_cache", {"tools": {"get_a_share_history": 60}}, origin="user"
    )

    summary = await store.entities_summary()
    assert summary["revision"] == 2
    mootdx2 = next(u for u in summary["upstreams"] if u["name"] == "mootdx2")
    assert mootdx2["enabled"] is False
    section = next(s for s in summary["sections"] if s["section"] == "fuyao_cache")
    assert section["origin"] == "user"
    await store.close()


async def test_entities_handler_db_mode(seeded_store):
    store, _ = seeded_store
    await store.initialize()
    await seed_database(store, UPSTREAMS_YAML)
    ca.set_config_db(store, 0)
    try:
        resp = await ca.config_entities_api(_FakeRequest())
        assert resp.status_code == 200
        payload = json.loads(resp.body.decode())
        assert payload["source"] == "db"
        assert payload["revision"] == 0
        assert len(payload["upstreams"]) == 8
        assert payload["tools"]["total"] == 218
    finally:
        ca.set_config_db(None)
        await store.close()


async def test_entities_handler_yaml_mode():
    # 未注入 store / 实体模式关闭: 200 + source=yaml (面板只渲染逃生提示)
    ca.set_config_db(None)
    resp = await ca.config_entities_api(_FakeRequest())
    assert resp.status_code == 200
    assert json.loads(resp.body.decode()) == {"source": "yaml"}


def test_entities_route_mounted():
    routes = ca.build_upstream_routes()
    by_path = {
        r.path: (r.methods or set())
        for r in routes
        if isinstance(r, Route)
    }
    assert "GET" in by_path["/api/config/entities"]
    # P1 overlay 路由已随 UI 退役, 不得再挂
    assert not any(p.startswith("/api/config/overlays") for p in by_path)

    from src.unihive.gateway_server import GatewayServer

    assert "build_upstream_routes()" in inspect.getsource(GatewayServer.serve_http)
