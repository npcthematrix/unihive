"""LifecycleMixin — 初始化 (YAML/config.db 装配)、start/stop 与缓存清理循环。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
``Cache`` 经模块限定 ``cache_mod.Cache`` 访问以便 monkeypatch 命中本模块
binding; 其余跨簇调用一律经 ``self``。
"""
import asyncio
import contextlib
import logging
import signal
import sys
import time

from ..core.config_db import resolve_project_path
from ..core.proxy_settings import ProxySettings
from ..core.router import Router
from ..storage import cache as cache_mod
from ..storage.cache import CacheConfig
from ..storage.config_store import ConfigStore
from ..utils.config_loader import validate_config

logger = logging.getLogger(__name__)


class LifecycleMixin:
    """生命周期方法簇 (GatewayServer 组装时混入)。"""

    async def initialize(self):
        # 双重检查锁: 第二次 fast path 直接返回; 拿到锁后再校验一次防 TOCTOU。
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._do_initialize()

    async def _load_db_config(self) -> None:
        """以 data/config.db 实体表为事实源装配 self.config (v2, 2026-09-18)。

        YAML (upstreams.yaml + tools_*.yaml) 只是出厂模板: 空库自动从模板
        解构 seed, 之后运行时只读库内 sections / upstream / tool 实体,
        不再回读 YAML; config.yaml 仅贡献 bootstrap 子集 (console 整段 +
        gateway host/port/mcp_path, 永不入库)。设计见
        docs/design/2026-09-18-sqlite-config-full-database.md。

        - ``UNIHIVE_CONFIG_SOURCE=yaml`` 完全绕过 DB (故障逃生通道);
        - ``UNIHIVE_CONFIG_DB`` 覆盖默认路径 data/config.db;
        - 必须在任何 cache/client 构造之前执行: upstream.enabled 决定是否
          建连。装配结果 resolve env 后若过不了 validate_config, 直接
          fail-closed, 避免在错误配置上悄悄建连。
        """
        from ..core.config_db import ensure_seeded, load_template_from_store
        from ..core.db_assembler import assemble_config, merge_bootstrap
        from ..core.template_normalize import load_raw_config
        from ..utils import console_api as _ca
        from ..utils.config_loader import resolve_env_vars

        if not self._uses_db_source():
            logger.info("UNIHIVE_CONFIG_SOURCE=yaml, 配置事实源=YAML 文件 (无 SQLite)")
            _ca.set_config_db(None)
            return

        store = ConfigStore(self._db_path)
        # M4 (round 9 audit): store.initialize() 纳入 try —— 自身失败 (库路径不可
        # 写 / 磁盘满) 时旧实现在 try 外抛, except 接不到, store.close() 不被
        # 调 → SQLAlchemy engine 泄漏。这里赋值在 try 前, except 安全调 close。
        try:
            await store.initialize()
            # 空库: 从未解析 env 的 YAML 模板解构 seed (明文密钥 fail-closed)。
            await ensure_seeded(store, self.config_path)
            template = await load_template_from_store(store)
            self._db_hidden_tools = {
                t.name for t in template.tools if t.publish_mode == "hidden"
            }
            assembled = assemble_config(template)
            # bootstrap 只从 config.yaml 合 console + 监听键; 其余段以库为准,
            # 不被文件旧值盖住 (浅合并, 与 yaml 加载链同语义)。
            config_yaml_raw = load_raw_config(self.config_path.parent / "config.yaml")
            merge_bootstrap(assembled, config_yaml_raw)
            # M6 (round 9 audit): strict 下也跳过 disabled 上游块的 ${VAR} 解析,
            # 与 validate_config 同语义 —— 禁用上游不要求环境变量齐备,
            # 否则控制台禁用 fuyao 但环境无其 key 时网关起不来 (热改却容忍)。
            resolved, missing = resolve_env_vars(
                assembled, strict=self._strict_env, skip_disabled_upstreams=True
            )
            if missing and not self._strict_env:
                logger.warning("Missing env vars (kept as placeholders): %s", missing)
            errors = validate_config(resolved)
            if errors:
                raise ValueError(
                    f"DB config validation failed ({len(errors)} error(s)): "
                    + "; ".join(errors)
                )
        except Exception:
            await store.close()
            raise

        self.config_store = store
        self.config = resolved
        # 库内 gateway 调参段可能与 YAML 预检基线不同, 重放一次。
        self._apply_gateway_tuning()
        revision = await store.revision()
        # 与 console_api 共享同一 live dict: 热开关/TTL 热改两边即时可见。
        _ca.set_config(resolved)
        _ca.set_config_db(store, revision)
        logger.info(
            "Loaded config from %s: %d upstreams, %d tools, revision=%d, template=%s",
            self._db_path,
            len(resolved.get("upstreams") or {}),
            len(resolved.get("tools") or []),
            revision,
            (await store.template_version_seeded() or "")[:12],
        )

    async def _do_initialize(self):
        logger.info("Initializing UniHive Gateway Server...")

        # console_api 是 set_omni_client 的目标。__init__ 里也 import 了
        # _ca, 那是给 set_config 用的; 这个 import 是给 _find_omni_client()
        # 之后的 _ca.set_omni_client() 用, 必须在 _do_initialize 局部可见。
        from ..utils import console_api as _ca

        try:
            # v2 配置实体库: 必须在 cache/client 构造前完成装配,
            # upstream.enabled 决定上游是否建连。
            await self._load_db_config()

            # 初始化缓存
            cache_cfg = self.config.get("cache", {})
            self.cache = cache_mod.Cache(CacheConfig(
                enabled=cache_cfg.get("enabled", True),
                db_path=str(resolve_project_path(
                    cache_cfg.get("db_path"), "logs/cache.db"
                )),
                max_entries=int(cache_cfg.get("max_entries", 50000)),
                operation_timeout=float(cache_cfg.get("operation_timeout", 3.0)),
            ))
            await self.cache.initialize()

            # 启动周期 cache 清理任务 (2026-09-15 MCP 二次审视 M4):
            # 间隔 5 min, 等价于把 LRU 上界从「50000 条随时混过期」
            # 收紧到「过期条目最迟 5 min 内回收」。任务在 stop() 取消。
            self._cache_cleanup_task = asyncio.create_task(
                self._cache_cleanup_loop(),
                name="unihive-cache-cleanup",
            )

            # 初始化上游客户端
            # 用 `or {}` 归一化, 防 YAML `upstreams: null` 让 .items() 抛 AttributeError (HIGH #2)
            # 构造/启动拆成 _construct_upstream_client / _start_upstream_client,
            # 供上游启用/禁用热开关复用 (2026-09-18)。
            for name, cfg in (self.config.get("upstreams") or {}).items():
                if not cfg.get("enabled", False):
                    continue
                # H2 (round 9 audit): 单个上游构造/启动抛错 (如 mootdx2 非法
                # servers、tdx_quant 缺 DLL) 不得让整个网关拒启 —— 与
                # _start_upstream_client 已有的 "失败也登记为不可用" 语义一致,
                # 这里把构造也纳入 try, 该上游跳过 + log, 其余照常启动。
                # CancelledError (BaseException) 仍传播, 不被吞。
                try:
                    client = self._construct_upstream_client(name, cfg)
                    await self._start_upstream_client(name, cfg, client)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        "Failed to construct/start upstream %r (skipping, "
                        "other upstreams continue): %s",
                        name,
                        e,
                    )

            # 2026-09-15: 让 console_api 能查到已注册的 upstream client,
            # 服务 /api/upstreams/<name>/metrics 端点。必须在装配循环结束、
            # HTTP server 启动前注入 (HTTP server 启动前不会有请求, 安全)。
            _ca.set_upstream_registry(self.upstreams)
            # 2026-09-18: 注入 server 自身, 供 /api/config/upstreams/<name>/enabled
            # 热开关调用 set_upstream_enabled()。stdio 模式下 console_api 无路由,
            # 注入无副作用。
            _ca.set_gateway_server(self)

            # Inject OmniClient into console_api
            _omni = self._find_omni_client()
            _ca.set_omni_client(_omni)

            # 全量 spec (db 模式来自库内实体; yaml 模式此处合入生成文件并
            # 回填顶层 mapping), 存全量供热开关重算发布集。
            tool_specs = self._load_all_tools()
            # 存全量 spec 供热开关重算发布集; validate/注册逻辑不变。
            self.tool_specs = tool_specs

            # 2026-09-14: ProxySettings — 读 config/proxy_settings.yaml,
            # 控制 mootdx2 → tdx_quant 透明代理. yaml 不存在时自动 fallback
            # 到默认 (enabled=False), 不阻断 gateway 启动.
            proxy_settings = ProxySettings(
                path=str(resolve_project_path(
                    None, "config/proxy_settings.yaml"
                )),
            )

            # 初始化路由
            self.router = Router(
                upstreams=self.upstreams,
                routing_config=self.config.get("routing", {}),
                upstream_tool_mapping=self.config.get("upstream_tool_mapping", {}),
                per_upstream_timeout=getattr(self, "_per_upstream_timeout", None),
                proxy_settings=proxy_settings,
                tool_specs=tool_specs,
            )

            # Store proxy_settings on self for proxy_api handlers to mutate later
            self.proxy_settings = proxy_settings

            # Configure proxy_api with module-level state (so it doesn't need app.state)
            from ..utils.proxy_api import configure_proxy_api
            configure_proxy_api(proxy_settings, self.upstreams)

            # 初始化 MCP Server（传入已加载的 specs，避免重复 _load_all_tools() 调用）
            # 经 _create_and_register_gateway_mcp 工厂走, FastMCP 构造 + tools
            # 注册 + capability filter 安装一气呵成, 防止未来 refactor 跳过 filter。
            await self._create_and_register_gateway_mcp(tool_specs)

        except Exception:
            # LOW6: 改为 except Exception, 不再吃 CancelledError / KeyboardInterrupt /
            # SystemExit. CancelledError 自 Py3.8 起是 BaseException 子类
            # (而非 Exception), 它应直接传播让 asyncio.run() 处理取消;
            # KeyboardInterrupt 同理. 这里只清理真正的 init 失败。
            # M2 (round 11): 先取消 late-start 自愈任务, 再 stop client。
            await self._cancel_late_starts()
            for client in self.upstreams.values():
                try:
                    await asyncio.wait_for(client.stop(), timeout=2.0)
                except Exception:
                    pass
            self.upstreams.clear()
            # M4 (round 9 audit): 取消半建的 cache cleanup task, 否则它引用
            # 即将关闭的 cache 且重试时会泄漏第二个 task。
            cleanup_task = getattr(self, "_cache_cleanup_task", None)
            if cleanup_task is not None:
                cleanup_task.cancel()
                try:
                    await cleanup_task
                except (asyncio.CancelledError, Exception):
                    pass
                self._cache_cleanup_task = None
            if self.cache:
                try:
                    await asyncio.wait_for(self.cache.close(), timeout=2.0)
                except Exception:
                    pass
                self.cache = None
            # M4: v2 配置库 engine 也须 dispose, 否则直接 await initialize()
            # 的嵌入宿主/同实例重试会泄漏第二个 SQLAlchemy engine。
            config_store = getattr(self, "config_store", None)
            if config_store is not None:
                try:
                    await config_store.close()
                except Exception:
                    pass
                self.config_store = None
            # M4: 复位 console_api 写目标全局, 不再指向已死 server 的 store/config。
            _ca.set_config_db(None)
            _ca.set_config({})
            _ca.set_gateway_server(None)
            _ca.set_upstream_registry({})
            _ca.set_omni_client(None)
            # M1 (2026-09-07 audit): 半构造的 router / mcp 也归零, 否则后续
            # get_server_status / 路由调用拿到指向未完全初始化对象的引用。
            self.router = None
            self.mcp = None
            raise

        self._initialized = True
        logger.info(f"Gateway initialized with {len(self.upstreams)} upstreams")

    async def start(self):
        # M5 (2026-09-07 5th-round audit): initialize() 失败时重置 _running 并
        # 调 stop() 清 partial state, 允许同实例重试。Pre-fix: _running 在
        # initialize 之前设 True, 失败时留在 True, 重试 start() 会状态错位。
        self._running = True
        # L8 (round 9 audit): stop() 末尾 set _shutdown_event 唤醒旧 watcher,
        # 但从不 clear → 同实例 stdio restart 时新 watcher `await event.wait()`
        # 立即返回, cancel 当前 main_task → run_stdio_async 没跑就被杀。
        # 建 watcher 前清掉, 让残留 set 不影响重启。
        self._shutdown_event.clear()
        try:
            await self.initialize()
        except BaseException:
            await self.stop()
            raise
        # 注册 SIGINT/SIGTERM handler: stdio transport 模式下, 父进程 (Claude
        # Desktop 等) 关停会发 SIGTERM, Python 默认 handler 不通知 asyncio,
        # 我们的 stop() 不会被调用, stdio MCP 子进程泄漏。
        # Windows 的 asyncio 完全不支持 add_signal_handler (会抛 NotImplementedError),
        # 那里只能靠父进程行为 + finally 兜底; Unix 上正常注册。
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM, self._shutdown_event.set)
            loop.add_signal_handler(signal.SIGINT, self._shutdown_event.set)
        # M4 (2026-09-07 5th-round audit): signal handler 只 set _shutdown_event,
        # 但 run_stdio_async 不 watch 它 (MCP stdio 默认依赖 stdin close 触发关闭)。
        # 之前的注释说 "handler 修了泄漏" 是误导 — 实际上 stop() 仍然不会被调,
        # 子进程要等 stdin 关才退出, 期间 health_task / upstreams / cache 全部泄漏。
        # 加 _signal_watcher: 等 _shutdown_event, 触发后 cancel 当前 main_task,
        # 让 run_stdio_async 抛 CancelledError, outer finally 调 stop()。
        main_task = asyncio.current_task()

        async def _signal_watcher():
            await self._shutdown_event.wait()
            if main_task is not None and not main_task.done():
                main_task.cancel()

        _watcher = asyncio.create_task(_signal_watcher())
        try:
            try:
                await self.mcp.run_stdio_async()
            finally:
                _watcher.cancel()
                try:
                    await _watcher
                except (asyncio.CancelledError,):
                    pass
        finally:
            # 任何退出路径都跑 cleanup, 包括 KeyboardInterrupt / SIGTERM /
            # _signal_watcher 触发的 cancel。
            await self.stop()

    async def stop(self):
        """优雅关闭：等待在途请求完成后关闭连接池"""
        self._running = False
        self._shutdown_event.set()  # 唤醒 stdio signal watcher

        # Reset console_api state
        # _ca 在 stop() 局部 import, 与 _do_initialize / serve_http 各 import
        # 一次保持一致 (避免隐式依赖 __init__ 的局部变量)。
        from ..utils import console_api as _ca
        _ca.set_omni_client(None)
        # 2026-09-18: 摘掉热开关回调目标, 防止 stop 后 /api 仍调到死 server
        _ca.set_gateway_server(None)
        # v2: 摘掉 config.db 写目标与实体模式标记, 支持同实例 stop/start
        _ca.set_config_db(None)
        # LOW-1 (2026-09-14 audit): 显式关掉 probe executor, 不依赖 atexit
        # (stdio transport 下 atexit 不保证触发)。
        _ca._shutdown_probe_executor()

        # 等待在途请求完成（最多 10 秒）
        wait_start = time.time()
        max_wait = 10.0
        while True:
            async with self._requests_lock:
                active = self._active_requests
            if active == 0:
                logger.info("All in-flight requests completed, proceeding with shutdown")
                break
            if time.time() - wait_start > max_wait:
                logger.warning(f"Timeout waiting for {active} in-flight requests, force-exit")
                break
            logger.info(f"Waiting for {active} in-flight requests to complete...")
            await asyncio.sleep(0.5)

        # 与热开关互斥: 否则禁用/启用可能在 gather 快照后才 pop/登记 client,
        # 新 client 被最后的 upstreams.clear() 丢弃而未 stop (httpx/子进程泄漏)。
        # 锁内协程若在关停止于 _start_upstream_client, 等其结束后一并 gather。
        # 与热开关互斥到 upstreams 清空: 禁用/启用可能在 gather 快照后才
        # pop/登记 client, 新 client 若不被 gather 覆盖就会被 clear() 丢弃
        # 而未 stop (httpx/子进程泄漏)。锁内 toggle 若止于 _start, 此处一并关。
        toggle_lock = getattr(self, "_upstream_toggle_lock", None)
        async with toggle_lock if toggle_lock is not None else contextlib.nullcontext():
            # M2 (round 11): 先取消全部 late-start 自愈任务, 再关 client —
            # 必须在 client.stop() 之前, 防止已停 client 被晚到任务复活。
            await self._cancel_late_starts()
            # 关闭所有上游客户端 (L1, 2026-09-07 5th-round audit): 并行 gather
            # 替代顺序循环, 总关闭时间从 O(N) 降到 O(1)。
            if self.upstreams:
                await asyncio.gather(
                    *[self._stop_one_upstream(name, client)
                      for name, client in list(self.upstreams.items())],
                    return_exceptions=True,
                )

            # 关闭阻塞上游调用专用线程池 (2026-09-15 MCP 二次审视 M2):
            # 在 upstream 客户端之后关闭, 避免它们还在 await run_in_executor 时被强杀。
            # shutdown_blocking_executor 是模块级单例, 显式调用避免 atexit 依赖 (stdio 下不可靠)。
            from ..core.blocking_pool import shutdown_blocking_executor
            shutdown_blocking_executor()

            # 关闭缓存
            if self.cache:
                try:
                    await asyncio.wait_for(self.cache.close(), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning("cache close() timed out after 2s")
                self.cache = None

            # 关闭 SQLite 配置覆盖层连接池
            # getattr 兜底: 测试用 __new__ 跳过 __init__ 时字段不存在
            config_store = getattr(self, "config_store", None)
            if config_store is not None:
                await config_store.close()
                self.config_store = None

            # 取消周期 cache 清理任务 (2026-09-15 MCP 二次审视 M4)。
            # cache.close() 之后做, 确保最后一次 tick 不会与 close 抢锁。
            # 用 getattr 兜底: 测试用 __new__ 跳过 __init__ 时字段不存在。
            cleanup_task = getattr(self, "_cache_cleanup_task", None)
            if cleanup_task is not None:
                cleanup_task.cancel()
                try:
                    await cleanup_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"cache cleanup task ended with error: {e}")
                self._cache_cleanup_task = None

            # 重置状态以支持同实例 restart (HIGH B1, 2026-09-07 audit):
            # 不清掉的话, 第二次 initialize() 会因 _initialized=True 短路,
            # 用已经 stop 过的死 upstreams / router / mcp 路由 → 全程失败。
            # 生产路径通常 stop 后进程退出, 重置是无害的; stop/start 循环
            # (supervisor 重启 / 热重载配置) 则依赖这些归零。
            self.upstreams.clear()
        self.router = None
        self.mcp = None
        # 热开关状态一并归零, 支持同实例 stop/start 循环
        self._published_tools = set()
        self.tool_specs = []
        self._db_hidden_tools = set()
        self._initialized = False

        # M1 (2026-09-15 MCP 二次审视): 重置全局限流器单例，支持 stop/start 循环
        from ..core.rate_limiter import reset_default_limiter
        reset_default_limiter()

        # L1: 重置 proxy_api 模块级全局状态
        from ..utils.proxy_api import reset_proxy_api
        reset_proxy_api()

        logger.info("Gateway shutdown complete")

    async def _stop_one_upstream(self, name: str, client):
        try:
            await asyncio.wait_for(client.stop(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"[{name}] stop() timed out after 5s, force-exit")
        except Exception as e:
            logger.warning(f"[{name}] stop() raised: {e}")

    # 2026-09-15 MCP 二次审视 M4: 周期 cache 清理。间隔 5 min — 在清理频率
    # 与 CPU 开销间取中。Cache.cleanup_expired() 内部已包 operation_timeout,
    # 不会因一次清理卡住 loop。
    _CACHE_CLEANUP_INTERVAL_SEC = 300

    async def _cache_cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._CACHE_CLEANUP_INTERVAL_SEC)
                if self.cache is None:
                    return
                try:
                    removed = await self.cache.cleanup_expired()
                    if removed:
                        logger.debug(f"Cache cleanup removed {removed} expired/LRU entries")
                except Exception as e:
                    logger.warning(f"Cache cleanup tick failed: {e}")
        except asyncio.CancelledError:
            return
