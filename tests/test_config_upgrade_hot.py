"""P3-6: 出厂更新热应用 — preview / apply / 发布差集 / client detach /
provider 失败回滚 / 乐观锁冲突回滚 / lint+冲突前置门。

绕过 GatewayServer.__init__ (同 test_tool_edit_hot 手法), plan_config_upgrade
打桩为真实 merge_bundles + 合成"新出厂 bundle", 不碰仓库 YAML。
"""
from __future__ import annotations

import asyncio
import copy

import pytest

from src.unihive.core import config_db
from src.unihive.core.config_lint import LintRejected
from src.unihive.core.config_upgrade import merge_bundles
from src.unihive.core.router import Router
from src.unihive.core.tool_loader import filter_specs_by_env
from src.unihive.core.db_assembler import assemble_config, entities_from_json
from src.unihive.gateway_server import GatewayServer
from src.unihive.storage.config_store import ConfigStore, RevisionConflict

from tests.test_upstream_enabled_toggle import _FakeMCP
from tests.test_config_store_v2 import BUNDLE, VERSION


@pytest.fixture(autouse=True)
async def _isolate_console_api_globals():
    # apply_config_upgrade 成功路径会调 _ca.set_config_db/set_config 注入全局;
    # 本文件用的是临时库, 测后必须复位, 否则跨文件污染 /api/interfaces 读路径。
    import src.unihive.utils.console_api as ca_mod

    saved = {
        "_config_store": ca_mod._config_store,
        "_db_mode": ca_mod._db_mode,
        "_db_revision": ca_mod._db_revision,
        "_overlay_rows": ca_mod._overlay_rows,
        "_overlay_revision": ca_mod._overlay_revision,
        "_GW_CFG": ca_mod._GW_CFG,
        "_INJECTED": ca_mod._INJECTED,
    }
    yield
    ca_mod._config_store = saved["_config_store"]
    ca_mod._db_mode = saved["_db_mode"]
    ca_mod._db_revision = saved["_db_revision"]
    ca_mod._overlay_rows = saved["_overlay_rows"]
    ca_mod._overlay_revision = saved["_overlay_revision"]
    ca_mod._GW_CFG = saved["_GW_CFG"]
    ca_mod._INJECTED = saved["_INJECTED"]
    ca_mod._interfaces_cache.clear()
    ca_mod._mcp_tools_cache.clear()


@pytest.fixture
async def store(tmp_path):
    s = ConfigStore(tmp_path / "config.db")
    await s.initialize()
    await s.seed_from_bundle(VERSION, BUNDLE)
    yield s
    await s.close()


def _tool(bundle, name):
    return next(t for t in bundle["tools"] if t["name"] == name)


def _factory_with_cthree():
    new = copy.deepcopy(BUNDLE)
    new["tools"].append(
        {
            "name": "c_three",
            "routing": "c_three",
            "description": "third",
            "cache_ttl_key": "historical",
            "requires_env": None,
            "publish_mode": "default",
            "mapping": {"alpha": "c_three"},
            "spec": {
                "name": "c_three",
                "routing": "c_three",
                "description": "third",
                "cache_ttl_key": "historical",
                "params": [],
            },
            "source_file": "tools_alpha.yaml",
            "origin": "builtin",
        }
    )
    return new


def _factory_v2():
    new = copy.deepcopy(BUNDLE)
    _tool(new, "a_one")["description"] = "factory v2"
    _tool(new, "a_one")["spec"]["description"] = "factory v2"
    return new


def _factory_alpha_disabled():
    new = copy.deepcopy(BUNDLE)
    next(u for u in new["upstreams"] if u["name"] == "alpha")["enabled"] = False
    return new


def _patch_plan(monkeypatch, new_version, new_bundle):
    def _fake_plan(old_bundle, cur_bundle, *, config_path, resolutions=None):
        result = merge_bundles(
            old_bundle, cur_bundle, new_bundle, resolutions=resolutions
        )
        return new_version, new_bundle, result

    monkeypatch.setattr(config_db, "plan_config_upgrade", _fake_plan)


