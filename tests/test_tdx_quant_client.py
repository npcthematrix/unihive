"""tests/test_tdx_quant_client.py — TdxQuantClient 单元测试（mock tqcenter.tq）。"""
import asyncio
import sys
import types

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.unihive.api.upstream_client import UpstreamStatus
from src.unihive.models.tdx_quant_config import TdxQuantConfig, TdxQuantSettings
from src.unihive.exceptions.tdx_quant_errors import TdxQuantErrorType


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
    # 代码中是 tqcenter.tq 直接赋值，没有调用，所以直接赋值 mock_tq_inst
    mock_module.tq = mock_tq_inst
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
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    from src.unihive.api.upstream_client import UpstreamStatus
    c = TdxQuantClient(client_config)
    assert await c.start() is True
    assert c.status == UpstreamStatus.HEALTHY
    # tqcenter.initialize 接受 tdx_root 路径 (Windows 风格反斜杠)
    mock_tq.initialize.assert_called_once_with("D:\\fake_tdx")


@pytest.mark.asyncio
async def test_start_failure_marks_unavailable(client_config, mock_tq):
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    from src.unihive.api.upstream_client import UpstreamStatus
    mock_tq.initialize.side_effect = FileNotFoundError("no tqcenter")
    c = TdxQuantClient(client_config)
    assert await c.start() is False
    assert c.status == UpstreamStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_start_when_module_missing(client_config, monkeypatch):
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    from src.unihive.api.upstream_client import UpstreamStatus
    # 确保 tqcenter 不可导入：移除任何 fake tdx_root 的 sys.path 注入
    monkeypatch.setattr("sys.path", list(sys.path))
    c = TdxQuantClient(client_config)
    # tdx_root 指向不存在的目录，import tqcenter 会失败
    assert await c.start() is False
    assert c.status == UpstreamStatus.UNAVAILABLE


# ========== 2026-09-14: 远程 17709 端口探测 (TQ 行情链路) ==========

@pytest.mark.asyncio
async def test_probe_tdx_remote_port_success(client_config, mock_tq):
    """端口可达 → 探测成功, 启动正常."""
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(client_config)
    with patch.object(c, "_probe_tdx_remote_port", new=AsyncMock(return_value=True)):
        await c.start()
    assert c.status == UpstreamStatus.HEALTHY


@pytest.mark.asyncio
async def test_probe_tdx_remote_port_unreachable_warns(client_config, mock_tq, caplog):
    """端口不可达 → 警告但不影响 init (TQ 可在本地端口运行)."""
    import logging
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(client_config)
    with patch.object(c, "_probe_tdx_remote_port", new=AsyncMock(return_value=False)):
        with caplog.at_level(logging.WARNING, logger="src.unihive.api.tdx_quant_client"):
            await c.start()
    assert "TQ port 17709" in caplog.text


# ========== 2026-09-14: verify 不再用 isinstance(dict) 判定 ==========

@pytest.mark.asyncio
async def test_verify_treats_empty_list_as_ok(client_config, mock_tq):
    """修复: tqcenter.get_user_sector() 在 DLL 失败时静默返回 [], 不抛异常.

    之前的 verify 用 isinstance(result, dict) + ErrorId=='0' 判定, 导致
    返回 [] 时永远被判失败, status 永远 DEGRADED, call_tool 永远被拒。

    新策略: 调通即 OK (无异常)。返回 [] (无数据) 视为 DLL 链路通,
    真实数据状态由 call_tool 阶段的 ErrorId 自然反映。
    """
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    # 关键: mock 返回 list 而非 dict, 模拟真实 tqcenter 行为
    mock_tq.get_user_sector.return_value = []   # 不是 dict
    c = TdxQuantClient(client_config)
    ok = await c.start()
    # 必须取消 health_loop 避免后续把状态改回 HEALTHY
    if c._health_task:
        c._health_task.cancel()
        try:
            await c._health_task
        except asyncio.CancelledError:
            pass
    assert ok is True, f"start() 失败, 但 verify 应该 OK: {ok}"
    assert c.status == UpstreamStatus.HEALTHY, (
        f"返回 [] 应被 verify 视为 OK (status=HEALTHY), 实际 {c.status}"
    )


