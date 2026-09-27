"""PoolConfig 优先级链 + validate 校验测试。

方案 C lite (2026-09-27): cfg > env > default, validate reader_limit > worker WARNING。
"""
import logging

import pytest

from src.unihive.core.pool_config import (
    DEFAULT_MOOTDX2_READER_LIMIT,
    DEFAULT_MOOTDX2_WORKER_THREADS,
    DEFAULT_TDX_QUANT_WORKER_THREADS,
    ENV_MOOTDX2_READER_LIMIT,
    ENV_MOOTDX2_WORKER_THREADS,
    ENV_TDX_QUANT_WORKER_THREADS,
    PoolConfig,
    resolve_pool_config,
    validate_pool_config,
)


# === 1. cfg 优先 ===
def test_cfg_beats_env_and_default_for_mootdx2_worker_threads(monkeypatch):
    """mootdx2 worker_threads: cfg=12, env=20 → 12。"""
    monkeypatch.setenv(ENV_MOOTDX2_WORKER_THREADS, "20")
    cfg = {"upstreams": {"mootdx2": {"worker_threads": 12}}}
    pc = resolve_pool_config(cfg)
    assert pc.mootdx2_worker_threads == 12


def test_cfg_beats_env_for_tdx_quant_worker_threads(monkeypatch):
    """tdx_quant worker_threads: cfg=15, env=25 → 15。"""
    monkeypatch.setenv(ENV_TDX_QUANT_WORKER_THREADS, "25")
    cfg = {"upstreams": {"tdx_quant": {"worker_threads": 15}}}
    pc = resolve_pool_config(cfg)
    assert pc.tdx_quant_worker_threads == 15


def test_cfg_beats_env_for_mootdx2_reader_limit(monkeypatch):
    """mootdx2 reader_limit: cfg=6, env=10 → 6。"""
    monkeypatch.setenv(ENV_MOOTDX2_READER_LIMIT, "10")
    cfg = {"upstreams": {"mootdx2": {"reader_limit": 6}}}
    pc = resolve_pool_config(cfg)
    assert pc.mootdx2_reader_limit == 6


# === 2. cfg 缺失走 env ===
def test_env_used_when_cfg_missing_for_mootdx2_worker(monkeypatch):
    """mootdx2 worker_threads: cfg 缺失, env=16 → 16。"""
    monkeypatch.setenv(ENV_MOOTDX2_WORKER_THREADS, "16")
    pc = resolve_pool_config({"upstreams": {"mootdx2": {}}})
    assert pc.mootdx2_worker_threads == 16


def test_env_used_when_cfg_missing_for_reader_limit(monkeypatch):
    """mootdx2 reader_limit: cfg 缺失, env=8 → 8。"""
    monkeypatch.setenv(ENV_MOOTDX2_READER_LIMIT, "8")
    pc = resolve_pool_config({})
    assert pc.mootdx2_reader_limit == 8


# === 3. cfg+env 都缺失走 default ===
def test_default_used_when_cfg_and_env_missing(monkeypatch):
    monkeypatch.delenv(ENV_MOOTDX2_WORKER_THREADS, raising=False)
    monkeypatch.delenv(ENV_MOOTDX2_READER_LIMIT, raising=False)
    monkeypatch.delenv(ENV_TDX_QUANT_WORKER_THREADS, raising=False)
    pc = resolve_pool_config(None)
    assert pc.mootdx2_reader_limit == DEFAULT_MOOTDX2_READER_LIMIT
    assert pc.mootdx2_worker_threads == DEFAULT_MOOTDX2_WORKER_THREADS
    assert pc.tdx_quant_worker_threads == DEFAULT_TDX_QUANT_WORKER_THREADS


def test_default_used_when_cfg_empty_dict(monkeypatch):
    monkeypatch.delenv(ENV_MOOTDX2_WORKER_THREADS, raising=False)
    pc = resolve_pool_config({})
    assert pc.mootdx2_worker_threads == DEFAULT_MOOTDX2_WORKER_THREADS


