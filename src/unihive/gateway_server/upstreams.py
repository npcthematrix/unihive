"""UpstreamControlMixin — 上游 client 启动等待与超时后台自愈 (round 11 M2)。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
跨簇调用经 ``self``; 阻塞型 client 的 late-start 任务字典在 ``__init__`` 建。
"""
import asyncio
import logging

from ..api.fuyao_client import FuyaoClient, FuyaoConfig
from ..api.mootdx2_client import MooTDX2Client, MooTDX2Config
from ..api.omni_client import OmniClient, OmniConfig
from ..api.tdx_quant_client import TdxQuantClient
from ..api.thsdk_client import ThsdkClient
from ..api.upstream_client import UpstreamClient, UpstreamConfig, UpstreamStatus
from ..core.config_db import resolve_project_path
from ..models.tdx_quant_config import (
    TdxQuantConfig,
    TdxQuantSettings,
    clamp_strategy_instances,
)
from ..models.thsdk_config import ThsdkConfig

from ._helpers import _parse_mootdx2_servers

logger = logging.getLogger(__name__)


class UpstreamControlMixin:
    """上游启动/late-start 方法簇 (GatewayServer 组装时混入)。"""

    async def _start_upstream_client(self, name: str, cfg: dict, client) -> bool:
        """启动已构造的 client 并登记进 self.upstreams, 返回 is_available。

        cfg 是历史签名保留 (L7 回滚): 测试 fixture 替换本方法为
        ``fake_start(name, cfg, client)`` 验证取消/回滚路径, 必须保留 3 参
        签名。cfg 仍接收但不读取。
        """
        del cfg  # noqa: F841  保留 2nd positional 以兼容测试 fixture
        try:
            await asyncio.wait_for(
                client.start(), timeout=self._upstream_start_timeout
            )
        except asyncio.CancelledError:
            # CancelledError 是 BaseException, 下面 except Exception 接不住。
            # 先登记 client 防 subprocess / httpx 连接泄漏, 再让取消继续传播。
            logger.error(
                f"[{name}] upstream start() cancelled, registering "
                f"client for cleanup and propagating"
            )
            self.upstreams[name] = client
            raise
        except asyncio.TimeoutError:
            # 单 upstream 超时不中断其他 upstream; 仍登记 (状态由 client 内部标记),
            # get_server_status / stop() 都能看到这个客户端。
            # M2 (round 11): mootdx2/tdx_quant 的阻塞初始化跑在不可中断的线程
            # 池上, factory 预算可达 ~90s > 30s 启动预算 —— 超时只代表"本次
            # boot 等不起", 不代表上游没机会恢复。登记后给支持的 client 挂
            # 后台自愈任务, 让它在阻塞调用返回后完成 probe + 健康循环启动,
            # 不必重启网关; disable/stop 会取消该任务。
            logger.error(
                f"[{name}] upstream start() timed out after "
                f"{self._upstream_start_timeout}s, marking unavailable; "
                f"late recovery will continue in background"
            )
            self.upstreams[name] = client
            self._spawn_late_start(name, client)
            # Round-12 簇I: 没有 late-recovery 钩子的 client (thsdk/fuyao/
            # omni/stdio) start() 被 wait_for cancel 时, 其内部
            # `except Exception` 接不住 CancelledError (Py3.8+ 属
            # BaseException), 状态永远停在初始 UNKNOWN, 控制台长期显示
            # "未知"。本次 boot 已等不起又无后台自愈, 诚实落 UNAVAILABLE;
            # 有钩子的 mootdx2/tdx_quant 保留 UNKNOWN, 等阻塞调用返回后由
            # start_after_timeout 重跑 start() 自行定状态, 不提前判死。
            if not hasattr(client, "start_after_timeout"):
                if getattr(client, "status", None) is UpstreamStatus.UNKNOWN:
                    client._status = UpstreamStatus.UNAVAILABLE
            return False
        except Exception as e:
            # 非超时异常 (FileNotFoundError / OSError / 等) 同样登记 client,
            # 让 stop() 能关掉 subprocess/httpx 连接。
            logger.error(
                f"[{name}] upstream start() raised {type(e).__name__}: {e}; "
                f"marking unavailable"
            )
            self.upstreams[name] = client
            # 异常是 start() 同步快失败 (非超时), 不存在仍在跑的阻塞线程,
            # 即使是带 late-recovery 钩子的 client (mootdx2/tdx_quant) 也没有
            # 后续自愈会来定状态 —— 无条件把 UNKNOWN 收敛为 UNAVAILABLE;
            # 否则状态永久残留 UNKNOWN, aggregate_health 把它计入 degraded
            # 而非 unhealthy, 健康结论被乐观化。
            if getattr(client, "status", None) is UpstreamStatus.UNKNOWN:
                client._status = UpstreamStatus.UNAVAILABLE
            return False
        self.upstreams[name] = client
        connected = bool(client.is_available)
        logger.info(f"[{name}] {'Connected' if connected else 'Failed'}")
        return connected

    def _schedule_background(
        self,
        name: str,
        coro,
        *,
        task_name_prefix: str,
        guard=None,
    ) -> asyncio.Task | None:
        """统一后台任务调度: 创建 + 登记 + done 自清理, _spawn_background_start
        与 _spawn_late_start 共用。

        guard 可选: 返回 False 时不创建任务 (用于 late-start 的
        hasattr(client, "start_after_timeout") 守卫)。

        单 task 容器: 同名重启时新 task 覆盖旧 task, 旧 task 在 stop 时取消。
        """
        if guard is not None and not guard():
            return None
        task = asyncio.create_task(coro, name=f"{task_name_prefix}{name}")
        self._late_start_tasks[name] = task
        task.add_done_callback(
            lambda t, n=name: self._late_start_tasks.pop(n, None)
        )
        return task

    def _spawn_background_start(self, name: str, cfg: dict, client) -> None:
        """boot 期 fire-and-forget: 后台跑 client.start(), 不阻塞网关启动。

        cfg 是历史签名保留 (L7 回滚: 与 _start_upstream_client 对称, 3 参)。
        内部未读取 (不再依赖 cfg["type"]), 保留仅为 fixture 兼容。

        client 已由调用方登记进 self.upstreams (启动中态)。start() 内部自带
        call_timeout, 故这里不再套 wait_for 启动预算; 完成/失败由 client 自行
        标记状态。任务异常必须可见 (done_callback 记录), 不得静默丢失。
        """
        del cfg  # noqa: F841  保留 2nd positional 以兼容测试 fixture
        self._schedule_background(
            name,
            self._background_start_once(name, client),
            task_name_prefix="unihive-bg-start-",
        )

    async def _background_start_once(self, name: str, client) -> None:
        try:
            connected = await client.start()
            logger.info(
                "[%s] background start finished: connected=%s",
                name,
                bool(connected),
            )
        except asyncio.CancelledError:
            logger.debug("[%s] background start cancelled", name)
            raise
        except Exception as e:  # noqa: BLE001 - 后台失败只告警, 不炸网关
            logger.warning(
                "[%s] background start failed: %s: %s",
                name,
                type(e).__name__,
                e,
            )
            # start() 抛异常时诚实落 UNAVAILABLE, 不留 UNKNOWN 让健康结论虚高。
            if getattr(client, "status", None) is UpstreamStatus.UNKNOWN:
                client._status = UpstreamStatus.UNAVAILABLE

    # ------------------------------------------------------------------
    # M2 (round 11): 启动超时后的后台自愈
    # ------------------------------------------------------------------

    def _spawn_late_start(self, name: str, client) -> None:
        """为支持 start_after_timeout 的阻塞型 client 挂后台自愈任务。

        无该钩子的 client (fuyao/omni/stdio 等, 启动探测本身
        有界或不走 wait_for 特例) 不挂。同名 task 会被新 task 覆盖 (旧 task
        在 stop() 时一并取消即可)。
        """
        self._schedule_background(
            name,
            self._late_start_once(name, client),
            task_name_prefix="unihive-late-start-",
            guard=lambda: hasattr(client, "start_after_timeout"),
        )

    async def _late_start_once(self, name: str, client) -> None:
        try:
            await client.start_after_timeout()
            logger.info(
                "[%s] late recovery after start-timeout finished: available=%s",
                name,
                bool(getattr(client, "is_available", False)),
            )
        except asyncio.CancelledError:
            logger.debug("[%s] late recovery cancelled", name)
            raise
        except Exception as e:  # noqa: BLE001 - 自愈失败只告警, 不炸后台
            logger.warning(
                "[%s] late recovery failed: %s: %s",
                name,
                type(e).__name__,
                e,
            )

    async def _cancel_late_starts(self, name: str | None = None) -> None:
        """取消在途 late-start 任务。name=None 取消全部 (stop / init 失败)。

        client.stop() 必须在取消之后调: 已取消的 start 续体不会再建新健康
        循环, 配合 client 的 _stopped 位杜绝复活。
        """
        groups = getattr(self, "_late_start_tasks", None)
        if not groups:
            return
        if name is not None:
            selected = [groups.pop(name)] if name in groups else []
        else:
            selected = list(groups.values())
            groups.clear()
        if not selected:
            return
        for task in selected:
            if not task.done():
                task.cancel()
        await asyncio.gather(*selected, return_exceptions=True)

    # ------------------------------------------------------------------
    # 按 cfg.type 构造上游 client (不启动)。启动循环与热启用共用。
    # ------------------------------------------------------------------

    def _construct_upstream_client(self, name: str, cfg: dict):
        """按 cfg.type 构造 (但不启动) 一个上游 client。

        启动循环与热启用共用, 避免两处 client 构造参数漂移。
        """
        if cfg.get("type") == "http":
            fuyao_cfg = FuyaoConfig(
                name=name,
                base_url=cfg["base_url"],
                api_key=cfg.get("api_key", ""),
                timeout_seconds=cfg.get("timeout_seconds", 30),
                max_retry=cfg.get("retry", {}).get("max_attempts", 3),
            )
            return FuyaoClient(fuyao_cfg)
        if cfg.get("type") == "tdx_quant":
            tdx_quant_settings = TdxQuantSettings(
                tdx_root=cfg.get("tdx_root", ""),
                strategy_id=cfg.get("strategy_id", ""),
                health_check_interval_sec=cfg.get("health_check_interval_sec", 60),
                call_timeout_sec=cfg.get("call_timeout_sec", 10),
                call_limit=max(1, int(cfg.get("call_limit", 2) or 2)),
                strategy_instances=clamp_strategy_instances(
                    cfg.get("strategy_instances")
                ),
                reconnect_threshold=cfg.get("reconnect_threshold", 3),
                unavailable_threshold=cfg.get("unavailable_threshold", 10),
                unavailable_cooldown_sec=max(
                    0, int(cfg.get("unavailable_cooldown_sec", 30) or 0)
                ),
                # L3 (round 9 audit): 透传 tdx_remote_port, 否则 YAML 里配的非
                # 默认端口被静默丢弃, 运行时永远连默认 17709。
                tdx_remote_port=int(cfg.get("tdx_remote_port", 17709)),
            )
            tdx_quant_cfg = TdxQuantConfig(
                name=name,
                market=cfg.get("market", "std"),
                settings=tdx_quant_settings,
            )
            return TdxQuantClient(tdx_quant_cfg)
        if cfg.get("type") == "mootdx2":
            from ..models.mootdx2_config import MooTDX2Settings

            # tdxdir 为空时让 mootdx2 自动探测 C:/new_tdx
            pinned_servers = _parse_mootdx2_servers(cfg.get("servers") or [])
            mootdx2_settings = MooTDX2Settings(
                market=cfg.get("market", "std"),
                tdxdir=cfg.get("tdxdir", ""),
                servers=pinned_servers,
                # 指定了服务器就关闭 bestip 全网测速, 固定连配置的 IP
                auto_select_fastest_server=not pinned_servers,
                connect_timeout_seconds=cfg.get("connect_timeout_seconds", 3),
                read_timeout_seconds=cfg.get("read_timeout_seconds", 5),
                # Reader 闸门容量: cfg 直传, 走 from_dict 内部钳到 ≥1
                reader_limit=cfg.get("reader_limit"),
            )
            mootdx2_cfg = MooTDX2Config(
                name=name,
                market=cfg.get("market", "std"),
                settings=mootdx2_settings,
            )
            return MooTDX2Client(mootdx2_cfg)
        if cfg.get("type") == "omni":
            omni_cfg = OmniConfig(
                name=name,
                db_path=str(resolve_project_path(
                    cfg.get("db_path"), "./data/board.db"
                )),
            )
            return OmniClient(omni_cfg)
        if cfg.get("type") == "thsdk":
            # 凭证只从环境变量读; call_timeout 在 from_dict 内钳制进路由预算,
            # 防 config.db 旧 30s 值反超路由 10s 致单 worker 僵尸。
            thsdk_cfg = ThsdkConfig.from_dict(name, cfg, self._per_upstream_timeout)
            return ThsdkClient(thsdk_cfg)
        upstream_type = cfg.get("type")
        if upstream_type is None:
            # 历史契约: 缺 type 键 = 通用 stdio MCP 扩展点 (UpstreamClient)。
            # validate_config 在 strict 模式仍会报 "missing 'type'";
            # 这里给非 strict / 旧配置保留默认, 也让启动失败的 client 能被
            # 登记进 upstreams 走 stop() 清理 (见 test_startup_init_failure)。
            upstream_type = "stdio"
        if upstream_type != "stdio":
            # Round-12 簇E: 显式未知 type 以前静默落到 stdio 分支, 构造出
            # 永远启动不了的客户端, 延迟到调用期才以费解错误爆炸。构造期
            # fail-fast 并列出合法值。
            valid_types = (
                "http", "mootdx2", "tdx_quant", "omni",
                "thsdk", "stdio",
            )
            raise ValueError(
                f"upstream {name!r}: 未知 type={cfg.get('type')!r}; "
                f"合法值: {', '.join(valid_types)}"
            )
        # 通用 stdio MCP 客户端 (自定义 MCP server 扩展点)
        upstream_cfg = UpstreamConfig(
            name=name,
            enabled=True,
            package=cfg.get("package", ""),
            command=cfg.get("command", []),
            env=cfg.get("env", {}),
            timeout_seconds=cfg.get("timeout_seconds", 30),
            max_retry=cfg.get("retry", {}).get("max_attempts", 3),
            backoff_base=cfg.get("retry", {}).get("backoff_base", 2),
        )
        return UpstreamClient(upstream_cfg)
