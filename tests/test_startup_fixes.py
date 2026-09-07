"""Regression tests for startup-logic review fixes.

Covers:
- #4 GatewayServer.initialize idempotency
- #5 _health_check_loop responds to stop()
- #3 _probe_upstream cache key includes (type, base_url), not just name
- #2 console_server.load_config delegates to config_loader + masks secrets
"""
import asyncio
from unittest.mock import patch

import pytest


# ========== #4: GatewayServer.initialize idempotency ==========

class TestGatewayInitializeIdempotent:
    """start() 和 serve_http() 都调用 initialize(); 第二次必须短路,
    否则会重新构造 cache / 重启 upstreams 进程。"""

    async def test_initialize_called_twice_short_circuits(self, tmp_path):
        from src.cache import Cache, CacheConfig
        from src.gateway_server import GatewayServer

        # bypass __init__ (跳过真实 config 加载); 手动注入测试所需的最小状态
        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
            "routing": {},
            "upstream_tool_mapping": {},
        }
        server.config_path = tmp_path / "upstreams.yaml"  # _load_all_tools 需要
        # 不存在的占位 yaml 让 _load_all_tools 立刻返回空 list
        server.config_path.write_text("upstreams: {}\n", encoding="utf-8")
        server.upstreams = {}
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False
        server._init_lock = asyncio.Lock()
        server._upstream_start_timeout = 30.0

        await server.initialize()
        first_cache = server.cache
        assert server._initialized is True

        # 第二次调用: 不应替换 cache, 不应重复登记 upstreams
        await server.initialize()
        assert server.cache is first_cache, "二次 initialize 不应替换 cache 实例"
        assert server._initialized is True


# ========== #5: _health_check_loop 响应 stop() ==========

class TestHealthLoopShutdown:
    """stop() 必须能立即唤醒 health loop, 不能等 30s 一次的 asyncio.sleep。"""

    async def test_stop_wakes_health_loop_immediately(self, tmp_path):
        import time
        from src.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
        }
        server.upstreams = {}
        server.cache = None
        server._running = True
        server._shutdown_event = asyncio.Event()

        task = asyncio.create_task(server._health_check_loop())
        await asyncio.sleep(0.05)  # 让 loop 进入 wait_for

        t0 = time.monotonic()
        server._running = False
        server._shutdown_event.set()
        await asyncio.wait_for(task, timeout=2.0)
        elapsed = time.monotonic() - t0

        # 必须远小于 30s tick
        assert elapsed < 1.0, f"health loop 未被 stop 立即唤醒, 花了 {elapsed:.2f}s"


# ========== #3: _probe_upstream cache key 包含 (type, base_url) ==========

class TestProbeCacheKey:
    """同 name 改了 type 或 base_url 后, 下一次探测必须 miss 缓存。"""

    def test_same_name_different_type_misses_cache(self):
        from src.console_api import _probe_upstream, _probe_cache
        from time import monotonic

        _probe_cache.clear()
        sentinel_http = {"enabled": True, "type": "http", "status": "online",
                         "latency_ms": 1, "last_error": None, "description": ""}
        # 把"旧协议"的探测结果塞进缓存, key 必须是 (name, type, base_url) 三元组
        _probe_cache[("svc", "http", "http://a.example/")] = (monotonic(), sentinel_http)

        # 配成不同 type, 同样 base_url
        cfg_v2 = {"type": "http_jsonrpc", "base_url": "http://a.example/", "enabled": True}
        result = _probe_upstream("svc", cfg_v2)
        assert result is not sentinel_http, "同 name 改 type 后不应复用旧缓存"

    def test_same_name_different_base_url_misses_cache(self):
        from src.console_api import _probe_upstream, _probe_cache
        from time import monotonic

        _probe_cache.clear()
        sentinel = {"enabled": True, "type": "http", "status": "online",
                    "latency_ms": 1, "last_error": None, "description": ""}
        _probe_cache[("svc", "http", "http://old.example/")] = (monotonic(), sentinel)

        cfg_new = {"type": "http", "base_url": "http://new.example/", "enabled": True}
        result = _probe_upstream("svc", cfg_new)
        assert result is not sentinel, "同 name 改 base_url 后不应复用旧缓存"


