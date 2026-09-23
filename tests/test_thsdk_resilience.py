"""THSDK 韧性测试：原生超时注入 / 熔断状态机 / 单 worker 执行器。

背景：2026-09-22 排查确认 thsdk 3.0.0 的 api.py 便捷函数调用原生层时不带
timeout_ms，hq.dll 网络瞬断可阻塞 8-15 分钟；session._Client._request 持
_auth_lock 串行全部调用；路由 10s wait_for 只取消 await，僵尸线程留在池里。
对应修复 A（注入默认 timeout_ms）/ B（连续超时熔断 + 半开探活）/ C（单 worker）。
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest
import yaml

from src.unihive.api.thsdk_client import (
    ThsdkClient,
    _Throttle,
    resolve_thsdk_call_timeout,
)
from src.unihive.api.upstream_client import ToolResult, UpstreamStatus
from src.unihive.core.router import Router
from src.unihive.exceptions.thsdk_errors import (
    ThsdkErrorType,
    classify_exception,
)
from src.unihive.core.blocking_pool import (
    get_thsdk_executor,
    shutdown_thsdk_executor,
)
from src.unihive.models.thsdk_config import ThsdkConfig


# 与真实 thsdk 3.0.0 一致的异常继承链（类名必须与真实类一致：
# 分类器按 __name__ 的 MRO 匹配）：NotAuthenticated 是 Auth 的子类
class THSDKError(Exception):
    pass


class AuthenticationError(THSDKError):
    pass


class NotAuthenticatedError(AuthenticationError):
    pass


class APIError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------- 带 session/bridge 的假 thsdk 模块 ----------

class _RecordingBridge:
    """模拟 _RuntimeBridge：记录 call 的 timeout_ms。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call(self, method: str, params: Any = None, *, timeout_ms: int | None = None) -> dict:
        self.calls.append(
            {"method": method, "params": params, "timeout_ms": timeout_ms}
        )
        return {"ok": True, "data": True}


def _make_fake_mod(
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_session: bool = True,
    bridge: _RecordingBridge | None = None,
    price_side_effect: Any = None,
) -> tuple[types.ModuleType, _RecordingBridge | None]:
    bridge = bridge or (_RecordingBridge() if with_session else None)
    mod = types.ModuleType("thsdk")
    mod.auth = MagicMock(return_value=True)

    def _get_price(security: str, **kwargs: Any) -> pd.DataFrame:
        # 与真实 api.py 一样，经由默认 session client 的 runtime 下发
        holder = mod.session._get_client()
        holder.runtime.call(
            "list_security_k_lines", {"security": security, "kwargs": kwargs}
        )
        return pd.DataFrame(
            {"close": [10.6]}, index=pd.to_datetime(["2026-09-01"])
        )

    mod.get_price = MagicMock(
        side_effect=price_side_effect if price_side_effect is not None else _get_price
    )

    if with_session:
        session_mod = types.ModuleType("thsdk.session")

        class _Holder:
            @property
            def runtime(self) -> _RecordingBridge:
                assert bridge is not None
                return bridge

        session_mod._get_client = MagicMock(return_value=_Holder())
        mod.session = session_mod
        monkeypatch.setitem(sys.modules, "thsdk.session", session_mod)

    monkeypatch.setitem(sys.modules, "thsdk", mod)
    return mod, bridge


def _client(call_timeout_sec: int = 5) -> ThsdkClient:
    return ThsdkClient(
        ThsdkConfig(
            name="thsdk", call_timeout_sec=call_timeout_sec, throttle_ms=0
        )
    )


