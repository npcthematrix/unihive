"""第 9 轮启动逻辑审视 — Batch A: v2 热改/启动正确性。

覆盖: L1 (config source/path 严格校验) / M1 (seed 竞赛容忍) /
L2 (写事务 BaseException 回滚) / M6 (disabled 上游 env 跳过) /
M4 (initialize 失败清理 v2 资源) / M5 (监听键快照后移) /
M2 (热改回滚避免双活 client) / M3 (热升级 CancelledError 回滚)。
"""
from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.unihive.core.config_db import (
    ensure_seeded,
    project_root,
    resolve_config_db_path,
)
from src.unihive.gateway_server import GatewayServer
from src.unihive.storage.config_store import AlreadySeededError, ConfigStore
from src.unihive.utils import console_api as ca
from src.unihive.utils.config_loader import resolve_env_vars

from tests.test_config_store_v2 import BUNDLE, VERSION
from tests.test_upstream_edit_hot import _make_beta_server
from tests.test_upstream_enabled_toggle import _FakeClient, _FakeMCP


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


# ---------------------------------------------------------------------------
# L1: config source / db path 严格校验
# ---------------------------------------------------------------------------


def test_l1_empty_config_db_env_treated_as_none():
    """UNIHIVE_CONFIG_DB="" 不得被当作"项目根目录"路径 (会变成目录,
    sqlite 报难读错); 应与 None 等价锚定项目根 data/config.db。"""
    assert resolve_config_db_path("") == resolve_config_db_path(None)
    assert resolve_config_db_path("") == project_root() / "data" / "config.db"


def test_l1_bad_config_source_rejects_instead_of_silent_db(
    monkeypatch, tmp_path
):
    """UNIHIVE_CONFIG_SOURCE 拼错 (如 "deeb") 不得静默当作 db 模式;
    应 fail-closed 报 ValueError, 否则用户以为逃生到了 yaml 却进了 db。"""
    monkeypatch.setenv("UNIHIVE_CONFIG_SOURCE", "deeb")
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", str(tmp_path / "config.db"))
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="(?i)UNIHIVE_CONFIG_SOURCE|config_source"):
        GatewayServer(str(cfg), strict_env=False)


# ---------------------------------------------------------------------------
# M1: ensure_seeded 容忍跨进程首启竞赛
# ---------------------------------------------------------------------------


_VALID_YAML = (
    "upstreams:\n"
    "  ok:\n"
    "    type: http\n"
    "    enabled: true\n"
    "    base_url: http://127.0.0.1:9/sse\n"
    "    api_key: ${M1_OK_KEY}\n"
    "upstream_tool_mapping: {}\n"
    "routing: {}\n"
)


