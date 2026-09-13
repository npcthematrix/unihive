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
import uuid
from pathlib import Path
from typing import Any, Optional

from ..models.tdx_quant_config import TdxQuantConfig
from ..exceptions.tdx_quant_errors import (
    TdxQuantErrorType,
    classify_exception,
    translate_errorid,
)
from .upstream_client import ToolResult, UpstreamStatus
from ..core.blocking_pool import get_blocking_executor

logger = logging.getLogger(__name__)

# strategy_id 持久化路径: 让 UUID 在进程重启间保持稳定, TdxW.exe 才能跨
# 重启保留该 strategy 的状态 (持仓/自选股等)。
_STRATEGY_ID_FILE = Path("logs/tdx_quant_strategy_id")


def _resolve_strategy_id(configured: str) -> str:
    """LOW7: 配置优先 → 持久化 UUID → 新生成 UUID.

    不再用 __file__ 兜底 (路径既不稳定又暴露部署信息)。
    """
    if configured:
        return configured
    try:
        if _STRATEGY_ID_FILE.exists():
            return _STRATEGY_ID_FILE.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning(f"read strategy_id file failed: {e}, generating new UUID")
    new_id = str(uuid.uuid4())
    try:
        _STRATEGY_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STRATEGY_ID_FILE.write_text(new_id, encoding="utf-8")
    except OSError as e:
        logger.warning(f"persist strategy_id failed: {e}")
    return new_id


class TdxQuantClient:
    """进程内直调 tqcenter.py 的上游客户端。"""

    def __init__(self, config: TdxQuantConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._tq: Any = None
        self._health_task: Optional[asyncio.Task] = None
        self._reconnect_lock = asyncio.Lock()
        # MED4: 跟踪所有 in-flight reconnect 任务, stop() 时必须等它们
        # 完成, 防止 tq.close() 与 reconnect 内部的 close+initialize 序列
        # 发生竞态 (reconnect 跑到一半被截断, 留下半初始化状态)。
        self._reconnect_tasks: set[asyncio.Task] = set()
        self._fail_count = 0
        self._strategy_id = _resolve_strategy_id(config.settings.strategy_id)

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        # P1 (2026-09-09 7th-round audit): TdxQuant 的 _reconnect() 失败会
        # 把 status 置 DEGRADED（半恢复状态, 仍能响应 tq.* 调用）。
        # 之前只有 HEALTHY 才 is_available, reconnect 失败期间 router 会
        # 直接跳过, 降级路径走错。改为对齐 UpstreamClient / OmniClient。
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

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
            # tq 是 classmethod 集合, 不要实例化
            self._tq = tqcenter.tq
            # tqcenter.initialize 仅接受 path 参数 (dll_path='')，不支持 strategy_id
            tdx_root = str(self.config.tdx_root_path)
            self._tq.initialize(tdx_root)
            logger.info(f"[{self.name}] TQ strategy_id={self._strategy_id} (记录用, 未传给 DLL)")
            self._status = UpstreamStatus.HEALTHY
            self._health_task = asyncio.create_task(self._health_loop())
            logger.info(f"[{self.name}] initialized (tdx_root={tdx_root}, strategy_id={self._strategy_id})")
            return True
        except (FileNotFoundError, ModuleNotFoundError) as e:
            logger.error(f"[{self.name}] init failed: {e}")
            self._status = UpstreamStatus.UNAVAILABLE
            return False
        except Exception as e:
            # 含 ErrorId='12' 同名策略：仅警告，仍标记 HEALTHY
            err_str = str(e)
            if "ErrorId='12'" in err_str or "同名策略" in err_str:
                logger.warning(f"[{self.name}] initialize warning: {e}")
                self._status = UpstreamStatus.HEALTHY
                self._health_task = asyncio.create_task(self._health_loop())
                return True
            logger.error(f"[{self.name}] initialize failed: {e}")
            self._status = UpstreamStatus.UNAVAILABLE
            return False

    async def stop(self):
        """取消探活任务 + tq.close() (后者是同步 DLL 调用, 必须 run_in_executor)。

        MED4: 先等所有 in-flight _reconnect() 完成, 再 tq.close()。
        否则 reconnect 内部 close+initialize 序列会被 tq.close() 半路截断。
        """
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        # MED4: 等 in-flight reconnect 完成 (最多 5s, 防止挂死的 reconnect
        # 永久阻塞 stop)
        if self._reconnect_tasks:
            pending = list(self._reconnect_tasks)
            logger.info(
                f"[{self.name}] waiting for {len(pending)} in-flight reconnect task(s)"
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{self.name}] reconnect task(s) did not finish in 5s, cancelling"
                )
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        if self._tq:
            try:
                # tq.close() 同步调 DLL, 在主线程跑会阻塞 event loop 0.5s+
                # (HIGH2). executor 跑让其他 in-flight 请求继续调度。
                await asyncio.get_event_loop().run_in_executor(
                    get_blocking_executor(), self._tq.close
                )
            except Exception as e:
                logger.warning(f"[{self.name}] close warning: {e}")
        self._tq = None
        self._status = UpstreamStatus.UNAVAILABLE

    def _schedule_reconnect(self) -> None:
        """MED4: 启动 reconnect 任务并加入 tracking set, 完成后自动从 set 移除."""
        task = asyncio.create_task(self._reconnect())
        self._reconnect_tasks.add(task)
        task.add_done_callback(self._reconnect_tasks.discard)

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
                    get_blocking_executor(),
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
                self._schedule_reconnect()
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
                self._schedule_reconnect()
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
                        get_blocking_executor(), lambda: self._tq.get_user_sector()
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
                    # HIGH2: tq.close() 同步 DLL, 必须 run_in_executor
                    await asyncio.get_event_loop().run_in_executor(
                        get_blocking_executor(), self._tq.close
                    )
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
