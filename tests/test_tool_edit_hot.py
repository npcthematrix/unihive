"""P3-2: 单工具热编辑 — 契约确认门 / lint 门 / 热重注册失败回滚 / hidden。

绕过 GatewayServer.__init__, 用真实 tmp config.db + _FakeMCP (同热开关测试手法)。
"""
from __future__ import annotations

import asyncio

import pytest

from src.unihive.core.config_lint import LintRejected
from src.unihive.core.db_assembler import assemble_config, entities_from_json
from src.unihive.core.entity_edit import DangerousConfirmationRequired
from src.unihive.core.router import Router
from src.unihive.core.tool_loader import filter_specs_by_env
from src.unihive.gateway_server import RESERVED_TOOL_NAMES, GatewayServer
from src.unihive.storage.config_store import ConfigStore, RevisionConflict

from tests.test_upstream_enabled_toggle import _FakeMCP
from tests.test_config_store_v2 import BUNDLE, VERSION


@pytest.fixture
async def seeded_store(tmp_path):
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    await store.seed_from_bundle(VERSION, BUNDLE)
    yield store
    await store.close()


async def _make_edit_server(store):
    """从已 seed 库装配最小可用 server (状态与真实 _load_db_config 同构)。"""
    import src.unihive.gateway_server as gs_mod

    server = GatewayServer.__new__(GatewayServer)
    server._db_mode = True
    server.config_store = store

    bundle = await store.load_bundle()
    template = entities_from_json(bundle)
    config = assemble_config(template)
    server.config = config
    server.tool_specs = filter_specs_by_env(list(config["tools"]))
    server._db_hidden_tools = {
        t.name for t in template.tools if t.publish_mode == "hidden"
    }

    server.mcp = _FakeMCP()
    server.upstreams = {}
    server.router = Router(
        upstreams={},
        routing_config=config.get("routing", {}),
        upstream_tool_mapping=config.get("upstream_tool_mapping", {}),
        tool_specs=server.tool_specs,
    )
    server._upstream_toggle_lock = asyncio.Lock()

    # a_one 背 alpha (omni, 启用) → 发布; b_two 背 beta (http, 禁用) → 未发布
    publishable = {s["name"] for s in server.tool_specs if s["name"] == "a_one"}
    for spec in server.tool_specs:
        if spec["name"] in publishable:
            gs_mod.register_tool(server, spec)
    server.mcp._tools = set(publishable)
    server._published_tools = set(publishable)
    # provider 状态 (上面的预发布) 保留, 但探针只统计编辑期间的增删,
    # 否则 setup 注册的 a_one 会污染 registered 断言。
    server.mcp.tool_calls.registered.clear()
    server.mcp.removed.clear()
    return server


# --- 确认门 ---------------------------------------------------------------

async def test_contract_patch_requires_confirmation(seeded_store):
    server = await _make_edit_server(seeded_store)
    with pytest.raises(DangerousConfirmationRequired) as exc:
        await server.apply_tool_edit(
            "a_one", {"mapping": {"beta": "other"}}
        )
    assert exc.value.contract_fields == ["mapping"]
    # 运行态与库都未动
    assert await seeded_store.revision() == 0
    assert server.mcp.removed == []


async def test_description_layer_saves_hot_without_confirmation(seeded_store):
    server = await _make_edit_server(seeded_store)
    result = await server.apply_tool_edit("a_one", {"description": "新描述"})

    assert result["changed"] is True
    assert result["revision"] == 1
    assert result["published"] is True
    assert result["contract_fields"] == []
    tool = await seeded_store.get_tool("a_one")
    assert tool["origin"] == "user"
    assert tool["spec"]["description"] == "新描述"
    # /mcp 已重注册 (一次 remove + 一次 add), 名字不变
    assert server.mcp.removed == ["a_one"]
    assert {n for n, _ in server.mcp.tool_calls.registered} == {"a_one"}
    # live config / router 元数据同步
    live = next(s for s in server.config["tools"] if s["name"] == "a_one")
    assert live["description"] == "新描述"
    assert server.router._tool_metadata["a_one"]["description"] == "新描述"


