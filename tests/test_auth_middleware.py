"""BearerTokenMiddleware 单元测试。

用最小 ASGI 应用做端到端断言：直接构造中间件包住一个 echo app，
发送模拟 HTTP 请求，验证响应状态码和 body。
"""
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.auth_middleware import BearerTokenMiddleware


def _echo_mcp_app():
    """最小 MCP-like 端点：返回路径和 headers，供测试观察是否到达下游。"""

    async def mcp_endpoint(request: Request):
        return JSONResponse({"path": request.url.path, "auth": request.headers.get("authorization")})

    async def health(request: Request):
        return PlainTextResponse("ok")

    async def root(request: Request):
        return PlainTextResponse("hi")

    async def api_status(request: Request):
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[
        Route("/mcp", mcp_endpoint),
        Route("/health", health),
        Route("/", root),
        Route("/api/status", api_status),
    ])
    return app


@pytest.fixture
def client():
    app = BearerTokenMiddleware(_echo_mcp_app(), token="secret123")
    return TestClient(app)


def test_missing_authorization_returns_401(client):
    r = client.get("/mcp")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")
    assert r.json() == {"error": "missing_authorization"}


def test_wrong_token_returns_401(client):
    r = client.get("/mcp", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert r.json() == {"error": "invalid_token"}


def test_correct_token_passes_through(client):
    r = client.get("/mcp", headers={"Authorization": "Bearer secret123"})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "/mcp"
    assert body["auth"] == "Bearer secret123"


def test_health_bypasses_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.text == "ok"


def test_root_bypasses_auth(client):
    r = client.get("/")
    assert r.status_code == 200


def test_api_prefix_bypasses_auth(client):
    r = client.get("/api/status")
    assert r.status_code == 200


def test_case_insensitive_scheme(client):
    r = client.get("/mcp", headers={"Authorization": "bearer secret123"})
    assert r.status_code == 200


def test_malformed_header_returns_401(client):
    r = client.get("/mcp", headers={"Authorization": "Token xxx"})
    assert r.status_code == 401
    assert r.json() == {"error": "missing_authorization"}


def test_lifespan_scope_passes_through():
    """lifespan 事件不应被中间件拦截。"""
    app = BearerTokenMiddleware(_echo_mcp_app(), token="secret123")
    # TestClient 上下文触发 lifespan；不应抛错
    with TestClient(app):
        pass  # 启动 + 关闭 lifespan 无异常即通过
