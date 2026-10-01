"""Round-12 簇H: 生命周期/并发收口。

1. mootdx2 懒工厂 (_get_quotes/_get_reader) 加 _stopped 闸 —— stop() 之后
   的晚到调用以前会重新 factory 出一个无人关闭的 TDX 连接/Reader; 锁内
   二次复检挡住 "过闸后 stop 抢先摘除" 的竞态; probe 在 stop 后不碰 quotes。
2. tdx_quant: sys.path 注入 + import tqcenter 以前跑在事件循环线程上
   (import 触发 DLL 加载, 可能冻结 boot), 整体移入阻塞池并持 _dll_lock;
   _dll_initialize 在 stop 后拒绝再打 DLL。
3. shutdown_mootdx2_executor 不取消排队任务 —— stop() 排在僵尸 factory
   后面的 close job (mootdx2 注释承诺 "最终必执行") 以前被 cancel_futures
   取消, 晚赋值的活 TDX 连接无人关。daemon 池排空排队任务不拖退出。
4. lifecycle.stop(): config_store.close() 异常不截断后续复位;
   _in_flight_requests 清空 (旧实现 stop 后残留 future 引用);
   initialize 失败清理对 CancelledError 也生效 (旧 except Exception 直接
   放过 → 已启动 client / cache / cleanup task 全泄漏)。
5. yaml 逃生模式 _load_db_config 也重放 _apply_gateway_tuning (旧实现只在
   db 分支重放, yaml 模式控制台改调参后重启不生效——该分支本应保持与
   __init__ 基线一致的重放契约)。
6. omni run_sync 的同步 _ensure_db (mkdir+DDL) 走 to_thread, 不冻结 loop。
"""
from __future__ import annotations

import asyncio
import sys
import threading
import types

import pytest

from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config
from src.unihive.api.omni_client import OmniClient, OmniConfig
from src.unihive.api.tdx_quant_client import TdxQuantClient
from src.unihive.api.upstream_client import UpstreamStatus
from src.unihive.core.blocking_pool import (
    get_mootdx2_executor,
    get_tdx_quant_executor,
    shutdown_mootdx2_executor,
)
from src.unihive.core.rate_limiter import reset_default_limiter
from src.unihive.gateway_server import GatewayServer
from src.unihive.models.tdx_quant_config import TdxQuantConfig, TdxQuantSettings


# ---------------------------------------------------------------------------
# 1. mootdx2 _stopped 守卫
# ---------------------------------------------------------------------------


class _RacingLock:
    """模拟 "过外层闸后、拿锁前 stop() 抢先置位" 的竞态: 进锁即置 stopped。"""

    def __init__(self, client):
        self._client = client

    def __enter__(self):
        self._client._stopped = True
        return self

    def __exit__(self, *exc):
        return False


