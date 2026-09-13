"""registry 单元测试"""
import inspect

import pytest

from src.unihive.core.registry import (
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

    def test_normalize_date_dash_to_compact(self):
        server = _FakeServer()
        spec = {
            "name": "k",
            "description": "d",
            "routing": "k",
            "params": [
                {"name": "stock_code", "type": "str", "required": True},
                {"name": "start_time", "type": "str", "required": False},
                {"name": "end_time", "type": "str", "required": False},
            ],
        }
        fn = build_tool_function(spec, server)
        import asyncio
        asyncio.run(fn(
            stock_code="600000.SH",
            start_time="2026-09-01",
            end_time="2026/09/05",
        ))
        assert server.calls[0][1] == {
            "stock_code": "600000.SH",
            "start_time": "20260901",
            "end_time": "20260905",
        }

    def test_normalize_date_idempotent_compact(self):
        server = _FakeServer()
        spec = {
            "name": "k",
            "description": "d",
            "routing": "k",
            "params": [
                {"name": "start_date", "type": "str", "required": False},
            ],
        }
        fn = build_tool_function(spec, server)
        import asyncio
        asyncio.run(fn(start_date="20260905"))
        assert server.calls[0][1] == {"start_date": "20260905"}

    def test_normalize_csv_string_to_list(self):
        server = _FakeServer()
        spec = {
            "name": "m",
            "description": "d",
            "routing": "m",
            "params": [
                {"name": "stock_list", "type": "str", "required": True},
            ],
        }
        fn = build_tool_function(spec, server)
        import asyncio
        asyncio.run(fn(stock_list="600000.SH,000001.SZ, 688318.SH "))
        assert server.calls[0][1] == {
            "stock_list": ["600000.SH", "000001.SZ", "688318.SH"],
        }

    def test_normalize_csv_single_value(self):
        server = _FakeServer()
        spec = {
            "name": "m",
            "description": "d",
            "routing": "m",
            "params": [
                {"name": "stock_list", "type": "str", "required": True},
            ],
        }
        fn = build_tool_function(spec, server)
        import asyncio
        asyncio.run(fn(stock_list="600000.SH"))
        assert server.calls[0][1] == {"stock_list": ["600000.SH"]}

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
            asyncio.run(fn(path="/api/test"))
        assert any("DANGEROUS call" in r.message for r in caplog.records)
        assert not any("rejected" in r.message for r in caplog.records)
        assert not any("unconfirmed" in r.message for r in caplog.records)

    def test_dangerous_tool_signature_has_no_confirm(self):
        """Post-removal: dangerous tools expose only their upstream params, no confirm gate."""
        server = _FakeServer()
        spec = {
            "name": "delete_sector",
            "description": "⚠️ DANGER 删除板块 (高风险写操作，操作不可逆)",
            "routing": "delete_sector",
            "dangerous": True,
            "params": [{"name": "sector", "type": "str", "required": True}],
        }
        fn = build_tool_function(spec, server)
        sig = inspect.signature(fn)
        assert "confirm" not in sig.parameters
        assert "sector" in sig.parameters

    def test_dangerous_tool_calls_execute_cached_immediately(self):
        """Post-removal: dangerous tool dispatches to upstream without any confirm dance."""
        server = _FakeServer()
        spec = {
            "name": "delete_sector",
            "description": "⚠️ DANGER",
            "routing": "delete_sector",
            "dangerous": True,
            "params": [{"name": "sector", "type": "str", "required": True}],
        }
        fn = build_tool_function(spec, server)
        import asyncio
        result = asyncio.run(fn(sector="自选"))
        # Reaches _execute_cached (server.calls non-empty) and returns the upstream echo
        assert server.calls == [("delete_sector", {"sector": "自选"}, "delete_sector", None)]
        assert result["success"] is True
        # No refusal envelope
        assert "requires_confirmation" not in result


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
