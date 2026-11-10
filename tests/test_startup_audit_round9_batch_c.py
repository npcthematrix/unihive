"""第 9 轮启动逻辑审视 — Batch C: 阻塞 / 资源 / 打包姿态。

覆盖:
  H1 (mootdx2 + tdx_quant 同步阻塞包 to_thread) /
  H3 (OmniClient 空 DB 不再假 HEALTHY) /
  M8 (fuyao start 做可达性探测, 死网关 → UNAVAILABLE) /
  L10 (omni worker-thread SQLite 连接泄漏) /
  L12 (uvicorn timeout_graceful_shutdown).
"""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading
import time
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.unihive.api.fuyao_client import FuyaoClient, FuyaoConfig
from src.unihive.api.mootdx2_client import MooTDX2Client
from src.unihive.api.omni_client import OmniClient, OmniConfig
from src.unihive.api.tdx_quant_client import TdxQuantClient
from src.unihive.api.upstream_client import UpstreamStatus


# ---------------------------------------------------------------------------
# H1-a: mootdx2 start() 的 _ensure_quotes_initialized 必须跑在线程里
# ---------------------------------------------------------------------------


async def test_h1a_mootdx2_ensure_quotes_runs_off_event_loop():
    """start() 直接同步调 _ensure_quotes_initialized → 其内部 _get_quotes()
    触发 Quotes.factory() 对所有 TDX 服务器测速 (阻塞 IO) 跑在事件循环
    线程上, 冻结其它 asyncio task, 且 gateway 的 wait_for 无法打断它
    (无 await 点)。修复后 start() 用 asyncio.to_thread 包装, factory
    在 worker thread 跑, 事件循环不被冻结。"""

    client = MooTDX2Client.__new__(MooTDX2Client)
    client.name = "mootdx2"
    client.config = SimpleNamespace(settings=SimpleNamespace(tdxdir="C:/tdx"))
    client._status = UpstreamStatus.UNKNOWN
    client._quotes = None
    client._health_task = None

    recorded: list[int] = []

    def fake_ensure() -> None:
        recorded.append(threading.get_ident())

    client._ensure_quotes_initialized = fake_ensure  # type: ignore[assignment]
    client._probe_tdxdir = lambda: None  # type: ignore[assignment]
    client._probe_with_existing_quotes = AsyncMock(return_value=True)  # type: ignore[assignment]
    client._start_health_loop = lambda: None  # type: ignore[assignment]

    loop_thread = threading.get_ident()
    await client.start()

    assert recorded, "_ensure_quotes_initialized must be called during start()"
    assert recorded[0] != loop_thread, (
        "Quotes.factory() speed-test must run in a worker thread so the event "
        "loop is not frozen and gateway wait_for can interrupt start()"
    )


# ---------------------------------------------------------------------------
# H1-b: tdx_quant _try_initialize 的 tq.initialize 必须跑在线程里
# ---------------------------------------------------------------------------


async def test_h1b_tdx_quant_initialize_runs_off_event_loop(monkeypatch):
    """_try_initialize 直接 `self._tq.initialize(tdx_root)` 是同步 DLL 调用
    (加载 TdxW 插件 + 登录), 跑在事件循环上会冻结 boot。stop() 的
    tq.close() 已包 run_in_executor (HIGH2), initialize 对称地也必须包。
    """

    recorded: list[int] = []

    class _FakeTq:
        def initialize(self, path):  # noqa: D401, ANN001
            recorded.append(threading.get_ident())

    fake_mod = types.ModuleType("tqcenter")
    fake_mod.tq = _FakeTq()
    monkeypatch.setitem(sys.modules, "tqcenter", fake_mod)

    client = TdxQuantClient.__new__(TdxQuantClient)
    client.config = SimpleNamespace(
        tqcenter_dir="", tdx_root_path="C:/tdx", settings=SimpleNamespace(strategy_id="sid")
    )
    client.name = "tdq"
    client._tq = None
    client._strategy_id = "sid"
    # M2 (round 11): initialize 经 _dll_initialize 持 _dll_lock 串行化
    client._dll_lock = threading.Lock()
    # Round-12 簇H: import+initialize 一体走 _dll_import_and_initialize,
    # 锁内先检 _stopped; __new__ 测试实例需手工补该字段。
    client._stopped = False

    loop_thread = threading.get_ident()
    ok, err = await client._try_initialize()

    assert ok is True, f"init should succeed with fake tqcenter; err={err}"
    assert recorded, "tq.initialize must be called"
    assert recorded[0] != loop_thread, (
        "tq.initialize (sync DLL load + login) must run in executor thread, "
        "not on the event loop"
    )


