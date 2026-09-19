"""ConfigStore.upsert_tools — OpenAPI 实时导入批量写 (origin='user')。

语义契约:
- 单事务批量, 全批只 bump 一次全局 revision; 净效果无变化不 bump;
- 新工具在 max(position)+1 起顺序追加;
- 已存在行只刷契约列 (routing/description/mapping/spec/source_file),
  position 保持, 本地门控列 (cache_ttl_key/requires_env/publish_mode) 恒保留 —
  导入只改契约, 用户 hidden/TTL/env 门控不能被刷新冲掉;
- origin 恒为 'user'; expected_revision 过期 → RevisionConflict 整笔回滚。
"""
from __future__ import annotations

import pytest

from unihive.storage.config_store import ConfigStore, RevisionConflict

BUNDLE = {
    "sections": {},
    "upstreams": [
        {"name": "THS-LOCAL-GATEWAY", "type": "ths_local_gateway",
         "enabled": True, "config": {}},
    ],
    "tools": [
        {
            "name": "fqgate_a",
            "routing": "fqgate_a",
            "description": "old desc",
            "cache_ttl_key": "realtime_quote",
            "requires_env": None,
            "publish_mode": "hidden",
            "mapping": {"THS-LOCAL-GATEWAY": "fqgate_a"},
            "spec": {"name": "fqgate_a", "routing": "fqgate_a",
                     "description": "old desc", "params": [],
                     "cache_ttl_key": "realtime_quote",
                     "upstream_tool_mapping": {"THS-LOCAL-GATEWAY": "fqgate_a"},
                     "data_source_type": "online"},
            "source_file": "tools_ths_local.yaml",
            "origin": "builtin",
        },
        {
            "name": "fqgate_b",
            "routing": "fqgate_b",
            "description": "b",
            "cache_ttl_key": None,
            "requires_env": "ALLOW_X",
            "publish_mode": "default",
            "mapping": {"THS-LOCAL-GATEWAY": "fqgate_b"},
            "spec": {"name": "fqgate_b", "routing": "fqgate_b",
                     "requires_env": "ALLOW_X", "params": []},
            "source_file": "tools_ths_local.yaml",
            "origin": "builtin",
        },
    ],
}
VERSION = "b" * 64


def _import_entity(name: str, description: str = "", *, origin: str = "user") -> dict:
    """convert_openapi_tools 产的实体行形态 (门控列 None/'default')。"""
    return {
        "name": name,
        "routing": name,
        "description": description,
        "cache_ttl_key": None,
        "requires_env": None,
        "publish_mode": "default",
        "mapping": {"THS-LOCAL-GATEWAY": name},
        "spec": {
            "name": name,
            "routing": name,
            "description": description,
            "params": [],
            "upstream_tool_mapping": {"THS-LOCAL-GATEWAY": name},
            "data_source_type": "online",
        },
        "source_file": "openapi-import",
        "origin": origin,
    }


@pytest.fixture
async def seeded_store(tmp_path):
    s = ConfigStore(tmp_path / "config.db")
    await s.initialize()
    await s.seed_from_bundle(VERSION, BUNDLE)
    yield s
    await s.close()


async def test_new_tools_append_after_max_position_as_user(seeded_store):
    rev = await seeded_store.upsert_tools([_import_entity("fqgate_c"),
                                           _import_entity("fqgate_d")])
    assert rev == 1
    by_name = {t["name"]: t for t in (await seeded_store.load_bundle())["tools"]}
    # position 来自 get_tool (load_bundle 不带 position)
    c, d = await seeded_store.get_tool("fqgate_c"), await seeded_store.get_tool("fqgate_d")
    assert c["position"] == 2
    assert d["position"] == 3
    assert c["origin"] == "user"
    assert c["source_file"] == "openapi-import"
    assert c["mapping"] == {"THS-LOCAL-GATEWAY": "fqgate_c"}
    assert by_name["fqgate_c"]["spec"]["data_source_type"] == "online"


async def test_existing_row_keeps_position_and_local_gate_columns(seeded_store):
    updated = _import_entity("fqgate_a", description="new desc")
    rev = await seeded_store.upsert_tools([updated])
    assert rev == 1
    a = await seeded_store.get_tool("fqgate_a")
    assert a["position"] == 0                      # 位置不动
    assert a["origin"] == "user"                  # 导入即翻 user
    assert a["source_file"] == "openapi-import"
    assert a["description"] == "new desc"
    assert a["spec"]["description"] == "new desc"
    # 本地门控列保留
    assert a["publish_mode"] == "hidden"
    assert a["cache_ttl_key"] == "realtime_quote"
    assert a["spec"]["cache_ttl_key"] == "realtime_quote"

    b = await seeded_store.get_tool("fqgate_b")
    assert b["requires_env"] == "ALLOW_X"
    assert b["spec"]["requires_env"] == "ALLOW_X"


async def test_origin_forced_user_even_if_entity_claims_builtin(seeded_store):
    await seeded_store.upsert_tools([_import_entity("fqgate_a", "x", origin="builtin")])
    assert (await seeded_store.get_tool("fqgate_a"))["origin"] == "user"


async def test_mixed_batch_bumps_revision_once(seeded_store):
    rev = await seeded_store.upsert_tools([
        _import_entity("fqgate_a", description="changed"),
        _import_entity("fqgate_c"),
        _import_entity("fqgate_d"),
    ])
    assert rev == 1
    assert await seeded_store.revision() == 1


async def test_no_net_change_does_not_bump(seeded_store):
    # 与现状逐字段一致 (门控列保留后净效果为零): 用当前 spec 原样回灌
    a = await seeded_store.get_tool("fqgate_a")
    entity = {
        "name": a["name"],
        "routing": a["routing"],
        "description": a["description"],
        "mapping": a["mapping"],
        "spec": a["spec"],
        "source_file": a["source_file"],
        "origin": "user",
    }
    rev = await seeded_store.upsert_tools([entity])
    assert rev == 0
    assert await seeded_store.revision() == 0


async def test_stale_revision_conflicts_and_rolls_back(seeded_store):
    with pytest.raises(RevisionConflict):
        await seeded_store.upsert_tools(
            [_import_entity("fqgate_c"), _import_entity("fqgate_a", "x")],
            expected_revision=99,
        )
    # 整笔回滚: 新行不在, 旧行未改, revision 不动
    assert await seeded_store.get_tool("fqgate_c") is None
    assert (await seeded_store.get_tool("fqgate_a"))["description"] == "old desc"
    assert await seeded_store.revision() == 0


async def test_empty_batch_is_noop(seeded_store):
    assert await seeded_store.upsert_tools([]) == 0
