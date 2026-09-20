"""HttpServeMixin — HTTP 装配 (uvicorn + /mcp 挂载 + console 鉴权/静态资源)。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
跨簇调用 (生命周期/上游/console API) 经 ``self`` 或方法内 inline import。
"""
import asyncio
import ipaddress
import json
import logging
import re
import socket
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import Response
from starlette.routing import Mount, Route, get_route_path
from starlette.staticfiles import StaticFiles

from ._helpers import _get_console_auth_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Round-13 簇L: HTTP 装配硬化助手
# ---------------------------------------------------------------------------

# mcp_path 不得与控制台/健康/静态等保留根路径完全相等 (Mount 前缀匹配会
# 影子化整棵子树)。更深的子路径 (如 /api/mcp, round-9 M10 场景) 允许。
_RESERVED_MCP_ROOTS = (
    "/api",
    "/static",
    "/health",
    "/login",
    "/logout",
    "/console.html",
)


# 路径字符白名单: 字母数字 + 路径分隔符 + URL 安全符号 (. _ ~ % -)。
# 拒绝空格/控制字符/反斜杠/?# 等 —— mcp_path 直接进 Mount/中间件匹配,
# 只允许规范 URL 路径形态 (review L3, 纵深防御; 当前无直接可利用路径)。
_MCP_PATH_CHARS_RE = re.compile(r"^[A-Za-z0-9._~%/_-]+$")