# ---------------------------------------------------------------------------
# H3: OmniClient 空 DB 不再假 HEALTHY
# ---------------------------------------------------------------------------


async def test_h3_omni_empty_db_is_degraded_not_healthy(tmp_path):
    """OmniClient.__init__ 的 _ensure_db() 会建库建表 → db_path.exists()
    恒为真 → start() 无条件 HEALTHY, 即便 sectors 表 0 行 (同步未跑)。
    修复后探测 COUNT(*) FROM sectors: 0 行 → DEGRADED, >0 → HEALTHY。"""

    # 空 DB: 文件存在 (同步建过表) 但 sectors 0 行。M2 (round 10) 后 __init__
    # 不再物化缺库, 先建空文件模拟真实"未灌数据"场景。
    empty_path = tmp_path / "empty.db"
    sqlite3.connect(str(empty_path)).close()
    empty = OmniClient(OmniConfig(db_path=str(empty_path)))
    await empty.start()
    assert empty._status == UpstreamStatus.DEGRADED, (
        "empty sectors table (sync not run) must be DEGRADED, not fake HEALTHY"
    )
    await empty.stop()

    # 有数据: 插一行后再 start → HEALTHY (M2 后先物化空文件, __init__ 仅补表)
    pop_path = tmp_path / "pop.db"
    sqlite3.connect(str(pop_path)).close()
    pop = OmniClient(OmniConfig(db_path=str(pop_path)))  # __init__ 对已存在库补表
    conn = sqlite3.connect(str(pop_path))
    conn.execute(
        "INSERT INTO sectors (source, board_type, code, name) "
        "VALUES ('THS', 'industry', '001', 'test')"
    )
    conn.commit()
    conn.close()
    await pop.start()
    assert pop._status == UpstreamStatus.HEALTHY, (
        "populated sectors table must be HEALTHY"
    )
    await pop.stop()


async def test_h3_omni_missing_table_is_unavailable(tmp_path):
    """DB 文件存在但 sectors 表缺失 (损坏 / 异构) → 不应抛, 标 UNAVAILABLE。"""

    bad_path = tmp_path / "bad.db"
    conn = sqlite3.connect(str(bad_path))
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()

    client = OmniClient(OmniConfig(db_path=str(bad_path)))
    # _ensure_db 会 CREATE TABLE IF NOT EXISTS sectors → 表会被补建 → 0 行。
    # 要测"表缺失"路径, 先 drop 掉 _ensure_db 建的表。
    conn = sqlite3.connect(str(bad_path))
    conn.execute("DROP TABLE sectors")
    conn.execute("DROP TABLE sector_stocks")
    conn.execute("DROP TABLE sector_sync_log")
    conn.commit()
    conn.close()
    # 不再触发 _ensure_db (已 construct 过); 直接 start
    await client.start()
    assert client._status == UpstreamStatus.UNAVAILABLE, (
        "DB present but sectors table missing → UNAVAILABLE, not HEALTHY/DEGRADED"
    )
    await client.stop()


# ---------------------------------------------------------------------------
# M8: fuyao start() 做可达性探测, 死网关 → UNAVAILABLE
# ---------------------------------------------------------------------------


