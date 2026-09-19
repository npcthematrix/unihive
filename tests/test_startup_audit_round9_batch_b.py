"""第 9 轮启动逻辑审视 — Batch B: 启动健壮性。

覆盖:
  L3 (tdx_remote_port 保留) / L5 (死 metrics 路由) / L6 (cache busy_timeout +
       init 失败 dispose engine) / L8 (_shutdown_event 复位支持 restart) /
  L9 (probe executor 惰性重建) / M7 (stdio stderr drain 防管道自锁) /
  M11 (start_gateway.ps1 端口源) / H2 (单上游构造失败降级) /
  H4 (env auth 不崩 + 路由装配纳入 stop 保护)。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from starlette.applications import Starlette

from src.unihive.api.upstream_client import UpstreamClient, UpstreamConfig
from src.unihive.gateway_server import GatewayServer, _console_auth_config_from_env
from src.unihive.storage.cache import Cache, CacheConfig
from src.unihive.utils import console_api as ca
from src.unihive.utils.config_loader import validate_config

from tests.test_config_store_v2 import BUNDLE, VERSION
from tests.test_upstream_edit_hot import _make_beta_server
from tests.test_upstream_enabled_toggle import _FakeClient


def _enabled_beta_bundle() -> dict:
    """beta 启用 (http + ${BETA_KEY} 占位), b_two 无 env 门控可发布。"""
    import copy

    bundle = copy.deepcopy(BUNDLE)
    beta = next(u for u in bundle["upstreams"] if u["name"] == "beta")
    beta["enabled"] = True
    beta["config"] = {"base_url": "http://old", "api_key": "${BETA_KEY}"}
    b_two = next(t for t in bundle["tools"] if t["name"] == "b_two")
    b_two["requires_env"] = None
    b_two["spec"] = {"name": "b_two", "routing": "b_two", "params": []}
    return bundle


@pytest.fixture
async def beta_store(tmp_path):
    from src.unihive.storage.config_store import ConfigStore

    store = ConfigStore(str(tmp_path / "config.db"))
    await store.initialize()
    await store.seed_from_bundle(VERSION, _enabled_beta_bundle())
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# L3: TdxQuantSettings 保留 tdx_remote_port
# ---------------------------------------------------------------------------


def test_l3_tdx_quant_construct_preserves_remote_port():
    """gateway_server._construct_upstream_client 构造 tdx_quant 时漏传
    tdx_remote_port → YAML 里配的非默认端口被静默丢弃, 运行时永远连
    默认 17709。修复后该字段从 cfg 透传到 settings。"""
    server = GatewayServer.__new__(GatewayServer)
    cfg = {
        "type": "tdx_quant",
        "tdx_root": "C:/new_tdx",
        "tdx_remote_port": 19999,
        "strategy_id": "sid",
    }
    client = server._construct_upstream_client("tdq", cfg)
    assert client.config.settings.tdx_remote_port == 19999, (
        "tdx_remote_port must be propagated from cfg, not silently dropped to 17709"
    )


# ---------------------------------------------------------------------------
# L5: 删除死 metrics 路由
# ---------------------------------------------------------------------------


def test_l5_dead_metrics_route_removed_from_omni_routes():
    """build_omni_routes 里 ``/api/upstreams/<name>/metrics`` 用 Flask 语法
    ``<name>``, Starlette 1.6 不认 (编译出 params={}, 永远匹配不上) →
    死路由。真正工作的是 gateway_server 里 ``{name}`` 那条。修复后死路由
    从 build_omni_routes 移除 (handler 保留供 gateway_server 挂载)。"""
    paths = [getattr(r, "path", "") for r in ca.build_omni_routes()]
    assert "/api/upstreams/<name>/metrics" not in paths, (
        "dead <name> route must be removed; live {name} route lives in gateway_server"
    )
    # handler 仍可被 gateway_server 导入挂载
    assert hasattr(ca, "upstream_metrics_api")


# ---------------------------------------------------------------------------
# L6: cache.db busy_timeout + init 失败 dispose engine
# ---------------------------------------------------------------------------


async def test_l6_cache_init_failure_disposes_engine(tmp_path, monkeypatch):
    """initialize() 在 engine 已建、CREATE TABLE 失败时, 旧实现 except 只置
    enabled=False, _engine 留挂着 → 同实例重试 initialize() 泄漏第二个
    engine。修复后 except 块 dispose + 置 None。"""

    class _FakeEngine:
        disposed = False

        async def dispose(self):
            self.disposed = True

    fake_engine = _FakeEngine()

    import src.unihive.storage.cache as cache_mod

    monkeypatch.setattr(cache_mod, "create_async_engine", lambda *a, **kw: fake_engine)

    class _BoomSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **kw):
            raise RuntimeError("table create boom")

    monkeypatch.setattr(
        cache_mod, "sessionmaker", lambda *a, **kw: (lambda: _BoomSession())
    )

    cache = Cache(CacheConfig(db_path=str(tmp_path / "c.db"), enabled=True))
    await cache.initialize()
    assert cache.enabled is False
    assert cache._engine is None, "engine must be disposed and reset on init failure"
    assert fake_engine.disposed, "engine.dispose() must be awaited"
    assert cache._session_factory is None


# ---------------------------------------------------------------------------
# L8: _shutdown_event 复位支持 restart
# ---------------------------------------------------------------------------


async def test_l8_stale_shutdown_event_cleared_on_stdio_restart(monkeypatch):
    """stop() 末尾设 _shutdown_event 唤醒 signal watcher, 但从不 clear →
    同实例 stdio restart 时新 watcher `await event.wait()` 立即返回,
    cancel 当前 main_task → boot 自杀, run_stdio_async 根本没跑。
    修复后 start() 在建 watcher 前 clear, 残留 set 不影响重启。"""
    server = GatewayServer.__new__(GatewayServer)
    server._running = False
    server._initialized = True
    server._init_lock = asyncio.Lock()
    server._shutdown_event = asyncio.Event()
    server._shutdown_event.set()  # 模拟上次 stop 留下的残留
    server.upstreams = {}
    server.cache = None
    server.config_store = None
    server._cache_cleanup_task = None
    server.router = None
    server.tool_specs = []
    server._published_tools = set()
    server._db_hidden_tools = set()
    server._upstream_toggle_lock = asyncio.Lock()

    ran: list[str] = []

    class _FakeMCP:
        async def run_stdio_async(self):
            ran.append("start")
            # 让出控制权: 修复前残留 event 让 watcher 在此期间 cancel main_task,
            # sleep 被打断 → "done" 永不追加。修复后 event 已 clear, watcher
            # 挂起, sleep 正常完成。
            await asyncio.sleep(0.05)
            ran.append("done")

    server.mcp = _FakeMCP()  # type: ignore[assignment]

    async def fake_stop(self):
        self._running = False

    monkeypatch.setattr(GatewayServer, "stop", fake_stop)

    try:
        await server.start()
    except asyncio.CancelledError:
        # 修复前: 残留 event 让 watcher 立刻 cancel main_task → start 抛 CancelledError
        pass

    assert "done" in ran, (
        "stdio restart must complete run_stdio_async; a stale _shutdown_event "
        "must not pre-cancel the new watcher mid-run"
    )


# ---------------------------------------------------------------------------
# L9: probe executor 惰性重建
# ---------------------------------------------------------------------------


def test_l9_probe_executor_rebuildable_after_shutdown():
    """stop() 调 _shutdown_probe_executor() 关掉进程级 ThreadPoolExecutor,
    但不置 None → get_upstream_status 仍向已关闭的 executor submit, 抛
    RuntimeError (cannot schedule new futures)。同进程 restart 后状态页
    直接崩。修复后 _shutdown_probe_executor 置 None, 惰性 getter 重建。"""
    # 先确保有一个 executor
    ex0 = ca._get_probe_executor()
    assert ex0 is not None
    # 关掉 (stop() 路径)
    ca._shutdown_probe_executor()
    # 再取必须是**新**建的 executor, 且可 submit
    ex1 = ca._get_probe_executor()
    assert ex1 is not None
    assert ex1 is not ex0, "shut-down executor must be replaced, not reused"
    fut = ex1.submit(lambda: 7)
    assert fut.result(timeout=5) == 7
    # 清理
    ca._shutdown_probe_executor()


# ---------------------------------------------------------------------------
# M7: stdio stderr drain 防管道自锁
# ---------------------------------------------------------------------------


async def test_m7_stdio_stderr_drained_to_prevent_pipe_deadlock(monkeypatch):
    """UpstreamClient.start() 用 stderr=PIPE 但从不 drain → 子进程往 stderr
    写满 64KB 后在 write() 阻塞, 整个上游假死。修复后后台任务 readline
    到 EOF, 管道不积压。"""

    class _FakeStream:
        def __init__(self, lines):
            self.readline = AsyncMock(side_effect=list(lines) + [b""])
            self.read = AsyncMock(return_value=b"")

    class _FakeProc:
        def __init__(self):
            self.returncode = None  # 仍在运行
            self.stderr = _FakeStream([b"[warn] starting up\n"])
            self.stdout = _FakeStream([])
            self.terminated = False

        def terminate(self):
            self.terminated = True

        async def wait(self):
            return 0

    async def fake_create(*a, **kw):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    # start() 里 await asyncio.sleep(0.5) 等进程起来; 用真实 0 延迟但保留
    # yield (AsyncMock 不 yield, 会让 drain 任务永远没机会跑)。先存真 sleep。
    _real_sleep = asyncio.sleep

    async def _fast_sleep(_seconds):
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    cfg = UpstreamConfig(
        name="x", enabled=True, package="", command=["dummy"], env={}
    )
    client = UpstreamClient(cfg)
    ok = await client.start()
    assert ok, "start should report success for a running process"

    # 修复后: 必须存在一个 stderr drain 任务
    drain = getattr(client, "_stderr_drain_task", None)
    assert drain is not None, "a stderr drain task must be created on start"

    # 让 drain 任务跑一轮
    await asyncio.sleep(0.05)
    assert client._process.stderr.readline.await_count >= 1, (
        "stderr must be drained (readline awaited) to avoid 64KB pipe self-lock"
    )
    # 清理 drain 任务
    if not drain.done():
        drain.cancel()
        try:
            await drain
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# M11: start_gateway.ps1 端口源
# ---------------------------------------------------------------------------


def test_m11_start_gateway_ps1_reads_config_yaml_for_port():
    """gateway.host/port/mcp_path 是 config.yaml 的 bootstrap 段 (CLAUDE.md:
    config.yaml 贡献 console + 监听键, 永不入库)。ps1 只读 upstreams.yaml
    → 改 config.yaml 的端口不生效, 静默回落 18080。修复后从 config.yaml
    取端口。"""
    ps1 = (Path(__file__).resolve().parent.parent / "scripts" / "start_gateway.ps1")
    text = ps1.read_text(encoding="utf-8")
    assert "config.yaml" in text, (
        "start_gateway.ps1 must resolve gateway port from config/config.yaml "
        "(the bootstrap holder), not only upstreams.yaml"
    )


# ---------------------------------------------------------------------------
# H2: 单上游构造失败降级不杀 boot
# ---------------------------------------------------------------------------


async def test_h2_construct_failure_degrades_upstream_not_boot(beta_store, monkeypatch):
    """_do_initialize 启动循环里 _construct_upstream_client 未包 try → 单个
    上游构造抛错 (如 mootdx2 servers 非法) 让整个网关拒启。修复后该上游
    跳过 + log, 其余上游照常, 网关正常启动。"""
    import src.unihive.gateway_server as gs_mod

    monkeypatch.setenv("BETA_KEY", "secret-123")
    # 关掉 beta_store, 让 _load_db_config 自己用 _db_path 重开 (与生产同路径)
    db_path = beta_store._db_path if hasattr(beta_store, "_db_path") else str(
        beta_store.db_path
    )
    await beta_store.close()

    server = GatewayServer.__new__(GatewayServer)
    server._db_mode = True
    server._strict_env = False
    server._db_path = db_path
    server.config_path = Path(__file__).resolve().parent.parent / "config" / "upstreams.yaml"
    server.config = {}
    server.upstreams = {}
    server.config_store = None
    server._db_hidden_tools = set()
    server._upstream_toggle_lock = asyncio.Lock()
    server._init_lock = asyncio.Lock()
    server._initialized = False
    server._running = False
    server._cache_cleanup_task = None
    server.cache = None
    server.router = None
    server.mcp = None
    server.tool_specs = []
    server._published_tools = set()
    server._apply_gateway_tuning()

    # 让 beta 构造抛错 (模拟 mootdx2 非法 servers / 任意 client 构造异常)
    def boom_construct(name, cfg):
        raise RuntimeError(f"construct boom for {name}")

    server._construct_upstream_client = boom_construct  # type: ignore[assignment]

    # _start_upstream_client 不会被调到 (construct 先炸), 留个哨兵
    async def fake_start(name, cfg, client):
        raise AssertionError("start must not be reached when construct fails")

    server._start_upstream_client = fake_start  # type: ignore[assignment]

    # 屏蔽 cache (不写 logs/cache.db) + mcp 工厂
    class _FakeCache:
        enabled = True

        async def initialize(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(
        "src.unihive.storage.cache.Cache", lambda *a, **kw: _FakeCache()
    )

    async def fake_mcp(specs):
        return None

    server._create_and_register_gateway_mcp = fake_mcp  # type: ignore[assignment]

    # 不让 _cache_cleanup_loop 真跑 (fake cache 无 cleanup_expired)
    async def fake_cleanup_loop(self):
        return None

    monkeypatch.setattr(GatewayServer, "_cache_cleanup_loop", fake_cleanup_loop)

    # 修复前: construct 抛错 → _do_initialize except → 整个 boot 失败 (raise)
    # 修复后: beta 被跳过, boot 完成
    await server._do_initialize()

    assert server._initialized is True, "boot must complete despite one upstream failing to construct"
    assert "beta" not in server.upstreams, "failed upstream must be skipped, not registered"


# ---------------------------------------------------------------------------
# H4: env auth 不崩 + 路由装配纳入 stop 保护
# ---------------------------------------------------------------------------


async def test_h4_env_console_auth_returns_hash_not_plaintext(monkeypatch):
    """env 鉴权路径 (_console_auth_config_from_env) 把明文 password 直接塞
    进 cfg → setup_console_auth 现在拒绝明文 (要 password_hash) →
    serve_http 在路由装配段崩。修复后 env 路径就地 hash, 不再崩。"""
    monkeypatch.setenv("UNIHIVE_CONSOLE_USER", "admin")
    monkeypatch.setenv("UNIHIVE_CONSOLE_PASSWORD", "secret123")
    monkeypatch.setenv("UNIHIVE_CONSOLE_SESSION_SECRET", "x" * 32)

    cfg, reason = _console_auth_config_from_env()
    assert reason == "ok"
    assert "password_hash" in cfg, "env path must hash plaintext into password_hash"
    assert "password" not in cfg, "env path must not surface plaintext password"
    assert cfg["password_hash"].startswith("pbkdf2_sha256$")

    # setup_console_auth 必须接受 (修复前会 raise ValueError)
    app = Starlette()
    from src.unihive.console_auth import setup_console_auth

    setup_console_auth(app, cfg)  # 不抛即通过
    assert "password" not in app.state.console_auth_config
    assert app.state.console_auth_config["password_hash"].startswith("pbkdf2_sha256$")


async def test_h4_route_assembly_failure_runs_stop(monkeypatch):
    """serve_http 路由装配段 (mcp.http_app / setup_console_auth) 在 probe
    try/finally 之前 → 此段抛错绕过 stop(), 半建 resource 泄漏。修复后
    装配段纳入 try/finally, 崩溃也 stop()。"""
    import src.unihive.gateway_server as gs_mod

    server = GatewayServer.__new__(GatewayServer)
    server.config = {
        "gateway": {"host": "127.0.0.1", "port": 19090, "mcp_path": "/mcp"},
        "logging": {"level": "info"},
    }
    server._running = False
    server._initialized = False
    server.upstreams = {}
    server._cache_cleanup_task = None
    server.cache = None
    server.config_store = None
    server.router = None

    monkeypatch.setattr(GatewayServer, "initialize", AsyncMock())

    class _FakeApp:
        def http_app(self, **kwargs):
            raise RuntimeError("route assembly boom")

    server.mcp = _FakeApp()  # type: ignore[assignment]

    stop_calls: list[bool] = []

    async def fake_stop(self):
        stop_calls.append(True)

    monkeypatch.setattr(GatewayServer, "stop", fake_stop)

    with pytest.raises(RuntimeError, match="route assembly boom"):
        await server.serve_http()

    assert stop_calls, (
        "route-assembly crash must still run stop() to release half-built resources"
    )
