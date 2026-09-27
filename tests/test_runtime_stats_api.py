"""/api/runtime 网关运行态端点测试 (2026-09-23 网关运行监控页 M1)。

覆盖:
- CachedExecutionMixin.get_runtime_snapshot: pid / 运行时长 / 在途 /
  工具数 / 近 1 分钟、5 分钟调用计数
- _record_tool_call: 顺带修剪回溯窗口外时间戳
- runtime_stats_api: 200 / 503 (网关未注入) / 500 (快照异常)
- build_upstream_routes 注册 /api/runtime
"""
import json
import os
import time
from collections import deque

import pytest

from src.unihive.gateway_server.cached_calls import CachedExecutionMixin
from src.unihive.utils import console_api
from src.unihive.utils.console_api import runtime_stats_api


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_req():
    from starlette.requests import Request as StarletteRequest

    async def receive():
        return {"type": "http.request", "body": b""}

    return StarletteRequest(
        scope={
            "type": "http",
            "path_params": {},
            "query_string": b"",
            "headers": [],
        },
        receive=receive,
    )


def _stub_server(*, age_sec=90.0, stamps=(), active=0, tool_count=0):
    """装出 get_runtime_snapshot 所需属性的裸对象。"""
    server = CachedExecutionMixin.__new__(CachedExecutionMixin)
    now_mono = time.monotonic()
    server._started_monotonic = now_mono - age_sec
    server._call_timestamps = deque(now_mono - offset for offset in stamps)
    server._active_requests = active
    server.tool_specs = [{} for _ in range(tool_count)]
    return server


class _ApiServer:
    def __init__(self, snapshot=None, raises=False):
        self._snapshot = snapshot or {}
        self._raises = raises

    def get_runtime_snapshot(self):
        if self._raises:
            raise RuntimeError("boom")
        return self._snapshot


# ---------------------------------------------------------------------------
# get_runtime_snapshot
# ---------------------------------------------------------------------------


def test_snapshot_counts_and_fields():
    # offsets: 10s/40s 落在 1 分钟内; 90s/200s 落在 5 分钟内; 400s 已出窗
    server = _stub_server(
        age_sec=120.0,
        stamps=(10, 40, 90, 200, 400),
        active=2,
        tool_count=212,
    )
    snap = server.get_runtime_snapshot()

    assert snap["pid"] == os.getpid()
    assert snap["uptime_seconds"] == pytest.approx(120.0, abs=0.5)
    assert snap["active_requests"] == 2
    assert snap["tool_count"] == 212
    assert snap["calls_last_1m"] == 2
    assert snap["calls_last_5m"] == 4
    assert "calls_per_min" not in snap


def test_snapshot_null_uptime_before_ready():
    server = _stub_server(stamps=())
    server._started_monotonic = None
    snap = server.get_runtime_snapshot()
    assert snap["uptime_seconds"] is None


def test_record_tool_call_appends_and_prunes():
    server = _stub_server(stamps=(400, 100))
    server._record_tool_call()

    # 400s 外的旧戳被修剪; 100s 保留 + 新增的 0s
    offsets = [time.monotonic() - ts for ts in server._call_timestamps]
    assert len(offsets) == 2
    assert min(offsets) == pytest.approx(0, abs=0.1)
    assert max(offsets) == pytest.approx(100, abs=0.5)


# ---------------------------------------------------------------------------
# runtime_stats_api
# ---------------------------------------------------------------------------


def test_runtime_api_200():
    import asyncio

    api_server = _ApiServer(snapshot={
        "pid": 4242,
        "uptime_seconds": 300.5,
        "active_requests": 1,
        "tool_count": 212,
        "calls_last_1m": 3,
        "calls_last_5m": 10,
    })
    console_api.set_gateway_server(api_server)
    try:
        resp = asyncio.run(runtime_stats_api(_make_req()))
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["pid"] == 4242
        assert body["tool_count"] == 212
        assert body["calls_last_5m"] == 10
        assert "timestamp" in body
    finally:
        console_api.set_gateway_server(None)


def test_runtime_api_503_without_server():
    import asyncio

    console_api.set_gateway_server(None)
    resp = asyncio.run(runtime_stats_api(_make_req()))
    assert resp.status_code == 503


def test_runtime_api_500_on_snapshot_error():
    import asyncio

    console_api.set_gateway_server(_ApiServer(raises=True))
    try:
        resp = asyncio.run(runtime_stats_api(_make_req()))
        assert resp.status_code == 500
    finally:
        console_api.set_gateway_server(None)


def test_route_registered():
    paths = {route.path for route in console_api.build_upstream_routes()}
    assert "/api/runtime" in paths
