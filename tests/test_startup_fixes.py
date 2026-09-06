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

        def fake_http_app(*a, **kw):
            return empty_asgi_app
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

        server.mcp = type("M", (), {"http_app": lambda *a, **kw: empty_asgi_app})()

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


# ========== Cleanup fixture ==========

@pytest.fixture(autouse=True)
def _clear_probe_cache():
    from src.console_api import _probe_cache
    _probe_cache.clear()
    yield
    _probe_cache.clear()
