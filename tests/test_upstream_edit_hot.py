"""P3-3: 上游连接块热编辑 — 确认门 / 禁用态直写 / 启用态热重连 / 乐观锁回滚。

2026-09-19 批次2 增补: H2 (${VAR} 仅运行态解析, 库内留占位) /
M7 (lint 门 + 先构造新 client 后停旧 + 构造失败零损伤) /
M2 (落库撞冲突后运行态与旧 client 回滚)。
"""
from __future__ import annotations

import asyncio
import copy

import pytest

from src.unihive.core.config_lint import LintRejected
from src.unihive.core.db_assembler import assemble_config, entities_from_json
from src.unihive.core.entity_edit import DangerousConfirmationRequired
from src.unihive.core.router import Router
from src.unihive.core.tool_loader import filter_specs_by_env
from src.unihive.gateway_server import GatewayServer
from src.unihive.storage.config_store import ConfigStore, RevisionConflict
from src.unihive.utils import console_api as ca
from src.unihive.utils.config_loader import resolve_env_vars

from tests.test_config_store_v2 import BUNDLE, VERSION
from tests.test_tool_edit_hot import _make_edit_server
from tests.test_upstream_enabled_toggle import _FakeClient, _FakeMCP


@pytest.fixture(autouse=True)
async def _isolate_console_api_globals():
    # 成功路径会 _ca.set_config_db() 注入临时库全局, 测后必须复位。
    saved = {
        "_config_store": ca._config_store,
        "_db_mode": ca._db_mode,
        "_db_revision": ca._db_revision,
        "_overlay_rows": ca._overlay_rows,
        "_overlay_revision": ca._overlay_revision,
        "_GW_CFG": ca._GW_CFG,
        "_INJECTED": ca._INJECTED,
    }
    yield
    ca._config_store = saved["_config_store"]
    ca._db_mode = saved["_db_mode"]
    ca._db_revision = saved["_db_revision"]
    ca._overlay_rows = saved["_overlay_rows"]
    ca._overlay_revision = saved["_overlay_revision"]
    ca._GW_CFG = saved["_GW_CFG"]
    ca._INJECTED = saved["_INJECTED"]
    ca._interfaces_cache.clear()
    ca._mcp_tools_cache.clear()


@pytest.fixture
async def seeded_store(tmp_path):
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    await store.seed_from_bundle(VERSION, BUNDLE)
    yield store
    await store.close()


def _enabled_beta_bundle() -> dict:
    """beta 启用 (http + ${BETA_KEY} 占位), b_two 无 env 门控可发布。"""
    bundle = copy.deepcopy(BUNDLE)
    beta = next(u for u in bundle["upstreams"] if u["name"] == "beta")
    beta["enabled"] = True
    beta["config"] = {"base_url": "http://old", "api_key": "${BETA_KEY}"}
    b_two = next(t for t in bundle["tools"] if t["name"] == "b_two")
    b_two["requires_env"] = None
    b_two["spec"] = {"name": "b_two", "routing": "b_two", "params": []}
    return bundle


@pytest.fixture
async def beta_store(tmp_path):
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    await store.seed_from_bundle(VERSION, _enabled_beta_bundle())
    yield store
    await store.close()


async def _make_beta_server(store: ConfigStore) -> GatewayServer:
    """与 _make_edit_server 同构, 但 a_one+b_two 均发布, alpha/beta 都挂旧 client。"""
    import src.unihive.gateway_server as gs_mod

    server = GatewayServer.__new__(GatewayServer)
    server._db_mode = True
    server.config_store = store
    server._strict_env = False

    bundle = await store.load_bundle()
    template = entities_from_json(bundle)
    config = assemble_config(template)
    config, _missing = resolve_env_vars(config, strict=False)
    server.config = config
    server.tool_specs = filter_specs_by_env(list(config["tools"]))
    server._db_hidden_tools = {
        t.name for t in template.tools if t.publish_mode == "hidden"
    }
    server.mcp = _FakeMCP()
    server.upstreams = {"alpha": object(), "beta": _FakeClient("beta")}
    server.router = Router(
        upstreams={},
        routing_config=config.get("routing", {}),
        upstream_tool_mapping=config.get("upstream_tool_mapping", {}),
        tool_specs=server.tool_specs,
    )
    server._upstream_toggle_lock = asyncio.Lock()
    for spec in server.tool_specs:
        gs_mod.register_tool(server, spec)
    names = {s["name"] for s in server.tool_specs}
    server.mcp._tools = set(names)
    server._published_tools = set(names)
    server.mcp.tool_calls.registered.clear()
    server.mcp.removed.clear()
    return server


def _patch_client_lifecycle(server, *, construct=None, start=None, stop=None):
    if construct is not None:
        server._construct_upstream_client = construct
    if start is not None:
        server._start_upstream_client = start
    if stop is not None:
        server._stop_one_upstream = stop