# === 4. cfg 非法值回落 env (WARNING) ===
def test_cfg_invalid_falls_back_to_env_for_worker_threads(monkeypatch, caplog):
    """cfg worker_threads="abc" 非法 → env=18 + WARNING。"""
    monkeypatch.setenv(ENV_MOOTDX2_WORKER_THREADS, "18")
    cfg = {"upstreams": {"mootdx2": {"worker_threads": "abc"}}}
    with caplog.at_level(logging.WARNING, logger="src.unihive.core.pool_config"):
        pc = resolve_pool_config(cfg)
    assert pc.mootdx2_worker_threads == 18
    assert any("invalid" in r.message.lower() for r in caplog.records)


def test_cfg_invalid_falls_back_to_env_for_reader_limit(monkeypatch, caplog):
    """cfg reader_limit=None (走 env/default, 不算非法) — 反向: cfg=0 应钳到 1。

    钳到 ≥1 是设计守卫, 不是 fallback. 验证钳逻辑生效。"""
    monkeypatch.setenv(ENV_MOOTDX2_READER_LIMIT, "5")
    cfg = {"upstreams": {"mootdx2": {"reader_limit": 0}}}
    pc = resolve_pool_config(cfg)
    assert pc.mootdx2_reader_limit == 1


def test_cfg_negative_clamps_to_minimum(monkeypatch):
    """cfg=-3 应钳到 1, 不抛错。"""
    pc = resolve_pool_config({"upstreams": {"mootdx2": {"worker_threads": -3}}})
    assert pc.mootdx2_worker_threads == 1


def test_cfg_string_numeric_coerced(monkeypatch):
    """cfg "12" 可被 int() 解析 → 12 (不是非法)。"""
    monkeypatch.delenv(ENV_MOOTDX2_WORKER_THREADS, raising=False)
    pc = resolve_pool_config({"upstreams": {"mootdx2": {"worker_threads": "12"}}})
    assert pc.mootdx2_worker_threads == 12


# === 5. env 非法值回落 default ===
def test_env_invalid_falls_back_to_default(monkeypatch, caplog):
    """env worker_threads="not_a_number" → default + WARNING。"""
    monkeypatch.setenv(ENV_MOOTDX2_WORKER_THREADS, "not_a_number")
    with caplog.at_level(logging.WARNING, logger="src.unihive.core.pool_config"):
        pc = resolve_pool_config(None)
    assert pc.mootdx2_worker_threads == DEFAULT_MOOTDX2_WORKER_THREADS
    assert any("invalid" in r.message.lower() for r in caplog.records)


def test_env_negative_clamps_to_minimum(monkeypatch):
    """env=-1 钳到 1 (与 cfg 行为一致, ≥1 设计守卫)。"""
    monkeypatch.setenv(ENV_TDX_QUANT_WORKER_THREADS, "-1")
    pc = resolve_pool_config(None)
    assert pc.tdx_quant_worker_threads == 1


# === 6. cfg 缺失字段但 upstream dict 存在 ===
def test_other_upstream_present_but_unaffected(monkeypatch):
    """tdx_quant cfg 缺 worker_threads → 默认, mootdx2 cfg 完整 → mootdx2 用 cfg。"""
    monkeypatch.delenv(ENV_MOOTDX2_WORKER_THREADS, raising=False)
    monkeypatch.delenv(ENV_TDX_QUANT_WORKER_THREADS, raising=False)
    cfg = {
        "upstreams": {
            "mootdx2": {"worker_threads": 14},
            "tdx_quant": {},  # 缺 worker_threads
        }
    }
    pc = resolve_pool_config(cfg)
    assert pc.mootdx2_worker_threads == 14
    assert pc.tdx_quant_worker_threads == DEFAULT_TDX_QUANT_WORKER_THREADS


# === 7. cfg=None ===
def test_cfg_none_walks_env_then_default(monkeypatch):
    monkeypatch.setenv(ENV_MOOTDX2_READER_LIMIT, "3")
    monkeypatch.delenv(ENV_MOOTDX2_WORKER_THREADS, raising=False)
    monkeypatch.delenv(ENV_TDX_QUANT_WORKER_THREADS, raising=False)
    pc = resolve_pool_config(None)
    assert pc.mootdx2_reader_limit == 3  # 走 env
    assert pc.mootdx2_worker_threads == DEFAULT_MOOTDX2_WORKER_THREADS  # 走 default
    assert pc.tdx_quant_worker_threads == DEFAULT_TDX_QUANT_WORKER_THREADS


