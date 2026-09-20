"""Round-12 簇G: 注册隔离 / 健康聚合 / 启动悬空引用 / 死代码 / 路由消息。

1. register_tools_from_config: 单个 spec 注册失败 (如与保留名撞车被
   FastMCP 拒) 不得拖垮其余 288 个工具, 失败 log + skip;
2. RESERVED_TOOL_NAMES 下沉到 core.registry, validate_specs 拦截保留名,
   lint 经 T001 自动覆盖; search_stock 不再是保留名 (死手写工具已删);
3. 启动门 validate_specs(known_upstreams=...): mapping/chain 引用未配置
   上游 = 配置错误, 不再等到运行时才报 Unknown upstream;
4. aggregate_health 接收 expected 集合: 配置启用但未登记 (构造/启动失败)
   报 "unregistered"; 启用集合非空而登记为空 → unhealthy, 不再谎称 healthy;
   get_server_status.gateway 复用聚合结果;
5. Router 悬空上游的 hop 消息说明 "not running (disabled/failed to start)";
6. validate_config 上游类型表与构造器对齐 (http_jsonrpc/python/npx 已下线,
   stdio 必须给 package/command)。
"""
from __future__ import annotations

import asyncio

import pytest

from src.unihive.core import registry
from src.unihive.gateway_server import RESERVED_TOOL_NAMES, GatewayServer


# ---------------------------------------------------------------------------
# 1. 注册隔离
# ---------------------------------------------------------------------------


class _FakeMCP:
    def __init__(self, fail: frozenset[str] = frozenset()):
        self.tools: dict[str, object] = {}
        self._fail = fail

    def tool(self, annotations=None):
        def deco(fn):
            if fn.__name__ in self._fail:
                raise ValueError(f"tool {fn.__name__!r} already registered")
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakeServer:
    def __init__(self, fail=()):
        self.mcp = _FakeMCP(fail=frozenset(fail))


class TestRegistrationIsolation:
    def test_one_bad_spec_does_not_block_others(self, caplog):
        specs = [
            {"name": "good_one", "routing": "good_one"},
            {"name": "bad_one", "routing": "bad_one"},
            {"name": "good_two", "routing": "good_two"},
        ]
        server = _FakeServer(fail={"bad_one"})
        with caplog.at_level("ERROR"):
            registered = registry.register_tools_from_config(server, specs)
        assert registered == ["good_one", "good_two"]
        assert set(server.mcp.tools) == {"good_one", "good_two"}
        assert any(
            "bad_one" in rec.message
            for rec in caplog.records
            if rec.levelname == "ERROR"
        )

    def test_all_good_registers_all(self):
        specs = [{"name": "a", "routing": "a"}, {"name": "b", "routing": "b"}]
        server = _FakeServer()
        assert registry.register_tools_from_config(server, specs) == ["a", "b"]


# ---------------------------------------------------------------------------
# 2. 保留名
# ---------------------------------------------------------------------------


class TestReservedNames:
    def test_reserved_lives_in_core_registry(self):
        assert "get_health" in registry.RESERVED_TOOL_NAMES
        assert "get_server_status" in registry.RESERVED_TOOL_NAMES

    def test_validate_specs_rejects_reserved_name(self):
        specs = [{"name": "get_health", "routing": "x"}]
        errors = registry.validate_specs(
            specs, routing_config={"x": {"chain": ["f"]}},
            upstream_tool_mapping={}, strict=False,
            known_upstreams={"f"},
        )
        assert any("get_health" in e and "reserved" in e.lower() for e in errors)

    def test_search_stock_no_longer_reserved(self):
        """死手写 _register_search_stock 已删, 名字允许配置定义。"""
        assert "search_stock" not in RESERVED_TOOL_NAMES
        assert not hasattr(GatewayServer, "_register_search_stock")


# ---------------------------------------------------------------------------
# 3. 启动门悬空上游引用
# ---------------------------------------------------------------------------


class TestDanglingUpstreamRefs:
    def test_mapping_unknown_upstream_rejected(self):
        errors = registry.validate_specs(
            [{"name": "t", "routing": "t"}],
            routing_config={},
            upstream_tool_mapping={"t": {"ghost": "t"}},
            strict=False,
            known_upstreams={"fuyao_meta"},
        )
        assert any("ghost" in e and "upstream_tool_mapping" in e for e in errors)

    def test_chain_unknown_upstream_rejected(self):
        errors = registry.validate_specs(
            [{"name": "t", "routing": "t"}],
            routing_config={"t": {"chain": ["fuyao_meta", "ghost"]}},
            upstream_tool_mapping={},
            strict=False,
            known_upstreams={"fuyao_meta"},
        )
        assert any("ghost" in e and "chain" in e for e in errors)

    def test_mapping_non_dict_rejected(self):
        errors = registry.validate_specs(
            [{"name": "t", "routing": "t"}],
            routing_config={},
            upstream_tool_mapping={"t": ["oops"]},
            strict=False,
            known_upstreams=set(),
        )
        assert any("upstream_tool_mapping" in e for e in errors)

    def test_known_upstreams_none_keeps_legacy_behavior(self):
        # lint 调用不传 known_upstreams: 悬空引用由 config_lint 自己报 M001/M002
        errors = registry.validate_specs(
            [{"name": "t", "routing": "t"}],
            routing_config={},
            upstream_tool_mapping={"t": {"ghost": "t"}},
            strict=False,
        )
        assert errors == []