async def test_upstream_edit_requires_confirmation(seeded_store):
    server = await _make_edit_server(seeded_store)
    with pytest.raises(DangerousConfirmationRequired) as exc:
        await server.apply_upstream_edit("beta", {"base_url": "http://y"})
    assert "upstream.config" in exc.value.contract_fields
    assert await seeded_store.revision() == 0


async def test_upstream_edit_unknown_raises_keyerror(seeded_store):
    server = await _make_edit_server(seeded_store)
    with pytest.raises(KeyError):
        await server.apply_upstream_edit(
            "ghost", {"x": 1}, confirm_dangerous=True
        )


async def test_disabled_upstream_edit_persists_without_reconnect(seeded_store):
    server = await _make_edit_server(seeded_store)
    calls = {"detach": [], "attach": []}

    async def fake_detach(name):
        calls["detach"].append(name)

    async def fake_attach(name, cfg):
        calls["attach"].append((name, cfg))
        return object(), True

    server._detach_upstream_client_locked = fake_detach
    server._attach_upstream_client_locked = fake_attach

    result = await server.apply_upstream_edit(
        "beta",
        {"base_url": "http://y", "api_key": "${BETA_KEY}"},
        confirm_dangerous=True,
    )
    assert result["changed"] is True
    assert result["revision"] == 1
    assert result["enabled"] is False
    assert result["connected"] is False
    assert calls == {"detach": [], "attach": []}
    # live config 整块替换, 列键合并
    assert server.config["upstreams"]["beta"] == {
        "base_url": "http://y",
        "api_key": "${BETA_KEY}",
        "enabled": False,
        "type": "http",
    }
    entity = await seeded_store.get_upstream("beta")
    assert entity["origin"] == "user"
    assert entity["config"]["base_url"] == "http://y"


async def test_upstream_edit_noop_keeps_revision_and_skips_reconnect(seeded_store):
    server = await _make_edit_server(seeded_store)
    attached: list = []

    async def fake_attach(name, cfg):
        attached.append(name)
        return object(), True

    server._attach_upstream_client_locked = fake_attach
    result = await server.apply_upstream_edit(
        "beta",
        {"base_url": "http://x", "api_key": "${BETA_KEY}"},
        confirm_dangerous=True,
    )
    assert result == {
        "changed": False,
        "name": "beta",
        "revision": 0,
        "enabled": False,
        "connected": False,
        "contract_fields": ["upstream.config"],
    }
    assert attached == []
    assert (await seeded_store.get_upstream("beta"))["origin"] == "builtin"


async def test_enabled_upstream_edit_starts_new_client_before_stopping_old(
    seeded_store,
):
    """M7: 启用上游重连 = 构造新 client → 启动登记 → 停旧 client (先接后断);
    旧实现先 detach 旧 client 再构造, 构造失败会留下"配置已换但无 client"的窗。"""
    server = await _make_edit_server(seeded_store)
    old_client = _FakeClient("alpha")
    server.upstreams["alpha"] = old_client
    new_client = _FakeClient("alpha")
    events: list[tuple] = []

    def fake_construct(name, cfg):
        events.append(("construct", name, cfg, new_client))
        return new_client

    async def fake_start(name, cfg, client):
        events.append(("start", name, client))
        server.upstreams[name] = client
        return True

    async def fake_stop(name, client):
        events.append(("stop", name, client))
        client.stopped = True

    _patch_client_lifecycle(
        server, construct=fake_construct, start=fake_start, stop=fake_stop
    )

    result = await server.apply_upstream_edit(
        "alpha",
        {"description": "b"},
        confirm_dangerous=True,
    )
    assert result["changed"] is True
    assert result["connected"] is True
    assert result["enabled"] is True
    assert [e[0] for e in events] == ["construct", "start", "stop"]
    assert events[0][2] == {"description": "b", "enabled": True, "type": "omni"}
    assert events[1][2] is new_client
    assert events[2][2] is old_client
    assert server.upstreams["alpha"] is new_client
    assert old_client.stopped is True
    assert server.config["upstreams"]["alpha"]["description"] == "b"


