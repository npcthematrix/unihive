"""
路由层 - 基于 Phase 0 探测结果路由请求到上游
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..api.upstream_client import ToolResult, UpstreamClient
from .rate_limiter import AsyncTokenBucketLimiter, get_default_limiter

if TYPE_CHECKING:
    from ..api.fuyao_client import FuyaoClient
    from ..api.mootdx2_client import MooTDX2Client
    from ..api.tdx_quant_client import TdxQuantClient

logger = logging.getLogger(__name__)


@dataclass
class RouteHop:
    """路由跳点记录"""
    source: str
    tool_name: str
    duration_ms: int
    success: bool
    error: str | None = None


@dataclass
class RouteResult:
    """路由结果"""
    success: bool
    data: Any = None
    error: str | None = None
    source: str | None = None
    hops: list[RouteHop] = field(default_factory=list)
    cache_hit: bool = False


class Router:
    """
    路由决策器
    基于配置优先级链路由请求，支持降级
    """

    DEFAULT_PER_UPSTREAM_TIMEOUT = 30.0

    def __init__(
        self,
        upstreams: dict[str, UpstreamClient | FuyaoClient | MooTDX2Client | TdxQuantClient],
        routing_config: dict[str, dict],
        upstream_tool_mapping: dict[str, dict[str, str]] | None = None,
        per_upstream_timeout: float | None = None,
        limiter: AsyncTokenBucketLimiter | None = None,
    ):
        self.upstreams = upstreams
        self.routing_config = routing_config
        # 来自 config.upstream_tool_mapping；未声明的 gateway_tool 自动回退到 chain 顺序
        self.upstream_tool_mapping = upstream_tool_mapping or {}
        # H2 (2026-09-08 6th-round audit): 单个 upstream.call_tool() 超时,
        # 防止上游卡死把整条 chain 拖住, 后续 fallback 永远走不到。
        # 之前没有 per-upstream timeout, 整条 chain 总时间受 outer
        # _execute_cached 30s 限制, hung upstream 1 会把后续 upstream 全部
        # 浪费。
        self._per_upstream_timeout = (
            per_upstream_timeout
            if per_upstream_timeout is not None
            else self.DEFAULT_PER_UPSTREAM_TIMEOUT
        )
        # 限流器：保护 fuyao/thsdk 等互联网上游，避免触发上游 429/QPD 限制。
        # 不传则用进程默认单例 (20 QPS / 桶 40)。本地上游 (mootdx2/tdx_quant/
        # omni) 不需要限流，但走同一接口也无害（桶大从不阻塞）。
        self.limiter = limiter or get_default_limiter()

    def _get_routing_chain(self, gateway_tool: str) -> list[str]:
        """获取路由链"""
        if gateway_tool in self.routing_config:
            return self.routing_config[gateway_tool].get("chain", [])
        return []

    def _get_upstream_tool_mapping(self, gateway_tool: str) -> dict[str, str]:
        """网关工具到上游工具的映射。从 config 读取。"""
        return self.upstream_tool_mapping.get(gateway_tool, {})

    async def route(
        self,
        gateway_tool: str,
        params: dict[str, Any],
        force_source: str | None = None,
        per_upstream_timeout: float | None = None,
    ) -> RouteResult:
        """
        执行路由

        Args:
            gateway_tool: 网关工具名
            params: 调用参数
            force_source: 强制使用指定上游，绕过路由链

        Returns:
            RouteResult: 路由结果，包含所有跳点信息
        """
        hops: list[RouteHop] = []
        start_time = time.time()

        # 确定路由链
        if force_source:
            chain = [force_source]
        else:
            chain = self._get_routing_chain(gateway_tool)
            if not chain:
                # 回退：从 upstream_tool_mapping 推导
                chain = list(self.upstream_tool_mapping.get(gateway_tool, {}).keys())

        if not chain:
            return RouteResult(
                success=False,
                error=f"No routing chain defined for {gateway_tool}",
            )

        # 获取上游工具映射
        tool_mapping = self._get_upstream_tool_mapping(gateway_tool)

        # 遍历路由链
        for source in chain:
            if source not in self.upstreams:
                hop = RouteHop(
                    source=source,
                    tool_name="",
                    duration_ms=0,
                    success=False,
                    error=f"Unknown upstream: {source}"
                )
                hops.append(hop)
                continue

            upstream = self.upstreams[source]
            tool_name = tool_mapping.get(source, gateway_tool)

            # 记录跳点
            hop_start = time.time()

            if not upstream.is_available:
                hop = RouteHop(
                    source=source,
                    tool_name=tool_name,
                    duration_ms=0,
                    success=False,
                    error="Upstream unavailable"
                )
                hops.append(hop)
                logger.info(f"[{gateway_tool}] {source} unavailable, trying next...")
                continue

            # 限流：protect 互联网上游 (fuyao/thsdk) 避免触发上游 429/QPD 配额
            # 本地上游 (mootdx2/tdx_quant/omni) 走同一接口也无害（默认 20 QPS
            # 桶大，从不阻塞）。wait=False：桶空直接跳过该 upstream 试下一个。
            if not await self.limiter.acquire(source, wait=False):
                hop = RouteHop(
                    source=source,
                    tool_name=tool_name,
                    duration_ms=0,
                    success=False,
                    error=f"rate limited (qps cap reached for {source})"
                )
                hops.append(hop)
                logger.info(f"[{gateway_tool}] {source} rate limited, trying next...")
                continue

            # 调用上游
            # H2 (2026-09-08 6th-round audit): wrap call_tool in
            # asyncio.wait_for so a hung upstream doesn't block the
            # fallback chain. Per-call override > constructor default.
            timeout = (
                per_upstream_timeout
                if per_upstream_timeout is not None
                else self._per_upstream_timeout
            )
            try:
                result = await asyncio.wait_for(
                    upstream.call_tool(tool_name, params),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{gateway_tool}] {source} call_tool timed out after "
                    f"{timeout}s, trying next upstream"
                )
                hop = RouteHop(
                    source=source,
                    tool_name=tool_name,
                    duration_ms=int((time.time() - hop_start) * 1000),
                    success=False,
                    error=f"call_tool timed out after {timeout}s",
                )
                hops.append(hop)
                continue

            hop = RouteHop(
                source=source,
                tool_name=tool_name,
                duration_ms=int((time.time() - hop_start) * 1000),
                success=result.success,
                error=result.error
            )
            hops.append(hop)

            if result.success:
                total_duration = int((time.time() - start_time) * 1000)
                logger.info(
                    f"[{gateway_tool}] Success via {source} in {total_duration}ms "
                    f"(hops: {len(hops)})"
                )
                return RouteResult(
                    success=True,
                    data=result.data,
                    source=source,
                    hops=hops,
                )
            else:
                logger.warning(
                    f"[{gateway_tool}] Failed via {source}: {result.error}"
                )

        # 所有上游都失败
        total_duration = int((time.time() - start_time) * 1000)
        errors = {hop.source: hop.error for hop in hops if hop.error}

        logger.error(f"[{gateway_tool}] All upstream failed: {errors}")

        return RouteResult(
            success=False,
            error=f"All upstream failed: {errors}",
            source=None,
            hops=hops,
        )
