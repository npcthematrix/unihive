"""Console authentication module."""
import hashlib
import hmac
import html
import secrets
from typing import Optional


class ConsoleAuthConfigError(ValueError):
    """Console 鉴权被显式启用但配置不完整/不安全时抛出。

    继承 ValueError: gateway main() 已把 ValueError 映射为干净的配置错误
    消息 + 退出码 2, 不需要额外的异常分支。典型场景: UNIHIVE_CONSOLE_*
    只配了一两个 (半配置 fail-closed)、session_secret 太短、照抄了公开
    出厂默认凭证。
    """

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


# 旧出厂 config/config.yaml 随公开仓库发布的固定凭证。任何拿到仓库的人
# 都能读到它们: 固定 session_secret 可离线伪造会话 cookie (无需口令),
# 固定口令哈希对应公开默认口令。2026-09-19 起出厂模板不再携带凭证;
# 显式配置照抄这些值一律拒绝, 防 "新部署沿用旧钥匙"。
KNOWN_DEFAULT_SESSION_SECRET = "0123456789abcdef" * 4
KNOWN_DEFAULT_PASSWORD_HASHES = frozenset({
    # admin / admin123 (PBKDF2-HMAC-SHA256, 200000)
    "pbkdf2_sha256$200000$d506b2bd1306c1e8f4d3d228a3313022$"
    "41d50a4c0592c0a8e4eebdf9afdd99e2cda48a743a87095272cf1cc89b349445",
})


# 常见弱口令: 黑名单固定哈希挡不住 "新鲜哈希的弱口令" (每次 hash_password
# 随机盐), 必须按口令集合校验。env 路径有明文可 O(1) 直查; YAML 路径只有
# 哈希, 用 verify_password 逐个验 (一次性启动成本, 约数百 ms)。
WEAK_PASSWORDS = frozenset({"admin", "admin123", "123456", "password"})


def reject_weak_password(cfg: dict, *, plaintext_password: Optional[str] = None) -> None:
    """拒绝常见弱口令/与用户名相同的口令, 不合法抛 ConsoleAuthConfigError。

    - ``plaintext_password`` 给出 (env 边界路径): 直接集合比对 + 用户名比对;
    - 仅给 cfg['password_hash'] (YAML 路径): 对弱口令集逐个 PBKDF2 验证。
    """
    username = cfg.get("username")
    if plaintext_password is not None:
        if plaintext_password in WEAK_PASSWORDS or (
            username and plaintext_password == username
        ):
            raise ConsoleAuthConfigError(
                "console password is a commonly-used weak password (or equal "
                "to the username) — choose a non-dictionary password"
            )
        return

    stored_hash = cfg.get("password_hash")
    if stored_hash and any(
        verify_password(weak, stored_hash) for weak in WEAK_PASSWORDS
    ):
        raise ConsoleAuthConfigError(
            "console password_hash corresponds to a commonly-used weak "
            "password — set your own via UNIHIVE_CONSOLE_PASSWORD or "
            "hash_password()"
        )


