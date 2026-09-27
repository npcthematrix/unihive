"""阻塞上游调用专用的有界线程池（按上游拆分，各自独立定容）。

mootdx2（通达信本地 TCP 库）、tdx_quant（tqcenter DLL）、thsdk（本地组件）
都是同步阻塞 API，靠 run_in_executor 丢到线程执行。若用默认 executor
(ThreadPoolExecutor, max_workers=min(32, cpu+4))，这些金融阻塞调用会与其它
默认池任务互相挤占。

按上游各拆一个 DaemonBlockingExecutor，容量按各上游的并发上限独立设定：

- mootdx2: 8 worker（Reader 闸门 `_READER_SEMAPHORE_LIMIT=4` 已把并发 Reader
  钉死为 4，池容量给到 8 留余量给 probe/close 等非 Reader 阻塞调用）。
- tdx_quant: 10 worker（get 调用可并发，init/close 由 `_dll_lock` 串行）。
- thsdk: 1 worker（hq.dll 内部 RLock 串行；单 worker 把"僵尸 DLL 调用"
  上限钉死为 1）。

fuyao（httpx async）与 omni（aiosqlite async）是异步客户端，无需线程池。
每个池进程内单例，随事件循环生命周期复用；容量可用各上游的 env 覆盖。
"""
from __future__ import annotations

import os
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import thread as _cf_thread
from typing import Optional


# 各上游默认 worker 数：阻塞点在本地库/网络 I/O，线程可适当多于 CPU。
# 可用对应的 env 覆盖。
_MOOTDX2_DEFAULT_SIZE = 8
_TDX_QUANT_DEFAULT_SIZE = 10


def _pool_size(env_var: Optional[str], default: int) -> int:
    """从 env 读 worker 数。env_var=None 或值非法 → 回落到 default, 钳到 ≥1。"""
    if env_var is None:
        return max(1, default)
    try:
        n = int(os.environ.get(env_var, str(default)))
        return max(1, n)
    except (TypeError, ValueError):
        return max(1, default)


class DaemonBlockingExecutor(ThreadPoolExecutor):
    """worker 为 daemon 线程、且不注册进 concurrent.futures 的 atexit join 表。

    L4 (round 10 audit): 上游同步调用 (tq.initialize / Quotes.factory 等 DLL/
    本地库) 理论上可能永久挂死; H1 已让事件循环能 wait_for 挣脱, 但解释器退出
    阶段仍有两道无超时 join 会拖住进程:

    1. concurrent.futures.thread._python_exit 遍历 ``_threads_queues`` join 全部 worker;
    2. threading 模块退出时 join 全部 **non-daemon** 线程。

    默认池两条都中。这里覆盖 _adjust_thread_count: worker 设 daemon=True (绕过 2),
    且刻意不写入 ``_threads_queues`` (绕过 1); executor 自身的 self._threads 仍
    跟踪, shutdown(wait=...) 语义不变。卡死的 worker 随进程终止被 OS 回收。

    复制的是 CPython 3.10-3.13 稳定的 _adjust_thread_count 线程创建段 (模块级
    ``_worker`` 签名自 3.8 未变); 若未来版本移除该私有 API, 回退普通池即可。
    """

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, q=self._work_queue):
            q.put(None)

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = "%s_%d" % (self._thread_name_prefix or self, num_threads)
            t = threading.Thread(
                name=thread_name,
                target=_cf_thread._worker,
                args=(
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                ),
                daemon=True,
            )
            t.start()
            self._threads.add(t)
            # 刻意不执行 _threads_queues[t] = self._work_queue — 否则 atexit
            # _python_exit 仍会无超时 join 这个可能永久挂死的线程。


def _executor_snapshot(executor: ThreadPoolExecutor) -> dict:
    """线程池实时快照: 容量 / 活动线程 / 排队任务。

    _work_queue.qsize() 在 CPython 的 SimpleQueue 上可用; 已创建 worker 数 =
    len(_threads), 但"已创建"不等于"在忙", 故活动数用 _idle_semaphore 推算:
    idle permit 数即空闲 worker, active = 总 worker - idle。
    """
    total_workers = len(executor._threads)
    # 不阻塞地取空闲许可数: drain 后立刻归还
    idle = 0
    while executor._idle_semaphore.acquire(timeout=0):
        idle += 1
    for _ in range(idle):
        executor._idle_semaphore.release()
    return {
        "capacity": executor._max_workers,
        "workers": total_workers,
        "active": max(0, total_workers - idle),
        "queued": executor._work_queue.qsize(),
    }


