"""阻塞上游调用专用的有界线程池。

mootdx2（通达信本地 TCP 库）、tdx_quant（tqcenter DLL）、thsdk（本地组件）
都是同步阻塞 API，靠 run_in_executor 丢到线程执行。若用默认 executor
(ThreadPoolExecutor, max_workers=min(32, cpu+4))，这些金融阻塞调用会与其它
默认池任务互相挤占，且 3 个上游共享同一上限，行情高并发时排队。

这里提供一个独立的、有界的共享线程池把"上游阻塞调用"与 asyncio 默认池隔离，
容量可按并发需求配置。进程内单例，随事件循环生命周期复用。
"""
from __future__ import annotations

import os
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import thread as _cf_thread

# 默认工作线程数：金融行情并发读取为主，阻塞点在本地库/网络 I/O，
# 线程可适当多于 CPU。可用 UNIHIVE_WORKER_THREADS 覆盖。
_DEFAULT_SIZE = 16


def _pool_size() -> int:
    try:
        n = int(os.environ.get("UNIHIVE_WORKER_THREADS", str(_DEFAULT_SIZE)))
        return max(1, n)
    except (TypeError, ValueError):
        return _DEFAULT_SIZE


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


_executor: ThreadPoolExecutor | None = None


def get_blocking_executor() -> ThreadPoolExecutor:
    """返回进程内共享的阻塞调用线程池（惰性创建）。"""
    global _executor
    if _executor is None:
        _executor = DaemonBlockingExecutor(
            max_workers=_pool_size(),
            thread_name_prefix="unihive-upstream",
        )
    return _executor


def shutdown_blocking_executor() -> None:
    """进程退出时调用（可选）：关闭线程池。"""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