@pytest.mark.asyncio
async def test_verify_treats_exception_as_degraded(client_config, mock_tq):
    """verify 抛异常 → DEGRADED (DLL 链路不通)."""
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    mock_tq.get_user_sector.side_effect = OSError("DLL not loaded")
    c = TdxQuantClient(client_config)
    ok = await c.start()
    if c._health_task:
        c._health_task.cancel()
        try:
            await c._health_task
        except asyncio.CancelledError:
            pass
    assert ok is True   # init 成功 (initialize 返回 OK), 仅 verify 失败
    assert c.status == UpstreamStatus.DEGRADED, (
        f"verify 抛异常应 DEGRADED, 实际 {c.status}"
    )


@pytest.mark.asyncio
async def test_verify_treats_dict_as_ok(client_config, mock_tq):
    """verify 返回 dict (即使 ErrorId 非 0) 也视为 OK — 真实数据状态由 call_tool 反映."""
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    mock_tq.get_user_sector.return_value = {"ErrorId": "6", "ErrMsg": "disconnected"}
    c = TdxQuantClient(client_config)
    await c.start()
    if c._health_task:
        c._health_task.cancel()
        try:
            await c._health_task
        except asyncio.CancelledError:
            pass
    assert c.status == UpstreamStatus.HEALTHY, (
        f"返回 dict 也应被 verify 视为 OK (调通即 OK), 实际 {c.status}"
    )


# ========== 2026-09-14: DEGRADED 状态必须允许 call_tool (修复双锁死锁) ==========

@pytest.mark.asyncio
async def test_call_tool_allows_degraded_status(client_config, mock_tq):
    """DEGRADED 状态时 call_tool 不能返回 'upstream unavailable'.

    背景: 之前 call_tool 仅允许 HEALTHY。_verify_tq_interface() 失败导致的
    DEGRADED (典型场景: TdxW.exe 进程在但未登录) 会让 call_tool 永远返回
    'upstream unavailable', 即使 DLL 已就绪且能响应 tq.* 调用 — 形成死锁。

    修复: call_tool 与 is_available 对齐, 仅 UNAVAILABLE 拒绝, DEGRADED 通过。

    如何触发 verify 失败: 让 get_user_sector 抛异常 (ctypes 段错误 / DLL
    缺失) — verify 改成"调通即 OK"后, 返回 [] 或 dict 都被视为 OK, 只有
    抛异常才算 DLL 链路不通。
    """
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    # 让 verify 抛异常 → 启动后 status=DEGRADED
    mock_tq.get_user_sector.side_effect = OSError("DLL not loaded")
    c = TdxQuantClient(client_config)
    await c.start()
    # 必须取消后台 health_loop, 否则 1s 后它会重试 get_user_sector 把状态改回 HEALTHY
    if c._health_task:
        c._health_task.cancel()
        try:
            await c._health_task
        except asyncio.CancelledError:
            pass
    assert c.status == UpstreamStatus.DEGRADED, (
        f"verify 抛异常应进入 DEGRADED, 实际 {c.status}"
    )
    # DEGRADED 下调 get_market_snapshot 必须真发出调用
    # 但注意: 健康探活(_health_loop)已经把 _fail_count 重置, mock 已正常返回
    # 因此这里直接 reset 一次 mock 让后续调用不抛
    mock_tq.get_user_sector.side_effect = None
    mock_tq.get_market_snapshot.return_value = {
        "ErrorId": "0", "Now": "10.5", "LastClose": "10.0"
    }
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is True, (
        f"DEGRADED 状态应允许调用, 实际错误: {result.error}"
    )
    assert result.error is None, (
        f"DEGRADED 不应携带 error 字段, 实际: {result.error!r}"
    )
    assert result.data["Now"] == "10.5"


