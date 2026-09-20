"""成功路径 warn 透传契约（2026-09-20）。

旧链路在 router 成功分支只取 data/source/hops，ToolResult.error 与
error_detail.warn 被丢弃，HTTP 消费者拿到裸 success=true。契约：
  - error_detail.warn（消歧/truncated/部分降级）→ RouteResult.warn
  - success + data=None + error 文本（no_data 业务态）→ RouteResult.warn
  - 普通成功无提示 → warn is None
"""
import pytest

from src.unihive.api.upstream_client import ToolResult, UpstreamStatus
from src.unihive.core.router import Router, _warn_from_tool_result


class FakeUpstream:
    def __init__(self, result):
        self.name = "mootdx2"
        self._result = result
        self.calls = []

    @property
    def status(self):
        return UpstreamStatus.HEALTHY

    @property
    def is_available(self):
        return True

    async def call_tool(self, tool, params):
        self.calls.append((tool, params))
        return self._result


def _router(result, gateway_tool="get_daily_local"):
    return Router(
        upstreams={"mootdx2": FakeUpstream(result)},
        routing_config={gateway_tool: {"chain": ["mootdx2"]}},
    )


def test_extract_explicit_warn():
    r = ToolResult(
        success=True, data=[{"x": 1}],
        error_detail={"warn": "代码 000001 歧义", "recoverable": True},
    )
    assert _warn_from_tool_result(r) == "代码 000001 歧义"


def test_extract_no_data_message_as_warn():
    r = ToolResult(
        success=True, data=None, error="指数 sh932000 本地无日线文件",
        error_detail={"error_type": "no_data"},
    )
    assert _warn_from_tool_result(r) == "指数 sh932000 本地无日线文件"


def test_extract_plain_success_is_none():
    assert _warn_from_tool_result(ToolResult(success=True, data=[{"x": 1}])) is None
    assert _warn_from_tool_result(ToolResult(success=True, data=[])) is None


def test_unmarked_success_none_error_stays_hidden():
    """无 no_data 标记的 success+None+error 不提升为 warn（约定未声明，保持隐藏）。"""
    r = ToolResult(
        success=True, data=None, error="Sector not found",
        error_detail={"error_type": "other"},
    )
    assert _warn_from_tool_result(r) is None


@pytest.mark.asyncio
async def test_route_success_carries_warn():
    result = ToolResult(
        success=True, data=[{"date": "2026-09-18"}], source="mootdx2",
        error_detail={"warn": "歧义码提示", "recoverable": True},
    )
    out = await _router(result).route("get_daily_local", {"code": "000001"})
    assert out.success is True
    assert out.warn == "歧义码提示"


@pytest.mark.asyncio
async def test_route_no_data_message_reaches_route_result():
    result = ToolResult(
        success=True, data=None, source="mootdx2",
        error="指数 sh932000 本地无日线文件（未下载）",
        error_detail={"error_type": "no_data", "message": "指数 sh932000 本地无日线文件（未下载）"},
    )
    out = await _router(result, "get_index_kline_local").route(
        "get_index_kline_local", {"code": "932000"}
    )
    assert out.success is True and out.data is None
    assert "sh932000" in out.warn
