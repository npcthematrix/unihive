"""上游启用/禁用热开关测试 (2026-09-18)。

覆盖三层:
1. tool_loader 纯函数: backer 解析 / enabled 过滤 (含多 backer 兜底语义);
2. console_api: yaml 保格式定向改写 + save_upstream_enabled 乐观锁/校验;
3. GatewayServer.set_upstream_enabled: 热摘除/发布工具 + 断连/重连差集。
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
import yaml

from src.unihive.core.tool_loader import (
    backing_upstreams,
    enabled_upstream_names,
    filter_specs_by_enabled,
)
from src.unihive.core.registry import register_tool, register_tools_from_config
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
# 2. console_api yaml 改写 + 持久化
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


def test_rewrite_flips_only_target_scalar():
    out = ca.rewrite_upstream_enabled(SAMPLE_YAML, "alpha", False)
    parsed = yaml.safe_load(out)
    assert parsed["upstreams"]["alpha"]["enabled"] is False
    # 其他块与顶层段原样
    assert parsed["upstreams"]["beta"] == {"type": "stdio"}
    assert parsed["routing"] == {}
    changed = [
        (i, a, b)
        for i, (a, b) in enumerate(
            zip(SAMPLE_YAML.splitlines(), out.splitlines()), start=1
        )
        if a != b
    ]
    assert changed == [(3, "    enabled: true", "    enabled: false")]


def test_rewrite_inserts_enabled_when_missing():
    out = ca.rewrite_upstream_enabled(SAMPLE_YAML, "beta", True)
    parsed = yaml.safe_load(out)
    assert parsed["upstreams"]["beta"]["enabled"] is True
    # 插入位置在块头 (lines[4]='  beta:') 之后、type 之前
    lines = out.splitlines()
    assert lines[5] == "    enabled: true"
    assert lines[6] == "    type: stdio"


def test_rewrite_preserves_inline_comment_and_roundtrips():
    out = ca.rewrite_upstream_enabled(SAMPLE_YAML, "gamma", True)
    assert "# 行内注释保留" in out
    assert yaml.safe_load(out)["upstreams"]["gamma"]["enabled"] is True
    # 翻回去与原文逐字节一致
    assert ca.rewrite_upstream_enabled(out, "gamma", False) == SAMPLE_YAML


def test_rewrite_unknown_upstream_raises():
    with pytest.raises(KeyError):
        ca.rewrite_upstream_enabled(SAMPLE_YAML, "nope", True)


def test_save_upstream_enabled_persists_and_versions(tmp_path, monkeypatch):
    cfg_file = tmp_path / "upstreams.yaml"
    cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(cfg_file))

    result = ca.save_upstream_enabled("alpha", False)
    assert "version" in result and len(result["version"]) == 16
    on_disk = yaml.safe_load(cfg_file.read_text(encoding="utf-8"))
    assert on_disk["upstreams"]["alpha"]["enabled"] is False
    # 注释/手工格式未被整文件 dump 破坏
    assert "# 行内注释保留" in cfg_file.read_text(encoding="utf-8")


def test_save_upstream_enabled_stale_version_conflicts(tmp_path, monkeypatch):
    cfg_file = tmp_path / "upstreams.yaml"
    cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(cfg_file))
    with pytest.raises(ca.VersionConflict):
        ca.save_upstream_enabled("alpha", False, expected_version="stale-hash")
    # 冲突时不得落盘
    assert yaml.safe_load(cfg_file.read_text(encoding="utf-8"))[
        "upstreams"
    ]["alpha"]["enabled"] is True


def test_rewrite_preserves_crlf_newline_style():
    crlf_text = SAMPLE_YAML.replace("\n", "\r\n")
    out = ca.rewrite_upstream_enabled(crlf_text, "alpha", False)
    assert "\r\n" in out
    # 不得混入裸 LF (除被 CRLF 包裹的之外)
    assert "\n" not in out.replace("\r\n", "")
    assert yaml.safe_load(out)["upstreams"]["alpha"]["enabled"] is False


def test_rewrite_preserves_single_space_comment_gap():
    text = "upstreams:\n  x:\n    enabled: true # 注释\nrouting: {}\n"
    out = ca.rewrite_upstream_enabled(text, "x", False)
    assert "    enabled: false # 注释" in out
    assert ca.rewrite_upstream_enabled(out, "x", True) == text


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


def test_save_upstream_enabled_validates_inputs(tmp_path, monkeypatch):
    cfg_file = tmp_path / "upstreams.yaml"
    cfg_file.write_text(SAMPLE_YAML, encoding="utf-8")
    monkeypatch.setattr(ca, "GATEWAY_CONFIG_PATH", str(cfg_file))
    with pytest.raises(KeyError):
        ca.save_upstream_enabled("ghost", True)
    with pytest.raises(ValueError):
        ca.save_upstream_enabled("alpha", "true")  # type: ignore[arg-type]


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

    monkeypatch.setattr(gs_mod, "register_tool", flaky_register)

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


def test_route_mounted_in_serve_http():
    from src.unihive.gateway_server import GatewayServer

    source = inspect.getsource(GatewayServer.serve_http)
    assert "build_upstream_routes()" in source