async def _make_server(store):
    import src.unihive.gateway_server as gs_mod

    server = GatewayServer.__new__(GatewayServer)
    server._db_mode = True
    server.config_store = store
    server._strict_env = False

    bundle = await store.load_bundle()
    template = entities_from_json(bundle)
    config = assemble_config(template)
    server.config = config
    server.tool_specs = filter_specs_by_env(list(config["tools"]))
    server._db_hidden_tools = {
        t.name for t in template.tools if t.publish_mode == "hidden"
    }
    server.mcp = _FakeMCP()
    # alpha 启用且连接块不变: 预置已挂 client, apply 差集循环应零动作
    # (否则会真的构造 OmniClient, 走 "配了但连不上" 的建连降级路径)。
    server.upstreams = {"alpha": object()}
    server.router = Router(
        upstreams={},
        routing_config=config.get("routing", {}),
        upstream_tool_mapping=config.get("upstream_tool_mapping", {}),
        tool_specs=server.tool_specs,
    )
    server._upstream_toggle_lock = asyncio.Lock()

    publishable = {"a_one"}
    for spec in server.tool_specs:
        if spec["name"] in publishable:
            gs_mod.register_tool(server, spec)
    server.mcp._tools = set(publishable)
    server._published_tools = set(publishable)
    server.mcp.tool_calls.registered.clear()
    server.mcp.removed.clear()
    return server


# --- preview ---------------------------------------------------------------


async def test_preview_noop_when_factory_unchanged(store, monkeypatch):
    _patch_plan(monkeypatch, VERSION, copy.deepcopy(BUNDLE))
    server = await _make_server(store)

    preview = await server.preview_config_upgrade()
    assert preview["changed"] is False
    assert preview["template_changed"] is False
    assert preview["summary"] == {
        "added": 0,
        "removed": 0,
        "updated": 0,
        "kept_user": 0,
        "conflicts": 0,
    }
    assert preview["impact"]["tools_added"] == []
    assert preview["impact"]["tools_removed"] == []
    assert preview["impact"]["tools_republished"] == []
    assert preview["impact"]["upstreams_attach"] == []
    assert preview["lint_errors"] == []
    # 零写入
    assert await store.revision() == 0


async def test_preview_new_tool_shows_add_impact(store, monkeypatch):
    new_bundle = _factory_with_cthree()
    _patch_plan(monkeypatch, "b" * 64, new_bundle)
    server = await _make_server(store)

    preview = await server.preview_config_upgrade()
    assert preview["changed"] is True
    assert preview["template_changed"] is True
    assert preview["impact"]["tools_added"] == ["c_three"]
    assert preview["impact"]["tools_removed"] == []
    assert preview["impact"]["tools_republished"] == []
    # dry-run 不发布、不写库
    assert "c_three" not in server._published_tools
    assert await store.get_tool("c_three") is None


async def test_preview_spec_change_shows_republish_impact(store, monkeypatch):
    _patch_plan(monkeypatch, "c" * 64, _factory_v2())
    server = await _make_server(store)

    preview = await server.preview_config_upgrade()
    assert preview["changed"] is True
    impact = preview["impact"]
    assert impact["tools_republished"] == ["a_one"]
    assert impact["tools_added"] == []
    assert impact["tools_removed"] == []
    assert "a_one" in server._published_tools  # dry-run 不动 provider


async def test_preview_requires_store_and_initialized_server(store, monkeypatch):
    _patch_plan(monkeypatch, VERSION, copy.deepcopy(BUNDLE))
    server = await _make_server(store)
    server.config_store = None
    with pytest.raises(RuntimeError, match="config.db"):
        await server.preview_config_upgrade()

    server = await _make_server(store)
    server.tool_specs = []
    with pytest.raises(RuntimeError, match="not initialized"):
        await server.preview_config_upgrade()


# --- apply: 发布差集 + 落库 + 快照前推 + 备份 -------------------------------


