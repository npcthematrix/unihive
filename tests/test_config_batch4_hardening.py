"""配置审视批次4: 快速后端硬化 (H6 + M1)。

- H6: 控制台写库的三条实体写路径 (update_tool / upsert_tools /
  upsert_section) 与上游连接块写入门同构 —— 疑似明文密钥 fail-closed;
  工具 params 条目不许多余键透传进 spec (键白名单)。
- M1: RevisionConflict 的 409 消息必须是中文且含 "revision 不一致",
  与 fuyao / enabled 写路径的 VersionConflict 文案一致。
"""
from __future__ import annotations

import pytest

from unihive.core.entity_edit import EntityEditError, apply_tool_patch
from unihive.storage.config_store import ConfigStore, RevisionConflict
from unihive.utils import console_api as ca

BUNDLE = {
    "sections": {},
    "upstreams": [
        {"name": "demo-gateway", "type": "demo_gateway",
         "enabled": True, "config": {}},
    ],
    "tools": [
        {
            "name": "demo_a",
            "routing": "demo_a",
            "description": "old desc",
            "cache_ttl_key": None,
            "requires_env": None,
            "publish_mode": "default",
            "mapping": {"demo-gateway": "demo_a"},
            "spec": {"name": "demo_a", "routing": "demo_a",
                     "description": "old desc", "params": [],
                     "upstream_tool_mapping": {"demo-gateway": "demo_a"},
                     "data_source_type": "online"},
            "source_file": "tools_demo.yaml",
            "origin": "builtin",
        },
    ],
}
VERSION = "c" * 64


def _row():
    return {
        "name": "a_one",
        "routing": "a_one",
        "description": "first",
        "cache_ttl_key": None,
        "requires_env": None,
        "publish_mode": "default",
        "mapping": {"alpha": "a_one"},
        "spec": {
            "name": "a_one",
            "routing": "a_one",
            "description": "first",
            "params": [{"name": "code", "type": "str", "required": True}],
        },
        "source_file": "tools_alpha.yaml",
        "position": 0,
        "origin": "builtin",
    }


@pytest.fixture
async def store(tmp_path):
    s = ConfigStore(tmp_path / "config.db")
    await s.initialize()
    await s.seed_from_bundle(VERSION, BUNDLE)
    yield s
    await s.close()


# ========== H6: params 键白名单 ==========


def test_h6_params_reject_unknown_keys():
    with pytest.raises(EntityEditError, match="params\\[0\\]"):
        apply_tool_patch(
            _row(),
            {"params": [{"name": "code", "sneaky_extra": 1}]},
        )
    with pytest.raises(EntityEditError, match="params\\[1\\]"):
        apply_tool_patch(
            _row(),
            {"params": [
                {"name": "code"},
                {"name": "x", "description": "ok", "wat": "no"},
            ]},
        )


def test_h6_params_accept_all_factory_keys_including_normalize():
    params = [{
        "name": "code",
        "type": "str",
        "required": True,
        "description": "代码",
        "enum": ["600519"],
        "normalize": "code",
    }]
    out = apply_tool_patch(_row(), {"params": params})
    assert out["spec"]["params"] == params


def test_h6_params_normalize_must_be_nonempty_string():
    with pytest.raises(EntityEditError):
        apply_tool_patch(
            _row(), {"params": [{"name": "code", "normalize": 123}]}
        )
    with pytest.raises(EntityEditError):
        apply_tool_patch(
            _row(), {"params": [{"name": "code", "normalize": ""}]}
        )


# ========== H6: 实体写路径明文密钥 fail-closed ==========


