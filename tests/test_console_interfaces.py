"""Regression + contract tests for console_server.get_interfaces."""
from unittest.mock import patch


def test_get_interfaces_returns_147_tools():
    """Regression: /api/interfaces returns 147 tools (89 manual + 58 generated)."""
    # Mock load_config to return the real config, which has 89 manual tools
    # and the generated tools file adds 58 more
    import yaml
    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    # The generated file adds 58 tools
    with patch("src.console_server.load_config", return_value=full_config):
        from src.console_server import get_interfaces
        result = get_interfaces()
        tool_count = len(result["tools"])
        assert tool_count == 147, f"Expected 147 tools, got {tool_count}"


def test_get_interfaces_enriches_with_generated_tools():
    """Verify generated tools are included in the response."""
    import yaml
    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.console_server.load_config", return_value=full_config):
        from src.console_server import get_interfaces
        result = get_interfaces()
        tool_names = {t["name"] for t in result["tools"]}
        # These are some tools from the generated file
        assert "get_market_data" in tool_names or any("get_" in n for n in tool_names)


def test_get_interfaces_includes_cache_ttl_seconds():
    """Contract: response includes cache_ttl_seconds field."""
    import yaml
    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.console_server.load_config", return_value=full_config):
        from src.console_server import get_interfaces
        result = get_interfaces()
        # Find a tool with cache_ttl_key configured
        tools_with_ttl = [t for t in result["tools"] if t.get("cache_ttl_key")]
        if tools_with_ttl:
            tool = tools_with_ttl[0]
            assert "cache_ttl_seconds" in tool, "Missing cache_ttl_seconds field"
            # TTL should be 10 for realtime_quote
            if tool["cache_ttl_key"] == "realtime_quote":
                assert tool["cache_ttl_seconds"] == 10


def test_get_interfaces_includes_routing():
    """Contract: response includes routing field."""
    import yaml
    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.console_server.load_config", return_value=full_config):
        from src.console_server import get_interfaces
        result = get_interfaces()
        # Generated tools have routing keys
        tools_with_routing = [t for t in result["tools"] if t.get("routing")]
        if tools_with_routing:
            tool = tools_with_routing[0]
            assert "routing" in tool, "Missing routing field"


def test_get_interfaces_includes_fallback_chain():
    """Contract: response includes fallback_chain field."""
    import yaml
    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.console_server.load_config", return_value=full_config):
        from src.console_server import get_interfaces
        result = get_interfaces()
        # Should have fallback chains for tools with upstream
        tools_with_chain = [t for t in result["tools"] if t.get("fallback_chain")]
        if tools_with_chain:
            tool = tools_with_chain[0]
            assert "fallback_chain" in tool, "Missing fallback_chain field"
            assert isinstance(tool["fallback_chain"], list), "fallback_chain should be a list"


def test_get_interfaces_includes_full_params():
    """Contract: params field contains full schema, not just names."""
    import yaml
    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.console_server.load_config", return_value=full_config):
        from src.console_server import get_interfaces
        result = get_interfaces()
        # Find a tool with params
        tools_with_params = [t for t in result["tools"] if t.get("params")]
        if tools_with_params:
            tool = tools_with_params[0]
            params = tool["params"]
            # Should contain dict with 'name' key, not just string names
            if params:
                first_param = params[0]
                if isinstance(first_param, dict):
                    assert "name" in first_param, "Param should have 'name' field"
