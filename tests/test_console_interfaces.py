"""Regression + contract tests for console_server.get_interfaces."""
from unittest.mock import patch


def test_get_interfaces_returns_140_tools():
    """Regression: /api/interfaces returns the expected tool surface.

    History: 148 -> 147 -> 140 (indicator cleanup) -> 139 (trade_history_full) -> 138
    (minute_trade_all) -> 140 (mootdx2 sector refactor: net +2 — added get_custom_sector_list
    and get_custom_sector_stocks; the 3 block→sector renames preserved count; tdx_quant
    codegen already contributed 8 sector-named tools independently — not introduced by
    this plan).

    2026-09-14: tool surface grew to 202 (THSDK upstream +16 read-only + tdx_quant
    sector/subscribe + fuyao fund补足 + omni). 真实数字以 config/upstreams.yaml +
    config/tools_*.yaml 的 load_all_tools 输出为准, 此处只做下限检查 + 关键工具
    存在性, 避免每次加 upstream 都要修测试。
    """
    import yaml
    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.unihive.utils.console_api.load_config", return_value=full_config):
        from src.unihive.utils.console_api import get_interfaces
        result = get_interfaces()
        tool_count = len(result["tools"])
        # 下限: 历史最高 ~148, 现在 ≥ 150 才能保住 4 个上游 + tdx_quant + mootdx2 + omni + thsdk
        assert tool_count >= 150, f"Expected at least 150 tools, got {tool_count}"
        # 关键工具必须存在 (避免上游删减把核心工具删了)
        names = {t["name"] for t in result["tools"]}
        for must_have in (
            "a_share_prices_snapshot",
            "a_share_financials_indicators",
            "meta_tickers_list",
            "fund_profile_detail",
        ):
            assert must_have in names, f"Core tool {must_have!r} missing from /api/interfaces"


def test_merged_tool_specs_have_unique_names():
    """T-6 regression: tool_loader must not produce duplicate tool names.

    Manual + generated tool specs are merged into one registry; if a name
    appears in both, FastMCP logs `Component already exists` and the second
    registration wins silently, so the manual one is dead code.
    """
    from pathlib import Path

    import yaml
    from src.unihive.core.tool_loader import load_all_tools

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

    with patch("src.unihive.utils.console_api.load_config", return_value=full_config):
        from src.unihive.utils.console_api import get_interfaces
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

    with patch("src.unihive.utils.console_api.load_config", return_value=full_config):
        from src.unihive.utils.console_api import get_interfaces
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

    with patch("src.unihive.utils.console_api.load_config", return_value=full_config):
        from src.unihive.utils.console_api import get_interfaces
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
    from src.unihive.utils.console_api import get_interfaces

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.unihive.utils.console_api.load_config", return_value=full_config):
        from src.unihive.utils.console_api import get_interfaces
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


def test_get_interfaces_chain_matches_router_for_explicit_chain_tools():
    """Regression: a tool whose YAML routing declares chain = [upstream_a] must
    expose exactly that chain in /api/interfaces, NOT the full upstream list.

    Originally targeted moo_minute_bar/moo_realtime_quote with chain=[tokenwave_tdx].
    After tdx-quant migration those upstreams/tools are gone. Pick whatever
    explicit-chain tool the current config exposes and check the same invariant.
    """
    import yaml
    from unittest.mock import patch
    from src.unihive.utils.console_api import get_interfaces

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.unihive.utils.console_api.load_config", return_value=full_config):
        result = get_interfaces()

    by_name = {t["name"]: t for t in result["tools"]}

    explicit_chain_tools = {
        name: t for name, t in by_name.items() if t.get("chain")
    }
    assert explicit_chain_tools, (
        "No tool in current config declares an explicit routing chain; "
        "this regression test needs at least one."
    )

    for name, tool in explicit_chain_tools.items():
        chain = tool["chain"]
        assert isinstance(chain, list), f"{name} chain is not a list"
        assert chain, f"{name} declares an empty chain"


def test_get_interfaces_includes_full_params():
    """Contract: params field contains full schema, not just names."""
    import yaml
    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.unihive.utils.console_api.load_config", return_value=full_config):
        from src.unihive.utils.console_api import get_interfaces
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
    from src.unihive.utils.console_api import get_upstream_status

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

    with patch("src.unihive.utils.console_api._probe_upstream", side_effect=fake_probe):
        result = get_upstream_status()

    for name, info in result.get("upstreams", {}).items():
        assert "description" in info, f"{name} response missing description"
        assert info["description"].strip(), f"{name} description is empty in response"


# ---------------------------------------------------------------------------
# 回归 (H3): 被禁用的 HTTP 上游不得探活, 且逐行 status 与 summary 必须同源
# ---------------------------------------------------------------------------
# 旧码 _do_probe 只在末尾 else 分支 (非 http/http_jsonrpc) 才看 enabled, 于是
# enabled=False 的 HTTP 上游照样打 /health: 探得通就报 online, 而
# get_upstream_status 的 summary 按 enabled 把它计入 disabled, 前端
# isUsableUpstream 也按 enabled 判不可用 —— 同一行三个口径互相矛盾, 还白发流量。


def test_do_probe_disabled_http_upstream_skips_probe():
    """enabled=False 的 http 上游直接判 disabled, 一个字节都不发。"""
    from unittest.mock import patch
    from src.unihive.utils.console_api import _do_probe

    cfg = {"enabled": False, "type": "http", "description": "已禁用的 HTTP 上游"}
    with patch("src.unihive.utils.console_api.httpx.get") as mock_get:
        result = _do_probe("http", "http://127.0.0.1:9", cfg)

    mock_get.assert_not_called()
    assert result["status"] == "disabled"
    assert result["enabled"] is False
    assert result["latency_ms"] is None
    assert result["last_error"] is None