class TestMootdx2StoppedGuards:
    def test_get_quotes_after_stop_raises(self):
        client = MooTDX2Client(MooTDX2Config(name="t", market="std"))
        client._stopped = True
        with pytest.raises(RuntimeError, match="stop"):
            client._get_quotes()
        assert client._quotes is None

    def test_get_quotes_racing_stop_under_lock_does_not_refactory(
        self, monkeypatch
    ):
        import mootdx2.quotes as quotes_mod

        def _boom(**kwargs):
            raise AssertionError("stop 赢了竞态后不得重新 factory Quotes")

        monkeypatch.setattr(quotes_mod.Quotes, "factory", _boom)
        client = MooTDX2Client(MooTDX2Config(name="t", market="std"))
        client._stopped = False
        client._quotes = None
        client._quotes_lock = _RacingLock(client)
        with pytest.raises(RuntimeError):
            client._get_quotes()
        assert client._quotes is None

    def test_get_quotes_live_factories_once(self, monkeypatch):
        import mootdx2.quotes as quotes_mod

        sentinel = object()
        calls = {"n": 0}

        def _factory(**kwargs):
            calls["n"] += 1
            return sentinel

        monkeypatch.setattr(quotes_mod.Quotes, "factory", _factory)
        client = MooTDX2Client(MooTDX2Config(name="t", market="std"))
        assert client._get_quotes() is sentinel
        assert client._get_quotes() is sentinel
        assert calls["n"] == 1

    def test_get_reader_after_stop_raises(self):
        client = MooTDX2Client(MooTDX2Config(name="t", market="std"))
        client._stopped = True
        with pytest.raises(RuntimeError, match="stop"):
            client._get_reader()
        assert client._reader is None

    def test_get_reader_racing_stop_under_lock_does_not_refactory(
        self, monkeypatch
    ):
        import mootdx2.reader as reader_mod

        def _boom(**kwargs):
            raise AssertionError("stop 赢了竞态后不得重新 factory Reader")

        monkeypatch.setattr(reader_mod.Reader, "factory", _boom)
        client = MooTDX2Client(MooTDX2Config(name="t", market="std"))
        client._stopped = False
        client._reader = None
        client._reader_lock = _RacingLock(client)
        with pytest.raises(RuntimeError):
            client._get_reader()
        assert client._reader is None

    def test_get_reader_live_factories_once(self, monkeypatch):
        import mootdx2.reader as reader_mod

        sentinel = object()
        calls = {"n": 0}

        def _factory(**kwargs):
            calls["n"] += 1
            return sentinel

        monkeypatch.setattr(reader_mod.Reader, "factory", _factory)
        client = MooTDX2Client(MooTDX2Config(name="t", market="std"))
        assert client._get_reader() is sentinel
        assert client._get_reader() is sentinel
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_probe_after_stop_does_not_touch_quotes(self):
        client = MooTDX2Client(MooTDX2Config(name="t", market="std"))

        class _BoomQuotes:
            def quotes(self, symbol):
                raise AssertionError("stop 后 probe 不得再打 TDX")

        client._quotes = _BoomQuotes()
        client._stopped = True
        assert await client._probe_with_existing_quotes() is False

    @pytest.mark.asyncio
    async def test_start_resets_stopped_and_quotes_reusable(self):
        client = MooTDX2Client(MooTDX2Config(name="t", market="std"))
        client._stopped = True
        sentinel_q = object()
        client._probe_tdxdir = lambda: None
        client._ensure_quotes_initialized = lambda: setattr(
            client, "_quotes", sentinel_q
        )

        async def _fake_probe():
            assert client._quotes is sentinel_q
            return True

        client._probe_with_existing_quotes = _fake_probe
        client._start_health_loop = lambda: None
        await client.start()
        assert client._stopped is False
        assert client.status == UpstreamStatus.HEALTHY


# ---------------------------------------------------------------------------
# 2. tdx_quant: import 入池 + DLL 停止闸
# ---------------------------------------------------------------------------


def _install_fake_tqcenter(initialize_returns=None, initialize_side=None):
    """在 sys.modules 注入 mock tqcenter 模块, 返回 mock tq 实例。"""
    from unittest.mock import MagicMock

    mod = types.ModuleType("tqcenter")
    tq = MagicMock()
    tq.initialize = MagicMock(
        side_effect=initialize_side,
        return_value=initialize_returns or {"ErrorId": "0"},
    )
    tq.close = MagicMock()
    mod.tq = tq
    sys.modules["tqcenter"] = mod
    return tq


@pytest.fixture
def tq_client_config():
    return TdxQuantConfig(
        name="tdx_quant",
        settings=TdxQuantSettings(
            tdx_root="D:/fake_tdx",
            strategy_id="s1",
            health_check_interval_sec=60,
        ),
    )


