"""ConfigStore v2: 实体表 seed/装配读取/启用位/section 更新/revision 语义。"""
from __future__ import annotations

import pytest

from unihive.core.db_assembler import assemble_config, entities_from_json
from unihive.storage.config_store import AlreadySeededError, ConfigStore, RevisionConflict

BUNDLE = {
    "sections": {
        "cache": {"enabled": True, "ttl": {"historical": 3600}},
        "routing": {},
        "gateway": {"per_upstream_timeout": 10},
    },
    "upstreams": [
        {"name": "alpha", "type": "omni", "enabled": True, "config": {"description": "a"}},
        {
            "name": "beta",
            "type": "http",
            "enabled": False,
            "config": {"base_url": "http://x", "api_key": "${BETA_KEY}"},
        },
    ],
    "tools": [
        {
            "name": "a_one",
            "routing": "a_one",
            "description": "first",
            "cache_ttl_key": "historical",
            "requires_env": None,
            "publish_mode": "default",
            "mapping": {"alpha": "a_one"},
            "spec": {
                "name": "a_one",
                "routing": "a_one",
                "description": "first",
                "cache_ttl_key": "historical",
                "params": [],
            },
            "source_file": "tools_alpha.yaml",
            "origin": "builtin",
        },
        {
            "name": "b_two",
            "routing": "b_two",
            "description": "",
            "cache_ttl_key": None,
            "requires_env": "ALLOW_X",
            "publish_mode": "default",
            "mapping": {"beta": "get_b"},
            "spec": {"name": "b_two", "routing": "b_two", "requires_env": "ALLOW_X"},
            "source_file": "tools_beta.yaml",
            "origin": "builtin",
        },
    ],
}
VERSION = "a" * 64


@pytest.fixture
async def store(tmp_path):
    s = ConfigStore(tmp_path / "config.db")
    await s.initialize()
    yield s
    await s.close()


async def test_fresh_seed_keeps_revision_zero_and_records_template(store):
    revision = await store.seed_from_bundle(VERSION, BUNDLE)
    assert revision == 0
    assert await store.is_seeded()
    assert await store.revision() == 0
    snapshot = await store.template_snapshot()
    assert snapshot is not None
    version, payload = snapshot
    assert version == VERSION
    assert payload == BUNDLE
    assert await store.template_version_seeded() == VERSION