def reject_known_default_credentials(cfg: dict) -> None:
    """拒绝照抄旧出厂模板的公开默认凭证, 不合法抛 ConsoleAuthConfigError。

    setup_console_auth 与配置解析层 (env / YAML) 共用同一道闸:
    解析层拦不住的拼装路径 (直接调 setup 的测试/脚本) 在这里兜底。
    """
    if cfg.get("session_secret") == KNOWN_DEFAULT_SESSION_SECRET:
        raise ConsoleAuthConfigError(
            "console session_secret is the known public default shipped in "
            "old config/config.yaml — anyone can forge session cookies with "
            "it; generate a fresh one with "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if cfg.get("password_hash") in KNOWN_DEFAULT_PASSWORD_HASHES:
        raise ConsoleAuthConfigError(
            "console password_hash is the known public default (admin/"
            "admin123) shipped in old config/config.yaml — set your own "
            "password via UNIHIVE_CONSOLE_PASSWORD or hash_password()"
        )


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
        <p class="login-meta">需授权访问 · 凭证配置见 .env (UNIHIVE_CONSOLE_*)</p>
    </main>
</body>
</html>"""


class ConsoleAuthMiddleware:
    """Middleware to protect console routes."""

    # /api/auth/login 必须公开 (否则未登录无法换取会话); logout 也公开,
    # 这样会话过期后前端仍能完成退出。/api/auth/me 不在此列 — 未登录拿到
    # 401 正是前端判断会话状态的依据。
    PUBLIC_PATHS = (
        "/health",
        "/login",
        "/logout",
        "/api/auth/login",
        "/api/auth/logout",
        "/static",
    )

    def __init__(self, app, mcp_path: str = "/mcp"):
        self.app = app
        # M10 (round 9 audit): mcp_path 可配 (config.yaml gateway.mcp_path)。
        # 旧实现把 "/mcp" 写死进 PUBLIC_PATHS → 配 "/api/mcp" 后该路径吃
        # /api/ 鉴权门 401, 上游假死。这里按配置动态豁免。尾斜杠归一。
        self.mcp_path = (mcp_path or "/mcp").rstrip("/") or "/"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")

        # Allow public paths (含配置的 mcp_path)
        public = self.PUBLIC_PATHS + (self.mcp_path,)
        if any(path == p or path.startswith(p + "/") for p in public):
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
    # Round-12 簇F: error 是反射进 HTML 的查询参数, 必须转义防 XSS
    # (quote=True 同时挡属性注入向量)。
    safe_error = html.escape(error, quote=True)
    error_html = (
        f'<div class="login-error" role="alert">{safe_error}</div>' if safe_error else ""
    )
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
    # 非对象 JSON (数组/字符串/数字/null) 没有 .get, 必须在边界上 400,
    # 否则 AttributeError 会以 500 漏出去。
    if not isinstance(body, dict):
        return JSONResponse(
            {"error": "请求体必须是 JSON 对象 (含 username/password)"},
            status_code=400,
        )
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
    """GET /api/auth/me (鉴权启用态) → 200 {username, auth_required: true}。

    未登录由 ConsoleAuthMiddleware 在到达本函数前返回 401。
    """
    return JSONResponse(
        {"username": request.session.get("user"), "auth_required": True}
    )


async def api_me_open(request: Request) -> JSONResponse:
    """GET /api/auth/me (鉴权关闭态) → 200 {username: null, auth_required: false}。

    Round-13 M3: 关闭鉴权时也必须注册 /me。旧实现只在 setup_console_auth
    里注册该路由, 关闭态返回 404 —— 前端守卫据此误判 "未登录" 永久卡
    登录页, 而后端 /api/* 反而全裸, 开关两侧语义不对称。
    """
    return JSONResponse({"username": None, "auth_required": False})


def setup_console_auth(app: Starlette, console_cfg: dict, mcp_path: str = "/mcp") -> None:
    """Set up console authentication.

    Args:
        app: Starlette application
        console_cfg: Console configuration dict with username, password_hash, session_secret
        mcp_path: 对外 MCP 端点路径 (来自 config.yaml gateway.mcp_path), 免鉴权

    Note:
        Only ``password_hash`` is accepted. Plaintext ``password`` is
        rejected at setup time so credentials never sit in process memory
        longer than necessary (the hash is what gets persisted to
        ``app.state`` and compared on every login).

        Generate a hash with::

            from unihive.console_auth import hash_password
            print(hash_password("your-password"))
    """
    secret_key = console_cfg.get("session_secret")
    if not secret_key or len(secret_key) < 32:
        raise ConsoleAuthConfigError(
            "session_secret must be at least 32 characters"
        )

    if not console_cfg.get("password_hash"):
        raise ConsoleAuthConfigError(
            "console config requires password_hash (PBKDF2 format). "
            "Plaintext password is no longer accepted to avoid keeping "
            "credentials in memory. Generate one with hash_password()."
        )
    if "password" in console_cfg:
        # Refuse any plaintext password that snuck in — fail loud so callers
        # don't think the plaintext is being honored.
        raise ConsoleAuthConfigError(
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
    # 兜底拒绝公开出厂默认凭证 (env/YAML 解析层也各拦一次)。
    reject_known_default_credentials(app.state.console_auth_config)

    # Add auth middleware first (inner - runs after session is populated)
    # M10: 传 mcp_path 让配置的 MCP 端点免鉴权 (不再写死 /mcp)。
    app.add_middleware(ConsoleAuthMiddleware, mcp_path=mcp_path)

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
