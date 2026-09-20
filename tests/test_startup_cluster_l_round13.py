"""Round-13 簇L — 后端 HTTP 装配硬化。

M2  四个 console API handler 在事件循环线程里跑同步阻塞调用
    (get_upstream_status 的探针 fut.result ~6s / get_health socket 连接 /
    load_config sqlite / get_interfaces YAML), 远程上游不可达时整个 loop
    停顿 —— 必须 asyncio.to_thread 下放 worker 线程。
B2  gateway.mcp_path 未校验: 配成 "/" 会吞掉全部路由, 配成 "/api" 会让
    Mount 影子化所有控制台 API。装配期 fail-fast (ValueError → main 退 2)。
B3  console.html 缺失时首页静默 404, 启动预检给出可操作的 build 提示。
B1  console.html 必须 no-cache (新版发布后旧 HTML 引用的 hash chunk 已被
    finalize-build 删除 → 白屏); /static/assets/* 内容指纹文件 immutable。
"""
from __future__ import annotations

import asyncio
import inspect
import threading
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from src.unihive.gateway_server import HttpServeMixin
from src.unihive.gateway_server.http import (
    ImmutableHashedAssets,
    _blocking_json_api,
    _console_index_response,
    _preflight_console_html,
    validate_mcp_path,
)
from src.unihive.utils import console_api as _ca

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# B2: mcp_path 装配期校验
# ---------------------------------------------------------------------------


class TestValidateMcpPath:
    @pytest.mark.parametrize("raw", ["/mcp", "/api/mcp", "/rpc/v1", "/mcp/"])
    def test_accepts_well_formed_paths(self, raw):
        out = validate_mcp_path(raw)
        assert out.startswith("/")
        assert not out.endswith("/")
        assert out != "/"

    def test_default_mcp_path_unchanged(self):
        assert validate_mcp_path("/mcp") == "/mcp"

    def test_deep_path_under_api_allowed(self):
        # M10 (round 9) 明确支持 /api/mcp; 它只 shadow 该前缀自身,
        # 不影响 /api/status 等兄弟路由, middleware 另有动态豁免。
        assert validate_mcp_path("/api/mcp") == "/api/mcp"

    @pytest.mark.parametrize("bad", ["/", "/api", "/static", "/health", "/login", "/logout"])
    def test_rejects_reserved_roots(self, bad):
        with pytest.raises(ValueError, match="mcp_path"):
            validate_mcp_path(bad)

    @pytest.mark.parametrize("bad", ["mcp", "", "  ", None, 18080])
    def test_rejects_missing_leading_slash_or_non_string(self, bad):
        with pytest.raises(ValueError, match="mcp_path"):
            validate_mcp_path(bad)

    def test_rejects_double_slash(self):
        with pytest.raises(ValueError, match="mcp_path"):
            validate_mcp_path("//mcp")


# ---------------------------------------------------------------------------
# B3: console.html 预检
# ---------------------------------------------------------------------------


class TestConsoleHtmlPreflight:
    def test_repo_console_html_present_passes(self):
        # 开发树: finalize-build 已把 dist/index.html 拷到根 console.html
        _preflight_console_html(_ca.CONSOLE_HTML_PATH)

    def test_missing_file_raises_with_build_hint(self, tmp_path):
        missing = tmp_path / "nope" / "console.html"
        with pytest.raises(FileNotFoundError, match="npm run build"):
            _preflight_console_html(missing)


# ---------------------------------------------------------------------------
# B1: 缓存头
# ---------------------------------------------------------------------------


class TestCacheHeaders:
    def test_index_html_is_no_cache(self):
        resp = _console_index_response(b"<!doctype html><html></html>")
        assert resp.media_type == "text/html"
        assert resp.headers["cache-control"].lower() == "no-cache"

    def test_hashed_assets_immutable_others_not(self, tmp_path):
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "index-abc123.js").write_text("/* hash chunk */", encoding="utf-8")
        (tmp_path / "login.css").write_text("/* non-hashed */", encoding="utf-8")

        app = Starlette(
            routes=[
                Mount(
                    "/static",
                    app=ImmutableHashedAssets(
                        StaticFiles(directory=str(tmp_path), html=False)
                    ),
                )
            ]
        )
        with TestClient(app) as client:
            hashed = client.get("/static/assets/index-abc123.js")
            unhashed = client.get("/static/login.css")

        assert hashed.status_code == 200
        assert "immutable" in hashed.headers["cache-control"]
        assert "max-age=31536000" in hashed.headers["cache-control"]
        # 非指纹文件不放大缓存 (避免 login.css 改版被长期缓存)
        cc = unhashed.headers.get("cache-control", "")
        assert "immutable" not in cc


# ---------------------------------------------------------------------------
# M2: 阻塞调用必须下放到 worker 线程
# ---------------------------------------------------------------------------


class TestBlockingHandlersOffloaded:
    def test_handler_runs_sync_fn_off_event_loop_thread(self):
        """_blocking_json_api 包出来的 handler 必须在 worker 线程执行同步函数,
        且同步函数阻塞期间事件循环仍可调度 (旧实现直接调用会卡死 ~6s 探针)。

        确定性证明 (不靠墙钟竞速): worker 进入同步函数后等一个 event,
        由事件循环侧在另一个 task 里 set 它 —— 若同步函数占着 loop 线程,
        循环侧的 set 永远不会发生, worker 等到超时即失败。
        """
        entered = threading.Event()
        can_leave = threading.Event()
        worker_thread = None
        loop_thread = None

        def blocking_sync():
            nonlocal worker_thread
            worker_thread = threading.get_ident()
            entered.set()
            if not can_leave.wait(timeout=2.0):
                raise RuntimeError(
                    "sync fn ran on the event loop thread (never released)"
                )
            return {"ok": True}

        handler = _blocking_json_api(blocking_sync)

        async def scenario():
            nonlocal loop_thread
            loop_thread = threading.get_ident()
            handler_task = asyncio.create_task(handler(None))
            while not entered.is_set():
                await asyncio.sleep(0.005)
            # worker 此刻正阻塞在同步函数里; 下面的 sleep 必须照常完成,
            # 且 set 必须能送达 worker。
            await asyncio.sleep(0.01)
            can_leave.set()
            return await handler_task

        result = asyncio.run(asyncio.wait_for(scenario(), timeout=5.0))
        assert result.status_code == 200
        assert worker_thread is not None
        assert worker_thread != loop_thread

    @pytest.mark.parametrize(
        "fn_name",
        [
            "get_upstream_status",
            "get_interfaces",
            "load_config",
            "get_health",
        ],
    )
    def test_serve_http_wires_each_blocking_handler(self, fn_name):
        src = inspect.getsource(HttpServeMixin.serve_http)
        assert f"_blocking_json_api(_ca.{fn_name})" in src, (
            f"/api handler for {fn_name} must be wrapped in _blocking_json_api"
        )

    def test_serve_http_wires_validation_preflight_and_headers(self):
        src = inspect.getsource(HttpServeMixin.serve_http)
        assert "validate_mcp_path(" in src
        assert "_preflight_console_html(" in src
        assert "_console_index_response(" in src
        assert "ImmutableHashedAssets(" in src
        # source_map 构建含 YAML 读盘, 同样不得占 loop
        assert "asyncio.to_thread(_ca._build_source_map)" in src
