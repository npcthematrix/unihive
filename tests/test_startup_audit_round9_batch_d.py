"""第 9 轮启动逻辑审视 — Batch D: 路径 / 打包 / 姿态。

覆盖 (经用户裁决, H5 + L7 按 Round-8 跳过, L14 已不存在):
  M9 (runtime 状态路径锚定项目根) /
  M10 (PUBLIC_PATHS mcp_path 可配置) /
  L13 (wheel 打包 console 资源 + CONSOLE_HTML_PATH 兼容 installed 布局)。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.unihive.console_auth import ConsoleAuthMiddleware, setup_console_auth, hash_password
from src.unihive.core.config_db import project_root, resolve_project_path

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# M9: runtime 状态路径锚定项目根
# ---------------------------------------------------------------------------


def test_m9_resolve_project_path_relative_anchored_to_root():
    """cache.db / board.db / strategy-id / proxy_settings.yaml 等相对路径
    若按 CWD 解析, 从非 repo 根启动会读写到另一个文件 (split-brain), 而
    config.db 已锚定。修复后统一锚定到 project_root。"""

    p = resolve_project_path("logs/cache.db", "logs/cache.db")
    assert p == project_root() / "logs" / "cache.db"
    assert p.is_absolute(), "anchored path must be absolute (CWD-independent)"


def test_m9_resolve_project_path_absolute_passthrough():
    """绝对路径是逃生通道 (指向库外实例), 原样返回不重锚。"""
    abs_p = Path("/var/data/cache.db") if Path("/var/data").exists() else Path(
        project_root().anchor + "tmp" + ("\\" if "\\" in str(project_root()) else "/") + "x.db"
    )
    # 用一个明确的绝对路径 (跨平台: project_root 所在盘符根下的路径)
    abs_path = Path(project_root().anchor) / "abs" / "cache.db"
    out = resolve_project_path(abs_path, "logs/cache.db")
    assert out == abs_path


def test_m9_resolve_project_path_empty_falls_to_default_anchored():
    """None / 空串 → default, 再按相对锚定 (避免 Path('')='.' 误把项目根目录当文件)。"""
    assert resolve_project_path(None, "logs/cache.db") == project_root() / "logs" / "cache.db"
    assert resolve_project_path("   ", "logs/cache.db") == project_root() / "logs" / "cache.db"


def test_m9_gateway_anchors_runtime_state_paths():
    """gateway_server / tdx_quant 的状态路径消费点改用 resolve_project_path。"""
    gw = (REPO / "src" / "unihive" / "gateway_server.py").read_text(encoding="utf-8")
    assert "resolve_project_path" in gw, (
        "gateway_server must anchor config-driven state paths (cache.db / "
        "board.db / proxy_settings) via resolve_project_path, not CWD-relative"
    )
    tdx = (REPO / "src" / "unihive" / "api" / "tdx_quant_client.py").read_text(encoding="utf-8")
    assert "resolve_project_path" in tdx, (
        "tdx_quant strategy-id file must be anchored to project_root (UUID "
        "churn breaks TdxW.exe continuity when launched from non-root CWD)"
    )


# ---------------------------------------------------------------------------
# M10: PUBLIC_PATHS mcp_path 可配置
# ---------------------------------------------------------------------------


async def test_m10_custom_mcp_path_exempted_from_auth():
    """PUBLIC_PATHS 硬编码 "/mcp" → 配 mcp_path="/api/mcp" 后所有 MCP 调用
    (路径以 /api/ 开头) 吃 401, 上游假死。修复后 middleware 接 mcp_path,
    配置路径免鉴权。"""

    reached: list[str] = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])

    async def send(message):
        pass

    async def receive():
        return {}

    mw = ConsoleAuthMiddleware(app=inner, mcp_path="/api/mcp")
    # /api/mcp 无 session → 修复后免鉴权 (是配置的 mcp path), 直达 inner
    await mw({"type": "http", "path": "/api/mcp", "session": {}}, receive, send)
    assert reached, (
        "configured mcp_path must be exempt from the /api/ auth gate even when "
        "it lives under /api/ — otherwise every MCP JSON-RPC call returns 401"
    )


async def test_m10_default_mcp_path_still_exempted():
    """默认 mcp_path="/mcp" 行为不变 (回归)。"""

    reached: list[str] = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])

    async def send(message):
        pass

    async def receive():
        return {}

    mw = ConsoleAuthMiddleware(app=inner)  # 默认 mcp_path
    await mw({"type": "http", "path": "/mcp", "session": {}}, receive, send)
    assert reached, "default mcp_path /mcp must remain exempt"


async def test_m10_setup_console_auth_threads_mcp_path():
    """setup_console_auth 把 mcp_path 传给 middleware。用一个非 /api/ 下的
    路径证明 mcp_path 被注入 (否则 /custom/mcp 会因非 /api/ 直接 passthrough,
    测不出)。改用 /api/mcp 这种受 /api/ 鉴权门控的路径。"""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.testclient import TestClient

    app = Starlette()
    app.add_route("/api/mcp", lambda req: PlainTextResponse("mcp"))
    cfg = {
        "username": "admin",
        "password_hash": hash_password("secret"),
        "session_secret": "x" * 32,
    }
    setup_console_auth(app, cfg, mcp_path="/api/mcp")
    with TestClient(app) as client:
        r = client.get("/api/mcp")  # 无 session
    assert r.status_code != 401, (
        "setup_console_auth must thread mcp_path into the middleware so the "
        "configured MCP path is exempt even under /api/"
    )


# ---------------------------------------------------------------------------
# L13: wheel 打包 console 资源 + CONSOLE_HTML_PATH 兼容 installed 布局
# ---------------------------------------------------------------------------


def test_l13_wheel_includes_console_assets():
    """pyproject.toml hatchling 只打 src/, console.html + static/console 在
    repo 根 (包外) → pip install 装出的包 /console.html 与 /static 404。
    修复后 wheel force-include 这两类资源。"""

    toml = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "console.html" in toml, (
        "wheel must force-include console.html so installed packages serve the console"
    )
    assert "static" in toml, "wheel must force-include static/console assets"
    assert "force-include" in toml or "artifacts" in toml, (
        "hatchling force-include / artifacts table must be present for out-of-tree assets"
    )


def test_l13_console_html_path_resolves_dev_layout(tmp_path):
    """dev 布局: <repo_root>/console.html 存在 → 用 repo_root (console_api.py
    向上 4 层)。"""
    from src.unihive.utils.console_api import resolve_console_html_path

    f = tmp_path / "src" / "unihive" / "utils" / "console_api.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("")
    (tmp_path / "console.html").write_text("dev")
    assert resolve_console_html_path(f) == tmp_path / "console.html"


def test_l13_console_html_path_resolves_installed_layout(tmp_path):
    """installed 布局: <site-packages>/console.html (force-include 落点),
    repo_root 向上 4 层不再有 console.html → 回落到 site-packages (向上 3 层)。"""
    from src.unihive.utils.console_api import resolve_console_html_path

    f = tmp_path / "unihive" / "utils" / "console_api.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("")
    (tmp_path / "console.html").write_text("installed")
    # 确认向上 4 层没有 console.html (模拟 installed: site-packages 的父目录无该文件)
    assert resolve_console_html_path(f) == tmp_path / "console.html"
