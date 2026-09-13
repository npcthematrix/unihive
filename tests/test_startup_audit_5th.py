"""Tests for 5th-round startup audit (H1/M1/M2/M3/M4/M5 + L1-L5).

H1: serve_http port conflict must trigger stop() cleanup so health_task /
    upstreams / cache don't leak. Pre-fix: sys.exit(2) was raised before
    the try/finally that wraps uvicorn.serve(), so stop() was never called
    and the half-initialized gateway leaked resources.
"""
from __future__ import annotations

import asyncio
import socket
import sys
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_root_logging_handlers():
    """test_gateway_startup_batch3 autouse fixture pattern: configure_logging
    installed a sys.stderr StreamHandler that interferes with capsys across
    tests. Reset root handlers after each test.
    """
    yield
    import logging

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)


# ========== H1: port conflict must trigger stop() ==========

class TestPortConflictTriggersStop:
    async def test_port_in_use_calls_stop_for_cleanup(self, tmp_path, capsys):
        """H1: serve_http 端口冲突时 stop() 必须被调用, 否则 health_task /
        upstreams / cache 全部泄漏。

        Pre-fix: sys.exit(2) 在 try/finally 外面, 直接 raise SystemExit,
        finally 不跑, stop() 不被调。
        Post-fix: port probe + sys.exit(2) 都在 outer try/finally 里,
        stop() 在 finally 兜底。
        """
        from src import gateway_server as gs

        # 占住端口
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)
        try:
            cfg = tmp_path / "upstreams.yaml"
            cfg.write_text(
                "upstreams:\n  x:\n    enabled: true\n    type: http\n"
                "    base_url: http://x\n    api_key: k\n",
                encoding="utf-8",
            )

            # 用 monkeypatch 跟踪 stop() 是否被调
            stop_calls = []
            real_stop = gs.GatewayServer.stop

            async def tracking_stop(self):
                stop_calls.append(self)
                return await real_stop(self)

            with patch.object(gs.GatewayServer, "stop", tracking_stop):
                server = gs.GatewayServer(
                    config_path=str(cfg), strict_env=False
                )
                with pytest.raises(SystemExit) as exc_info:
                    await server.serve_http(host="127.0.0.1", port=port)
                assert exc_info.value.code == 2

            assert stop_calls, (
                "stop() must be invoked on port conflict to clean up "
                "health_task / upstreams / cache (H1, 5th-round audit)"
            )

            # 不变量: stop() 之后 _running 应回到 False, 允许重试 start()
            assert server._running is False

            captured = capsys.readouterr()
            assert "port already in use" in captured.err
            assert "traceback" not in captured.err.lower()
        finally:
            s.close()


# ========== M4: SIGTERM-driven shutdown via watcher ==========

class TestStartUnwindsOnShutdownEvent:
    async def test_sigterm_event_triggers_run_stdio_cancel_and_stop(
        self, tmp_path, monkeypatch
    ):
        """M4: setting _shutdown_event (simulating SIGTERM) must cancel
        run_stdio_async() and trigger stop() via the outer finally.

        Pre-fix: SIGTERM handler only set _shutdown_event. run_stdio_async
        doesn't watch it. So even though stop() was in the finally, it
        never ran because the await never returned.
        Post-fix: a _signal_watcher task waits on the event; when set, it
        cancels the main task. run_stdio_async unwinds with
        CancelledError, finally runs stop().
        """
        from src.unihive.gateway_server import GatewayServer
        from unittest.mock import MagicMock

        cfg = tmp_path / "upstreams.yaml"
        cfg.write_text("upstreams: {}", encoding="utf-8")

        async def fake_init(self):
            self._initialized = True
            self.cache = None
            self.upstreams = {}
            self.router = None
            self.mcp = MagicMock()
            self._stdio_done = asyncio.Event()

            async def fake_run_stdio():
                # Never returns naturally — only on cancellation
                await self._stdio_done.wait()

            self.mcp.run_stdio_async = fake_run_stdio

        monkeypatch.setattr(GatewayServer, "initialize", fake_init)

        # Track stop calls
        stop_calls = []
        real_stop = GatewayServer.stop

        async def tracking_stop(self):
            stop_calls.append(True)
            return await real_stop(self)

        monkeypatch.setattr(GatewayServer, "stop", tracking_stop)

        server = GatewayServer(
            config_path=str(cfg), strict_env=False, strict_validation=False
        )

        # Run start() in background; it should block in run_stdio_async
        start_task = asyncio.create_task(server.start())

        # Give it time to enter run_stdio_async (initialize + create health)
        await asyncio.sleep(0.1)

        # Simulate SIGTERM: handler would call _shutdown_event.set()
        server._shutdown_event.set()

        # Wait (without cancelling) for start() to unwind on its own.
        # If the watcher works, _signal_watcher sees the event set, cancels
        # the main task, run_stdio_async unwinds, finally calls stop().
        # If watcher is broken, start_task stays pending forever.
        for _ in range(30):  # up to 3s, polling every 100ms
            if start_task.done():
                break
            await asyncio.sleep(0.1)

        # Verify watcher did its job: stop() was called
        assert stop_calls, (
            "stop() must run after _shutdown_event is set (M4, 5th-round "
            "audit). Without the watcher, run_stdio_async never returns "
            "and stop() never runs."
        )
        assert start_task.done(), (
            "start() must unwind within 3s of _shutdown_event.set(); "
            "watcher failed to cancel main task"
        )

        # Drain any pending exception from start_task so it doesn't leak
        if not start_task.cancelled() and start_task.exception() is not None:
            # CancelledError is expected and OK; re-raise others for debugging
            exc = start_task.exception()
            if not isinstance(exc, asyncio.CancelledError):
                raise exc


