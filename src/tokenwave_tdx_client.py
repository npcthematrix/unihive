"""
TokenWave TDX MCP Client
基于 mootdx 库的数据访问客户端，支持 local 和 network 模式
"""
import logging
from dataclasses import dataclass
from typing import Any

from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


@dataclass
class TokenWaveTdxConfig:
    """TokenWave TDX 配置"""
    name: str
    mode: str = "auto"  # auto | local | network


class TokenWaveTdxClient:
    """TokenWave TDX MCP 客户端"""

    def __init__(self, config: TokenWaveTdxConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._local_client = None
        self._network_client = None

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == UpstreamStatus.AVAILABLE

    async def start(self):
        """初始化客户端"""
        # 初始化 local 和 network 客户端
        pass

    async def stop(self):
        """停止客户端"""
        pass

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        """调用工具"""
        pass
