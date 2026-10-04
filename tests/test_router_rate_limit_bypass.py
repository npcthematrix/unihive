"""本地 upstream 跳过限流器契约 (2026-10-04)。

背景: 早期 router 对**所有** upstream 无差别走 limiter, 默认 20 QPS /
桶 40 对本地上游本意"无害", 但实测偶发会让本地被误踢 (4 calls in 22ms
全失败事件)。结构性修复: 显式白名单 `_REQUIRES_RATE_LIMIT` 只对有上游
配额的远程 HTTP 生效 (fuyao 系列), 本地 (mootdx2/tdx_quant/omni/thsdk/
ths_local_gateway) 默认豁免, fail-closed。
"""
import pytest

from src.unihive.api.upstream_client import ToolResult, UpstreamStatus
from src.unihive.core.router import (
    Router,
    _REQUIRES_RATE_LIMIT,
)


class FakeUpstream:
    def __init__(self, name, result=None):
        self.name = name
        self._result = result or ToolResult(success=True, data={"ok": 1}, source=name)
        self.calls: list[tuple[str, dict]] = []

    @property
    def status(self):
        return UpstreamStatus.HEALTHY

    @property
    def is_available(self):
        return True

    async def call_tool(self, tool, params):
        self.calls.append((tool, params))
        return self._result


class TrackingLimiter:
    """每次 acquire 都被记录的 limiter; 可强制拒绝返回 False。

    替代 `AsyncTokenBucketLimiter`, 让测试不依赖真实的令牌桶状态机,
    直接断言 router 在本地路径上**根本没调** acquire。
    """

    def __init__(self, allow: bool = True):
        self.allow = allow
        self.acquires: list[str] = []

    async def acquire(self, key, n=1, wait=False):
        self.acquires.append(key)
        return self.allow


def _router(*upstream_names, limiter=None):
    upstreams = {n: FakeUpstream(n) for n in upstream_names}
    routing = {"tool_x": {"chain": list(upstream_names)}}
    return Router(
        upstreams=upstreams,
        routing_config=routing,
        upstream_tool_mapping={},
        limiter=limiter,
    )


# ---------------------------------------------------------------------------
# 结构性: 白名单成员契约
# ---------------------------------------------------------------------------


def test_whitelist_contains_only_remote_fuyao_sources():
    """白名单必须只包含有上游配额的远程 HTTP (fuyao 系列)。

    任何本地 upstream (mootdx2/tdx_quant/omni/thsdk/ths_local_gateway)
    都不能出现, 否则会被错误限流。
    """
    # 期望: 仅 fuyao 系列
    expected = {
        "fuyao_ashare",
        "fuyao_index",
        "fuyao_meta",
        "fuyao_fund",
    }
    assert set(_REQUIRES_RATE_LIMIT) == expected

    # 反向断言: 已知本地源不能进白名单
    for local_name in (
        "mootdx2",
        "tdx_quant",
        "omni",
        "thsdk",
        "THS-LOCAL-GATEWAY",
    ):
        assert local_name not in _REQUIRES_RATE_LIMIT, (
            f"本地 upstream {local_name!r} 不应出现在限流白名单里"
        )


# ---------------------------------------------------------------------------
# 行为: 本地 upstream 跳过 limiter.acquire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "local_source",
    ["mootdx2", "tdx_quant", "omni", "thsdk", "THS-LOCAL-GATEWAY"],
)
async def test_local_upstream_never_calls_acquire(local_source):
    """本地 upstream 走 router 时, limiter.acquire 不应被触发。

    即使 limiter 配置成永远拒绝, 本地也不应被踢 (因为根本不会进桶)。
    """
    limiter = TrackingLimiter(allow=False)  # 即使强制拒绝也无害
    router = _router(local_source, limiter=limiter)

    out = await router.route("tool_x", {"x": 1})

    assert out.success is True, f"本地 {local_source!r} 不应被限流踢出"
    assert limiter.acquires == [], (
        f"本地 {local_source!r} 不应触发 limiter.acquire, "
        f"实际调用了 {limiter.acquires}"
    )


# ---------------------------------------------------------------------------
# 行为: 远程 upstream 仍然走 limiter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remote_fuyao_ashare_respects_rate_limit_deny():
    """fuyao_ashare 在 limiter 拒绝时应被踢出, 走 fallback。"""
    limiter = TrackingLimiter(allow=False)
    router = _router("fuyao_ashare", "tdx_quant", limiter=limiter)

    out = await router.route("tool_x", {"x": 1})

    # fuyao_ashare 被限流踢出 → fallback 到 tdx_quant (本地, 应跳过 limiter)
    assert limiter.acquires == ["fuyao_ashare"], (
        "fuyao_ashare 应调用一次 acquire; tdx_quant 是本地不应调用"
    )
    assert out.success is True
    assert out.source == "tdx_quant"


@pytest.mark.asyncio
async def test_remote_fuyao_passes_when_limiter_allows():
    """fuyao_ashare 在 limiter 允许时正常调用上游。"""
    limiter = TrackingLimiter(allow=True)
    router = _router("fuyao_ashare", limiter=limiter)

    out = await router.route("tool_x", {"x": 1})

    assert limiter.acquires == ["fuyao_ashare"]
    assert out.success is True
    assert out.source == "fuyao_ashare"


# ---------------------------------------------------------------------------
# 边界: 混排链路上仅白名单上游会被 limiter 触碰
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_chain_only_whitelist_keys_are_acquired():
    """混合链 [mootdx2, fuyao_ashare, omni, fuyao_fund] 顺序路由时,
    limiter 仅在 fuyao_ashare / fuyao_fund 这两个白名单 key 上调用,
    本地 (mootdx2/omni) 不调用。
    """
    limiter = TrackingLimiter(allow=True)
    # 让 mootdx2 / omni 本地先失败, 强制链走到 fuyao 系列;
    # fuyao_ashare 失败 → fuyao_fund 成功, 验证 acquire 计数。
    fail = ToolResult(success=False, error="boom")
    m = FakeUpstream("mootdx2", result=fail)
    o = FakeUpstream("omni", result=fail)
    fa = FakeUpstream("fuyao_ashare", result=fail)
    ff = FakeUpstream("fuyao_fund")  # 默认成功
    upstreams = {"mootdx2": m, "fuyao_ashare": fa, "omni": o, "fuyao_fund": ff}

    router = Router(
        upstreams=upstreams,
        routing_config={"tool_x": {"chain": ["mootdx2", "fuyao_ashare", "omni", "fuyao_fund"]}},
        upstream_tool_mapping={},
        limiter=limiter,
    )

    out = await router.route("tool_x", {"x": 1})

    # 顺序: mootdx2(本地,无 acquire)→失败 → fuyao_ashare(白名单,1 次 acquire)失败 →
    #        omni(本地,无 acquire)→失败 → fuyao_fund(白名单,1 次 acquire)成功
    assert limiter.acquires == ["fuyao_ashare", "fuyao_fund"], (
        f"只有白名单 upstream 应触发 acquire, 实际 {limiter.acquires}"
    )
    assert out.success is True
    assert out.source == "fuyao_fund"