class TestTdxQuantDllGuards:
    def test_dll_initialize_after_stop_raises(self, tq_client_config):
        tq = _install_fake_tqcenter()
        try:
            c = TdxQuantClient(tq_client_config)
            c._tq = tq
            c._stopped = True
            with pytest.raises(RuntimeError, match="stop"):
                c._dll_initialize("D:\\fake_tdx")
            tq.initialize.assert_not_called()
        finally:
            sys.modules.pop("tqcenter", None)

    def test_import_and_initialize_off_thread_under_lock(
        self, tq_client_config, tmp_path
    ):
        captured = {}
        tq_dir = tmp_path / "PYPlugins" / "user"
        tq_dir.mkdir(parents=True)

        def _init_side(root):
            captured["off_thread"] = (
                threading.current_thread() is not threading.main_thread()
            )
            captured["lock_held"] = c._dll_lock.locked()
            captured["root"] = root

        tq = _install_fake_tqcenter(initialize_side=_init_side)
        try:
            c = TdxQuantClient(tq_client_config)
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    loop.run_in_executor(
                        get_tdx_quant_executor(),
                        lambda: c._dll_import_and_initialize(
                            str(tmp_path), str(tq_dir)
                        ),
                    )
                )
            finally:
                loop.close()
            assert c._tq is tq
            assert captured["off_thread"] is True
            assert captured["lock_held"] is True
            assert captured["root"] == str(tmp_path)
            assert str(tq_dir) in sys.path
        finally:
            sys.modules.pop("tqcenter", None)
            if str(tq_dir) in sys.path:
                sys.path.remove(str(tq_dir))

    @pytest.mark.asyncio
    async def test_try_initialize_import_runs_off_event_loop(
        self, tq_client_config, tmp_path
    ):
        """端到端: 落一个真 tqcenter.py 到 tdx_root, 记录 initialize 执行线程。

        旧实现 sys.path.insert + import 在事件循环线程 (import 触发 DLL 加载
        会冻结 boot); 新实现 import 与 initialize 同在阻塞池持锁完成。
        """
        tdx_root = tmp_path / "tdx"
        tq_dir = tdx_root / "PYPlugins" / "user"
        tq_dir.mkdir(parents=True)
        (tq_dir / "tqcenter.py").write_text(
            "import threading\n"
            # 模块体在 import 瞬间执行: 记录是否跑在主线程 (event loop 线程)。
            "import_thread_main = threading.current_thread() is threading.main_thread()\n"
            "calls = {'off_thread': None, 'root': None}\n"
            "class _Tq:\n"
            "    def initialize(self, root):\n"
            "        calls['off_thread'] = (\n"
            "            threading.current_thread() is not threading.main_thread())\n"
            "        calls['root'] = root\n"
            "        return {'ErrorId': '0'}\n"
            "    def close(self):\n"
            "        pass\n"
            "tq = _Tq()\n",
            encoding="utf-8",
        )
        cfg = TdxQuantConfig(
            name="tdx_quant",
            settings=TdxQuantSettings(
                tdx_root=str(tdx_root),
                strategy_id="s1",
                health_check_interval_sec=60,
            ),
        )
        c = TdxQuantClient(cfg)
        try:
            ok, err = await c._try_initialize()
            assert ok is True, err
            mod = sys.modules["tqcenter"]
            assert mod.import_thread_main is False
            assert mod.calls["off_thread"] is True
            assert mod.calls["root"] == str(tdx_root)
        finally:
            sys.modules.pop("tqcenter", None)
            if str(tq_dir) in sys.path:
                sys.path.remove(str(tq_dir))

    @pytest.mark.asyncio
    async def test_try_initialize_missing_module_returns_error(
        self, tmp_path
    ):
        cfg = TdxQuantConfig(
            name="tdx_quant",
            settings=TdxQuantSettings(
                tdx_root=str(tmp_path / "no_tdx_here"),
                strategy_id="s1",
                health_check_interval_sec=60,
            ),
        )
        c = TdxQuantClient(cfg)
        ok, err = await c._try_initialize()
        assert ok is False
        assert err

    @pytest.mark.asyncio
    async def test_try_initialize_after_stop_returns_error(self, tq_client_config):
        c = TdxQuantClient(tq_client_config)
        c._stopped = True
        ok, err = await c._try_initialize()
        assert ok is False
        assert "stop" in err.lower()


# ---------------------------------------------------------------------------
# 3. 阻塞池: 排队的 close job 不被取消
# ---------------------------------------------------------------------------


class TestBlockingPoolQueuedJobs:
    def test_shutdown_keeps_queued_close_jobs(self, monkeypatch):
        # Route A 后共享池已拆分: 这里验 mootdx2 专用池 (prefix unihive-mootdx2)。
        # 用单 worker 池复现 "唯一 worker 被僵尸 factory 占住, close job 只能排队"
        # 的场景, 断言 shutdown(cancel_futures=False) 不取消排队的 close。
        monkeypatch.setenv("UNIHIVE_MOOTDX2_WORKER_THREADS", "1")
        shutdown_mootdx2_executor()
        pool = get_mootdx2_executor()
        try:
            started = threading.Event()
            release = threading.Event()

            def _blocker():
                started.set()
                release.wait(10.0)

            marker = {"ran": False}

            def _queued_close():
                marker["ran"] = True

            f1 = pool.submit(_blocker)
            assert started.wait(5.0)
            f2 = pool.submit(_queued_close)
            # stop() 在唯一 worker 被僵尸 factory 占住时关闭池:
            # 排队的 close 必须不被取消 (daemon worker 随后排空)。
            shutdown_mootdx2_executor()
            assert f2.cancelled() is False
            release.set()
            assert f2.result(10.0) is None
            assert marker["ran"] is True
            f1.result(5.0)
            # 单例已复位, stop/start 重启拿到新池
            assert get_mootdx2_executor() is not pool
        finally:
            release.set()
            shutdown_mootdx2_executor()


# ---------------------------------------------------------------------------
# 4. lifecycle.stop()/init 失败收口
# ---------------------------------------------------------------------------


class _FakeUpstream:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _FakeCache:
    def __init__(self):
        self.closed = False

    async def initialize(self):
        pass

    async def close(self):
        self.closed = True


class _BoomConfigStore:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True
        raise RuntimeError("simulated config_store close failure")


