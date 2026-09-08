"""
FUYAO (同花顺) HTTP MCP Client
连接同花顺金融数据服务的 HTTP MCP 端点
"""
import httpx
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


@dataclass
class FuyaoConfig:
    """FUYAO 上游配置"""
    name: str
    base_url: str
    api_key: str
    timeout_seconds: int = 30
    max_retry: int = 3


class FuyaoClient:
    """同花顺 HTTP MCP 客户端"""

    def __init__(self, config: FuyaoConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._client: httpx.AsyncClient | None = None

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == UpstreamStatus.HEALTHY

    async def start(self) -> bool:
        """初始化连接"""
        try:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds
            )
            self._status = UpstreamStatus.HEALTHY
            logger.info(f"[{self.name}] Configured for {self.config.base_url}")
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
        """调用 MCP 工具"""
        start_time = time.time()

        if not self._client:
            return ToolResult(
                success=False,
                error="Client not initialized",
                source=self.name,
                duration_ms=0
            )

        try:
            # 构建 JSON-RPC 请求
            request = {
                "jsonrpc": "2.0",
                "id": int(time.time() * 1000),
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments
                }
            }

            response = await self._client.post(
                self.config.base_url,
                json=request,
                headers={"X-api-key": self.config.api_key}
            )
            response.raise_for_status()

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

        except httpx.HTTPStatusError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error(f"[{self.name}] {error_msg}")
            return ToolResult(
                success=False,
                error=error_msg,
                source=self.name,
                duration_ms=duration_ms
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[{self.name}] Call failed: {e}")
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
                json=request,
                headers={"X-api-key": self.config.api_key}
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
        try:
            result = await self.call_tool("get_meta_tickers_search", {"q": "test", "limit": 1})
            return result.success or "invalid" not in (result.error or "").lower()
        except Exception:
            return False
