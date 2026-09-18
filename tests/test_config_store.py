"""ConfigStore (data/config.db 薄覆盖层) 单元测试。"""
from __future__ import annotations

import pytest

from unihive.storage.config_store import ConfigStore, OverlayRow, RevisionConflict

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def store(tmp_path):
    s = ConfigStore(tmp_path / "data" / "config.db")
    await s.initialize()
    yield s
    await s.close()


async def test_initialize_creates_schema_and_zero_revision(store, tmp_path):
    assert (tmp_path / "data" / "config.db").exists()
    assert await store.revision() == 0
    assert await store.list_overlays() == []


async def test_reopen_persists_rows(tmp_path):
    s1 = ConfigStore(tmp_path / "config.db")
    await s1.initialize()
    rev = await s1.upsert_overlay("upstreams.fuyao.enabled", False)
    await s1.close()

    s2 = ConfigStore(tmp_path / "config.db")
    await s2.initialize()
    assert await s2.revision() == rev
    row = await s2.get_overlay("upstreams.fuyao.enabled")
    assert row is not None
    assert row.value is False
    assert row.revision == 1
    assert row.updated_at
    await s2.close()


async def test_upsert_bumps_revision_and_returns_row(store):
    rev = await store.upsert_overlay("fuyao_cache.tools.get_x", 60)
    assert rev == 1
    assert await store.revision() == 1

    row = await store.get_overlay("fuyao_cache.tools.get_x")
    assert isinstance(row, OverlayRow)
    assert row.path == "fuyao_cache.tools.get_x"
    assert row.value == 60
    assert row.revision == 1


async def test_upsert_same_value_is_noop(store):
    await store.upsert_overlay("a.b", 60)
    rev = await store.upsert_overlay("a.b", 60)
    assert rev == 1
    assert await store.revision() == 1


async def test_upsert_different_value_bumps_again(store):
    await store.upsert_overlay("a.b", 60)
    assert await store.upsert_overlay("a.b", 120) == 2
    row = await store.get_overlay("a.b")
    assert row.value == 120
    assert row.revision == 2


async def test_list_overlays_prefix_filter_and_order(store):
    await store.upsert_overlay("upstreams.fuyao.enabled", False)
    await store.upsert_overlay("fuyao_cache.tools.get_x", 60)
    await store.upsert_overlay("fuyao_cache.tools.get_y", 10)

    rows = await store.list_overlays()
    assert [r.path for r in rows] == sorted(r.path for r in rows)

    prefixed = await store.list_overlays(prefix="fuyao_cache.tools.")
    assert [r.path for r in prefixed] == [
        "fuyao_cache.tools.get_x",
        "fuyao_cache.tools.get_y",
    ]
    assert {r.value for r in prefixed} == {60, 10}


async def test_get_missing_returns_none(store):
    assert await store.get_overlay("nope.path") is None


async def test_delete_existing_bumps_revision(store):
    await store.upsert_overlay("a.b", 1)
    rev = await store.delete_overlay("a.b")
    assert rev == 2
    assert await store.get_overlay("a.b") is None


async def test_delete_missing_is_noop(store):
    rev = await store.delete_overlay("a.b")
    assert rev == 0
    assert await store.revision() == 0


async def test_reset_clears_all_and_bumps_once(store):
    await store.upsert_overlay("a.b", 1)
    await store.upsert_overlay("c.d", 2)
    rev = await store.reset()
    assert rev == 3
    assert await store.list_overlays() == []


async def test_reset_empty_is_noop(store):
    assert await store.reset() == 0


async def test_expected_revision_mismatch_raises(store):
    with pytest.raises(RevisionConflict) as exc:
        await store.upsert_overlay("a.b", 1, expected_revision=5)
    assert exc.value.expected == 5
    assert exc.value.actual == 0
    # 事务失败不留痕
    assert await store.revision() == 0
    assert await store.list_overlays() == []


async def test_expected_revision_none_skips_check(store):
    await store.upsert_overlay("a.b", 1, expected_revision=None)
    assert await store.revision() == 1


async def test_expected_revision_matching_passes(store):
    await store.upsert_overlay("a.b", 1, expected_revision=0)
    assert await store.revision() == 1


async def test_apply_changes_mixed_is_single_revision_bump(store):
    await store.upsert_overlay("a.b", 1)
    await store.upsert_overlay("a.c", 2)
    rev = await store.apply_changes(
        upserts={"a.c": 3, "a.d": 4},
        deletes=["a.b", "a.does_not_exist"],
        expected_revision=2,
    )
    assert rev == 3
    paths = {r.path: r.value for r in await store.list_overlays()}
    assert paths == {"a.c": 3, "a.d": 4}
    # 同一次事务写入的行记录同一 revision
    rows = await store.list_overlays()
    assert {r.revision for r in rows} == {3}


async def test_apply_changes_conflict_is_atomic(store):
    await store.upsert_overlay("a.b", 1)
    with pytest.raises(RevisionConflict):
        await store.apply_changes(
            upserts={"a.c": 2}, deletes=["a.b"], expected_revision=99
        )
    assert await store.revision() == 1
    assert (await store.get_overlay("a.b")).value == 1
    assert await store.get_overlay("a.c") is None


@pytest.mark.parametrize(
    "bad_path",
    ["", ".a.b", "a..b", "a.b.", ".", "a\nb", "a/../b"],
)
async def test_invalid_path_rejected(store, bad_path):
    with pytest.raises(ValueError):
        await store.upsert_overlay(bad_path, 1)
    with pytest.raises(ValueError):
        await store.delete_overlay(bad_path)
