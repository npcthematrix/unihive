# Plan: 管理控制台简单登录认证

**状态**: 待实施（上下文耗尽时拆出，详见 `feedback_no_sse_no_mcp_auth_console_auth`）
**范围**: `/api/*` + `/` (console.html)；`/mcp` 和 `/health` 公开
**目的**: 防止配置/状态/工具清单等内部信息被同主机用户随便读

---

## 设计

- **认证模型**: username + password + 会话 cookie
- **会话**: Starlette `SessionMiddleware`（签名 cookie，无服务端存储）
- **密码存储**: PBKDF2-HMAC-SHA256（stdlib, 无新依赖），config 支持 `password`（dev 明文）或 `password_hash`（生产）
- **保护范围**: `path == "/"` 或 `path.startswith("/api/")`
- **公开**: `/mcp`, `/health`, `/login`, `/logout`, `/static/*`（如未来加）
- **默认行为**: config 没 `console` 段 → 不开启 auth（向后兼容），启动日志告警

## config 形态

```yaml
console:
  enabled: true                # 缺省: 有 password/password_hash 即 true
  username: admin              # 缺省 admin
  password: "dev-pass"         # dev 友好; 或 password_hash: "pbkdf2_sha256$200000$salt$hex"
  session_secret: "<32-byte-hex>"  # 必填, 用于 cookie 签名
```

启动时若 `enabled=true` 但缺 `session_secret` → 启动失败（exit 2）。生成器：
```python
import secrets; print(secrets.token_hex(32))
```

## 实施步骤

### Step 1: `src/console_auth.py`（新文件，~150 行）

```python
# 内容:
# - hash_password(plain) -> str  (pbkdf2$iters$salt$hex)
# - verify_password(plain, hashed) -> bool  (hmac.compare_digest)
# - class ConsoleAuthMiddleware: path 门控, /mcp /health /login /logout 直通
# - login_get / login_post / logout_post (Starlette Route handlers)
# - LOGIN_HTML 常量: 内联最小化登录页 (username/password 字段 + error message)
# - setup_console_auth(app, console_cfg) -> None:
#     * SessionMiddleware(app, secret_key=..., session_cookie="unihive_console")
#     * 装载 ConsoleAuthMiddleware
#     * mount 3 个 login/logout routes
```

**关键逻辑**:
```python
class ConsoleAuthMiddleware:
    PUBLIC = ("/mcp", "/health", "/login", "/logout", "/static")
    PROTECTED = ("/",)  # 加上任何以 /api/ 开头的
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if any(path == p or path.startswith(p + "/") for p in self.PUBLIC):
            return await self.app(scope, receive, send)
        is_protected = path == "/" or path.startswith("/api/")
        if not is_protected:
            return await self.app(scope, receive, send)
        session = scope.get("session", {})
        if not session.get("user"):
            if path.startswith("/api/"):
                resp = JSONResponse({"error": "unauthorized"}, status_code=401)
            else:
                resp = RedirectResponse("/login", status_code=303)
            return await resp(scope, receive, send)
        return await self.app(scope, receive, send)
```

### Step 2: `src/gateway_server.py` 接线

在 `serve_http` 构造完 Starlette `app` 后追加:
```python
console_cfg = self.config.get("console", {})
if console_cfg.get("enabled", bool(console_cfg.get("password") or console_cfg.get("password_hash"))):
    from .console_auth import setup_console_auth
    if not console_cfg.get("session_secret"):
        logger.error("console.enabled=true 但缺 session_secret, 退出")
        sys.exit(2)
    setup_console_auth(app, console_cfg)
    logger.info("console auth enabled (user=%s)", console_cfg.get("username", "admin"))
else:
    logger.warning("console auth DISABLED — /api/* 和 / 可匿名访问")
```

加 import: `sys`（已有）。

### Step 3: `src/config_loader.py` 验证

`validate_config` 里追加 `console` 段检查:
```python
console = cfg.get("console", {})
if console:
    if not console.get("username"):
        errors.append("console.username missing")
    if not (console.get("password") or console.get("password_hash")):
        errors.append("console.password or password_hash required")
    if console.get("enabled", True) and not console.get("session_secret"):
        errors.append("console.session_secret required when enabled")
```

### Step 4: `config/upstreams.yaml` 加示例

```yaml
console:
  enabled: true
  username: admin
  password: "change-me-in-prod"  # dev 明文; 生产用 password_hash
  # password_hash: "pbkdf2_sha256$200000$..."
  session_secret: "REPLACE-WITH-python -c 'import secrets;print(secrets.token_hex(32))'"
```

### Step 5: `tests/test_console_auth.py`（新文件, ~180 行）

测试套件（按 AAA）:
1. `test_hash_verify_roundtrip` — hash + verify 同密码 → True
2. `test_verify_wrong_password` — 错密码 → False
3. `test_verify_malformed_hash` — 损坏 hash → False (不抛)
4. `test_login_get_returns_html_form` — GET /login → 200 + 含 `<form`
5. `test_login_post_valid_sets_session` — POST 正确 creds → 303 + Set-Cookie
6. `test_login_post_invalid_returns_401` — POST 错 creds → 401
7. `test_auth_middleware_blocks_api_when_unauthenticated` — GET /api/status (no cookie) → 401 JSON
8. `test_auth_middleware_blocks_root_when_unauthenticated` — GET / (no cookie) → 303 → /login
9. `test_auth_middleware_passes_mcp_unauthenticated` — GET /mcp → 200 (FastMCP 处理)
10. `test_auth_middleware_passes_health_unauthenticated` — GET /health → 200
11. `test_logout_clears_session` — POST /logout + 再 GET /api/status → 401
12. `test_session_persists_across_requests` — 登录后 2 次 /api/* 都 200
13. `test_auth_disabled_when_no_console_section` — config 无 console 段 → 全部公开

**用 Starlette `TestClient`** 跑（不真起 uvicorn）。Mock `console_cfg` 直接喂 `setup_console_auth`。

### Step 6: 全测验证

```bash
pytest tests/ -q
```

预期: 326 + ~13 = **339 passed, 0 failed**

## 验收标准

- [ ] 启动日志明确显示 `console auth enabled` 或 `DISABLED`
- [ ] `console.session_secret` 缺失时启动失败（exit 2）
- [ ] 未登录 GET / → 303 to /login
- [ ] 未登录 GET /api/* → 401 JSON
- [ ] /mcp 与 /health 始终公开
- [ ] 正确 creds 登录后 session 持续（cookie-based）
- [ ] logout 后 session 失效
- [ ] PBKDF2 hash verify 走 `hmac.compare_digest`（防 timing attack）

## 风险

- `SessionMiddleware` 签名 cookie, 密钥泄漏 = session 伪造; 文档需强调 `session_secret` 不能进 git
- 登录页用最简 form (无 CSRF), 适合本地 dev; 若上 production 加 CSRF token
- 当前 console.html 是 static 文件, 登录后无 UI 改动, 仍走 `/api/*` 同源; 若 console.html 要根据登录态改 UI, 需要 fetch `/api/session` 端点（可选, 不在本次范围）

## 改动量估算

```
 src/console_auth.py            | ~150 行 (新)
 src/gateway_server.py          | +15 行
 src/config_loader.py           | +12 行
 config/upstreams.yaml          | +8 行
 tests/test_console_auth.py     | ~180 行 (新)
                              --------
 总计                          | ~365 行, 5 文件
```
