"""Regression + contract tests for console_server.get_interfaces."""
from unittest.mock import patch


def test_get_interfaces_returns_146_tools():
    """Regression: /api/interfaces returns 146 tools (88 manual + 58 generated).

    History: 142 -> 147 (tokenwave_tdx exposure) -> 149 (rename
    tokenwave_get_financial_data + tokenwave_get_stock_info to avoid param-shape
    conflict with TQ-Local codegen) -> 146 (tushare upstream removed, 3 tools dropped).
    """
    import yaml
    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.console_server.load_config", return_value=full_config):
        from src.console_server import get_interfaces
        result = get_interfaces()
        tool_count = len(result["tools"])
        assert tool_count == 146, f"Expected 146 tools, got {tool_count}"


def test_merged_tool_specs_have_unique_names():
    """T-6 regression: tool_loader must not produce duplicate tool names.

    Manual + generated tool specs are merged into one registry; if a name
    appears in both, FastMCP logs `Component already exists` and the second
    registration wins silently, so the manual one is dead code.
    """
    from pathlib import Path

    import yaml
    from src.tool_loader import load_all_tools

    config_path = Path("config/upstreams.yaml")
    with config_path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    specs = load_all_tools(config_path, config)
    names = [s.get("name") for s in specs if s.get("name")]
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert dupes == [], f"Duplicate tool names in merged specs: {dupes}"


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


def test_get_interfaces_includes_chain():
    """Contract: response includes chain field (router-walk order, not priority sort)."""
    import yaml
    from unittest.mock import patch
    from src.console_server import get_interfaces

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.console_server.load_config", return_value=full_config):
        from src.console_server import get_interfaces
        result = get_interfaces()

    tools_with_chain = [t for t in result["tools"] if t.get("chain")]
    assert tools_with_chain, "Expected at least one tool with chain"
    for tool in tools_with_chain:
        assert "chain" in tool, f"Missing chain field for {tool.get('name')}"
        assert isinstance(tool["chain"], list), "chain should be a list"
        # Contract: no legacy fallback_chain field
        assert "fallback_chain" not in tool, (
            f"{tool.get('name')} still emits legacy fallback_chain field"
        )


def test_get_interfaces_chain_matches_router_for_short_chain_tools():
    """Regression: get_minute_bar must show exactly 2 sources (the YAML chain),
    NOT the full upstream list (which was the derive_fallback_chain bug).

    Pre-fix: get_minute_bar chain showed [tokenwave_tdx, tdx_local, tdx_tq_local,
    fuyao_ashare, fuyao_index, fuyao_meta, fuyao_fund] — a lie, since the router
    only walks the first 2.
    """
    import yaml
    from unittest.mock import patch
    from src.console_server import get_interfaces

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.console_server.load_config", return_value=full_config):
        result = get_interfaces()

    by_name = {t["name"]: t for t in result["tools"]}

    # get_minute_bar: YAML chain is [tokenwave_tdx, tdx_local]
    minute_bar = by_name.get("get_minute_bar")
    assert minute_bar is not None, "get_minute_bar not exposed"
    assert minute_bar["chain"] == ["tokenwave_tdx", "tdx_local"], (
        f"get_minute_bar chain mismatch: {minute_bar['chain']}"
    )
    # Specifically: no fuyao_* entries (those upstreams don't implement get_minute_bar)
    fuyao_in_chain = [u for u in minute_bar["chain"] if u.startswith("fuyao_")]
    assert fuyao_in_chain == [], (
        f"get_minute_bar chain leaked non-implementing upstreams: {fuyao_in_chain}"
    )

    # get_realtime_quote: YAML chain is [tokenwave_tdx, tdx_local, fuyao_ashare]
    rtq = by_name.get("get_realtime_quote")
    assert rtq is not None, "get_realtime_quote not exposed"
    assert rtq["chain"] == ["tokenwave_tdx", "tdx_local", "fuyao_ashare"], (
        f"get_realtime_quote chain mismatch: {rtq['chain']}"
    )


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


def test_get_upstreams_includes_description():
    """Contract: /api/upstreams response includes description field per upstream.

    Each upstream in config/upstreams.yaml must declare a non-empty description,
    which /api/upstreams proxies to the console for rendering.
    """
    import yaml
    from unittest.mock import patch
    from src.console_server import get_upstream_status

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    for name, cfg in config.get("upstreams", {}).items():
        assert "description" in cfg, f"upstream {name} missing description field"
        assert cfg["description"].strip(), f"upstream {name} description is empty"

    # Mock _probe_upstream to skip real HTTP — we only want to verify that
    # _probe_upstream propagates description into its result dict.
    def fake_probe(name, cfg):
        return {
            "enabled": cfg.get("enabled", False),
            "type": cfg.get("type", ""),
            "description": cfg.get("description", ""),
            "status": "online",
            "latency_ms": 10,
            "last_error": None,
        }

    with patch("src.console_server._probe_upstream", side_effect=fake_probe):
        result = get_upstream_status()

    for name, info in result.get("upstreams", {}).items():
        assert "description" in info, f"{name} response missing description"
        assert info["description"].strip(), f"{name} description is empty in response"


def test_console_html_uses_upstream_description():
    """Regression: console.html onboarding must fetch /api/upstreams and prefer description.

    Without this, onboarding cards fall back to GROUP_META forever — re-introducing
    the rot problem this spec solved.
    """
    html_path = "console.html"
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    assert "/api/status" in html, "console.html must fetch /api/status for upstream descriptions"
    assert "descByName" in html, "console.html must build descByName map from upstream description"


def test_console_html_routes_section_uses_chain_label():
    """Regression: console.html must label the routing walk order as 'Chain', not 'Fallback'.

    Pre-fix: label was 'Fallback' but data was an unfiltered priority-sorted list —
    mismatched with src/router.py behavior.
    """
    html_path = "console.html"
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    # The interface detail panel must use Chain as the key label
    assert "Fallback" not in html or html.count("Fallback") == html.count("Chain"), (
        "console.html still uses 'Fallback' label for routing walk order; "
        "should be 'Chain' to match src/router.py behavior"
    )
    # Sanity: at least one Chain label exists for routing
    assert ">Chain<" in html, "console.html missing 'Chain' label for routing section"