async def test_noop_patch_changes_nothing(seeded_store):
    server = await _make_edit_server(seeded_store)
    result = await server.apply_tool_edit("a_one", {"description": "first"})
    assert result == {
        "changed": False,
        "name": "a_one",
        "revision": 0,
        "published": True,
        "previously_published": True,
    }
    assert server.mcp.removed == []
    assert (await seeded_store.get_tool("a_one"))["origin"] == "builtin"


# --- lint 门 --------------------------------------------------------------

async def test_unknown_upstream_mapping_rejected_before_runtime_and_db(seeded_store):
    server = await _make_edit_server(seeded_store)
    with pytest.raises(LintRejected) as exc:
        await server.apply_tool_edit(
            "a_one",
            {"mapping": {"ghost_upstream": "x"}},
            confirm_dangerous=True,
        )
    codes = {i.code for i in exc.value.issues}
    assert "M001" in codes
    assert server.mcp.removed == []
    assert await seeded_store.revision() == 0


async def test_rename_into_reserved_tool_rejected(seeded_store):
    server = await _make_edit_server(seeded_store)
    for reserved in RESERVED_TOOL_NAMES:
        with pytest.raises(ValueError):
            await server.apply_tool_edit(
                "a_one", {"name": reserved}, confirm_dangerous=True
            )


# --- 热重注册 + 回滚 -------------------------------------------------------

async def test_rename_reregisters_and_rewires_live_state(seeded_store):
    server = await _make_edit_server(seeded_store)
    result = await server.apply_tool_edit(
        "a_one", {"name": "a_one_v2", "routing": "a_one_v2"},
        confirm_dangerous=True,
    )
    assert result["name"] == "a_one_v2"
    assert result["revision"] == 1
    assert server.mcp.removed == ["a_one"]
    assert {n for n, _ in server.mcp.tool_calls.registered} == {"a_one_v2"}
    assert "a_one" not in server._published_tools
    assert "a_one_v2" in server._published_tools
    names = [s["name"] for s in server.config["tools"]]
    assert names == ["a_one_v2", "b_two"]  # position 保留
    # 旧 routing key 的顶层映射随实体迁移
    assert "a_one" not in server.config["upstream_tool_mapping"]
    assert server.config["upstream_tool_mapping"]["a_one_v2"] == {"alpha": "a_one"}
    assert "a_one" not in server.router._tool_metadata
    assert server.router._tool_metadata["a_one_v2"]["routing"] == "a_one_v2"
    assert await seeded_store.get_tool("a_one") is None


async def test_register_failure_rolls_runtime_back_and_never_writes_db(
    seeded_store, monkeypatch
):
    import src.unihive.gateway_server as gs_mod

    server = await _make_edit_server(seeded_store)

    real_register = gs_mod.register_tool
    calls = {"n": 0}

    def boom_then_restore(srv, spec):
        calls["n"] += 1
        # 候选注册 (第 1 次) 失败; 回滚恢复旧工具 (第 2 次) 必须成功
        if calls["n"] == 1:
            raise RuntimeError("provider exploded")
        return real_register(srv, spec)

    monkeypatch.setattr("src.unihive.core.registry.register_tool", boom_then_restore)
    with pytest.raises(RuntimeError, match="re-register failed"):
        await server.apply_tool_edit("a_one", {"description": "x"})

    assert await seeded_store.revision() == 0
    assert (await seeded_store.get_tool("a_one"))["description"] == "first"
    # 旧工具在 provider 侧恢复发布
    assert "a_one" in server._published_tools


async def test_db_conflict_after_swap_restores_runtime(seeded_store):
    server = await _make_edit_server(seeded_store)
    # 运行时切换成功, 但提交期乐观锁冲突 → 运行态回滚, 库不动
    with pytest.raises(RevisionConflict):
        await server.apply_tool_edit(
            "a_one",
            {"description": "x"},
            confirm_dangerous=False,
            expected_revision=99,
        )
    # 注意: expected_revision 在 store 写阶段才检查; 此时旧工具应已恢复
    assert "a_one" in server._published_tools
    assert await seeded_store.revision() == 0


