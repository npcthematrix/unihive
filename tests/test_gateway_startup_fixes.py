"""Tests for gateway_server startup-logic review fixes.

- HIGH #1: initialize() concurrent calls only run init once (asyncio.Lock)
- HIGH #2: upstreams: null in YAML doesn't crash initialize()
- HIGH1 (review): single upstream start() timeout isolates, doesn't abort init
- HIGH #3: --config CLI flag is accepted and plumbed through
- MED  #3 (review): health_check per-client timeout, slow client doesn't stall tick
- MED  #5: console_api imports deduped (single import path)
"""
from __future__ import annotations

import asyncio
import sys

import pytest


# ========== HIGH #1: initialize() concurrent safety ==========

class TestInitializeConcurrent:
    """两次 await initialize() 必须串行化, cache / upstream 只能 init 一次."""

    async def test_concurrent_initialize_calls_only_init_cache_once(self, tmp_path):
        """两个并发 initialize 调用, 内部 _do_initialize 只应跑一次."""
        from src.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
            "routing": {},
            "upstream_tool_mapping": {},
        }
        server.config_path = tmp_path / "upstreams.yaml"
        server.config_path.write_text("upstreams: {}\n", encoding="utf-8")
        server.upstreams = {}
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False
        server._init_lock = asyncio.Lock()
        server._upstream_start_timeout = 30.0

        do_init_calls = {"n": 0}
        real_do_init = server._do_initialize

        async def counting_do_init():
            do_init_calls["n"] += 1
            # 慢一点, 让第二个 await initialize() 有机会挤进来
            await asyncio.sleep(0.05)
            await real_do_init()

        server._do_initialize = counting_do_init  # type: ignore[assignment]

        # 同步发起两个 initialize
        await asyncio.gather(server.initialize(), server.initialize())

        assert do_init_calls["n"] == 1, (
            f"_do_initialize 跑了 {do_init_calls['n']} 次, 并发场景下应只跑 1 次"
        )
        assert server._initialized is True


# ========== HIGH #2: upstreams: null YAML safety ==========

class TestNullUpstreams:
    async def test_initialize_handles_null_upstreams(self, tmp_path):
        """YAML 里 `upstreams: null` 不能让 initialize() 抛 AttributeError."""
        from src.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": None,  # YAML `upstreams: null` 还原
            "routing": {},
            "upstream_tool_mapping": {},
        }
        server.config_path = tmp_path / "upstreams.yaml"
        server.config_path.write_text("upstreams: null\n", encoding="utf-8")
        server.upstreams = {}
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False
        server._init_lock = asyncio.Lock()
        server._upstream_start_timeout = 30.0

        # 不应抛 AttributeError: 'NoneType' object has no attribute 'items'
        await server.initialize()
        assert server._initialized is True
        assert server.upstreams == {}


# ========== HIGH1 (review): per-upstream timeout isolation ==========

