"""Round-13 console 启动审视 — 安全根 (C1/H1/M1/M3)。

- C1/H1: 出厂 config/config.yaml 不得携带可用的固定 session_secret /
  口令哈希 (公开仓库 → 任何人可离线伪造 cookie / 用默认口令登录)。
- M1: 显式但不完整的 console 配置 fail-closed 拒启, 不再静默裸奔;
  已知出厂默认凭证即使被显式配置也拒绝。
- M3: 鉴权关闭态必须注册 /api/auth/me 并返回 auth_required=false,
  否则 Vue 守卫拿到 404 会永久卡登录页 (而 /api/* 反而全裸)。
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import yaml
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from src.unihive.console_auth import (
    ConsoleAuthConfigError,
    api_me_open,
    hash_password,
    setup_console_auth,
)
from src.unihive.gateway_server import HttpServeMixin
from src.unihive.gateway_server._helpers import _get_console_auth_config

ROOT = Path(__file__).resolve().parent.parent

_KNOWN_SECRET = "0123456789abcdef" * 4
_KNOWN_ADMIN123_HASH = (
    "pbkdf2_sha256$200000$d506b2bd1306c1e8f4d3d228a3313022$"
    "41d50a4c0592c0a8e4eebdf9afdd99e2cda48a743a87095272cf1cc89b349445"
)


# ---------------------------------------------------------------------------
# C1/H1: 出厂模板不带固定凭证
# ---------------------------------------------------------------------------


class TestFactoryTemplateClean:
    def test_config_yaml_has_no_active_console_credentials(self):
        text = (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
        cfg = yaml.safe_load(text)
        console = (cfg or {}).get("console")
        assert not console, (
            "出厂 config.yaml 不得启用 console 段 (固定凭证随公开仓库发布);"
            "应只保留注释模板, 真实凭证走 .env / UNIHIVE_CONSOLE_*"
        )

    def test_config_yaml_does_not_ship_known_constants(self):
        text = (ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
        # 注释里可以说明, 但真实常量串不得出现在文件中
        assert _KNOWN_SECRET not in text
        assert _KNOWN_ADMIN123_HASH not in text

    def test_env_example_documents_opt_in(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        assert "UNIHIVE_CONSOLE_USER" in text
        assert "UNIHIVE_CONSOLE_PASSWORD" in text
        assert "UNIHIVE_CONSOLE_SESSION_SECRET" in text


# ---------------------------------------------------------------------------
# M1: yaml 半配置 / 已知默认凭证 fail-closed
# ---------------------------------------------------------------------------


class TestYamlConsoleConfigFailClosed:
    def test_partial_yaml_console_block_raises(self):
        config = {"console": {"username": "admin"}}  # 缺 hash + secret
        with pytest.raises(ConsoleAuthConfigError):
            _get_console_auth_config(config)

    def test_known_default_hash_rejected(self):
        config = {
            "console": {
                "username": "admin",
                "password_hash": _KNOWN_ADMIN123_HASH,
                "session_secret": "x" * 64,
            }
        }
        with pytest.raises(ConsoleAuthConfigError, match="default|known|public"):
            _get_console_auth_config(config)

    def test_known_default_secret_rejected(self):
        config = {
            "console": {
                "username": "admin",
                "password_hash": _KNOWN_ADMIN123_HASH,
                "session_secret": _KNOWN_SECRET,
            }
        }
        with pytest.raises(ConsoleAuthConfigError):
            _get_console_auth_config(config)

    def test_explicit_fresh_credentials_accepted(self):
        from src.unihive.console_auth import hash_password

        fresh = hash_password("a-distinct-password-9f3k")
        cfg, reason = _get_console_auth_config(
            {"console": {
                "username": "ops",
                "password_hash": fresh,
                "session_secret": "z" * 40,
            }}
        )
        assert reason == "ok"
        assert cfg["username"] == "ops"

    def test_no_console_anywhere_is_disabled(self, monkeypatch):
        for var in (
            "UNIHIVE_CONSOLE_USER",
            "UNIHIVE_CONSOLE_PASSWORD",
            "UNIHIVE_CONSOLE_SESSION_SECRET",
        ):
            monkeypatch.delenv(var, raising=False)
        cfg, reason = _get_console_auth_config({})
        assert cfg is None
        assert "disabled" in reason


class TestSetupRejectsKnownDefaults:
    """setup_console_auth 兜底拒绝公开出厂凭证 (不经 YAML/env 解析层的
    直接调用路径也要挡住)。"""

    def test_setup_rejects_known_default_secret(self):
        app = Starlette()
        with pytest.raises(ConsoleAuthConfigError, match="default|known|public"):
            setup_console_auth(
                app,
                {
                    "username": "admin",
                    "password_hash": hash_password("anything-fresh-7q"),
                    "session_secret": _KNOWN_SECRET,
                },
            )

    def test_setup_rejects_known_default_hash(self):
        app = Starlette()
        with pytest.raises(ConsoleAuthConfigError, match="default|known|public"):
            setup_console_auth(
                app,
                {
                    "username": "admin",
                    "password_hash": _KNOWN_ADMIN123_HASH,
                    "session_secret": "z" * 48,
                },
            )


# ---------------------------------------------------------------------------
# M3: 鉴权关闭态 /api/auth/me 可用, 前端不卡登录页
# ---------------------------------------------------------------------------


class TestOpenModeMeEndpoint:
    def test_open_mode_me_reports_auth_not_required(self):
        app = Starlette(routes=[Route("/api/auth/me", api_me_open, methods=["GET"])])
        with TestClient(app) as client:
            resp = client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json() == {"username": None, "auth_required": False}

    def test_serve_http_registers_me_in_disabled_branch(self):
        """装配源码必须在鉴权关闭分支也挂 /api/auth/me。"""
        src = inspect.getsource(HttpServeMixin.serve_http)
        # 锚定 auth 装配的 else 分支 (到 canonicalizer 装配为止),
        # 不允许把 open-mode me 错挂进 enabled 分支或方法尾部。
        else_idx = src.index("else:")
        branch_end = src.index("make_mcp_path_canonicalizer", else_idx)
        disabled_branch = src[else_idx:branch_end]
        assert "api_me_open" in disabled_branch
        assert 'Route("/api/auth/me"' in disabled_branch

    def test_enabled_mode_me_reports_required(self):
        """启用态: setup 注册的 /api/auth/me 载荷含 auth_required=true。

        用已登录 cookie 实跑一遍, 断言返回字段 (而不只是路由存在)。
        """
        from src.unihive.console_auth import hash_password

        app = Starlette()
        setup_console_auth(
            app,
            {
                "username": "ops",
                "password_hash": hash_password("pw-9f3k"),
                "session_secret": "x" * 40,
            },
        )
        with TestClient(app) as client:
            login = client.post(
                "/api/auth/login",
                json={"username": "ops", "password": "pw-9f3k"},
            )
            assert login.status_code == 200
            me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json() == {"username": "ops", "auth_required": True}