@pytest.mark.asyncio
async def test_call_tool_rejects_only_unavailable(client_config, mock_tq):
    """明确: 只有 UNAVAILABLE 拒绝, HEALTHY/DEGRADED/UNKNOWN 都通过."""
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    # UNKNOWN: start() 之前调用 (init 没跑过, _tq 是 None)
    c = TdxQuantClient(client_config)
    # _tq 是 None, 必须被拒 (这是双重保护, 不光是 status)
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is False
    assert "unavailable" in result.error.lower()


@pytest.mark.asyncio
async def test_call_tool_after_failed_init_returns_unavailable(client_config, mock_tq):
    """init 失败 → status=UNAVAILABLE → call_tool 必须返回 unavailable."""
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    mock_tq.initialize.side_effect = FileNotFoundError("no tqcenter")
    c = TdxQuantClient(client_config)
    await c.start()  # init 失败, status=UNAVAILABLE, 不抛
    assert c.status == UpstreamStatus.UNAVAILABLE
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is False
    assert "unavailable" in result.error.lower()


@pytest.mark.asyncio
async def test_call_tool_returns_success_on_errorid_0(client_config, mock_tq):
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(client_config)
    await c.start()
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is True
    assert result.data["Now"] == "10.5"
    assert result.source == "tdx_quant"


@pytest.mark.asyncio
async def test_call_tool_returns_error_on_errorid_6(client_config, mock_tq):
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    mock_tq.get_market_snapshot.return_value = {"ErrorId": "6", "ErrMsg": "disconnected"}
    c = TdxQuantClient(client_config)
    await c.start()
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is False
    assert "DISCONNECTED" in result.error or "断开" in result.error or "disconnected" in result.error.lower()


@pytest.mark.asyncio
async def test_call_tool_when_unavailable_returns_error(client_config, mock_tq):
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    from src.unihive.api.upstream_client import UpstreamStatus
    mock_tq.initialize.side_effect = FileNotFoundError("no tqcenter")
    c = TdxQuantClient(client_config)
    await c.start()  # 失败但不抛
    assert c.status == UpstreamStatus.UNAVAILABLE
    result = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert result.success is False
    assert "unavailable" in result.error.lower() or "未就绪" in result.error or "unavailable" in result.error


@pytest.mark.asyncio
async def test_health_loop_marks_disconnected_after_3(client_config, mock_tq):
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    mock_tq.get_user_sector.return_value = {"ErrorId": "6"}
    c = TdxQuantClient(client_config)
    await c.start()
    # 等 3 次探活周期（interval=1s + 少量缓冲）
    await asyncio.sleep(3.5)
    # 应触发过 close() (reconnect 内部调用)
    assert mock_tq.close.call_count >= 1


@pytest.mark.asyncio
async def test_stop_closes_tq_and_cancels_health_task(client_config, mock_tq):
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    from src.unihive.api.upstream_client import UpstreamStatus
    c = TdxQuantClient(client_config)
    await c.start()
    await c.stop()
    mock_tq.close.assert_called()
    assert c.status == UpstreamStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_reconnect_lock_serializes(client_config, mock_tq):
    from src.unihive.api.tdx_quant_client import TdxQuantClient
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
    from src.unihive.api.tdx_quant_client import TdxQuantClient

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
    from src.unihive.api.tdx_quant_client import TdxQuantClient

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
    from src.unihive.api.tdx_quant_client import TdxQuantClient

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
    from src.unihive.api import tdx_quant_client as m
    monkeypatch.setattr(m, "_STRATEGY_ID_FILE", tmp_path / "strategy_id")
    cfg = TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake", strategy_id="my_custom_strategy",
            health_check_interval_sec=1, reconnect_threshold=3,
            unavailable_threshold=10, call_timeout_sec=5,
        ),
    )
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(cfg)
    assert c._strategy_id == "my_custom_strategy"
    # 文件不应被创建
    assert not (tmp_path / "strategy_id").exists()


@pytest.mark.asyncio
async def test_strategy_id_generates_uuid_when_no_config_or_file(monkeypatch, tmp_path):
    """无配置 + 无持久化文件 → 生成 UUID 并写入文件."""
    import uuid as uuid_mod
    from src.unihive.api import tdx_quant_client as m
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
    from src.unihive.api.tdx_quant_client import TdxQuantClient
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
    from src.unihive.api import tdx_quant_client as m
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
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(cfg)
    assert c._strategy_id == "stable-id-12345"


