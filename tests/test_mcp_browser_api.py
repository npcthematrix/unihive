"""MCP 浏览器: 原始 tools/list 拉取 + /api/mcp-browser/* 端点。

被测的是 src/unihive/api/mcp_tools_fetch.py 与 console_api 的三段新代码。
全部走真实 httpx 路径 (MockTransport), 这样状态码 / 超时 / 每页 header 都是
真的经过 httpx 的, 而不是我们自己 stub 出来的。
"""
import inspect
import json

import httpx
import pytest
from starlette.requests import Request as StarletteRequest

from src.unihive.api.fuyao_client import FuyaoClient, FuyaoConfig
from src.unihive.api.mcp_tools_fetch import (
    ERROR_HTTP,
    ERROR_JSONRPC,
    ERROR_MALFORMED,
    ERROR_NOT_INITIALIZED,
    ERROR_TIMEOUT,
    ERROR_TRANSPORT,
    TRUNCATED_CURSOR_LOOP,
    TRUNCATED_MAX_BYTES,
    TRUNCATED_MAX_TOOLS,
    TRUNCATED_PAGE_CAP,
    fetch_all_raw_tools,
)
from src.unihive.api.ths_local_gateway_client import ThsLocalClient, ThsLocalConfig
from src.unihive.api.upstream_client import UpstreamStatus
from src.unihive.utils import console_api


# ---------------------------------------------------------------------------
# 脚手架
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_registry():
    """_upstream_registry 是模块级全局, 不清会跨用例泄漏 stub client。"""
    saved = dict(console_api._upstream_registry)
    console_api.set_upstream_registry({})
    yield
    console_api.set_upstream_registry(saved)


class _RaisingTransport(httpx.AsyncBaseTransport):
    """MockTransport 不好模拟超时, 用它把异常直接抛进 httpx 调用栈。"""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self._exc


