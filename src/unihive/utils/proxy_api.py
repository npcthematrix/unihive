"""Proxy settings HTTP handlers — GET/PUT /api/proxy/settings.

Mounted by gateway_server on the /api/* route table. Reuses existing
console_auth cookie middleware (the route lives under /api/*, so it's
already protected).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..core.proxy_adapter import Mootdx2ToTdxQuantAdapter
from ..core.proxy_settings import ProxySettings

if TYPE_CHECKING:
    from ..api.upstream_client import UpstreamClient

logger = logging.getLogger(__name__)

_ADAPTER = Mootdx2ToTdxQuantAdapter()

# Module-level state — set by gateway_server via configure_proxy_api()
_proxy_settings: ProxySettings | None = None
_upstreams: dict[str, "UpstreamClient"] | None = None


def configure_proxy_api(proxy_settings: ProxySettings, upstreams: dict[str, "UpstreamClient"]) -> None:
    """Call from gateway_server after ProxySettings + upstreams are created."""
    global _proxy_settings, _upstreams
    _proxy_settings = proxy_settings
    _upstreams = upstreams
    logger.info("proxy_api configured with proxy_settings and upstreams")


def _get_app_state(req: Request) -> Any:
    """Pull our injected app_state (set by gateway_server on each request)."""
    return req.app.state


def _coverage_table() -> list[dict[str, str]]:
    """Read-only coverage list of mootdx2 → tdx_quant tool mappings."""
    return [
        {"mootdx2_tool": k, "tdx_quant_tool": v}
        for k, v in _ADAPTER.TOOL_MAP.items()
    ]


def _tdx_quant_status_from_module() -> str:
    """Read tdx_quant upstream status from module-level _upstreams."""
    if _upstreams is None:
        return "MISSING"
    client = _upstreams.get("tdx_quant")
    if client is None:
        return "MISSING"
    status = client.status
    # Normalize to uppercase for frontend STATUS_HINTS dict
    raw = getattr(status, "value", str(status))
    return raw.upper() if raw else "MISSING"


async def get_proxy_settings_handler(req: Request, app_state=None) -> JSONResponse:
    """GET /api/proxy/settings — current state + tdx status + coverage.

    接受 app_state 参数 (测试 fixture 注入), 优先于 module-level 状态。
    """
    ps, upstreams = _resolve_state(app_state)
    if ps is None:
        return JSONResponse(
            {"error": "proxy not configured"},
            status_code=500,
        )
    return JSONResponse(
        {
            "enabled": ps.is_enabled(),
            "tdx_quant_status": _tdx_quant_status(upstreams),
            "coverage": _coverage_table(),
        }
    )


async def put_proxy_settings_handler(req: Request, app_state=None) -> JSONResponse:
    """PUT /api/proxy/settings — body {enabled: bool}, persists + triggers reload."""
    ps, upstreams = _resolve_state(app_state)
    if ps is None:
        return JSONResponse(
            {"error": "proxy not configured"},
            status_code=500,
        )
    try:
        body = await req.json()
    except json.JSONDecodeError:
        return JSONResponse(
            {"error": "invalid JSON body"}, status_code=400
        )

    if "enabled" not in body:
        return JSONResponse(
            {"error": "missing required field: enabled"}, status_code=400
        )

    enabled_val = body["enabled"]
    if not isinstance(enabled_val, bool):
        return JSONResponse(
            {"error": f"enabled must be bool, got {type(enabled_val).__name__}"},
            status_code=400,
        )

    ps.set_enabled(enabled_val)
    logger.info(f"proxy settings updated: enabled={enabled_val}")
    return JSONResponse(
        {
            "enabled": ps.is_enabled(),
            "tdx_quant_status": _tdx_quant_status(upstreams),
            "coverage": _coverage_table(),
        }
    )


def _resolve_state(app_state):
    """从 app_state (优先) 或 module-level 拿 (proxy_settings, upstreams)"""
    if app_state is not None:
        ps = getattr(app_state, "proxy_settings", None)
        upstreams = getattr(app_state, "upstreams", None)
        return ps, upstreams
    return _proxy_settings, _upstreams


def _tdx_quant_status(upstreams):
    """Read tdx_quant upstream status (from upstreams dict, may be module-level)."""
    if upstreams is None:
        return "MISSING"
    client = upstreams.get("tdx_quant")
    if client is None:
        return "MISSING"
    status = client.status
    raw = getattr(status, "value", str(status))
    return raw.upper() if raw else "MISSING"


def build_proxy_routes() -> list:
    """Build proxy route list for gateway to mount."""
    from starlette.routing import Route

    async def _get(req: Request):
        return await get_proxy_settings_handler(req)

    async def _put(req: Request):
        return await put_proxy_settings_handler(req)

    return [
        Route("/api/proxy/settings", _get),
        Route("/api/proxy/settings", _put, methods=["PUT"]),
    ]