async def test_reseed_without_force_rejected(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    with pytest.raises(AlreadySeededError):
        await store.seed_from_bundle("b" * 64, BUNDLE)


async def test_force_reseed_replaces_and_bumps_revision(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    changed = {
        **BUNDLE,
        "tools": [
            {**BUNDLE["tools"][0], "description": "changed"},
            BUNDLE["tools"][1],
        ],
    }
    revision = await store.seed_from_bundle("c" * 64, changed, force=True)
    assert revision == 1
    assert await store.template_version_seeded() == "c" * 64
    bundle = await store.load_bundle()
    assert bundle["tools"][0]["description"] == "changed"


async def test_load_bundle_preserves_order_and_json_shapes(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    bundle = await store.load_bundle()
    assert [u["name"] for u in bundle["upstreams"]] == ["alpha", "beta"]
    assert [t["name"] for t in bundle["tools"]] == ["a_one", "b_two"]
    assert bundle["upstreams"][1]["enabled"] is False
    assert bundle["upstreams"][1]["config"]["api_key"] == "${BETA_KEY}"
    assert bundle["tools"][0]["mapping"] == {"alpha": "a_one"}
    assert bundle["sections"]["cache"]["ttl"] == {"historical": 3600}
    # 还原为实体后可直接喂给装配器
    template = entities_from_json(bundle)
    assembled_cfg = assemble_config(template)
    assert assembled_cfg["upstreams"]["beta"]["type"] == "http"
    assert assembled_cfg["tools"][1]["requires_env"] == "ALLOW_X"


async def test_update_upstream_enabled_bumps_once_then_noop(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    assert await store.update_upstream_enabled("beta", True) == 1
    assert await store.update_upstream_enabled("beta", True) == 1  # 同值不 bump
    assert await store.update_upstream_enabled("beta", False) == 2
    bundle = await store.load_bundle()
    assert bundle["upstreams"][1]["enabled"] is False
    with pytest.raises(KeyError):
        await store.update_upstream_enabled("ghost", True)


async def test_update_upstream_enabled_optimistic_lock(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    with pytest.raises(RevisionConflict):
        await store.update_upstream_enabled("beta", True, expected_revision=99)


async def test_upsert_section_bumps_and_noop_on_equal(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    assert await store.upsert_section("fuyao_cache", {"tools": {"a_one": 60}}) == 1
    assert await store.upsert_section("fuyao_cache", {"tools": {"a_one": 60}}) == 1
    assert await store.upsert_section("fuyao_cache", {"tools": {}}) == 2
    bundle = await store.load_bundle()
    assert bundle["sections"]["fuyao_cache"] == {"tools": {}}


async def test_upsert_section_validates_name(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    with pytest.raises(ValueError):
        await store.upsert_section("", {"x": 1})


async def test_empty_database_is_not_seeded(store):
    assert await store.is_seeded() is False
    assert await store.template_snapshot() is None
    assert await store.template_version_seeded() is None
    bundle = await store.load_bundle()
    assert bundle == {"sections": {}, "upstreams": [], "tools": []}


async def test_get_tool_returns_full_entity_and_missing_none(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    tool = await store.get_tool("b_two")
    assert tool is not None
    assert tool["name"] == "b_two"
    assert tool["routing"] == "b_two"
    assert tool["requires_env"] == "ALLOW_X"
    assert tool["publish_mode"] == "default"
    assert tool["mapping"] == {"beta": "get_b"}
    assert tool["spec"]["name"] == "b_two"
    assert tool["position"] == 1
    assert tool["origin"] == "builtin"
    assert isinstance(tool["updated_at"], str) and tool["updated_at"]
    assert await store.get_tool("ghost") is None


async def test_get_upstream_and_section_detail(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    upstream = await store.get_upstream("beta")
    assert upstream == {
        "name": "beta",
        "type": "http",
        "enabled": False,
        "config": {"base_url": "http://x", "api_key": "${BETA_KEY}"},
        "origin": "builtin",
        "updated_at": upstream["updated_at"],
    }
    assert await store.get_upstream("ghost") is None
    section = await store.get_section("cache")
    assert section is not None
    assert section["doc"] == {"enabled": True, "ttl": {"historical": 3600}}
    assert await store.get_section("ghost") is None


async def test_update_tool_description_bumps_flips_origin_syncs_spec(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    final_name, revision = await store.update_tool("a_one", {"description": "新版描述"})
    assert (final_name, revision) == ("a_one", 1)
    tool = await store.get_tool("a_one")
    assert tool["origin"] == "user"
    assert tool["description"] == "新版描述"
    assert tool["spec"]["description"] == "新版描述"
    # 未改字段不丢
    assert tool["cache_ttl_key"] == "historical"
    assert tool["mapping"] == {"alpha": "a_one"}
    bundle = await store.load_bundle()
    assert bundle["tools"][0]["spec"]["description"] == "新版描述"


async def test_update_tool_noop_keeps_builtin_and_revision(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    final_name, revision = await store.update_tool("a_one", {"description": "first"})
    assert (final_name, revision) == ("a_one", 0)
    assert (await store.get_tool("a_one"))["origin"] == "builtin"
    assert await store.revision() == 0


async def test_update_tool_rename_keeps_position_and_columns(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    final_name, revision = await store.update_tool(
        "b_two", {"name": "c_three", "routing": "c_three"}
    )
    assert (final_name, revision) == ("c_three", 1)
    assert await store.get_tool("b_two") is None
    tool = await store.get_tool("c_three")
    assert tool["position"] == 1
    assert tool["spec"]["name"] == "c_three"
    assert tool["routing"] == "c_three"
    assert tool["spec"]["routing"] == "c_three"
    # 其余抽取列随行保留
    assert tool["requires_env"] == "ALLOW_X"
    assert tool["mapping"] == {"beta": "get_b"}
    bundle = await store.load_bundle()
    assert [t["name"] for t in bundle["tools"]] == ["a_one", "c_three"]


async def test_update_tool_rename_collision_rolls_back_entirely(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    with pytest.raises(ValueError):
        await store.update_tool("a_one", {"name": "b_two"})
    assert await store.revision() == 0
    assert await store.get_tool("a_one") is not None
    assert (await store.get_tool("b_two"))["origin"] == "builtin"


async def test_update_tool_contract_and_hidden_fields(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    _, revision = await store.update_tool(
        "a_one",
        {
            "params": [{"name": "code", "type": "str", "required": False}],
            "cache_ttl_key": None,
            "requires_env": "ALLOW_X",
            "mapping": {"alpha": "get_a"},
            "publish_mode": "hidden",
        },
    )
    assert revision == 1
    tool = await store.get_tool("a_one")
    assert tool["publish_mode"] == "hidden"
    assert tool["cache_ttl_key"] is None
    assert "cache_ttl_key" not in tool["spec"]
    assert tool["requires_env"] == "ALLOW_X"
    assert tool["spec"]["requires_env"] == "ALLOW_X"
    assert tool["mapping"] == {"alpha": "get_a"}
    assert tool["spec"]["params"] == [
        {"name": "code", "type": "str", "required": False}
    ]


async def test_update_tool_rejects_unknown_field_missing_and_stale_revision(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    with pytest.raises(ValueError):
        await store.update_tool("a_one", {"data_source_type": "offline"})
    with pytest.raises(KeyError):
        await store.update_tool("ghost", {"description": "x"})
    with pytest.raises(RevisionConflict):
        await store.update_tool(
            "a_one", {"description": "x"}, expected_revision=99
        )
    assert await store.revision() == 0


async def test_update_upstream_config_replaces_flips_origin_and_bumps(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    assert (
        await store.update_upstream_config(
            "beta", {"base_url": "http://y", "api_key": "${BETA_KEY}"}
        )
        == 1
    )
    upstream = await store.get_upstream("beta")
    assert upstream["config"] == {"base_url": "http://y", "api_key": "${BETA_KEY}"}
    assert upstream["type"] == "http"
    assert upstream["origin"] == "user"
    # 同内容再写不 bump, origin 不再翻
    assert (
        await store.update_upstream_config(
            "beta", {"base_url": "http://y", "api_key": "${BETA_KEY}"}
        )
        == 1
    )
    assert (await store.get_upstream("beta"))["origin"] == "user"
    # 连类型一起改
    assert (
        await store.update_upstream_config(
            "beta", {"base_url": "http://z"}, type="mootdx2"
        )
        == 2
    )
    changed = await store.get_upstream("beta")
    assert changed["type"] == "mootdx2"
    assert changed["config"] == {"base_url": "http://z"}


async def test_update_upstream_config_validates_boundary(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    with pytest.raises(ValueError):
        await store.update_upstream_config("beta", {"enabled": True})
    with pytest.raises(ValueError):
        # 明文密钥拒绝入库 (只允许 ${VAR} 占位)
        await store.update_upstream_config(
            "beta", {"base_url": "http://y", "api_key": "sk-literal"}
        )
    with pytest.raises(KeyError):
        await store.update_upstream_config("ghost", {"base_url": "http://y"})
    with pytest.raises(RevisionConflict):
        await store.update_upstream_config(
            "beta", {"base_url": "http://y"}, expected_revision=99
        )
    assert await store.revision() == 0


async def test_stale_expected_revision_blocks_force_reseed(store):
    await store.seed_from_bundle(VERSION, BUNDLE)
    with pytest.raises(RevisionConflict):
        await store.seed_from_bundle("d" * 64, BUNDLE, force=True, expected_revision=5)
    # 冲突整笔回滚: 模板版本未变
    assert await store.template_version_seeded() == VERSION
