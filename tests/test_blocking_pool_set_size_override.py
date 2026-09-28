"""_PoolBundle.set_size_override 测试。

方案 C lite (2026-09-27): 启动期 cfg 注入 worker 数, 已创建 executor 守卫 (重启才生效)。
"""
import logging

import pytest

from src.unihive.core import blocking_pool as bp


@pytest.fixture
def fresh_mootdx2_bundle():
    """返还一个干净 _PoolBundle (override=None, executor=None), 避免污染 module singleton。

    每个用例独立 bundle, 互不干扰。"""
    return bp._PoolBundle(
        env_var="UNIHIVE_TEST_POOL_SIZE_OVERRIDE",
        default_size=16,
        prefix="test-override",
    )


# === set_size_override 基础生效 ===
def test_set_size_override_sets_value(fresh_mootdx2_bundle):
    """未创建 executor 时, set_size_override 写入 _size_override 字段。"""
    fresh_mootdx2_bundle.set_size_override(16)
    assert fresh_mootdx2_bundle._size_override == 16


def test_set_size_override_reflected_in_snapshot_capacity(fresh_mootdx2_bundle):
    """未创建 executor 时, snapshot 的 capacity = override (前端 metrics 一致性)。"""
    fresh_mootdx2_bundle.set_size_override(20)
    snap = fresh_mootdx2_bundle.snapshot()
    assert snap["capacity"] == 20
    assert snap["workers"] == 0
    assert snap["active"] == 0
    assert snap["queued"] == 0


def test_get_uses_override_for_max_workers(fresh_mootdx2_bundle):
    """get() 创建 executor 时, max_workers = override。"""
    fresh_mootdx2_bundle.set_size_override(5)
    executor = fresh_mootdx2_bundle.get()
    try:
        assert executor._max_workers == 5
    finally:
        executor.shutdown(wait=True)


# === 已创建 executor 守卫 (重启才生效) ===
def test_set_size_override_ignored_when_executor_exists(fresh_mootdx2_bundle, caplog):
    """executor 已创建后, set_size_override WARNING + 忽略 (避免误导热改)。"""
    executor = fresh_mootdx2_bundle.get()  # 先创建
    try:
        assert executor._max_workers == 16  # 默认 size
        with caplog.at_level(logging.WARNING, logger="src.unihive.core.blocking_pool"):
            fresh_mootdx2_bundle.set_size_override(32)
        # 字段没改
        assert fresh_mootdx2_bundle._size_override is None  # 未生效
        assert executor._max_workers == 16  # 仍然 16
        # WARNING 记录
        assert any("ignored" in r.message.lower() for r in caplog.records)
        assert any("restart" in r.message.lower() for r in caplog.records)
    finally:
        executor.shutdown(wait=True)


def test_set_size_override_after_executor_does_not_affect_capacity(fresh_mootdx2_bundle):
    """executor 已创建后调 set_size_override, snapshot capacity 仍是旧的 _max_workers。"""
    executor = fresh_mootdx2_bundle.get()
    try:
        fresh_mootdx2_bundle.set_size_override(64)
        snap = fresh_mootdx2_bundle.snapshot()
        assert snap["capacity"] == 16  # executor 的 _max_workers, 不是 override
    finally:
        executor.shutdown(wait=True)


# === 优先级链: override > env > default ===
def test_get_uses_env_when_no_override(monkeypatch, fresh_mootdx2_bundle):
    """未设 override, env=12 → executor max_workers=12。"""
    monkeypatch.setenv("UNIHIVE_TEST_POOL_SIZE_OVERRIDE", "12")
    executor = fresh_mootdx2_bundle.get()
    try:
        assert executor._max_workers == 12
    finally:
        executor.shutdown(wait=True)


def test_get_uses_default_when_no_override_no_env(monkeypatch, fresh_mootdx2_bundle):
    """未设 override + 无 env → executor max_workers=default_size=16。"""
    monkeypatch.delenv("UNIHIVE_TEST_POOL_SIZE_OVERRIDE", raising=False)
    executor = fresh_mootdx2_bundle.get()
    try:
        assert executor._max_workers == 16
    finally:
        executor.shutdown(wait=True)


