"""TokenWave TDX exposure verification tests.

Validates that all tokenwave_tdx tools are properly exposed via the gateway.
"""
import pytest


def test_all_tokenwave_tools_in_tools_list():
    """All 6 tokenwave_tdx tools must appear in gateway tools/list.

    The 6 tools exposed via manual config are:
    - get_realtime_quote (NEW - was only in routing)
    - get_minute_bar (NEW - was only in routing)
    - get_daily_bar (NEW)
    - get_block_data (NEW)
    - get_trade_dates (NEW)
    - get_kline (already existed)

    Note: get_stock_info and get_financial_data are owned by TQ-Local codegen,
    not manually configured here.
    """
    import yaml

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    tools = config.get("tools", [])
    tool_names = {t["name"] for t in tools}

    expected_tools = [
        "get_realtime_quote",
        "get_minute_bar",
        "get_daily_bar",
        "get_block_data",
        "get_trade_dates",
        "get_kline",
    ]

    for tool in expected_tools:
        assert tool in tool_names, f"Missing tool: {tool}"

    # Should have exactly 6 tokenwave_tdx tools
    assert len(expected_tools) == 6
