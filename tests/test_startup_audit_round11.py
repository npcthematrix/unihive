"""Round-11 启动审视 (2026-09-19)。

- M1: wheel 必须带 seed 模板 YAML (upstreams + tools_*), 但绝不能把含真实
  session_secret / 口令哈希的 config.yaml 打进 wheel; proxy_settings.yaml 也
  不打 (缺失即默认 disabled, 不能给 installed 用户意外启用代理)。
- L2: load_all_tools 不再有锚定 CWD 的第三候选 (``Path("config")/...``),
  防止从非 repo CWD 运行时误拾外来 tools_*.yaml。
- L4: CLI ``config lint/seed-db/upgrade-db`` 的相对 --config 与网关一样
  锚定项目根, 从任意 CWD 跑默认值都指向同一套出厂模板。
- M2: 上游 start() 被 30s wait_for 取消后, 网关后台 late-start 自愈任务
  会驱动 client (mootdx2 / tdx_quant) 完成初始化并启动各自的健康循环;
  disable/stop 时该任务可被取消, 不对已停 client 复活。
- L1: mootdx2 stop() 的 Quotes 摘除+关闭在 _quotes_lock 内完成, 超时后
  仍在途的 factory 线程晚赋值时关闭动作排队等同一把锁, 不泄漏 TDX 连接。
- L3: main() 对已知启动失败 (OSError / sqlite3.Error / RuntimeError)
  给干净错误行 + exit 1, 不再裸 traceback。
- L5: 删除生产从不调度的网关级 _health_check_loop / _probe_upstreams_once
  死代码 (全仓仅测试 create_task) 及只服务它们的 health_check_timeout 参数。
"""
import asyncio
import inspect
import threading
import time
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

from src.unihive.api.mootdx2_client import MooTDX2Client  # noqa: E402
from src.unihive.api.tdx_quant_client import TdxQuantClient  # noqa: E402
from src.unihive.api.upstream_client import UpstreamStatus  # noqa: E402
from src.unihive.api.mootdx2_client import MooTDX2Config  # noqa: E402
from src.unihive.core.tool_loader import load_all_tools  # noqa: E402


# ---------------------------------------------------------------------------
# M1: wheel 打包内容
# ---------------------------------------------------------------------------

_SEED_TEMPLATE_FILES = (
    "config/upstreams.yaml",
    "config/tools_tdx_quant.yaml",
    "config/tools_mootdx2.yaml",
    "config/tools_omni.yaml",
    "config/tools_thsdk.yaml",
    "config/tools_ths_local.yaml",
)
# config.yaml (真实 session_secret + PBKDF2 口令哈希, git 跟踪但无 ${VAR}
# 占位) 与 proxy_settings.yaml (fail-soft 默认 disabled) 都不在 wheel 内。
_NEVER_PACKAGED_FILES = (
    "config/config.yaml",
    "config/proxy_settings.yaml",
)


def test_m1_wheel_includes_seed_templates_but_not_credentials():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for rel in _SEED_TEMPLATE_FILES:
        mapping = f'"{rel}" = "{rel}"'
        assert mapping in pyproject, (
            f"installed 布局空库首启需从 wheel 内 {rel} seed, 必须 force-include"
        )
    for rel in _NEVER_PACKAGED_FILES:
        assert f'"{rel}"' not in pyproject, (
            f"{rel} 含本机凭据/本机设置, 不得打进 wheel 分发"
        )


# ---------------------------------------------------------------------------
# L2: tool_loader 去掉 CWD 相对兜底
# ---------------------------------------------------------------------------