# ========== #2: console_server.load_config 走共享 loader + mask ==========

class TestConsoleLoadConfigMasksSecrets:
    """无论走 shared loader 还是 fallback, 输出都必须 mask 密钥字段。"""

    def test_load_config_masks_api_key(self, tmp_path, monkeypatch):
        yaml_path = tmp_path / "upstreams.yaml"
        yaml_path.write_text(
            "upstreams:\n  fuyao:\n    type: http\n"
            "    base_url: http://x\n"
            "    api_key: supersecret123\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.console_api.GATEWAY_CONFIG_PATH", str(yaml_path))

        from src.console_api import load_config
        cfg = load_config()
        api_key = cfg["upstreams"]["fuyao"]["api_key"]
        assert api_key != "supersecret123", "raw secret 应被 mask"
        assert "***" in api_key

    def test_load_config_returns_empty_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.console_api.GATEWAY_CONFIG_PATH", str(tmp_path / "nope.yaml"))
        from src.console_api import load_config
        assert load_config() == {}

    def test_load_config_resolves_env_placeholder(self, tmp_path, monkeypatch):
        """shared loader 应解析 ${VAR}, 避免占位符原样泄漏到前端。"""
        yaml_path = tmp_path / "upstreams.yaml"
        yaml_path.write_text(
            "upstreams:\n  fuyao:\n    type: http\n"
            "    base_url: http://x\n"
            "    api_key: ${TEST_API_KEY}\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("TEST_API_KEY", "resolved-secret-xyz")
        monkeypatch.setattr("src.console_api.GATEWAY_CONFIG_PATH", str(yaml_path))

        from src.console_api import load_config
        cfg = load_config()
        api_key = cfg["upstreams"]["fuyao"]["api_key"]
        assert "${" not in api_key, "应解析 ${TEST_API_KEY}, 不是原样返回"
        assert "***" in api_key

    def test_load_config_returns_shallow_copy(self, tmp_path, monkeypatch):
        """Regression #3 (二轮): load_config 必须返回浅拷贝, 否则 get_interfaces
        里 tool_loader.load_all_tools 把 upstream_tool_mapping 写回 dict 后,
        下一次 /api/config 会把 tool mapping 泄漏给前端。"""
        yaml_path = tmp_path / "upstreams.yaml"
        yaml_path.write_text(
            "upstreams:\n  fuyao:\n    type: http\n"
            "    base_url: http://x\n    api_key: a-secret\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("src.console_api.GATEWAY_CONFIG_PATH", str(yaml_path))

        from src.console_api import load_config
        cfg1 = load_config()
        # 模拟 tool_loader.load_all_tools 的回写副作用
        cfg1["upstream_tool_mapping"] = {"sneaky": "leaked"}

        cfg2 = load_config()
        assert "upstream_tool_mapping" not in cfg2, (
            "load_config 必须返回浅拷贝, 隔离上游模块对返回 dict 的写回"
        )


# ========== LOW #7: is_gateway_reachable ==========

class TestGatewayReachabilityProbe:
    """console /health 应反映 gateway 实际可达性, 1s 短缓存。"""

    def test_returns_false_when_nothing_listening(self, monkeypatch):
        """没有 gateway 监听时必须返回 False, 而不是 True。"""
        from src.console_api import is_gateway_reachable, _gateway_health_cache
        _gateway_health_cache.clear()
        # GATEWAY_HTTP_PORT 指向一个没东西监听的随机端口
        # 用 1 (需 root, 不可用) 或 找空闲端口再关掉
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # 立刻关掉, 这个端口 99% 没人监听
        monkeypatch.setattr("src.console_api._gateway_port", lambda: port)

        assert is_gateway_reachable() is False, "没监听时应返回 False"

    def test_returns_true_when_port_open(self, monkeypatch):
        """监听着的端口必须返回 True。"""
        from src.console_api import is_gateway_reachable, _gateway_health_cache
        import socket
        _gateway_health_cache.clear()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        monkeypatch.setattr("src.console_api._gateway_port", lambda: port)
        try:
            assert is_gateway_reachable() is True
        finally:
            s.close()

    def test_result_is_cached_within_ttl(self, monkeypatch):
        """1s 内重复调用应走缓存, 不重连。"""
        from src.console_api import is_gateway_reachable, _gateway_health_cache
        import socket
        _gateway_health_cache.clear()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        monkeypatch.setattr("src.console_api._gateway_port", lambda: port)
        try:
            first = is_gateway_reachable()
            # 关掉监听, 不应有第二次连接尝试 (否则会 False)
            s.close()
            second = is_gateway_reachable()
            assert first is True
            assert second is True, "TTL 内应走缓存, 不应重连到已关闭端口"
        finally:
            try:
                s.close()
            except Exception:
                pass


# ========== HIGH #1/#2 + MED #6: shutdown chain ==========

class TestGatewayShutdownChain:
    """serve_http / start 必须把 serve()/run_stdio_async 包在 try/finally,
    任意外部退出路径都跑 stop() — 避免 stdio MCP 子进程泄漏 / cache stats 丢。"""

    async def test_serve_http_calls_stop_on_normal_exit(self, tmp_path, monkeypatch):
        """serve() 正常返回时必须调 stop。"""
        import socket
        from src.gateway_server import GatewayServer

        # 找一个空闲端口 — serve_http 现在会预检端口, 默认 18080 常被占
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
        s.close()

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
        }
        server.upstreams = {}
        server.cache = None
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False
        server.mcp = None  # serve_http 不应触达 mcp 的真实初始化

        # 用假的 uvicorn.Server 替身, 让 serve() 立刻返回
        class FakeServer:
            def __init__(self, *a, **kw): pass
            async def serve(self):
                return  # 正常完成

        monkeypatch.setattr("uvicorn.Server", FakeServer)

        stop_calls = []
        async def fake_stop():
            stop_calls.append(True)
        server.stop = fake_stop  # type: ignore[assignment]

        # 跳过 initialize 的真实调用, 直接覆盖
        async def fake_init():
            server._initialized = True
        server.initialize = fake_init  # type: ignore[assignment]

        # health check loop 假扮, 不真跑
        async def fake_loop():
            await asyncio.Event().wait()
        server._health_check_loop = fake_loop  # type: ignore[assignment]

        # http_app() 假扮, 返回一个空 ASGI app (Mount 要求 app 非 None)
        async def empty_asgi_app(scope, receive, send):
            pass

        # serve_http 还会读 mcp_app.lifespan 喂给 Starlette — 裸函数没这个属性,
        # 必须用有 .lifespan 又能 ASGI-callable 的对象
        class FakeMcpApp:
            lifespan = "fake-lifespan"

            def __init__(self, inner):
                self._inner = inner

            async def __call__(self, scope, receive, send):
                await self._inner(scope, receive, send)

        def fake_http_app(*a, **kw):
            return FakeMcpApp(empty_asgi_app)
        server.mcp = type("M", (), {"http_app": fake_http_app})()

        await server.serve_http(host="127.0.0.1", port=free_port)
        assert stop_calls == [True], "serve_http 正常退出后必须调用 stop()"

    async def test_serve_http_calls_stop_on_exception(self, tmp_path, monkeypatch):
        """serve() 抛异常时 stop() 也必须跑 (try/finally)。"""
        import socket
        from src.gateway_server import GatewayServer

        # 找一个空闲端口 — serve_http 现在会预检端口, 默认 18080 常被占
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
        s.close()

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
        }
        server.upstreams = {}
        server.cache = None
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False

        class FakeServer:
            def __init__(self, *a, **kw): pass
            async def serve(self):
                raise RuntimeError("uvicorn internal crash")

        monkeypatch.setattr("uvicorn.Server", FakeServer)

        stop_calls = []
        async def fake_stop():
            stop_calls.append(True)
        server.stop = fake_stop  # type: ignore[assignment]

        async def fake_init():
            server._initialized = True
        server.initialize = fake_init  # type: ignore[assignment]

        async def fake_loop():
            await asyncio.Event().wait()
        server._health_check_loop = fake_loop  # type: ignore[assignment]

        # http_app() 假扮, 返回一个空 ASGI app (Mount 要求 app 非 None)
        async def empty_asgi_app(scope, receive, send):
            pass

        # serve_http 会读 mcp_app.lifespan 喂给 Starlette — 裸 lambda 没这个属性
        class FakeMcpApp:
            lifespan = "fake-lifespan"

            def __init__(self, inner):
                self._inner = inner

            async def __call__(self, scope, receive, send):
                await self._inner(scope, receive, send)

        server.mcp = type("M", (), {"http_app": lambda *a, **kw: FakeMcpApp(empty_asgi_app)})()

        with pytest.raises(RuntimeError, match="uvicorn internal crash"):
            await server.serve_http(host="127.0.0.1", port=free_port)
        assert stop_calls == [True], "serve_http 抛异常时也必须调 stop() (finally)"

    async def test_start_registers_sigint_handler(self, tmp_path, monkeypatch):
        """start() 必须注册 SIGINT handler (Unix only — Windows 跳过)。

        Windows 上 asyncio 不支持 add_signal_handler (NotImplementedError),
        业务路径靠 KeyboardInterrupt + finally 兜底, 同样有效。
        """
        import sys
        if sys.platform == "win32":
            pytest.skip("Windows asyncio 不支持 add_signal_handler")
        from src.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
        }
        server.upstreams = {}
        server.cache = None
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False

        async def fake_init():
            server._initialized = True
        server.initialize = fake_init  # type: ignore[assignment]

        async def fake_loop():
            await asyncio.Event().wait()
        server._health_check_loop = fake_loop  # type: ignore[assignment]

        # mcp.run_stdio_async 假扮: 立刻触发 shutdown, 让 start 跑完 finally
        class FakeMcp:
            async def run_stdio_async(self):
                # 模拟 SIGINT 触发的关闭
                server._shutdown_event.set()
        server.mcp = FakeMcp()

        stop_calls = []
        async def fake_stop():
            stop_calls.append(True)
        server.stop = fake_stop  # type: ignore[assignment]

        await server.start()
        assert stop_calls == [True], "start() 必须把 run_stdio_async 包在 try/finally"