# ========== M5: initialize() failure must reset state ==========

class TestInitializeFailureResetsRunning:
    async def test_initialize_failure_resets_running_and_runs_stop(
        self, tmp_path, monkeypatch
    ):
        """M5: start() / serve_http() 在 initialize() 抛异常时必须:
        1. _running 回到 False (允许重试)
        2. stop() 在 finally 里跑 (清理 partial 状态)

        Pre-fix: _running = True 在 initialize() 之前, initialize 失败时
        _running 留在 True。_do_initialize 的 except 块清掉了 upstreams /
        cache / mcp, 但 health_task 没创建 (line 在 initialize 之后),
        stop() 也未被调 (整个 try/finally 还没进)。
        """
        from src.unihive.gateway_server import GatewayServer

        cfg = tmp_path / "upstreams.yaml"
        cfg.write_text("upstreams: {}", encoding="utf-8")

        async def fake_init_raises(self):
            self._initialized = False
            self.upstreams = {"x": object()}  # partial state
            raise RuntimeError("simulated init failure (M5 test)")

        monkeypatch.setattr(GatewayServer, "initialize", fake_init_raises)

        stop_calls = []
        real_stop = GatewayServer.stop

        async def tracking_stop(self):
            stop_calls.append(True)
            return await real_stop(self)

        monkeypatch.setattr(GatewayServer, "stop", tracking_stop)

        server = GatewayServer(
            config_path=str(cfg), strict_env=False, strict_validation=False
        )

        # start() should propagate the exception
        with pytest.raises(RuntimeError, match="simulated init failure"):
            await server.start()

        # M5 invariant: _running must be False after init failure
        assert server._running is False, (
            "_running must be reset to False on init failure (M5). "
            "Otherwise retry start() sees stale _running=True and may "
            "create duplicate health_tasks."
        )
        assert stop_calls, (
            "stop() must run on init failure path (M5) — _do_initialize's "
            "except block already cleans partial state, but stop() also "
            "needs to fire for full cleanup contract."
        )

    async def test_serve_http_initialize_failure_resets_running(
        self, tmp_path, monkeypatch
    ):
        """M5: serve_http 同理 — initialize 失败必须重置 _running 并调 stop()."""
        from src.unihive.gateway_server import GatewayServer

        cfg = tmp_path / "upstreams.yaml"
        cfg.write_text(
            "upstreams:\n  x:\n    enabled: true\n    type: http\n"
            "    base_url: http://x\n    api_key: k\n",
            encoding="utf-8",
        )

        async def fake_init_raises(self):
            self._initialized = False
            raise RuntimeError("simulated init failure (serve_http M5)")

        monkeypatch.setattr(GatewayServer, "initialize", fake_init_raises)

        stop_calls = []
        real_stop = GatewayServer.stop

        async def tracking_stop(self):
            stop_calls.append(True)
            return await real_stop(self)

        monkeypatch.setattr(GatewayServer, "stop", tracking_stop)

        server = GatewayServer(
            config_path=str(cfg), strict_env=False, strict_validation=False
        )

        # serve_http should propagate
        with pytest.raises(RuntimeError, match="simulated init failure"):
            await server.serve_http(host="127.0.0.1", port=18099)

        assert server._running is False
        assert stop_calls


# ========== M1: cache layer operation timeout ==========

