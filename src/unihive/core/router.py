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
from .proxy_adapter import Mootdx2ToTdxQuantAdapter, ProxyAdapterError
from .proxy_settings import ProxySettings
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
        proxy_settings: ProxySettings | None = None,
        tool_specs: list[dict] | None = None,
    ):
        self.upstreams = upstreams
        self.routing_config = routing_config
        # 来自 config.upstream_tool_mapping；未声明的 gateway_tool 自动回退到 chain 顺序
        self.upstream_tool_mapping = upstream_tool_mapping or {}
        # 工具规格：用于获取 data_source_type 等元数据
        # 格式: {tool_name: {data_source_type: "offline"|"online", ...}}
        self.tool_specs = tool_specs or []
        self._tool_metadata = {spec.get("name"): spec for spec in self.tool_specs}

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
        # Proxy: when enabled, route 7 行情类 mootdx2 tools through tdx_quant
        # first, falling back to mootdx2 on tdx failure. Optional — when not
        # provided, proxy is disabled and routing behavior is unchanged.
        self.proxy_settings = proxy_settings
        self._proxy_adapter = Mootdx2ToTdxQuantAdapter()

    def replace_tool_specs(self, tool_specs: list[dict] | None) -> None:
        """热编辑后整体替换 spec 列表与 name→spec 索引 (P3, 2026-09-18)。

        routing_config / upstream_tool_mapping 在 GatewayServer 侧与
        self.config 同引用, 改名/改映射后无需经此函数重绑。
        """
        self.tool_specs = tool_specs or []
        self._tool_metadata = {spec.get("name"): spec for spec in self.tool_specs}

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

        # Proxy attempt: enabled + tdx 健康 + tool 在 TOOL_MAP 时, 先试 tdx_quant
        # MED (round 8 audit): 用 getattr 兜底, 兼容 Router.__new__(Router)
        # 跳过 __init__ 的测试场景 (e.g. round 8 audit 直接构造 Router 实例)
        proxy_settings = getattr(self, "proxy_settings", None)
        if proxy_settings is not None:
            proxy_result = await self._try_proxy(gateway_tool, params, start_time)
            if proxy_result is not None:
                return proxy_result

        # 确定路由链
        if force_source:
            chain = [force_source]
        else:
            chain = self._get_routing_chain(gateway_tool)
            if not chain:
                # 回退：从 upstream_tool_mapping 推导
                chain = list(self.upstream_tool_mapping.get(gateway_tool, {}).keys())

        if not chain:
            # MED-1 (2026-09-14 round 8 audit): 错误消息带上排查指引,
            # 让上游 config 作者无需查代码就知道去 upstreams.yaml 配 routing 段。
            return RouteResult(
                success=False,
                error=(
                    f"No routing chain defined for {gateway_tool}; "
                    f"check config/upstreams.yaml routing section "
                    f"and ensure the tool has a chain list."
                ),
            )

        # 获取上游工具映射
        tool_mapping = self._get_upstream_tool_mapping(gateway_tool)

        # 遍历路由链
        for source in chain:
            if source not in self.upstreams:
                # Round-12 簇G: "Unknown upstream" 对运维不可操作 —— 链上的
                # 名字不在运行表, 真实原因只有两种: 配置里被禁用, 或启动时
                # 构造/初始化失败。消息直接说清楚并指向配置段。
                hop = RouteHop(
                    source=source,
                    tool_name="",
                    duration_ms=0,
                    success=False,
                    error=(
                        f"upstream {source!r} is not running "
                        f"(disabled in config/upstreams.yaml or failed to start); "
                        f"check the upstreams.{source} section and startup logs"
                    ),
                )
                hops.append(hop)
                continue

            upstream = self.upstreams[source]
            tool_name = tool_mapping.get(source, gateway_tool)

            # 记录跳点
            hop_start = time.time()

            # 获取工具元数据，判断是否离线工具
            tool_meta = self._tool_metadata.get(gateway_tool, {})
            is_offline_tool = tool_meta.get("data_source_type") == "offline"

            # 跳过 is_available 检查：离线工具读取本地文件，不需要网络
            # 其他所有工具都直接调用，失败再处理
            if is_offline_tool and not upstream.is_available:
                hop = RouteHop(
                    source=source,
                    tool_name=tool_name,
                    duration_ms=0,
                    success=False,
                    error=(
                        f"upstream {source!r} is not running "
                        f"(offline client unavailable, e.g. board.db not synced)"
                    ),
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
                # DEBUG 而非 INFO: 路由成功是常态, 降级避免事件循环线程同步写盘放大。
                logger.debug(
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

    async def _try_proxy(
        self,
        gateway_tool: str,
        params: dict[str, Any],
        start_time: float,
    ) -> RouteResult | None:
        """Try TDX_QUANT first when proxy enabled. None = not attempted; RouteResult = handled."""
        if self.proxy_settings is None:
            return None
        if not self.proxy_settings.is_enabled():
            return None
        if not self._proxy_adapter.supports(gateway_tool):
            return None

        tdx_client = self.upstreams.get("tdx_quant")
        if tdx_client is None or not tdx_client.is_available:
            return None  # 降级到原 chain

        try:
            tdx_tool, tdx_params, normalizer = self._proxy_adapter.adapt(
                gateway_tool, params
            )
        except ProxyAdapterError:
            return None  # 兜底走原 chain

        hop_start = time.time()
        try:
            result = await asyncio.wait_for(
                tdx_client.call_tool(tdx_tool, tdx_params),
                timeout=self._per_upstream_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[{gateway_tool}] proxy tdx_quant call_tool timed out, "
                f"falling back to chain"
            )
            return None
        except Exception as e:
            # Round 10 audit principle: never let a proxy exception escape —
            # otherwise the router fallback chain breaks. Log + fall back.
            logger.warning(
                f"[{gateway_tool}] proxy tdx_quant exception: {e}, "
                f"falling back to chain"
            )
            return None

        hop = RouteHop(
            source="tdx_quant",
            tool_name=tdx_tool,
            duration_ms=int((time.time() - hop_start) * 1000),
            success=result.success,
            error=result.error,
        )

        if result.success:
            total_duration = int((time.time() - start_time) * 1000)
            logger.info(
                f"[{gateway_tool}] proxy Success via tdx_quant in "
                f"{total_duration}ms"
            )
            return RouteResult(
                success=True,
                data=normalizer(result.data),
                source="tdx_quant",
                hops=[hop],
            )

        logger.warning(
            f"[{gateway_tool}] proxy tdx_quant failed: {result.error}, "
            f"falling back to chain"
        )
        return None  # 失败 → 降级到原 chain
