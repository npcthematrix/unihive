"""registry 单元测试"""
import inspect

import pytest

from src.registry import (
    _DEFAULT_MAP,
    _TYPE_MAP,
    build_signature,
    build_tool_function,
    register_tools_from_config,
    validate_specs,
)


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakeServer:
    def __init__(self):
        self.mcp = _FakeMCP()
        self.calls = []

    async def _execute_cached(self, name, params, route_key, ttl_key):
        self.calls.append((name, params, route_key, ttl_key))
        return {"success": True, "echo": (name, params, route_key, ttl_key)}


class TestBuildSignature:
    def test_required_only(self):
        sig = build_signature([{"name": "symbol", "type": "str", "required": True}])
        assert "symbol" in sig.parameters
        assert sig.parameters["symbol"].default is inspect.Parameter.empty
        assert sig.parameters["symbol"].annotation is str

    def test_optional_with_default(self):
        sig = build_signature([{"name": "limit", "type": "int", "required": False}])
        assert sig.parameters["limit"].default == 0
        assert sig.parameters["limit"].annotation is int

    def test_unknown_type_falls_back_to_str(self):
        sig = build_signature([{"name": "x", "type": "weird", "required": True}])
        assert sig.parameters["x"].annotation is str


class TestBuildToolFunction:
    def test_signature_and_doc(self):
        server = _FakeServer()
        spec = {
            "name": "get_x",
            "description": "获取 X",
            "routing": "get_x",
            "params": [{"name": "symbol", "type": "str", "required": True}],
        }
        fn = build_tool_function(spec, server)
        assert fn.__name__ == "get_x"
        assert fn.__doc__ == "获取 X"
        sig = inspect.signature(fn)
        assert "symbol" in sig.parameters
        assert sig.parameters["symbol"].annotation is str

    def test_invocation_dispatches_to_execute_cached(self):
        server = _FakeServer()
        spec = {
            "name": "quote",
            "description": "d",
            "routing": "get_quote",
            "params": [{"name": "symbol", "type": "str", "required": True}],
            "cache_ttl_key": "realtime_quote",
        }
        fn = build_tool_function(spec, server)
        import asyncio
        result = asyncio.run(fn(symbol="sh600519"))
        assert result["success"] is True
        assert server.calls == [
            ("quote", {"symbol": "sh600519"}, "get_quote", "realtime_quote")
        ]

    def test_normalize_code(self):
        server = _FakeServer()
        spec = {
            "name": "q",
            "description": "d",
            "routing": "q",
            "params": [
                {"name": "symbol", "type": "str", "required": True, "normalize": "code"}
            ],
        }
        fn = build_tool_function(spec, server)
        import asyncio
        asyncio.run(fn(symbol="600519"))
        assert server.calls[0][1] == {"symbol": "sh600519"}

    def test_optional_param_skipped_when_empty(self):
        server = _FakeServer()
        spec = {
            "name": "k",
            "description": "d",
            "routing": "k",
            "params": [
                {"name": "symbol", "type": "str", "required": True},
                {"name": "start_date", "type": "str", "required": False},
            ],
        }
        fn = build_tool_function(spec, server)
        import asyncio
        asyncio.run(fn(symbol="sh600519", start_date=""))
        assert server.calls[0][1] == {"symbol": "sh600519"}

    def test_dangerous_logs_warning(self, caplog):
        server = _FakeServer()
        spec = {
            "name": "dangerous_call",
            "description": "d",
            "routing": "tdx_call",
            "dangerous": True,
            "params": [{"name": "path", "type": "str", "required": True}],
        }
        fn = build_tool_function(spec, server)
        import asyncio
        with caplog.at_level("WARNING"):
            asyncio.run(fn(path="/api/test", confirm=True))
        assert any("DANGEROUS call" in r.message for r in caplog.records)


