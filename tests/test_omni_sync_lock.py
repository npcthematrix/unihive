"""OMNI 同步 in-flight 互斥锁测试 (2026-09-18 控制台功能审计 M1)。

并发 POST /api/omni/sync (双击 / 旧客户端超时后重发) 不得让两个 run_sync
并发写同一个 board.db (SQLite locked + 重复入库)。第二个请求快速失败,
不排队; 锁在完成后释放, 后续同步照常。
"""
from __future__ import annotations

import asyncio

from src.unihive.utils import console_api as ca


class _FakeOmni:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.calls = 0

    async def run_sync(self, source: str, board_type: str) -> dict:
        self.calls += 1
        self.started.set()
        await self.finish.wait()
        return {"status": "success", "record_count": 7, "message": "ok"}


async def test_concurrent_sync_second_request_fails_fast():
    fake = _FakeOmni()
    ca.set_omni_client(fake)
    assert ca._omni_sync_lock.locked() is False
    try:
        first = asyncio.create_task(ca.run_omni_sync("all", "all"))
        await fake.started.wait()

        second = asyncio.create_task(ca.run_omni_sync("all", "all"))
        rejected = await asyncio.wait_for(second, timeout=1)

        assert rejected["status"] == "failed"
        assert "已有同步任务" in rejected["message"]
        assert fake.calls == 1  # 第二个请求根本没进 run_sync

        fake.finish.set()
        ok = await asyncio.wait_for(first, timeout=1)
        assert ok["status"] == "success"
        assert ca._omni_sync_lock.locked() is False  # 完成后锁释放
    finally:
        fake.finish.set()
        ca.set_omni_client(None)


async def test_sync_allowed_again_after_completion():
    """上一轮结束后新同步正常执行 (锁不是一次性的)。"""
    fake = _FakeOmni()
    fake.finish.set()  # 不阻塞, 立即完成
    ca.set_omni_client(fake)
    try:
        result = await ca.run_omni_sync("all", "all")
        assert result["status"] == "success"
        assert fake.calls == 1
        assert ca._omni_sync_lock.locked() is False
    finally:
        ca.set_omni_client(None)


async def test_invalid_source_does_not_take_lock():
    """白名单拒绝发生在取锁之前, 不得把锁遗留在 locked 态。"""
    fake = _FakeOmni()
    ca.set_omni_client(fake)
    try:
        result = await ca.run_omni_sync("bogus", "all")
        assert result["status"] == "failed"
        assert fake.calls == 0
        assert ca._omni_sync_lock.locked() is False
    finally:
        ca.set_omni_client(None)
