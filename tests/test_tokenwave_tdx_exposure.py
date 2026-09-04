"""TokenWave TDX exposure verification tests.

Validates that all tokenwave_tdx tools are properly exposed via the gateway.
"""


def test_all_tokenwave_tools_in_tools_list():
    """All 8 tokenwave_tdx tools must appear in gateway tools/list.

    The 8 tokenwave_tdx-served tools (manual config) are:
    - get_realtime_quote, get_kline, get_minute_bar, get_daily_bar
    - get_block_data, get_trade_dates, get_etf_list
    - tokenwave_get_financial_data, tokenwave_get_stock_info (renamed from
      get_financial_data/get_stock_info to avoid param-shape conflict with
      TQ-Local codegen)
    """
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    tools = config.get("tools", [])
    tool_names = {t["name"] for t in tools}

    expected_tools = [
        "get_realtime_quote",
        "get_kline",
        "get_minute_bar",
        "get_daily_bar",
        "get_block_data",
        "get_trade_dates",
        "get_etf_list",
        "tokenwave_get_financial_data",
        "tokenwave_get_stock_info",
    ]

    for tool in expected_tools:
        assert tool in tool_names, f"Missing tool: {tool}"

    assert len(expected_tools) == 9


def test_realtime_quote_chain_starts_with_tokenwave():
    """get_realtime_quote routing chain must start with tokenwave_tdx."""
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    routing = config.get("routing", {})
    chain = routing.get("get_realtime_quote", {}).get("chain", [])
    assert chain and chain[0] == "tokenwave_tdx", f"Expected tokenwave_tdx first, got {chain}"


def test_minute_bar_chain_starts_with_tokenwave():
    """get_minute_bar routing chain must start with tokenwave_tdx."""
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    routing = config.get("routing", {})
    chain = routing.get("get_minute_bar", {}).get("chain", [])
    assert chain and chain[0] == "tokenwave_tdx", f"Expected tokenwave_tdx first, got {chain}"


def test_daily_bar_chain_starts_with_tokenwave():
    """get_daily_bar routing chain must start with tokenwave_tdx."""
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    routing = config.get("routing", {})
    chain = routing.get("get_daily_bar", {}).get("chain", [])
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
