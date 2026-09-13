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
from concurrent.futures import ThreadPoolExecutor

# 默认工作线程数：金融行情并发读取为主，阻塞点在本地库/网络 I/O，
# 线程可适当多于 CPU。可用 UNIHIVE_WORKER_THREADS 覆盖。
_DEFAULT_SIZE = 16


def _pool_size() -> int:
    try:
        n = int(os.environ.get("UNIHIVE_WORKER_THREADS", str(_DEFAULT_SIZE)))
        return max(1, n)
    except (TypeError, ValueError):
        return _DEFAULT_SIZE


_executor: ThreadPoolExecutor | None = None


def get_blocking_executor() -> ThreadPoolExecutor:
    """返回进程内共享的阻塞调用线程池（惰性创建）。"""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
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
