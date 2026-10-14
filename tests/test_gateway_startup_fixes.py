"""Tests for gateway_server startup-logic review fixes.

- HIGH #1: initialize() concurrent calls only run init once (asyncio.Lock)
- HIGH #2: upstreams: null in YAML doesn't crash initialize()
- HIGH1 (review): single upstream start() timeout isolates, doesn't abort init
- HIGH #3: --config CLI flag is accepted and plumbed through
- MED  #3 (review): health_check per-client timeout, slow client doesn't stall tick
- LOW  #8 (review): get_health() aggregates upstream status (not hardcoded healthy)
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
        from src.unihive.gateway_server import GatewayServer

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
        from src.unihive.gateway_server import GatewayServer

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
        server._init_lock = asyncio.Lock()
        server._upstream_start_timeout = 30.0

        # 不应抛 AttributeError: 'NoneType' object has no attribute 'items'
        await server.initialize()
        assert server._initialized is True
        assert server.upstreams == {}


# ========== HIGH1 (review): per-upstream timeout isolation ==========

class TestUpstreamBackgroundStart:
    """boot 期上游 fire-and-forget: initialize() 不 await 任何上游 start()。

    新行为: client 构造后立即登记 (启动中态), start() 丢后台 task;
    initialize() 很快返回, 慢上游在后台慢慢启动, 不阻塞网关对外服务。
    """

    def _build_server(self, tmp_path):
        from src.unihive.gateway_server import GatewayServer

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
        server._init_lock = asyncio.Lock()
        server._upstream_start_timeout = 0.2
        server._late_start_tasks = {}
        return server

    async def test_init_does_not_wait_for_upstreams(self, tmp_path):
        """good 立刻成功, bad 卡死; initialize() 不等, 两者都立即登记。"""
        import src.unihive.gateway_server as gs

        server = self._build_server(tmp_path)

        class FakeClient:
            def __init__(self, name: str):
                self.name = name
                self.is_available = name == "good"
                self.status = type(
                    "S", (), {"value": "connected" if name == "good" else "unknown"}
                )()

            async def start(self):
                if self.name == "bad":
                    await asyncio.sleep(10)

            async def stop(self):
                pass

        import src.unihive.gateway_server.upstreams as gs_upstreams
        original_fuyao = gs_upstreams.FuyaoClient
        gs_upstreams.FuyaoClient = lambda cfg: FakeClient(cfg.name)  # type: ignore[assignment]

        try:
            await server.initialize()
        finally:
            gs_upstreams.FuyaoClient = original_fuyao  # type: ignore[assignment]

        # init 完成且两个 upstream 都已登记; bad 的后台 start 仍在跑 (未 ready)。
        assert server._initialized is True
        assert "good" in server.upstreams
        assert "bad" in server.upstreams
        assert server.upstreams["good"].is_available is True
        assert server.upstreams["bad"].is_available is False
        # bad 对应一个在途后台启动任务
        assert server._late_start_tasks.get("bad")

    async def test_background_start_failure_marks_unavailable(self, tmp_path):
        """后台 start() 抛错 → client 落 UNAVAILABLE, 不留 UNKNOWN。"""
        import src.unihive.gateway_server as gs
        from src.unihive.api.upstream_client import UpstreamStatus

        server = self._build_server(tmp_path)

        class FailClient:
            """模拟真实 client: status 是读 self._status 的 property。"""

            name = "bad"

            def __init__(self):
                self.is_available = False
                self._status = UpstreamStatus.UNKNOWN

            @property
            def status(self):
                return self._status

            async def start(self):
                raise RuntimeError("boom")

            async def stop(self):
                pass

        import src.unihive.gateway_server.upstreams as gs_upstreams
        original_fuyao = gs_upstreams.FuyaoClient
        gs_upstreams.FuyaoClient = lambda cfg: FailClient()  # type: ignore[assignment]

        try:
            await server.initialize()
            # 让已 schedule 的后台 start 任务跑一轮 (start() 立即抛错, 任务
            # 完成后会自行从跟踪集移除, 故不对集合做断言)。
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        finally:
            gs_upstreams.FuyaoClient = original_fuyao  # type: ignore[assignment]

        assert server.upstreams["bad"].status is UpstreamStatus.UNAVAILABLE


# ========== LOW #8 (review): get_health() aggregates upstream status ==========

class TestGetHealthAggregation:
    """LOW8: get_health() 必须聚合 upstream 状态, 不能硬编码 healthy."""

    async def _build_server_with_upstreams(self, statuses: dict[str, str]):
        """构造带 mock upstream 的 server. statuses: {name: status_value}."""
        from src.unihive.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {"upstreams": {}}
        server.upstreams = {}
        server.cache = None
        server._shutdown_event = asyncio.Event()

        from src.unihive.api.upstream_client import UpstreamStatus

        for name, status_value in statuses.items():
            client_status = UpstreamStatus(status_value)

            class MockClient:
                async def stop(self):
                    pass

            mc = MockClient()
            mc.name = name
            mc.status = client_status
            mc.is_available = status_value == "healthy"
            server.upstreams[name] = mc
        return server

    async def test_all_healthy_returns_healthy(self):
        from src.unihive.gateway_server import GatewayServer

        server = await self._build_server_with_upstreams(
            {"a": "healthy", "b": "healthy"}
        )
        result = GatewayServer.aggregate_health(server.upstreams)
        assert result["status"] == "healthy"
        assert result["timestamp"] > 0
        assert result["upstreams"]["a"] == "healthy"
        assert result["upstreams"]["b"] == "healthy"

    async def test_some_degraded_returns_degraded(self):
        from src.unihive.gateway_server import GatewayServer

        server = await self._build_server_with_upstreams(
            {"a": "healthy", "b": "degraded"}
        )
        result = GatewayServer.aggregate_health(server.upstreams)
        assert result["status"] == "degraded"

    async def test_some_unavailable_returns_degraded(self):
        """任一 upstream 不可用应反映到整体 health."""
        from src.unihive.gateway_server import GatewayServer

        server = await self._build_server_with_upstreams(
            {"a": "healthy", "b": "unavailable"}
        )
        result = GatewayServer.aggregate_health(server.upstreams)
        assert result["status"] == "degraded"

    async def test_all_unavailable_returns_unhealthy(self):
        from src.unihive.gateway_server import GatewayServer

        server = await self._build_server_with_upstreams(
            {"a": "unavailable", "b": "unavailable"}
        )
        result = GatewayServer.aggregate_health(server.upstreams)
        assert result["status"] == "unhealthy"

    async def test_no_upstreams_returns_healthy(self):
        """无 upstream 时不应误报 degraded."""
        from src.unihive.gateway_server import GatewayServer

        server = await self._build_server_with_upstreams({})
        result = GatewayServer.aggregate_health(server.upstreams)
        assert result["status"] == "healthy"


# ========== HIGH #3: --config CLI flag ==========

class TestConfigCLI:
    def test_config_flag_accepted_by_argparser(self):
        """`--config PATH` 必须被 argparse 接受并出现在 Namespace.config 里."""
        from src.unihive.gateway_server import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args(["--config", "/tmp/custom-upstreams.yaml"])
        assert args.config == "/tmp/custom-upstreams.yaml"

    def test_config_flag_optional_defaults_none(self):
        """不传 --config 时, args.config 必须为 None (让默认值生效)."""
        from src.unihive.gateway_server import _build_arg_parser

        parser = _build_arg_parser()
        args = parser.parse_args([])
        assert args.config is None

    def test_keyboard_interrupt_exits_cleanly(self, monkeypatch):
        """stdio 模式父进程关停 → KeyboardInterrupt 干净退出 (码 0), 不裸打 traceback."""
        from src.unihive import gateway_server as gs

        def _interrupt(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(gs.asyncio, "run", _interrupt)
        monkeypatch.setattr(gs.sys, "argv", ["unihive"])

        with pytest.raises(SystemExit) as excinfo:
            gs.main()

        assert excinfo.value.code == 0

    def test_async_main_accepts_config_path(self, tmp_path):
        """async_main 必须能把 config_path 传给 GatewayServer — 通过 monkeypatch 验证."""
        cfg_file = tmp_path / "upstreams.yaml"
        cfg_file.write_text(
            "upstreams:\n  good:\n    enabled: true\n    type: http\n"
            "    base_url: http://x\n    api_key: k\n",
            encoding="utf-8",
        )

        from src.unihive import gateway_server as gs

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
        from src.unihive.gateway_server import GatewayServer

        src = inspect.getsource(GatewayServer.serve_http)
        # `from . import console_api` 或 `from .console_api import` 应各最多一次
        from_relative = src.count("from . import console_api")
        direct_relative = src.count("from .console_api import")
        assert from_relative + direct_relative <= 1, (
            f"serve_http 里 console_api 导入重复: "
            f"`from . import console_api`={from_relative}, "
            f"`from .console_api import`={direct_relative}"
        )
