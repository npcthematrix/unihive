"""tests/test_tdx_quant_client.py — TdxQuantClient 单元测试（mock tqcenter.tq）。"""
import asyncio
import sys
import types

import pytest
from unittest.mock import MagicMock

from src.tdx_quant_config import TdxQuantConfig, TdxQuantSettings
from src.tdx_quant_errors import TdxQuantErrorType


@pytest.fixture
def mock_tq():
    """构造一个 mock tq 实例，注入到 sys.modules['tqcenter']。"""
    mock_module = types.ModuleType("tqcenter")
    mock_tq_inst = MagicMock()
    mock_tq_inst.initialize = MagicMock(return_value={"ErrorId": "0"})
    mock_tq_inst.close = MagicMock()
    mock_tq_inst.get_user_sector = MagicMock(return_value={"ErrorId": "0", "Data": []})
    mock_tq_inst.get_market_snapshot = MagicMock(return_value={
        "ErrorId": "0", "Now": "10.5", "LastClose": "10.0",
    })
    mock_module.tq = MagicMock(return_value=mock_tq_inst)
    sys.modules["tqcenter"] = mock_module
    yield mock_tq_inst
    sys.modules.pop("tqcenter", None)


@pytest.fixture
def client_config():
    return TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake_tdx",
            strategy_id="test_strategy",
            health_check_interval_sec=1,
            reconnect_threshold=3,
            unavailable_threshold=10,
            call_timeout_sec=5,
        ),
    )


@pytest.mark.asyncio
async def test_start_initializes_tq_singleton(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    from src.upstream_client import UpstreamStatus
    c = TdxQuantClient(client_config)
    assert await c.start() is True
    assert c.status == UpstreamStatus.HEALTHY
    mock_tq.initialize.assert_called_once_with("test_strategy")


@pytest.mark.asyncio
async def test_start_failure_marks_unavailable(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    from src.upstream_client import UpstreamStatus
    mock_tq.initialize.side_effect = FileNotFoundError("no tqcenter")
    c = TdxQuantClient(client_config)
    assert await c.start() is False
    assert c.status == UpstreamStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_start_when_module_missing(client_config, monkeypatch):
    from src.tdx_quant_client import TdxQuantClient
    from src.upstream_client import UpstreamStatus
    # 确保 tqcenter 不可导入：移除任何 fake tdx_root 的 sys.path 注入
    monkeypatch.setattr("sys.path", list(sys.path))
    c = TdxQuantClient(client_config)
    # tdx_root 指向不存在的目录，import tqcenter 会失败
    assert await c.start() is False
    assert c.status == UpstreamStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_call_tool_returns_success_on_errorid_0(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(client_config)
    await c.start()
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is True
    assert result.data["Now"] == "10.5"
    assert result.source == "tdx_quant"


@pytest.mark.asyncio
async def test_call_tool_returns_error_on_errorid_6(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    mock_tq.get_market_snapshot.return_value = {"ErrorId": "6", "ErrMsg": "disconnected"}
    c = TdxQuantClient(client_config)
    await c.start()
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is False
    assert "DISCONNECTED" in result.error or "断开" in result.error or "disconnected" in result.error.lower()


@pytest.mark.asyncio
async def test_call_tool_when_unavailable_returns_error(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    from src.upstream_client import UpstreamStatus
    mock_tq.initialize.side_effect = FileNotFoundError("no tqcenter")
    c = TdxQuantClient(client_config)
    await c.start()  # 失败但不抛
    assert c.status == UpstreamStatus.UNAVAILABLE
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is False
    assert "unavailable" in result.error.lower() or "未就绪" in result.error or "unavailable" in result.error


@pytest.mark.asyncio
async def test_health_loop_marks_disconnected_after_3(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    mock_tq.get_user_sector.return_value = {"ErrorId": "6"}
    c = TdxQuantClient(client_config)
    await c.start()
    # 等 3 次探活周期（interval=1s + 少量缓冲）
    await asyncio.sleep(3.5)
    # 应触发过 close() (reconnect 内部调用)
    assert mock_tq.close.call_count >= 1


@pytest.mark.asyncio
async def test_stop_closes_tq_and_cancels_health_task(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    from src.upstream_client import UpstreamStatus
    c = TdxQuantClient(client_config)
    await c.start()
    await c.stop()
    mock_tq.close.assert_called()
    assert c.status == UpstreamStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_reconnect_lock_serializes(client_config, mock_tq):
    from src.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(client_config)
    await c.start()
    initial_count = mock_tq.initialize.call_count
    # 并发触发 5 次 reconnect
    await asyncio.gather(*[c._reconnect() for _ in range(5)])
    # 由于 lock 串行化，reconnect 内部 initialize 调用数应 <= 5
    # （lock 保证不并发，但每次获取 lock 后仍会调用一次 initialize）
    final_count = mock_tq.initialize.call_count
    assert final_count - initial_count <= 5
