"""HttpServeMixin — HTTP 装配 (uvicorn + /mcp 挂载 + console 鉴权/静态资源)。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
跨簇调用 (生命周期/上游/console API) 经 ``self`` 或方法内 inline import。
"""
import asyncio
import ipaddress
import logging
import re
import socket
import sys
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


def _precheck_bind(host: str, port: int) -> None:
    """端口占用预检: bind 一次首个解析地址, 占用则退出码 2 + 明确消息。

    Round-14: 旧实现写死 AF_INET, 配置 ``::1`` / ``::`` 等 IPv6 host 时预检
    抛 gaierror/EAFNOSUPPORT 并 re-raise, uvicorn (本身支持 IPv6) 从未被尝试。
    改按 getaddrinfo 解析出的实际地址族建探针; 解析不出任何地址则跳过预检,
    交给 uvicorn 报错 (不比真实服务更严格)。仅探首个地址, 与 uvicorn 绑定
    首个解析结果的行为一致。
    """
    try:
        infos = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            flags=socket.AI_PASSIVE,
        )
    except socket.gaierror:
        return
    if not infos:
        return
    family, socktype, proto, _canonname, sockaddr = infos[0]
    probe = socket.socket(family, socktype, proto)
    try:
        try:
            probe.bind(sockaddr)
        except OSError as e:
            if e.errno in (98, 10048) or "address already in use" in str(e).lower():
                print(
                    f"error: cannot bind {host}:{port} — port already in use",
                    file=sys.stderr,
                )
                sys.exit(2)
            raise
    finally:
        # 无论 bind 成功还是非占用 OSError 路径, 都释放探针 fd。
        probe.close()


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


# console.html 外壳 CSP:
# - 无内联脚本, script-src 只放同源 (chunk 在 /static/console/assets/);
# - 构建产物含内联启动 <style>, style-src 需要 'unsafe-inline';
# - Element Plus 部分图标走 data: URI;
# - frame-ancestors 'none' + X-Frame-Options DENY 防控制台被嵌套点击劫持。
_CONSOLE_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def _console_index_response(html: bytes) -> Response:
    """console.html 外壳响应: 必须 no-cache, 并带 CSP / 防嵌套头。

    vite 构建出的 JS chunk 名带内容 hash, finalize-build 每次整目录替换;
    若浏览器缓存旧 HTML, 其引用的旧 hash chunk 已被删除 → 白屏。
    chunk 本身 (/static/assets/*) 走 immutable 长缓存。
    """
    return Response(
        html,
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache",
            "Content-Security-Policy": _CONSOLE_CSP,
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        },
    )