async def _started(monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> tuple[ThsdkClient, types.ModuleType, _RecordingBridge | None]:
    call_timeout_sec = kwargs.pop("call_timeout_sec", 5)
    mod, bridge = _make_fake_mod(monkeypatch, **kwargs)
    client = _client(call_timeout_sec)
    assert await client.start() is True
    return client, mod, bridge


@pytest.fixture(autouse=True)
def _fresh_thsdk_executor() -> Any:
    shutdown_thsdk_executor()
    yield
    shutdown_thsdk_executor()


# ========== Fix A：原生 timeout_ms 注入 ==========

async def test_native_timeout_injected_after_start(monkeypatch: pytest.MonkeyPatch):
    """start() 后 bridge.call 被包装：不传 timeout_ms 时注入 (call_timeout - 1s)。"""
    client, mod, bridge = await _started(monkeypatch, call_timeout_sec=5)
    assert bridge is not None

    # 经由 client 调一次业务工具：底层 bridge 收到注入的 4000ms
    result = await client.call_tool(
        "ths_get_price", {"security": "USHA600519", "count": 1}
    )
    assert result.success
    recorded = bridge.calls[-1]
    assert recorded["method"] == "list_security_k_lines"
    assert recorded["timeout_ms"] == 4000


async def test_explicit_timeout_ms_preserved(monkeypatch: pytest.MonkeyPatch):
    _, _, bridge = await _started(monkeypatch)
    assert bridge is not None
    bridge.call("some_method", {}, timeout_ms=2500)
    assert bridge.calls[-1]["timeout_ms"] == 2500


async def test_bind_skipped_without_session(monkeypatch: pytest.MonkeyPatch):
    """旧结构假模块（无 session）不应导致 start 失败：防御性跳过绑定。"""
    client, _, _ = await _started(monkeypatch, with_session=False)
    assert client.is_available


async def test_rebind_picks_up_new_timeout(monkeypatch: pytest.MonkeyPatch):
    """同进程 stop/start（热重连）后，配置变更的超时值必须生效，不是嵌套旧包装。"""
    mod, bridge = _make_fake_mod(monkeypatch)
    client = _client(5)
    await client.start()
    assert bridge is not None
    bridge.call("m")
    assert bridge.calls[-1]["timeout_ms"] == 4000

    await client.stop()
    client2 = ThsdkClient(
        ThsdkConfig(name="thsdk", call_timeout_sec=8, throttle_ms=0)
    )
    assert await client2.start() is True
    bridge.call("m")
    assert bridge.calls[-1]["timeout_ms"] == 7000


# ========== Fix B：熔断状态机 ==========

async def _open_breaker(client: ThsdkClient, mod: types.ModuleType) -> None:
    mod.get_price.side_effect = APIError("超时", code="request_timeout")
    for _ in range(3):
        result = await client.call_tool(
            "ths_get_price", {"security": "USHA600519"}
        )
        assert result.success is False


async def test_breaker_opens_after_three_timeout_failures(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    await _open_breaker(client, mod)

    assert client.status is UpstreamStatus.UNAVAILABLE
    # 第四次快速失败，不进 DLL（get_price 仍只被调 3 次）
    result = await client.call_tool(
        "ths_get_price", {"security": "USHA600519"}
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "circuit_open"
    assert "熔断" in (result.error or "")
    assert mod.get_price.call_count == 3


async def test_non_timeout_failures_do_not_count(monkeypatch: pytest.MonkeyPatch):
    client, mod, bridge = await _started(monkeypatch)
    mod.get_price.side_effect = ValueError("bad args")
    for _ in range(5):
        result = await client.call_tool(
            "ths_get_price", {"security": "BAD"}
        )
        assert result.success is False
    assert client.status is UpstreamStatus.HEALTHY
    assert mod.get_price.call_count == 5


async def test_success_resets_failure_count(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.get_price.side_effect = APIError("x", code="deadline_exceeded")
    for _ in range(2):
        await client.call_tool("ths_get_price", {"security": "USHA600519"})

    # 一次成功后计数归零；再来两次超时不应触发熔断
    mod.get_price.side_effect = None
    await client.call_tool("ths_get_price", {"security": "USHA600519"})
    mod.get_price.side_effect = APIError("x", code="deadline_exceeded")
    for _ in range(2):
        await client.call_tool("ths_get_price", {"security": "USHA600519"})
    assert client.status is UpstreamStatus.HEALTHY


async def test_asyncio_timeout_counts_toward_breaker(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.get_price.side_effect = asyncio.TimeoutError()
    for _ in range(3):
        await client.call_tool("ths_get_price", {"security": "USHA600519"})
    assert client.status is UpstreamStatus.UNAVAILABLE


async def test_half_open_probe_success_recovers(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    await _open_breaker(client, mod)

    # 冷却时间过后（直接回拨 opened_at 模拟）
    client._cb_opened_at = time.monotonic() - 31

    mod.get_price.side_effect = None
    result = await client.call_tool(
        "ths_get_price", {"security": "USHA600519"}
    )
    assert result.success
    assert client.status is UpstreamStatus.HEALTHY
    assert client._cb_state == "closed"


async def test_half_open_probe_failure_reopens(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    await _open_breaker(client, mod)
    client._cb_opened_at = time.monotonic() - 31

    # 探针仍超时：返回真实上游错误，但熔断器重新打开（完整冷却期重来）
    result = await client.call_tool(
        "ths_get_price", {"security": "USHA600519"}
    )
    assert result.success is False
    assert client._cb_state == "open"
    assert time.monotonic() - client._cb_opened_at < 1

    # 随后的调用快速失败，不进 DLL
    fast_fail = await client.call_tool(
        "ths_get_price", {"security": "USHA600519"}
    )
    assert fast_fail.success is False
    assert fast_fail.error_detail["error_type"] == "circuit_open"


async def test_auth_status_bypasses_breaker(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    await _open_breaker(client, mod)

    result = await client.call_tool("ths_auth_status", {})
    assert result.success
    assert result.data["authenticated"] is True


# ========== Fix B-2：外层取消计数 / 半开单探针 / 非超时探针失败 ==========

async def test_router_cancellation_counts_toward_breaker(
    monkeypatch: pytest.MonkeyPatch,
):
    """路由先取消时 CancelledError 必须计入熔断（事故主路径）。"""
    client, mod, _ = await _started(monkeypatch)

    async def _boom(_p: dict) -> None:
        raise asyncio.CancelledError()

    client._tool_ths_get_price = _boom
    for _ in range(3):
        with pytest.raises(asyncio.CancelledError):
            await client.call_tool(
                "ths_get_price", {"security": "USHA600519"}
            )
    assert client._cb_state == "open"
    assert client.status is UpstreamStatus.UNAVAILABLE

    # 熔断已开：后续调用走门控快速失败，不再抛 CancelledError
    result = await client.call_tool(
        "ths_get_price", {"security": "USHA600519"}
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "circuit_open"


async def test_half_open_single_probe_others_fast_fail(
    monkeypatch: pytest.MonkeyPatch,
):
    """半开期只允许一个探针；探针在执行时其余调用快速失败。"""
    client, mod, _ = await _started(monkeypatch)
    await _open_breaker(client, mod)
    client._cb_opened_at = time.monotonic() - 31

    entered = threading.Event()
    release = threading.Event()

    def _probe(security: str, **kwargs: Any) -> pd.DataFrame:
        entered.set()
        release.wait(5)
        return pd.DataFrame(
            {"close": [10.6]}, index=pd.to_datetime(["2026-09-01"])
        )

    mod.get_price.side_effect = _probe
    first = asyncio.create_task(
        client.call_tool("ths_get_price", {"security": "USHA600519"})
    )
    for _ in range(200):
        if entered.is_set():
            break
        await asyncio.sleep(0.01)
    assert entered.is_set()
    assert client._cb_probe_inflight is True

    # 探针未出结果：第二个调用被门控拦回
    second = await client.call_tool(
        "ths_get_price", {"security": "USHA600519"}
    )
    assert second.success is False
    assert second.error_detail["error_type"] == "circuit_open"
    assert "探活" in (second.error or "")

    release.set()
    first_result = await first
    assert first_result.success
    assert client._cb_state == "closed"
    assert client._cb_probe_inflight is False


async def test_half_open_probe_non_timeout_failure_reopens(
    monkeypatch: pytest.MonkeyPatch,
):
    """探针返回非超时类错误（query_failed）同样重新熔断，防 half_open 卡死。"""
    client, mod, _ = await _started(monkeypatch)
    await _open_breaker(client, mod)
    client._cb_opened_at = time.monotonic() - 31

    mod.get_price.side_effect = APIError("x", code="query_failed")
    result = await client.call_tool(
        "ths_get_price", {"security": "URFI885537"}
    )
    assert result.success is False
    assert client._cb_state == "open"

    fast_fail = await client.call_tool(
        "ths_get_price", {"security": "USHA600519"}
    )
    assert fast_fail.success is False
    assert fast_fail.error_detail["error_type"] == "circuit_open"


def test_resolve_call_timeout_clamps_to_router_budget():
    """客户端超时钳制：保证严格小于路由超时。"""
    assert resolve_thsdk_call_timeout(30, 10) == 9
    assert resolve_thsdk_call_timeout(10, 10) == 9
    assert resolve_thsdk_call_timeout(5, 10) == 5
    assert resolve_thsdk_call_timeout(30, None) == 30
    assert resolve_thsdk_call_timeout(30, 3) == 2


async def test_config_error_code_not_trip_breaker(
    monkeypatch: pytest.MonkeyPatch,
):
    """含 timeout 字样的客户端配置错误码连续 5 次仍 HEALTHY（精确集合匹配）。"""
    client, mod, _ = await _started(monkeypatch)
    mod.get_price.side_effect = APIError("bad", code="invalid_timeout_param")
    for _ in range(5):
        result = await client.call_tool(
            "ths_get_price", {"security": "USHA600519"}
        )
        assert result.success is False
    assert client.status is UpstreamStatus.HEALTHY
    assert client._cb_failures == 0


# ========== CRITICAL 回归：异常分类顺序 + 会话过期自愈 ==========

def test_not_authenticated_classified_before_auth_failed():
    """真实 MRO 下 NotAuthenticated 不得被误判 AUTH_FAILED。"""
    err = classify_exception(NotAuthenticatedError("session expired"))
    assert err.error_type is ThsdkErrorType.NOT_AUTHENTICATED
    assert err.recoverable is True


def test_base_authentication_error_classified_auth_failed():
    err = classify_exception(AuthenticationError("bad password"))
    assert err.error_type is ThsdkErrorType.AUTH_FAILED
    assert err.recoverable is False


async def test_session_expiry_triggers_reauth_and_retry(
    monkeypatch: pytest.MonkeyPatch,
):
    """端到端：会话失效 → 自动重登 → 只读 handler 重试一次成功。"""
    client, mod, _ = await _started(monkeypatch)
    attempts = {"n": 0}

    async def _handler(_p: dict) -> list[dict]:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise NotAuthenticatedError("expired")
        return [{"close": 10.6}]

    client._tool_ths_get_price = _handler
    result = await client.call_tool(
        "ths_get_price", {"security": "USHA600519"}
    )
    assert result.success
    assert attempts["n"] == 2
    assert mod.auth.call_count >= 2  # start 一次 + 自愈一次


# ========== CRITICAL 回归：板块强类型归一 ==========

async def test_rank_block_numeric_string_passes_int(
    monkeypatch: pytest.MonkeyPatch,
):
    client, mod, _ = await _started(monkeypatch)
    mod.resolve_block = MagicMock()
    mod.rank_block_securities = MagicMock(return_value={"securities": []})

    result = await client.call_tool(
        "ths_rank_block_securities", {"block": "61322", "count": 5}
    )
    assert result.success
    kwargs = mod.rank_block_securities.call_args.kwargs
    assert kwargs["block"] == 61322
    assert isinstance(kwargs["block"], int)
    assert kwargs["sort_count"] == 5
    mod.resolve_block.assert_not_called()


async def test_rank_block_name_resolved_to_mapping(
    monkeypatch: pytest.MonkeyPatch,
):
    client, mod, _ = await _started(monkeypatch)
    block_map = {"id": 61322, "name": "沪深300"}
    mod.resolve_block = MagicMock(return_value=block_map)
    mod.rank_block_securities = MagicMock(return_value={"securities": []})

    result = await client.call_tool(
        "ths_rank_block_securities", {"block": "沪深300"}
    )
    assert result.success
    mod.resolve_block.assert_called_once_with(name="沪深300")
    assert mod.rank_block_securities.call_args.kwargs["block"] == block_map


async def test_list_block_descriptions_normalizes_each_element(
    monkeypatch: pytest.MonkeyPatch,
):
    client, mod, _ = await _started(monkeypatch)
    block_map = {"id": 30, "name": "人工智能"}
    mod.resolve_block = MagicMock(return_value=block_map)
    mod.list_block_descriptions = MagicMock(return_value=[])

    result = await client.call_tool(
        "ths_list_block_descriptions",
        {"blocks": ["61322", "人工智能"]},
    )
    assert result.success
    mod.list_block_descriptions.assert_called_once_with(
        blocks=[61322, block_map]
    )


async def test_list_block_descriptions_empty_rejected(
    monkeypatch: pytest.MonkeyPatch,
):
    client, mod, _ = await _started(monkeypatch)
    mod.list_block_descriptions = MagicMock()
    result = await client.call_tool(
        "ths_list_block_descriptions", {"blocks": []}
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "invalid_param"
    mod.list_block_descriptions.assert_not_called()


async def test_hot_event_news_requires_block(
    monkeypatch: pytest.MonkeyPatch,
):
    client, mod, _ = await _started(monkeypatch)
    mod.list_hot_event_news = MagicMock()

    result = await client.call_tool(
        "ths_list_hot_event_news", {"range": "recent"}
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "invalid_param"
    assert "block" in (result.error or "")
    mod.list_hot_event_news.assert_not_called()


async def test_hot_event_news_passes_normalized_block(
    monkeypatch: pytest.MonkeyPatch,
):
    client, mod, _ = await _started(monkeypatch)
    mod.list_hot_event_news = MagicMock(return_value={"items": []})

    result = await client.call_tool(
        "ths_list_hot_event_news",
        {"block": "61322", "range": "recent"},
    )
    assert result.success
    kwargs = mod.list_hot_event_news.call_args.kwargs
    assert kwargs["block"] == 61322
    assert kwargs["range"] == "recent"


# ========== CRITICAL 回归：映射值必须可路由（router 层，不再绕过） ==========

_TOOLS_YAML = Path(__file__).resolve().parents[1] / "config" / "tools_thsdk.yaml"


def _yaml_tools() -> dict[str, Any]:
    with open(_TOOLS_YAML, encoding="utf-8") as f:
        return {t["name"]: t for t in yaml.safe_load(f)["tools"]}


class _FakeUpstream:
    """记录 router 实际下发的上游工具键。"""

    def __init__(self) -> None:
        self.is_available = True
        self.received: list[tuple[str, Any]] = []

    async def call_tool(self, tool_name: str, params: Any, **kwargs: Any) -> ToolResult:
        self.received.append((tool_name, params))
        return ToolResult(success=True, data={"echo": tool_name})


async def test_market_securities_mapping_routes_to_handler():
    """全链路：router 用 mapping 值调 upstream.call_tool，必须命中 handler 键。

    回归 thsdk.list_market_securities → list_market_securities → Unknown tool：
    映射值是网关 handler 键，不是原生函数名。
    """
    spec = _yaml_tools()["ths_market_securities"]
    mapping = spec["upstream_tool_mapping"]
    fake = _FakeUpstream()
    router = Router(
        upstreams={"thsdk": fake},
        routing_config={"ths_market_securities": {"chain": ["thsdk"]}},
        upstream_tool_mapping={"ths_market_securities": mapping},
    )

    result = await router.route(
        "ths_market_securities", {"market": "USHA"}
    )
    assert result.success
    assert fake.received[0][0] == "ths_market_securities"


def test_all_thsdk_yaml_mappings_resolve_to_handlers():
    """防整类 bug：YAML 中每个 thsdk 映射值都必须有对应 _tool_<value>。"""
    client = _client()
    bad: list[tuple[str, str]] = []
    for name, spec in _yaml_tools().items():
        mapping = spec.get("upstream_tool_mapping") or {}
        # 与 router 一致：未声明映射时回退为网关工具名本身
        value = mapping.get("thsdk", name)
        if not hasattr(client, f"_tool_{value}"):
            bad.append((name, value))
    assert bad == []


# ========== Fix C：thsdk 专用单 worker 执行器 ==========

def test_thsdk_executor_single_worker_singleton():
    ex = get_thsdk_executor()
    assert ex._max_workers == 1
    assert get_thsdk_executor() is ex
    shutdown_thsdk_executor()
    assert get_thsdk_executor() is not ex


def test_queued_job_cancelable_single_worker():
    """单 worker 被卡住时，排队中的第二个任务可取消，不会变僵尸执行。"""
    ex = get_thsdk_executor()
    started = threading.Event()
    release = threading.Event()

    def _first() -> None:
        started.set()
        release.wait(5)

    def _second() -> None:
        pytest.fail("queued job must not run after cancel")

    f1 = ex.submit(_first)
    assert started.wait(2)
    try:
        f2 = ex.submit(_second)
        assert f2.cancel() is True
        assert f2.cancelled()
    finally:
        release.set()
        f1.result(3)


def test_throttle_uses_thsdk_executor(monkeypatch: pytest.MonkeyPatch):
    client = _client()
    assert client._throttle._executor_factory() is get_thsdk_executor()


async def test_throttle_serializes_concurrent_calls():
    """并发两个调用必须严格串行（单 worker + asyncio 锁），第二个在第一个完成后才进 DLL。"""
    throttle = _Throttle(0.0, get_thsdk_executor)
    loop = asyncio.get_running_loop()
    order: list[tuple[str, str]] = []

    def _make(label: str, delay: float):
        def _fn() -> None:
            order.append(("start", label))
            time.sleep(delay)
            order.append(("end", label))
        return _fn

    async def _run(label: str, delay: float) -> None:
        await throttle.run(loop, _make(label, delay), timeout=5)

    await asyncio.gather(_run("a", 0.2), _run("b", 0.05))
    assert order == [
        ("start", "a"), ("end", "a"),
        ("start", "b"), ("end", "b"),
    ]


# ========== 死工具修复回归：原生必填字段由网关补齐 ==========

def test_typeerror_keyerror_classified_invalid_param():
    err = classify_exception(TypeError("缺少必填请求字段: window"))
    assert err.error_type is ThsdkErrorType.INVALID_PARAM
    assert err.recoverable is False
    err2 = classify_exception(KeyError("metrics"))
    assert err2.error_type is ThsdkErrorType.INVALID_PARAM


async def test_ticks_default_count_window(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_ticks = MagicMock(return_value=[])

    result = await client.call_tool(
        "ths_list_security_ticks", {"security": "USHA600519"}
    )
    assert result.success
    kwargs = mod.list_security_ticks.call_args.kwargs
    assert kwargs["window"] == {"count": 100}


async def test_ticks_time_range_window(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_ticks = MagicMock(return_value=[])

    result = await client.call_tool(
        "ths_list_security_ticks",
        {
            "security": "USHA600519",
            "start_time": "2026-09-20T09:30:00+08:00",
            "end_time": "2026-09-20T11:30:00+08:00",
        },
    )
    assert result.success
    kwargs = mod.list_security_ticks.call_args.kwargs
    assert kwargs["window"] == {
        "start": "2026-09-20T09:30:00+08:00",
        "end": "2026-09-20T11:30:00+08:00",
    }


async def test_ticks_half_range_rejected(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_ticks = MagicMock()

    result = await client.call_tool(
        "ths_list_security_ticks",
        {"security": "USHA600519", "start_time": "2026-09-20T09:30:00+08:00"},
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "invalid_param"
    mod.list_security_ticks.assert_not_called()


async def test_intraday_bars_default_date_today(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_intraday_bars = MagicMock(return_value=[])

    result = await client.call_tool(
        "ths_list_security_intraday_bars", {"security": "USHA600519"}
    )
    assert result.success
    kwargs = mod.list_security_intraday_bars.call_args.kwargs
    assert kwargs["date"] == "0"


async def test_news_events_default_builds_window(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_news_events = MagicMock(return_value=[])

    result = await client.call_tool(
        "ths_list_security_news_events",
        {
            "security": "USHA600519",
            "start": "2026-08-01T00:00:00+08:00",
            "end": "2026-09-02T23:59:59+08:00",
        },
    )
    assert result.success
    kwargs = mod.list_security_news_events.call_args.kwargs
    assert kwargs["timeline"] == "historical"
    assert kwargs["window"] == {
        "start": "2026-08-01T00:00:00+08:00",
        "end": "2026-09-02T23:59:59+08:00",
    }


async def test_news_events_raw_window_door_closed(monkeypatch: pytest.MonkeyPatch):
    """F8：未在 spec 声明的 window 直传暗门已删——无 start/end 一律参数错误。

    旧实现把调用方塞的 window 原样下发原生层，绕过网关参数校验；
    现在 window 只能由 start/end 构造。
    """
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_news_events = MagicMock(return_value=[])
    raw = {"since": "2026-09-01T00:00:00+08:00"}

    result = await client.call_tool(
        "ths_list_security_news_events",
        {"security": "USHA600519", "timeline": "realtime", "window": raw},
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "invalid_param"
    mod.list_security_news_events.assert_not_called()


async def test_news_events_missing_window_rejected(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_news_events = MagicMock()

    result = await client.call_tool(
        "ths_list_security_news_events", {"security": "USHA600519"}
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "invalid_param"
    mod.list_security_news_events.assert_not_called()


async def test_realtime_statistics_default_all_metrics(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.calculate_security_realtime_statistics = MagicMock(return_value=[])

    result = await client.call_tool(
        "ths_calculate_security_realtime_statistics",
        {"security": "USZA300033"},
    )
    assert result.success
    kwargs = mod.calculate_security_realtime_statistics.call_args.kwargs
    assert kwargs["securities"] == ["USZA300033"]
    assert sorted(kwargs["metrics"]) == sorted(
        [330323, 330324, 330325, 330329]
    )


async def test_realtime_statistics_named_metrics(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.calculate_security_realtime_statistics = MagicMock(return_value=[])

    result = await client.call_tool(
        "ths_calculate_security_realtime_statistics",
        {
            "security": "USZA300033",
            "metrics": ["limit_up_state", "limit_up_type"],
        },
    )
    assert result.success
    kwargs = mod.calculate_security_realtime_statistics.call_args.kwargs
    assert kwargs["metrics"] == [330329, 330325]


async def test_realtime_statistics_bad_metric_rejected(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.calculate_security_realtime_statistics = MagicMock()

    result = await client.call_tool(
        "ths_calculate_security_realtime_statistics",
        {"security": "USZA300033", "metrics": ["nope"]},
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "invalid_param"
    mod.calculate_security_realtime_statistics.assert_not_called()


async def test_corporate_actions_defaults(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_corporate_actions = MagicMock(return_value=[])

    result = await client.call_tool(
        "ths_list_security_corporate_actions",
        {"security": "USHA600519"},
    )
    assert result.success
    kwargs = mod.list_security_corporate_actions.call_args.kwargs
    assert kwargs["start"] == -10
    assert kwargs["end"] == 0
    assert kwargs["adjust"] == 0
    assert kwargs["period"] == 0x4000


async def test_corporate_actions_custom_enums(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_corporate_actions = MagicMock(return_value=[])

    result = await client.call_tool(
        "ths_list_security_corporate_actions",
        {
            "security": "USHA600519",
            "start": -20,
            "end": -1,
            "adjust": "forward",
            "period": "week",
        },
    )
    assert result.success
    kwargs = mod.list_security_corporate_actions.call_args.kwargs
    assert kwargs["start"] == -20
    assert kwargs["end"] == -1
    assert kwargs["adjust"] == 1
    assert kwargs["period"] == 0x5001


async def test_financial_snapshots_default_all_fields(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_financial_snapshots = MagicMock(return_value=[])

    result = await client.call_tool(
        "ths_list_security_financial_snapshots",
        {"securities": ["USHA600519"]},
    )
    assert result.success
    kwargs = mod.list_security_financial_snapshots.call_args.kwargs
    assert kwargs["fields"] == [
        "total_shares", "float_shares", "net_asset_per_share"
    ]


async def test_financial_snapshots_validates_fields(monkeypatch: pytest.MonkeyPatch):
    client, mod, _ = await _started(monkeypatch)
    mod.list_security_financial_snapshots = MagicMock()

    result = await client.call_tool(
        "ths_list_security_financial_snapshots",
        {"securities": ["USHA600519"], "fields": ["bogus"]},
    )
    assert result.success is False
    assert result.error_detail["error_type"] == "invalid_param"
    mod.list_security_financial_snapshots.assert_not_called()
