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
import socket
import sys
import threading
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
from ..core.blocking_pool import get_tdx_quant_executor
from ..core.config_db import resolve_project_path

logger = logging.getLogger(__name__)

# strategy_id 持久化路径: 让 UUID 在进程重启间保持稳定, TdxW.exe 才能跨
# 重启保留该 strategy 的状态 (持仓/自选股等)。
# M9 (round 9 audit): 锚定到项目根, 否则从非 repo 根启动会生成新 UUID
# (文件在 CWD 下), churn 破坏 TdxW.exe 跨重启连续性。
_STRATEGY_ID_FILE = resolve_project_path(None, "logs/tdx_quant_strategy_id")


def _do_probe_port(host: str, port: int, timeout: float) -> bool:
    """socket.create_connection 短超时探测, 失败返 False。

    同步底层, 由 sync 调用方 (get_metrics) 或经 run_in_executor 包装 (async
    调用方) 复用。OSError 之外的所有异常被视为不可恢复 bug, 向上传播。
    """
    sock: Optional[socket.socket] = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        return True
    except OSError:
        return False
    finally:
        if sock is not None:
            sock.close()


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


#: 单标的参数名 —— 这些位置出现空串时 tqcenter 会先 ``tq.close()`` 再抛错。
_CODE_ARG_NAMES: tuple[str, ...] = (
    "stock_code", "code", "codes", "zs_code", "block_code",
)


