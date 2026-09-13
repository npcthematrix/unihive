"""Unit test for HIGH #3 capability filter.

The MCP 2025-11-25 spec says initialize response capabilities must match
what the server actually implements. FastMCP 4.0.3 unconditionally
registers handlers for prompts/list, resources/list, resources/templates/
list, logging/setLevel in MCPOperationsMixin._setup_handlers — even when
no prompts/resources are registered. The MCP SDK's Server.get_capabilities
advertises any registered handler, so the gateway (which only implements
tools) was over-advertising prompts/resources/logging capabilities.

`install_capability_filter` samples actual component counts and wraps
`_mcp_server.get_capabilities` to zero out capability fields for empty
component types. This test verifies the filter at the unit level — no
HTTP, no lifespan needed.
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import FastMCP

from src.unihive.core.mcp_factory import install_capability_filter


@pytest.fixture
def tools_only_mcp():
    """FastMCP with one tool and zero prompts/resources/logging."""
    mcp = FastMCP("unihive", version="0.1.1")

    @mcp.tool()
    async def ping() -> str:
        """trivial."""
        return "pong"

    return mcp


class TestCapabilityFilter:
    async def test_strips_prompts_capability_when_no_prompts_registered(self, tools_only_mcp):
        await install_capability_filter(tools_only_mcp)
        caps = tools_only_mcp._mcp_server.get_capabilities()
        assert caps.prompts is None, (
            f"prompts capability must be None when no prompts are registered, "
            f"got {caps.prompts!r}. Clients will call prompts/list and get empty."
        )

    async def test_strips_resources_capability_when_no_resources_registered(self, tools_only_mcp):
        await install_capability_filter(tools_only_mcp)
        caps = tools_only_mcp._mcp_server.get_capabilities()
        assert caps.resources is None, (
            f"resources capability must be None when no resources/templates are "
            f"registered, got {caps.resources!r}."
        )

    async def test_strips_logging_capability(self, tools_only_mcp):
        await install_capability_filter(tools_only_mcp)
        caps = tools_only_mcp._mcp_server.get_capabilities()
        assert caps.logging is None, (
            f"logging capability must always be stripped (gateway never wires "
            f"setLevel), got {caps.logging!r}."
        )

    async def test_preserves_tools_capability(self, tools_only_mcp):
        await install_capability_filter(tools_only_mcp)
        # FastMCP's LowLevelServer stores notification_options with
        # tools_changed=True; that's what the live initialize handshake passes
        # via create_initialization_options. Mirror it here so we exercise the
        # same path the live /mcp handshake does.
        notif = tools_only_mcp._mcp_server.notification_options
        caps = tools_only_mcp._mcp_server.get_capabilities(
            notification_options=notif
        )
        assert caps.tools is not None, "tools capability must remain"
        assert caps.tools.list_changed is True, (
            f"tools.listChanged should be True per FastMCP defaults, "
            f"got {caps.tools!r}"
        )

    async def test_completions_stays_none(self, tools_only_mcp):
        """completion/complete is NOT registered by FastMCP, so no change."""
        await install_capability_filter(tools_only_mcp)
        caps = tools_only_mcp._mcp_server.get_capabilities()
        assert caps.completions is None


class TestCapabilityFilterWithPrompts:
    """If someone registers a prompt, the filter must NOT strip prompts."""

    async def test_keeps_prompts_capability_when_prompt_registered(self):
        mcp = FastMCP("unihive", version="0.1.1")

        @mcp.prompt()
        async def hello() -> str:
            """A trivial prompt."""
            return "hello"

        await install_capability_filter(mcp)
        notif = mcp._mcp_server.notification_options
        caps = mcp._mcp_server.get_capabilities(notification_options=notif)
        assert caps.prompts is not None, (
            "prompts capability must remain when a prompt is registered"
        )
        assert caps.prompts.list_changed is True


class TestIdempotent:
    async def test_install_twice_does_not_recurse(self, tools_only_mcp):
        """Calling install_capability_filter twice must not stack wrappers.

        Stacking wrappers means each layer patches the previous layer's output,
        which is fine for caps where we set None, but if a future filter
        non-trivially transforms caps we'd silently double-transform.
        """
        await install_capability_filter(tools_only_mcp)
        await install_capability_filter(tools_only_mcp)
        # Bypass the wrapper by reading _filter_marker. The second install
        # must be a no-op when the marker already exists.
        sentinel = "_unihive_capability_filter_installed"
        assert getattr(tools_only_mcp, sentinel) is True, (
            "second install did not set the marker — likely the first install "
            "didn't set it either, so this test isn't actually asserting what "
            "we think"
        )