"""TdxQuant 准确状态判定测试。

验证两件事:
1. get_metrics() 返回真实信号 (port_reachable / tq_active)
2. console_api._client_status_bucket 不再把 DEGRADED 折叠成 online
"""
from types import SimpleNamespace

import pytest

from src.unihive.api.tdx_quant_client import TdxQuantClient
from src.unihive.api.upstream_client import UpstreamStatus
from src.unihive.models.tdx_quant_config import (
    TdxQuantConfig,
    TdxQuantSettings,
)
from src.unihive.utils.console_api import _client_status_bucket


def _client() -> TdxQuantClient:
    return TdxQuantClient(
        TdxQuantConfig(
            name="tdx_quant",
            settings=TdxQuantSettings(tdx_root="D:/new_tdx_mock"),
        )
    )


def test_get_metrics_when_port_open():
    client = _client()
    client._status = UpstreamStatus.HEALTHY
    snap = client.get_metrics()
    # 当前环境 17709 确实在监听
    assert snap["port_reachable"] is True
    assert snap["tq_active"] is True
    assert snap["status"] == "healthy"
    assert snap["port"] == 17709


def test_get_metrics_degraded_state():
    client = _client()
    client._status = UpstreamStatus.DEGRADED
    snap = client.get_metrics()
    # 端口仍可达 (窗口关闭不停服务), 但 TQ 接口非 active
    assert snap["port_reachable"] is True
    assert snap["tq_active"] is False
    assert snap["available"] is True  # is_available 对 DEGRADED 为 True (既有契约)


def test_status_bucket_preserves_degraded():
    # 模拟 DEGRADED client: is_available=True 但状态枚举是 degraded
    client = SimpleNamespace(
        status=UpstreamStatus.DEGRADED, is_available=True
    )
    assert _client_status_bucket(client) == "degraded"


def test_status_bucket_maps_enum_values():
    assert _client_status_bucket(
        SimpleNamespace(status=UpstreamStatus.HEALTHY, is_available=True)
    ) == "online"
    assert _client_status_bucket(
        SimpleNamespace(status=UpstreamStatus.UNAVAILABLE, is_available=False)
    ) == "offline"
    assert _client_status_bucket(
        SimpleNamespace(status=UpstreamStatus.UNKNOWN, is_available=False)
    ) == "unknown"


def test_status_bucket_fallback_for_bare_double():
    # 无 status 属性时退回 is_available 二值
    assert _client_status_bucket(SimpleNamespace(is_available=True)) == "online"
    assert _client_status_bucket(SimpleNamespace(is_available=False)) == "offline"
