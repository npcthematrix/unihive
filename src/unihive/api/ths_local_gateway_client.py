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

from .mcp_tools_fetch import RawToolsResult, fetch_all_raw_tools
from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


class OpenApiFetchError(RuntimeError):
    """拉取/解析上游 OpenAPI 失败 (未初始化 / 网络 / 非 200 / 非 JSON 对象)。

    调用方 (console_api) 统一映射为 502; 显式失败, 不做静默兜底。
    """


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

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    async def start(self) -> bool:
        """初始化连接并做健康检查。

        刻意不在启动时拉工具清单: 旧实现会缓存 tools/list 的**首页**并打进
        日志, 而该上游实测分 3 页, 首页计数必然误导。真实计数由控制台
        MCP 浏览器 (/api/mcp-browser/{upstream}/tools) 按需拉取。这同时把
        启动时的 tools/list 往返省掉了 —— 上游没起时不再被拖慢。
        """
        try:
            timeout = httpx.Timeout(
                self.config.timeout_seconds,
                connect=self.config.connect_timeout_seconds,
            )
            self._client = httpx.AsyncClient(timeout=timeout)
            # 执行健康检查确认连接可用
            # L11 (round 9 audit): _start_upstream_client 对 ths_local_gateway
            # 特例不走 wait_for (gateway 级超时不生效), 而 health_check 的
            # httpx POST 仅受 timeout_seconds (默认 30s) 约束 → 上游挂死时
            # boot 卡 30s。这里用 connect_timeout_seconds 做 boot-probe 硬
            # 超时, 挂死在 ~boot-probe 返回 DEGRADED 而非等满 read timeout。
            boot_probe_timeout = max(self.config.connect_timeout_seconds, 1.0)
            try:
                healthy = await asyncio.wait_for(
                    self.health_check(), timeout=boot_probe_timeout
                )
            except asyncio.TimeoutError:
                healthy = False
                logger.warning(
                    f"[{self.name}] health check timed out after "
                    f"{boot_probe_timeout}s, marked as DEGRADED"
                )
            if healthy:
                self._status = UpstreamStatus.HEALTHY
                logger.info(f"[{self.name}] Connected to {self.config.base_url}")
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
            duration_ms = int((time.time() - start_time) * 1000)

            # HTTP 层先判: FastAPI 422 返 {"detail":[...]}、401/403/5xx 同样
            # 是无 "error" 键的 JSON, 不拦截会静默变成 success+None。
            if response.status_code >= 400:
                snippet = response.text[:300] if response.text else ""
                return ToolResult(
                    success=False,
                    error=(
                        f"{self.name} HTTP {response.status_code}: {snippet}"
                    ),
                    source=self.name,
                    duration_ms=duration_ms
                )

            data = response.json()

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
        """获取工具列表 (含全量分页)。

        委托给 fetch_all_raw_tools —— 旧实现只读 tools/list 的 result.tools,
        丢弃 nextCursor, 静默返回 87 个工具中的首页 32 个。仓库内已无调用方
        (控制台 MCP 浏览器走 list_tools_raw), 保留此方法只为对外契约。

        契约保持不变: ok=False 时记 error 并返回 []; ok=True (含截断) 返回
        已拿到的 tools。
        """
        result = await self.list_tools_raw()
        if not result.ok:
            logger.error(f"[{self.name}] List tools failed: {result.error}")
            return []
        return result.tools

    async def list_tools_raw(self, **kwargs) -> RawToolsResult:
        """原始 tools/list (含全量分页) —— 供控制台 MCP 浏览器使用。

        该上游实测分 3 页 (32/32/23 = 87 个工具), 而 list_tools() 只返回首页的
        32 个。方法的有无同时充当 "该上游是不是 MCP server" 的判据。
        """
        return await fetch_all_raw_tools(
            self._client,
            self.config.base_url,
            self.name,
            **kwargs,
        )

    async def fetch_openapi(self) -> dict[str, Any]:
        """实时拉取站点根的 ``openapi.json`` (REST 契约权威, 非 MCP 端点)。

        ``base_url`` 通常是 ``.../mcp``, 去掉末尾 ``/mcp`` (含尾斜杠容错) 得到
        站点根, 再拼 ``/openapi.json``; 不带 ``/mcp`` 后缀时按站点根处理。
        超时沿用 client 既有配置。任何失败抛 :class:`OpenApiFetchError`。
        """
        if not self._client:
            raise OpenApiFetchError(
                f"[{self.name}] client not initialized; call start() first"
            )
        root = self.config.base_url.rstrip("/")
        if root.endswith("/mcp"):
            root = root[: -len("/mcp")]
        url = root + "/openapi.json"
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            # TimeoutException / 连接拒绝等全部归一, 调用方映射 502。
            raise OpenApiFetchError(
                f"[{self.name}] fetch openapi failed: {exc}"
            ) from exc
        if response.status_code != 200:
            raise OpenApiFetchError(
                f"[{self.name}] fetch openapi returned HTTP {response.status_code}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise OpenApiFetchError(
                f"[{self.name}] openapi response is not valid JSON"
            ) from exc
        if not isinstance(data, dict):
            raise OpenApiFetchError(
                f"[{self.name}] openapi document is not a JSON object"
            )
        return data

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
