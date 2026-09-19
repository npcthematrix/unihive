"""P3-6: 出厂更新编排 — store.apply_upgrade_bundle / validate_resolutions /
upgrade_database (CLI 纯库路径) / write_upgrade_backup / 运行态候选装配。

merge 引擎本身的语义在 test_config_upgrade_merge.py 覆盖; 这里只覆盖
"合并结果如何事务化落库 + 快照前推 + lint/冲突门 + 备份"。
"""
from __future__ import annotations

import copy
import json

import pytest

from src.unihive.core import config_db
from src.unihive.core.config_lint import LintRejected
from src.unihive.core.config_upgrade import merge_bundles
from src.unihive.storage.config_store import ConfigStore, RevisionConflict

from tests.test_config_store_v2 import BUNDLE, VERSION


@pytest.fixture
async def store(tmp_path):
    s = ConfigStore(tmp_path / "config.db")
    await s.initialize()
    yield s
    await s.close()


def _tool(bundle, name):
    return next(t for t in bundle["tools"] if t["name"] == name)


def _factory_v2():
    """出厂 v2: a_one 描述改为 factory v2 (列 + spec 镜像)。"""
    new = copy.deepcopy(BUNDLE)
    _tool(new, "a_one")["description"] = "factory v2"
    _tool(new, "a_one")["spec"]["description"] = "factory v2"
    return new


def _factory_with_cthree():
    """出厂 v2: 新增 c_three (alpha 背书, 可过 lint)。"""
    new = copy.deepcopy(BUNDLE)
    new["tools"].append(
        {
            "name": "c_three",
            "routing": "c_three",
            "description": "third",
            "cache_ttl_key": "historical",
            "requires_env": None,
            "publish_mode": "default",
            "mapping": {"alpha": "c_three"},
            "spec": {
                "name": "c_three",
                "routing": "c_three",
                "description": "third",
                "cache_ttl_key": "historical",
                "params": [],
            },
            "source_file": "tools_alpha.yaml",
            "origin": "builtin",
        }
    )
    return new


# --- ConfigStore.apply_upgrade_bundle -------------------------------------


async def test_apply_upgrade_requires_seeded_store(store):
    merged = merge_bundles({}, {}, BUNDLE)
    with pytest.raises(RuntimeError, match="no seeded entities"):
        await store.apply_upgrade_bundle(VERSION, BUNDLE, merged.bundle)