class ImmutableHashedAssets:
    """给内容指纹静态资源加 immutable 缓存头的 ASGI 包装。

    包在 StaticFiles 外 (Mount 剥前缀后, 内层 scope path 已是 /console/assets/…):
    vite 产出的 chunk 名带 hash, 内容不变则名不变 → 一年 immutable 安全;
    指纹外的 login.css / favicon.svg 不放大缓存。
    """

    _IMMUTABLE = b"public, max-age=31536000, immutable"
    _ASSET_PREFIX = "/console/assets/"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Mount 不改写 scope["path"], 子 app 经 get_route_path 取剥前缀后
        # 的相对路径 (此处为 "/console/assets/…")。
        if scope.get("type") != "http" or not get_route_path(scope).startswith(
            self._ASSET_PREFIX
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
        # initialize() 内部 (_do_initialize except) 已自清理 + 调 stop();
        # 失败由 outer try/finally (_precheck_bind + uvicorn.Serve 块) 兜底 stop。
        await self.initialize()
        # M5 (round 9 audit): host/port/mcp_path 默认值必须在 initialize() 之后
        # 从 self.config 取。M9 路径下 __init__ 预检失败时 self.config={} (或无
        # gateway 段); 旧实现在 init 前快照 → 静默回落 127.0.0.1:18080/mcp,
        # DB/config.yaml 改的端口永不生效。显式参数 (CLI) 仍优先。
        gw_cfg = self.config.get("gateway", {})
        host = host if host is not None else gw_cfg.get("host", "127.0.0.1")
        port = port if port is not None else gw_cfg.get("port", 18080)
        mcp_path = mcp_path if mcp_path is not None else gw_cfg.get("mcp_path", "/mcp")

        # Handlers 经工厂构造 (原内嵌闭包, 现参数化传给 _build_inner_app)。
        # 因 _build_inner_app 已内联回 serve_http, handler 也内联回此处,
        # 仍用工厂函数减少内联代码量。
        from ..utils import console_api as _ca

        def _read_file(path: str):
            p = Path(path)
            return p.read_bytes() if p.exists() else None

        async def _index_route(req: Request):
            # 下放线程: 文件被杀软/网络盘占用时同步读会阻塞整个事件循环
            html = await asyncio.to_thread(_read_file, _ca.CONSOLE_HTML_PATH)
            if html is None:
                # B3 预检已在启动时拦截; 运行期被删走这条防御路径。
                return JSONResponse({"error": "console.html not found"}, status_code=404)
            return _console_index_response(html)

        # M2 (round-13): 同步阻塞读全部下放 worker 线程, 不占事件循环。
        status_api = _blocking_json_api(_ca.get_upstream_status)
        interfaces_api = _blocking_json_api(_ca.get_interfaces)
        config_api = _blocking_json_api(_ca.load_config)
        health_api = _blocking_json_api(_ca.get_health)

        # H4 (round 9 audit): 路由装配段 (mcp.http_app / NoSlashStarlette /
        # setup_console_auth) 抛错时旧实现绕过 stop() → 半建 inner_app/mcp
        # 泄漏。装配段纳入 try, 崩溃也 stop()。上面 handler 构造只声明闭包,
        # 不会抛, 留在 try 外。
        try:
            # B2 (round-13): mcp_path 装配期 fail-fast, 不与控制台/静态/健康
            # 检查保留根路径冲突。
            mcp_path = validate_mcp_path(mcp_path)
            # B3 (round-13): console.html 缺失直接拒启并给出 build 提示,
            # 不等首页 404 才发现布局/构建问题。
            _preflight_console_html(_ca.CONSOLE_HTML_PATH)

            from ..core.mcp_factory import NoSlashStarlette, make_mcp_path_canonicalizer
            from ..utils.proxy_api import build_proxy_routes

            mcp_app = self.mcp.http_app(
                path="/",
                json_response=True,
                host_origin_protection=False,
            )

            inner_app = NoSlashStarlette(
                lifespan=mcp_app.lifespan,
                routes=[
                    Mount(mcp_path, app=mcp_app),
                    # H1 (round 10 audit): 用布局感知的 _ca.STATIC_DIR (dev=repo/static,
                    # installed=site-packages/static 经 wheel force-include 落地);
                    # 旧实现写死 4-parents 目录, 非 editable wheel 安装下目录不存在,
                    # StaticFiles 构造期即 raise → 网关拒启。
                    # B1 (round-13): /static/assets/* 是内容指纹 chunk → immutable
                    # 长缓存; 指纹外文件不加。
                    Mount(
                        "/static",
                        app=ImmutableHashedAssets(
                            StaticFiles(directory=_ca.STATIC_DIR, html=False)
                        ),
                    ),
                    Route("/", _index_route),
                    Route("/console.html", _index_route),
                    Route("/api/status", status_api),
                    *_ca.build_omni_routes(),
                    *_ca.build_mcp_browser_routes(),
                    Route("/api/upstreams/{name}/metrics", _ca.upstream_metrics_api),
                    *_ca.build_cache_routes(),
                    *_ca.build_error_routes(),
                    *build_proxy_routes(),
                    Route("/api/interfaces", interfaces_api),
                    Route("/api/config", config_api),
                    # 2026-09-18: 上游启用/禁用热开关 (鉴权随 /api/* 中间件继承)
                    *_ca.build_upstream_routes(),
                    Route("/health", health_api),
                ]
            )

            # HIGH #4 (console needs login): wire console_auth into inner_app when
            # console config is set in YAML or all three UNIHIVE_CONSOLE_* env vars.
            # ConsoleAuthMiddleware exempts /mcp, /health, /login, /logout, /static —
            # so MCP clients and health probes keep working. If not configured,
            # leave the console open and log why (backward compatible; opt-in auth).
            # L5 (round-13 review): console 段读取尊重 --config 锚定的配置目录,
            # 不再只认 layout 根下的 config/config.yaml。__new__ 构造的轻量测试
            # 实例可能没有 config_path; None 时 _get_console_auth_config 回退到
            # layout 锚定的默认 config.yaml。
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
                # Vue 路由守卫据此误判 "未登录" 永久卡登录页, 而 /api/* 反而全裸
                # —— 开关两侧语义必须对称。
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
            log_level = self.config.get("logging", {}).get("level", "warning").lower()
            # L12 (round 9 audit): uvicorn 默认 timeout_graceful_shutdown=None
            # (无硬上限), 长闲连接会拖住 SIGTERM 远超 stop() 的 max_wait=10s。
            # 与 stop() 的 max_wait 对齐, 让 uvicorn 在同一预算内强收连接。
            config = uvicorn.Config(
                app,
                host=host,
                port=port,
                log_level=log_level,
                access_log=False,  # 禁用 uvicorn 访问日志
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
            _precheck_bind(host, port)
            await uvicorn.Server(config).serve()
        finally:
            await self.stop()