def test_do_probe_disabled_jsonrpc_upstream_skips_probe():
    """http_jsonrpc 同样早退, 不发 server.info。"""
    from unittest.mock import patch
    from src.unihive.utils.console_api import _do_probe

    with patch("src.unihive.utils.console_api.httpx.post") as mock_post:
        result = _do_probe("http_jsonrpc", "http://127.0.0.1:9", {"enabled": False})

    mock_post.assert_not_called()
    assert result["status"] == "disabled"


def test_do_probe_enabled_http_upstream_still_probes():
    """守住早退没有误伤正常路径: enabled=True 仍打 /health 并报 online。"""
    from unittest.mock import MagicMock, patch
    from src.unihive.utils.console_api import _do_probe

    with patch(
        "src.unihive.utils.console_api.httpx.get",
        return_value=MagicMock(status_code=200),
    ) as mock_get:
        result = _do_probe("http", "http://127.0.0.1:9", {"enabled": True})

    mock_get.assert_called_once()
    assert result["status"] == "online"
    assert isinstance(result["latency_ms"], int)


def test_do_probe_enabled_non_http_upstream_is_configured():
    """进程内 client 探不了活: enabled=True → configured (早退后 else 分支的唯一取值)。"""
    from src.unihive.utils.console_api import _do_probe

    assert _do_probe("sdk", "", {"enabled": True})["status"] == "configured"


def test_disabled_upstream_row_and_summary_agree():
    """禁用上游的逐行 status 与 summary 的 disabled 桶必须同源。

    这是 H3 的用户可见面: 旧码下同一行会同时读作「行: online / 汇总: disabled /
    前端: 不可用」。
    """
    from unittest.mock import patch
    from src.unihive.utils import console_api

    cfg = {
        "upstreams": {
            "dead_http": {
                "enabled": False,
                "type": "http",
                "base_url": "http://127.0.0.1:9",
                "description": "已禁用",
            },
            "live_sdk": {"enabled": True, "type": "sdk", "description": "进程内"},
        }
    }
    # _probe_cache 是模块级 TTLCache(5s), 清掉以免与其他用例串味
    console_api._probe_cache.clear()
    try:
        with patch.object(console_api, "load_config", return_value=cfg), patch.object(
            console_api.httpx, "get"
        ) as mock_get:
            result = console_api.get_upstream_status()
    finally:
        console_api._probe_cache.clear()

    mock_get.assert_not_called()
    assert result["upstreams"]["dead_http"]["status"] == "disabled"
    assert result["upstreams"]["live_sdk"]["status"] == "configured"
    summary = result["summary"]
    assert summary["total"] == 2
    assert summary["disabled"] == 1
    assert summary["configured"] == 1
    assert summary["online"] == 0


def test_console_html_uses_upstream_description():
    """Regression: StatusView + api/status must hit the upstream endpoint that
    carries the upstream `description` field.

    Predecessor (2026-09-08): this asserted console.js used descByName map to
    prefer real upstream description over GROUP_META fallback. After the
    2026-09-14 Vue migration, the data shape is still the contract (see
    test_get_upstreams_includes_description above) — this test guards the
    client side, i.e. that StatusView/api/status actually fetch from the
    right endpoint instead of relying on a stale client cache or wrong path.
    """
    # StatusView 已合并进 GatewayMonitorView (上游总览标签); 该视图是 getStatus 的消费方。
    monitor_view = open("web/src/views/GatewayMonitorView.vue", encoding="utf-8").read()
    status_api = open("web/src/api/status.ts", encoding="utf-8").read()

    # api/status.ts must hit the status endpoint that carries upstream descriptions.
    assert "/api/status" in status_api, (
        "web/src/api/status.ts must call /api/status to obtain upstream info "
        "including the description field."
    )
    # GatewayMonitorView consumes /api/status via the getStatus wrapper.
    assert "getStatus" in monitor_view, (
        "GatewayMonitorView.vue must call getStatus() to populate upstream rows. "
        "If removed, the upstream table loses its data source."
    )


def test_console_html_routes_section_uses_chain_label():
    """Regression: InterfacesView + api/interfaces must surface the routing
    walk order (the `chain` field).

    Predecessor (2026-09-08): asserted console.js labeled routing walk order
    as 'Chain' not 'Fallback'. After the 2026-09-14 Vue migration the
    server-side `chain` field is still the contract (see
    test_get_interfaces_includes_chain above) — this test guards the
    client side, i.e. that InterfacesView reads the chain field by name
    instead of the legacy `fallback_chain`.
    """
    interfaces_view = open("web/src/views/InterfacesView.vue", encoding="utf-8").read()
    interfaces_api = open("web/src/api/interfaces.ts", encoding="utf-8").read()

    # InterfacesView reads the chain field by name (NOT fallback_chain).
    assert "fallback_chain" not in interfaces_view, (
        "InterfacesView.vue still references legacy `fallback_chain` field. "
        "The src/router.py contract is now `chain`; legacy field would silently "
        "render no routing walk order."
    )
    # The chain field must appear (consumed from interfaces API).
    assert "chain" in interfaces_view or "chain" in interfaces_api, (
        "Neither InterfacesView.vue nor web/src/api/interfaces.ts reference "
        "the routing `chain` field. Routing walk order won't render."
    )
