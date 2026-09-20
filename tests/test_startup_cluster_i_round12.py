"""Round-12 簇I: 入口杂项 LOW 收口。

1. stdio 编码: async_main 里解释器启动**之后**才 os.environ 设
   PYTHONIOENCODING —— 对当前进程的 stdio 编码毫无作用 (只影响子进程),
   中文 Windows (cp936) 上 MCP JSON-RPC 帧里的中文会炸。改为启动前在
   ps1 设环境变量 (对子进程生效) + 进程内 sys.stdout/stdin.reconfigure
   (对 console script / 直接 -m 启动生效, 覆盖不经 ps1 的 MCP 宿主)。
2. _read_gateway_version: importlib.metadata 优先 (pip install 后的规范
   来源), pyproject 兜底的 regex 锚定 [project] 表 (旧实现在全文匹配首个
   version=, 若未来某 [tool.*] 表先出现 version 键会读错版本)。
3. stdio transport 显式传 host/port 以前被静默忽略, 给 warning。
4. 网关启动 wait_for 超时: 无 late-recovery 钩子的 client (thsdk/fuyao/
   omni/stdio) start() 被 cancel 时内部 except Exception 接不住
   CancelledError, 状态永远停在初始 UNKNOWN; 超时而又无后台自愈时诚实
   落 UNAVAILABLE (有钩子的 mootdx2/tdx_quant 保留 UNKNOWN 等自愈)。
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

import pytest

import src.unihive.gateway_server as gs
from src.unihive.api.upstream_client import UpstreamStatus
from src.unihive.gateway_server import _helpers

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


# ---------------------------------------------------------------------------
# 1. stdio utf-8
# ---------------------------------------------------------------------------


class _FakeStream:
    def __init__(self):
        self.calls: list[dict] = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


class TestStdioUtf8:
    def test_reconfigures_stdout_and_stdin(self, monkeypatch):
        out, inp = _FakeStream(), _FakeStream()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stdin", inp)
        gs._configure_stdio_utf8()
        assert out.calls[0].get("encoding") == "utf-8"
        assert inp.calls[0].get("encoding") == "utf-8"

    def test_stream_without_reconfigure_is_tolerated(self, monkeypatch):
        # io.StringIO 没有 reconfigure; 某些宿主 (frozen exe) 也可能替换流
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        monkeypatch.setattr(sys, "stdin", io.StringIO())
        gs._configure_stdio_utf8()  # 不得抛

    @pytest.mark.asyncio
    async def test_async_main_stdio_reconfigures_and_does_not_set_env_late(
        self, monkeypatch
    ):
        called = {"n": 0}

        def _fake_configure():
            called["n"] += 1

        monkeypatch.setattr(gs, "_configure_stdio_utf8", _fake_configure)
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)

        class _FakeServer:
            config = {"upstreams": {}}

            def __init__(self, *args, **kwargs):
                pass

            async def start(self):
                return None

        monkeypatch.setattr(gs, "GatewayServer", _FakeServer)
        await gs.async_main(transport="stdio", config_path="x.yaml")
        assert called["n"] == 1
        # 解释器启动后的 env 设置对本进程无效, 不得再假装它有用
        assert "PYTHONIOENCODING" not in os.environ

    @pytest.mark.asyncio
    async def test_async_main_stdio_warns_when_host_port_given(
        self, monkeypatch, caplog
    ):
        monkeypatch.setattr(gs, "_configure_stdio_utf8", lambda: None)
        # configure_logging 会清空 root handlers (stdio 下只挂文件 handler),
        # pytest caplog 的捕获 handler 也会被摘掉 → 这里 no-op 保住捕获。
        monkeypatch.setattr(gs, "configure_logging", lambda *a, **k: None)

        class _FakeServer:
            config = {"upstreams": {}}

            def __init__(self, *args, **kwargs):
                pass

            async def start(self):
                return None

        monkeypatch.setattr(gs, "GatewayServer", _FakeServer)
        with caplog.at_level("WARNING", logger="src.unihive.gateway_server"):
            await gs.async_main(
                transport="stdio",
                host="0.0.0.0",
                port=9999,
                config_path="x.yaml",
            )
        assert "stdio" in caplog.text.lower()
        assert "0.0.0.0" in caplog.text
        assert "9999" in caplog.text

    @pytest.mark.asyncio
    async def test_async_main_http_still_passes_host_port(self, monkeypatch):
        seen = {}

        class _FakeServer:
            config = {"upstreams": {}}

            def __init__(self, *args, **kwargs):
                pass

            async def serve_http(self, host=None, port=None, mcp_path=None):
                seen["host"] = host
                seen["port"] = port

        monkeypatch.setattr(gs, "GatewayServer", _FakeServer)
        await gs.async_main(
            transport="http", host="127.0.0.1", port=18099, config_path="x.yaml"
        )
        assert seen == {"host": "127.0.0.1", "port": 18099}


def test_ps1_sets_pythonioencoding_before_python_launch():
    text = (SCRIPTS_DIR / "start_gateway.ps1").read_text(encoding="utf-8-sig")
    assert "$env:PYTHONIOENCODING" in text
    # 必须在任何 python 子进程启动之前设置, 否则对子进程也不生效
    env_pos = text.index("$env:PYTHONIOENCODING")
    first_python = text.index("python -m src.unihive.gateway_server")
    assert env_pos < first_python


# ---------------------------------------------------------------------------
# 2. version reader
# ---------------------------------------------------------------------------


class TestVersionReader:
    def test_metadata_wins(self, monkeypatch):
        import importlib.metadata as im

        monkeypatch.setattr(im, "version", lambda dist: "9.9.9")
        assert _helpers._read_gateway_version() == "9.9.9"

    def test_project_section_anchor_picks_project_version(self):
        text = (
            '[tool.bumpversion]\n'
            'version = "7.7.7"\n\n'
            '[project]\n'
            'name = "unihive"\n'
            'version = "0.1.1"\n'
        )
        assert _helpers._version_from_pyproject_text(text) == "0.1.1"

    def test_version_only_in_other_table_returns_none(self):
        assert _helpers._version_from_pyproject_text(
            '[tool.poetry]\nversion = "1.0.0"\n'
        ) is None

    def test_project_section_without_version_returns_none(self):
        assert _helpers._version_from_pyproject_text(
            '[project]\nname = "unihive"\n'
        ) is None

    def test_fallback_unknown_without_metadata_and_project_version(
        self, monkeypatch, tmp_path
    ):
        import importlib.metadata as im

        def _boom(dist):
            raise im.PackageNotFoundError(dist)

        monkeypatch.setattr(im, "version", _boom)
        monkeypatch.setattr(_helpers, "resolve_layout_root", lambda f: tmp_path)
        (tmp_path / "pyproject.toml").write_text(
            '[tool.x]\nversion = "1.0"\n', encoding="utf-8"
        )
        assert _helpers._read_gateway_version() == "unknown"


# ---------------------------------------------------------------------------
# 4. 启动超时 → 无自愈钩子 client 的 UNKNOWN 落 UNAVAILABLE
# ---------------------------------------------------------------------------


class _SlowNoHookClient:
    """start() 永久挂起、且没有 start_after_timeout 钩子 (thsdk 型)。"""

    def __init__(self):
        self._status = UpstreamStatus.UNKNOWN

    @property
    def status(self):
        return self._status

    @property
    def is_available(self):
        return False

    async def start(self):
        await asyncio.sleep(100)


class _SlowRecoverableClient(_SlowNoHookClient):
    """mootdx2/tdx_quant 型: 有 late-recovery 钩子。"""

    async def start_after_timeout(self):
        return None


class TestStartTimeoutStatusMapping:
    @pytest.mark.asyncio
    async def test_hookless_timeout_marks_unavailable(
        self, gateway_server_minimal
    ):
        server = gateway_server_minimal
        server.upstreams = {}
        server._late_start_tasks = {}
        server._upstream_start_timeout = 0.05
        client = _SlowNoHookClient()
        connected = await server._start_upstream_client(
            "thsdk", {"type": "thsdk"}, client
        )
        assert connected is False
        assert server.upstreams["thsdk"] is client
        assert client.status == UpstreamStatus.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_hook_client_keeps_unknown_and_spawns_recovery(
        self, gateway_server_minimal
    ):
        server = gateway_server_minimal
        server.upstreams = {}
        server._late_start_tasks = {}
        server._upstream_start_timeout = 0.05
        client = _SlowRecoverableClient()
        try:
            connected = await server._start_upstream_client(
                "mootdx2", {"type": "mootdx2"}, client
            )
            assert connected is False
            # 后台自愈还没跑, 状态不得被提前判死
            assert client.status == UpstreamStatus.UNKNOWN
            assert server._late_start_tasks.get("mootdx2")
        finally:
            await server._cancel_late_starts()
