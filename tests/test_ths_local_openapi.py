"""ThsLocalClient.fetch_openapi — 实时拉取站点根 openapi.json。

契约:
- base_url 以 /mcp 结尾时取站点根 (含尾斜杠容错), GET <root>/openapi.json;
- 200 + JSON 对象 → dict;
- 非 200 / 非 JSON / JSON 非对象 / 网络异常 / 未 start → OpenApiFetchError;
- 超时由 client 既有 timeout 配置承载 (httpx.TimeoutException ⊂ HTTPError)。
"""
from __future__ import annotations

import httpx
import pytest

from unihive.api.ths_local_gateway_client import (
    OpenApiFetchError,
    ThsLocalClient,
    ThsLocalConfig,
)

DOC = {"openapi": "3.1.0", "paths": {}, "components": {"schemas": {}}}


def _make_client(handler, *, base_url: str = "http://127.0.0.1:17281/mcp"):
    client = ThsLocalClient(ThsLocalConfig(name="THS-LOCAL-GATEWAY", base_url=base_url))
    # 直接注入 MockTransport client, 跳过 start() 的健康检查往返。
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def test_fetch_openapi_derives_site_root_from_mcp_suffix():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=DOC)

    client = _make_client(handler)
    try:
        doc = await client.fetch_openapi()
    finally:
        await client.stop()  # stop 会关 client; 状态位无所谓
    assert doc == DOC
    assert seen["url"] == "http://127.0.0.1:17281/openapi.json"


async def test_fetch_openapi_tolerates_trailing_slash():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=DOC)

    client = _make_client(handler, base_url="http://127.0.0.1:17281/mcp/")
    try:
        await client.fetch_openapi()
    finally:
        await client.stop()
    assert seen["path"] == "/openapi.json"


async def test_fetch_openapi_root_base_url_without_mcp_suffix():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=DOC)

    client = _make_client(handler, base_url="http://127.0.0.1:17281")
    try:
        await client.fetch_openapi()
    finally:
        await client.stop()
    assert seen["path"] == "/openapi.json"


async def test_fetch_openapi_non_200_raises():
    client = _make_client(lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(OpenApiFetchError, match="404"):
        await client.fetch_openapi()


async def test_fetch_openapi_non_json_body_raises():
    client = _make_client(
        lambda r: httpx.Response(200, content=b"<html>oops</html>",
                                 headers={"content-type": "text/html"})
    )
    with pytest.raises(OpenApiFetchError):
        await client.fetch_openapi()


async def test_fetch_openapi_json_array_raises():
    client = _make_client(lambda r: httpx.Response(200, json=["not", "object"]))
    with pytest.raises(OpenApiFetchError):
        await client.fetch_openapi()


async def test_fetch_openapi_network_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _make_client(handler)
    with pytest.raises(OpenApiFetchError):
        await client.fetch_openapi()


async def test_fetch_openapi_before_start_raises():
    client = ThsLocalClient(
        ThsLocalConfig(name="THS-LOCAL-GATEWAY", base_url="http://x/mcp")
    )
    with pytest.raises(OpenApiFetchError):
        await client.fetch_openapi()
