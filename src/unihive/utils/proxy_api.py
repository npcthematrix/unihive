"""Proxy settings HTTP handlers — GET/PUT /api/proxy/settings.

Mounted by gateway_server on the /api/* route table. Reuses existing
console_auth cookie middleware (the route lives under /api/*, so it's
already protected).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..core.proxy_adapter import Mootdx2ToTdxQuantAdapter

logger = logging.getLogger(__name__)

_ADAPTER = Mootdx2ToTdxQuantAdapter()


def _get_app_state(req: Request) -> Any:
    """Pull our injected app_state (set by gateway_server on each request)."""
    return req.app.state


def _coverage_table() -> list[dict[str, str]]:
    """Read-only coverage list of mootdx2 → tdx_quant tool mappings."""
    return [
        {"mootdx2_tool": k, "tdx_quant_tool": v}
        for k, v in _ADAPTER.TOOL_MAP.items()
    ]


def _tdx_quant_status(app_state) -> str:
    """Read tdx_quant upstream status — returns enum value name or 'MISSING'."""
    client = app_state.upstreams.get("tdx_quant")
    if client is None:
        return "MISSING"
    status = client.status
    # status is enum — its .value is the underlying string
    return getattr(status, "value", str(status))


async def get_proxy_settings_handler(req: Request, app_state=None) -> JSONResponse:
    """GET /api/proxy/settings — current state + tdx status + coverage."""
    if app_state is None:
        app_state = _get_app_state(req)
    return JSONResponse(
        {
            "enabled": app_state.proxy_settings.is_enabled(),
            "tdx_quant_status": _tdx_quant_status(app_state),
            "coverage": _coverage_table(),
        }
    )


async def put_proxy_settings_handler(req: Request, app_state=None) -> JSONResponse:
    """PUT /api/proxy/settings — body {enabled: bool}, persists + triggers reload."""
    if app_state is None:
        app_state = _get_app_state(req)
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

    app_state.proxy_settings.set_enabled(enabled_val)
    logger.info(f"proxy settings updated: enabled={enabled_val}")
    return JSONResponse(
        {
            "enabled": app_state.proxy_settings.is_enabled(),
            "tdx_quant_status": _tdx_quant_status(app_state),
            "coverage": _coverage_table(),
        }
    )


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
