"""
通用 HTTP JSON-RPC 上游客户端（如 TQ-Local 通达信本地服务）
非 MCP 协议，method 直接转发为 JSON-RPC method，params 直接作为 JSON-RPC params。
"""
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


@dataclass
class HttpJsonRpcConfig:
    """HTTP JSON-RPC 上游配置"""
    name: str
    base_url: str
    timeout_seconds: int = 30
    max_retry: int = 3
    headers: dict[str, str] | None = None
    id_seed: int = 1


class HttpJsonRpcClient:
    """通用 HTTP JSON-RPC 客户端。

    与 RhthsClient 不同的关键点：不发 `tools/call`，直接把 tool_name
    作为 JSON-RPC `method` 转发，`arguments` 作为 `params`。
    """

    def __init__(self, config: HttpJsonRpcConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._client: httpx.AsyncClient | None = None
        self._next_id = config.id_seed

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == UpstreamStatus.HEALTHY

    async def start(self) -> bool:
        try:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.timeout_seconds,
                headers=self.config.headers or {},
            )
            self._status = UpstreamStatus.HEALTHY
            logger.info(f"[{self.name}] Configured for {self.config.base_url}")
            return True
        except Exception as e:
            logger.error(f"[{self.name}] Failed to initialize: {e}")
            self._status = UpstreamStatus.UNAVAILABLE
            return False

    async def stop(self):
        if self._client:
            await self._client.aclose()
            self._client = None
        self._status = UpstreamStatus.UNAVAILABLE

    def _next_request_id(self) -> int:
        rid = self._next_id
        self._next_id += 1
        return rid

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        start_time = time.time()
        if not self._client:
            return ToolResult(
                success=False, error="Client not initialized",
                source=self.name, duration_ms=0,
            )

        request = {
            "jsonrpc": "2.0",
            "id": self._next_request_id(),
            "method": tool_name,
            "params": arguments or {},
        }

        try:
            resp = await self._client.post("", json=request)
            resp.raise_for_status()
            data = resp.json()
            duration_ms = int((time.time() - start_time) * 1000)

            if "error" in data and data["error"] is not None:
                return ToolResult(
                    success=False,
                    error=str(data["error"]),
                    source=self.name,
                    duration_ms=duration_ms,
                )
            return ToolResult(
                success=True,
                data=data.get("result"),
                source=self.name,
                duration_ms=duration_ms,
            )
        except httpx.ConnectError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return ToolResult(
                success=False,
                error=f"连接失败: {e}",
                source=self.name,
                duration_ms=duration_ms,
            )
        except httpx.HTTPStatusError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error(f"[{self.name}] {msg}")
            return ToolResult(
                success=False, error=msg,
                source=self.name, duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"[{self.name}] Call failed: {e}")
            return ToolResult(
                success=False, error=str(e),
                source=self.name, duration_ms=duration_ms,
            )

    async def list_tools(self) -> list[dict]:
        return []

    async def health_check(self) -> bool:
        if not self._client:
            return False
        try:
            resp = await self._client.post("", json={
                "jsonrpc": "2.0", "id": self._next_request_id(),
                "method": "get_user_sector", "params": {},
            }, timeout=3.0)
            return resp.status_code == 200
        except Exception:
            return False
