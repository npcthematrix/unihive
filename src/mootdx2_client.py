"""MooTDX2 MCP Client 基于 mootdx2 库"""
import logging
from dataclasses import dataclass

from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


@dataclass
class MooTDX2Config:
    """MooTDX2 配置"""
    name: str
    market: str = "std"


class MooTDX2Client:
    """MooTDX2 MCP 客户端"""

    def __init__(self, config: MooTDX2Config):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == UpstreamStatus.HEALTHY

    async def start(self):
        self._status = UpstreamStatus.HEALTHY

    async def stop(self):
        self._status = UpstreamStatus.UNKNOWN

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        return ToolResult(success=False, error="Not implemented")