async def test_apply_adds_tool_publishes_advances_snapshot_and_backs_up(
    store, monkeypatch
):
    new_bundle = _factory_with_cthree()
    _patch_plan(monkeypatch, "d" * 64, new_bundle)
    server = await _make_server(store)

    result = await server.apply_config_upgrade()
    assert result["changed"] is True
    assert result["revision"] == 1
    assert result["tools_added"] == ["c_three"]
    assert result["tools_removed"] == []
    assert result["tools_republished"] == []
    assert result["upstreams_attached"] == []
    assert result["warnings"] == []

    # provider + 运行态
    assert "c_three" in server._published_tools
    assert {n for n, _ in server.mcp.tool_calls.registered} == {"c_three"}
    assert any(s["name"] == "c_three" for s in server.tool_specs)

    # 库: 实体 + 快照前推
    assert await store.revision() == 1
    cthree = await store.get_tool("c_three")
    assert cthree is not None and cthree["origin"] == "builtin"
    snap_version, snap_bundle = await store.template_snapshot()
    assert snap_version == "d" * 64
    assert snap_bundle == new_bundle

    # 备份落盘且内容是升级前 cur
    import json
    from pathlib import Path

    backups = list((Path(store.db_path).parent / "backups").glob("config-upgrade-*.json"))
    assert len(backups) == 1
    payload = json.loads(backups[0].read_text(encoding="utf-8"))
    assert payload["revision_before"] == 0
    assert [t["name"] for t in payload["bundle"]["tools"]] == ["a_one", "b_two"]


async def test_apply_republishes_changed_tool_and_flows_description(
    store, monkeypatch
):
    _patch_plan(monkeypatch, "e" * 64, _factory_v2())
    server = await _make_server(store)

    result = await server.apply_config_upgrade()
    assert result["tools_republished"] == ["a_one"]
    assert result["tools_added"] == []
    assert result["tools_removed"] == []
    assert server.mcp.removed == ["a_one"]
    assert {n for n, _ in server.mcp.tool_calls.registered} == {"a_one"}
    assert "a_one" in server._published_tools
    tool = await store.get_tool("a_one")
    assert tool["description"] == "factory v2"
    assert tool["origin"] == "builtin"
    live = next(s for s in server.config["tools"] if s["name"] == "a_one")
    assert live["description"] == "factory v2"


async def test_apply_disabled_upstream_detaches_and_unpublishes(
    store, monkeypatch
):
    _patch_plan(monkeypatch, "f" * 64, _factory_alpha_disabled())
    server = await _make_server(store)

    detached: list[str] = []

    async def _fake_detach(name):
        server.upstreams.pop(name, None)
        detached.append(name)

    server.upstreams["alpha"] = object()
    server._detach_upstream_client_locked = _fake_detach

    result = await server.apply_config_upgrade()
    assert detached == ["alpha"]
    assert result["upstreams_detached"] == ["alpha"]
    assert result["tools_removed"] == ["a_one"]
    assert server.mcp.removed == ["a_one"]
    assert "a_one" not in server._published_tools
    assert server.config["upstreams"]["alpha"]["enabled"] is False
    alpha = await store.get_upstream("alpha")
    assert alpha["enabled"] is False


async def test_apply_unpublishes_tools_before_detaching_clients(store, monkeypatch):
    """M8: 禁用上游时, 其工具必须先从 provider 摘除, 再 detach client ——
    不允许"client 已断但工具仍发布"的窗口 (新调用落到正在关闭的 client)。"""
    _patch_plan(monkeypatch, "7" * 64, _factory_alpha_disabled())
    server = await _make_server(store)

    events: list[tuple[str, str]] = []
    real_unpublish = server._unpublish_tools_locked

    def unpublish(names, errors):
        got = real_unpublish(names, errors)
        events.extend(("unpublish", n) for n in got)
        return got

    server._unpublish_tools_locked = unpublish

    async def fake_detach(name):
        # detach 发生时该上游的工具必须已不在发布集
        assert "a_one" not in server._published_tools
        events.append(("detach", name))
        server.upstreams.pop(name, None)

    server._detach_upstream_client_locked = fake_detach

    await server.apply_config_upgrade()

    assert events == [("unpublish", "a_one"), ("detach", "alpha")]


def _factory_alpha_reconnect():
    """alpha 连接块变化 (→reconnect) + a_one spec 变化 (→republish)。"""
    new = copy.deepcopy(BUNDLE)
    next(u for u in new["upstreams"] if u["name"] == "alpha")["config"] = {
        "description": "changed"
    }
    _tool(new, "a_one")["description"] = "factory v2"
    _tool(new, "a_one")["spec"]["description"] = "factory v2"
    return new