def validate_mcp_path(raw: object) -> str:
    """校验 gateway.mcp_path 并返回归一化值 (无尾斜杠)。

    非法配置在装配期 fail-fast (ValueError → main 退出码 2), 不带着坏
    Mount 进 uvicorn: "/" 会吞掉全部路由, "/api" 会影子化所有控制台 API。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(
            f"gateway.mcp_path 必须是非空字符串且以 '/' 开头 (got {raw!r})"
        )
    # 不用 strip() 归一: 首尾空白/控制字符 (如 '/mcp\n') 必须交给字符白名单
    # 拒绝, 而不是静默吃掉后放行。
    path = raw
    if not path.startswith("/"):
        raise ValueError(
            f"gateway.mcp_path 必须以 '/' 开头 (got {raw!r})"
        )
    if not _MCP_PATH_CHARS_RE.fullmatch(path):
        raise ValueError(
            f"gateway.mcp_path 含非法字符 (仅允许字母数字与 . _ ~ % - /, "
            f"拒绝空格/控制字符/反斜杠/?#) (got {raw!r})"
        )
    segments = path.split("/")
    if any(seg in (".", "..") for seg in segments):
        raise ValueError(
            f"gateway.mcp_path 不得含 '.' 或 '..' 路径段 (got {raw!r})"
        )
    if "//" in path:
        raise ValueError(f"gateway.mcp_path 含空路径段 '//' (got {raw!r})")
    normalized = path.rstrip("/")
    if not normalized:
        raise ValueError("gateway.mcp_path 不能是 '/'")
    if normalized in _RESERVED_MCP_ROOTS:
        raise ValueError(
            f"gateway.mcp_path {raw!r} 与保留路径 {normalized!r} 冲突; "
            "请使用独立前缀 (如 /mcp 或 /api/mcp)"
        )
    return normalized


def is_non_loopback_host(host: str | None) -> bool:
    """host 是否可能暴露在回环之外 (用于 "开放绑定 + 无鉴权" 告警决策)。

    回环: 127.0.0.0/8、::1、localhost。0.0.0.0/:: (全网卡)、私网/公网 IP
    都算非回环。无法判定的主机名按 "可能对外暴露" 处理 —— 该函数只决定
    是否打告警, 保守侧成本仅一行日志。
    """
    if not host:
        return False
    if host.strip().lower() == "localhost":
        return False
    try:
        ip = ipaddress.ip_address(host.strip())
    except ValueError:
        return True
    return not ip.is_loopback


def _preflight_console_html(html_path: str | Path) -> None:
    """启动预检 console.html 存在; 缺失给可操作的构建提示。

    旧行为: 缺失时首页静默 404, 且布局启发式可能误判运行时根目录。
    FileNotFoundError 经 main() 映射为退出码 1。
    """
    p = Path(html_path)
    if not p.is_file():
        raise FileNotFoundError(
            f"console.html not found at {p}. Build the console first: "
            "cd web && npm run build "
            "(finalize-build copies dist/index.html to <root>/console.html "
            "and static assets to static/console/)"
        )


def _console_index_response(html: bytes) -> Response:
    """console.html 外壳响应: 必须 no-cache。

    vite 构建出的 JS chunk 名带内容 hash, finalize-build 每次整目录替换;
    若浏览器缓存旧 HTML, 其引用的旧 hash chunk 已被删除 → 白屏。
    chunk 本身 (/static/assets/*) 走 immutable 长缓存。
    """
    return Response(
        html,
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


class ImmutableHashedAssets:
    """给内容指纹静态资源加 immutable 缓存头的 ASGI 包装。

    包在 StaticFiles 外 (Mount 剥前缀后, 内层 scope path 已是 /assets/…):
    vite 产出的 chunk 名带 hash, 内容不变则名不变 → 一年 immutable 安全;
    指纹外的 login.css / favicon.svg 不放大缓存。
    """

    _IMMUTABLE = b"public, max-age=31536000, immutable"

    def __init__(self, app, prefix: str = "/assets/"):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        # Mount 不改写 scope["path"], 子 app 经 get_route_path 取剥前缀后
        # 的相对路径 (此处为 "/assets/…")。
        if scope.get("type") != "http" or not get_route_path(scope).startswith(
            self.prefix
        ):
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", self._IMMUTABLE))
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _blocking_json_api(sync_fn):
    """把同步阻塞的 console_api 读函数包成 async handler, 下放 worker 线程。

    get_upstream_status (探针 future.result 最坏 ~6s) / get_health (socket
    连接) / load_config (sqlite) / get_interfaces (YAML) 都不得在事件循环
    线程直接跑, 否则一个不可达上游会让整个网关的 HTTP 面停顿。
    """

    async def _handler(_req: Request):
        return JSONResponse(await asyncio.to_thread(sync_fn))

    return _handler


class HttpServeMixin:
    """serve_http 方法簇 (GatewayServer 组装时混入)。"""

    async def serve_http(self, host: str = None, port: int = None, mcp_path: str = None):
        """HTTP 模式: 同时 serve MCP 接口和 Console 管理 UI."""
        self._running = True
        # M5 (2026-09-07 5th-round audit): initialize() 失败时重置 _running 并
        # 调 stop() 清 partial state, 允许同实例重试。
        try:
            await self.initialize()
        except BaseException:
            await self.stop()
            raise
        # M5 (round 9 audit): host/port/mcp_path 默认值必须在 initialize() 之后
        # 从 self.config 取。M9 路径下 __init__ 预检失败时 self.config={} (或无
        # gateway 段); 旧实现在 init 前快照 → 静默回落 127.0.0.1:18080/mcp,
        # DB/config.yaml 改的端口永不生效。显式参数 (CLI) 仍优先。
        gw_cfg = self.config.get("gateway", {})
        host = host if host is not None else gw_cfg.get("host", "127.0.0.1")
        port = port if port is not None else gw_cfg.get("port", 18080)
        mcp_path = mcp_path if mcp_path is not None else gw_cfg.get("mcp_path", "/mcp")

        # Console API handlers (delegates to console_api.py)
        from ..utils import console_api as _ca
        from ..utils.proxy_api import build_proxy_routes

        def _proxy_routes():
            return build_proxy_routes()

        # M2 (round-13): 同步阻塞读全部下放 worker 线程, 不占事件循环。
        _status_api = _blocking_json_api(_ca.get_upstream_status)
        _interfaces_api = _blocking_json_api(_ca.get_interfaces)
        _config_api = _blocking_json_api(_ca.load_config)
        _health_api = _blocking_json_api(_ca.get_health)

        async def _fuyao_cache_get_api(req: Request):
            return JSONResponse(_ca.get_fuyao_cache())

        async def _fuyao_cache_put_api(req: Request):
            # M3: 非法 JSON 体 → 400 (旧实现裸 await req.json() 直接 500);
            # fuyao_cache 缺失/null 不再被 ``or {}`` 静默当成清空。
            try:
                body = await req.json()
            except Exception:
                return JSONResponse({"error": "invalid JSON body"}, status_code=400)
            try:
                payload, expected_version = _ca.parse_fuyao_cache_put_body(body)
            except ValueError as e:
                return JSONResponse(
                    {"error": "validation_failed", "message": str(e)},
                    status_code=400,
                )
            try:
                result = await _ca.save_fuyao_cache(
                    payload,
                    expected_version=expected_version,
                )
            except _ca.ConfigStoreUnavailable as e:
                return JSONResponse(
                    {"error": "config_store_unavailable", "message": str(e)},
                    status_code=503,
                )
            except _ca.VersionConflict as e:
                return JSONResponse(
                    {"error": "version_conflict", "message": str(e)},
                    status_code=409,
                )
            except ValueError as e:
                return JSONResponse(
                    {"error": "validation_failed", "message": str(e)},
                    status_code=400,
                )
            return JSONResponse(result)

        async def _mcp_tools_api(req: Request):
            # L3 (2026-09-07 5th-round audit): mcp 未初始化时返回 503,
            # 避免 AttributeError。实际不可达 (serve_http 必先 await init),
            # 但加 guard 让 handler 更鲁棒。
            if self.mcp is None:
                return JSONResponse(
                    {"error": "gateway not initialized"}, status_code=503
                )
            raw = await self.mcp.list_tools()
            # 构建 routing_key → source 映射（从 upstream_tool_mapping 查，查不到返回 unknown）
            # M2 (round-13): 映射构建读 YAML/配置, 同步阻塞, 下放 worker 线程。
            source_map = await asyncio.to_thread(_ca._build_source_map)
            tools_out = []
            for ft in raw:
                d = ft.model_dump()
                name = d.get("name") or ""
                params_schema = d.get("parameters") or {}
                props = params_schema.get("properties", {})
                required = params_schema.get("required", [])
                params = []
                for pname, pinfo in props.items():
                    desc = pinfo.get("description", "")
                    ptype = pinfo.get("type", "any")
                    enum_vals = pinfo.get("enum")
                    p = {"name": pname, "type": ptype, "required": pname in required, "description": desc}
                    if enum_vals:
                        p["enum"] = enum_vals
                    params.append(p)
                source = source_map.get(name, "unknown")
                tools_out.append({"name": name, "description": d.get("description") or "", "source": source, "params": params})
            body = json.dumps({"timestamp": int(time.time()), "tools": tools_out}, indent=2, ensure_ascii=False)
            return Response(body, media_type="application/json")

        def _read_file(path: str):
            p = Path(path)
            return p.read_bytes() if p.exists() else None

        async def _index_route(req: Request):
            html = _read_file(_ca.CONSOLE_HTML_PATH)
            if html is None:
                # B3 预检已在启动时拦截; 运行期被删走这条防御路径。
                return JSONResponse({"error": "console.html not found"}, status_code=404)
            return _console_index_response(html)

        # MCP ASGI app — path="/" 让 inner 路由在 root,
        # 由 Mount("/mcp", ...) 接管 URL 前缀。否则 inner 路由在 /mcp/ 会被
        # Mount 剥离前缀后变成 404 (外 /mcp/ → 内 /, 路由在 /mcp/ 不匹配)。
        # 显式传 allowed_hosts / allowed_origins / host_origin_protection,
        # 不依赖 FastMCP 4.x 的默认值 (默认开, 但版本升级可能改),
        # 始终满足 MCP 2025-11-25 spec 对 DNS-rebinding 防护的 MUST 要求。
        # H4 (round 9 audit): 路由装配段 (mcp.http_app / NoSlashStarlette /
        # setup_console_auth) 抛错时旧实现绕过 stop() → 半建 inner_app/mcp
        # 泄漏。装配段纳入 try, 崩溃也 stop()。上面的 handler 定义只声明闭包,
        # 不会抛, 留在 try 外。
        try:
            # B2 (round-13): mcp_path 装配期 fail-fast, 不与控制台/静态/
            # 健康检查保留根路径冲突。
            mcp_path = validate_mcp_path(mcp_path)
            # B3 (round-13): console.html 缺失直接拒启并给出 build 提示,
            # 不等首页 404 才发现布局/构建问题。
            _preflight_console_html(_ca.CONSOLE_HTML_PATH)

            mcp_app = self.mcp.http_app(
                path="/",
                allowed_hosts=["127.0.0.1:*", "localhost:*"],
                allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
                host_origin_protection=True,
            )

            # Mount("/mcp", ...) 对子路径 /mcp/foo 会剥前缀成 /foo,
            # 对完全相等的 /mcp 则不剥, 透传 /mcp 给 inner app, 但 mcp_app
            # 的 route 挂在 "/" 上, 不匹配会 404。
            # 用 make_mcp_path_canonicalizer 把 /mcp 改写成 /mcp/, 让 Mount 能命中。
            # 用 NoSlashStarlette 关掉 Starlette 默认的 redirect_slashes=True,
            # 否则 POST /mcp → 307 → /mcp/, 多数 MCP 客户端不会自动跟 POST 307。
            # 来自 src.core.mcp_factory (Refactor 1, 2026-09-08)。
            from ..core.mcp_factory import NoSlashStarlette, make_mcp_path_canonicalizer

            inner_app = NoSlashStarlette(
                lifespan=mcp_app.lifespan,
                routes=[
                    Mount(mcp_path, app=mcp_app),
                    # H1 (round 10 audit): 用布局感知的 _ca.STATIC_DIR (dev=repo/static,
                    # installed=site-packages/static 经 wheel force-include 落地);
                    # 旧实现写死 4-parents 目录, 非 editable wheel 安装下目录不存在,
                    # StaticFiles 构造期即 raise → 网关拒启。
                    # B1 (round-13): /static/assets/* 是内容指纹 chunk →
                    # immutable 长缓存; 指纹外文件不加。
                    Mount(
                        "/static",
                        app=ImmutableHashedAssets(
                            StaticFiles(directory=_ca.STATIC_DIR, html=False)
                        ),
                    ),
                    Route("/", _index_route),
                    Route("/console.html", _index_route),
                    Route("/api/status", _status_api),
                    *_ca.build_omni_routes(),
                    *_ca.build_mcp_browser_routes(),
                    Route("/api/upstreams/{name}/metrics", _ca.upstream_metrics_api),
                    *_proxy_routes(),
                    Route("/api/interfaces", _interfaces_api),
                    Route("/api/config", _config_api),
                    Route("/api/config/fuyao-cache", _fuyao_cache_get_api),
                    Route("/api/config/fuyao-cache", _fuyao_cache_put_api, methods=["PUT"]),
                    # 2026-09-18: 上游启用/禁用热开关 (鉴权随 /api/* 中间件继承)
                    *_ca.build_upstream_routes(),
                    Route("/health", _health_api),
                    Route("/api/mcp-tools-list", _mcp_tools_api),
                ]
            )

            # HIGH #4 (console needs login): wire console_auth into inner_app when
            # console config is set in YAML or all three UNIHIVE_CONSOLE_* env vars.
            # ConsoleAuthMiddleware exempts /mcp, /health, /login, /logout, /static —
            # so MCP clients and health probes keep working. If not configured,
            # leave the console open and log why (backward compatible; opt-in auth).
            # L5 (round-13 review): console 段读取尊重 --config 锚定的配置
            # 目录, 不再只认 layout 根下的 config/config.yaml。
            # __new__ 构造的轻量测试实例可能没有 config_path; None 时
            # _get_console_auth_config 回退到 layout 锚定的默认 config.yaml。
            _anchor = getattr(self, "config_path", None)
            _config_yaml_path = (
                _anchor.parent / "config.yaml" if _anchor is not None else None
            )
            auth_cfg, auth_reason = _get_console_auth_config(
                self.config, config_yaml_path=_config_yaml_path
            )
            if auth_cfg is not None:
                from ..console_auth import setup_console_auth
                # M10: 传 mcp_path 让配置的 MCP 端点免鉴权 (不再写死 /mcp)。
                setup_console_auth(inner_app, auth_cfg, mcp_path=mcp_path)
                logger.info("Console auth enabled (UNIHIVE_CONSOLE_* set)")
            else:
                # M3 (round-13): 关闭态也必须注册 /api/auth/me 并声明
                # auth_required=false。旧实现在这里什么都不挂 → /me 404,
                # Vue 路由守卫据此误判 "未登录" 永久卡登录页, 而 /api/*
                # 反而全裸 —— 开关两侧语义必须对称。
                from ..console_auth import api_me_open

                inner_app.router.routes.append(
                    Route("/api/auth/me", api_me_open, methods=["GET"])
                )
                logger.warning(f"Console auth disabled: {auth_reason}")
                # 安全 H1 (round-13 review): 鉴权默认关闭 + 出厂模板曾默认
                # 0.0.0.0 = 同网段任何人都能未认证写 /api/* (改上游/导凭证)。
                # 出厂 host 已改回 127.0.0.1; 用户显式绑非回环且不开鉴权时,
                # 打显著 WARNING 把风险讲明 (不强制拒绝 —— 本机/受信网场景保留)。
                if is_non_loopback_host(host):
                    logger.warning(
                        "SECURITY: console listens on non-loopback host %r "
                        "with console auth DISABLED — /api/* write endpoints "
                        "(upstream/proxy/config edits, omni sync) are "
                        "reachable without login from the network. Set "
                        "UNIHIVE_CONSOLE_* env vars to enable auth, or bind "
                        "127.0.0.1",
                        host,
                    )

            app = make_mcp_path_canonicalizer(inner_app, mcp_path)
            log_level = self.config.get("logging", {}).get("level", "info").lower()
            # L12 (round 9 audit): uvicorn 默认 timeout_graceful_shutdown=None
            # (无硬上限), 长闲连接会拖住 SIGTERM 远超 stop() 的 max_wait=10s。
            # 与 stop() 的 max_wait 对齐, 让 uvicorn 在同一预算内强收连接。
            config = uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level=log_level,
                timeout_graceful_shutdown=10,
            )
        except BaseException:
            await self.stop()
            raise

        # 端口可用性预检: uvicorn 绑定失败只 log + 退出 3, 不 raise OSError,
        # 拿不到干净错误。预检给一个明确消息 + 退出码 2。
        #
        # TOCTOU 评估 (MED5): _probe.close() → uvicorn serve() 之间存在
        # 亚毫秒窗口, 另一进程理论上能抢端口。考虑到:
        #   1) 单用户本地 dev 场景, 端口竞争源少;
        #   2) 即便 uvicorn 二次 bind 失败, 它会进自己的 retry/backoff,
        #      我们已有 serve_http 的 finally cleanup (停止 health loop /
        #      upstreams), 不至于泄漏资源;
        #   3) 加 retry loop 或 SO_REUSEADDR 反而会盖掉真实的端口冲突信号
        #      (SO_REUSEADDR 在 Linux 上允许 bind 已被 LISTEN 但已 CLOSE 的端口,
        #       在 Windows 上行为不同)。
        # 结论: 接受 TOCTOU 风险, 维持现有预检作为友好错误信号。
        #
        # H1 (2026-09-07 5th-round audit): port probe + sys.exit(2) 必须
        # 在 outer try/finally 里面, 否则端口冲突时 stop() 不跑,
        # health_task / upstreams / cache 全部泄漏。
        try:
            _probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                try:
                    _probe.bind((host, port))
                except OSError as e:
                    if e.errno in (98, 10048) or "address already in use" in str(e).lower():
                        print(
                            f"error: cannot bind {host}:{port} — port already in use",
                            file=sys.stderr,
                        )
                        sys.exit(2)
                    raise
            finally:
                # Always release the probe fd regardless of bind outcome
                # (including non-errno-98 OSError paths that re-raise).
                _probe.close()

            await uvicorn.Server(config).serve()
        finally:
            await self.stop()
