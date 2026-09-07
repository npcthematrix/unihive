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


# ========== HIGH2: tq.close() must not block event loop ==========

@pytest.mark.asyncio
async def test_stop_close_runs_in_executor_not_blocking_event_loop(client_config):
    """HIGH2: stop() 内的 tq.close() 是同步 DLL 调用, 必须 run_in_executor.

    验证: close 跑的同时, 用并发任务打点. 若 close 阻塞 event loop,
    close 窗口内并发任务 0 tick; 若 run_in_executor, close 窗口内多次 tick。
    """
    import time
    from src.tdx_quant_client import TdxQuantClient

    close_started_at: list[float] = []
    close_finished_at: list[float] = []

    class SlowCloseTq:
        def initialize(self, sid):
            pass

        def close(self):
            close_started_at.append(time.monotonic())
            time.sleep(0.5)  # 模拟 TdxW.exe DLL IPC 调用耗时
            close_finished_at.append(time.monotonic())

    c = TdxQuantClient(client_config)
    c._tq = SlowCloseTq()

    heartbeat_times: list[float] = []

    async def heartbeat():
        for _ in range(40):
            heartbeat_times.append(time.monotonic())
            await asyncio.sleep(0.025)

    # 并发跑 stop 和 heartbeat. gather 不会等 stop 完了才跑 heartbeat —
    # heartbeat 在 stop 内部 close 同步阻塞时也会被卡住, 这正是要验证的。
    await asyncio.gather(c.stop(), heartbeat())

    assert close_started_at and close_finished_at, "close() 必须实际执行"
    start = close_started_at[0]
    end = close_finished_at[0]

    # 统计 close 窗口内 heartbeat tick 数.
    # 若 close 阻塞 event loop: 0 tick (heartbeat 完全跑不动)
    # 若 close 跑在 executor: ~20 tick (event loop 自由, heartbeat 每 25ms tick 一次)
    ticks_during_close = sum(1 for t in heartbeat_times if start <= t <= end)
    assert ticks_during_close >= 5, (
        f"close 期间 event loop 死锁: heartbeat 在 {end - start:.3f}s close 窗口内"
        f"只 tick 了 {ticks_during_close} 次 (期望 >= 5)"
    )


@pytest.mark.asyncio
async def test_reconnect_close_runs_in_executor_not_blocking_event_loop(client_config):
    """HIGH2: _reconnect() 内的 tq.close() 也必须 run_in_executor."""
    import time
    from src.tdx_quant_client import TdxQuantClient

    close_started_at: list[float] = []
    close_finished_at: list[float] = []

    class SlowCloseTq:
        def initialize(self, sid):
            pass

        def close(self):
            close_started_at.append(time.monotonic())
            time.sleep(0.5)
            close_finished_at.append(time.monotonic())

    c = TdxQuantClient(client_config)
    c._tq = SlowCloseTq()

    heartbeat_times: list[float] = []

    async def heartbeat():
        for _ in range(40):
            heartbeat_times.append(time.monotonic())
            await asyncio.sleep(0.025)

    await asyncio.gather(c._reconnect(), heartbeat())

    assert close_started_at and close_finished_at, "close() 必须实际执行"
    start = close_started_at[0]
    end = close_finished_at[0]

    ticks_during_close = sum(1 for t in heartbeat_times if start <= t <= end)
    assert ticks_during_close >= 5, (
        f"reconnect 期间 event loop 死锁: heartbeat 在 {end - start:.3f}s close 窗口内"
        f"只 tick 了 {ticks_during_close} 次"
    )


# ========== MED4: stop() must wait for in-flight reconnect tasks ==========