class TestUpstreamTimeoutIsolation:
    """HIGH1: 单个 upstream 的 start() 超时不应让整个 initialize() 失败.

    旧行为: asyncio.wait_for 超时 → raise RuntimeError → 外层 except BaseException
    清空所有已启动 upstream + cache, 网关起不来。
    新行为: 超时 → log + 标记 UNAVAILABLE + 继续下一个 upstream。
    """

    async def test_single_upstream_timeout_does_not_break_init(self, tmp_path):
        """场景: good 立刻成功, bad 卡死超过 timeout. 期望: init 不抛, 两者都被跟踪."""
        from src.gateway_server import GatewayServer
        import src.gateway_server as gs

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {
                "good": {"enabled": True, "type": "http", "base_url": "http://x", "api_key": "k"},
                "bad": {"enabled": True, "type": "http", "base_url": "http://x", "api_key": "k"},
            },
            "routing": {},
            "upstream_tool_mapping": {},
        }
        server.config_path = tmp_path / "upstreams.yaml"
        server.config_path.write_text("upstreams: {}\n", encoding="utf-8")
        server.upstreams = {}
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False
        server._init_lock = asyncio.Lock()
        server._upstream_start_timeout = 0.2

        class FakeClient:
            def __init__(self, name: str):
                self.name = name
                self.is_available = (name == "good")
                self.status = type(
                    "S", (), {"value": "connected" if name == "good" else "unavailable"}
                )()

            async def start(self):
                if self.name == "bad":
                    await asyncio.sleep(10)

            async def stop(self):
                pass

        original_fuyao = gs.FuyaoClient
        gs.FuyaoClient = lambda cfg: FakeClient(cfg.name)  # type: ignore[assignment]

        try:
            # 不应抛 RuntimeError
            await server.initialize()
        finally:
            gs.FuyaoClient = original_fuyao  # type: ignore[assignment]

        # init 成功, 两个 upstream 都被收录 (bad 状态为 unavailable 但仍被跟踪)
        assert server._initialized is True
        assert "good" in server.upstreams
        assert "bad" in server.upstreams
        assert server.upstreams["good"].is_available is True
        assert server.upstreams["bad"].is_available is False

    async def test_timed_out_upstream_logs_error_not_raises(self, tmp_path, caplog):
        """超时的 upstream 必须打 ERROR 日志, 不能悄无声息."""
        import logging
        from src.gateway_server import GatewayServer
        import src.gateway_server as gs

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {
                "bad": {"enabled": True, "type": "http", "base_url": "http://x", "api_key": "k"},
            },
            "routing": {},
            "upstream_tool_mapping": {},
        }
        server.config_path = tmp_path / "upstreams.yaml"
        server.config_path.write_text("upstreams: {}\n", encoding="utf-8")
        server.upstreams = {}
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False
        server._init_lock = asyncio.Lock()
        server._upstream_start_timeout = 0.2

        class StuckClient:
            name = "bad"
            is_available = False
            status = type("S", (), {"value": "unavailable"})()

            async def start(self):
                await asyncio.sleep(10)

            async def stop(self):
                pass

        original_fuyao = gs.FuyaoClient
        gs.FuyaoClient = lambda cfg: StuckClient()  # type: ignore[assignment]

        try:
            with caplog.at_level(logging.ERROR):
                await server.initialize()

            error_msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
            assert any("timed out" in m and "bad" in m for m in error_msgs), (
                f"应记录 bad upstream 超时 ERROR 日志, 实际: {error_msgs}"
            )
        finally:
            gs.FuyaoClient = original_fuyao  # type: ignore[assignment]


# ========== MED #3 (review): health_check timeout ==========

class TestHealthCheckTimeout:
    """MED3: 单 client.health_check() 卡死不能阻塞 health loop 整个 tick."""

    async def test_slow_health_check_does_not_block_other_clients(self, tmp_path):
        """slow client 永远挂死, fast client 仍必须被 health_check 到."""
        from src.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
            "routing": {},
            "upstream_tool_mapping": {},
        }
        server.config_path = tmp_path / "upstreams.yaml"
        server.config_path.write_text("upstreams: {}\n", encoding="utf-8")
        server.upstreams = {}
        server.cache = None
        server._running = True
        server._shutdown_event = asyncio.Event()
        server._health_check_timeout = 0.1  # 100ms timeout

        class SlowClient:
            name = "slow"
            is_available = True
            status = type("S", (), {"value": "healthy"})()
            health_check_called = 0

            async def health_check(self):
                self.health_check_called += 1
                await asyncio.sleep(100)  # 永远不返回

            async def stop(self):
                pass

        class FastClient:
            name = "fast"
            is_available = True
            status = type("S", (), {"value": "healthy"})()
            health_check_called = 0

            async def health_check(self):
                self.health_check_called += 1

            async def stop(self):
                pass

        slow = SlowClient()
        fast = FastClient()
        # slow 在前 — 无 timeout 时 fast 永远等不到
        server.upstreams = {"slow": slow, "fast": fast}

        loop_task = asyncio.create_task(server._health_check_loop())

        # 等 fast 至少被调用过 (说明 loop 没被 slow 拖死)
        deadline = asyncio.get_event_loop().time() + 2.0
        while fast.health_check_called == 0 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)

        server._running = False
        server._shutdown_event.set()
        try:
            await asyncio.wait_for(loop_task, timeout=2)
        except asyncio.TimeoutError:
            loop_task.cancel()

        assert fast.health_check_called >= 1, (
            f"fast client 应至少被 health_check 1 次, 实际 {fast.health_check_called} 次 "
            f"(loop 被 slow 拖住)"
        )
        # slow 至少被启动过一次 (loop 真的尝试调它了)
        assert slow.health_check_called >= 1


