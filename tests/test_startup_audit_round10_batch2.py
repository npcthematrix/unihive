"""Round-10 启动审视 · 批 2 (L1 + L2 + L3 + L4), LOW 收尾。

- L1: mootdx2 启动 factory / probe / close 的同步阻塞调用统一走专用阻塞池
  (_run_blocking → get_mootdx2_executor), 不落 asyncio.to_thread 默认池。
- L2: OmniClient stop() 后 _get_conn/health_check 不重开泄漏连接 (stopped 闸)。
- L3: GatewayServer 相对 config_path 锚定项目根, 与 CWD 无关 (空库首启不再 seed 空模板)。
- L4: 阻塞池 worker 为 daemon 且不注册进 concurrent.futures atexit join 表,
  DLL 永久挂死时进程退出不被无超时 join 拖住。
"""
import asyncio
import inspect
import os
import sqlite3
import threading
from concurrent.futures import thread as _cf_thread
from pathlib import Path

import pytest

from src.unihive.api.omni_client import OmniClient, OmniConfig
from src.unihive.api.upstream_client import UpstreamStatus
from src.unihive.core.config_db import project_root


# ---------------------------------------------------------------------------
# L1: mootdx2 启动阻塞调用走专用池
# ---------------------------------------------------------------------------


def test_l1_mootdx2_start_uses_dedicated_blocking_pool():
    from src.unihive.api.mootdx2_client import MooTDX2Client

    start_src = inspect.getsource(MooTDX2Client.start)
    assert "_run_blocking(self._ensure_quotes_initialized)" in start_src, (
        "Quotes.factory() is a heavy blocking call; it must run on the dedicated "
        "unihive-mootdx2 pool, not asyncio.to_thread's default executor"
    )
    # 禁的是实际调用, 不是解释性注释里的名字 (assert call-form, not bare name)
    assert "asyncio.to_thread(" not in start_src


def test_l1_mootdx2_probe_and_close_use_dedicated_blocking_pool():
    from src.unihive.api.mootdx2_client import MooTDX2Client

    probe_src = inspect.getsource(MooTDX2Client._probe_with_existing_quotes)
    assert "_run_blocking" in probe_src
    assert "asyncio.to_thread(" not in probe_src

    stop_src = inspect.getsource(MooTDX2Client.stop)
    assert "_run_blocking" in stop_src
    assert "asyncio.to_thread(" not in stop_src


# ---------------------------------------------------------------------------
# L2: omni stop 后不重开连接
# ---------------------------------------------------------------------------


async def test_l2_get_conn_after_stop_raises_and_no_leak(tmp_path):
    db = tmp_path / "board.db"
    sqlite3.connect(str(db)).close()
    client = OmniClient(OmniConfig(db_path=str(db)))
    await client.start()

    # 先用一次, 产生并登记一条连接
    await asyncio.to_thread(client._query_sectors_sync, None, "all", "")
    assert client._all_conns, "precondition: a connection was registered"

    await client.stop()
    assert client._all_conns == []

    # stop 后 health_check 不得经 _get_conn 重开连接 (旧实现会重开并重新登记,
    # 再无 stop() 关闭 → 句柄泄漏)。
    healthy = await client.health_check()
    assert healthy is False
    assert client._all_conns == [], "post-stop health_check must not reopen/register a connection"

    with pytest.raises(RuntimeError):
        client._get_conn()


async def test_l2_restart_reopens_after_stop(tmp_path):
    """stopped 闸必须在 start() 复位, 支持同实例 stop/start 循环。"""
    db = tmp_path / "board.db"
    sqlite3.connect(str(db)).close()  # 先物化空文件
    client = OmniClient(OmniConfig(db_path=str(db)))  # __init__ 对已存在库补表
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO sectors (source, board_type, code, name) "
        "VALUES ('THS', 'industry', '001', 'test')"
    )
    conn.commit()
    conn.close()

    await client.start()
    assert client._status == UpstreamStatus.HEALTHY
    await client.stop()
    with pytest.raises(RuntimeError):
        client._get_conn()

    await client.start()
    try:
        c = client._get_conn()  # 不再抛, 连接可重开
        c.execute("SELECT 1").fetchone()
    finally:
        await client.stop()


# ---------------------------------------------------------------------------
# L3: config_path 锚定项目根
# ---------------------------------------------------------------------------


def test_l3_relative_config_path_anchored_to_project_root(tmp_path, monkeypatch):
    from src.unihive.gateway_server import GatewayServer

    monkeypatch.chdir(tmp_path)  # 从任意 CWD 启动
    # strict 关掉: chdir 后 .env 解析/校验可能失败, 本断言只看路径锚定
    server = GatewayServer(
        config_path="config/upstreams.yaml",
        strict_env=False,
        strict_validation=False,
    )
    assert server.config_path == project_root() / "config" / "upstreams.yaml"


def test_l3_absolute_config_path_passthrough(tmp_path, monkeypatch):
    from src.unihive.gateway_server import GatewayServer

    monkeypatch.chdir(tmp_path)
    abs_cfg = tmp_path / "custom" / "upstreams.yaml"
    abs_cfg.parent.mkdir(parents=True, exist_ok=True)
    # conftest 强制 UNIHIVE_CONFIG_SOURCE=yaml: 缺文件会直接 FileNotFoundError,
    # 写最小可解析配置 (validation 错误在 strict_validation=False 下仅告警)。
    abs_cfg.write_text("upstreams: {}\nrouting: {}\n", encoding="utf-8")
    server = GatewayServer(
        config_path=str(abs_cfg),
        strict_env=False,
        strict_validation=False,
    )
    assert Path(server.config_path) == abs_cfg


def test_l3_gateway_uses_resolve_project_path_for_config():
    """静态断言: __init__ 经 resolve_project_path 锚定 config_path。"""
    from src.unihive import gateway_server

    src = inspect.getsource(gateway_server.GatewayServer.__init__)
    assert "resolve_project_path" in src


# ---------------------------------------------------------------------------
# L4: 阻塞池 worker daemon + 不注册 atexit join
# ---------------------------------------------------------------------------


def test_l4_blocking_pool_workers_are_daemon_and_unregistered():
    from src.unihive.core.blocking_pool import get_mootdx2_executor

    pool = get_mootdx2_executor()
    pool.submit(lambda: None).result(timeout=5)  # 触发 worker 创建

    workers = [
        t for t in threading.enumerate()
        if t.name.startswith("unihive-mootdx2")
    ]
    assert workers, "dedicated pool must have spawned at least one worker"
    assert all(t.daemon for t in workers), (
        "worker must be daemon so a stuck DLL call cannot block interpreter shutdown"
    )
    assert all(t not in _cf_thread._threads_queues for t in workers), (
        "worker must not be registered in concurrent.futures atexit join table "
        "(_python_exit joins with no timeout → stuck thread hangs process exit)"
    )