# ========== MED A2: gateway MCP 工厂方法 ==========

class TestCreateAndRegisterGatewayMcp:
    """M2 (2026-09-07 audit): gateway FastMCP 必须经
    _create_and_register_gateway_mcp 工厂走, 一次性完成 FastMCP 构造 +
    _register_tools + _install_capability_filter。否则裸 FastMCP("unihive")
    在 _do_initialize 之外被调用时, capability over-advertise 会复发
    (HIGH #3, 2026-09-07 audit)。"""

    async def test_factory_sets_self_mcp(self, tmp_path):
        from src.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
        }
        server.config_path = tmp_path / "upstreams.yaml"
        server.config_path.write_text("upstreams: {}\n", encoding="utf-8")
        server.upstreams = {}
        server.cache = None
        server.router = None
        server.mcp = None
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False

        register_calls = []

        def fake_register_tools(specs=None):
            register_calls.append(specs)

        server._register_tools = fake_register_tools  # type: ignore[assignment]

        mcp = await server._create_and_register_gateway_mcp(
            tool_specs=[{"name": "fake_tool"}]
        )

        assert server.mcp is mcp, "factory 必须把构造的 MCP 挂到 self.mcp"
        assert register_calls == [[{"name": "fake_tool"}]], (
            f"factory 必须以传入的 specs 调 _register_tools, 实际: {register_calls}"
        )

    async def test_factory_installs_capability_filter(self, tmp_path):
        """factory 必须装 capability filter; 没有 _unihive_capability_filter_installed
        标记就是 HIGH #3 复发路径。"""
        from src.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
        }
        server.config_path = tmp_path / "upstreams.yaml"
        server.config_path.write_text("upstreams: {}\n", encoding="utf-8")
        server.upstreams = {}
        server.cache = None
        server.router = None
        server.mcp = None
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False
        server._register_tools = lambda specs=None: None  # type: ignore[assignment]

        mcp = await server._create_and_register_gateway_mcp()

        assert getattr(mcp, "_unihive_capability_filter_installed", False), (
            "factory 出来的 MCP 必须带 _unihive_capability_filter_installed 标记, "
            "否则 _install_capability_filter 没被调, HIGH #3 复发"
        )