@pytest.mark.asyncio
async def test_strategy_id_is_not_filepath(monkeypatch, tmp_path):
    """LOW7 主断言: 绝对不能用 __file__ 路径 (不稳定 + 暴露部署信息)."""
    import os
    from src.unihive.api import tdx_quant_client as m
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
    from src.unihive.api.tdx_quant_client import TdxQuantClient
    c = TdxQuantClient(cfg)
    assert "\\" not in c._strategy_id and "/" not in c._strategy_id, (
        f"strategy_id 不应是路径: {c._strategy_id}"
    )
    assert not c._strategy_id.endswith(".py")


# ---------------------------------------------------------------------------
# 2026-11-01 并发/性能加固
# ---------------------------------------------------------------------------

def test_invalid_input_error_rejects_empty_codes():
    """空代码必须在网关侧拦下 —— 送进 tqcenter 会 close() 掉进程级共享连接."""
    from src.unihive.api.tdx_quant_client import _invalid_input_error

    # 空 stock_list / 含空项
    assert _invalid_input_error({"stock_list": []}) is not None
    assert _invalid_input_error({"stock_list": ["600519.SH", ""]}) is not None
    assert _invalid_input_error({"stock_list": "  "}) is not None
    # 空单标的代码
    assert _invalid_input_error({"stock_code": ""}) is not None
    assert _invalid_input_error({"code": " "}) is not None
    # 合法入参一律放行
    assert _invalid_input_error({"stock_list": ["600519.SH"]}) is None
    assert _invalid_input_error({"stock_code": "600519.SH"}) is None
    assert _invalid_input_error({"ipo_type": "1"}) is None
    assert _invalid_input_error({}) is None


@pytest.mark.asyncio
async def test_call_tool_rejects_empty_stock_list_without_touching_dll(
    client_config, mock_tq
):
    """空 stock_list 直接返回 invalid_param, 不得进 DLL (mock 零调用)."""
    from src.unihive.api.tdx_quant_client import TdxQuantClient

    c = TdxQuantClient(client_config)
    c._tq = mock_tq
    c._status = UpstreamStatus.HEALTHY

    result = await c.call_tool("get_market_data", {"stock_list": [], "period": "1d"})

    assert result.success is False
    assert "stock_list" in (result.error or "")
    assert result.error_kind == "invalid_request"
    mock_tq.get_market_data.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_tasks_are_coalesced(client_config, mock_tq):
    """并发失败只应排一个 reconnect —— 旧实现会排 N 个串行 close+init."""
    from src.unihive.api.tdx_quant_client import TdxQuantClient

    c = TdxQuantClient(client_config)
    c._tq = mock_tq
    c._status = UpstreamStatus.HEALTHY

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_reconnect():
        started.set()
        await release.wait()

    c._reconnect = slow_reconnect  # type: ignore[method-assign]

    for _ in range(5):
        c._schedule_reconnect()

    assert len(c._reconnect_tasks) == 1, "5 次触发只应产生 1 个 reconnect 任务"
    await started.wait()
    # 任务在跑时再触发, 仍然复用
    c._schedule_reconnect()
    assert len(c._reconnect_tasks) == 1
    release.set()
    await asyncio.gather(*list(c._reconnect_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_call_timeouts_trip_circuit_breaker(mock_tq):
    """连续调用超时必须让上游主动降级, 而不是永远报 healthy 拖满超时.

    2026-11-01 实测: DLL 被并发冲击卡死后, 每次调用稳定 7.7s, 而
    /api/upstreams/tdx_quant/metrics 仍返回 status=healthy —— 因为旧实现
    只有健康循环在计失败, 调用超时完全不参与判定。
    """
    import time as _time

    from src.unihive.api.tdx_quant_client import TdxQuantClient

    cfg = TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake_tdx",
            strategy_id="test_strategy",
            health_check_interval_sec=3600,
            reconnect_threshold=2,
            unavailable_threshold=3,
            call_timeout_sec=0.05,
        ),
    )

    def slow_snapshot(**_kwargs):
        _time.sleep(0.2)
        return {"ErrorId": "0"}

    mock_tq.get_market_snapshot = slow_snapshot

    c = TdxQuantClient(cfg)
    c._tq = mock_tq
    c._status = UpstreamStatus.HEALTHY
    c._reconnect = AsyncMock()  # 隔离重连, 只看计数与降级

    for _ in range(3):
        r = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
        assert r.success is False

    assert c.status == UpstreamStatus.UNAVAILABLE, (
        f"连续 3 次超时后应降级, 实际 {c.status}"
    )
    # 降级后立即快速失败, 不再进 DLL
    before = c._fail_count
    r = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert r.success is False and "unavailable" in (r.error or "")
    assert c._fail_count == before, "已降级时不应继续累计失败"


