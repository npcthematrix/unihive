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
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UniHive Console - Login</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .login-box { background: white; padding: 2rem; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); width: 300px; }
        h1 { margin: 0 0 1.5rem; font-size: 1.5rem; color: #333; }
        .error { color: #dc3545; font-size: 0.875rem; margin-bottom: 1rem; }
        input { width: 100%; padding: 0.75rem; margin-bottom: 1rem; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; font-size: 1rem; }
        button { width: 100%; padding: 0.75rem; background: #007bff; color: white; border: none; border-radius: 4px; font-size: 1rem; cursor: pointer; }
        button:hover { background: #0056b3; }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>UniHive Console</h1>
        {error}
        <form method="post">
            <input type="text" name="username" placeholder="Username" required autocomplete="username">
            <input type="password" name="password" placeholder="Password" required autocomplete="current-password">
            <button type="submit">Login</button>
        </form>
    </div>
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
    error_html = f'<div class="error">{error}</div>' if error else ""
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

    # Verify credentials
    valid = False
    if stored_password and password == stored_password:
        valid = True
    elif stored_hash and verify_password(password, stored_hash):
        valid = True

    if not valid or username != expected_user:
        # Re-render login page with error message
        error_html = '<div class="error">Invalid credentials</div>'
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
        console_cfg: Console configuration dict with username, password/password_hash, session_secret
    """
    secret_key = console_cfg.get("session_secret")
    if not secret_key or len(secret_key) < 32:
        raise ValueError("session_secret must be at least 32 characters")

    # Store config on app for access in routes
    app.state.console_auth_config = console_cfg

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