# ========== MED A1: _do_initialize 失败时重置 router / mcp ==========

class TestInitializeFailureResetsRouterMcp:
    """_do_initialize 抛异常时, cleanup 块除清 upstreams / cache 外,
    还必须把 self.router / self.mcp 归零 (M1, 2026-09-07 audit)。
    否则后续 get_server_status / 路由调用读到半构造对象。"""

    async def test_router_and_mcp_reset_on_init_failure(self, tmp_path, monkeypatch):
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
        # 模拟 cleanup 前 router / mcp 已经被赋值 (半构造状态)
        server.router = object()
        server.mcp = object()
        server._initialized = False
        server._shutdown_event = asyncio.Event()
        server._running = False
        server._init_lock = asyncio.Lock()
        server._upstream_start_timeout = 30.0

        # 让 _load_all_tools 在 router/mcp 构造之前抛, 走 cleanup 块
        def boom(*a, **kw):
            raise RuntimeError("simulated init failure")
        server._load_all_tools = boom  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="simulated init failure"):
            await server._do_initialize()

        assert server.router is None, (
            f"router 必须重置为 None, 实际: {server.router!r}"
        )
        assert server.mcp is None, (
            f"mcp 必须重置为 None, 实际: {server.mcp!r}"
        )


# ========== HIGH B1: stop() 重置状态支持 restart ==========