class _PoolBundle:
    """per-source 阻塞池三件套工厂 (get / snapshot / shutdown)。

    每个上游一个实例, 自身持 ThreadPoolExecutor 单例 + env/默认容量/前缀。
    公共 API 通过下面 9 个 module-level 包装函数暴露, 保持向后兼容的导入面。
    """

    __slots__ = ("_env_var", "_default_size", "_prefix", "_executor")

    def __init__(self, env_var: Optional[str], default_size: int, prefix: str) -> None:
        self._env_var = env_var
        self._default_size = default_size
        self._prefix = prefix
        self._executor: Optional[ThreadPoolExecutor] = None

    def get(self) -> ThreadPoolExecutor:
        """惰性创建并返回专用池 (进程内单例)。"""
        if self._executor is None:
            self._executor = DaemonBlockingExecutor(
                max_workers=_pool_size(self._env_var, self._default_size),
                thread_name_prefix=self._prefix,
            )
        return self._executor

    def snapshot(self) -> dict:
        """池快照。未创建时按容量事实返空池状态。"""
        if self._executor is None:
            return {
                "capacity": _pool_size(self._env_var, self._default_size),
                "workers": 0,
                "active": 0,
                "queued": 0,
            }
        return _executor_snapshot(self._executor)

    def shutdown(self) -> None:
        """关停并清空单例。

        Round-12 簇H: cancel_futures 刻意为 False —— stop() 摘关 Quotes 的 close
        job 可能排在一个卡死的 factory 后面 (唯一 worker 被占住, mootdx2 stop 注释
        承诺 "最终必执行")。daemon worker 会继续排空队列且不注册 atexit join,
        不拖进程退出; wait=False 让调用立即返回, 不影响关停时延。
        """
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=False)
            self._executor = None


# ---------------------------------------------------------------------------
# per-source 实例 + 公共三件套 (向后兼容的 module-level 函数)
# ---------------------------------------------------------------------------

_mootdx2 = _PoolBundle("UNIHIVE_MOOTDX2_WORKER_THREADS", _MOOTDX2_DEFAULT_SIZE, "unihive-mootdx2")
_tdx_quant = _PoolBundle("UNIHIVE_TDX_QUANT_WORKER_THREADS", _TDX_QUANT_DEFAULT_SIZE, "unihive-tdx-quant")
_thsdk = _PoolBundle(env_var=None, default_size=1, prefix="unihive-thsdk")


# mootdx2
def get_mootdx2_executor() -> ThreadPoolExecutor:
    """返回 mootdx2 专用的阻塞调用线程池（惰性创建，默认 8 worker）。"""
    return _mootdx2.get()


def mootdx2_executor_snapshot() -> dict:
    """mootdx2 专用池快照 (未创建则返回空池事实)。"""
    return _mootdx2.snapshot()


def shutdown_mootdx2_executor() -> None:
    """关停 mootdx2 专用池。详见 _PoolBundle.shutdown。"""
    _mootdx2.shutdown()


# tdx_quant
def get_tdx_quant_executor() -> ThreadPoolExecutor:
    """返回 tdx_quant 专用的阻塞调用线程池（惰性创建，默认 10 worker）。

    与 mootdx2 拆开: get 调用可并发, init/close 由 ``_dll_lock`` 串行; 共享池
    会把两者占用混进同一快照, 监控行不诚实。
    """
    return _tdx_quant.get()


def tdx_quant_executor_snapshot() -> dict:
    """tdx_quant 专用池快照 (未创建则返回空池事实)。"""
    return _tdx_quant.snapshot()


def shutdown_tdx_quant_executor() -> None:
    """关停 tdx_quant 专用池（与 shutdown_mootdx2_executor 同样的 cancel_futures=False 语义）。

    stop() 摘关 tq 的 close job 可能排在一个卡死的 initialize (DLL 加载) 后面;
    取消它会让晚赋值的 DLL 句柄无人释放。
    """
    _tdx_quant.shutdown()


# thsdk
def get_thsdk_executor() -> ThreadPoolExecutor:
    """返回 thsdk 专用的单 worker 线程池（惰性创建）。

    为何独立且只给 1 个 worker：thsdk 原生组件（hq.dll）内部本就以 RLock
    串行调用，并发提交无收益；关键是 asyncio.wait_for 超时只取消 await、
    杀不掉已在 DLL 内阻塞的线程。单 worker 把"僵尸 DLL 调用"上限钉死为 1,
    其余任务在队列里排队且可随超时取消（未开跑的 Future.cancel 成功）,
    不会像多 worker 池那样每次超时都新增一个僵尸线程。
    """
    return _thsdk.get()


def thsdk_executor_snapshot() -> dict:
    """thsdk 单 worker 池快照。"""
    return _thsdk.snapshot()


def shutdown_thsdk_executor() -> None:
    """关停 thsdk 专用池（与其它上游池同样的 cancel_futures=False 语义）。"""
    _thsdk.shutdown()