async def test_apply_reconnect_order_unpublish_detach_attach_publish(
    store, monkeypatch
):
    """M8: reconnect 上游的完整顺序必须是 摘旧工具 → detach 旧 client →
    attach 新 client → 发新工具 (旧实现先 churn client 再动工具)。"""
    _patch_plan(monkeypatch, "8" * 64, _factory_alpha_reconnect())
    server = await _make_server(store)

    events: list[tuple[str, str]] = []
    real_unpublish = server._unpublish_tools_locked
    real_publish = server._publish_tools_locked

    def unpublish(names, errors):
        got = real_unpublish(names, errors)
        events.extend(("unpublish", n) for n in got)
        return got

    def publish(names, errors):
        got = real_publish(names, errors)
        events.extend(("publish", n) for n in got)
        return got

    server._unpublish_tools_locked = unpublish
    server._publish_tools_locked = publish

    async def fake_detach(name):
        events.append(("detach", name))
        server.upstreams.pop(name, None)

    async def fake_attach(name, cfg):
        events.append(("attach", name))
        client = object()
        server.upstreams[name] = client
        return client, True

    server._detach_upstream_client_locked = fake_detach
    server._attach_upstream_client_locked = fake_attach

    result = await server.apply_config_upgrade()

    assert result["upstreams_reconnected"] == ["alpha"]
    assert result["tools_republished"] == ["a_one"]
    assert events == [
        ("unpublish", "a_one"),
        ("detach", "alpha"),
        ("attach", "alpha"),
        ("publish", "a_one"),
    ]


# --- apply 前置门 -----------------------------------------------------------


async def test_apply_unresolved_conflict_blocks_everything(store, monkeypatch):
    await store.update_tool("a_one", {"description": "user desc"})
    _patch_plan(monkeypatch, "1" * 64, _factory_v2())
    server = await _make_server(store)

    with pytest.raises(ValueError, match="unresolved field conflict"):
        await server.apply_config_upgrade()

    assert await store.revision() == 1
    assert (await store.get_tool("a_one"))["description"] == "user desc"
    assert server.mcp.removed == []
    assert server.mcp.tool_calls.registered == []
    from pathlib import Path

    assert not (Path(store.db_path).parent / "backups").exists()


async def test_apply_accept_new_resolution_hot_republishes(store, monkeypatch):
    await store.update_tool("a_one", {"description": "user desc"})
    _patch_plan(monkeypatch, "2" * 64, _factory_v2())
    server = await _make_server(store)

    result = await server.apply_config_upgrade(
        resolutions={
            "tool:a_one:description": "accept_new",
            "tool:a_one:spec.description": "accept_new",
        }
    )
    assert result["changed"] is True
    assert result["tools_republished"] == ["a_one"]
    assert result["resolutions_applied"] == [
        "tool:a_one:description",
        "tool:a_one:spec.description",
    ]
    tool = await store.get_tool("a_one")
    assert tool["description"] == "factory v2"
    assert tool["origin"] == "builtin"


async def test_apply_invalid_resolution_shape_rejected(store, monkeypatch):
    _patch_plan(monkeypatch, VERSION, copy.deepcopy(BUNDLE))
    server = await _make_server(store)
    with pytest.raises(ValueError):
        await server.preview_config_upgrade({"tool:a_one:description": "accept_old"})


async def test_apply_lint_error_blocks_before_backup(store, monkeypatch, tmp_path):
    bad = copy.deepcopy(BUNDLE)
    bad["tools"].append(
        {
            "name": "ghost_tool",
            "routing": "ghost_tool",
            "description": "",
            "cache_ttl_key": None,
            "requires_env": None,
            "publish_mode": "default",
            "mapping": {"ghost_upstream": "x"},
            "spec": {"name": "ghost_tool", "routing": "ghost_tool"},
            "source_file": "tools_x.yaml",
            "origin": "builtin",
        }
    )
    _patch_plan(monkeypatch, "3" * 64, bad)
    server = await _make_server(store)

    with pytest.raises(LintRejected):
        await server.apply_config_upgrade()
    assert await store.revision() == 0
    assert await store.get_tool("ghost_tool") is None
    assert server.mcp.removed == []
    from pathlib import Path

    assert not (Path(store.db_path).parent / "backups").exists()


async def test_apply_noop_returns_without_side_effects(store, monkeypatch):
    _patch_plan(monkeypatch, VERSION, copy.deepcopy(BUNDLE))
    server = await _make_server(store)
    result = await server.apply_config_upgrade()
    assert result == {
        "changed": False,
        "revision": 0,
        "old_version": VERSION,
        "new_version": VERSION,
        "summary": {
            "added": 0,
            "removed": 0,
            "updated": 0,
            "kept_user": 0,
            "conflicts": 0,
        },
    }
    assert server.mcp.removed == []
    assert await store.revision() == 0


