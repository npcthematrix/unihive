"""阻塞线程池快照测试。"""
import threading

from src.unihive.core import blocking_pool as bp


def test_snapshot_when_uncreated():
    # 未创建时返回容量事实 (结构稳定, 容量按环境)
    snap = bp.mootdx2_executor_snapshot()
    assert set(snap) == {"capacity", "workers", "active", "queued"}
    assert snap["workers"] == 0
    assert snap["active"] == 0
    assert snap["queued"] == 0


def test_snapshot_idle_pool():
    executor = bp.DaemonBlockingExecutor(
        max_workers=3, thread_name_prefix="test-snap-idle"
    )
    try:
        # 跑一个任务让 worker 被创建
        assert executor.submit(lambda: 42).result(timeout=5) == 42
        snap = bp._executor_snapshot(executor)
        assert snap["capacity"] == 3
        assert snap["workers"] == 1
        assert snap["active"] == 0  # 任务完成 → 空闲
        assert snap["queued"] == 0
    finally:
        executor.shutdown(wait=True)


def test_snapshot_active_worker():
    executor = bp.DaemonBlockingExecutor(
        max_workers=2, thread_name_prefix="test-snap-busy"
    )
    entered = threading.Event()
    release = threading.Event()
    executor.submit(lambda: (entered.set(), release.wait(timeout=5)))
    assert entered.wait(timeout=5)
    try:
        snap = bp._executor_snapshot(executor)
        assert snap["active"] == 1
        assert snap["workers"] == 1
    finally:
        release.set()
        executor.shutdown(wait=True)