async def test_h6_update_tool_rejects_plaintext_secret_without_persisting(tmp_path):
    # 低层 seed 复刻一条"历史遗留"含明文密钥 spec 的行 (写入门上线前可能
    # 落库的存量); 此后任何编辑都必须 fail-closed, 绝不把泄露 spec 再写一遍。
    leaky_bundle = {
        "sections": {},
        "upstreams": [
            {"name": "demo-gateway", "type": "demo_gateway",
             "enabled": True, "config": {}},
        ],
        "tools": [
            {
                "name": "demo_a",
                "routing": "demo_a",
                "description": "old desc",
                "cache_ttl_key": None,
                "requires_env": None,
                "publish_mode": "default",
                "mapping": {"demo-gateway": "demo_a"},
                "spec": {"name": "demo_a", "routing": "demo_a",
                         "description": "old desc", "params": [],
                         "token": "sk-leaked-123"},
                "source_file": "tools_demo.yaml",
                "origin": "builtin",
            },
        ],
    }
    s = ConfigStore(tmp_path / "config.db")
    await s.initialize()
    await s.seed_from_bundle(VERSION, leaky_bundle)
    try:
        with pytest.raises(ValueError, match="[Pp]laintext secret|明文密钥"):
            await s.update_tool("demo_a", {"description": "新描述"})

        # 行与 revision 都不变 (异常在任何 DML 之前)
        row = await s.get_tool("demo_a")
        assert row["description"] == "old desc"
        assert await s.revision() == 0
    finally:
        await s.close()


async def test_h6_update_tool_placeholder_secret_key_is_allowed(store):
    # ${VAR} 占位符是配置库唯一允许的密钥形态 (与上游连接块门同语义)
    rev = await store.update_tool(
        "demo_a",
        {"params": [{"name": "code"}, {"name": "token"}]},
    )
    assert rev  # 正常写入不被误伤


async def test_h6_upsert_tools_rejects_plaintext_secret_batch_rolls_back(store):
    bad_entity = {
        "name": "demo_evil",
        "routing": "demo_evil",
        "description": "",
        "mapping": {"demo-gateway": "demo_evil"},
        "spec": {
            "name": "demo_evil",
            "routing": "demo_evil",
            "params": [{"name": "q", "api_key": "sk-leaked-in-param"}],
            "upstream_tool_mapping": {"demo-gateway": "demo_evil"},
        },
        "source_file": "openapi-import",
    }
    good_entity = {
        "name": "demo_good",
        "routing": "demo_good",
        "description": "",
        "mapping": {"demo-gateway": "demo_good"},
        "spec": {"name": "demo_good", "routing": "demo_good", "params": []},
        "source_file": "openapi-import",
    }
    with pytest.raises(ValueError, match="[Pp]laintext secret|明文密钥"):
        await store.upsert_tools([good_entity, bad_entity])

    # 整笔回滚: 好实体也不得落库, revision 不 bump
    assert await store.get_tool("demo_good") is None
    assert await store.get_tool("demo_evil") is None
    assert await store.revision() == 0


async def test_h6_upsert_tools_allows_placeholder_under_secret_named_key(store):
    entity = {
        "name": "demo_ph",
        "routing": "demo_ph",
        "description": "",
        "mapping": {"demo-gateway": "demo_ph"},
        "spec": {"name": "demo_ph", "routing": "demo_ph", "params": [],
                 "password": "${UPSTREAM_PASSWORD}"},
        "source_file": "openapi-import",
    }
    rev = await store.upsert_tools([entity])
    assert rev == 1


async def test_h6_upsert_section_rejects_plaintext_secret(store):
    with pytest.raises(ValueError, match="[Pp]laintext secret|明文密钥"):
        await store.upsert_section(
            "proxy_settings", {"token": "raw-secret-value"}, origin="user"
        )
    assert await store.get_section("proxy_settings") is None
    assert await store.revision() == 0


async def test_h6_upsert_section_allows_placeholder_and_regular_sections(store):
    rev = await store.upsert_section(
        "proxy_settings", {"token": "${PROXY_TOKEN}"}, origin="user"
    )
    assert rev == 1
    # fuyao_cache 常规形态不被误伤
    rev2 = await store.upsert_section(
        "fuyao_cache", {"tools": {"get_a_share_history": 60}}, origin="user"
    )
    assert rev2 == 2


# ========== M1: RevisionConflict 中文 409 ==========


def test_m1_revision_conflict_message_is_chinese():
    exc = RevisionConflict(expected=3, actual=5)
    assert exc.expected == 3
    assert exc.actual == 5
    assert "revision 不一致" in str(exc)


async def test_m1_entity_edit_error_mapping_carries_chinese_409():
    resp = ca._entity_edit_error_response(RevisionConflict(1, 2))
    assert resp.status_code == 409
    import json as _json

    payload = _json.loads(resp.body.decode())
    assert payload["error"] == "revision_conflict"
    assert "revision 不一致" in payload["message"]
    assert payload["expected"] == 1
    assert payload["actual"] == 2