async def test_m1_already_seeded_treated_as_load_existing(tmp_path, monkeypatch):
    """两进程同时首启空库: B 拿锁后复查已有实体抛 AlreadySeededError
    (RuntimeError, 非 ValueError)。ensure_seeded 应把它当作"读现有库继续",
    而非让配置正确的 B 进程启动失败。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("M1_OK_KEY", "k")
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text(_VALID_YAML, encoding="utf-8")
    db = tmp_path / "config.db"

    # A 进程先 seed 完毕
    store_a = ConfigStore(str(db))
    await store_a.initialize()
    await ensure_seeded(store_a, cfg)
    await store_a.close()

    # B 进程: is_seeded() 仍返回 False (模拟 check-then-seed 竞赛窗口),
    # seed_from_bundle 复查发现已有实体 → AlreadySeededError。
    store_b = ConfigStore(str(db))
    await store_b.initialize()
    store_b.is_seeded = AsyncMock(return_value=False)  # type: ignore[method-assign]

    # 修复前: AlreadySeededError 逃逸 → 启动失败。
    # 修复后: 当作已 seed, 直接读出库内实体。
    template = await ensure_seeded(store_b, cfg)
    assert {u.name for u in template.upstreams} == {"ok"}
    assert await store_b.is_seeded() is False  # mock 不影响断言语义
    await store_b.close()


# ---------------------------------------------------------------------------
# L2: config_store 写事务 CancelledError 也回滚
# ---------------------------------------------------------------------------


class _FakeSession:
    """最小 session: 记录 execute/commit/rollback, 模拟 async context。"""

    def __init__(self) -> None:
        self.execute = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_l2_write_txn_rollback_on_cancelled_error(tmp_path):
    """写事务 (BEGIN IMMEDIATE) 体抛 CancelledError (BaseException) 时,
    except Exception 接不住 → 连接未 rollback 还池, RESERVED 写锁滞留。
    修复后: except BaseException → rollback → raise。"""
    store = ConfigStore(str(tmp_path / "config.db"))
    await store.initialize()
    fake = _FakeSession()
    store._session = AsyncMock(return_value=fake)  # type: ignore[method-assign]
    # _write_lock 仍真实, 保证串行
    raised: asyncio.CancelledError | None = None
    try:
        async with store._txn(immediate=True):
            raise asyncio.CancelledError("client disconnected mid-write")
    except asyncio.CancelledError as exc:
        raised = exc
    assert raised is not None
    # 核心: rollback 必须被调用 (修复前不会被调)
    fake.rollback.assert_awaited()
    fake.commit.assert_not_awaited()
    await store.close()


# ---------------------------------------------------------------------------
# M6: strict env 解析跳过 disabled 上游
# ---------------------------------------------------------------------------


def test_m6_strict_env_skips_disabled_upstream_missing_var():
    """禁用上游引用未设环境变量时, strict=True 不得 KeyError 拒启动
    (与 validate_config 同语义: disabled 不参与校验);
    启用上游的缺失变量仍须报 KeyError。"""
    config = {
        "upstreams": {
            "on": {"enabled": True, "type": "http", "api_key": "${ON_KEY}"},
            "off": {"enabled": False, "type": "http", "api_key": "${OFF_KEY}"},
        }
    }
    import os

    os.environ["ON_KEY"] = "set"
    try:
        resolved, missing = resolve_env_vars(
            config, strict=True, skip_disabled_upstreams=True
        )
    finally:
        os.environ.pop("ON_KEY", None)
    # 启用上游解析成功
    assert resolved["upstreams"]["on"]["api_key"] == "set"
    # 禁用上游保留占位 (不解析, 也不进 missing)
    assert resolved["upstreams"]["off"]["api_key"] == "${OFF_KEY}"
    assert "OFF_KEY" not in missing


def test_m6_strict_env_still_raises_for_enabled_upstream_missing_var():
    """启用上游缺 env 仍须 KeyError (不能因 skip 选项放过真问题)。"""
    config = {
        "upstreams": {
            "on": {"enabled": True, "type": "http", "api_key": "${MISSING_KEY_X}"},
        }
    }
    with pytest.raises(KeyError, match="MISSING_KEY_X"):
        resolve_env_vars(config, strict=True, skip_disabled_upstreams=True)


# ---------------------------------------------------------------------------
# M4: initialize 失败清理 v2 资源 (config_store / console_api 全局 / cleanup task)
# ---------------------------------------------------------------------------


async def test_m4a_load_db_config_closes_store_on_initialize_failure(
    tmp_path, monkeypatch
):
    """store.initialize() 自身失败 (如库路径不可写) 时, 旧实现在 try 外抛,
    except 接不到 → store.close() 不被调 → engine 泄漏。修复后 close 被调。"""
    monkeypatch.chdir(tmp_path)
    server = GatewayServer.__new__(GatewayServer)
    server._db_mode = True
    server._strict_env = False
    server._db_path = str(tmp_path / "config.db")
    server.config_path = tmp_path / "upstreams.yaml"
    server.config_path.write_text(_VALID_YAML, encoding="utf-8")
    server.config = {}
    server.config_store = None

    close_calls: list[ConfigStore] = []

    async def boom_initialize(self):
        raise RuntimeError("cannot open db")

    async def spy_close(self):
        close_calls.append(self)

    monkeypatch.setattr(ConfigStore, "initialize", boom_initialize)
    monkeypatch.setattr(ConfigStore, "close", spy_close)

    with pytest.raises(RuntimeError, match="cannot open db"):
        await server._load_db_config()
    assert len(close_calls) == 1, "store.close() must be called on init failure"


async def test_m4b_do_initialize_failure_cleans_v2_resources(
    tmp_path, monkeypatch
):
    """_do_initialize 在 config_store 已赋值 + cleanup task 已建之后失败
    (如 MCP 工厂抛错) 时, except 块必须: 关 config_store、取消 cleanup task、
    复位 console_api 全局。修复前只清 client/cache/router/mcp。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UNIHIVE_CONFIG_DB", str(tmp_path / "config.db"))
    cfg = tmp_path / "upstreams.yaml"
    # 全禁用上游 → 启动循环不构造 client, 隔离网络
    cfg.write_text(
        "upstreams:\n"
        "  off:\n"
        "    type: http\n"
        "    enabled: false\n"
        "    base_url: http://127.0.0.1:9\n"
        "upstream_tool_mapping: {}\n"
        "routing: {}\n",
        encoding="utf-8",
    )
    # 预 seed 库, 让 _load_db_config 直接走 load 路径
    store = ConfigStore(str(tmp_path / "config.db"))
    await store.initialize()
    await ensure_seeded(store, cfg)
    await store.close()

    # 保存 console_api 全局, 测后复位
    saved = {
        "_config_store": ca._config_store,
        "_GW_CFG": ca._GW_CFG,
        "_INJECTED": ca._INJECTED,
    }
    try:
        server = GatewayServer(str(cfg), strict_env=False)
        # 让 MCP 工厂构造阶段抛错 (config_store 与 cleanup task 已就位)
        async def boom_mcp(specs):
            raise RuntimeError("mcp factory boom")

        server._create_and_register_gateway_mcp = boom_mcp  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="mcp factory boom"):
            await server._do_initialize()

        # config_store 必须被关且复位
        assert server.config_store is None
        # cleanup task 必须被取消/清空
        task = getattr(server, "_cache_cleanup_task", None)
        assert task is None or task.cancelled() or task.done()
        # console_api 写目标全局必须复位 (不再指向已死 server 的 store)
        assert ca._config_store is None
    finally:
        ca._config_store = saved["_config_store"]
        ca._GW_CFG = saved["_GW_CFG"]
        ca._INJECTED = saved["_INJECTED"]
        ca._interfaces_cache.clear()
        ca._mcp_tools_cache.clear()