async def test_enabled_edit_resolves_env_for_runtime_but_keeps_db_placeholder(
    beta_store, monkeypatch
):
    """H2: 热编辑启用中的 http 上游, 构造新 client 必须拿到解析后的密钥;
    live 块同步为已解析值 (与启动装配同态), 库内永远留 ${VAR} 占位。"""
    monkeypatch.setenv("BETA_KEY", "secret-123")
    server = await _make_beta_server(beta_store)
    old_client = server.upstreams["beta"]
    new_client = _FakeClient("beta")
    captured: list[dict] = []

    def fake_construct(name, cfg):
        captured.append(copy.deepcopy(cfg))
        return new_client

    async def fake_start(name, cfg, client):
        server.upstreams[name] = client
        return True

    async def fake_stop(name, client):
        client.stopped = True

    _patch_client_lifecycle(
        server, construct=fake_construct, start=fake_start, stop=fake_stop
    )

    result = await server.apply_upstream_edit(
        "beta",
        {"base_url": "http://new", "api_key": "${BETA_KEY}"},
        confirm_dangerous=True,
    )
    assert result["changed"] is True
    assert result["connected"] is True
    assert result["revision"] == 1
    assert captured[0]["api_key"] == "secret-123"
    assert captured[0]["base_url"] == "http://new"
    assert old_client.stopped is True
    assert server.upstreams["beta"] is new_client
    # live 块与启动装配一样持解析值 (避免升级时差比较误判 reconnect)
    assert server.config["upstreams"]["beta"]["api_key"] == "secret-123"
    assert server.config["upstreams"]["beta"]["base_url"] == "http://new"
    # 库内事实源只存占位符
    row = await beta_store.get_upstream("beta")
    assert row["config"] == {"base_url": "http://new", "api_key": "${BETA_KEY}"}


async def test_enabled_edit_unresolved_env_rejected_before_runtime(
    beta_store, monkeypatch
):
    """H2: 占位变量在网关环境缺失 = 新 client 必败 (确定性 401), 必须在任何
    运行态/库变更前 400 拒绝, 旧 client 原样保留。"""
    monkeypatch.delenv("BETA_KEY", raising=False)
    server = await _make_beta_server(beta_store)
    old_client = server.upstreams["beta"]

    def must_not_construct(name, cfg):
        raise AssertionError("client must not be constructed when env unresolved")

    _patch_client_lifecycle(server, construct=must_not_construct)

    with pytest.raises(ValueError, match="BETA_KEY"):
        await server.apply_upstream_edit(
            "beta",
            {"base_url": "http://new", "api_key": "${BETA_KEY}"},
            confirm_dangerous=True,
        )
    assert await beta_store.revision() == 0
    assert server.upstreams["beta"] is old_client
    assert old_client.stopped is False
    assert server.config["upstreams"]["beta"]["base_url"] == "http://old"
    row = await beta_store.get_upstream("beta")
    assert row["config"]["base_url"] == "http://old"


async def test_enabled_edit_lint_gate_blocks_before_runtime_and_db(
    beta_store, monkeypatch
):
    """M7: 候选块装配出过不了 lint 的形态 (启用 http 缺 base_url/api_key)
    必须 422 拒绝, 不构造 client、不动 live 块、不写库。"""
    monkeypatch.setenv("BETA_KEY", "secret-123")
    server = await _make_beta_server(beta_store)
    old_client = server.upstreams["beta"]

    def must_not_construct(name, cfg):
        raise AssertionError("lint must reject before client construction")

    _patch_client_lifecycle(server, construct=must_not_construct)

    with pytest.raises(LintRejected) as exc:
        await server.apply_upstream_edit("beta", {}, confirm_dangerous=True)
    assert "C001" in {i.code for i in exc.value.issues}
    assert await beta_store.revision() == 0
    assert server.upstreams["beta"] is old_client
    assert old_client.stopped is False
    assert server.config["upstreams"]["beta"]["base_url"] == "http://old"


async def test_enabled_edit_construct_failure_leaves_old_client_and_writes_nothing(
    beta_store, monkeypatch
):
    """M7: 新 client 纯构造阶段失败时, 旧 client 必须仍在服务、live 块未换、
    库未写 (构造先于一切状态变更)。"""
    monkeypatch.setenv("BETA_KEY", "secret-123")
    server = await _make_beta_server(beta_store)
    old_client = server.upstreams["beta"]

    def boom(name, cfg):
        raise ValueError("cannot build client")

    async def must_not_stop(name, client):
        raise AssertionError("old client must not be stopped when construct fails")

    _patch_client_lifecycle(server, construct=boom, stop=must_not_stop)

    with pytest.raises(ValueError, match="cannot build client"):
        await server.apply_upstream_edit(
            "beta",
            {"base_url": "http://new", "api_key": "${BETA_KEY}"},
            confirm_dangerous=True,
        )
    assert await beta_store.revision() == 0
    assert server.upstreams["beta"] is old_client
    assert old_client.stopped is False
    assert server.config["upstreams"]["beta"]["base_url"] == "http://old"


