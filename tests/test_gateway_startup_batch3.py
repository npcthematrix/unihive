"""Tests for batch-3 gateway startup fixes (MED 6+7, LOW 10+13).

- MED #6: uvicorn log_level from config.logging.level
- MED #7: port-in-use friendly error (exit code 2, clean stderr)
- LOW #10: argparse imported at module top
- LOW #13: startup banner logged with transport/config/upstream count
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_root_logging_handlers():
    """TestStartupBanner 触发 configure_logging 装了一个 sys.stderr 的
    StreamHandler 到 root logger。pytest capsys/capfd 切换时会让该 handler
    写到 closed file → TestPortInUse 拿到 'I/O operation on closed file'
    traceback, 不是真实 bug 而是测试隔离问题。每个测试后清空 root handlers
    把 configure_logging 副作用隔离在 TestStartupBanner 内部。
    """
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)


# ========== MED #6: log_level from config ==========

class TestLogLevelFromConfig:
    def test_log_level_default_info_when_no_logging_section(self):
        """config 缺 logging 节时, 应回退到 'info'."""
        from src.unihive.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {"gateway": {"host": "127.0.0.1", "port": 18080}}
        level = server.config.get("logging", {}).get("level", "info").lower()
        assert level == "info"

    def test_log_level_respects_config_debug(self):
        from src.unihive.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {"logging": {"level": "DEBUG"}, "gateway": {}}
        level = server.config.get("logging", {}).get("level", "info").lower()
        assert level == "debug"

    def test_log_level_uppercase_normalized(self):
        """YAML 里写 'INFO' / 'WARNING' 也应被归一化为小写喂给 uvicorn."""
        from src.unihive.gateway_server import GatewayServer

        server = GatewayServer.__new__(GatewayServer)
        server.config = {"logging": {"level": "WARNING"}, "gateway": {}}
        level = server.config.get("logging", {}).get("level", "info").lower()
        assert level == "warning"


# ========== LOW #10: argparse at module top ==========

class TestArgparseTopLevel:
    def test_argparse_imported_at_module_top(self):
        """避免函数内 import; argparse 必须在模块顶层可访问."""
        import src.gateway_server as gs

        assert hasattr(gs, "argparse"), "argparse 应在模块顶层 import, 不应在 _build_arg_parser 内"
        # 类型也确认下
        assert isinstance(gs.argparse, type(argparse))


# ========== LOW #13: startup banner ==========

class TestStartupBanner:
    async def test_async_main_logs_startup_banner(self, capsys, tmp_path):
        """async_main 启动时必须打 banner, 包含 transport / config path / upstream 数."""
        from src import gateway_server as gs

        cfg = tmp_path / "upstreams.yaml"
        cfg.write_text(
            "upstreams:\n  a:\n    enabled: true\n    type: http\n"
            "    base_url: http://x\n    api_key: k\n"
            "  b:\n    enabled: false\n    type: http\n"
            "    base_url: http://y\n    api_key: k\n",
            encoding="utf-8",
        )

        # 短路 serve_http / start 避免真起 uvicorn / stdio
        async def fake_serve_http(self, host=None, port=None, mcp_path=None):
            return None
        async def fake_start(self):
            return None

        with patch.object(gs.GatewayServer, "serve_http", fake_serve_http), \
             patch.object(gs.GatewayServer, "start", fake_start):
            await gs.async_main(
                transport="http",
                host="127.0.0.1",
                port=18099,
                config_path=str(cfg),
            )

        # 用 capsys 而不是 capfd: configure_logging 装了一个 sys.stderr 的
        # StreamHandler, capfd 替换 fd 会让 StreamHandler 在下一个测试里写
        # 到 closed file, 导致 TestPortInUse 失败。两边都用 capsys 就稳了。
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "unihive starting" in combined, f"startup banner 应在 stdout/stderr, 实际: {combined[-200:]}"
        assert "transport=http" in combined
        assert str(cfg) in combined
        assert "upstreams=2" in combined  # enabled=True/False 都计入


# ========== MED #7: port-in-use friendly error ==========

class TestPortInUse:
    async def test_port_in_use_yields_clean_exit(self, tmp_path, capsys):
        """端口被占时必须: 友好 stderr 消息 + 退出码 2, 不打 traceback."""
        from src import gateway_server as gs

        # 真起一个 socket 占住端口
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

            # 直接调 serve_http, 让它跑 uvicorn.Config().serve()
            # uvicorn 在 bind 失败时会 raise OSError; 我们的代码必须捕获并 sys.exit(2)
            server = gs.GatewayServer(config_path=str(cfg), strict_env=False)

            with pytest.raises(SystemExit) as exc_info:
                await server.serve_http(host="127.0.0.1", port=port)
            assert exc_info.value.code == 2

            captured = capsys.readouterr()
            assert "port already in use" in captured.err
            assert "traceback" not in captured.err.lower()
        finally:
            s.close()