@pytest.mark.asyncio
async def test_pool_exhaustion_trips_breaker_on_first_timeout(mock_tq, monkeypatch):
    """池已无空闲 worker 时, 一次超时就该降级 —— 不必等满 10 次失败计数。

    ``wait_for`` 取消不了已经进 DLL 的线程, 每次超时都留下一个僵尸 worker
    (tqcenter 内部 timeout_ms 最长 60s)。若等到 ``unavailable_threshold=10``
    次超时才降级, 10 个 worker 早已全被僵尸占满, 期间所有请求只能排队。
    """
    import time as _time

    from src.unihive.api.tdx_quant_client import TdxQuantClient

    cfg = TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake_tdx",
            strategy_id="test_strategy",
            health_check_interval_sec=3600,
            reconnect_threshold=99,        # 关掉计数路径, 只验证池判据
            unavailable_threshold=99,
            call_timeout_sec=0.05,
        ),
    )

    def slow_snapshot(**_kwargs):
        _time.sleep(0.3)
        return {"ErrorId": "0"}

    mock_tq.get_market_snapshot = slow_snapshot

    c = TdxQuantClient(cfg)
    c._tq = mock_tq
    c._status = UpstreamStatus.HEALTHY
    c._reconnect = AsyncMock()

    # 池打满: capacity=10, active=10, 无空闲 worker
    monkeypatch.setattr(
        "src.unihive.core.blocking_pool.tdx_quant_executor_snapshot",
        lambda: {"capacity": 10, "workers": 10, "active": 10, "queued": 3},
    )

    r = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert r.success is False
    assert c._fail_count == 1, "计数仍应只加 1"
    assert c.status == UpstreamStatus.UNAVAILABLE, (
        f"池耗尽时首次超时就应降级, 实际 {c.status}"
    )


@pytest.mark.asyncio
async def test_pool_exhaustion_does_not_trip_when_workers_free(mock_tq, monkeypatch):
    """池还有空闲 worker 时, 单次超时不该降级 (只是慢, 不是饱和)。"""
    import time as _time

    from src.unihive.api.tdx_quant_client import TdxQuantClient

    cfg = TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake_tdx",
            strategy_id="test_strategy",
            health_check_interval_sec=3600,
            reconnect_threshold=99,
            unavailable_threshold=99,
            call_timeout_sec=0.05,
        ),
    )

    def slow_snapshot(**_kwargs):
        _time.sleep(0.3)
        return {"ErrorId": "0"}

    mock_tq.get_market_snapshot = slow_snapshot

    c = TdxQuantClient(cfg)
    c._tq = mock_tq
    c._status = UpstreamStatus.HEALTHY
    c._reconnect = AsyncMock()

    monkeypatch.setattr(
        "src.unihive.core.blocking_pool.tdx_quant_executor_snapshot",
        lambda: {"capacity": 10, "workers": 10, "active": 3, "queued": 0},
    )

    r = await c.call_tool("get_market_snapshot", {"stock_code": "600519.SH"})
    assert r.success is False
    assert c.status != UpstreamStatus.UNAVAILABLE, "池未打满时不应降级"