async def test_m8_fuyao_start_probes_dead_gateway():
    """start() 仅构造 httpx client 即标 HEALTHY → 上游死着 boot 也报可用,
    首次 call_tool 才暴露。修复后 start() GET 一次 base_url (不发 JSON-RPC,
    不耗配额), 连接拒绝/超时 → UNAVAILABLE + 关 client。"""

    # 127.0.0.1:1 几乎必然连接拒绝 (Windows WSAECONNREFUSED, 立即)
    cfg = FuyaoConfig(
        name="fuyao",
        base_url="http://127.0.0.1:1/mcp",
        api_key="k",
        connect_timeout_seconds=0.5,
        timeout_seconds=1.0,
    )
    client = FuyaoClient(cfg)
    t0 = time.time()
    ok = await client.start()
    elapsed = time.time() - t0
    assert ok is False, "dead gateway must not report start success"
    assert client._status == UpstreamStatus.UNAVAILABLE
    assert client._client is None, "httpx client must be closed on probe failure"
    assert elapsed < 5, "probe must be bounded by a short boot-probe timeout, not 30s"


# ---------------------------------------------------------------------------
# L10: omni worker-thread SQLite 连接泄漏
# ---------------------------------------------------------------------------


async def test_l10_omni_stop_closes_worker_thread_conns(tmp_path):
    """_query_sectors_sync 经 asyncio.to_thread 跑在 worker thread, 每线程
    在 threading.local 缓存一个长连接。stop() 只 _close_thread_conn()
    关闭当前 (事件循环) 线程的连接, worker thread 的连接留在 tls 永不关
    (句柄泄漏)。修复后 stop() 关闭所有已登记连接 + 重置 tls。"""

    pop_path = tmp_path / "leak.db"
    sqlite3.connect(str(pop_path)).close()  # M2 (round 10): 先物化空文件
    client = OmniClient(OmniConfig(db_path=str(pop_path)))  # __init__ 对已存在库补表
    conn = sqlite3.connect(str(pop_path))
    conn.execute(
        "INSERT INTO sectors (source, board_type, code, name) "
        "VALUES ('THS', 'industry', '001', 't')"
    )
    conn.commit()
    conn.close()
    await client.start()
    assert client._status == UpstreamStatus.HEALTHY

    # 在 worker thread 上跑一次查询 → 该线程 tls 缓存一条长连接并登记
    await asyncio.to_thread(client._query_sectors_sync, None, "all", "")

    registered = list(getattr(client, "_all_conns", []))
    assert registered, (
        "worker-thread connection must be registered for cleanup tracking"
    )
    # sqlite3.Connection 无 .closed 属性; 用 execute 是否抛 ProgrammingError
    # 判定存活 (open conn 能执行 SELECT 1)。
    for c in registered:
        c.execute("SELECT 1").fetchone()  # open conn: 不抛

    await client.stop()

    assert len(client._all_conns) == 0, "all registered conns must be cleared on stop"
    for c in registered:
        with pytest.raises(sqlite3.ProgrammingError):
            c.execute("SELECT 1")  # closed conn: ProgrammingError


# ---------------------------------------------------------------------------
# L12: uvicorn timeout_graceful_shutdown
# ---------------------------------------------------------------------------


def test_l12_uvicorn_config_sets_graceful_shutdown_timeout():
    """serve_http 构造 uvicorn.Config 时不传 timeout_graceful_shutdown →
    默认 None (无硬上限), 长闲连接拖住 SIGTERM 远超 stop() 的 max_wait=10s。
    修复后显式设 timeout_graceful_shutdown 与 stop() 的 max_wait 对齐。"""

    # 2026-09-19 god-module 拆包后 serve_http 位于 http.py; inspect 会自动
    # 跟到定义子模块, 不再按文件路径读 gateway_server.py。
    import inspect

    from src.unihive.gateway_server import GatewayServer

    text = inspect.getsource(GatewayServer.serve_http)
    assert "timeout_graceful_shutdown" in text, (
        "uvicorn.Config must set timeout_graceful_shutdown so lingering "
        "connections don't outlive stop()'s max_wait budget"
    )
