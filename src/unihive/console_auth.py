"""Console authentication module."""
import hashlib
import hmac
import secrets
from typing import Optional

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from starlette.routing import Route


def hash_password(plain: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256.

    Format: pbkdf2_sha256$iterations$salt$hex
    """
    salt_bytes = secrets.token_bytes(16)
    iterations = 200000
    key = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode("utf-8"),
        salt_bytes,
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt_bytes.hex()}${key.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against a hash.

    Returns False on any error (malformed hash, etc.) to avoid timing attacks.
    """
    try:
        parts = hashed.split("$")
        if len(parts) != 4:
            return False
        prefix, iter_str, salt_hex, key_hex = parts
        if prefix != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        salt_bytes = bytes.fromhex(salt_hex)
        computed = hashlib.pbkdf2_hmac(
            "sha256",
            plain.encode("utf-8"),
            salt_bytes,
            iterations,
        )
        return hmac.compare_digest(computed.hex(), key_hex)
    except Exception:
        return False


LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'self'; form-action 'self';">
    <title>UniHive Console — 登录</title>
    <link rel="icon" type="image/svg+xml" href="/static/console/favicon.svg">
    <link rel="stylesheet" href="/static/console/login.css">
</head>
<body>
    <main class="login-card">
        <img class="brand-icon" src="/static/console/favicon.svg" alt="" width="40" height="40">
        <h1>UniHive Console</h1>
        <p class="login-subtitle">金融数据 MCP 聚合网关</p>
        {error}
        <form method="post" class="login-form" autocomplete="on">
            <input type="text" name="username" class="login-input" placeholder="用户名" required autocomplete="username" autofocus>
            <input type="password" name="password" class="login-input" placeholder="密码" required autocomplete="current-password">
            <button type="submit" class="login-button">登录</button>
        </form>
        <p class="login-meta">需授权访问 · 配置请见 config/config.yaml</p>
    </main>
</body>
</html>"""


class ConsoleAuthMiddleware:
    """Middleware to protect console routes."""

    # /api/auth/login 必须公开 (否则未登录无法换取会话); logout 也公开,
    # 这样会话过期后前端仍能完成退出。/api/auth/me 不在此列 — 未登录拿到
    # 401 正是前端判断会话状态的依据。
    PUBLIC_PATHS = (
        "/mcp",
        "/health",
        "/login",
        "/logout",
        "/api/auth/login",
        "/api/auth/logout",
        "/static",
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")

        # Allow public paths
        if any(path == p or path.startswith(p + "/") for p in self.PUBLIC_PATHS):
            return await self.app(scope, receive, send)

        # 只有 /api/* 需要会话: 未登录返回 401 JSON, 由前端 axios 拦截器
        # 跳 Vue 登录页。HTML 外壳 (/ 与 /console.html) 对未登录用户同样
        # 下发 —— 外壳不含数据, 是否展示登录页由前端路由守卫调
        # /api/auth/me 决定 (标准 SPA 模式; /console.html 此前已是公开的)。
        if not path.startswith("/api/"):
            return await self.app(scope, receive, send)

        session = scope.get("session", {})
        if not session.get("user"):
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


async def login_get(request: Request) -> HTMLResponse:
    """Serve login page."""
    error = request.query_params.get("error", "")
    error_html = f'<div class="login-error" role="alert">{error}</div>' if error else ""
    return HTMLResponse(LOGIN_HTML.replace("{error}", error_html))


def _verify_credentials(request: Request, username: str, password: str) -> bool:
    """校验用户名/密码 against app.state.console_auth_config。

    MED-6 (2026-09-14 audit): setup_console_auth 只接受 password_hash,
    stored_password 分支仅为绕过 setup 的遗留调用方保留。
    """
    cfg = request.app.state.console_auth_config
    expected_user = cfg.get("username", "admin")
    stored_password = cfg.get("password")
    stored_hash = cfg.get("password_hash")

    valid = False
    if stored_hash and verify_password(password, stored_hash):
        valid = True
    elif stored_password and hmac.compare_digest(
        password.encode("utf-8"), stored_password.encode("utf-8")
    ):
        valid = True

    return valid and hmac.compare_digest(
        username.encode("utf-8"), expected_user.encode("utf-8")
    )


async def login_post(request: Request) -> HTMLResponse:
    """Handle login POST (legacy server-rendered form)."""
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    if not _verify_credentials(request, username, password):
        # Re-render login page with error message
        error_html = '<div class="login-error" role="alert">用户名或密码错误</div>'
        return HTMLResponse(LOGIN_HTML.replace("{error}", error_html), status_code=401)

    # Set session
    request.session["user"] = username
    return RedirectResponse("/", status_code=303)


async def logout_post(request: Request) -> PlainTextResponse:
    """Handle logout POST (legacy form endpoint)."""
    request.session.clear()
    return PlainTextResponse("Logged out", status_code=200)


# ========== JSON API (Vue 控制台, 2026-09-14) ==========
# 会话载体仍是同一个签名 cookie (unihive_console); 这里只把 form/303
# 交互换成 fetch 友好的 JSON, 不引入 token 或新的权限模型。


async def api_login(request: Request) -> JSONResponse:
    """POST /api/auth/login {username, password} → 200 {username} / 401。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求体必须是 JSON"}, status_code=400)
    username = str(body.get("username") or "")
    password = str(body.get("password") or "")
    if not username or not password:
        return JSONResponse(
            {"error": "用户名和密码不能为空"}, status_code=400
        )

    if not _verify_credentials(request, username, password):
        return JSONResponse({"error": "用户名或密码错误"}, status_code=401)

    request.session["user"] = username
    return JSONResponse({"username": username})


async def api_logout(request: Request) -> JSONResponse:
    """POST /api/auth/logout → 200。公开端点, 会话过期也可调用。"""
    request.session.clear()
    return JSONResponse({"ok": True})


async def api_me(request: Request) -> JSONResponse:
    """GET /api/auth/me → 200 {username}。未登录由中间件返回 401。"""
    return JSONResponse({"username": request.session.get("user")})


def setup_console_auth(app: Starlette, console_cfg: dict) -> None:
    """Set up console authentication.

    Args:
        app: Starlette application
        console_cfg: Console configuration dict with username, password_hash, session_secret

    Note:
        Only ``password_hash`` is accepted. Plaintext ``password`` is
        rejected at setup time so credentials never sit in process memory
        longer than necessary (the hash is what gets persisted to
        ``app.state`` and compared on every login).

        Generate a hash with::

            from src.unihive.console_auth import hash_password
            print(hash_password("your-password"))
    """
    secret_key = console_cfg.get("session_secret")
    if not secret_key or len(secret_key) < 32:
        raise ValueError("session_secret must be at least 32 characters")

    if not console_cfg.get("password_hash"):
        raise ValueError(
            "console config requires password_hash (PBKDF2 format). "
            "Plaintext password is no longer accepted to avoid keeping "
            "credentials in memory. Generate one with hash_password()."
        )
    if "password" in console_cfg:
        # Refuse any plaintext password that snuck in — fail loud so callers
        # don't think the plaintext is being honored.
        raise ValueError(
            "console config must not include plaintext 'password' — "
            "use 'password_hash' (PBKDF2) instead."
        )

    # Store ONLY the hash on app.state so debug dumps / introspection can't
    # surface a plaintext password.
    app.state.console_auth_config = {
        "username": console_cfg.get("username", "admin"),
        "password_hash": console_cfg["password_hash"],
        "session_secret": secret_key,
    }

    # Add auth middleware first (inner - runs after session is populated)
    app.add_middleware(ConsoleAuthMiddleware)

    # Add session middleware last (outer - runs first to populate session)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        session_cookie="unihive_console",
    )

    # Add login/logout routes (legacy form + Vue 控制台 JSON API)
    app.router.routes.extend([
        Route("/login", login_get, methods=["GET"]),
        Route("/login", login_post, methods=["POST"]),
        Route("/logout", logout_post, methods=["POST"]),
        Route("/api/auth/login", api_login, methods=["POST"]),
        Route("/api/auth/logout", api_logout, methods=["POST"]),
        Route("/api/auth/me", api_me, methods=["GET"]),
    ])
