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
    <link rel="stylesheet" href="/static/console/login.css">
</head>
<body>
    <main class="login-card">
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

    PUBLIC_PATHS = ("/mcp", "/health", "/login", "/logout", "/static")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")

        # Allow public paths
        if any(path == p or path.startswith(p + "/") for p in self.PUBLIC_PATHS):
            return await self.app(scope, receive, send)

        # Check if protected
        is_protected = path == "/" or path.startswith("/api/")

        if not is_protected:
            return await self.app(scope, receive, send)

        # Check session
        session = scope.get("session", {})
        if not session.get("user"):
            if path.startswith("/api/"):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
            else:
                response = RedirectResponse("/login", status_code=303)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


async def login_get(request: Request) -> HTMLResponse:
    """Serve login page."""
    error = request.query_params.get("error", "")
    error_html = f'<div class="login-error" role="alert">{error}</div>' if error else ""
    return HTMLResponse(LOGIN_HTML.replace("{error}", error_html))


async def login_post(request: Request) -> HTMLResponse:
    """Handle login POST."""
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    cfg = request.app.state.console_auth_config
    expected_user = cfg.get("username", "admin")
    stored_password = cfg.get("password")
    stored_hash = cfg.get("password_hash")

    # Verify credentials.
    # MED-6 (2026-09-14 audit): plaintext password branch removed —
    # setup_console_auth now requires password_hash. We keep the
    # stored_password fallback here only to support legacy callers that
    # bypassed setup_console_auth (e.g. tests constructing the middleware
    # directly); setup_console_auth itself never accepts plaintext.
    valid = False
    if stored_hash and verify_password(password, stored_hash):
        valid = True
    elif stored_password and hmac.compare_digest(
        password.encode("utf-8"), stored_password.encode("utf-8")
    ):
        valid = True

    if not valid or not hmac.compare_digest(
        username.encode("utf-8"), expected_user.encode("utf-8")
    ):
        # Re-render login page with error message
        error_html = '<div class="login-error" role="alert">用户名或密码错误</div>'
        return HTMLResponse(LOGIN_HTML.replace("{error}", error_html), status_code=401)

    # Set session
    request.session["user"] = username
    return RedirectResponse("/", status_code=303)


async def logout_post(request: Request) -> PlainTextResponse:
    """Handle logout POST."""
    request.session.clear()
    return PlainTextResponse("Logged out", status_code=200)


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

    # Add login/logout routes
    app.router.routes.extend([
        Route("/login", login_get, methods=["GET"]),
        Route("/login", login_post, methods=["POST"]),
        Route("/logout", logout_post, methods=["POST"]),
    ])
