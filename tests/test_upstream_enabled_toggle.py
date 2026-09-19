"""上游启用/禁用热开关测试 (2026-09-18, P1 后持久化走 config.db overlay)。

覆盖三层:
1. tool_loader 纯函数: backer 解析 / enabled 过滤 (含多 backer 兜底语义);
2. console_api: save_upstream_enabled / save_fuyao_cache 写 SQLite overlay,
   乐观锁 revision、恢复出厂 (与出厂值相同则删行)、镜像合并读;
3. GatewayServer.set_upstream_enabled: 热摘除/发布工具 + 断连/重连差集。
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from src.unihive.core.tool_loader import (
    backing_upstreams,
    enabled_upstream_names,
    filter_specs_by_enabled,
)
from src.unihive.core.registry import register_tool, register_tools_from_config
from src.unihive.storage.config_store import ConfigStore
from src.unihive.utils import console_api as ca


# ---------------------------------------------------------------------------
# 1. tool_loader 纯函数
# ---------------------------------------------------------------------------

def _cfg() -> dict:
    return {
        "upstreams": {
            "A": {"enabled": True},
            "B": {"enabled": False},
            "C": {},  # 缺省 = 禁用
        },
        "routing": {
            "t_chain": {"chain": ["A", "B"]},
            # 有 routing 块但无 chain —— router 实际回落到 mapping (展示层才返 [])
            "t_no_chain": {"description": "todo"},
        },
        "upstream_tool_mapping": {
            "t_a": {"A": "t_a"},
            "t_ab": {"A": "x", "B": "x"},
            "t_b": {"B": "x"},
            "t_bc": {"B": "x", "C": "x"},
            "t_no_chain": {"B": "y"},
        },
    }


def test_backing_upstreams_chain_takes_priority():
    cfg = _cfg()
    assert backing_upstreams("t_chain", cfg) == ["A", "B"]
    # mapping 里没有 t_chain, chain 仍然权威
    assert "t_chain" not in cfg["upstream_tool_mapping"]


def test_backing_upstreams_falls_back_to_mapping_keys():
    cfg = _cfg()
    assert backing_upstreams("t_ab", cfg) == ["A", "B"]
    # routing 块存在但无 chain → 与 router.route() 一样回落 mapping
    assert backing_upstreams("t_no_chain", cfg) == ["B"]
    assert backing_upstreams("missing", cfg) == []


def test_enabled_upstream_names_defaults_disabled():
    enabled = enabled_upstream_names(_cfg())
    assert enabled == {"A"}
    assert enabled_upstream_names({}) == set()
    assert enabled_upstream_names({"upstreams": None}) == set()


def test_filter_specs_by_enabled_multi_backer_semantics():
    cfg = _cfg()
    specs = [
        {"name": "t_a", "routing": "t_a"},      # 仅 A (启用) → 保留
        {"name": "t_ab", "routing": "t_ab"},    # A+B, A 启用 → 保留
        {"name": "t_b", "routing": "t_b"},      # 仅 B (禁用) → 隐藏
        {"name": "t_bc", "routing": "t_bc"},    # B+C 全禁用 → 隐藏
        {"name": "t_chain", "routing": "t_chain"},  # chain 含 A → 保留
        {"name": "t_orphan", "routing": "t_orphan"},  # 无 backer → 防御性保留
    ]
    kept = {s["name"] for s in filter_specs_by_enabled(specs, cfg)}
    assert kept == {"t_a", "t_ab", "t_chain", "t_orphan"}


def test_filter_hides_shared_tool_when_all_backers_disabled():
    cfg = {
        "upstreams": {"A": {"enabled": False}, "B": {"enabled": False}},
        "upstream_tool_mapping": {"t_ab": {"A": "x", "B": "x"}},
    }
    assert filter_specs_by_enabled([{"name": "t_ab", "routing": "t_ab"}], cfg) == []
    cfg["upstreams"]["A"]["enabled"] = True
    assert {
        s["name"] for s in filter_specs_by_enabled(
            [{"name": "t_ab", "routing": "t_ab"}], cfg
        )
    } == {"t_ab"}


# ---------------------------------------------------------------------------
# 2. console_api: SQLite overlay 持久化
# ---------------------------------------------------------------------------

SAMPLE_YAML = (
    "upstreams:\n"
    "  alpha:\n"
    "    enabled: true\n"
    "    type: http\n"
    "  beta:\n"
    "    type: stdio\n"
    "  gamma:\n"
    "    enabled: false  # 行内注释保留\n"
    "    type: omni\n"
    "routing: {}\n"
)


@pytest.fixture
async def store_env(tmp_path, monkeypatch):
    """tmp YAML 基线 + 真 ConfigStore 注入 console_api。"""
    cfg_file = tmp_path / "upstreams.yaml"
    cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(cfg_file))
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    ca.set_config_store(store, [], 0)
    yield {"store": store, "cfg_file": cfg_file}
    ca.set_config_store(None)
    await store.close()


def test_registry_aliases_live_dict_not_snapshot():
    """HIGH 回归: console_api 必须别名化 live upstreams dict,
    热开关 pop/新 client 赋值后立即可见, 不能是 init 时刻的快照拷贝。"""
    live = {"A": object()}
    ca.set_upstream_registry(live)
    try:
        assert ca._get_upstream_client("A") is live["A"]
        old = live.pop("A")  # 模拟 disable pop
        assert ca._get_upstream_client("A") is None
        live["A"] = old  # 模拟 re-enable 新 client
        assert ca._get_upstream_client("A") is old
    finally:
        ca.set_upstream_registry({})


async def test_save_disable_upserts_overlay_and_read_back(store_env):
    result = await ca.save_upstream_enabled("alpha", False)
    assert isinstance(result["version"], int) and result["version"] == 1

    row = await store_env["store"].get_overlay("upstreams.alpha.enabled")
    assert row is not None and row.value is False

    # 同步读路径 (镜像刷新后) 立即反映
    config = ca.load_config()
    assert config["upstreams"]["alpha"]["enabled"] is False
    assert config["version"] == 1
    # 兄弟字段不丢
    assert config["upstreams"]["alpha"]["type"] == "http"
    # YAML 出厂文件一个字节都不动
    assert store_env["cfg_file"].read_text(encoding="utf-8") == SAMPLE_YAML


async def test_save_re_enable_to_factory_deletes_overlay(store_env):
    await ca.save_upstream_enabled("gamma", True)   # factory false → upsert
    assert await store_env["store"].revision() == 1
    result = await ca.save_upstream_enabled("gamma", False)  # 回出厂 → delete
    assert result["version"] == 2
    assert await store_env["store"].list_overlays() == []
    assert ca.load_config()["upstreams"]["gamma"]["enabled"] is False


async def test_save_upstream_stale_revision_conflicts(store_env):
    with pytest.raises(ca.VersionConflict):
        await ca.save_upstream_enabled("alpha", False, expected_version=42)
    # 冲突不留痕
    assert await store_env["store"].list_overlays() == []


async def test_save_upstream_validates_inputs(store_env):
    with pytest.raises(KeyError):
        await ca.save_upstream_enabled("ghost", True)
    with pytest.raises(ValueError):
        await ca.save_upstream_enabled("alpha", "true")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await ca.save_upstream_enabled("alpha", True, expected_version="abc")


async def test_save_without_store_is_503_class(tmp_path, monkeypatch):
    cfg_file = tmp_path / "upstreams.yaml"
    cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(cfg_file))
    ca.set_config_store(None)
    with pytest.raises(ca.ConfigStoreUnavailable):
        await ca.save_upstream_enabled("alpha", False)


async def test_fuyao_save_upserts_tool_rows_and_hot_updates_runtime(store_env, monkeypatch):
    runtime_cfg = {"cache": {"ttl": {}}}
    monkeypatch.setattr(ca, "_GW_CFG", runtime_cfg)
    monkeypatch.setattr(ca, "_INJECTED", True)

    result = await ca.save_fuyao_cache(
        {"tools": {"get_x": 60, "get_y": 0}}, expected_version=0
    )
    assert result["version"] == 1
    paths = {r.path: r.value for r in await store_env["store"].list_overlays()}
    assert paths == {
        "fuyao_cache.tools.get_x": 60,
        "fuyao_cache.tools.get_y": 0,
    }
    got = ca.get_fuyao_cache()
    assert got["tools"] == {"get_x": 60, "get_y": 0}
    assert got["version"] == 1
    # 热改进程内 config, fuyao_ttl_for 下次调用即生效
    assert runtime_cfg["fuyao_cache"]["tools"] == {"get_x": 60, "get_y": 0}


async def test_fuyao_save_dropped_tool_deletes_row(store_env, monkeypatch):
    monkeypatch.setattr(ca, "_GW_CFG", {})
    monkeypatch.setattr(ca, "_INJECTED", True)
    await ca.save_fuyao_cache({"tools": {"get_x": 60, "get_y": 10}})
    result = await ca.save_fuyao_cache({"tools": {"get_x": 60}})
    rows = {r.path for r in await store_env["store"].list_overlays()}
    assert rows == {"fuyao_cache.tools.get_x"}
    assert result["version"] == 2


async def test_fuyao_save_equal_to_baseline_deletes_row(tmp_path, monkeypatch):
    yaml_text = SAMPLE_YAML + "fuyao_cache:\n  tools:\n    get_x: 30\n"
    cfg_file = tmp_path / "upstreams.yaml"
    cfg_file.write_text(yaml_text, encoding="utf-8")
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(cfg_file))
    store = ConfigStore(tmp_path / "config.db")
    await store.initialize()
    ca.set_config_store(store, [], 0)
    try:
        await ca.save_fuyao_cache({"tools": {"get_x": 60}})
        # 改成出厂值 30 → 覆盖行删除, 生效值回落 YAML 30
        await ca.save_fuyao_cache({"tools": {"get_x": 30}})
        assert await store.list_overlays() == []
        assert ca.get_fuyao_cache()["tools"] == {"get_x": 30}
    finally:
        ca.set_config_store(None)
        await store.close()


async def test_fuyao_save_validation(store_env):
    with pytest.raises(ValueError):
        await ca.save_fuyao_cache({"tools": {"get_x": -1}})
    with pytest.raises(ValueError):
        await ca.save_fuyao_cache({"tools": {"get_x": "60"}})
    with pytest.raises(ValueError):
        await ca.save_fuyao_cache({"endpoints": {}})  # 旧 shape, extra forbid


async def test_fuyao_batch_save_is_single_revision(store_env):
    await ca.save_fuyao_cache({"tools": {f"t{i}": 10 + i for i in range(5)}})
    assert await store_env["store"].revision() == 1


# ---------------------------------------------------------------------------
# 3. registry 单工具注册
# ---------------------------------------------------------------------------

class _FakeToolDecorator:
    def __init__(self) -> None:
        self.registered: list[tuple[str, dict | None]] = []

    def __call__(self, **kwargs):
        annotations = kwargs.get("annotations")

        def decorator(fn):
            self.registered.append((fn.__name__, annotations))
            return fn

        return decorator


class _FakeMCP:
    def __init__(self) -> None:
        self.tool_calls = _FakeToolDecorator()
        self.removed: list[str] = []
        self._tools: set[str] = set()

    def tool(self, **kwargs):
        return self.tool_calls(**kwargs)

    class _LocalProvider:
        def __init__(self, owner: "_FakeMCP") -> None:
            self.owner = owner

        def remove_tool(self, name: str, version=None) -> None:
            if name not in self.owner._tools:
                raise KeyError(name)
            self.owner._tools.discard(name)
            self.owner.removed.append(name)

    @property
    def local_provider(self):
        return _FakeMCP._LocalProvider(self)


class _FakeServer:
    def __init__(self) -> None:
        self.mcp = _FakeMCP()

    async def _execute_cached(self, *a, **kw):
        return {}


def test_register_tool_single_and_batch_equivalent():
    spec = {
        "name": "demo_tool",
        "routing": "demo_tool",
        "description": "d",
        "params": [],
        "annotations": {"read_only_hint": True},
    }
    s1 = _FakeServer()
    name = register_tool(s1, spec)
    assert name == "demo_tool"
    assert s1.mcp.tool_calls.registered == [
        ("demo_tool", {"read_only_hint": True})
    ]

    plain = {"name": "plain", "routing": "plain", "params": []}
    s2 = _FakeServer()
    assert register_tools_from_config(s2, [plain]) == ["plain"]
    assert s2.mcp.tool_calls.registered == [("plain", None)]


# ---------------------------------------------------------------------------
# 4. GatewayServer.set_upstream_enabled 热编排
# ---------------------------------------------------------------------------

def _make_server() -> object:
    """绕过 __init__ 构造最小可用 GatewayServer (仓库既有测试同款手法)。"""
    from src.unihive.gateway_server import GatewayServer

    server = GatewayServer.__new__(GatewayServer)
    server.config = {
        "upstreams": {
            "A": {"enabled": True, "type": "http"},
            "B": {"enabled": False, "type": "http"},
        },
        "upstream_tool_mapping": {
            "t_a": {"A": "t_a"},
            "t_ab": {"A": "t_ab", "B": "t_ab"},
            "t_b": {"B": "t_b"},
        },
        "routing": {},
    }
    server.tool_specs = [
        {"name": n, "routing": n, "description": n, "params": []}
        for n in ("t_a", "t_ab", "t_b")
    ]
    server.mcp = _FakeMCP()
    server._published_tools = {"t_a", "t_ab"}
    server._upstream_toggle_lock = asyncio.Lock()
    server.upstreams = {}
    return server


class _FakeClient:
    def __init__(self, name: str, available: bool = True) -> None:
        self.name = name
        self.is_available = available
        self.stopped = False

    async def stop(self) -> None:
        await asyncio.sleep(0)
        self.stopped = True


async def test_disable_removes_tools_then_stops_client():
    server = _make_server()
    # 默认 _make_server 里 B 已是禁用: A 禁用后 t_ab 也失去最后启用 backer
    client_a = _FakeClient("A")
    client_b = _FakeClient("B")
    server.upstreams["A"] = client_a
    server.upstreams["B"] = client_b  # B 禁用但残留 (例如启动失败登记)
    server.mcp._tools = {"t_a", "t_ab"}

    result = await server.set_upstream_enabled("A", False)

    assert result["enabled"] is False
    assert sorted(result["tools_removed"]) == ["t_a", "t_ab"]
    assert result["tools_added"] == []
    assert server.mcp.removed == ["t_a", "t_ab"]  # sorted 顺序摘除
    # 只 pop/stop 被禁用的 A, B 残留不动
    assert "A" not in server.upstreams
    assert client_a.stopped is True
    assert server.upstreams["B"] is client_b
    assert client_b.stopped is False


async def test_disable_keeps_shared_tool_with_another_enabled_backer():
    server = _make_server()
    server.config["upstreams"]["B"]["enabled"] = True
    client_a = _FakeClient("A")
    server.upstreams["A"] = client_a
    server.upstreams["B"] = _FakeClient("B")
    server.mcp._tools = {"t_a", "t_ab"}

    result = await server.set_upstream_enabled("A", False)

    assert sorted(result["tools_removed"]) == ["t_a"]
    assert "t_ab" not in server.mcp.removed
    assert "t_ab" in server._published_tools
    assert "A" not in server.upstreams
    assert client_a.stopped is True
    assert "B" in server.upstreams  # 另一上游不动


async def test_enable_starts_client_and_publishes_tools():
    server = _make_server()
    new_client = _FakeClient("B")

    server._construct_upstream_client = lambda name, cfg: new_client  # type: ignore[attr-defined]

    async def _fake_start(name, cfg, client) -> bool:
        server.upstreams[name] = client
        return client.is_available

    server._start_upstream_client = _fake_start  # type: ignore[attr-defined]

    result = await server.set_upstream_enabled("B", True)

    assert result["enabled"] is True
    assert result["connected"] is True
    # t_ab 在 A 启用时已发布, 不重复注册; 只有 B 专属的 t_b 是新增
    assert result["tools_added"] == ["t_b"]
    assert server.upstreams["B"] is new_client
    assert server._published_tools == {"t_a", "t_ab", "t_b"}
    registered_names = {n for n, _ in server.mcp.tool_calls.registered}
    assert registered_names == {"t_b"}
    assert server.config["upstreams"]["B"]["enabled"] is True


async def test_toggle_noop_when_state_unchanged():
    server = _make_server()
    client_a = _FakeClient("A")
    server.upstreams["A"] = client_a

    result = await server.set_upstream_enabled("A", True)

    assert result["changed"] is False
    assert result["tools_added"] == [] and result["tools_removed"] == []
    assert client_a.stopped is False


async def test_disable_clears_console_omni_pointer():
    """MED 回归: 禁用的若是 OmniClient, console_api 持有的 omni 指针必须
    置 None, 否则 /api/omni* (含 POST sync) 仍打在已停 client 上。"""
    from src.unihive.api.omni_client import OmniClient

    class _FakeOmni(OmniClient):
        def __init__(self) -> None:  # 跳过真实 sqlite 初始化
            self.stopped = False

        @property
        def is_available(self) -> bool:  # OmniClient 为只读 property, 覆写
            return True

        async def stop(self) -> None:
            self.stopped = True

    server = _make_server()
    fake_omni = _FakeOmni()
    server.upstreams["A"] = fake_omni
    ca.set_omni_client(fake_omni)
    try:
        result = await server.set_upstream_enabled("A", False)
        assert result["enabled"] is False
        assert fake_omni.stopped is True
        assert ca._omni() is None
    finally:
        ca.set_omni_client(None)


async def test_toggle_unknown_upstream_raises():
    server = _make_server()
    with pytest.raises(KeyError):
        await server.set_upstream_enabled("ghost", True)


async def test_enable_partial_publish_failure_self_heals_on_retry(monkeypatch):
    """发布 t_b 中途失败: cfg 已翻为 enabled、client 已连, 500 抛出;
    前端原样重试 (状态已一致) 走 reconcile, 必须补发缺失工具而非 no-op 短路。"""
    import src.unihive.gateway_server as gs_mod

    server = _make_server()
    new_client = _FakeClient("B")
    server._construct_upstream_client = lambda name, cfg: new_client  # type: ignore[attr-defined]

    async def _fake_start(name, cfg, client) -> bool:
        server.upstreams[name] = client
        return True

    server._start_upstream_client = _fake_start  # type: ignore[attr-defined]

    real_register_tool = gs_mod.register_tool
    call_count = {"n": 0}

    def flaky_register(srv, spec):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("boom")
        return real_register_tool(srv, spec)

    monkeypatch.setattr("src.unihive.core.registry.register_tool", flaky_register)

    with pytest.raises(RuntimeError, match="partial failure"):
        await server.set_upstream_enabled("B", True)
    assert server.config["upstreams"]["B"]["enabled"] is True
    assert server.upstreams["B"] is new_client
    assert "t_b" not in server._published_tools

    # 同态重试: reconcile 补发
    monkeypatch.undo()
    result = await server.set_upstream_enabled("B", True)
    assert result["changed"] is True
    assert result["tools_added"] == ["t_b"]
    assert result["tools_removed"] == []
    assert "t_b" in server._published_tools


async def test_disable_partial_failure_keeps_client_and_heals_on_retry(monkeypatch):
    """摘除 t_a 失败: 已成功摘的 t_ab 落账, 失败的 t_a 保留发布; client 不断
    (残存工具仍可服务); 同态重试补摘后再断连。"""
    server = _make_server()
    client_a = _FakeClient("A")
    server.upstreams["A"] = client_a
    server.mcp._tools = {"t_a", "t_ab"}

    real_remove = _FakeMCP._LocalProvider.remove_tool
    state = {"failed_once": False}

    def flaky_remove(self, name, version=None):
        if name == "t_a" and not state["failed_once"]:
            state["failed_once"] = True
            raise RuntimeError("provider down")
        return real_remove(self, name, version)

    monkeypatch.setattr(_FakeMCP._LocalProvider, "remove_tool", flaky_remove)

    with pytest.raises(RuntimeError, match="partial failure"):
        await server.set_upstream_enabled("A", False)
    # client 保留且未 stop, t_a 仍在发布集, t_ab 已摘
    assert server.upstreams["A"] is client_a
    assert client_a.stopped is False
    assert server._published_tools == {"t_a"}

    monkeypatch.undo()
    result = await server.set_upstream_enabled("A", False)
    assert result["changed"] is True
    assert result["tools_removed"] == ["t_a"]
    assert server._published_tools == set()
    assert "A" not in server.upstreams
    assert client_a.stopped is True


def test_validate_toggle_enabled_body():
    assert ca._validate_toggle_enabled_body({"enabled": True}) == (True, None)
    assert ca._validate_toggle_enabled_body({"enabled": False}) == (False, None)
    enabled, msg = ca._validate_toggle_enabled_body({"enabled": "yes"})
    assert enabled is None and isinstance(msg, str)
    enabled, msg = ca._validate_toggle_enabled_body(["not", "object"])
    assert enabled is None and isinstance(msg, str)
    enabled, msg = ca._validate_toggle_enabled_body({})
    assert enabled is None and isinstance(msg, str)


async def test_concurrent_toggles_serialize_through_lock():
    """两个不同上游的并发开关都正确收敛 (锁不串扰、无丢失)。"""
    server = _make_server()
    server.config["upstreams"]["B"]["enabled"] = True
    server.upstreams["A"] = _FakeClient("A")
    server.upstreams["B"] = _FakeClient("B")
    server.mcp._tools = {"t_a", "t_ab"}
    # A/B 都启用: 禁 A 删 t_a; 禁 B 则 t_ab 失去最后 backer 也被删
    await asyncio.gather(
        server.set_upstream_enabled("A", False),
        server.set_upstream_enabled("B", False),
    )
    assert server.config["upstreams"]["A"]["enabled"] is False
    assert server.config["upstreams"]["B"]["enabled"] is False
    assert not server.upstreams
    assert server._published_tools == set()


async def test_toggle_rereads_live_block_inside_lock_after_concurrent_replace():
    """H3: cfg 必须在 toggle 锁内从 live config 重读。

    旧实现在锁外捕获 cfg: 等待锁期间若 apply_upstream_edit/出厂升级整块替换
    了 self.config['upstreams'][name], toggle 会在孤儿 dict 上翻 enabled,
    用旧连接块建 client, 却返回 enabled=True/tools_added=[] —— live 真值仍是
    禁用。锁内重读后必须操作新块 (新 base_url), live enabled 同步翻转。
    """
    import copy as _copy

    server = _make_server()
    server.config["upstreams"]["B"]["base_url"] = "http://old"

    real_lock = server._upstream_toggle_lock
    new_config = _copy.deepcopy(server.config)
    new_config["upstreams"]["B"] = {
        "enabled": False,
        "type": "http",
        "base_url": "http://new",
    }

    class _RacyLock:
        async def __aenter__(self):
            await real_lock.acquire()
            # 模拟本协程等待锁期间, 另一写者整块替换了 live config
            server.config = new_config

        async def __aexit__(self, *exc):
            real_lock.release()

    server._upstream_toggle_lock = _RacyLock()

    captured: list[dict] = []
    server._construct_upstream_client = (
        lambda name, cfg: captured.append(cfg) or _FakeClient(name)
    )

    async def _fake_start(name, cfg, client) -> bool:
        server.upstreams[name] = client
        return True

    server._start_upstream_client = _fake_start

    result = await server.set_upstream_enabled("B", True)

    assert result["enabled"] is True
    # live 真值必须真的被翻转 (旧实现只改了孤儿 dict)
    assert server.config["upstreams"]["B"]["enabled"] is True
    # client 必须按新块构造 (旧实现用的是锁外捕获的 http://old)
    assert captured and captured[0]["base_url"] == "http://new"


def test_route_mounted_in_serve_http():
    from src.unihive.gateway_server import GatewayServer

    source = inspect.getsource(GatewayServer.serve_http)
    assert "build_upstream_routes()" in source
