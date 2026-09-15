"""console_api.upstream_metrics_api + set_upstream_registry 集成测试."""
import json

from starlette.requests import Request as StarletteRequest

from src.unihive.utils import console_api
from src.unihive.utils.console_api import upstream_metrics_api


class FakeClient:
    def __init__(self, metrics):
        self._metrics = metrics

    def get_metrics(self):
        return self._metrics


class FakeClientNoMetrics:
    pass  # no get_metrics()


def _make_req(name: str) -> StarletteRequest:
    """构造一个最小可用的 Starlette Request stub (handler 只需 path_params)."""
    return StarletteRequest(
        scope={"type": "http", "path_params": {"name": name}}
    )


def test_set_registry_and_lookup():
    """set_upstream_registry 后, _get_upstream_client 能找到."""
    fake = FakeClient({"daily_l1": {"hits": 1}})
    console_api.set_upstream_registry({"mootdx2": fake})
    assert console_api._get_upstream_client("mootdx2") is fake
    assert console_api._get_upstream_client("nonexistent") is None


def test_set_registry_overwrites_previous():
    """重复调用 set_upstream_registry 应清空旧引用, 避免泄漏."""
    console_api.set_upstream_registry({"old": FakeClient({})})
    console_api.set_upstream_registry({"new": FakeClient({"x": 1})})
    assert console_api._get_upstream_client("old") is None
    assert console_api._get_upstream_client("new") is not None


def test_get_metrics_returns_501_for_no_get_metrics():
    """未实现 get_metrics() 的 client 应返回 501."""
    import asyncio
    console_api.set_upstream_registry({"foo": FakeClientNoMetrics()})
    resp = asyncio.run(upstream_metrics_api(_make_req("foo")))
    assert resp.status_code == 501


def test_get_metrics_returns_404_for_unknown():
    import asyncio
    console_api.set_upstream_registry({})
    resp = asyncio.run(upstream_metrics_api(_make_req("x")))
    assert resp.status_code == 404


def test_get_metrics_returns_200_with_payload():
    import asyncio
    fake = FakeClient({"daily_l1": {"hits": 1, "misses": 2}, "total_requests": 3})
    console_api.set_upstream_registry({"mootdx2": fake})
    resp = asyncio.run(upstream_metrics_api(_make_req("mootdx2")))
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["upstream"] == "mootdx2"
    assert body["metrics"]["daily_l1"]["hits"] == 1
    assert "timestamp" in body