async def test_apply_upgrade_replaces_entities_and_advances_snapshot(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    new_bundle = _factory_with_cthree()
    result = merge_bundles(BUNDLE, BUNDLE, new_bundle)

    revision = await store.apply_upgrade_bundle(
        "b" * 64,
        new_bundle,
        result.bundle,
        section_origins=config_db.section_origins(new_bundle, result.bundle),
    )
    assert revision == 1

    # 快照前推到新出厂 (下次合并的 old 基准)
    snap_version, snap_bundle = await store.template_snapshot()
    assert snap_version == "b" * 64
    assert snap_bundle == new_bundle
    assert await store.template_version_seeded() == "b" * 64

    cur = await store.load_bundle()
    assert [t["name"] for t in cur["tools"]] == ["a_one", "b_two", "c_three"]
    cthree = next(t for t in cur["tools"] if t["name"] == "c_three")
    assert cthree["origin"] == "builtin"
    # 行 revision 记本次
    assert await store.revision() == 1


async def test_apply_upgrade_preserves_user_origins_and_section_origin(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    # 用户在库内改了 a_one 描述 + cache 段文档
    await store.update_tool("a_one", {"description": "user desc"})
    await store.upsert_section(
        "cache", {"enabled": False, "ttl": {"historical": 99}}, origin="user"
    )
    cur_bundle = await store.load_bundle()

    new_bundle = _factory_v2()  # 出厂只改 a_one 描述 → 冲突, 默认 keep_cur
    result = merge_bundles(BUNDLE, cur_bundle, new_bundle)
    # 描述冲突未裁决: 合并结果保留用户值
    assert _tool(result.bundle, "a_one")["description"] == "user desc"

    revision = await store.apply_upgrade_bundle(
        "c" * 64,
        new_bundle,
        result.bundle,
        section_origins=config_db.section_origins(new_bundle, result.bundle),
    )
    assert revision == await store.revision()
    cur2 = await store.load_bundle()
    a_one = _tool(cur2, "a_one")
    assert a_one["description"] == "user desc"
    assert a_one["origin"] == "user"
    # 用户改过的 cache 段 origin 保持 user
    cache_section = await store.get_section("cache")
    assert cache_section["origin"] == "user"
    assert cache_section["doc"] == {"enabled": False, "ttl": {"historical": 99}}


async def test_apply_upgrade_optimistic_lock_conflict_rolls_back(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    result = merge_bundles(BUNDLE, BUNDLE, _factory_with_cthree())
    with pytest.raises(RevisionConflict):
        await store.apply_upgrade_bundle(
            "d" * 64,
            _factory_with_cthree(),
            result.bundle,
            expected_revision=99,
        )
    # 整笔回滚: 快照/实体/revision 全不动
    assert await store.revision() == 0
    version, payload = await store.template_snapshot()
    assert version == VERSION and payload == BUNDLE
    cur = await store.load_bundle()
    assert [t["name"] for t in cur["tools"]] == ["a_one", "b_two"]


# --- validate_resolutions --------------------------------------------------


def test_validate_resolutions_shapes():
    assert config_db.validate_resolutions(None) == {}
    assert config_db.validate_resolutions({}) == {}
    resolved = config_db.validate_resolutions(
        {"tool:x:description": "accept_new", "upstream:u:config": "keep_cur"}
    )
    assert resolved["tool:x:description"] == "accept_new"
    for bad in (
        [],
        {"k": 1},
        {"k": "accept_old"},
        {12: "accept_new"},
        {"": "accept_new"},
    ):
        with pytest.raises(ValueError):
            config_db.validate_resolutions(bad)


# --- upgrade_database (CLI 路径) -------------------------------------------


def _patch_plan(monkeypatch, new_version, new_bundle):
    """让 plan_config_upgrade 走真实 merge 但不读 YAML。"""

    def _fake_plan(old_bundle, cur_bundle, *, config_path, resolutions=None):
        result = merge_bundles(
            old_bundle, cur_bundle, new_bundle, resolutions=resolutions
        )
        return new_version, new_bundle, result

    monkeypatch.setattr(config_db, "plan_config_upgrade", _fake_plan)


async def test_upgrade_database_noop_short_circuits(store, monkeypatch, tmp_path):
    await store.seed_from_bundle(VERSION, BUNDLE)
    _patch_plan(monkeypatch, VERSION, copy.deepcopy(BUNDLE))
    outcome = await config_db.upgrade_database(
        store, config_path=None, backup_dir=tmp_path / "backups"
    )
    assert outcome["changed"] is False
    assert outcome["backup_path"] is None
    assert await store.revision() == 0
    assert not (tmp_path / "backups").exists()


async def test_upgrade_database_apply_flows_factory_change_and_writes_backup(
    store, monkeypatch, tmp_path
):
    await store.seed_from_bundle(VERSION, BUNDLE)
    new_bundle = _factory_with_cthree()
    _patch_plan(monkeypatch, "e" * 64, new_bundle)

    backup_dir = tmp_path / "backups"
    outcome = await config_db.upgrade_database(
        store, config_path=None, backup_dir=backup_dir
    )
    assert outcome["changed"] is True
    assert outcome["revision"] == 1
    assert outcome["summary"]["added"] == 1

    backup_files = list(backup_dir.glob("config-upgrade-*.json"))
    assert len(backup_files) == 1
    payload = json.loads(backup_files[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "unihive-config-upgrade-backup"
    assert payload["revision_before"] == 0
    assert [t["name"] for t in payload["bundle"]["tools"]] == ["a_one", "b_two"]


async def test_upgrade_database_rejects_unresolved_conflicts(
    store, monkeypatch, tmp_path
):
    await store.seed_from_bundle(VERSION, BUNDLE)
    await store.update_tool("a_one", {"description": "user desc"})
    cur_bundle = await store.load_bundle()
    new_bundle = _factory_v2()
    _patch_plan(monkeypatch, "f" * 64, new_bundle)

    # monkeypatch 的 plan 需要 cur; upgrade_database 自己读 cur,
    # 这里 cur 已含用户改动; 直接断言 ValueError。
    with pytest.raises(ValueError, match="unresolved field conflict"):
        await config_db.upgrade_database(
            store, config_path=None, backup_dir=tmp_path / "backups"
        )
    assert await store.revision() == 1  # 用户编辑那一笔; 升级未追加
    assert (await store.get_tool("a_one"))["description"] == "user desc"


async def test_upgrade_database_accept_new_resolution_applies(
    store, monkeypatch, tmp_path
):
    await store.seed_from_bundle(VERSION, BUNDLE)
    await store.update_tool("a_one", {"description": "user desc"})
    new_bundle = _factory_v2()
    _patch_plan(monkeypatch, "9" * 64, new_bundle)

    outcome = await config_db.upgrade_database(
        store,
        config_path=None,
        resolutions={
            "tool:a_one:description": "accept_new",
            "tool:a_one:spec.description": "accept_new",
        },
        backup_dir=tmp_path / "backups",
    )
    assert outcome["changed"] is True
    tool = await store.get_tool("a_one")
    assert tool["description"] == "factory v2"
    assert tool["origin"] == "builtin"  # 接受厂方值后回到出厂签名


async def test_upgrade_database_lint_error_blocks_apply(store, monkeypatch, tmp_path):
    await store.seed_from_bundle(VERSION, BUNDLE)
    bad = copy.deepcopy(BUNDLE)
    bad["tools"].append(
        {
            "name": "ghost_tool",
            "routing": "ghost_tool",
            "description": "",
            "cache_ttl_key": None,
            "requires_env": None,
            "publish_mode": "default",
            "mapping": {"ghost_upstream": "x"},
            "spec": {"name": "ghost_tool", "routing": "ghost_tool"},
            "source_file": "tools_x.yaml",
            "origin": "builtin",
        }
    )
    _patch_plan(monkeypatch, "a" * 64, bad)
    with pytest.raises(LintRejected):
        await config_db.upgrade_database(
            store, config_path=None, backup_dir=tmp_path / "backups"
        )
    assert await store.revision() == 0
    assert await store.get_tool("ghost_tool") is None


async def test_upgrade_database_requires_snapshot(store, monkeypatch, tmp_path):
    # 已初始化但从未 seed
    with pytest.raises(RuntimeError, match="no factory template snapshot"):
        await config_db.upgrade_database(
            store, config_path=None, backup_dir=tmp_path / "backups"
        )


# --- assemble_runtime_candidate --------------------------------------------


async def test_assemble_runtime_candidate_without_bootstrap_matches_store_bundle(
    monkeypatch,
):
    monkeypatch.delenv("ALLOW_X", raising=False)
    candidate, specs, hidden = config_db.assemble_runtime_candidate(
        BUNDLE, config_path=None
    )
    names = {s["name"] for s in specs}
    # b_two 有 requires_env=ALLOW_X: 不设该变量 → 被 env 门控滤掉
    assert "a_one" in names
    assert "b_two" not in names
    assert hidden == set()
    # 装配器把异型连接块拍平到 upstream 块上 (非嵌套 config)
    assert candidate["upstreams"]["beta"]["api_key"] == "${BETA_KEY}"
    assert candidate["upstreams"]["beta"]["type"] == "http"
    assert candidate["upstreams"]["beta"]["enabled"] is False
