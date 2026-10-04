"""
路由层 - 基于 Phase 0 探测结果路由请求到上游
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..api.upstream_client import ErrorKind, ToolResult, UpstreamClient
from .proxy_adapter import Mootdx2ToTdxQuantAdapter, ProxyAdapterError
from .proxy_settings import ProxySettings

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
    # 网关级错误类别 (invalid_request/timeout/...); 失败路径从上游
    # ToolResult.error_kind 或 router 自身的超时判定填, 供最终文案分类。
    error_kind: str | None = None


@dataclass
class RouteResult:
    """路由结果"""
    success: bool
    data: Any = None
    error: str | None = None
    source: str | None = None
    hops: list[RouteHop] = field(default_factory=list)
    cache_hit: bool = False
    # 成功响应的非阻断告警（如裸 000xxx 消歧提示、truncated、BJ 批次降级）。
    # ToolResult.error 只在失败路径进信封，成功路径的提示经此字段透传。
    warn: str | None = None
    # 全链失败时的网关级错误类别 (invalid_request/upstream_unavailable/...)。
    error_kind: str | None = None


# no_data 业务态标记（与 models 层 MooTDXErrorType.NO_DATA.value 保持字面量一致，
# core 层不反向依赖各上游 error enum）
_NO_DATA_MARKER = "no_data"


def _warn_from_tool_result(result: ToolResult) -> str | None:
    """从 ToolResult 提取成功路径的用户可见提示。

    - ``error_detail.warn`` 为显式告警（消歧/truncated/部分降级）。
    - 成功但 ``data is None`` 且 ``error_detail.error_type == 'no_data'``
      时，说明文本（如"本地无 .day 文件"）作为 warn 透传，避免 HTTP
      消费者拿到 ``success=true, data=null, error=null`` 的静默空响应。
      必须带 no_data 标记：成功路径上的 ToolResult.error 默认不应面向
      用户，未标记的文本不提升为 warn。
    """
    detail = getattr(result, "error_detail", None)
    if not isinstance(detail, dict):
        return None
    warn = detail.get("warn")
    if isinstance(warn, str) and warn.strip():
        return warn
    if (
        result.data is None
        and detail.get("error_type") == _NO_DATA_MARKER
        and isinstance(result.error, str)
        and result.error.strip()
    ):
        return result.error
    return None


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
        """获取路由链。

        畸形条目 (null / 标量 / list 等非 mapping 形态) 按空链处理而不是
        抛 AttributeError: 此时工具无可路由上游, route() 走 "no chain"
        错误路径, 配置错误由 config_lint C004 在写入门/出厂升级时拦截。
        """
        entry = self.routing_config.get(gateway_tool)
        if not isinstance(entry, dict):
            if gateway_tool in self.routing_config:
                logger.warning(
                    "routing entry for %r is malformed (expected mapping with "
                    "'chain', got %r)",
                    gateway_tool,
                    type(entry).__name__,
                )
            return []
        chain = entry.get("chain", [])
        if not isinstance(chain, list):
            logger.warning(
                "routing chain for %r is malformed (expected list, got %r)",
                gateway_tool,
                type(chain).__name__,
            )
            return []
        return [str(upstream) for upstream in chain if upstream]

    def _get_upstream_tool_mapping(self, gateway_tool: str) -> dict[str, str]:
        """网关工具到上游工具的映射。从 config 读取。"""
        return self.upstream_tool_mapping.get(gateway_tool, {})

    def _build_failure_message(
        self, gateway_tool: str, hops: list[RouteHop],
    ) -> tuple[str, str]:
        """根据各跳错误类别构造可读、可区分的失败文案。

        归类规则 (决定整体语义):
          - 若所有失败 hop 都是 ``invalid_request`` → 整体判为参数/输入问题
            (不可重试), 文案引导用户检查入参;
          - 否则视为数据源侧问题 (默认 upstream_unavailable, 可重试)。
        单源 / 多源用不同措辞, 不再输出 Python dict repr。

        Returns:
            (面向调用方的消息, 整体错误类别 ErrorKind 值)
        """
        failed = [h for h in hops if not h.success]
        if not failed:
            return (f"工具 {gateway_tool} 未返回数据", ErrorKind.INTERNAL_ERROR.value)

        # 逐跳的 "数据源: 错误" 可读拼接 (替代 dict repr)
        detail_parts = [
            f"{h.source}: {h.error or '未知错误'}" for h in failed
        ]

        kinds = {h.error_kind for h in failed if h.error_kind}
        all_invalid = kinds and kinds == {ErrorKind.INVALID_REQUEST.value}

        if all_invalid:
            head = f"请求参数有误，{len(failed)} 个数据源均拒绝该调用"
            kind = ErrorKind.INVALID_REQUEST.value
        elif len(failed) == 1:
            h = failed[0]
            if h.error_kind == ErrorKind.TIMEOUT.value:
                head = f"数据源 {h.source} 调用超时"
                kind = ErrorKind.TIMEOUT.value
            elif h.error_kind == ErrorKind.RATE_LIMITED.value:
                head = f"数据源 {h.source} 触发限流，请稍后重试"
                kind = ErrorKind.RATE_LIMITED.value
            else:
                head = f"数据源 {h.source} 调用失败"
                kind = ErrorKind.UPSTREAM_UNAVAILABLE.value
        else:
            head = f"全部 {len(failed)} 个数据源当前均不可用，请稍后重试"
            # 多源混合类别时, 若含超时则归超时, 否则不可用
            if ErrorKind.TIMEOUT.value in kinds and len(kinds) == 1:
                kind = ErrorKind.TIMEOUT.value
            else:
                kind = ErrorKind.UPSTREAM_UNAVAILABLE.value

        return (f"{head}（{'；'.join(detail_parts)}）", kind)

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

            # boot 期上游在后台异步 start(); 尚未就绪时立即跳过、试下一个源,
            # 不空等 (决策 a)。离线工具上面已单独处理, 这里只管其余工具。
            if not is_offline_tool and not upstream.is_available:
                hop = RouteHop(
                    source=source,
                    tool_name=tool_name,
                    duration_ms=0,
                    success=False,
                    error=(
                        f"upstream {source!r} is still starting; "
                        f"retry shortly or use another source"
                    ),
                )
                hops.append(hop)
                logger.info(
                    f"[{gateway_tool}] {source} still starting, trying next..."
                )
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
                    error_kind=ErrorKind.TIMEOUT.value,
                )
                hops.append(hop)
                continue

            hop = RouteHop(
                source=source,
                tool_name=tool_name,
                duration_ms=int((time.time() - hop_start) * 1000),
                success=result.success,
                error=result.error,
                error_kind=getattr(result, "error_kind", None),
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
                    warn=_warn_from_tool_result(result),
                )
            else:
                logger.warning(
                    f"[{gateway_tool}] Failed via {source}: {result.error}"
                )

        # 所有上游都失败: 按错误类别生成准确、可读的文案 (替代旧的
        # "All upstream failed: {python dict repr}")。
        total_duration = int((time.time() - start_time) * 100)
        message, overall_kind = self._build_failure_message(gateway_tool, hops)

        if overall_kind == ErrorKind.INVALID_REQUEST.value:
            # 参数/输入类问题是用户侧错误, warning 而非 error (非网关故障)
            logger.warning(f"[{gateway_tool}] invalid request: {message}")
        else:
            logger.error(f"[{gateway_tool}] failed ({overall_kind}): {message}")

        return RouteResult(
            success=False,
            error=message,
            source=None,
            hops=hops,
            error_kind=overall_kind,
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
                warn=_warn_from_tool_result(result),
            )

        logger.warning(
            f"[{gateway_tool}] proxy tdx_quant failed: {result.error}, "
            f"falling back to chain"
        )
        return None  # 失败 → 降级到原 chain