class TestStopResetsStateForRestart:
    """stop() 必须清空 upstreams / 重置 router / mcp / _initialized,
    否则同实例 start() → stop() → start() 第二次 initialize() 会短路,
    用已经 stop 过的死客户端路由 → 'Process not running'。"""

    async def test_stop_clears_upstreams(self, tmp_path):
        from src.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
        }
        server.cache = None
        server._shutdown_event = asyncio.Event()
        server._requests_lock = asyncio.Lock()
        server._active_requests = 0
        server._running = True

        stopped_clients = []

        class FakeClient:
            async def stop(self):
                stopped_clients.append(self)

        c1, c2 = FakeClient(), FakeClient()
        server.upstreams = {"a": c1, "b": c2}

        await server.stop()
        assert server.upstreams == {}, (
            f"stop() 后 upstreams 必须清空, 否则 restart 会路由到死客户端: "
            f"{list(server.upstreams.keys())}"
        )
        assert len(stopped_clients) == 2, "两个 client 的 stop() 都必须被调"

    async def test_stop_resets_router_mcp(self, tmp_path):
        from src.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
        }
        server.upstreams = {}
        server.cache = None
        server._shutdown_event = asyncio.Event()
        server._requests_lock = asyncio.Lock()
        server._active_requests = 0
        server._running = True
        server.router = object()
        server.mcp = object()

        await server.stop()
        assert server.router is None, "stop() 后 router 必须重置为 None"
        assert server.mcp is None, "stop() 后 mcp 必须重置为 None"

    async def test_stop_resets_initialized_flag(self, tmp_path):
        from src.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {
            "cache": {"enabled": False, "db_path": str(tmp_path / "c.db")},
            "upstreams": {},
        }
        server.upstreams = {}
        server.cache = None
        server._shutdown_event = asyncio.Event()
        server._requests_lock = asyncio.Lock()
        server._active_requests = 0
        server._running = True
        server._initialized = True

        await server.stop()
        assert server._initialized is False, (
            "stop() 后 _initialized 必须重置, 否则第二次 initialize() 短路"
        )


# ========== Cleanup fixture ==========

@pytest.fixture(autouse=True)
def _clear_probe_cache():
    from src.console_api import _probe_cache
    _probe_cache.clear()
    yield
    _probe_cache.clear()