def test_l2_load_all_tools_ignores_foreign_cwd_config(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    cwd_dir = tmp_path / "some_cwd"
    (cfg_dir).mkdir()
    (cwd_dir / "config").mkdir(parents=True)

    # config_path 同目录的兄弟文件必须加载 (候选 1)
    (cfg_dir / "tools_tdx_quant.yaml").write_text(
        "tools:\n"
        "  - name: gen_sibling\n"
        "    routing: gen_sibling\n"
        "    upstream_tool_mapping: {tdx_quant: gen_sibling}\n",
        encoding="utf-8",
    )
    # CWD/config 下的外来文件必须被忽略 (旧第三候选)
    (cwd_dir / "config" / "tools_tdx_quant.yaml").write_text(
        "tools:\n"
        "  - name: gen_foreign_cwd\n"
        "    routing: gen_foreign_cwd\n"
        "    upstream_tool_mapping: {tdx_quant: gen_foreign_cwd}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(cwd_dir)

    specs = load_all_tools(cfg_dir / "upstreams.yaml", {"tools": []})
    names = {s["name"] for s in specs}
    assert "gen_sibling" in names
    assert "gen_foreign_cwd" not in names


def test_l2_tool_loader_has_no_cwd_relative_candidate():
    import src.unihive.core.tool_loader as tool_loader

    src = inspect.getsource(tool_loader)
    # call-form 断言: 解释性注释里会出现 Path("config") 字样, 禁的是实际调用
    assert 'Path("config") /' not in src and "Path('config') /" not in src


# ---------------------------------------------------------------------------
# L4: CLI --config 锚定项目根
# ---------------------------------------------------------------------------


def test_l4_cli_lint_default_path_anchored_off_cwd(tmp_path, monkeypatch):
    from src.unihive.cli import _cmd_config_lint

    monkeypatch.chdir(tmp_path)  # 从任意 CWD, 无 config/ 子目录
    args = Namespace(
        config="config/upstreams.yaml",
        strict_env=False,
        warnings_as_errors=False,
    )
    rc = _cmd_config_lint(args)
    assert rc == 0, "默认相对路径应锚定项目根找到出厂模板 (lint 无 ERROR)"


def test_l4_cli_source_uses_resolve_project_path():
    import src.unihive.cli as cli

    src = inspect.getsource(cli)
    assert "resolve_project_path" in src


# ---------------------------------------------------------------------------
# M2: 网关 late-start 自愈
# ---------------------------------------------------------------------------

def _new_minimal_server(timeout: float = 0.05):
    from src.unihive.gateway_server import GatewayServer

    server = GatewayServer.__new__(GatewayServer)
    server.upstreams = {}
    server._upstream_start_timeout = timeout
    server._late_start_tasks = {}
    return server


class _SlowStartClient:
    """模拟 start() 长时间不返回 (被 gateway 30s wait_for 超时取消) 的 client。"""

    name = "mootdx2_x"

    def __init__(self):
        self.is_available = False
        self.stopped = False
        self.recovered = False

    async def start(self):
        await asyncio.sleep(100)

    async def start_after_timeout(self):
        self.recovered = True
        self.is_available = True

    async def stop(self):
        self.stopped = True


class _PlainClient:
    """无 late-recovery 能力的 client (fuyao/omni/stdio 等)。"""

    name = "fuyao_x"
    is_available = False

    async def start(self):
        await asyncio.sleep(100)

    async def stop(self):
        pass


async def test_m2_timeout_registers_and_late_start_recovers():
    server = _new_minimal_server()
    client = _SlowStartClient()
    connected = await server._start_upstream_client(
        "mootdx2_x", {"type": "mootdx2"}, client
    )
    assert connected is False
    assert server.upstreams["mootdx2_x"] is client
    # late-start 后台任务真正跑起来
    for _ in range(10):
        await asyncio.sleep(0)
    assert client.recovered is True
    assert client.is_available is True
    assert server._late_start_tasks.get("mootdx2_x") is None  # 完成后自清


async def test_m2_no_late_task_for_client_without_recovery_hook():
    server = _new_minimal_server()
    client = _PlainClient()
    await server._start_upstream_client("fuyao_x", {"type": "http"}, client)
    assert server._late_start_tasks == {}


async def test_m2_detach_cancels_late_start_and_stops_client():
    server = _new_minimal_server()
    client = _SlowStartClient()

    class _NeverFinish:
        async def start_after_timeout(self_inner):
            await asyncio.sleep(100)

    # 挂一个永不完成的 late 任务 (模拟在途阻塞初始化)
    client.start_after_timeout = _NeverFinish().start_after_timeout
    await server._start_upstream_client(
        "mootdx2_x", {"type": "mootdx2"}, client
    )
    task = server._late_start_tasks.get("mootdx2_x")
    assert task is not None and not task.done()

    await server._detach_upstream_client_locked("mootdx2_x")

    assert "mootdx2_x" not in server.upstreams
    assert client.stopped is True
    assert task.done()


async def test_m2_cancel_all_late_starts_for_shutdown():
    server = _new_minimal_server()
    client = _SlowStartClient()

    class _NeverFinish:
        async def start_after_timeout(self_inner):
            await asyncio.sleep(100)

    client.start_after_timeout = _NeverFinish().start_after_timeout
    await server._start_upstream_client(
        "mootdx2_x", {"type": "mootdx2"}, client
    )
    pending = server._late_start_tasks.get("mootdx2_x")
    assert pending is not None
    await server._cancel_late_starts()
    assert pending.done()


# ---------------------------------------------------------------------------
# M2/L1: mootdx2 client 守卫
# ---------------------------------------------------------------------------

def _mootdx2_client() -> MooTDX2Client:
    return MooTDX2Client(MooTDX2Config(name="mootdx2"))


# 注：原 test_m2_mootdx2_health_loop_not_started_when_stopped 已随
# Round-11 L5 删除后台 _health_loop / _start_health_loop 一并移除
# (commit d385d4b)。


async def test_m2_mootdx2_start_after_timeout_skips_when_stopped():
    client = _mootdx2_client()
    client._stopped = True

    async def _boom():
        raise AssertionError("start() must not run when stopped")

    client.start = _boom
    await client.start_after_timeout()  # 不抛即正确


async def test_l1_stop_closes_quotes_assigned_while_factory_holds_lock():
    """僵尸 factory 线程持锁慢赋值; stop() 必须等锁并最终关掉晚到的 Quotes。"""
    client = _mootdx2_client()

    class _FakeQuotes:
        def __init__(self):
            self.closed = False
            self.close_lock = threading.Lock()

        def close(self):
            with self.close_lock:
                self.closed = True

    fake_quotes = _FakeQuotes()

    def _zombie_factory():
        # 模拟 factory 在 _quotes_lock 内阻塞 0.3s 后才赋值 (stop 先读到 None)
        with client._quotes_lock:
            time.sleep(0.3)
            client._quotes = fake_quotes

    t = threading.Thread(target=_zombie_factory, daemon=True)
    t.start()
    # 让 zombie 先进锁
    time.sleep(0.05)
    await client.stop()
    t.join(timeout=2)
    assert not t.is_alive()
    assert fake_quotes.closed is True
    assert client._quotes is None


def test_l1_mootdx2_stop_takes_quotes_lock():
    src = inspect.getsource(MooTDX2Client.stop)
    assert "_quotes_lock" in src
    start_src = inspect.getsource(MooTDX2Client.start)
    assert "_stopped = False" in start_src


# ---------------------------------------------------------------------------
# M2: tdx_quant client 守卫
# ---------------------------------------------------------------------------

def _tdx_quant_client():
    client = TdxQuantClient.__new__(TdxQuantClient)
    client.name = "tdx_quant"
    client._status = UpstreamStatus.UNKNOWN
    client._tq = None
    client._health_task = None
    client._stopped = False
    client._dll_lock = threading.Lock()
    return client


def test_m2_tdx_dll_lock_serializes_initialize():
    """并发两个 initialize 打进 DLL 时, threading 锁保证同一时刻只有一个。"""
    client = _tdx_quant_client()

    class _FakeTq:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self._lock = threading.Lock()

        def initialize(self, path):
            with self._lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.2)
            with self._lock:
                self.active -= 1

        def close(self):
            pass

    fake_tq = _FakeTq()
    client._tq = fake_tq

    t1 = threading.Thread(target=client._dll_initialize, args=("C:/tdx",))
    t2 = threading.Thread(target=client._dll_initialize, args=("C:/tdx",))
    t1.start()
    time.sleep(0.05)
    t2.start()
    t1.join(timeout=2)
    t2.join(timeout=2)
    assert fake_tq.max_active == 1, "DLL initialize 必须被 _dll_lock 串行化"


async def test_m2_tdx_health_loop_dedup_and_stopped_guard():
    client = _tdx_quant_client()

    class _Settings:
        health_check_interval_sec = 60

    class _Config:
        settings = _Settings()

    client.config = _Config()

    client._stopped = True
    assert client._start_health_loop() is None
    assert client._health_task is None

    client._stopped = False
    task = client._start_health_loop()
    assert task is not None
    assert client._start_health_loop() is task  # dedup
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_m2_tdx_start_resets_stopped_and_sets_on_stop():
    src_start = inspect.getsource(TdxQuantClient.start)
    src_stop = inspect.getsource(TdxQuantClient.stop)
    assert "_stopped = False" in src_start
    assert "_stopped = True" in src_stop
    assert "start_after_timeout" in dir(TdxQuantClient)
    assert "_dll_initialize" in dir(TdxQuantClient)


# ---------------------------------------------------------------------------
# L3: main() 启动失败退出码
# ---------------------------------------------------------------------------

def test_l3_main_known_startup_failure_clean_exit_1(monkeypatch, capsys):
    import sys
    import sqlite3

    import src.unihive.gateway_server as gs

    monkeypatch.setattr(sys, "argv", ["unihive"])
    cases = (
        OSError("address already in use-ish disk error"),
        sqlite3.OperationalError("database is locked"),
        RuntimeError("config store boom"),
    )
    for exc in cases:
        async def _fake_main(**_kwargs):
            raise exc

        monkeypatch.setattr(gs, "async_main", _fake_main)
        with pytest.raises(SystemExit) as ei:
            gs.main()
        assert ei.value.code == 1
        err = capsys.readouterr().err
        assert "error:" in err and str(exc) in err


def test_l3_main_value_error_still_exit_2(monkeypatch, capsys):
    import sys

    import src.unihive.gateway_server as gs

    monkeypatch.setattr(sys, "argv", ["unihive"])

    async def _fake_main(**_kwargs):
        raise ValueError("bad config")

    monkeypatch.setattr(gs, "async_main", _fake_main)
    with pytest.raises(SystemExit) as ei:
        gs.main()
    assert ei.value.code == 2


# ---------------------------------------------------------------------------
# L5: 死代码已删
# ---------------------------------------------------------------------------

def test_l5_gateway_level_dead_health_loop_removed():
    import src.unihive.gateway_server as gs

    src = inspect.getsource(gs)
    assert "_health_check_loop" not in src
    assert "_probe_upstreams_once" not in src
    assert "health_check_timeout" not in src
