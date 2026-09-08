"""MCP 2025-11-25 HTTP/StreamableHTTP compliance tests.

Pinned regressions for the startup-logic audit:

- CRIT #1: POST /mcp must return 200 directly, not 307 redirect to /mcp/.
  Many MCP clients (Claude Desktop, Cursor, mcp-cli) refuse to follow
  POST 307 because the spec mandates single-URL semantics for the MCP
  endpoint.
- HIGH #2: serverInfo.version in initialize response must equal the
  gateway's own version (0.1.1 from pyproject.toml), not FastMCP's
  framework version (4.0.3). Otherwise clients think the gateway
  changed every FastMCP upgrade.
- HIGH #6: protocolVersion must negotiate to 2025-11-25 (current).
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager


# ========== helpers ==========

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "compliance-test", "version": "0.0.1"},
    },
}


def _build_app_like_serve_http(version: str):
    """Replicate the Starlette construction in GatewayServer.serve_http.

    Uses the real `_NoSlashStarlette` and `_make_mcp_path_canonicalizer`
    from gateway_server so we catch regressions if either is removed.

    Returns the bare ASGI app — callers MUST run it under
    ``LifespanManager`` because FastMCP 4.x's StreamableHTTPSessionManager
    task group is only initialized during lifespan startup.
    """
    from fastmcp import FastMCP
    from starlette.routing import Mount

    from src.core.mcp_factory import (
        NoSlashStarlette,
        make_mcp_path_canonicalizer,
    )

    mcp = FastMCP("unihive", version=version)

    @mcp.tool()
    async def ping() -> str:
        """Trivial tool used to satisfy the initialize handshake."""
        return "pong"

    mcp_app = mcp.http_app(
        path="/",
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        host_origin_protection=True,
    )

    inner = NoSlashStarlette(
        lifespan=mcp_app.lifespan,
        routes=[Mount("/mcp", app=mcp_app)],
    )
    return make_mcp_path_canonicalizer(inner, "/mcp")


@asynccontextmanager
async def _live_app(version: str):
    """Build the gateway-shaped ASGI app and run its lifespan.

    FastMCP 4.x's StreamableHTTPSessionManager needs the lifespan startup
    phase to initialize its anyio task group. ``httpx.ASGITransport`` does
    not run lifespan by itself (httpx 0.28 has no ``lifespan="on"`` knob),
    so we wrap with ``asgi_lifespan.LifespanManager``.
    """
    app = _build_app_like_serve_http(version)
    async with LifespanManager(app):
        yield app


@asynccontextmanager
async def _live_app_with_capability_filter(version: str):
    """Like ``_live_app`` but also installs HIGH #3 capability filter.

    Mirrors what ``GatewayServer._do_initialize`` does after tool
    registration: install the filter so prompts/resources/logging
    capability fields are dropped when no components are present.
    """
    from fastmcp import FastMCP
    from starlette.routing import Mount

    from src.core.mcp_factory import (
        NoSlashStarlette,
        install_capability_filter,
        make_mcp_path_canonicalizer,
    )

    mcp = FastMCP("unihive", version=version)

    @mcp.tool()
    async def ping() -> str:
        return "pong"

    mcp_app = mcp.http_app(
        path="/",
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        host_origin_protection=True,
    )
    inner = NoSlashStarlette(
        lifespan=mcp_app.lifespan,
        routes=[Mount("/mcp", app=mcp_app)],
    )
    await install_capability_filter(mcp)
    app = make_mcp_path_canonicalizer(inner, "/mcp")
    async with LifespanManager(app):
        yield app


def _extract_sse_data(text: str) -> str:
    """Pull the `data:` payload out of an SSE response."""
    m = re.search(r"^data:\s*(.+)$", text, re.MULTILINE)
    assert m is not None, f"no data: line in SSE response:\n{text[:400]}"
    return m.group(1).strip()


# ========== CRIT #1: POST /mcp must NOT 307 ==========

class TestPostMcpNoRedirect:
    async def test_post_mcp_returns_200_not_307(self):
        """POST /mcp must go directly to the MCP handler.

        Pre-fix: Starlette's default redirect_slashes=True returns 307.
        Post-fix: app must subclass Starlette to set redirect_slashes=False.
        """
        async with _live_app("0.1.1") as app:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/mcp",
                    json=INITIALIZE,
                    headers={"Accept": "application/json, text/event-stream"},
                )
        assert resp.status_code == 200, (
            f"POST /mcp must be 200, got {resp.status_code} "
            f"(body: {resp.text[:200]!r})"
        )

    async def test_post_mcp_initialize_handshake_succeeds(self):
        """End-to-end: POST /mcp with initialize must return 200 + SSE data line."""
        async with _live_app("0.1.1") as app:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/mcp",
                    json=INITIALIZE,
                    headers={"Accept": "application/json, text/event-stream"},
                )
        assert resp.status_code == 200
        data = _extract_sse_data(resp.text)
        assert '"jsonrpc":"2.0"' in data
        assert '"id":1' in data


# ========== HIGH #2: serverInfo.version matches gateway version ==========

class TestServerInfoVersion:
    async def test_initialize_server_info_version_matches_gateway(self):
        """initialize response must report the gateway version, not FastMCP's.

        Pre-fix: version="4.0.3" (FastMCP fallback).
        Post-fix: version="0.1.1" (from pyproject.toml).
        """
        async with _live_app("0.1.1") as app:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/mcp",
                    json=INITIALIZE,
                    headers={"Accept": "application/json, text/event-stream"},
                )
        data = _extract_sse_data(resp.text)
        body = json.loads(data)
        server_info = body["result"]["serverInfo"]
        assert server_info["name"] == "unihive"
        assert server_info["version"] == "0.1.1", (
            f"serverInfo.version must be 0.1.1, got {server_info['version']!r}. "
            "FastMCP defaults to its own __version__ when not passed version=; "
            "this leaks framework version to clients."
        )


# ========== HIGH #6: protocolVersion negotiates to 2025-11-25 ==========

class TestProtocolVersionNegotiation:
    async def test_protocol_version_is_current_spec(self):
        async with _live_app("0.1.1") as app:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/mcp",
                    json=INITIALIZE,
                    headers={"Accept": "application/json, text/event-stream"},
                )
        body = json.loads(_extract_sse_data(resp.text))
        assert body["result"]["protocolVersion"] == "2025-11-25"


# ========== HIGH #3: capabilities match what we actually implement ==========

class TestCapabilitiesMatchImplementation:
    """initialize response must not advertise prompts/resources/logging.

    FastMCP 4.0.3 unconditionally registers handlers for prompts/list,
    resources/list, logging/setLevel — so without a filter, the wire
    response over-advertises capabilities the gateway never implements.
    GatewayServer._do_initialize calls _install_capability_filter to strip
    them. This test verifies the wire-level effect.
    """

    async def test_initialize_response_strips_prompts_resources_logging(self):
        async with _live_app_with_capability_filter("0.1.1") as app:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/mcp",
                    json=INITIALIZE,
                    headers={"Accept": "application/json, text/event-stream"},
                )
        assert resp.status_code == 200
        body = json.loads(_extract_sse_data(resp.text))
        caps = body["result"]["capabilities"]
        assert caps.get("prompts") is None, (
            f"prompts capability must be stripped when no prompts registered; "
            f"got {caps.get('prompts')!r}"
        )
        assert caps.get("resources") is None, (
            f"resources capability must be stripped when no resources/templates "
            f"registered; got {caps.get('resources')!r}"
        )
        assert caps.get("logging") is None, (
            f"logging capability must be stripped (gateway never wires "
            f"setLevel); got {caps.get('logging')!r}"
        )
        assert caps.get("tools") is not None, (
            "tools capability must remain — we register tools"
        )

    async def test_unfiltered_app_shows_over_advertisement(self):
        """Sanity: without the filter, the wire response shows the bug we fixed.

        Catches regressions where someone removes _install_capability_filter
        from _do_initialize — this test fails immediately, prompting the
        reviewer to put the filter back.
        """
        async with _live_app("0.1.1") as app:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/mcp",
                    json=INITIALIZE,
                    headers={"Accept": "application/json, text/event-stream"},
                )
        body = json.loads(_extract_sse_data(resp.text))
        caps = body["result"]["capabilities"]
        # Without the filter, prompts/resources/logging are advertised.
        assert caps.get("prompts") is not None
        assert caps.get("resources") is not None
        assert caps.get("logging") is not None


# ========== Guard rail: pyproject.toml version stays in sync ==========

class TestGatewayPackageVersion:
    def test_pyproject_version_is_stable(self):
        """If this fails, pyproject version changed and tests above need update.

        Reads version directly from pyproject.toml (we don't depend on
        the package being pip-installed in dev). If we ever publish 0.2.0,
        update both pyproject.toml and the version passed to FastMCP together.
        """
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        assert m is not None, "pyproject.toml missing version"
        version = m.group(1)
        assert re.match(r"^\d+\.\d+\.\d+", version), (
            f"unexpected version format: {version!r}"
        )
        assert version == "0.1.1", (
            f"tests in this file assume version 0.1.1; bump them when changing "
            f"pyproject.toml. Got {version!r}."
        )