async def test_enabled_edit_conflict_after_swap_restores_old_client_and_block(
    beta_store, monkeypatch
):
    """M2: 运行态切换成功但落库撞 RevisionConflict 时, 新 client 必须下线、
    旧连接块/旧 client 尽力复原, b_two 保持发布。"""
    monkeypatch.setenv("BETA_KEY", "secret-123")
    server = await _make_beta_server(beta_store)
    old_client = server.upstreams["beta"]
    clients: list[_FakeClient] = []

    def fake_construct(name, cfg):
        client = _FakeClient(name)
        clients.append(client)
        return client

    async def fake_start(name, cfg, client):
        server.upstreams[name] = client
        return True

    async def fake_stop(name, client):
        client.stopped = True

    _patch_client_lifecycle(
        server, construct=fake_construct, start=fake_start, stop=fake_stop
    )

    with pytest.raises(RevisionConflict):
        await server.apply_upstream_edit(
            "beta",
            {"base_url": "http://new", "api_key": "${BETA_KEY}"},
            confirm_dangerous=True,
            expected_revision=99,
        )

    assert await beta_store.revision() == 0
    # 构造两次: 一次新 client, 一次回滚复原; 复原 client 在槽且存活
    assert len(clients) == 2
    new_client, restored = clients
    assert new_client.stopped is True
    assert restored.stopped is False
    assert server.upstreams["beta"] is restored
    assert old_client.stopped is True  # 旧的已停, 由复原 client 接替
    # live 块复原到编辑前 (已解析) 块
    assert server.config["upstreams"]["beta"]["base_url"] == "http://old"
    assert "b_two" in server._published_tools
    row = await beta_store.get_upstream("beta")
    assert row["config"]["base_url"] == "http://old"


async def test_enabled_edit_publish_failure_rolls_runtime_back(beta_store, monkeypatch):
    """M2: 重连成功但差集重放发布失败 → 运行态/ client 回滚, 库不动。"""
    import src.unihive.gateway_server as gs_mod

    monkeypatch.setenv("BETA_KEY", "secret-123")
    server = await _make_beta_server(beta_store)

    async def fake_start(name, cfg, client):
        server.upstreams[name] = client
        return True

    async def fake_stop(name, client):
        client.stopped = True

    clients: list[_FakeClient] = []

    def fake_construct(name, cfg):
        client = _FakeClient(name)
        clients.append(client)
        return client

    _patch_client_lifecycle(
        server, construct=fake_construct, start=fake_start, stop=fake_stop
    )
    # 强制差集重放尝试补发 b_two 且注册失败
    server._toggle_tool_diff_locked = lambda name: ({"b_two"}, set())
    real_register = gs_mod.register_tool

    def boom_register(srv, spec):
        if spec["name"] == "b_two":
            raise RuntimeError("provider exploded")
        return real_register(srv, spec)

    monkeypatch.setattr("src.unihive.core.registry.register_tool", boom_register)

    with pytest.raises(RuntimeError, match="partial failure"):
        await server.apply_upstream_edit(
            "beta",
            {"base_url": "http://new", "api_key": "${BETA_KEY}"},
            confirm_dangerous=True,
        )

    assert await beta_store.revision() == 0
    assert server.config["upstreams"]["beta"]["base_url"] == "http://old"
    assert "b_two" in server._published_tools
    # 复原 client 在槽
    assert server.upstreams["beta"] is clients[-1]
    assert clients[-1].stopped is False


async def test_revision_conflict_leaves_runtime_untouched(seeded_store):
    server = await _make_edit_server(seeded_store)
    with pytest.raises(RevisionConflict):
        await server.apply_upstream_edit(
            "beta",
            {"base_url": "http://y"},
            confirm_dangerous=True,
            expected_revision=99,
        )
    assert await seeded_store.revision() == 0
    # live 配置未被替换
    assert server.config["upstreams"]["beta"]["base_url"] == "http://x"


async def test_plaintext_secret_rejected_before_runtime_swap(seeded_store):
    server = await _make_edit_server(seeded_store)
    with pytest.raises(ValueError, match="plaintext secret"):
        await server.apply_upstream_edit(
            "beta",
            {"api_key": "sk-raw-secret-1234567890"},
            confirm_dangerous=True,
        )
    assert await seeded_store.revision() == 0
    assert "${BETA_KEY}" in server.config["upstreams"]["beta"]["api_key"]


async def test_reserved_column_keys_rejected(seeded_store):
    server = await _make_edit_server(seeded_store)
    with pytest.raises(ValueError):
        await server.apply_upstream_edit(
            "beta",
            {"base_url": "http://y", "enabled": True},
            confirm_dangerous=True,
        )
    assert await seeded_store.revision() == 0


async def test_upstream_edit_without_store_raises(seeded_store):
    server = await _make_edit_server(seeded_store)
    server.config_store = None
    with pytest.raises(RuntimeError, match="config.db"):
        await server.apply_upstream_edit(
            "beta", {"x": 1}, confirm_dangerous=True
        )