class TestLifecycleStopHardening:
    @pytest.mark.asyncio
    async def test_config_store_close_error_does_not_abort_stop(
        self, gateway_server_minimal
    ):
        server = gateway_server_minimal
        server.upstreams = {}
        server._upstream_toggle_lock = asyncio.Lock()
        server._late_start_tasks = {}
        server.cache = None
        server._cache_cleanup_task = None
        server.config_store = _BoomConfigStore()
        server._published_tools = {"x"}
        server.tool_specs = [{"name": "x"}]
        server._db_hidden_tools = set()
        server.router = object()
        server.mcp = object()
        server._in_flight_requests["k"] = asyncio.Future()

        await server.stop()  # 不得抛

        assert server.config_store is None
        assert server._in_flight_requests == {}
        assert server.upstreams == {}
        assert server.router is None
        assert server.mcp is None
        assert server._initialized is False

    @pytest.mark.asyncio
    async def test_init_failure_cleanup_runs_on_cancelled_error(
        self, tmp_path, monkeypatch
    ):
        from src.unihive.gateway_server import lifecycle as lifecycle_mod

        fake_cache = _FakeCache()
        monkeypatch.setattr(
            lifecycle_mod.cache_mod, "Cache", lambda cfg: fake_cache
        )

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "upstreams": {},
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
        }
        fake_upstream = _FakeUpstream()
        server.upstreams = {"a": fake_upstream}
        server._late_start_tasks = {}
        server._upstream_toggle_lock = asyncio.Lock()
        server._in_flight_requests = {}
        server._cache_cleanup_task = None
        server.config_store = None
        server._db_hidden_tools = set()

        async def _noop_load():
            return None

        async def _boom(specs):
            raise asyncio.CancelledError()

        server._load_db_config = _noop_load
        server._load_all_tools = lambda: []
        server._create_and_register_gateway_mcp = _boom

        with pytest.raises(asyncio.CancelledError):
            await server._do_initialize()

        assert fake_upstream.stopped is True
        assert fake_cache.closed is True
        assert server.upstreams == {}
        assert server._cache_cleanup_task is None
        assert server.config_store is None
        assert server.router is None
        assert server.mcp is None


# ---------------------------------------------------------------------------
# 5. yaml 模式重放 gateway 调参
# ---------------------------------------------------------------------------


class TestYamlModeTuningReplay:
    """yaml 模式 gateway 调参契约 (__init__ 基线调一次, 不再在 _load_db_config
    重放 — 2026-10-01 启动精简: 重放冗余, 由 __init__ 单一来源负责)。"""

    @pytest.mark.asyncio
    async def test_yaml_load_skips_replay_and_init_applies_baseline(
        self, gateway_server_minimal
    ):
        reset_default_limiter()
        server = gateway_server_minimal
        server._db_mode = False
        server.config = {
            "gateway": {
                "per_upstream_timeout": 12.5,
                "rate_limit": {
                    "fuyao_a": {"rate": 5.0, "capacity": 10.0},
                },
            }
        }
        # yaml 逃生模式不再重放 _apply_gateway_tuning: 装配完成后调参应仍是
        # fixture 默认基线 (DEFAULT_REQUEST_TIMEOUT=30, limiter 空)。
        await server._load_db_config()
        assert server._per_upstream_timeout == 30.0
        assert server.limiter is not None
        assert server.limiter._configs.get("fuyao_a") is None

        # __init__ 单一基线: 手动跑一次 _apply_gateway_tuning, 验证 yaml 配置
        # 真能被装入 (与原 M1 invariant 同等覆盖)。
        server._apply_gateway_tuning()
        assert server._per_upstream_timeout == 12.5
        assert server.limiter._configs.get("fuyao_a") == (5.0, 10.0)


# ---------------------------------------------------------------------------
# 6. omni run_sync _ensure_db 离环
# ---------------------------------------------------------------------------


class TestOmniRunSyncOffLoop:
    @pytest.mark.asyncio
    async def test_ensure_db_runs_in_worker_thread(self, tmp_path):
        db_path = tmp_path / "board.db"
        client = OmniClient(OmniConfig(name="omni", db_path=str(db_path)))
        assert not db_path.exists()  # __init__ 不物化缺库

        ident = {}
        main_ident = threading.get_ident()
        original_ensure = client._ensure_db

        def _spy():
            ident["off_loop"] = threading.get_ident() != main_ident
            return original_ensure()

        client._ensure_db = _spy

        async def _fake_dispatch(source, board_type):
            return {
                "status": "success",
                "message": "ok",
                "record_count": 0,
                "stocks": 3,
            }

        client._dispatch_sync = _fake_dispatch
        result = await client.run_sync()
        assert result["status"] == "success"
        assert result["record_count"] == 3
        assert ident["off_loop"] is True
        assert db_path.exists()