# ---------------------------------------------------------------------------
# M5: serve_http 监听键快照挪到 initialize 之后
# ---------------------------------------------------------------------------


async def test_m5_listen_args_read_from_post_init_config(monkeypatch, tmp_path):
    """M9 路径下 __init__ 预检失败 self.config={} (或不含 gateway 段)。
    serve_http 必须在 initialize() 之后从 self.config 取 host/port/mcp_path,
    否则 DB/config.yaml 里改的端口永不生效 (静默回落 127.0.0.1:18080/mcp)。"""
    import src.unihive.gateway_server as gs_mod

    # 构造 server, config 为空 (模拟 M9 预检空 dict)
    server = GatewayServer.__new__(GatewayServer)
    server.config = {}  # pre-init 空态
    server._running = False
    server._initialized = False
    server.upstreams = {}
    server._cache_cleanup_task = None
    server.cache = None
    server.config_store = None
    server.router = None

    captured: dict = {}

    async def fake_initialize(self):
        # 模拟 _load_db_config 装配出 DB 里的 gateway 段
        self.config = {
            "gateway": {"host": "127.0.0.1", "port": 19090, "mcp_path": "/mcp9"},
            "logging": {"level": "info"},
        }
        self._initialized = True

    monkeypatch.setattr(GatewayServer, "initialize", fake_initialize)

    # 假 mcp: http_app 返回带 lifespan 的假 ASGI app
    class _FakeApp:
        lifespan = None

        def http_app(self, **kwargs):
            return _FakeApp()

    server.mcp = _FakeApp()  # type: ignore[assignment]

    # 屏蔽端口探测 socket
    fake_sock = MagicMock()
    fake_sock.bind = MagicMock()
    fake_sock.close = MagicMock()
    monkeypatch.setattr(gs_mod.socket, "socket", lambda *a, **kw: fake_sock)

    # 屏蔽 uvicorn: 记录 host/port
    class _FakeUvicornServer:
        def __init__(self, config):
            captured["host"] = config.host
            captured["port"] = config.port

        async def serve(self):
            return None

    class _FakeUvicornConfig:
        def __init__(self, app, host, port, **kw):
            self.host = host
            self.port = port
            self.app = app

    monkeypatch.setattr(gs_mod.uvicorn, "Server", _FakeUvicornServer, raising=True)
    monkeypatch.setattr(gs_mod.uvicorn, "Config", _FakeUvicornConfig, raising=True)

    # 屏蔽 stop (会碰未初始化字段)
    async def fake_stop(self):
        self._running = False

    monkeypatch.setattr(GatewayServer, "stop", fake_stop)

    await server.serve_http()  # host/port/mcp_path 全 None → 走 config 默认

    # 修复前: 快照在 init 前, self.config={} → port=18080
    # 修复后: 快照在 init 后, 取到 DB 的 19090
    assert captured.get("port") == 19090, (
        f"listen port must come from post-init config (got {captured.get('port')})"
    )


# ---------------------------------------------------------------------------
# M2: 热改回滚 — CancelledError 期间 start 新 client, 旧 client 不得成孤儿
# ---------------------------------------------------------------------------