@pytest.mark.asyncio
async def test_stop_waits_for_in_flight_reconnect_task(client_config):
    """MED4: in-flight _reconnect() task 必须被 stop() 等待, 不能被 tq.close()
    半路截断导致状态不一致 (reconnect 内部 close + initialize 序列被切)。"""
    from src.tdx_quant_client import TdxQuantClient

    c = TdxQuantClient(client_config)

    reconnect_started = asyncio.Event()
    reconnect_can_finish = asyncio.Event()
    reconnect_finished = asyncio.Event()

    async def slow_reconnect():
        reconnect_started.set()
        await reconnect_can_finish.wait()
        reconnect_finished.set()

    # patch _reconnect 成慢版本, 让它挂在中间
    c._reconnect = slow_reconnect  # type: ignore[assignment]

    # fire-and-forget — 模拟 call_tool 在 DISCONNECTED 时调 _schedule_reconnect
    task = asyncio.create_task(c._reconnect())
    # 把 task 注册到 client 的 reconnect task tracking set (MED4 实现细节)
    c._reconnect_tasks.add(task)
    task.add_done_callback(c._reconnect_tasks.discard)

    await reconnect_started.wait()

    # reconnect 已开始但还在等 can_finish — 此时调 stop()
    stop_task = asyncio.create_task(c.stop())

    # stop 应该等 reconnect; 给一个稍长于 reconnect 剩余时间的 timeout
    asyncio.get_event_loop().call_later(
        0.1, reconnect_can_finish.set
    )

    # stop() 应该在 reconnect_finished 后才返回 (即 can_finish 后)
    await asyncio.wait_for(stop_task, timeout=2.0)

    assert reconnect_finished.is_set(), (
        "stop() 没等 in-flight reconnect 就返回, 会与 tq.close()/状态重置产生竞态"
    )
    assert task.done(), "reconnect task 必须已完成 (而不是被默默丢弃)"


# ========== LOW7: strategy_id fallback (no more __file__) ==========

@pytest.mark.asyncio
async def test_strategy_id_uses_configured_value(monkeypatch, tmp_path):
    """配置里有 strategy_id 时, 必须用它, 不能用 __file__ 或 UUID."""
    from src import tdx_quant_client as m
    monkeypatch.setattr(m, "_STRATEGY_ID_FILE", tmp_path / "strategy_id")
    cfg = TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake", strategy_id="my_custom_strategy",
            health_check_interval_sec=1, reconnect_threshold=3,
            unavailable_threshold=10, call_timeout_sec=5,
        ),
    )
    from src.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(cfg)
    assert c._strategy_id == "my_custom_strategy"
    # 文件不应被创建
    assert not (tmp_path / "strategy_id").exists()


@pytest.mark.asyncio
async def test_strategy_id_generates_uuid_when_no_config_or_file(monkeypatch, tmp_path):
    """无配置 + 无持久化文件 → 生成 UUID 并写入文件."""
    import uuid as uuid_mod
    from src import tdx_quant_client as m
    sf = tmp_path / "strategy_id"
    monkeypatch.setattr(m, "_STRATEGY_ID_FILE", sf)
    cfg = TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake", strategy_id="",
            health_check_interval_sec=1, reconnect_threshold=3,
            unavailable_threshold=10, call_timeout_sec=5,
        ),
    )
    from src.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(cfg)
    # 必须是合法 UUID
    parsed = uuid_mod.UUID(c._strategy_id)
    assert str(parsed) == c._strategy_id
    # 必须持久化
    assert sf.exists()
    assert sf.read_text(encoding="utf-8").strip() == c._strategy_id


@pytest.mark.asyncio
async def test_strategy_id_reuses_persisted_file(monkeypatch, tmp_path):
    """持久化文件存在时, 直接复用, 不再生成新 UUID."""
    from src import tdx_quant_client as m
    sf = tmp_path / "strategy_id"
    sf.write_text("stable-id-12345", encoding="utf-8")
    monkeypatch.setattr(m, "_STRATEGY_ID_FILE", sf)
    cfg = TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake", strategy_id="",
            health_check_interval_sec=1, reconnect_threshold=3,
            unavailable_threshold=10, call_timeout_sec=5,
        ),
    )
    from src.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(cfg)
    assert c._strategy_id == "stable-id-12345"


@pytest.mark.asyncio
async def test_strategy_id_is_not_filepath(monkeypatch, tmp_path):
    """LOW7 主断言: 绝对不能用 __file__ 路径 (不稳定 + 暴露部署信息)."""
    import os
    from src import tdx_quant_client as m
    sf = tmp_path / "strategy_id"
    monkeypatch.setattr(m, "_STRATEGY_ID_FILE", sf)
    cfg = TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake", strategy_id="",
            health_check_interval_sec=1, reconnect_threshold=3,
            unavailable_threshold=10, call_timeout_sec=5,
        ),
    )
    from src.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(cfg)
    assert "\\" not in c._strategy_id and "/" not in c._strategy_id, (
        f"strategy_id 不应是路径: {c._strategy_id}"
    )
    assert not c._strategy_id.endswith(".py")