async def test_omitted_expected_pins_plan_revision(seeded_store, monkeypatch):
    """H5: 省略 expected_revision 时, apply 以 read_bundle 返回的 plan
    revision 为基准; plan→落库之间的并发写入必须让编辑撞冲突并回滚。"""
    server = await _make_edit_server(seeded_store)
    real_read = seeded_store.read_bundle

    async def read_then_concurrent_write():
        bundle, rev = await real_read()
        # 模拟另一个请求在 plan 之后、锁内落库之前完成了一笔写
        await seeded_store.upsert_overlay("concurrent.write", True)
        return bundle, rev

    monkeypatch.setattr(seeded_store, "read_bundle", read_then_concurrent_write)

    with pytest.raises(RevisionConflict):
        await server.apply_tool_edit("a_one", {"description": "x"})

    # 只有并发写落库 (0→1); 工具描述未被旧候选覆盖, 运行态保留发布
    assert await seeded_store.revision() == 1
    assert (await seeded_store.get_tool("a_one"))["description"] == "first"
    assert "a_one" in server._published_tools


# --- hidden / env 门控 -----------------------------------------------------

async def test_hidden_publish_mode_unpublishes_tool(seeded_store):
    server = await _make_edit_server(seeded_store)
    result = await server.apply_tool_edit("a_one", {"publish_mode": "hidden"})
    assert result["published"] is False
    assert server.mcp.removed == ["a_one"]
    assert server.mcp.tool_calls.registered == []
    assert "a_one" in server._db_hidden_tools
    assert "a_one" not in server._published_tools
    # spec 行仍在 (可恢复), 仅发布集摘除
    assert (await seeded_store.get_tool("a_one"))["publish_mode"] == "hidden"


async def test_requires_env_gating_unpublishes_when_env_unset(
    seeded_store, monkeypatch
):
    server = await _make_edit_server(seeded_store)
    monkeypatch.delenv("ALLOW_TOOL_EDIT_X", raising=False)
    result = await server.apply_tool_edit(
        "a_one", {"requires_env": "ALLOW_TOOL_EDIT_X"},
        confirm_dangerous=True,
    )
    assert result["published"] is False
    assert "a_one" not in server._published_tools
    assert all(s["name"] != "a_one" for s in server.tool_specs)


async def test_edit_unpublished_tool_skips_registration(seeded_store):
    server = await _make_edit_server(seeded_store)
    # b_two 背禁用上游 beta, 从未发布
    result = await server.apply_tool_edit("b_two", {"description": "改未发布工具"})
    assert result["published"] is False
    assert result["previously_published"] is False
    assert server.mcp.removed == []
    assert server.mcp.tool_calls.registered == []
    tool = await seeded_store.get_tool("b_two")
    assert tool["spec"]["description"] == "改未发布工具"


async def test_register_tools_respects_hidden_set_at_startup(seeded_store):
    server = await _make_edit_server(seeded_store)
    server._db_hidden_tools = {"a_one"}
    server._published_tools = set()
    server._register_tools(specs=server.tool_specs)
    assert "a_one" not in server._published_tools
    # 状态工具照常注册, 且不混入 spec 发布集
    assert "get_server_status" not in server._published_tools


async def test_toggle_diff_never_republishes_hidden_tool(seeded_store):
    server = await _make_edit_server(seeded_store)
    server._db_hidden_tools = {"a_one"}
    to_add, _ = server._toggle_tool_diff_locked("alpha")
    assert "a_one" not in to_add


async def test_unknown_tool_and_non_db_mode(seeded_store):
    server = await _make_edit_server(seeded_store)
    with pytest.raises(KeyError):
        await server.apply_tool_edit("ghost", {"description": "x"})

    server.config_store = None
    with pytest.raises(RuntimeError, match="config.db"):
        await server.apply_tool_edit("a_one", {"description": "x"})