# --- apply 失败回滚 ---------------------------------------------------------


async def test_apply_publish_failure_rolls_runtime_back_db_untouched(
    store, monkeypatch
):
    import src.unihive.gateway_server as gs_mod

    _patch_plan(monkeypatch, "4" * 64, _factory_with_cthree())
    server = await _make_server(store)

    def boom(srv, spec):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("src.unihive.core.registry.register_tool", boom)
    with pytest.raises(RuntimeError, match="factory-upgrade"):
        await server.apply_config_upgrade()

    assert await store.revision() == 0
    assert await store.get_tool("c_three") is None
    assert "c_three" not in server._published_tools
    assert "a_one" in server._published_tools
    # 运行态引用复位: candidate 里的 c_three 不在 live specs
    assert all(s["name"] != "c_three" for s in server.tool_specs)
    snap_version, _ = await store.template_snapshot()
    assert snap_version == VERSION


async def test_apply_db_revision_conflict_rolls_runtime_back(store, monkeypatch):
    _patch_plan(monkeypatch, "5" * 64, _factory_with_cthree())
    server = await _make_server(store)

    with pytest.raises(RevisionConflict):
        await server.apply_config_upgrade(expected_revision=99)

    assert await store.revision() == 0
    assert await store.get_tool("c_three") is None
    assert "c_three" not in server._published_tools
    assert "a_one" in server._published_tools
    assert all(s["name"] != "c_three" for s in server.tool_specs)
    # c_three 的注册确实发生过, 回滚后从发布真值集摘除
    # (fake decorator 不维护 _tools 集, provider 侧 remove 走 KeyError 幂等)
    assert {n for n, _ in server.mcp.tool_calls.registered} == {"c_three"}


async def test_apply_pins_plan_revision_when_expected_omitted(store, monkeypatch):
    """H5: 省略 expected_revision 时以 plan 时刻 revision 为乐观锁基准;
    plan→apply 之间完成的并发写入让全表重写撞 RevisionConflict,
    升级整笔回滚, 并发改动保留, 运行态复原。"""
    _patch_plan(monkeypatch, "6" * 64, _factory_with_cthree())
    server = await _make_server(store)

    real_prepare = server._prepare_config_upgrade

    async def prepare_then_concurrent_write(resolutions):
        plan = await real_prepare(resolutions)
        # 模拟另一个请求在 plan 之后、落库之前完成了一笔写
        await store.upsert_overlay("concurrent.write", True)
        return plan

    monkeypatch.setattr(
        server, "_prepare_config_upgrade", prepare_then_concurrent_write
    )

    with pytest.raises(RevisionConflict):
        await server.apply_config_upgrade()  # 刻意不传 expected_revision

    # 只有并发写落库 (rev 0→1); 升级事务回滚, c_three 未入实体表
    assert await store.revision() == 1
    assert (await store.get_overlay("concurrent.write")).value is True
    assert await store.get_tool("c_three") is None
    # 运行态回滚: c_three 发布被撤销, a_one 保留, spec 列表复原
    assert "c_three" not in server._published_tools
    assert "a_one" in server._published_tools
    assert all(s["name"] != "c_three" for s in server.tool_specs)


# --- 上游连接差集分类 (纯单元) ----------------------------------------------


async def test_upstream_impact_classification(store, monkeypatch):
    _patch_plan(monkeypatch, VERSION, copy.deepcopy(BUNDLE))
    server = await _make_server(store)
    sentinel = object()
    server.upstreams = {"alpha": sentinel, "beta": sentinel}

    candidate = copy.deepcopy(server.config)
    # alpha 连接块变了 (仍启用, 已挂) → reconnect
    candidate["upstreams"]["alpha"]["description"] = "changed"
    # beta 禁用 → detach
    candidate["upstreams"]["beta"]["enabled"] = False
    # 新增启用上游 gamma → attach
    candidate["upstreams"]["gamma"] = {
        "base_url": "http://g",
        "enabled": True,
        "type": "http",
    }

    attach, reconnect, detach = server._upgrade_upstream_impact(candidate)
    assert attach == ["gamma"]
    assert reconnect == ["alpha"]
    assert detach == ["beta"]
