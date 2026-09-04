"""Bearer Token 鉴权中间件。

包裹 ASGI app，对 MCP 入口强制校验 `Authorization: Bearer <token>`。
健康检查、根路径和 /api/* 路径直接放行（控制台独立绑 127.0.0.1，不暴露外网）。
"""
from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenMiddleware:
    """ASGI 中间件：除放行路径外要求 Bearer Token 匹配。"""

    PUBLIC_PATHS = ("/health", "/", "/api/")

    def __init__(self, app: ASGIApp, token: str):
        if not token:
            raise ValueError("token must be non-empty")
        self.app = app
        self.token = token

    def _is_public(self, path: str) -> bool:
        for p in self.PUBLIC_PATHS:
            if p == "/":
                if path == "/":
                    return True
            elif p.endswith("/"):
                if path.startswith(p):
                    return True
            else:
                if path == p:
                    return True
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if self._is_public(path):
            await self.app(scope, receive, send)
            return

        # 解析 Authorization header
        headers = dict(scope.get("headers") or [])
        auth_raw = headers.get(b"authorization")
        if auth_raw is None:
            await self._reject(send, "missing_authorization", include_www_auth=True)
            return

        scheme, _, token = auth_raw.decode("latin-1").partition(" ")
        if scheme.lower() != "bearer" or not token:
            await self._reject(send, "missing_authorization")
            return

        if token != self.token:
            await self._reject(send, "invalid_token")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send, code: str, include_www_auth: bool = False) -> None:
        body = f'{{"error":"{code}"}}'.encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if include_www_auth:
            headers.append((b"www-authenticate", b'Bearer realm="unihive"'))
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})