# ---------------------------------------------------------------------------
# 4. aggregate_health expected 集合
# ---------------------------------------------------------------------------


def _mock_client(status_value: str):
    from src.unihive.api.upstream_client import UpstreamStatus

    class MockClient:
        pass

    mc = MockClient()
    mc.status = UpstreamStatus(status_value)
    mc.is_available = status_value == "healthy"
    return mc


class TestAggregateExpectedSet:
    def test_configured_but_none_registered_is_unhealthy(self):
        result = GatewayServer.aggregate_health(
            {}, expected={"a", "b"}
        )
        assert result["status"] == "unhealthy"
        assert result["upstreams"] == {"a": "unregistered", "b": "unregistered"}

    def test_missing_expected_marks_unregistered(self):
        result = GatewayServer.aggregate_health(
            {"a": _mock_client("healthy")}, expected={"a", "b"}
        )
        assert result["status"] == "degraded"
        assert result["upstreams"]["a"] == "healthy"
        assert result["upstreams"]["b"] == "unregistered"

    def test_all_unregistered_is_unhealthy(self):
        result = GatewayServer.aggregate_health(
            {}, expected={"a"}
        )
        assert result["status"] == "unhealthy"

    def test_explicit_empty_expected_stays_healthy(self):
        assert GatewayServer.aggregate_health({}, expected=set())["status"] == "healthy"

    def test_legacy_positional_contract_unchanged(self):
        # 不传 expected: 以登记集合为准 (旧合同, lint/其他调用方依赖)
        assert GatewayServer.aggregate_health({})["status"] == "healthy"


# ---------------------------------------------------------------------------
# get_server_status / get_health 工具负载
# ---------------------------------------------------------------------------


class TestStatusToolsPayload:
    def _server(self, upstreams: dict, expected: set[str]):
        server = GatewayServer.__new__(GatewayServer)
        server.upstreams = upstreams
        server.config = {"upstreams": {n: {"enabled": True} for n in expected}}
        server.mcp = _FakeMCP()
        server._register_status_tools()
        return server

    def test_get_server_status_gateway_field_not_hardcoded(self):
        # 启用 a/b, 一个不可用 → gateway 不得硬编码 healthy
        server = self._server(
            {"a": _mock_client("healthy"), "b": _mock_client("unavailable")},
            {"a", "b"},
        )
        payload = asyncio.run(server.mcp.tools["get_server_status"]())
        assert payload["gateway"] == "degraded"
        assert payload["upstreams"]["b"]["status"] == "unavailable"

    def test_get_health_reports_unregistered_enabled_upstream(self):
        server = self._server({"a": _mock_client("healthy")}, {"a", "b"})
        payload = asyncio.run(server.mcp.tools["get_health"]())
        assert payload["status"] == "degraded"
        assert payload["upstreams"]["b"] == "unregistered"


# ---------------------------------------------------------------------------
# 5. Router 悬空消息
# ---------------------------------------------------------------------------


class TestRouterDanglingMessage:
    def test_unknown_chain_source_message_explains_why(self):
        from src.unihive.core.router import Router

        r = Router.__new__(Router)
        r.routing_config = {"t": {"chain": ["ghost"]}}
        r.upstream_tool_mapping = {}
        r.tool_specs = []
        r._tool_metadata = {}
        r.upstreams = {}
        r.proxy_settings = None
        result = asyncio.run(r.route("t", {}))
        assert result.success is False
        assert result.hops[0].source == "ghost"
        assert "not running" in result.hops[0].error
        assert "ghost" in result.hops[0].error

    def test_offline_unavailable_message_explains_why(self):
        from src.unihive.core.router import Router

        r = Router.__new__(Router)
        r.routing_config = {"t": {"chain": ["a"]}}
        r.upstream_tool_mapping = {}
        r._tool_metadata = {"t": {"data_source_type": "offline"}}
        r.proxy_settings = None
        r.upstreams = {"a": _mock_client("unavailable")}
        result = asyncio.run(r.route("t", {}))
        assert result.success is False
        assert "not running" in result.hops[0].error


# ---------------------------------------------------------------------------
# 6. validate_config 类型表对齐
# ---------------------------------------------------------------------------


class TestValidateConfigTypeTable:
    @pytest.mark.parametrize("stale", ["http_jsonrpc", "python", "npx"])
    def test_retired_types_rejected(self, stale):
        from src.unihive.utils.config_loader import validate_config

        errors = validate_config(
            {"upstreams": {"x": {"enabled": True, "type": stale}}}
        )
        assert any("unknown type" in e for e in errors)

    def test_stdio_requires_package_or_command(self):
        from src.unihive.utils.config_loader import validate_config

        errors = validate_config(
            {"upstreams": {"x": {"enabled": True, "type": "stdio"}}}
        )
        assert any("stdio" in e and ("package" in e or "command" in e) for e in errors)

    def test_stdio_with_package_passes_type_check(self):
        from src.unihive.utils.config_loader import validate_config

        errors = validate_config(
            {"upstreams": {"x": {
                "enabled": True, "type": "stdio", "package": "some-mcp"}}}
        )
        assert errors == []