def test_override_beats_env(monkeypatch, fresh_mootdx2_bundle):
    """override=10 + env=99 → 10 (cfg 优先)。"""
    monkeypatch.setenv("UNIHIVE_TEST_POOL_SIZE_OVERRIDE", "99")
    fresh_mootdx2_bundle.set_size_override(10)
    executor = fresh_mootdx2_bundle.get()
    try:
        assert executor._max_workers == 10
    finally:
        executor.shutdown(wait=True)


# === 非法值钳到 ≥1 ===
def test_set_size_override_clamps_zero_to_one(fresh_mootdx2_bundle):
    """override=0 → 钳到 1 (≥1 守卫)。"""
    fresh_mootdx2_bundle.set_size_override(0)
    assert fresh_mootdx2_bundle._size_override == 1


def test_set_size_override_clamps_negative_to_one(fresh_mootdx2_bundle):
    """override=-5 → 钳到 1。"""
    fresh_mootdx2_bundle.set_size_override(-5)
    assert fresh_mootdx2_bundle._size_override == 1


def test_set_size_override_string_numeric_coerced(fresh_mootdx2_bundle):
    """override="16" 可被 int() 解析 → 16。"""
    fresh_mootdx2_bundle.set_size_override("16")  # type: ignore[arg-type]
    assert fresh_mootdx2_bundle._size_override == 16


def test_set_size_override_invalid_string_silently_ignored(fresh_mootdx2_bundle):
    """override="abc" 非法 → 字段保持 None (无 WARN, 设计简单失败)。"""
    fresh_mootdx2_bundle.set_size_override("abc")  # type: ignore[arg-type]
    assert fresh_mootdx2_bundle._size_override is None


# === shutdown 后可重新设置 ===
def test_shutdown_then_set_size_override_works(fresh_mootdx2_bundle):
    """shutdown(wait=False) 清空 _executor, 之后 set_size_override 重新生效。"""
    executor = fresh_mootdx2_bundle.get()
    assert executor._max_workers == 16
    fresh_mootdx2_bundle.shutdown()
    assert fresh_mootdx2_bundle._executor is None
    fresh_mootdx2_bundle.set_size_override(24)
    assert fresh_mootdx2_bundle._size_override == 24
    # 再 get 创建, max_workers = 24
    executor2 = fresh_mootdx2_bundle.get()
    try:
        assert executor2._max_workers == 24
    finally:
        executor2.shutdown(wait=True)


# === snapshot 在未创建 executor 时反映 env (无 override) ===
def test_snapshot_capacity_uses_env_when_no_override(monkeypatch, fresh_mootdx2_bundle):
    """未创建 executor + 无 override, snapshot capacity 走 env。"""
    monkeypatch.setenv("UNIHIVE_TEST_POOL_SIZE_OVERRIDE", "11")
    snap = fresh_mootdx2_bundle.snapshot()
    assert snap["capacity"] == 11
    assert snap["workers"] == 0


def test_snapshot_capacity_uses_default_when_no_override_no_env(monkeypatch, fresh_mootdx2_bundle):
    """未创建 executor + 无 override + 无 env, snapshot capacity 走 default。"""
    monkeypatch.delenv("UNIHIVE_TEST_POOL_SIZE_OVERRIDE", raising=False)
    snap = fresh_mootdx2_bundle.snapshot()
    assert snap["capacity"] == 16


# === module singleton smoke (避免回归 module-level _PoolBundle 接错) ===
def test_module_mootdx2_bundle_has_no_size_override(monkeypatch):
    """module-level _mootdx2 在测试环境下应保持 _size_override=None (避免跨用例污染)。"""
    monkeypatch.delenv("UNIHIVE_MOOTDX2_WORKER_THREADS", raising=False)
    assert bp._mootdx2._size_override is None
    assert bp._mootdx2._executor is None or bp._mootdx2._executor._max_workers >= 1


def test_module_tdx_quant_bundle_has_no_size_override(monkeypatch):
    monkeypatch.delenv("UNIHIVE_TDX_QUANT_WORKER_THREADS", raising=False)
    assert bp._tdx_quant._size_override is None
    assert bp._tdx_quant._executor is None or bp._tdx_quant._executor._max_workers >= 1


def test_module_thsdk_bundle_size_one():
    """thsdk 1 worker 是设计约束 (DLL 内部 RLock 串行), 不动。"""
    assert bp._thsdk._default_size == 1
    assert bp._thsdk._env_var is None  # 无 env 覆盖