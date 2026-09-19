"""控制台实体编辑纯函数层 (P3): 字段白名单/类型校验/抽取列 ↔ spec 同步。"""
from __future__ import annotations

import copy

import pytest

from unihive.core.entity_edit import (
    TOOL_CONTRACT_FIELDS,
    TOOL_DESCRIPTION_FIELDS,
    EntityEditError,
    apply_tool_patch,
    contract_fields_in_patch,
    validate_upstream_config,
)


def make_row(**overrides):
    row = {
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
            "params": [{"name": "code", "type": "str", "required": True}],
        },
        "source_file": "tools_alpha.yaml",
        "position": 0,
        "origin": "builtin",
    }
    row.update(overrides)
    return row


# --- 字段分层 -----------------------------------------------------------

def test_field_sets_are_disjoint_and_cover_contract():
    assert TOOL_DESCRIPTION_FIELDS == {
        "description",
        "params",
        "cache_ttl_key",
        "publish_mode",
    }
    assert TOOL_CONTRACT_FIELDS == {"name", "routing", "requires_env", "mapping"}
    assert not (TOOL_DESCRIPTION_FIELDS & TOOL_CONTRACT_FIELDS)


def test_contract_fields_in_patch_classifies():
    assert contract_fields_in_patch({"description": "x", "cache_ttl_key": None}) == set()
    assert contract_fields_in_patch(
        {"description": "x", "mapping": {"a": "b"}}
    ) == {"mapping"}
    assert contract_fields_in_patch({"name": "x", "publish_mode": "hidden"}) == {"name"}


# --- 描述层 -------------------------------------------------------------

def test_description_edit_syncs_column_and_spec_without_mutation():
    row = make_row()
    snapshot = copy.deepcopy(row)
    out = apply_tool_patch(row, {"description": "改过的描述"})
    assert out["description"] == "改过的描述"
    assert out["spec"]["description"] == "改过的描述"
    # 其余列与 spec 键不变
    assert out["cache_ttl_key"] == "historical"
    assert out["mapping"] == {"alpha": "a_one"}
    assert out["spec"]["params"] == row["spec"]["params"]
    # 输入不可变
    assert row == snapshot


def test_params_edit_replaces_spec_params():
    row = make_row()
    params = [
        {"name": "code", "type": "str", "required": True, "description": "代码"},
        {"name": "period", "type": "str", "required": False, "enum": ["1d", "1w"]},
    ]
    out = apply_tool_patch(row, {"params": params})
    assert out["spec"]["params"] == params
    # 深拷贝隔离: 改入参不影响结果
    params[0]["description"] = "tampered"
    assert out["spec"]["params"][0]["description"] == "代码"


@pytest.mark.parametrize(
    "params",
    [
        [{"name": "code"}, {"name": "code"}],  # 重名
        [{"name": ""}],  # 空名
        [{"name": 123}],  # 非字符串名
        [{"name": "x", "required": "yes"}],  # required 非 bool
        [{"name": "x", "enum": "1d"}],  # enum 非列表
        ["x"],  # 元素非 dict
    ],
)
def test_params_edit_rejects_bad_shapes(params):
    with pytest.raises(EntityEditError):
        apply_tool_patch(make_row(), {"params": params})


def test_ttl_set_and_clear_syncs_spec_key():
    out = apply_tool_patch(make_row(), {"cache_ttl_key": "realtime_quote"})
    assert out["cache_ttl_key"] == "realtime_quote"
    assert out["spec"]["cache_ttl_key"] == "realtime_quote"
    cleared = apply_tool_patch(out, {"cache_ttl_key": None})
    assert cleared["cache_ttl_key"] is None
    assert "cache_ttl_key" not in cleared["spec"]


def test_publish_mode_hidden_and_unknown_rejected():
    out = apply_tool_patch(make_row(), {"publish_mode": "hidden"})
    assert out["publish_mode"] == "hidden"
    # publish_mode 只在列上, 不写进 spec
    assert "publish_mode" not in out["spec"]
    with pytest.raises(EntityEditError):
        apply_tool_patch(make_row(), {"publish_mode": "removed"})


# --- 契约层 -------------------------------------------------------------

def test_rename_syncs_row_and_spec_keeps_position():
    row = make_row(position=7)
    out = apply_tool_patch(row, {"name": "a_one_v2"}, existing_names={"b_two"})
    assert out["name"] == "a_one_v2"
    assert out["spec"]["name"] == "a_one_v2"
    assert out["position"] == 7


def test_rename_rejects_collision_and_bad_identifier():
    with pytest.raises(EntityEditError, match="already exists"):
        apply_tool_patch(make_row(), {"name": "b_two"}, existing_names={"b_two"})
    for bad in ["", "1abc", "a-b", "a.b", "a b"]:
        with pytest.raises(EntityEditError):
            apply_tool_patch(make_row(), {"name": bad}, existing_names=set())


def test_routing_edit_syncs_column_and_spec():
    out = apply_tool_patch(make_row(), {"routing": "shared_quotes"})
    assert out["routing"] == "shared_quotes"
    assert out["spec"]["routing"] == "shared_quotes"


def test_requires_env_set_and_clear():
    out = apply_tool_patch(make_row(), {"requires_env": "ALLOW_WATCHLIST_WRITE"})
    assert out["requires_env"] == "ALLOW_WATCHLIST_WRITE"
    assert out["spec"]["requires_env"] == "ALLOW_WATCHLIST_WRITE"
    cleared = apply_tool_patch(out, {"requires_env": None})
    assert cleared["requires_env"] is None
    assert "requires_env" not in cleared["spec"]
    with pytest.raises(EntityEditError):
        apply_tool_patch(make_row(), {"requires_env": "1BAD_NAME"})


def test_mapping_edit_is_column_only_and_validates_strings():
    out = apply_tool_patch(make_row(), {"mapping": {"beta": "get_b"}})
    assert out["mapping"] == {"beta": "get_b"}
    assert "mapping" not in out["spec"]
    with pytest.raises(EntityEditError):
        apply_tool_patch(make_row(), {"mapping": {"beta": 5}})
    with pytest.raises(EntityEditError):
        apply_tool_patch(make_row(), {"mapping": {"": "get_b"}})


# --- 通用入参守卫 --------------------------------------------------------

def test_unknown_and_non_writable_fields_rejected():
    with pytest.raises(EntityEditError, match="data_source_type"):
        apply_tool_patch(make_row(), {"data_source_type": "online"})
    with pytest.raises(EntityEditError):
        apply_tool_patch(make_row(), {"description": 123})
    with pytest.raises(EntityEditError):
        apply_tool_patch(make_row(), {})
    with pytest.raises(EntityEditError):
        apply_tool_patch(make_row(), "not-a-dict")  # type: ignore[arg-type]


# --- 上游连接块 ----------------------------------------------------------

def test_validate_upstream_config_accepts_placeholder_secrets():
    validate_upstream_config({"base_url": "http://x", "api_key": "${BETA_KEY}"})


@pytest.mark.parametrize(
    "config",
    [
        "nope",
        ["nope"],
        {"enabled": True},
        {"type": "http"},
        {123: "x"},
    ],
)
def test_validate_upstream_config_rejects_bad_shapes(config):
    with pytest.raises(EntityEditError):
        validate_upstream_config(config)
