"""Proxy API handlers: GET /api/proxy/settings, PUT /api/proxy/settings."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request

from src.unihive.core.proxy_settings import ProxySettings
from src.unihive.utils.proxy_api import (
    get_proxy_settings_handler,
    put_proxy_settings_handler,
)


@pytest.fixture
def tmp_yaml(tmp_path):
    p = tmp_path / "proxy_settings.yaml"
    p.write_text("proxy:\n  enabled: false\n", encoding="utf-8")
    return p


@pytest.fixture
def proxy_settings(tmp_yaml):
    return ProxySettings(tmp_yaml)


@pytest.fixture
def fake_tdx_client():
    """Mock tdx_quant client with is_available + status."""
    client = MagicMock()
    client.status.value = "HEALTHY"
    client.is_available = True
    return client


@pytest.fixture
def app_state(proxy_settings, fake_tdx_client):
    """A mutable container that handlers can read/write."""
    state = MagicMock()
    state.proxy_settings = proxy_settings
    state.upstreams = {"tdx_quant": fake_tdx_client}
    return state


def _make_request(method="GET", body=None):
    """Build a Starlette Request with given method/body."""
    if body is None:
        body_bytes = b""
    else:
        body_bytes = json.dumps(body).encode("utf-8")
    return Request(
        scope={
            "type": "http",
            "method": method,
            "path": "/api/proxy/settings",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        },
        receive=AsyncMock(
            return_value={"type": "http.request", "body": body_bytes, "more_body": False}
        ),
    )


class TestGetProxySettings:
    @pytest.mark.asyncio
    async def test_get_returns_enabled_false_and_tdx_status(self, app_state):
        req = _make_request("GET")
        resp = await get_proxy_settings_handler(req, app_state)
        body = json.loads(resp.body)
        assert body["enabled"] is False
        assert body["tdx_quant_status"] == "HEALTHY"
        # 8 个: get_batch_quote 2026-11-02 重新接入 get_market_snapshot_batch
        assert len(body["coverage"]) == 8
        assert {"mootdx2_tool": "batch_bars", "tdx_quant_tool": "get_market_data"} in body["coverage"]
        assert {
            "mootdx2_tool": "get_batch_quote",
            "tdx_quant_tool": "get_market_snapshot_batch",
        } in body["coverage"]
        # 验证 coverage 是只读说明, 不应包含可编辑字段
        for entry in body["coverage"]:
            assert "mootdx2_tool" in entry
            assert "tdx_quant_tool" in entry

    @pytest.mark.asyncio
    async def test_get_when_tdx_unavailable_reports_unavailable(self, app_state):
        app_state.upstreams["tdx_quant"].status.value = "UNAVAILABLE"
        app_state.upstreams["tdx_quant"].is_available = False
        req = _make_request("GET")
        resp = await get_proxy_settings_handler(req, app_state)
        body = json.loads(resp.body)
        assert body["tdx_quant_status"] == "UNAVAILABLE"


class TestPutProxySettings:
    @pytest.mark.asyncio
    async def test_put_persists_enabled_true(self, app_state, tmp_yaml):
        req = _make_request("PUT", body={"enabled": True})
        resp = await put_proxy_settings_handler(req, app_state)
        body = json.loads(resp.body)
        assert body["enabled"] is True
        # 验证 yaml 落盘
        import yaml
        data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
        assert data["proxy"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_put_persists_enabled_false(self, app_state, tmp_yaml):
        # 1) 先 enable
        app_state.proxy_settings.set_enabled(True)
        # 2) 再 disable
        req = _make_request("PUT", body={"enabled": False})
        resp = await put_proxy_settings_handler(req, app_state)
        body = json.loads(resp.body)
        assert body["enabled"] is False
        # 验证 yaml 落盘
        import yaml
        data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
        assert data["proxy"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_put_rejects_non_bool_enabled(self, app_state):
        req = _make_request("PUT", body={"enabled": "true"})
        resp = await put_proxy_settings_handler(req, app_state)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_put_rejects_missing_enabled(self, app_state):
        req = _make_request("PUT", body={})
        resp = await put_proxy_settings_handler(req, app_state)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_put_triggers_router_hot_reload_via_settings(self, app_state):
        """PUT 改完后, ProxySettings.is_enabled() 应立即返回新值."""
        req = _make_request("PUT", body={"enabled": True})
        await put_proxy_settings_handler(req, app_state)
        assert app_state.proxy_settings.is_enabled() is True

    @pytest.mark.asyncio
    async def test_put_rejects_malformed_json(self, app_state):
        """PUT with invalid JSON body should return 400."""
        req = Request(
            scope={
                "type": "http",
                "method": "PUT",
                "path": "/api/proxy/settings",
                "headers": [(b"content-type", b"application/json")],
                "query_string": b"",
            },
            receive=AsyncMock(
                return_value={"type": "http.request", "body": b"{invalid json}", "more_body": False}
            ),
        )
        resp = await put_proxy_settings_handler(req, app_state)
        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert "invalid JSON body" in body["error"]


class TestBuildProxyRoutes:
    """build_proxy_routes() returns a list with GET + PUT Route entries."""

    def test_build_proxy_routes_returns_two_routes(self):
        from src.unihive.utils.proxy_api import build_proxy_routes

        routes = build_proxy_routes()
        assert isinstance(routes, list)
        assert len(routes) == 2
        # First is GET, second is PUT
        assert routes[0].path == "/api/proxy/settings"
        assert "GET" in routes[0].methods
        assert routes[1].path == "/api/proxy/settings"
        assert "PUT" in routes[1].methods
