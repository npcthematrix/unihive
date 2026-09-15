"""阻塞上游调用专用的有界线程池。

mootdx2（通达信本地 TCP 库）、tdx_quant（tqcenter DLL）、thsdk（本地组件）
都是同步阻塞 API，靠 run_in_executor 丢到线程执行。若用默认 executor
(ThreadPoolExecutor, max_workers=min(32, cpu+4))，这些金融阻塞调用会与其它
默认池任务互相挤占，且 3 个上游共享同一上限，行情高并发时排队。

这里提供一个独立的、有界的共享线程池把"上游阻塞调用"与 asyncio 默认池隔离，
容量可按并发需求配置。进程内单例，随事件循环生命周期复用。

P0.1 (2026-09-15 mootdx2 高并发优化): 按 upstream 拆池，
避免高频上游 (mootdx2) 被低频上游 (omni/thsdk) 抢线程。
每个上游独立池容量由 UNIHIVE_WORKER_<UPSTREAM> 控制,
未配置时回退到 UNIHIVE_WORKER_THREADS (默认 16)。
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

# 默认工作线程数：金融行情并发读取为主，阻塞点在本地库/网络 I/O，
# 线程可适当多于 CPU。可用 UNIHIVE_WORKER_THREADS 覆盖。
_DEFAULT_SIZE = 16

# 每个上游的默认容量 (环境变量覆盖)
# mootdx2 高频行情; 其它按调用频率分配
_DEFAULT_POOL_SIZES = {
    "mootdx2": 24,   # 高频行情, 占用大头
    "tdx_quant": 8,  # 中频工具
    "thsdk": 4,      # 低频
    "omni": 2,       # SQLite 串行, 不需要多线程
}


def _pool_size(upstream: str | None = None) -> int:
    """获取线程池容量。

    优先级: UNIHIVE_WORKER_<UPSTREAM> > UNIHIVE_WORKER_THREADS > 预设 > 16
    """
    if upstream:
        env_key = f"UNIHIVE_WORKER_{upstream.upper()}"
        env_val = os.environ.get(env_key)
        if env_val:
            try:
                return max(1, int(env_val))
            except (TypeError, ValueError):
                pass
        if upstream in _DEFAULT_POOL_SIZES:
            return _DEFAULT_POOL_SIZES[upstream]
    try:
        n = int(os.environ.get("UNIHIVE_WORKER_THREADS", str(_DEFAULT_SIZE)))
        return max(1, n)
    except (TypeError, ValueError):
        return _DEFAULT_SIZE


_executors: dict[str, ThreadPoolExecutor] = {}
_default_executor: ThreadPoolExecutor | None = None


def get_blocking_executor(upstream: str | None = None) -> ThreadPoolExecutor:
    """返回进程内共享的阻塞调用线程池（惰性创建）。

    Args:
        upstream: 上游名称 (mootdx2/tdx_quant/thsdk/omni),
                  指定时返回该上游专属池; None 返回共享池。
    """
    if upstream:
        if upstream not in _executors:
            _executors[upstream] = ThreadPoolExecutor(
                max_workers=_pool_size(upstream),
                thread_name_prefix=f"unihive-{upstream}",
            )
        return _executors[upstream]
    # 未指定 upstream: 共享池 (向后兼容)
    global _default_executor
    if _default_executor is None:
        _default_executor = ThreadPoolExecutor(
            max_workers=_pool_size(),
            thread_name_prefix="unihive-upstream",
        )
    return _default_executor


def shutdown_blocking_executor() -> None:
    """进程退出时调用（可选）：关闭所有线程池。"""
    global _default_executor
    for name, executor in list(_executors.items()):
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
    _executors.clear()
    if _default_executor is not None:
        _default_executor.shutdown(wait=False, cancel_futures=True)
        _default_executor = None