def test_cfg_empty_upstreams_dict(monkeypatch):
    monkeypatch.delenv(ENV_MOOTDX2_WORKER_THREADS, raising=False)
    cfg = {"upstreams": {}}
    pc = resolve_pool_config(cfg)
    assert pc.mootdx2_worker_threads == DEFAULT_MOOTDX2_WORKER_THREADS


def test_cfg_unknown_upstream_is_silently_ignored(monkeypatch):
    """cfg 含未知上游 (e.g. fuyao/omni), 不影响池解析 → 全 default。"""
    monkeypatch.delenv(ENV_MOOTDX2_WORKER_THREADS, raising=False)
    cfg = {"upstreams": {"fuyao": {"worker_threads": 999}, "omni": {}}}
    pc = resolve_pool_config(cfg)
    assert pc.mootdx2_worker_threads == DEFAULT_MOOTDX2_WORKER_THREADS


# === 8/9/10. validate_pool_config ===
def test_validate_warns_when_reader_limit_exceeds_workers():
    """reader_limit > worker_threads → warning 非空 + 含 "Reader"。"""
    pc = PoolConfig(mootdx2_reader_limit=10, mootdx2_worker_threads=4, tdx_quant_worker_threads=10)
    warnings = validate_pool_config(pc)
    assert len(warnings) == 1
    assert "Reader" in warnings[0]
    assert "10" in warnings[0]
    assert "4" in warnings[0]


def test_validate_no_warn_when_reader_limit_equals_workers():
    """reader_limit == worker_threads → 无 warning (边界)。"""
    pc = PoolConfig(mootdx2_reader_limit=4, mootdx2_worker_threads=4, tdx_quant_worker_threads=10)
    assert validate_pool_config(pc) == []


def test_validate_no_warn_when_reader_limit_less_than_workers():
    """reader_limit < worker_threads → 无 warning (正常)。"""
    pc = PoolConfig(mootdx2_reader_limit=2, mootdx2_worker_threads=8, tdx_quant_worker_threads=10)
    assert validate_pool_config(pc) == []


def test_validate_does_not_raise_on_extreme_ratio():
    """极端比例 (1 vs 100) 仍 warning 不 raise — fail-open 设计。"""
    pc = PoolConfig(mootdx2_reader_limit=1, mootdx2_worker_threads=100, tdx_quant_worker_threads=10)
    warnings = validate_pool_config(pc)
    assert warnings == []  # 1 < 100, OK


# === PoolConfig frozen ===
def test_pool_config_is_frozen():
    """PoolConfig 是 frozen dataclass, 防运行时意外篡改。"""
    pc = PoolConfig(mootdx2_reader_limit=4, mootdx2_worker_threads=8, tdx_quant_worker_threads=10)
    with pytest.raises(Exception):  # FrozenInstanceError subclass of AttributeError
        pc.mootdx2_reader_limit = 5  # type: ignore[misc]


# === 默认常量 sanity ===
def test_default_constants_are_positive():
    """默认常量都是 ≥1 (避免 0 permit 把所有 Reader 闸死)。"""
    assert DEFAULT_MOOTDX2_READER_LIMIT >= 1
    assert DEFAULT_MOOTDX2_WORKER_THREADS >= 1
    assert DEFAULT_TDX_QUANT_WORKER_THREADS >= 1


def test_env_names_follow_unihive_prefix():
    """env 名都走 UNIHIVE_<SOURCE>_<FIELD> 命名 (PoolConfig docstring 约定)。"""
    for name in (
        ENV_MOOTDX2_READER_LIMIT,
        ENV_MOOTDX2_WORKER_THREADS,
        ENV_TDX_QUANT_WORKER_THREADS,
    ):
        assert name.startswith("UNIHIVE_")
        assert name.isupper()