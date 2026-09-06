"""TokenWave TDX exposure verification tests.

Validates that all tokenwave_tdx-served tools are properly exposed via the
gateway. The moo_-prefixed tools route to tokenwave_tdx as their primary
upstream.
"""


def test_all_tokenwave_tools_in_tools_list():
    """All 9 moo_-prefixed tools (served by tokenwave_tdx) must be exposed.

    The moo_ prefix avoids param-shape conflicts with TQ-Local codegen and
    TDX 直通 tools (e.g. get_kline, get_etf_list). These 9 map 1:1 to
    tokenwave_tdx-served endpoints:
    - moo_realtime_quote, moo_kline, moo_minute_bar, moo_daily_bar
    - moo_block_data, moo_trade_dates, moo_etf_list
    - moo_get_financial_data, moo_get_stock_info
    """
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    tools = config.get("tools", [])
    tool_names = {t["name"] for t in tools}

    expected_tools = [
        "moo_realtime_quote",
        "moo_kline",
        "moo_minute_bar",
        "moo_daily_bar",
        "moo_block_data",
        "moo_trade_dates",
        "moo_etf_list",
        "moo_get_financial_data",
        "moo_get_stock_info",
    ]

    for tool in expected_tools:
        assert tool in tool_names, f"Missing tool: {tool}"

    assert len(expected_tools) == 9


def test_realtime_quote_chain_starts_with_tokenwave():
    """moo_realtime_quote routing chain must start with tokenwave_tdx."""
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    routing = config.get("routing", {})
    chain = routing.get("moo_realtime_quote", {}).get("chain", [])
    assert chain and chain[0] == "tokenwave_tdx", f"Expected tokenwave_tdx first, got {chain}"


def test_minute_bar_chain_starts_with_tokenwave():
    """moo_minute_bar routing chain must start with tokenwave_tdx."""
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    routing = config.get("routing", {})
    chain = routing.get("moo_minute_bar", {}).get("chain", [])
    assert chain and chain[0] == "tokenwave_tdx", f"Expected tokenwave_tdx first, got {chain}"


def test_daily_bar_chain_starts_with_tokenwave():
    """moo_daily_bar routing chain must start with tokenwave_tdx."""
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    routing = config.get("routing", {})
    chain = routing.get("moo_daily_bar", {}).get("chain", [])
    assert chain and chain[0] == "tokenwave_tdx", f"Expected tokenwave_tdx first, got {chain}"


def test_get_kline_description_mentions_tokenwave():
    """get_kline description must mention tokenwave_tdx as primary."""
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    tools = config.get("tools", [])
    tool = next((t for t in tools if t.get("name") == "get_kline"), None)
    assert tool is not None, "get_kline tool not found"
    desc = tool.get("description", "")
    assert "tokenwave_tdx" in desc, f"get_kline description should mention tokenwave_tdx: {desc}"


def test_get_etf_list_description_mentions_tokenwave():
    """get_etf_list description must mention tokenwave_tdx as primary."""
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    tools = config.get("tools", [])
    tool = next((t for t in tools if t.get("name") == "get_etf_list"), None)
    assert tool is not None, "get_etf_list tool not found"
    desc = tool.get("description", "")
    assert "tokenwave_tdx" in desc, f"get_etf_list description should mention tokenwave_tdx: {desc}"