async def test_m2_cancel_during_start_reregisters_old_client_not_duplicate(
    beta_store, monkeypatch
):
    """apply_upstream_edit 启用上游重连, start(new_client) 被 cancel:
    旧实现回滚时另建一个 restored client, 而 old_client 从未被 stop →
    双活 (old 孤儿 + restored 在槽)。修复后: old 未停则重新登记 old,
    不构造 restored。"""
    monkeypatch.setenv("BETA_KEY", "secret-123")
    server = await _make_beta_server(beta_store)
    old_client = server.upstreams["beta"]
    construct_calls: list = []

    def fake_construct(name, cfg):
        construct_calls.append(name)
        return _FakeClient(name)  # new_client

    async def fake_start(name, cfg, client):
        # 模拟 _start_upstream_client 登记新 client 后被 cancel
        server.upstreams[name] = client
        raise asyncio.CancelledError()

    async def fake_stop(name, client):
        client.stopped = True

    server._construct_upstream_client = fake_construct  # type: ignore[assignment]
    server._start_upstream_client = fake_start  # type: ignore[assignment]
    server._stop_one_upstream = fake_stop  # type: ignore[assignment]

    with pytest.raises(asyncio.CancelledError):
        await server.apply_upstream_edit(
            "beta",
            {"base_url": "http://new", "api_key": "${BETA_KEY}"},
            confirm_dangerous=True,
        )

    # old_client 从未被 stop (cancel 发生在 start new 阶段)
    assert old_client.stopped is False
    # 修复后: old_client 重新登记回槽, 不构造 restored
    assert server.upstreams["beta"] is old_client
    # 只构造了一次 (new_client); 未因回滚再构造 restored
    assert construct_calls == ["beta"]


# ---------------------------------------------------------------------------
# M3: 热升级 attach 期间 CancelledError → 回滚 + 传播 (不得半迁移逃逸)
# ---------------------------------------------------------------------------


async def test_m3_cancel_during_attach_triggers_rollback_and_propagates(
    beta_store, monkeypatch
):
    """apply_config_upgrade attach 循环遇 CancelledError: 旧实现 except Exception
    接不住 BaseException → 直接逃出锁, live config/specs 已换、部分 client 已断、
    DB 未变, 长期半迁移。修复后: 触发 _rollback_config_upgrade_locked 并重抛。"""
    monkeypatch.setenv("BETA_KEY", "secret-123")
    server = await _make_beta_server(beta_store)

    rollback_called: list[bool] = []

    async def fake_rollback(**kwargs):
        rollback_called.append(True)

    server._rollback_config_upgrade_locked = fake_rollback  # type: ignore[assignment]

    async def boom_attach(name, cfg):
        raise asyncio.CancelledError()

    server._attach_upstream_client_locked = boom_attach  # type: ignore[assignment]

    # 用假 plan 喂给锁内段, 避开真实 _prepare_config_upgrade 的 DB+YAML 读。
    fake_result = MagicMock()
    fake_result.conflicts = []  # unresolved_conflicts → []
    fake_result.bundle = copy.deepcopy(BUNDLE)
    fake_result.summary = MagicMock(return_value={
        "added": [], "removed": [], "updated": [{"path": "beta"}]
    })
    fake_plan = {
        "resolutions": {},
        "revision": 0,
        "old_version": "v0",
        "new_version": "v1",
        "new_bundle": copy.deepcopy(BUNDLE),
        "result": fake_result,
        "summary": fake_result.summary.return_value,
        "changed": True,
        "candidate": copy.deepcopy(server.config),
        "candidate_specs": list(server.tool_specs),
        "candidate_hidden": set(),
        "lint_errors": [],
        "lint_warnings": [],
    }
    server._prepare_config_upgrade = AsyncMock(return_value=fake_plan)  # type: ignore[assignment]

    # 让 _upgrade_upstream_impact 返回需要 attach beta
    monkeypatch.setattr(
        server, "_upgrade_upstream_impact", lambda candidate: (["beta"], [], [])
    )
    monkeypatch.setattr(
        server,
        "_upgrade_tool_impact",
        lambda old_s, new_s, c, h: (set(), set(), []),
    )
    monkeypatch.setattr(
        server, "_unpublish_tools_locked", lambda to_remove, errors: []
    )
    monkeypatch.setattr(server, "_publish_tools_locked", lambda to_add, errors: [])
    monkeypatch.setattr(
        server, "_raise_if_toggle_errors", lambda label, errors: None
    )

    store = server.config_store
    # 落库不会到达 (cancel 先于落库)
    monkeypatch.setattr(
        store, "apply_upgrade_bundle", AsyncMock(return_value=1)
    )

    with pytest.raises(asyncio.CancelledError):
        await server.apply_config_upgrade(
            resolutions=None, expected_revision=0
        )

    assert rollback_called, "CancelledError during attach must trigger rollback"