# ========== HIGH #3: --config CLI flag ==========

class TestConfigCLI:
    def test_config_flag_accepted_by_argparser(self):
        """`--config PATH` 必须被 argparse 接受并出现在 Namespace.config 里."""
        from src.gateway_server import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args(["--config", "/tmp/custom-upstreams.yaml"])
        assert args.config == "/tmp/custom-upstreams.yaml"

    def test_config_flag_optional_defaults_none(self):
        """不传 --config 时, args.config 必须为 None (让默认值生效)."""
        from src.gateway_server import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args([])
        assert args.config is None

    def test_async_main_accepts_config_path(self, tmp_path):
        """async_main 必须能把 config_path 传给 GatewayServer — 通过 monkeypatch 验证."""
        cfg_file = tmp_path / "upstreams.yaml"
        cfg_file.write_text(
            "upstreams:\n  good:\n    enabled: true\n    type: http\n"
            "    base_url: http://x\n    api_key: k\n",
            encoding="utf-8",
        )

        from src import gateway_server as gs

        seen: dict[str, str] = {}

        original_init = gs.GatewayServer.__init__

        def spy_init(self, config_path="config/upstreams.yaml", **kwargs):
            seen["path"] = config_path
            return original_init(self, config_path=config_path, **kwargs)

        gs.GatewayServer.__init__ = spy_init  # type: ignore[assignment]

        try:
            # 喂 sys.argv 让 argparse 拿到 --config
            argv_backup = sys.argv
            sys.argv = ["prog", "--transport", "http", "--config", str(cfg_file)]
            try:
                # http transport 起 uvicorn 会一直跑, 用 timeout 卡住再取消
                # 这里只验证 main() 把 config_path 传进去了
                import threading
                # 跑 main 在另一个线程, 1s 后强制退出
                def run_main():
                    try:
                        gs.main()
                    except SystemExit:
                        pass
                t = threading.Thread(target=run_main, daemon=True)
                t.start()
                t.join(timeout=1.5)
            finally:
                sys.argv = argv_backup

            assert seen.get("path") == str(cfg_file), (
                f"GatewayServer 应收到自定义 config 路径, 实际: {seen}"
            )
        finally:
            gs.GatewayServer.__init__ = original_init  # type: ignore[assignment]


# ========== MED #5: console_api imports deduped ==========

class TestConsoleApiImportsDeduped:
    def test_serve_http_has_single_console_api_import(self):
        """serve_http 里只应出现一次 `from . import console_api` 形式导入."""
        import inspect
        from src.gateway_server import GatewayServer

        src = inspect.getsource(GatewayServer.serve_http)
        # `from . import console_api` 或 `from .console_api import` 应各最多一次
        from_relative = src.count("from . import console_api")
        direct_relative = src.count("from .console_api import")
        assert from_relative + direct_relative <= 1, (
            f"serve_http 里 console_api 导入重复: "
            f"`from . import console_api`={from_relative}, "
            f"`from .console_api import`={direct_relative}"
        )
