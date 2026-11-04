"""策略进程池: 独立 .py 路径、超时杀进程、sticky instance 0。"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from src.unihive.api.tdx_quant_instance_pool import (
    TdxQuantInstancePool,
    TdxQuantPoolBusy,
    TdxQuantPoolTimeout,
    write_strategy_files,
)


def test_write_strategy_files_are_unique(tmp_path):
    paths = write_strategy_files(tmp_path, "unihive_gateway", 3)
    assert len(paths) == 3
    assert len(set(paths)) == 3
    from pathlib import Path

    names = {Path(p).name for p in paths}
    assert names == {"unihive_gateway_0.py", "unihive_gateway_1.py", "unihive_gateway_2.py"}
    for p in paths:
        assert Path(p).exists()


def test_write_strategy_files_sanitizes_id(tmp_path):
    paths = write_strategy_files(tmp_path, "a/b c", 1)
    assert "a_b_c_0.py" in paths[0]


def test_pool_rejects_over_cap():
    with pytest.raises(ValueError, match="cap"):
        TdxQuantInstancePool(
            n=9,
            tqcenter_dir="x",
            strategy_paths=[f"{i}.py" for i in range(9)],
        )


class _HangConn:
    def send(self, _msg):
        return None

    def poll(self, timeout):
        time.sleep(min(timeout, 0.02))
        return False

    def recv(self):
        raise RuntimeError("hang conn should not recv")

    def close(self):
        return None


class _OkConn:
    def __init__(self):
        self.last = None

    def send(self, msg):
        self.last = msg

    def poll(self, _timeout):
        return True

    def recv(self):
        return {"ok": True, "data": {"ErrorId": "0", "sticky_probe": self.last}}

    def close(self):
        return None


def _alive_proc():
    return SimpleNamespace(is_alive=lambda: True, pid=4242)


def test_call_timeout_respawns_instance():
    pool = TdxQuantInstancePool(
        n=1, tqcenter_dir="x", strategy_paths=["a.py"], ready_timeout_sec=1
    )
    spawned: list[int] = []

    def fake_spawn(w):
        spawned.append(w.index)
        w.conn = _HangConn()
        w.proc = _alive_proc()

    pool._spawn = fake_spawn  # type: ignore[method-assign]
    pool._workers[0].conn = _HangConn()
    pool._workers[0].proc = _alive_proc()

    with pytest.raises(TdxQuantPoolTimeout):
        pool.call("get_user_sector", {}, timeout=0.05)
    assert spawned == [0]


def test_sticky_goes_to_instance_zero():
    pool = TdxQuantInstancePool(
        n=2, tqcenter_dir="x", strategy_paths=["a.py", "b.py"]
    )
    zero = _OkConn()
    one = _OkConn()
    pool._workers[0].conn = zero
    pool._workers[0].proc = _alive_proc()
    pool._workers[1].conn = one
    pool._workers[1].proc = _alive_proc()
    pool._spawn = lambda w: None  # type: ignore[method-assign]

    data = pool.call("formula_zb", {"code": "1"}, timeout=1, sticky=True)
    assert data["ErrorId"] == "0"
    assert zero.last["method"] == "formula_zb"
    assert one.last is None


def test_non_sticky_uses_free_instance():
    pool = TdxQuantInstancePool(
        n=2, tqcenter_dir="x", strategy_paths=["a.py", "b.py"]
    )
    zero = _OkConn()
    one = _OkConn()
    pool._workers[0].conn = zero
    pool._workers[0].proc = _alive_proc()
    pool._workers[1].conn = one
    pool._workers[1].proc = _alive_proc()
    pool._spawn = lambda w: None  # type: ignore[method-assign]
    assert pool._workers[0].lock.acquire(blocking=False)
    try:
        pool.call("get_market_snapshot", {"stock_code": "600519.SH"}, timeout=1)
        assert one.last["method"] == "get_market_snapshot"
        assert zero.last is None
    finally:
        pool._workers[0].lock.release()


def test_all_busy_raises():
    pool = TdxQuantInstancePool(
        n=1, tqcenter_dir="x", strategy_paths=["a.py"]
    )
    assert pool._workers[0].lock.acquire(blocking=False)
    try:
        with pytest.raises(TdxQuantPoolBusy):
            pool.call("get_user_sector", {}, timeout=1, acquire_timeout=0.02)
    finally:
        pool._workers[0].lock.release()