def _invalid_input_error(arguments: dict[str, Any]) -> str | None:
    """拦截「空代码」入参, 返回错误文案; 合法则返回 None。

    为什么必须在网关侧拦: tqcenter 的数据方法在入参校验失败时走的是
    ``cls.close()`` + raise (2026-11-01 实测源码 19 处), 而 ``tq`` 是**进程级
    单例**, ``close()`` 关的是所有请求共用的那一条 DLL 连接。一次
    ``get_market_data(stock_list=[])`` 就能把并发中的其它请求一起掐断, 之后
    只能靠下一次调用的 ``_auto_initialize`` 重建 (实测每次多付 7.7s)。

    空串/空列表对任何工具都不是合法代码, 判掉没有误伤风险; 有默认值语义的
    工具靠「不传该参数」表达, 不会传空串。
    """
    stock_list = arguments.get("stock_list")
    if isinstance(stock_list, (list, tuple)):
        if not stock_list:
            return "stock_list 不能为空"
        if any(not str(item).strip() for item in stock_list):
            return "stock_list 不能包含空代码"
    elif isinstance(stock_list, str) and not stock_list.strip():
        return "stock_list 不能为空"
    for name in _CODE_ARG_NAMES:
        value = arguments.get(name)
        if isinstance(value, str) and not value.strip():
            return f"{name} 不能为空"
    return None


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
        # M2 (round 11): DLL 串行锁 + 生命周期位。
        # - _dll_lock: start() 超时取消后, 在途 initialize (阻塞池不可中断)
        #   与 late recovery 重入的 initialize 不能并发打进同一 DLL;
        #   close 与 initialize 同样互斥, 复用 _reconnect 的 close→init 序。
        # - _stopped: 阻止已停 client 被晚到的健康循环/late recovery 复活。
        self._dll_lock = threading.Lock()
        self._stopped = False

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

    async def _probe_tdx_remote_port(self) -> bool:
        """探测 TQ 本地端口 (默认 17709) 是否可达。

        17709 是 TdxW.exe 暴露的本地服务端口, 用于 TQ 策略管理器与外部通信。
        注意: 此端口探测只能反映 TdxW.exe 是否运行, 无法验证 TQ 接口是否
        已激活 (需登录后)。

        外层 wait_for 3s 包含入队/调度时间, 内层 socket 超时 2s 由 _do_probe_port 控。
        """
        host = "127.0.0.1"  # 本地探测
        port = self.config.settings.tdx_remote_port
        try:
            return await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    get_tdx_quant_executor(),
                    lambda: _do_probe_port(host, port, 2.0),
                ),
                timeout=3.0,
            )
        except asyncio.TimeoutError as e:
            logger.debug(
                f"[{self.name}] remote TDX port {port} probe timed out: {e}"
            )
            return False

    async def start(self) -> bool:
        """加载 tqcenter + initialize + 启动探活任务。

        2026-09-14 重构 (round 9): 探测 17709 端口 (TQ 本地端口), init 后
        立即调一次轻量接口 (tq.get_user_sector) 验证 TQ 真正可用 (避免
        init 成功但 TdxW.exe 未登录导致请求失败)。

        Returns:
            True: 初始化成功，status=HEALTHY
            False: 初始化失败，status=UNAVAILABLE（不抛，gateway 继续）
        """
        # M2 (round 11): 同实例 stop/start 循环与 late recovery 都经此入口,
        # 先复位停止位, 否则健康循环会被停止位挡住无法重建。
        self._stopped = False
        # 探测 17709 端口连通性 (TQ 本地端口)
        port = self.config.settings.tdx_remote_port
        if not await self._probe_tdx_remote_port():
            logger.warning(
                f"[{self.name}] TQ port {port} unreachable. "
                f"Check: 1) TdxW.exe running and logged in "
                f"2) TQ strategy manager enabled"
            )

        init_success, first_err = await self._try_initialize()
        if not init_success:
            logger.error(
                f"[{self.name}] init failed: {first_err}. "
                f"Check TdxW.exe is running and logged in to行情."
            )
            self._status = UpstreamStatus.UNAVAILABLE
            return False

        # C4 (round 9 audit): init 成功后立即调一次轻量接口, 验证 TQ 真正
        # 可用 (TdxW.exe 已登录 + TQ 接口已激活)。若失败, 降级 DEGRADED
        # 而不是 HEALTHY, 让 router / health_loop 知道实际状态。
        tq_alive = await self._verify_tq_interface()
        if not tq_alive:
            logger.warning(
                f"[{self.name}] TQ interface inactive (port reachable but "
                f"tq.get_user_sector failed). Likely cause: TdxW.exe not "
                f"logged in or TQ strategy manager not enabled. "
                f"status=DEGRADED — health_loop will retry."
            )
            self._status = UpstreamStatus.DEGRADED
        else:
            self._status = UpstreamStatus.HEALTHY

        self._start_health_loop()
        logger.info(
            f"[{self.name}] initialized (tdx_root={self.config.tdx_root_path}, "
            f"strategy_id={self._strategy_id}, status={self._status.value})"
        )
        return True

    def _start_health_loop(self) -> Optional[asyncio.Task]:
        """M2 (round 11): 健康循环统一入口 — stop 后不复活, 重复调用判重,
        返回在跑的 task 引用。"""
        if self._stopped:
            return None
        if self._health_task and not self._health_task.done():
            return self._health_task
        self._health_task = asyncio.create_task(self._health_loop())
        return self._health_task

    async def start_after_timeout(self) -> None:
        """M2 (round 11): 网关启动 wait_for 超时后的后台自愈入口。

        被取消的 start() 可能留下在途 DLL initialize (阻塞池不可中断);
        _dll_lock 把本次 initialize 串到僵尸调用之后, 杜绝并发打进同一
        DLL, 随后正常探活 + 健康循环, UNKNOWN 孤儿无需重启网关即恢复。
        """
        if self._stopped:
            return
        await self.start()

    def _dll_initialize(self, tdx_root: str) -> None:
        """tq.initialize 的持锁包装, 串行化所有 DLL initialize 调用
        (首次初始化 / reconnect / 超时后的 late recovery)。

        Round-12 簇H: stop() 之后晚到的 reconnect/late recovery 不得再打
        进 DLL (stop 的 close 可能已排队在同一把锁后执行)。
        """
        if self._stopped:
            raise RuntimeError(
                f"[{self.name}] client has been stopped; call start() first"
            )
        with self._dll_lock:
            self._tq.initialize(tdx_root)

    def _dll_import_and_initialize(
        self, tdx_root: str, tqcenter_dir: str
    ) -> None:
        """sys.path 注入 + import tqcenter + tq.initialize 一体的持锁同步段。

        Round-12 簇H: 旧实现在事件循环线程做 sys.path.insert + import
        tqcenter —— import 会执行 tqcenter.py 模块体 (ctypes 加载 TdxW
        DLL), 可能冻结 boot 且 wait_for 无法打断。整体移入 unihive-upstream
        阻塞池并持 _dll_lock, 与 reconnect / stop() 的 close 串行。
        必须在 worker thread 经 run_in_executor 调用。
        """
        with self._dll_lock:
            if self._stopped:
                raise RuntimeError(
                    f"[{self.name}] client has been stopped; call start() first"
                )
            if tqcenter_dir and tqcenter_dir not in sys.path:
                sys.path.insert(0, tqcenter_dir)
            import tqcenter
            # tq 是 classmethod 集合, 不要实例化
            self._tq = tqcenter.tq
            tqcenter.tq.initialize(tdx_root)

    def _dll_close(self) -> None:
        """tq.close 的持锁包装, 与 initialize 互斥, 复刻 reconnect 的
        close→init 次序, 不留半初始化窗口。"""
        with self._dll_lock:
            tq = self._tq
            if tq is not None:
                tq.close()

    async def _verify_tq_interface(self) -> bool:
        """C4: 调一次轻量接口验证 TQ 真正可用。

        2026-09-14 修复: 之前用 isinstance(result, dict) + ErrorId=='0' 判定,
        但 tqcenter.get_user_sector() 在底层 DLL 失败时返回 [] (list),
        不是 dict, 也不抛异常 — 任何情况都会被错判为 verify 失败, 永远
        DEGRADED, call_tool 永远被拒 (死锁)。

        真实行为 (PYPlugins/user/tqcenter.py:1856-1886):
        - DLL 正常 + 有数据 → 返回 List[dict]
        - DLL 正常 + 用户无自选股 → 返回 []
        - DLL 失败 (TdxW.exe 未登录 / DLL 指针空) → 返回 []  (print 错误但不抛)
        - ctypes 崩溃 (dll 根本不在) → 抛异常

        判定策略: 不依赖返回值内容, 而是 try/except 探测 DLL 链路是否可达。
        成功调通(无论返回 [] 还是有数据)即视为 OK。
        """
        if self._tq is None:
            return False
        try:
            await asyncio.get_running_loop().run_in_executor(
                get_tdx_quant_executor(),
                lambda: self._tq.get_user_sector(),
            )
            # 调通即视为 OK: 返回 [] 表示 DLL 通但无数据(可能是用户没建
            # 自选股, 也可能是 TdxW.exe 未登录), 实际请求的 ErrorId 会
            # 在 call_tool 阶段自然反映真实状态。
            return True
        except Exception as e:
            logger.debug(f"[{self.name}] TQ verify exception: {e}")
            return False

    async def _try_initialize(self) -> tuple[bool, str | None]:
        """尝试初始化 tqcenter。

        Returns:
            (True, None): 初始化成功
            (False, error_message): 初始化失败及原因
        """
        try:
            tqcenter_dir = str(self.config.tqcenter_dir)
            # tqcenter.initialize 仅接受 path 参数 (dll_path='')，不支持 strategy_id
            tdx_root = str(self.config.tdx_root_path)
            # H1 (round 9 audit) + 簇H (round 12): sys.path 注入/import (执行
            # tqcenter.py 模块体, ctypes 加载 DLL) 与 initialize 同是同步阻塞段,
            # 一体包进 worker thread 并持 _dll_lock; 旧实现把 import 留在事件
            # 循环线程, import 期 DLL 加载仍会冻结 boot 且 wait_for 无法打断。
            await asyncio.get_running_loop().run_in_executor(
                get_tdx_quant_executor(),
                lambda: self._dll_import_and_initialize(tdx_root, tqcenter_dir),
            )
            logger.info(f"[{self.name}] TQ strategy_id={self._strategy_id} (记录用, 未传给 DLL)")
            return True, None
        except (FileNotFoundError, ModuleNotFoundError) as e:
            return False, str(e)
        except Exception as e:
            # 含 ErrorId='12' 同名策略：仅警告，算初始化成功
            err_str = str(e)
            if "ErrorId='12'" in err_str or "同名策略" in err_str:
                logger.warning(f"[{self.name}] initialize warning: {e}")
                return True, None
            return False, str(e)

    async def stop(self):
        """取消探活任务 + tq.close() (后者是同步 DLL 调用, 必须 run_in_executor)。

        MED4: 先等所有 in-flight _reconnect() 完成, 再 tq.close()。
        否则 reconnect 内部 close+initialize 序列会被 tq.close() 半路截断。

        M2 (round 11): 先置停止位挡住 late recovery / 健康循环复活;
        close 走 _dll_lock, 排在僵尸 initialize 之后执行, 不并发打进 DLL。
        """
        self._stopped = True
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
                # M2 (round 11): 持 _dll_lock 与在途/晚到 initialize 互斥。
                await asyncio.get_running_loop().run_in_executor(
                    get_tdx_quant_executor(), self._dll_close
                )
            except Exception as e:
                logger.warning(f"[{self.name}] close warning: {e}")
        self._tq = None
        self._status = UpstreamStatus.UNAVAILABLE

    def _record_failure(self) -> None:
        """累计一次「调用级」失败, 达阈值时重连 / 降级。

        与 ``_health_loop`` 共用 ``_fail_count`` (同一事件循环, 无需加锁)。

        2026-11-01: 之前只有健康循环的主动探活在计数, 而 DLL 卡死时最直接的
        信号恰恰是**调用超时**。不计数的话网关会一直以 healthy 接受请求, 每个
        都拖满 ``call_timeout_sec`` 再返回, 队列越积越深 —— 实测一次并发冲击
        后单次调用从 26ms 劣化到 7.7s, ``/api/upstreams/tdx_quant/metrics``
        仍然报 ``status=healthy``, 控制台看不出任何异常。计数后网关能在连续
        超时后主动降级, 后续请求快速失败而不是继续排队。
        """
        self._fail_count += 1
        if self._fail_count >= self.config.settings.reconnect_threshold:
            self._schedule_reconnect()
        if self._fail_count >= self.config.settings.unavailable_threshold:
            self._trip_unavailable(
                f"{self._fail_count} failed calls (health loop will retry)"
            )

    def _trip_unavailable(self, reason: str) -> None:
        """置 UNAVAILABLE 并记一条 error（幂等）。

        健康循环 / reconnect 成功后会把它复位成 HEALTHY。
        """
        if self._status == UpstreamStatus.UNAVAILABLE:
            return
        self._status = UpstreamStatus.UNAVAILABLE
        logger.error(f"[{self.name}] marked UNAVAILABLE: {reason}")

    def _pool_exhausted(self) -> bool:
        """阻塞池是否已无空闲 worker（全部卡在 DLL 内部）。

        为什么这条判据比「失败计数达阈值」更快也更准：

        ``asyncio.wait_for`` 只能取消 await, 杀不掉已经进入 DLL 的线程。tqcenter
        内部的 ``timeout_ms`` 最大 60s (K 线), 是 ``call_timeout_sec`` 的 6 倍,
        所以**每次调用超时都会留下一个僵尸线程继续占着 worker**, 最长再占 50s。
        worker 一旦全被占住, 新请求只能排队 —— 排多久都拿不到结果, 继续接受
        请求没有任何意义, 必须立刻快速失败。

        判据本身不含魔数: "有调用刚超时" + "池里一个空闲 worker 都没有" 就是
        饱和的确定性证据, 与具体接口的正常耗时无关。
        """
        from ..core.blocking_pool import tdx_quant_executor_snapshot

        snap = tdx_quant_executor_snapshot()
        capacity = snap.get("capacity") or 0
        return capacity > 0 and (snap.get("active") or 0) >= capacity

    def _schedule_reconnect(self) -> None:
        """MED4: 启动 reconnect 任务并加入 tracking set, 完成后自动从 set 移除.

        2026-11-01 合并并发触发: 一次断连会让在途的每个请求都失败, 旧实现
        于是排起 N 个 reconnect 任务 —— ``_reconnect_lock`` 只保证它们不并发,
        不保证不重复, 每个仍要跑一遍 close+initialize 的阻塞 DLL 序列, 把
        worker 和 DLL 队列占满, 恢复反而更慢。已有任务在跑或排队时直接复用。
        """
        if any(not task.done() for task in self._reconnect_tasks):
            logger.debug(
                f"[{self.name}] reconnect already scheduled, coalescing"
            )
            return
        task = asyncio.create_task(self._reconnect())
        self._reconnect_tasks.add(task)
        task.add_done_callback(self._reconnect_tasks.discard)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        """调用 tq.{tool_name}(**arguments)。

        2026-09-14: 允许 DEGRADED 状态调用 (与 is_available 对齐)。
        之前仅 HEALTHY 允许, 导致 _verify_tq_interface() 失败导致的 DEGRADED
        完全死锁 (init OK 但 request 永远被拒)。DEGRADED 表示 TQ 接口
        暂时无数据但底层 DLL 已就绪, 应该让请求尝试走通。
        """
        start_time = time.time()
        # 先拦空代码: 送进 DLL 的代价是关掉整条共享连接 (见 _invalid_input_error)
        invalid = _invalid_input_error(arguments)
        if invalid is not None:
            return ToolResult(
                success=False,
                error=f"[{self.name}] {invalid}",
                error_detail={"error_type": "invalid_param", "message": invalid},
                source=self.name,
                duration_ms=0,
            )
        if self._status == UpstreamStatus.UNAVAILABLE or self._tq is None:
            return ToolResult(
                success=False,
                error=f"[{self.name}] upstream unavailable (status={self._status.value})",
                source=self.name,
                duration_ms=0,
            )

        try:
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    get_tdx_quant_executor(),
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
            # 超时是最可靠的「DLL 卡死」信号, 必须计入健康判定 (见 _record_failure)
            self._record_failure()
            if self._pool_exhausted():
                self._trip_unavailable(
                    "阻塞池已无空闲 worker (调用超时, 线程仍卡在 DLL 内)"
                )
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
                # 链路级异常: 计数 + 立即排一次重连 (计数达阈值时同样会排,
                # _schedule_reconnect 已做合并, 重复触发无副作用)
                self._record_failure()
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

    def _probe_port_sync(self) -> bool:
        """17709 端口实时探测 (同步, 短超时)。

        与 async 版 _probe_tdx_remote_port 同源, 供 get_metrics 调用,
        避免依赖可能滞后 (health 间隔 60s) 的缓存 _status。
        """
        return _do_probe_port("127.0.0.1", self.config.settings.tdx_remote_port, 0.5)

    def get_metrics(self) -> dict:
        """TQ 状态快照 (供 /api/upstreams/tdx_quant/metrics 与上游总览)。

        两个真实信号实时组合:
        - port_reachable: 终端侧 TQ 服务 (17709) 是否在监听
        - tq_active:     Python↔DLL 链路 (initialize 成功且接口可用)

        thread_pool: 专用阻塞线程池快照 (Route A: 与 mootdx2 拆开, 各自独立快照)。
        tdx_quant 不在 client 层维护提交计数, 故 total/peak 由快照默认 0。
        """
        from ..core.blocking_pool import tdx_quant_executor_snapshot
        port_reachable = self._probe_port_sync()
        tq_active = self._status == UpstreamStatus.HEALTHY
        return {
            "status": self._status.value,
            "available": self.is_available,
            "port_reachable": port_reachable,
            "tq_active": tq_active,
            "strategy_id": self._strategy_id,
            "tdx_root": str(self.config.tdx_root_path),
            "port": self.config.settings.tdx_remote_port,
            "thread_pool": tdx_quant_executor_snapshot(),
        }

    async def _health_loop(self):
        """后台探活：周期性调 tq.get_user_sector()。

        当状态为 UNAVAILABLE 时（启动时 TdxW.exe 未运行），继续以较长间隔
        重试连接，实现"即插即用"——用户后启动 TdxW.exe 后网关能自动恢复。
        """
        interval = self.config.settings.health_check_interval_sec
        retry_interval = 10  # UNAVAILABLE 状态下每 10 秒重试一次
        current_interval = interval
        while True:
            await asyncio.sleep(current_interval)
            # UNAVAILABLE 状态下尝试重新初始化
            if self._status == UpstreamStatus.UNAVAILABLE:
                logger.info(f"[{self.name}] UNAVAILABLE, attempting reinitialize...")
                if await self.start():
                    logger.info(f"[{self.name}] reinitialized successfully, resuming health loop")
                    self._fail_count = 0
                    current_interval = interval
                    continue
                else:
                    # 保持 UNAVAILABLE，继续重试
                    current_interval = retry_interval
                    continue
            # 正常运行：非 UNAVILABLE 时才探活
            if self._status == UpstreamStatus.UNKNOWN:
                current_interval = retry_interval
                continue
            try:
                result = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        get_tdx_quant_executor(), lambda: self._tq.get_user_sector()
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
                        current_interval = retry_interval
                else:
                    self._fail_count = 0
                    self._status = UpstreamStatus.HEALTHY
                    current_interval = interval
            except (asyncio.TimeoutError, Exception):
                self._fail_count += 1
                if self._fail_count >= self.config.settings.reconnect_threshold:
                    await self._reconnect()
                if self._fail_count >= self.config.settings.unavailable_threshold:
                    self._status = UpstreamStatus.UNAVAILABLE
                    logger.error(
                        f"[{self.name}] marked UNAVAILABLE after {self._fail_count} failures"
                    )
                    current_interval = retry_interval

    async def _reconnect(self):
        """重连：tq.close() + tq.initialize()，asyncio.Lock 串行化。"""
        loop = asyncio.get_running_loop()
        async with self._reconnect_lock:
            try:
                if self._tq:
                    # HIGH2: tq.close() 同步 DLL, 必须 run_in_executor;
                    # M2 (round 11): 走 _dll_lock 与 initialize 互斥。
                    await loop.run_in_executor(
                        get_tdx_quant_executor(), self._dll_close
                    )
            except Exception as e:
                logger.warning(f"[{self.name}] close during reconnect: {e}")
            try:
                # C1 (round 9 audit): tqcenter.initialize() 只接受 path 参数
                # (策略 ID 仅记录用), 之前传 self._strategy_id 是 bug。
                # M2 (round 11): initialize 同为同步 DLL 调用, 旧实现直接在
                # 事件循环上跑会冻结 loop; 改 executor + _dll_lock 串行。
                tdx_root = str(self.config.tdx_root_path)
                await loop.run_in_executor(
                    get_tdx_quant_executor(),
                    lambda: self._dll_initialize(tdx_root),
                )
                self._fail_count = 0
                self._status = UpstreamStatus.HEALTHY
                logger.info(f"[{self.name}] reconnected")
            except Exception as e:
                self._status = UpstreamStatus.DEGRADED
                logger.warning(f"[{self.name}] reconnect failed: {e}")