class TestCacheOperationTimeout:
    async def test_get_returns_none_on_timeout(self, tmp_path, monkeypatch):
        """M1: cache.get 内 SQLite 卡死时, wait_for 超时应返回 None,
        不让 request 永久 hang。
        """
        from src.unihive.storage.cache import Cache, CacheConfig

        cfg = CacheConfig(
            enabled=True,
            db_path=str(tmp_path / "test.db"),
            operation_timeout=0.2,
        )
        cache = Cache(cfg)
        await cache.initialize()

        # 让 _get_impl hang, 模拟 SQLite 卡死
        async def hanging(self, key):
            await asyncio.Event().wait()  # 等到天荒地老

        monkeypatch.setattr(Cache, "_get_impl", hanging)

        import time as _time
        start = _time.time()
        result = await cache.get("any_key")
        elapsed = _time.time() - start

        assert result is None, "cache.get should return None on timeout"
        assert elapsed < 1.0, f"get should time out around 0.2s, took {elapsed:.2f}s"

    async def test_set_returns_false_on_timeout(self, tmp_path, monkeypatch):
        """M1: cache.set 超时应返回 False (写入失败, 但不挂死)。"""
        from src.unihive.storage.cache import Cache, CacheConfig

        cfg = CacheConfig(
            enabled=True,
            db_path=str(tmp_path / "test.db"),
            operation_timeout=0.2,
        )
        cache = Cache(cfg)
        await cache.initialize()

        async def hanging(self, key, value, ttl=None):
            await asyncio.Event().wait()

        monkeypatch.setattr(Cache, "_set_impl", hanging)

        result = await cache.set("k", {"x": 1}, ttl=60)
        assert result is False

    async def test_cleanup_expired_returns_zero_on_timeout(
        self, tmp_path, monkeypatch
    ):
        """M3: cleanup_expired 超时应返回 0 (清理失败, 但 health loop 不挂死)。"""
        from src.unihive.storage.cache import Cache, CacheConfig

        cfg = CacheConfig(
            enabled=True,
            db_path=str(tmp_path / "test.db"),
            operation_timeout=0.2,
        )
        cache = Cache(cfg)
        await cache.initialize()

        async def hanging(self):
            await asyncio.Event().wait()

        monkeypatch.setattr(Cache, "_cleanup_impl", hanging)

        result = await cache.cleanup_expired()
        assert result == 0

    async def test_operation_timeout_configurable(self, tmp_path):
        """M1: operation_timeout 应可通过 CacheConfig 配置, 默认 5s。"""
        from src.unihive.storage.cache import Cache, CacheConfig

        cfg = CacheConfig(enabled=True, db_path=str(tmp_path / "test.db"))
        cache = Cache(cfg)
        assert cache.config.operation_timeout == 5.0

        cfg2 = CacheConfig(
            enabled=True, db_path=str(tmp_path / "test2.db"), operation_timeout=2.5
        )
        cache2 = Cache(cfg2)
        assert cache2.config.operation_timeout == 2.5


# ========== M2: waiter counted in _active_requests ==========

class TestWaiterCountedInActiveRequests:
    async def test_waiters_counted_in_active_requests(
        self, tmp_path, gateway_server_minimal
    ):
        """M2: 单飞 waiter 必须计入 _active_requests, stop() 优雅关闭时
        才会等它们完成。

        Pre-fix: 只有 leader 调 _route_and_respond 会 += 1, waiter 不计数。
        Post-fix: 整个 _execute_cached 入口 + 出口都计数, waiter 也算上。
        """
        from dataclasses import dataclass
        from src.unihive.storage.cache import Cache, CacheConfig

        @dataclass
        class FakeResult:
            success: bool = True
            data: dict = None
            source: str = "fuyao_ashare"
            hops: list = None
            error: str | None = None

            def __post_init__(self):
                if self.hops is None:
                    self.hops = []

        class GatedRouter:
            """Gate: route 在 _gate.set() 之前不返回, 用于让 5 个请求都
            进入 _execute_cached 后再一起放行。"""
            def __init__(self):
                self.calls = 0
                self._gate = asyncio.Event()

            async def route(self, route_key, params, force_source=None):
                self.calls += 1
                await self._gate.wait()
                return FakeResult(data={"v": self.calls})

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "c.db")))
        await cache.initialize()

        server = gateway_server_minimal
        server.cache = cache
        server.router = GatedRouter()
        server.config = {"cache": {"ttl": {"test_key": 60}}}

        try:
            # 启动 5 个并发同 key 请求 (后台, 不等)
            tasks = [
                asyncio.create_task(server._execute_cached(
                    "t", {"k": "v"}, route_key="t", ttl_key="test_key"
                ))
                for _ in range(5)
            ]

            # 等 leader 进入 router.route (gate 已就位)
            for _ in range(20):
                if server.router.calls >= 1:
                    break
                await asyncio.sleep(0.02)

            # 先 set gate 让所有 task 都能完成, 然后再 assert (避免 assertion
            # 失败后 task 阻塞 30s 等 DEFAULT_REQUEST_TIMEOUT)
            server.router._gate.set()

            # M2 不变量: 5 个请求都已进 _execute_cached, _active_requests == 5
            # Pre-fix: 只有 leader (1) 计数, waiter 不计数。
            # 此时所有 task 仍在 _route_and_respond 路径上 (leader) 或
            # await existing 路径上 (waiter),_active_requests 应 == 5。
            assert server._active_requests == 5, (
                f"M2: all 5 concurrent requests must count in "
                f"_active_requests (waiters included). Got "
                f"{server._active_requests}"
            )

            results = await asyncio.gather(*tasks)

            assert server._active_requests == 0
            assert len(results) == 5
            # single-flight 仍成立 (跟 4th round HIGH 1 一致)
            assert server.router.calls == 1
        finally:
            server.router._gate.set()  # 万一 assertion 失败前就到这里, 兜底
            await cache.close()

