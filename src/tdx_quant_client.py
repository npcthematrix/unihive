"""TdxQuant 上游客户端。

通过进程内 singleton tq 实例调用 tqcenter.py，与运行中的 TdxW.exe
通过 DLL IPC 通信。结构对齐 mootdx2_client.py。

生命周期：
- start(): sys.path.insert + import tqcenter + tq() + initialize()
- 失败不抛，标记 UNAVAILABLE，gateway 继续启动其他上游
- 后台 _health_loop 周期探活，触发 _reconnect
- stop(): 取消探活任务 + tq.close()
"""
import asyncio
import logging
import sys
import time
from typing import Any, Optional

from .tdx_quant_config import TdxQuantConfig
from .tdx_quant_errors import (
    TdxQuantErrorType,
    classify_exception,
    translate_errorid,
)
from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


class TdxQuantClient:
    """进程内直调 tqcenter.py 的上游客户端。"""

    def __init__(self, config: TdxQuantConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._tq: Any = None
        self._health_task: Optional[asyncio.Task] = None
        self._reconnect_lock = asyncio.Lock()
        self._fail_count = 0
        self._strategy_id = config.settings.strategy_id or __file__

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == UpstreamStatus.HEALTHY

    async def start(self) -> bool:
        """加载 tqcenter + initialize + 启动探活任务。

        Returns:
            True: 初始化成功，status=HEALTHY
            False: 初始化失败，status=UNAVAILABLE（不抛，gateway 继续）
        """
        try:
            tqcenter_dir = str(self.config.tqcenter_dir)
            if tqcenter_dir and tqcenter_dir not in sys.path:
                sys.path.insert(0, tqcenter_dir)
            import tqcenter  # noqa: F401
            self._tq = tqcenter.tq()
            self._tq.initialize(self._strategy_id)
            self._status = UpstreamStatus.HEALTHY
            self._health_task = asyncio.create_task(self._health_loop())
            logger.info(f"[{self.name}] initialized (strategy_id={self._strategy_id})")
            return True
        except (FileNotFoundError, ModuleNotFoundError) as e:
            logger.error(f"[{self.name}] init failed: {e}")
            self._status = UpstreamStatus.UNAVAILABLE
            return False
        except Exception as e:
            # 含 ErrorId='12' 同名策略：仅警告，仍标记 HEALTHY
            logger.warning(f"[{self.name}] initialize warning: {e}")
            self._status = UpstreamStatus.HEALTHY
            self._health_task = asyncio.create_task(self._health_loop())
            return True

    async def stop(self):
        """取消探活任务 + tq.close()。"""
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        if self._tq:
            try:
                self._tq.close()
            except Exception as e:
                logger.warning(f"[{self.name}] close warning: {e}")
        self._tq = None
        self._status = UpstreamStatus.UNAVAILABLE

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """调用 tq.{tool_name}(**arguments)。"""
        start_time = time.time()
        if self._status != UpstreamStatus.HEALTHY or self._tq is None:
            return ToolResult(
                success=False,
                error=f"[{self.name}] upstream unavailable (status={self._status.value})",
                source=self.name,
                duration_ms=0,
            )

        try:
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: getattr(self._tq, tool_name)(**arguments),
                ),
                timeout=self.config.settings.call_timeout_sec,
            )
            duration_ms = int((time.time() - start_time) * 1000)

            err = translate_errorid(result)
            if err is None:
                return ToolResult(
                    success=True,
                    data=result,
                    source=self.name,
                    duration_ms=duration_ms,
                )
            if err.error_type == TdxQuantErrorType.DISCONNECTED:
                asyncio.create_task(self._reconnect())
            return ToolResult(
                success=False,
                error=err.message,
                source=self.name,
                duration_ms=duration_ms,
            )
        except asyncio.TimeoutError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            err = classify_exception(e)
            return ToolResult(
                success=False,
                error=err.message,
                source=self.name,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            err = classify_exception(e)
            if err.error_type == TdxQuantErrorType.UPSTREAM_UNAVAILABLE:
                asyncio.create_task(self._reconnect())
            logger.error(f"[{self.name}] call {tool_name} failed: {e}")
            return ToolResult(
                success=False,
                error=err.message,
                source=self.name,
                duration_ms=duration_ms,
            )

    async def list_tools(self) -> list[dict]:
        return []

    async def health_check(self) -> bool:
        """对外暴露的同步探活（用于上游状态查询）。"""
        return self._status == UpstreamStatus.HEALTHY

    async def _health_loop(self):
        """后台探活：周期性调 tq.get_user_sector()。"""
        interval = self.config.settings.health_check_interval_sec
        while self._status != UpstreamStatus.UNAVAILABLE:
            await asyncio.sleep(interval)
            try:
                result = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None, lambda: self._tq.get_user_sector()
                    ),
                    timeout=5.0,
                )
                err = translate_errorid(result)
                if err and err.error_type == TdxQuantErrorType.DISCONNECTED:
                    self._fail_count += 1
                    if self._fail_count >= self.config.settings.reconnect_threshold:
                        await self._reconnect()
                    if self._fail_count >= self.config.settings.unavailable_threshold:
                        self._status = UpstreamStatus.UNAVAILABLE
                        logger.error(
                            f"[{self.name}] marked UNAVAILABLE after {self._fail_count} failures"
                        )
                        return
                else:
                    self._fail_count = 0
                    self._status = UpstreamStatus.HEALTHY
            except (asyncio.TimeoutError, Exception):
                self._fail_count += 1
                if self._fail_count >= self.config.settings.reconnect_threshold:
                    await self._reconnect()
                if self._fail_count >= self.config.settings.unavailable_threshold:
                    self._status = UpstreamStatus.UNAVAILABLE
                    logger.error(
                        f"[{self.name}] marked UNAVAILABLE after {self._fail_count} failures"
                    )
                    return

    async def _reconnect(self):
        """重连：tq.close() + tq.initialize()，asyncio.Lock 串行化。"""
        async with self._reconnect_lock:
            try:
                if self._tq:
                    self._tq.close()
            except Exception as e:
                logger.warning(f"[{self.name}] close during reconnect: {e}")
            try:
                self._tq.initialize(self._strategy_id)
                self._fail_count = 0
                self._status = UpstreamStatus.HEALTHY
                logger.info(f"[{self.name}] reconnected")
            except Exception as e:
                self._status = UpstreamStatus.DEGRADED
                logger.warning(f"[{self.name}] reconnect failed: {e}")