def _client(handler, **kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


def _page(tools: list[dict], next_cursor=None) -> dict:
    result: dict = {"tools": tools}
    if next_cursor is not None:
        result["nextCursor"] = next_cursor
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def _paged_handler(pages: list[dict], record: list | None = None):
    """按调用顺序依次返回 pages。

    游标是不透明的 (上游方决定它长什么样), 所以这里不拿它当索引 —— 只按顺序
    发页, 并检查被请求的游标是否真的是上一页给的那个 (见 cursor_mismatch)。
    """
    state: dict = {"n": 0, "cursor_mismatch": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        body = json.loads(request.content.decode("utf-8"))
        cursor = body.get("params", {}).get("cursor")
        if state["n"] > 0:
            expected = pages[state["n"] - 1]["result"].get("nextCursor")
            if str(cursor) != str(expected):
                state["cursor_mismatch"] = True
        page = pages[min(state["n"], len(pages) - 1)]
        state["n"] += 1
        return httpx.Response(200, json=page)

    handler.state = state  # type: ignore[attr-defined]
    return handler


def _tool(name: str) -> dict:
    return {"name": name, "description": f"{name} 描述", "inputSchema": {"type": "object"}}


def _ths_pages() -> list[dict]:
    """THS-LOCAL-GATEWAY 实测形状: 32 / 32 / 23, 游标是 JSON 字符串。"""
    return [
        _page([_tool(f"fqgate_t{i:02d}") for i in range(32)], next_cursor="32"),
        _page([_tool(f"fqgate_t{i:02d}") for i in range(32, 64)], next_cursor="64"),
        _page([_tool(f"fqgate_t{i:02d}") for i in range(64, 87)]),
    ]


async def _fetch(handler, **kwargs):
    async with _client(handler) as client:
        return await fetch_all_raw_tools(client, "http://up.test/mcp", "test-up", **kwargs)


def _req(upstream: str) -> StarletteRequest:
    return StarletteRequest(scope={"type": "http", "path_params": {"upstream": upstream}})


# ---------------------------------------------------------------------------
# 分页
# ---------------------------------------------------------------------------


async def test_paginates_three_pages_to_87_tools():
    """核心回归: 旧 list_tools() 只返回首页 32 个。"""
    result = await _fetch(_paged_handler(_ths_pages()))

    assert result.ok is True
    assert result.pages == 3
    assert len(result.tools) == 87
    assert result.truncated is False
    assert result.error is None


async def test_page_order_is_preserved():
    """分页拼接顺序必须是上游给的顺序, 不能倒插或去重。"""
    result = await _fetch(_paged_handler(_ths_pages()))

    names = [t["name"] for t in result.tools]
    assert names == sorted(names)
    assert names[0] == "fqgate_t00"
    assert names[-1] == "fqgate_t86"
    assert len(set(names)) == 87


async def test_tools_are_passed_through_verbatim():
    """反归一化契约: 上游发什么就带回来什么, 一个键都不增删不改写。"""
    original = {
        "name": "fqgate_market_login",
        "title": "登录行情",
        "description": "登录",
        "inputSchema": {"type": "object", "properties": {"a": {"type": "string"}}},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "_meta": {"vendor": {"nested": [1, 2, 3]}},
        "totallyUnknownKey": {"deep": True},
    }
    result = await _fetch(_paged_handler([_page([original])]))

    # 逐字段相等, 含嵌套的 _meta 与未知键。数据过了一趟 JSON, 所以这里断言的是
    # "无损往返", 不是对象同一性。
    assert result.tools == [original]
    assert result.tools[0]["_meta"] == {"vendor": {"nested": [1, 2, 3]}}
    assert result.tools[0]["totallyUnknownKey"] == {"deep": True}
    assert set(result.tools[0]) == set(original)


async def test_single_page_without_cursor():
    """fuyao 形状: 无 nextCursor, 一页到底。"""
    result = await _fetch(
        _paged_handler([{"jsonrpc": "2.0", "id": 1, "result": {"tools": [_tool("a"), _tool("b")]}}])
    )

    assert result.ok is True
    assert result.pages == 1
    assert len(result.tools) == 2
    assert result.truncated is False


async def test_int_cursor_is_accepted():
    """规范没规定 nextCursor 类型, int 也得收 (收进来会归一成字符串发回去)。"""
    record: list[httpx.Request] = []
    pages = [_page([_tool("a")], next_cursor=1), _page([_tool("b")])]

    result = await _fetch(_paged_handler(pages, record=record))

    assert result.pages == 2
    assert [t["name"] for t in result.tools] == ["a", "b"]
    second = json.loads(record[1].content.decode("utf-8"))
    assert second["params"] == {"cursor": "1"}


async def test_blank_cursor_ends_pagination():
    """空串/空白串等同于 "没有下一页", 不能当成一个会循环的游标。"""
    result = await _fetch(_paged_handler([_page([_tool("a")], next_cursor="   ")]))

    assert result.pages == 1
    assert result.truncated is False


async def test_empty_tools_is_success_not_failure():
    """最重要的回归项: 上游合法地没工具 = ok=True, 与 "上游炸了" 必须可分。"""
    result = await _fetch(_paged_handler([_page([])]))

    assert result.ok is True
    assert result.tools == []
    assert result.pages == 1
    assert result.error is None
    assert result.error_kind is None


async def test_cursor_loop_stops_and_is_truncated_not_error():
    """游标不动会无限循环; 必须停下来, 且带回已经拿到的数据。"""
    pages: list[dict] = [
        _page([_tool("a")], next_cursor="same"),
        _page([_tool("b")], next_cursor="same"),
    ]
    result = await _fetch(_paged_handler(pages))

    assert result.ok is True
    assert result.truncated is True
    assert result.truncated_reason == TRUNCATED_CURSOR_LOOP
    assert [t["name"] for t in result.tools] == ["a", "b"]


async def test_page_cap_truncates_with_partial_data():
    pages = [_page([_tool(f"t{i}")], next_cursor=str(i + 1)) for i in range(10)]
    result = await _fetch(_paged_handler(pages), page_cap=3)

    assert result.ok is True
    assert result.truncated is True
    assert result.truncated_reason == TRUNCATED_PAGE_CAP
    assert result.pages == 3
    assert len(result.tools) == 3


async def test_max_tools_truncates():
    pages = [_page([_tool(f"t{i}")], next_cursor=str(i + 1)) for i in range(10)]
    result = await _fetch(_paged_handler(pages), max_tools=2)

    assert result.truncated_reason == TRUNCATED_MAX_TOOLS
    assert result.ok is True


async def test_max_bytes_truncates():
    big = {"name": "big", "description": "x" * 4000}
    pages = [_page([big], next_cursor=str(i + 1)) for i in range(10)]
    result = await _fetch(_paged_handler(pages), max_bytes=5000)

    assert result.truncated_reason == TRUNCATED_MAX_BYTES
    assert result.ok is True


async def test_last_page_is_not_reported_as_truncated():
    """页数正好等于上限但上游已经自然结束 —— 不是截断。"""
    pages = [_page([_tool("a")], next_cursor="1"), _page([_tool("b")])]
    result = await _fetch(_paged_handler(pages), page_cap=2)

    assert result.pages == 2
    assert result.truncated is False


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------


async def test_none_client_is_not_initialized():
    result = await fetch_all_raw_tools(None, "http://up.test/mcp", "test-up")

    assert result.ok is False
    assert result.error_kind == ERROR_NOT_INITIALIZED
    assert result.tools == []


async def test_http_error_status():
    handler = lambda request: httpx.Response(500, text="upstream exploded")  # noqa: E731
    result = await _fetch(handler)

    assert result.ok is False
    assert result.error_kind == ERROR_HTTP
    assert "500" in result.error
    assert "upstream exploded" in result.error


async def test_jsonrpc_error():
    handler = lambda request: httpx.Response(  # noqa: E731
        200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no such method"}}
    )
    result = await _fetch(handler)

    assert result.ok is False
    assert result.error_kind == ERROR_JSONRPC
    assert "no such method" in result.error


@pytest.mark.parametrize(
    "payload",
    [
        {"jsonrpc": "2.0", "id": 1},  # 没有 result
        {"jsonrpc": "2.0", "id": 1, "result": "nope"},  # result 不是 dict
        {"jsonrpc": "2.0", "id": 1, "result": {"tools": "nope"}},  # tools 不是 list
        [1, 2, 3],  # 顶层不是 dict
    ],
)
async def test_malformed_result(payload):
    result = await _fetch(lambda request: httpx.Response(200, json=payload))

    assert result.ok is False
    assert result.error_kind == ERROR_MALFORMED


async def test_non_json_body():
    result = await _fetch(lambda request: httpx.Response(200, text="<html>oops</html>"))

    assert result.ok is False
    assert result.error_kind == ERROR_MALFORMED


async def test_timeout_is_classified_separately_from_transport():
    async with httpx.AsyncClient(transport=_RaisingTransport(httpx.ReadTimeout("too slow"))) as c:
        result = await fetch_all_raw_tools(c, "http://up.test/mcp", "test-up")

    assert result.ok is False
    assert result.error_kind == ERROR_TIMEOUT


async def test_connect_error_is_transport():
    async with httpx.AsyncClient(transport=_RaisingTransport(httpx.ConnectError("refused"))) as c:
        result = await fetch_all_raw_tools(c, "http://up.test/mcp", "test-up")

    assert result.ok is False
    assert result.error_kind == ERROR_TRANSPORT


# ---------------------------------------------------------------------------
# headers
# ---------------------------------------------------------------------------


async def test_api_key_header_is_sent_on_every_page():
    """提升到循环外就会让第 2 页起丢掉 X-api-key —— 分页化最容易犯的错。"""
    record: list[httpx.Request] = []
    await _fetch(_paged_handler(_ths_pages(), record=record), headers={"X-api-key": "secret"})

    assert len(record) == 3
    assert all(r.headers.get("X-api-key") == "secret" for r in record)


async def test_cursor_is_sent_as_jsonrpc_param():
    record: list[httpx.Request] = []
    await _fetch(_paged_handler(_ths_pages(), record=record))

    bodies = [json.loads(r.content.decode("utf-8")) for r in record]
    assert [b["method"] for b in bodies] == ["tools/list"] * 3
    assert bodies[0]["params"] == {}
    assert bodies[1]["params"] == {"cursor": "32"}
    assert bodies[2]["params"] == {"cursor": "64"}


# ---------------------------------------------------------------------------
# 两个客户端的适配器
# ---------------------------------------------------------------------------


async def test_fuyao_adapter_sends_api_key_and_paginates():
    record: list[httpx.Request] = []
    client = FuyaoClient(FuyaoConfig(name="fuyao_meta", base_url="http://fy.test/mcp", api_key="k"))
    client._client = _client(_paged_handler(_ths_pages(), record=record))

    result = await client.list_tools_raw()

    assert len(result.tools) == 87
    assert all(r.headers.get("X-api-key") == "k" for r in record)


async def test_ths_local_adapter_returns_all_87():
    record: list[httpx.Request] = []
    client = ThsLocalClient(ThsLocalConfig(name="THS-LOCAL-GATEWAY", base_url="http://ths.test/mcp"))
    client._client = _client(_paged_handler(_ths_pages(), record=record))

    result = await client.list_tools_raw()

    assert result.ok is True
    assert result.pages == 3
    assert len(result.tools) == 87
    assert all(r.headers.get("X-api-key") is None for r in record)


# ---------------------------------------------------------------------------
# GET /api/mcp-browser/upstreams
# ---------------------------------------------------------------------------


class _NonMcpClient:
    """mootdx2 / omni 那一类: 有 list_tools() 但没有 list_tools_raw()。"""

    status = UpstreamStatus.HEALTHY
    is_available = True

    async def list_tools(self) -> list:
        return []


def test_upstreams_lists_only_mcp_clients():
    mcp = ThsLocalClient(ThsLocalConfig(name="THS-LOCAL-GATEWAY", base_url="http://x/mcp"))
    console_api.set_upstream_registry({"THS-LOCAL-GATEWAY": mcp, "mootdx2": _NonMcpClient()})

    body = console_api.get_mcp_browser_upstreams()

    assert [u["name"] for u in body["upstreams"]] == ["THS-LOCAL-GATEWAY"]
    assert body["upstreams"][0]["type"] == "ThsLocalClient"
    assert isinstance(body["timestamp"], int)


def test_upstreams_status_is_a_plain_string():
    """UpstreamStatus 是普通 Enum; 直接塞进 JSONResponse 会 TypeError → 500。"""
    mcp = ThsLocalClient(ThsLocalConfig(name="ths", base_url="http://x/mcp"))
    mcp._status = UpstreamStatus.HEALTHY
    console_api.set_upstream_registry({"ths": mcp})

    body = console_api.get_mcp_browser_upstreams()
    upstream = body["upstreams"][0]

    assert upstream["status"] == UpstreamStatus.HEALTHY.value
    assert isinstance(upstream["status"], str)
    assert upstream["available"] is True
    json.dumps(body)  # 序列化得通才说明真的没漏 Enum


# ---------------------------------------------------------------------------
# GET /api/mcp-browser/{upstream}/tools
# ---------------------------------------------------------------------------


async def test_tools_endpoint_404_for_unknown_upstream():
    console_api.set_upstream_registry({})
    status_code, body = await console_api.fetch_upstream_raw_tools("nope")

    assert status_code == 404
    assert "nope" in body["error"]


async def test_tools_endpoint_400_for_non_mcp_upstream():
    """刻意不是 501 (upstream_metrics_api 用的是 501) —— 语义不同, 见实现注释。"""
    console_api.set_upstream_registry({"mootdx2": _NonMcpClient()})
    status_code, body = await console_api.fetch_upstream_raw_tools("mootdx2")

    assert status_code == 400
    assert "not an MCP server" in body["error"]


async def test_tools_endpoint_502_when_upstream_fails():
    client = ThsLocalClient(ThsLocalConfig(name="ths", base_url="http://ths.test/mcp"))
    client._client = httpx.AsyncClient(transport=_RaisingTransport(httpx.ConnectError("refused")))
    console_api.set_upstream_registry({"ths": client})

    status_code, body = await console_api.fetch_upstream_raw_tools("ths")

    assert status_code == 502
    assert body["errorKind"] == ERROR_TRANSPORT
    assert body["upstream"] == "ths"
    assert "error" in body


async def test_tools_endpoint_200_with_empty_tools():
    """空态走 200 —— 前端靠这里区分 "上游没工具" 与 "请求失败"。"""
    client = ThsLocalClient(ThsLocalConfig(name="ths", base_url="http://ths.test/mcp"))
    client._client = _client(_paged_handler([_page([])]))
    console_api.set_upstream_registry({"ths": client})

    status_code, body = await console_api.fetch_upstream_raw_tools("ths")

    assert status_code == 200
    assert body["ok"] is True
    assert body["tools"] == []
    assert body["count"] == 0
    assert body["pages"] == 1


async def test_tools_endpoint_200_envelope_and_verbatim_tools():
    client = ThsLocalClient(ThsLocalConfig(name="ths", base_url="http://ths.test/mcp"))
    client._client = _client(_paged_handler(_ths_pages()))
    console_api.set_upstream_registry({"ths": client})

    status_code, body = await console_api.fetch_upstream_raw_tools("ths")

    assert status_code == 200
    assert body["upstream"] == "ths"
    assert body["ok"] is True
    assert body["error"] is None
    assert body["errorKind"] is None
    assert body["pages"] == 3
    assert body["truncated"] is False
    assert body["truncatedReason"] is None
    assert body["count"] == 87
    assert body["bytes"] > 0
    assert body["tools"][0] == _tool("fqgate_t00")


async def test_tools_handler_reads_upstream_from_path_params():
    client = ThsLocalClient(ThsLocalConfig(name="ths", base_url="http://ths.test/mcp"))
    client._client = _client(_paged_handler([_page([_tool("a")])]))
    console_api.set_upstream_registry({"ths": client})

    resp = await console_api.mcp_browser_tools_api(_req("ths"))
    body = json.loads(resp.body)

    assert resp.status_code == 200
    assert body["count"] == 1
    assert body["tools"] == [_tool("a")]


def test_routes_use_starlette_braces_not_angle_brackets():
    """Starlette 1.6 下 <upstream> 会编译成 params={} —— 不报错, 只是永远匹配不上。"""
    paths = [r.path for r in console_api.build_mcp_browser_routes()]

    assert paths == ["/api/mcp-browser/upstreams", "/api/mcp-browser/{upstream}/tools"]


def test_routes_are_mounted_in_gateway_server():
    """便宜的挂载守卫: 写了 handler 却忘了挂, 是本仓库出现过的失败模式。"""
    from src.unihive.gateway_server import GatewayServer

    assert "build_mcp_browser_routes" in inspect.getsource(GatewayServer.serve_http)


# ---------------------------------------------------------------------------
# list_tools() 委托 (cleanup step: 契约保持, 不再丢 nextCursor)
# ---------------------------------------------------------------------------


async def test_ths_local_list_tools_returns_all_87():
    """旧实现静默返回首页 32 个; 委托后必须返回全量。"""
    client = ThsLocalClient(ThsLocalConfig(name="ths", base_url="http://ths.test/mcp"))
    client._client = _client(_paged_handler(_ths_pages()))

    tools = await client.list_tools()

    assert len(tools) == 87
    assert tools[0]["name"] == "fqgate_t00"


async def test_fuyao_list_tools_returns_all():
    client = FuyaoClient(FuyaoConfig(name="fy", base_url="http://fy.test/mcp", api_key="k"))
    client._client = _client(_paged_handler(_ths_pages()))

    tools = await client.list_tools()

    assert len(tools) == 87


async def test_list_tools_keeps_error_contract():
    """契约不变: ok=False → 记 error + 返回 [], 不抛异常。"""
    client = ThsLocalClient(ThsLocalConfig(name="ths", base_url="http://ths.test/mcp"))
    client._client = httpx.AsyncClient(transport=_RaisingTransport(httpx.ConnectError("refused")))

    tools = await client.list_tools()

    assert tools == []
