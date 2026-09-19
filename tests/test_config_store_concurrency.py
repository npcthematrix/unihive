"""ConfigStore 事务原子性测试 (H1/H5/M4 根因修复)。

旧实现用 SQLite 默认 deferred 事务: SELECT 不开事务,
revision 的 SELECT→Python 比对→DML→commit 跨协程/进程非原子,
两个写者可同时通过同一 expected_revision, 把 revision 折叠、静默丢更新。

修复契约:
- 写事务 BEGIN IMMEDIATE: 后到写者在事务入口即被串行化,
  锁内重读 revision 必然看到新值, 过期 expected 抛 RevisionConflict;
- 不带 expected 的并发写也不得折叠 revision (各 bump 一次);
- 读侧多条 SELECT 在同一 BEGIN 快照内完成 (load_bundle 不撕裂)。
"""
from __future__ import annotations

import asyncio

import pytest

from unihive.storage.config_store import ConfigStore, RevisionConflict

pytestmark = pytest.mark.asyncio


def _seed_bundle() -> dict:
    return {
        "sections": {"cache": {"ttl": 30}},
        "upstreams": [
            {"name": "fuyao", "type": "http", "enabled": True, "config": {"base_url": "http://x"}}
        ],
        "tools": [
            {"name": "t1", "routing": "fuyao", "spec": {"name": "t1"}, "mapping": {}}
        ],
    }


@pytest.fixture
async def store(tmp_path):
    s = ConfigStore(tmp_path / "config.db")
    await s.initialize()
    yield s
    await s.close()


async def test_concurrent_writers_with_same_expected_exactly_one_wins(store):
    """两个写者持同一旧 revision 并发提交: 恰好一个成功, 另一个 RevisionConflict。"""
    results = await asyncio.gather(
        store.upsert_overlay("a.b", 1, expected_revision=0),
        store.upsert_overlay("c.d", 2, expected_revision=0),
        return_exceptions=True,
    )
    successes = [r for r in results if isinstance(r, int)]
    conflicts = [r for r in results if isinstance(r, RevisionConflict)]
    assert len(successes) == 1
    assert successes[0] == 1
    assert len(conflicts) == 1
    assert await store.revision() == 1
    rows = {r.path: r.value for r in await store.list_overlays()}
    assert rows == {"a.b": 1}  # 败者整笔回滚, 不留半行


async def test_concurrent_writers_without_expected_do_not_fold_revision(store):
    """两个不同 key 的并发写 (不带 expected): revision 必须到 2, 不得折叠成 1。"""
    results = await asyncio.gather(
        store.upsert_overlay("a.b", 1),
        store.upsert_overlay("c.d", 2),
    )
    assert sorted(results) == [1, 2]
    assert await store.revision() == 2
    rows = {r.path: r.value for r in await store.list_overlays()}
    assert rows == {"a.b": 1, "c.d": 2}


async def test_concurrent_section_and_upstream_writes_serialize(store):
    await store.seed_from_bundle("v1", _seed_bundle())
    assert await store.revision() == 0

    results = await asyncio.gather(
        store.upsert_section("cache", {"ttl": 60}, expected_revision=0),
        store.update_upstream_enabled("fuyao", False, expected_revision=0),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, BaseException)]
    conflicts = [r for r in results if isinstance(r, RevisionConflict)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert await store.revision() == 1

    # 胜出者的改动在, 败者的改动不在 (无交叉污染)
    up = await store.get_upstream("fuyao")
    section = await store.get_section("cache")
    winners = sum(
        [up is not None and up["enabled"] is False, section is not None and section["doc"] == {"ttl": 60}]
    )
    assert winners == 1


async def test_two_engines_same_db_expected_conflict_atomic(tmp_path):
    """跨引擎持同一旧 expected 并发: 旧实现两者都过 check (revision 折叠),
    修复后后到者锁内重读 → RevisionConflict, 恰好一个成功。"""
    s1 = ConfigStore(tmp_path / "config.db")
    s2 = ConfigStore(tmp_path / "config.db")
    await s1.initialize()
    await s2.initialize()
    try:
        results = await asyncio.gather(
            s1.upsert_overlay("a.b", 1, expected_revision=0),
            s2.upsert_overlay("c.d", 2, expected_revision=0),
            return_exceptions=True,
        )
        successes = [r for r in results if isinstance(r, int)]
        conflicts = [r for r in results if isinstance(r, RevisionConflict)]
        assert len(successes) == 1
        assert len(conflicts) == 1
        assert await s1.revision() == 1
    finally:
        await s1.close()
        await s2.close()


async def test_two_engines_same_db_concurrent_writes_serialize(tmp_path):
    """跨引擎 (= 跨连接/进程同态): 后到者在 BEGIN IMMEDIATE 等待, 锁内重读 revision。"""
    s1 = ConfigStore(tmp_path / "config.db")
    s2 = ConfigStore(tmp_path / "config.db")
    await s1.initialize()
    await s2.initialize()
    try:
        results = await asyncio.gather(
            s1.upsert_overlay("a.b", 1),
            s2.upsert_overlay("c.d", 2),
        )
        assert sorted(results) == [1, 2]
        assert await s1.revision() == 2
    finally:
        await s1.close()
        await s2.close()


async def test_upgrade_conflict_rolls_back_entity_rewrite(store):
    """升级全量重写撞过期 expected: RevisionConflict 且实体表完全不动。"""
    await store.seed_from_bundle("v1", _seed_bundle())

    # 先制造一笔并发改动, 把 revision 推到 1
    await store.upsert_section("cache", {"ttl": 99})

    new_bundle = _seed_bundle()
    merged = _seed_bundle()
    merged["tools"].append(
        {"name": "t2", "routing": "fuyao", "spec": {"name": "t2"}, "mapping": {}}
    )
    with pytest.raises(RevisionConflict):
        await store.apply_upgrade_bundle(
            "v2", new_bundle, merged, expected_revision=0
        )
    assert await store.revision() == 1
    bundle = await store.load_bundle()
    assert [t["name"] for t in bundle["tools"]] == ["t1"]
    assert bundle["sections"]["cache"] == {"ttl": 99}


async def test_load_bundle_reads_consistent_snapshot(store):
    """load_bundle 三条 SELECT 在同一只读事务内; 基本回归 (M4 同 BEGIN 修复)。"""
    await store.seed_from_bundle("v1", _seed_bundle())
    bundle = await store.load_bundle()
    assert set(bundle) == {"sections", "upstreams", "tools"}
    assert [u["name"] for u in bundle["upstreams"]] == ["fuyao"]
    assert [t["name"] for t in bundle["tools"]] == ["t1"]
