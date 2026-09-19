"""UpstreamControlMixin — 上游 client 启动等待与超时后台自愈 (round 11 M2)。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
跨簇调用经 ``self``; 阻塞型 client 的 late-start 任务字典在 ``__init__`` 建。
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


class UpstreamControlMixin:
    """上游启动/late-start 方法簇 (GatewayServer 组装时混入)。"""

    async def _start_upstream_client(self, name: str, cfg: dict, client) -> bool:
        """启动已构造的 client 并登记进 self.upstreams, 返回 is_available。

        复刻原启动循环语义: 单 client 超时/异常不抛出 (CancelledError 除外),
        client 仍登记进 self.upstreams 供健康检查与 stop() 清理; ths_local_gateway
        保留原特例 (不走 wait_for 超时)。
        """
        if cfg.get("type") == "ths_local_gateway":
            try:
                await client.start()
            except Exception as e:
                logger.error(f"[{name}] Failed to start: {e}")
            self.upstreams[name] = client
            connected = bool(client.is_available)
            logger.info(f"[{name}] {'Connected' if connected else 'Failed'}")
            return connected

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
            return False
        except Exception as e:
            # 非超时异常 (FileNotFoundError / OSError / 等) 同样登记 client,
            # 让 stop() 能关掉 subprocess/httpx 连接。
            logger.error(
                f"[{name}] upstream start() raised {type(e).__name__}: {e}; "
                f"marking unavailable"
            )
            self.upstreams[name] = client
            return False
        self.upstreams[name] = client
        connected = bool(client.is_available)
        logger.info(f"[{name}] {'Connected' if connected else 'Failed'}")
        return connected

    # ------------------------------------------------------------------
    # M2 (round 11): 启动超时后的后台自愈
    # ------------------------------------------------------------------

    def _spawn_late_start(self, name: str, client) -> None:
        """为支持 start_after_timeout 的阻塞型 client 挂后台自愈任务。

        无该钩子的 client (fuyao/omni/stdio/ths_local 等, 启动探测本身
        有界或不走 wait_for 特例) 不挂。
        """
        if not hasattr(client, "start_after_timeout"):
            return
        task = asyncio.create_task(
            self._late_start_once(name, client),
            name=f"unihive-late-start-{name}",
        )
        self._late_start_tasks.setdefault(name, set()).add(task)
        task.add_done_callback(
            lambda t, n=name: self._late_start_tasks.get(n, set()).discard(t)
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
            selected: list[asyncio.Task] = list(groups.get(name, set()))
        else:
            selected = [t for tasks in groups.values() for t in tasks]
        if not selected:
            return
        for task in selected:
            if not task.done():
                task.cancel()
        await asyncio.gather(*selected, return_exceptions=True)
