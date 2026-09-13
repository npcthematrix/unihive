"""
FUYAO (同花顺) HTTP MCP Client
连接同花顺金融数据服务的 HTTP MCP 端点
"""
import asyncio
import httpx
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


# 可重试的 HTTP 异常（连接错误 + 5xx + 429）
_RETRYABLE_HTTP_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


def _is_retryable_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


@dataclass
class FuyaoConfig:
    """FUYAO 上游配置"""
    name: str
    base_url: str
    api_key: str
    timeout_seconds: int = 30
    max_retry: int = 3
    # httpx 连接池：默认每 host 20 keepalive、全局 100 对 4 个 fuyao 端点
    # 偏紧，批量行情高并发会成瓶颈。放大并显式配置。
    max_connections: int = 200
    max_keepalive_connections: int = 100
    connect_timeout_seconds: float = 5.0
    # 重试退避：base * 2^attempt + jitter
    retry_backoff_base: float = 0.5
    retry_backoff_max: float = 5.0


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
        # P1 (2026-09-09 7th-round audit): 与 UpstreamClient / OmniClient 对齐,
        # DEGRADED 仍可响应, router 不应直接跳过 fallback。
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    async def start(self) -> bool:
        """初始化连接"""
        try:
            limits = httpx.Limits(
                max_connections=self.config.max_connections,
                max_keepalive_connections=self.config.max_keepalive_connections,
                keepalive_expiry=30.0,
            )
            # 连接建立给较短超时，读响应沿用配置的总超时
            timeout = httpx.Timeout(
                self.config.timeout_seconds,
                connect=self.config.connect_timeout_seconds,
            )
            self._client = httpx.AsyncClient(
                timeout=timeout,
                limits=limits,
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
        """调用 MCP 工具，带指数退避重试（仅对连接错误/5xx/429 重试）。"""
        start_time = time.time()

        if not self._client:
            return ToolResult(
                success=False,
                error="Client not initialized",
                source=self.name,
                duration_ms=0
            )

        # 构建 JSON-RPC 请求体
        request = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }
        headers = {"X-api-key": self.config.api_key}

        last_error: str | None = None
        max_attempts = max(1, self.config.max_retry + 1)  # max_retry=3 → 4 次尝试

        for attempt in range(max_attempts):
            try:
                response = await self._client.post(
                    self.config.base_url,
                    json=request,
                    headers=headers,
                )

                # 5xx/429 触发重试
                if _is_retryable_status(response.status_code):
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    if attempt < max_attempts - 1:
                        await self._backoff_sleep(attempt, last_error)
                        continue
                    duration_ms = int((time.time() - start_time) * 1000)
                    logger.error(f"[{self.name}] {last_error} (after {attempt+1} attempts)")
                    return ToolResult(
                        success=False, error=last_error,
                        source=self.name, duration_ms=duration_ms,
                    )

                # 4xx 非 429：直接返回，不重试（业务错误）
                if response.status_code >= 400:
                    duration_ms = int((time.time() - start_time) * 1000)
                    error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.error(f"[{self.name}] {error_msg}")
                    return ToolResult(
                        success=False, error=error_msg,
                        source=self.name, duration_ms=duration_ms,
                    )

                return self._parse_response(response.json(), start_time)

            except _RETRYABLE_HTTP_EXC as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt < max_attempts - 1:
                    await self._backoff_sleep(attempt, last_error)
                    continue
                duration_ms = int((time.time() - start_time) * 1000)
                logger.error(f"[{self.name}] {last_error} (after {attempt+1} attempts)")
                return ToolResult(
                    success=False, error=last_error,
                    source=self.name, duration_ms=duration_ms,
                )
            except Exception as e:
                # 非可重试异常（JSON 解析错等）直接返回
                duration_ms = int((time.time() - start_time) * 1000)
                logger.error(f"[{self.name}] Call failed: {e}")
                return ToolResult(
                    success=False, error=str(e),
                    source=self.name, duration_ms=duration_ms,
                )

        # 兜底（正常不会走到这里）
        duration_ms = int((time.time() - start_time) * 1000)
        return ToolResult(
            success=False, error=last_error or "max retries exhausted",
            source=self.name, duration_ms=duration_ms,
        )

    async def _backoff_sleep(self, attempt: int, last_error: str):
        """指数退避 + 抖动 (jitter)，上限 retry_backoff_max。"""
        base = self.config.retry_backoff_base * (2 ** attempt)
        delay = min(base, self.config.retry_backoff_max)
        # ±25% jitter，避免雷鸣群
        delay *= 0.75 + random.random() * 0.5
        logger.warning(
            f"[{self.name}] retry {attempt + 1} after {delay:.2f}s: {last_error}"
        )
        await asyncio.sleep(delay)

    def _parse_response(self, data: dict, start_time: float) -> ToolResult:
        """解析 Fuyao 响应，提取嵌套的 data.item。"""
        duration_ms = int((time.time() - start_time) * 1000)

        if "error" in data:
            return ToolResult(
                success=False, error=str(data["error"]),
                source=self.name, duration_ms=duration_ms,
            )

        # 解析 Fuyao 返回的嵌套格式
        # 格式: {"result": {"content": [{"text": "{\"code\":0,\"data\":{\"item\":[...]}}", "type": "text"}]}}
        result = data.get("result")
        if result and isinstance(result, dict):
            content = result.get("content")
            if content and isinstance(content, list) and len(content) > 0:
                first_content = content[0]
                if isinstance(first_content, dict):
                    text = first_content.get("text")
                    if text and isinstance(text, str):
                        try:
                            inner_data = json.loads(text)
                            if isinstance(inner_data, dict):
                                inner_result = inner_data.get("data", inner_data)
                                if isinstance(inner_result, dict):
                                    return ToolResult(
                                        success=True,
                                        data=inner_result.get("item", []),
                                        source=self.name,
                                        duration_ms=duration_ms,
                                    )
                                return ToolResult(
                                    success=True, data=inner_result,
                                    source=self.name, duration_ms=duration_ms,
                                )
                        except json.JSONDecodeError:
                            pass

        return ToolResult(
            success=True, data=result,
            source=self.name, duration_ms=duration_ms,
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
        """健康检查 — 直接读取本地状态，不再发网络请求。

        早期实现调用 get_meta_tickers_search 真打 fuyao, 每次都消耗
        上游配额；周期性触发会触发限流。改为判断本地 _status + _client
        是否初始化，作为"是否可路由"信号。
        """
        return (
            self._client is not None
            and self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)
        )

    def record_outcome(self, success: bool) -> None:
        """记录最近一次调用的成败（供 health_check / 控制台状态展示）。

        由调用方 (gateway / router) 在 call_tool 返回后调用。失败连续
        累计超过阈值时降级到 DEGRADED。
        """
        if success:
            self._status = UpstreamStatus.HEALTHY
        else:
            # 失败一次就降级，由下次成功恢复
            if self._status == UpstreamStatus.HEALTHY:
                self._status = UpstreamStatus.DEGRADED