class TestDangerousConfirmation:
    """危险工具必须显式确认后才触达上游。"""

    SPEC = {
        "name": "delete_sector",
        "description": "⚠️ DANGER 删除板块",
        "routing": "delete_sector",
        "dangerous": True,
        "params": [{"name": "sector", "type": "str", "required": True}],
    }

    def _run(self, server, **kwargs):
        import asyncio
        fn = build_tool_function(self.SPEC, server)
        return asyncio.run(fn(**kwargs))

    def test_unconfirmed_call_does_not_reach_upstream(self):
        server = _FakeServer()
        result = self._run(server, sector="自选")
        assert server.calls == []
        assert result["success"] is False
        assert result["requires_confirmation"] is True

    def test_unconfirmed_error_names_the_tool_and_the_remedy(self):
        server = _FakeServer()
        result = self._run(server, sector="自选")
        assert "delete_sector" in result["error"]
        assert "confirm=true" in result["error"]

    def test_confirmed_call_reaches_upstream(self):
        server = _FakeServer()
        result = self._run(server, sector="自选", confirm=True)
        assert result["success"] is True
        assert server.calls == [("delete_sector", {"sector": "自选"}, "delete_sector", None)]

    def test_confirm_flag_is_not_forwarded_upstream(self):
        server = _FakeServer()
        self._run(server, sector="自选", confirm=True)
        assert "confirm" not in server.calls[0][1]

    def test_confirm_param_exposed_in_signature_as_optional(self):
        fn = build_tool_function(self.SPEC, _FakeServer())
        sig = inspect.signature(fn)
        assert sig.parameters["confirm"].default is False
        assert sig.parameters["confirm"].annotation is bool

    def test_docstring_tells_model_to_get_user_approval(self):
        fn = build_tool_function(self.SPEC, _FakeServer())
        assert "⚠️ DANGER 删除板块" in fn.__doc__
        assert "confirm=true" in fn.__doc__

    def test_safe_tool_gets_no_confirm_param(self):
        spec = {
            "name": "get_quote",
            "description": "行情",
            "routing": "get_quote",
            "params": [{"name": "symbol", "type": "str", "required": True}],
        }
        fn = build_tool_function(spec, _FakeServer())
        assert "confirm" not in inspect.signature(fn).parameters

    def test_blocked_call_is_logged(self, caplog):
        server = _FakeServer()
        with caplog.at_level("WARNING"):
            self._run(server, sector="自选")
        assert any("unconfirmed" in r.message for r in caplog.records)


class TestRegisterToolsFromConfig:
    def test_registers_all(self):
        server = _FakeServer()
        specs = [
            {"name": "a", "description": "A", "routing": "a", "params": []},
            {"name": "b", "description": "B", "routing": "b", "params": []},
        ]
        names = register_tools_from_config(server, specs)
        assert names == ["a", "b"]
        assert set(server.mcp.tools.keys()) == {"a", "b"}

    def test_skips_dangerous_when_disabled(self):
        server = _FakeServer()
        specs = [
            {"name": "safe", "description": "S", "routing": "safe", "params": []},
            {"name": "risky", "description": "R", "routing": "risky",
             "params": [], "dangerous": True},
        ]
        names = register_tools_from_config(server, specs, disable_dangerous=True)
        assert names == ["safe"]
        assert "risky" not in server.mcp.tools

    def test_includes_dangerous_by_default(self):
        server = _FakeServer()
        specs = [
            {"name": "risky", "description": "R", "routing": "risky",
             "params": [], "dangerous": True},
        ]
        names = register_tools_from_config(server, specs)
        assert names == ["risky"]


class TestValidateSpecs:
    def test_all_valid(self):
        specs = [
            {"name": "a", "routing": "a"},
            {"name": "b", "routing": "b"},
        ]
        routing = {"a": {"chain": []}}
        mapping = {"b": {"x": "y"}}
        errors = validate_specs(specs, routing, mapping, strict=False)
        assert errors == []

    def test_missing_routing(self):
        specs = [{"name": "a", "routing": "missing"}]
        errors = validate_specs(specs, {}, {}, strict=False)
        assert any("not in routing" in e for e in errors)

    def test_missing_name(self):
        specs = [{"routing": "x"}]
        errors = validate_specs(specs, {"x": {}}, {}, strict=False)
        assert any("missing 'name'" in e for e in errors)

    def test_missing_routing_field(self):
        specs = [{"name": "a"}]
        errors = validate_specs(specs, {}, {}, strict=False)
        assert any("missing 'routing'" in e for e in errors)

    def test_strict_raises(self):
        specs = [{"name": "a", "routing": "missing"}]
        with pytest.raises(ValueError, match="validation failed"):
            validate_specs(specs, {}, {}, strict=True)
