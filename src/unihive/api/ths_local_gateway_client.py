"""
THS-LOCAL-GATEWAY HTTP JSON-RPC MCP Client
连接本地 MCP 网关服务
"""
import asyncio
import httpx
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


@dataclass
class ThsLocalConfig:
    """THS-LOCAL 上游配置"""
    name: str
    base_url: str
    timeout_seconds: int = 30
    connect_timeout_seconds: float = 5.0


class ThsLocalClient:
    """THS-LOCAL HTTP MCP 客户端"""

    def __init__(self, config: ThsLocalConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._client: httpx.AsyncClient | None = None
        self._tools: list[dict] = []  # 缓存工具列表

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    @property
    def tools(self) -> list[dict]:
        """同步获取工具列表（从缓存）"""
        return self._tools

    async def start(self) -> bool:
        """初始化连接并获取工具列表"""
        try:
            timeout = httpx.Timeout(
                self.config.timeout_seconds,
                connect=self.config.connect_timeout_seconds,
            )
            self._client = httpx.AsyncClient(timeout=timeout)
            # 执行健康检查确认连接可用
            if await self.health_check():
                self._status = UpstreamStatus.HEALTHY
                # 获取工具列表并缓存
                self._tools = await self.list_tools()
                logger.info(f"[{self.name}] Connected to {self.config.base_url}, {len(self._tools)} tools")
                return True
            else:
                self._status = UpstreamStatus.DEGRADED
                logger.warning(f"[{self.name}] Health check failed, marked as DEGRADED")
                return True
        except Exception as e:
            logger.error(f"[{self.name}] Failed to connect: {e}")
            self._status = UpstreamStatus.UNAVAILABLE
            return False

    async def stop(self):
        """关闭连接"""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._status = UpstreamStatus.UNAVAILABLE

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """调用远程 MCP 工具"""
        start_time = time.time()

        if not self._client:
            return ToolResult(
                success=False,
                error="Client not initialized",
                source=self.name,
                duration_ms=0
            )

        request = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }

        try:
            response = await self._client.post(
                self.config.base_url,
                json=request
            )
            data = response.json()
            duration_ms = int((time.time() - start_time) * 1000)

            if "error" in data:
                return ToolResult(
                    success=False,
                    error=str(data["error"]),
                    source=self.name,
                    duration_ms=duration_ms
                )

            return ToolResult(
                success=True,
                data=data.get("result"),
                source=self.name,
                duration_ms=duration_ms
            )

        except httpx.TimeoutException:
            duration_ms = int((time.time() - start_time) * 1000)
            return ToolResult(
                success=False,
                error="Request timeout",
                source=self.name,
                duration_ms=duration_ms
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ToolResult(
                success=False,
                error=str(e),
                source=self.name,
                duration_ms=duration_ms
            )

    async def list_tools(self) -> list[dict]:
        """获取工具列表"""
        if not self._client:
            return []

        try:
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {}
            }

            response = await self._client.post(
                self.config.base_url,
                json=request
            )
            data = response.json()

            if "result" in data and "tools" in data["result"]:
                return data["result"]["tools"]
            return []

        except Exception as e:
            logger.error(f"[{self.name}] List tools failed: {e}")
            return []

    async def health_check(self) -> bool:
        """健康检查"""
        if not self._client:
            return False

        try:
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {}
            }
            response = await self._client.post(self.config.base_url, json=request)
            return response.status_code == 200
        except Exception:
            return False
