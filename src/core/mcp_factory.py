"""
MCP 服务器工厂模块 - FastMCP 构造、 capability filter、 ASGI 路径规范化

从 GatewayServer 中抽出，确保 capability filter 必被安装（防止未来 refactor 跳过）。
所有公开符号都是无 self 依赖的，便于独立测试。
"""
from __future__ import annotations

from typing import Callable

from fastmcp import FastMCP
from starlette.applications import Starlette


class NoSlashStarlette(Starlette):
    """Starlette 子类，关掉默认 redirect_slashes=True。

    MCP StreamableHTTP 要求 POST /mcp 直接 200，不要被重定向到 /mcp/。
    很多 MCP 客户端 (Claude Desktop / Cursor) 不会自动跟 POST 307，
    会直接报 stream error。详见 MCP 2025-11-25 spec/authorization.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.router.redirect_slashes = False


async def install_capability_filter(mcp: FastMCP) -> None:
    """Strip capability fields from initialize response when no backing components exist.

    FastMCP 4.0.3 unconditionally registers handlers for prompts/list,
    resources/list, resources/templates/list, prompts/get, resources/read,
    logging/setLevel in ``MCPOperationsMixin._setup_handlers`` — even when no
    prompts/resources are registered. The MCP SDK's ``Server.get_capabilities``
    advertises any handler that's registered, so clients see ghost
    capabilities (``prompts``, ``resources``, ``logging``) that only ever
    return empty results.

    Sample actual component counts here (async context, components settled
    after ``_register_tools``) and wrap ``mcp._mcp_server.get_capabilities``
    so empty component types get ``None`` capability fields. We leave the
    handlers themselves registered so adding a component later (e.g. via a
    future prompt manager) keeps working without re-installing the filter.

    Tracked as HIGH #3 in MCP startup audit (2026-09-07). FastMCP 4.1+ was
    hoped to expose a public setter, but as of 4.0.3 there is none — the
    only fix is this private attr patch.
    """
    if getattr(mcp, "_unihive_capability_filter_installed", False):
        return
    has_prompts = bool(await mcp.list_prompts())
    has_resources = bool(await mcp.list_resources()) or bool(
        await mcp.list_resource_templates()
    )
    has_logging = False  # gateway never wires logging/setLevel to a sink

    ll_server = mcp._mcp_server
    original_get_capabilities = ll_server.get_capabilities

    def _filtered(
        notification_options=None,
        experimental_capabilities=None,
        extensions=None,
        *,
        protocol_version=None,
    ):
        caps = original_get_capabilities(
            notification_options,
            experimental_capabilities,
            extensions,
            protocol_version=protocol_version,
        )
        updates: dict = {}
        if not has_prompts and caps.prompts is not None:
            updates["prompts"] = None
        if not has_resources and caps.resources is not None:
            updates["resources"] = None
        if not has_logging and caps.logging is not None:
            updates["logging"] = None
        if updates:
            caps = caps.model_copy(update=updates)
        return caps

    ll_server.get_capabilities = _filtered
    mcp._unihive_capability_filter_installed = True


def make_mcp_path_canonicalizer(app, mcp_path: str):
    """ASGI 中间件: 把 POST /mcp 这种无尾斜杠的请求规范化为 /mcp/。

    Starlette 的 Mount("/mcp") 只匹配 /mcp/ 和 /mcp/... 不匹配 /mcp 本身,
    关掉 redirect_slashes 之后 /mcp 会直接 404。这个中间件在外层 router
    之前跑, 把 path 改写成 /mcp/ 后再交给 app, 让 Mount 能命中。
    """
    canonical = mcp_path.rstrip("/") + "/"

    async def middleware(scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "") == mcp_path:
            scope = {**scope, "path": canonical}
        await app(scope, receive, send)

    return middleware


async def create_and_register_gateway_mcp(
    tool_specs: list[dict] | None,
    *,
    version: str,
    on_constructed: Callable[[FastMCP], None],
) -> FastMCP:
    """Single entry point for constructing the gateway FastMCP server.

    三步串行（构造 → 注册工具 → 装 filter）都封装在此函数，
    防止未来 refactor 在调用方单独构造裸 FastMCP 时漏掉 capability filter
    （HIGH #3, 2026-09-07 audit）。

    Args:
        tool_specs: 已加载的 tool specs；传给 on_constructed。
        version: 给 FastMCP 的 serverInfo.version，避免框架版本被误判为 server 版本。
        on_constructed: 回调，GatewayServer 在此把 tools 注册到 mcp 上
            （可同时把 mcp 引用存到 self.mcp）。

    Returns:
        构造好的 FastMCP 实例，capability filter 已安装。
    """
    mcp = FastMCP("unihive", version=version)
    on_constructed(mcp)
    await install_capability_filter(mcp)
    return mcp