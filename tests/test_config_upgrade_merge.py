"""字段级三方合并引擎 golden 测试 (P3-5)。

覆盖:
- 经典三方: 出厂流入 / 用户保留 / 双方收敛 / 原子冲突 keep cur / accept_new 改判;
- 实体增删: 出厂新增、未改删除、改过保留 (delete_modify)、cur 独有保留;
- dict 递归按键合并, list 原子化;
- 工具抽取列与 spec 的镜像同步、source_file 跟新模板、origin 重算;
- 顺序: new 优先, cur 独有追加;
- 确定性与输入不被 mutate。
"""
from __future__ import annotations

import copy

from unihive.core.config_upgrade import (
    ACTION_ADDED,
    ACTION_KEPT_USER,
    ACTION_REMOVED,
    ACTION_UNCHANGED,
    ACTION_UPDATED,
    CONFLICT_DELETE_MODIFY,
    CONFLICT_MODIFY,
    CONFLICT_MODIFY_DELETE,
    RESOLUTION_ACCEPT_NEW,
    RESOLUTION_KEEP_CUR,
    merge_bundles,
    section_origins,
)


# --------------------------------------------------------------------- fixtures

def tool(
    name: str = "get_quote",
    *,
    routing: str | None = None,
    description: str = "quote",
    cache_ttl_key: str | None = "realtime_quote",
    requires_env: str | None = None,
    publish_mode: str = "default",
    mapping: dict | None = None,
    spec_extra: dict | None = None,
    source_file: str = "tools_fuyao.yaml",
    origin: str = "builtin",
) -> dict:
    """构造与 NormalizedTemplate.to_json() 同形的工具行。"""
    routing = routing or name
    spec = {
        "name": name,
        "routing": routing,
        "description": description,
        "params": [{"name": "code", "required": True}],
    }
    if cache_ttl_key is not None:
        spec["cache_ttl_key"] = cache_ttl_key
    if requires_env is not None:
        spec["requires_env"] = requires_env
    if spec_extra:
        spec.update(spec_extra)
    row = {
        "name": name,
        "routing": routing,
        "description": description,
        "cache_ttl_key": cache_ttl_key,
        "requires_env": requires_env,
        "publish_mode": publish_mode,
        "mapping": mapping if mapping is not None else {"fuyao_ashare": "query_quote"},
        "spec": spec,
        "source_file": source_file,
        "origin": origin,
    }
    return row


def upstream(
    name: str = "fuyao_ashare",
    *,
    type_: str = "http",
    enabled: bool = True,
    config: dict | None = None,
    origin: str = "builtin",
) -> dict:
    return {
        "name": name,
        "type": type_,
        "enabled": enabled,
        "config": config if config is not None else {"base_url": "http://old", "timeout_seconds": 10},
        "origin": origin,
    }


def bundle(*, tools=None, upstreams=None, sections=None) -> dict:
    return {
        "sections": sections or {},
        "upstreams": upstreams or [],
        "tools": tools or [],
    }


def by_name(changes: list) -> dict:
    return {c.name: c for c in changes}


def find_conflict(conflicts, *, kind: str, name: str, path: str):
    for c in conflicts:
        if c.kind == kind and c.name == name and c.path == path:
            return c
    raise AssertionError(f"conflict not found: {kind}:{name}:{path}")


# --------------------------------------------------------------------- 基础三方

def test_identical_bundles_no_changes():
    t = tool()
    u = upstream()
    sections = {"cache": {"ttl": {"realtime_quote": 5}}}
    b = bundle(tools=[t], upstreams=[u], sections=sections)
    result = merge_bundles(b, copy.deepcopy(b), copy.deepcopy(b))

    assert result.summary() == {
        "added": 0, "removed": 0, "updated": 0, "kept_user": 0, "conflicts": 0
    }
    assert by_name(result.tools)["get_quote"].action == ACTION_UNCHANGED
    assert by_name(result.upstreams)["fuyao_ashare"].action == ACTION_UNCHANGED
    assert by_name(result.sections)["cache"].action == ACTION_UNCHANGED
    # 合并结果与 new 完全一致 (origin 仍 builtin)
    assert result.bundle["tools"][0] == {**t, "origin": "builtin"}
    assert result.bundle["upstreams"][0] == {**u, "origin": "builtin"}
    assert result.bundle["sections"] == sections


def test_factory_description_change_flows_when_user_untouched():
    old = bundle(tools=[tool(description="old desc")])
    cur = bundle(tools=[tool(description="old desc")])
    new = bundle(tools=[tool(description="new desc")])

    result = merge_bundles(old, cur, new)
    merged = result.bundle["tools"][0]

    assert merged["description"] == "new desc"
    assert merged["spec"]["description"] == "new desc"
    assert merged["origin"] == "builtin"
    change = by_name(result.tools)["get_quote"]
    assert change.action == ACTION_UPDATED
    assert "description" in change.fields
    assert not result.conflicts


def test_user_description_edit_survives_unchanged_factory():
    old = bundle(tools=[tool(description="old desc")])
    cur = bundle(tools=[tool(description="my desc", origin="user")])
    new = bundle(tools=[tool(description="old desc")])

    result = merge_bundles(old, cur, new)
    merged = result.bundle["tools"][0]

    assert merged["description"] == "my desc"
    assert merged["spec"]["description"] == "my desc"
    assert merged["origin"] == "user"
    assert by_name(result.tools)["get_quote"].action == ACTION_UNCHANGED
    assert not result.conflicts


def test_both_changed_differently_conflicts_keep_cur():
    old = bundle(tools=[tool(description="old")])
    cur = bundle(tools=[tool(description="mine", origin="user")])
    new = bundle(tools=[tool(description="factory")])

    result = merge_bundles(old, cur, new)
    merged = result.bundle["tools"][0]

    assert merged["description"] == "mine"
    conflict = find_conflict(result.conflicts, kind="tool", name="get_quote", path="description")
    assert conflict.conflict == CONFLICT_MODIFY
    assert conflict.resolution == RESOLUTION_KEEP_CUR
    assert conflict.old == "old"
    assert conflict.cur == "mine"
    assert conflict.new == "factory"
    assert conflict.key == "tool:get_quote:description"
    change = by_name(result.tools)["get_quote"]
    assert change.action == ACTION_UPDATED
    assert "description" in change.conflicts
    assert merged["origin"] == "user"


def test_both_changed_to_same_value_converges_no_conflict():
    old = bundle(tools=[tool(description="old")])
    cur = bundle(tools=[tool(description="same")])
    new = bundle(tools=[tool(description="same")])

    result = merge_bundles(old, cur, new)
    assert not result.conflicts
    assert result.bundle["tools"][0]["description"] == "same"
    assert result.bundle["tools"][0]["origin"] == "builtin"


def test_conflict_accept_new_resolution_takes_factory():
    old = bundle(tools=[tool(description="old")])
    cur = bundle(tools=[tool(description="mine")])
    new = bundle(tools=[tool(description="factory")])

    result = merge_bundles(
        old, cur, new, resolutions={"tool:get_quote:description": RESOLUTION_ACCEPT_NEW}
    )
    merged = result.bundle["tools"][0]
    assert merged["description"] == "factory"
    assert merged["spec"]["description"] == "factory"
    conflict = find_conflict(result.conflicts, kind="tool", name="get_quote", path="description")
    assert conflict.resolution == RESOLUTION_ACCEPT_NEW
    # 接受出厂后实体重新与出厂一致 → origin 归 builtin
    assert merged["origin"] == "builtin"


# --------------------------------------------------------------------- 实体增删

def test_factory_added_tool_and_upstream_and_section():
    old = bundle()
    cur = bundle()
    new = bundle(
        tools=[tool("new_tool")],
        upstreams=[upstream("new_up")],
        sections={"fuyao_cache": {"tools": {}}},
    )

    result = merge_bundles(old, cur, new)

    assert result.added == 3
    assert by_name(result.tools)["new_tool"].action == ACTION_ADDED
    assert by_name(result.upstreams)["new_up"].action == ACTION_ADDED
    assert by_name(result.sections)["fuyao_cache"].action == ACTION_ADDED
    assert result.bundle["tools"][0]["origin"] == "builtin"
    assert result.bundle["upstreams"][0]["origin"] == "builtin"


def test_factory_removes_untouched_tool():
    old = bundle(tools=[tool("doomed")])
    cur = bundle(tools=[tool("doomed")])
    new = bundle()

    result = merge_bundles(old, cur, new)

    assert result.removed == 1
    assert by_name(result.tools)["doomed"].action == ACTION_REMOVED
    assert result.bundle["tools"] == []


def test_factory_removes_user_modified_tool_keeps_with_conflict():
    old = bundle(tools=[tool("doomed", description="old")])
    cur = bundle(tools=[tool("doomed", description="mine", origin="user")])
    new = bundle()

    result = merge_bundles(old, cur, new)

    assert result.kept_user == 1
    change = by_name(result.tools)["doomed"]
    assert change.action == ACTION_KEPT_USER
    assert result.bundle["tools"][0]["name"] == "doomed"
    assert result.bundle["tools"][0]["description"] == "mine"
    conflict = find_conflict(result.conflicts, kind="tool", name="doomed", path="")
    assert conflict.conflict == CONFLICT_DELETE_MODIFY
    assert conflict.resolution == RESOLUTION_KEEP_CUR
    # 原始对象保留 MISSING 哨兵, 序列化 (to_json) 时才渲染为 null
    assert conflict.to_json()["new"] is None


def test_factory_removes_user_modified_tool_accept_new_deletes():
    old = bundle(tools=[tool("doomed", description="old")])
    cur = bundle(tools=[tool("doomed", description="mine", origin="user")])
    new = bundle()

    result = merge_bundles(
        old, cur, new, resolutions={"tool:doomed:": RESOLUTION_ACCEPT_NEW}
    )
    assert result.bundle["tools"] == []
    assert by_name(result.tools)["doomed"].action == ACTION_REMOVED
    conflict = find_conflict(result.conflicts, kind="tool", name="doomed", path="")
    assert conflict.resolution == RESOLUTION_ACCEPT_NEW


def test_cur_only_user_tool_is_never_touched():
    old = bundle()
    cur = bundle(tools=[tool("p4_imported", origin="user", source_file="user")])
    new = bundle()

    result = merge_bundles(old, cur, new)

    assert result.kept_user == 1
    merged = result.bundle["tools"][0]
    assert merged["name"] == "p4_imported"
    assert merged["origin"] == "user"
    assert merged["source_file"] == "user"
    assert not result.conflicts


def test_modify_delete_conflict_when_cur_missing_but_factory_changed():
    # 防御场景: cur 没有该实体 (用户侧删除), 但出厂在新版本改了它。
    old = bundle(upstreams=[upstream("ghost", config={"base_url": "http://old"})])
    cur = bundle()
    new = bundle(upstreams=[upstream("ghost", config={"base_url": "http://new"})])

    result = merge_bundles(old, cur, new)

    # 默认维持删除
    assert result.bundle["upstreams"] == []
    conflict = find_conflict(result.conflicts, kind="upstream", name="ghost", path="")
    assert conflict.conflict == CONFLICT_MODIFY_DELETE
    # accept_new 恢复出厂值
    restored = merge_bundles(
        old, cur, new, resolutions={"upstream:ghost:": RESOLUTION_ACCEPT_NEW}
    )
    assert restored.bundle["upstreams"][0]["config"]["base_url"] == "http://new"


# --------------------------------------------------------------------- dict 递归

def test_upstream_config_key_level_merge():
    old = bundle(upstreams=[upstream(config={"base_url": "http://a", "timeout_seconds": 10})])
    cur = bundle(upstreams=[upstream(config={"base_url": "http://mine", "timeout_seconds": 10})])
    new = bundle(upstreams=[upstream(config={"base_url": "http://a", "timeout_seconds": 30, "retries": 3})])

    result = merge_bundles(old, cur, new)
    cfg = result.bundle["upstreams"][0]["config"]

    assert cfg == {"base_url": "http://mine", "timeout_seconds": 30, "retries": 3}
    change = by_name(result.upstreams)["fuyao_ashare"]
    assert "config.timeout_seconds" in change.fields
    assert "config.retries" in change.fields
    assert "config.base_url" not in change.fields
    assert result.bundle["upstreams"][0]["origin"] == "user"
    assert not result.conflicts


def test_factory_adds_strategy_instances_on_existing_tdx_quant():
    """出厂新增 strategy_instances: old/cur 都没有该键时必须流入, 否则库不升级永远 N=1。"""
    old = bundle(upstreams=[upstream(name="tdx_quant", type_="tdx_quant", config={"tdx_root": "D:/tdx"})])
    cur = bundle(upstreams=[upstream(name="tdx_quant", type_="tdx_quant", config={"tdx_root": "D:/tdx"})])
    new = bundle(upstreams=[upstream(
        name="tdx_quant",
        type_="tdx_quant",
        config={"tdx_root": "D:/tdx", "strategy_instances": 2},
    )])
    result = merge_bundles(old, cur, new)
    cfg = result.bundle["upstreams"][0]["config"]
    assert cfg["strategy_instances"] == 2
    assert cfg["tdx_root"] == "D:/tdx"
    change = by_name(result.upstreams)["tdx_quant"]
    assert "config.strategy_instances" in change.fields


def test_upstream_config_leaf_conflict_keeps_cur():
    old = bundle(upstreams=[upstream(config={"base_url": "http://old"})])
    cur = bundle(upstreams=[upstream(config={"base_url": "http://mine"})])
    new = bundle(upstreams=[upstream(config={"base_url": "http://factory"})])

    result = merge_bundles(old, cur, new)
    assert result.bundle["upstreams"][0]["config"]["base_url"] == "http://mine"
    find_conflict(result.conflicts, kind="upstream", name="fuyao_ashare", path="config.base_url")


def test_mapping_dict_merges_at_upstream_key_granularity():
    old = bundle(tools=[tool(mapping={"alpha": "m_old"})])
    cur = bundle(tools=[tool(mapping={"alpha": "m_mine"})])
    new = bundle(tools=[tool(mapping={"alpha": "m_old", "beta": "m_new"})])

    result = merge_bundles(old, cur, new)
    mapping = result.bundle["tools"][0]["mapping"]
    assert mapping == {"alpha": "m_mine", "beta": "m_new"}
    assert not result.conflicts


def test_params_list_atomic_factory_flows_when_untouched():
    old = bundle(tools=[tool()])
    cur = bundle(tools=[tool()])
    new_tool = tool()
    new_tool["spec"]["params"] = [{"name": "code", "required": True}, {"name": "limit"}]
    new = bundle(tools=[new_tool])

    result = merge_bundles(old, cur, new)
    params = result.bundle["tools"][0]["spec"]["params"]
    assert [p["name"] for p in params] == ["code", "limit"]
    assert "spec.params" in by_name(result.tools)["get_quote"].fields


def test_params_list_atomic_conflict_when_both_changed():
    old_tool = tool()
    cur_tool = tool()
    cur_tool["spec"]["params"] = [{"name": "code", "description": "mine"}]
    new_tool = tool()
    new_tool["spec"]["params"] = [{"name": "code", "required": True}, {"name": "limit"}]

    result = merge_bundles(bundle(tools=[old_tool]), bundle(tools=[cur_tool]), bundle(tools=[new_tool]))
    params = result.bundle["tools"][0]["spec"]["params"]
    assert params == [{"name": "code", "description": "mine"}]
    find_conflict(result.conflicts, kind="tool", name="get_quote", path="spec.params")


def test_nested_dict_conflict_does_not_block_sibling_keys():
    old = bundle(sections={"cache": {"ttl": {"a": 1, "b": 1}}})
    cur = bundle(sections={"cache": {"ttl": {"a": 9, "b": 1}}})
    new = bundle(sections={"cache": {"ttl": {"a": 5, "b": 2, "c": 3}}})

    result = merge_bundles(old, cur, new)
    ttl = result.bundle["sections"]["cache"]["ttl"]
    assert ttl == {"a": 9, "b": 2, "c": 3}
    find_conflict(result.conflicts, kind="section", name="cache", path="ttl.a")
    change = by_name(result.sections)["cache"]
    assert "ttl.b" in change.fields
    assert "ttl.c" in change.fields


# --------------------------------------------------------------------- 列/spec 镜像

def test_column_and_spec_stay_consistent_under_partial_edits():
    # 用户改了 requires_env; 出厂改了 description。两者应各得其所, 列与 spec 一致。
    old = bundle(tools=[tool(description="old", requires_env=None)])
    cur_tool = tool(description="old", requires_env="ALLOW_X", origin="user")
    cur = bundle(tools=[cur_tool])
    new = bundle(tools=[tool(description="fresh", requires_env=None)])

    result = merge_bundles(old, cur, new)
    merged = result.bundle["tools"][0]

    assert merged["description"] == "fresh"
    assert merged["spec"]["description"] == "fresh"
    assert merged["requires_env"] == "ALLOW_X"
    assert merged["spec"]["requires_env"] == "ALLOW_X"


def test_cache_ttl_cleared_to_null_pops_spec_key():
    old = bundle(tools=[tool(cache_ttl_key="realtime_quote")])
    cur = bundle(tools=[tool(cache_ttl_key="realtime_quote")])
    new = bundle(tools=[tool(cache_ttl_key=None)])
    new_tool = new["tools"][0]
    assert "cache_ttl_key" not in new_tool["spec"]

    result = merge_bundles(old, cur, new)
    merged = result.bundle["tools"][0]
    assert merged["cache_ttl_key"] is None
    assert "cache_ttl_key" not in merged["spec"]


def test_explicit_null_spec_key_is_preserved_round_trip():
    # 部分出厂 spec 显式带 cache_ttl_key: null (YAML 空值); 合并不得摘掉该键,
    # 否则零损耗往返被破坏, 还会误判 origin=user。
    t = tool(cache_ttl_key=None)
    t["spec"]["cache_ttl_key"] = None
    b = bundle(tools=[t])
    result = merge_bundles(copy.deepcopy(b), copy.deepcopy(b), copy.deepcopy(b))
    merged = result.bundle["tools"][0]
    assert "cache_ttl_key" in merged["spec"]
    assert merged["spec"]["cache_ttl_key"] is None
    assert merged["origin"] == "builtin"


def test_source_file_always_follows_new_factory():
    old = bundle(tools=[tool(source_file="old_file.yaml")])
    cur_tool = tool(source_file="old_file.yaml", description="mine", origin="user")
    cur = bundle(tools=[cur_tool])
    new = bundle(tools=[tool(source_file="new_file.yaml")])

    result = merge_bundles(old, cur, new)
    assert result.bundle["tools"][0]["source_file"] == "new_file.yaml"


def test_non_editable_spec_key_flows_from_factory():
    old = bundle(tools=[tool(spec_extra={"dangerous": False})])
    cur = bundle(tools=[tool(spec_extra={"dangerous": False})])
    new = bundle(tools=[tool(spec_extra={"dangerous": True, "data_source_type": "http"})])

    result = merge_bundles(old, cur, new)
    spec = result.bundle["tools"][0]["spec"]
    assert spec["dangerous"] is True
    assert spec["data_source_type"] == "http"


# --------------------------------------------------------------------- 标量/顺序/元数据

def test_user_enabled_toggle_survives_factory_config_change():
    old = bundle(upstreams=[upstream(enabled=True, config={"timeout_seconds": 10})])
    cur = bundle(upstreams=[upstream(enabled=False, config={"timeout_seconds": 10})])
    new = bundle(upstreams=[upstream(enabled=True, config={"timeout_seconds": 30})])

    result = merge_bundles(old, cur, new)
    up = result.bundle["upstreams"][0]
    assert up["enabled"] is False
    assert up["config"]["timeout_seconds"] == 30
    assert up["origin"] == "user"


def test_publish_mode_conflict_is_atomic():
    old = bundle(tools=[tool(publish_mode="default")])
    cur = bundle(tools=[tool(publish_mode="hidden", origin="user")])
    new = bundle(tools=[tool(publish_mode="default")])
    # 出厂没动 publish_mode, 用户值直接保留, 无冲突
    result = merge_bundles(old, cur, new)
    assert result.bundle["tools"][0]["publish_mode"] == "hidden"
    assert not result.conflicts


def test_ordering_follows_new_with_cur_only_appended():
    old = bundle(tools=[tool("a"), tool("b"), tool("c")])
    cur = bundle(tools=[tool("a"), tool("b"), tool("c"), tool("user_only", origin="user")])
    new = bundle(tools=[tool("c"), tool("a"), tool("factory_new")])

    result = merge_bundles(old, cur, new)
    names = [t["name"] for t in result.bundle["tools"]]
    # b 被出厂删除且未改 → 移除; c/a 按 new 顺序; factory_new 新增; user_only 追加
    assert names == ["c", "a", "factory_new", "user_only"]


def test_scalar_section_root_change_is_updated():
    old = bundle(sections={"disable_dangerous": False})
    cur = bundle(sections={"disable_dangerous": False})
    new = bundle(sections={"disable_dangerous": True})

    result = merge_bundles(old, cur, new)
    assert result.bundle["sections"]["disable_dangerous"] is True
    assert by_name(result.sections)["disable_dangerous"].action == ACTION_UPDATED


def test_section_origins_helper():
    old = bundle(sections={"cache": {"x": 1}, "logging": {"x": 1}})
    cur = bundle(sections={"cache": {"x": 1}, "logging": {"x": 2}})
    new = bundle(sections={"cache": {"x": 9}, "logging": {"x": 1}})

    result = merge_bundles(old, cur, new)
    origins = section_origins(new, result.bundle)
    # cache 出厂流入 → builtin; logging 用户保留 → user
    assert origins == {"cache": "builtin", "logging": "user"}


def test_factory_section_removal_untouched_vs_modified():
    old = bundle(sections={"drop_me": {"x": 1}, "keep_mine": {"x": 1}})
    cur = bundle(sections={"drop_me": {"x": 1}, "keep_mine": {"x": 2}})
    # 新出厂模板把两个段都删了: 没改过的接受删除, 改过的保留 + delete_modify
    new = bundle(sections={})

    result = merge_bundles(old, cur, new)
    assert "drop_me" not in result.bundle["sections"]
    assert result.bundle["sections"]["keep_mine"] == {"x": 2}
    assert by_name(result.sections)["drop_me"].action == ACTION_REMOVED
    assert by_name(result.sections)["keep_mine"].action == ACTION_KEPT_USER
    find_conflict(result.conflicts, kind="section", name="keep_mine", path="")


# --------------------------------------------------------------------- 纯函数保证

def test_inputs_are_not_mutated():
    old = bundle(tools=[tool(description="old")], sections={"s": {"a": 1}})
    cur = bundle(tools=[tool(description="mine")], sections={"s": {"a": 2}})
    new = bundle(tools=[tool(description="factory")], sections={"s": {"a": 3}})
    old_snap, cur_snap, new_snap = copy.deepcopy(old), copy.deepcopy(cur), copy.deepcopy(new)

    merge_bundles(old, cur, new)
    assert old == old_snap
    assert cur == cur_snap
    assert new == new_snap


def test_merge_is_deterministic():
    old = bundle(
        tools=[tool("a", description="old"), tool("b")],
        upstreams=[upstream("u", config={"base_url": "http://old", "timeout_seconds": 10})],
        sections={"cache": {"ttl": {"a": 1}}},
    )
    cur = bundle(
        tools=[tool("a", description="mine", origin="user"), tool("b")],
        upstreams=[upstream("u", config={"base_url": "http://mine", "timeout_seconds": 10})],
        sections={"cache": {"ttl": {"a": 2}}},
    )
    new = bundle(
        tools=[tool("a", description="factory"), tool("b", cache_ttl_key="hist")],
        upstreams=[upstream("u", config={"base_url": "http://factory", "timeout_seconds": 30})],
        sections={"cache": {"ttl": {"a": 1, "b": 9}}},
    )

    r1 = merge_bundles(old, cur, new)
    r2 = merge_bundles(copy.deepcopy(old), copy.deepcopy(cur), copy.deepcopy(new))
    assert r1.to_json() == r2.to_json()


def test_missing_bundle_keys_tolerated():
    # old=None (无快照防御) + 形态不完整的 bundle 不应崩;
    # 缺 base 时 cur/new 同名字段不同按 both-added 冲突处理, 默认保 cur。
    result = merge_bundles(None, {"tools": [tool()]}, {"tools": [tool(description="new")]})
    assert result.bundle["tools"][0]["description"] == "quote"
    assert result.has_conflicts


def test_conflict_json_shape_uses_none_for_missing_sides():
    import json

    old = bundle(tools=[tool(description="old")])
    cur = bundle(tools=[tool(description="mine")])
    new = bundle()
    # 整工具删除冲突: new 侧缺失, 序列化应为 None 而非哨兵对象, 且整表 JSON 可序列化
    result = merge_bundles(old, cur, new)
    payload = result.to_json()
    conflict = next(c for c in payload["conflicts"] if c["name"] == "get_quote")
    assert conflict["new"] is None
    assert conflict["cur"] == cur["tools"][0]  # cur 行完整回带
    json.dumps(payload)  # 不抛 TypeError = 无 MISSING 哨兵泄漏


def test_conflict_json_serializable_when_old_side_missing_entirely():
    """回归 (2026-09-29): 快照里没有、cur/new 都有的字段走 modify 冲突时,
    旧实现 ``copy.deepcopy(MISSING)`` 生成新 object() 实例, 逃过 to_json 的
    is MISSING 判定 → dry-run/apply HTTP 500。断言 JSON 可序列化且 old=None。
    """
    import json

    old = bundle()  # 快照没有该工具 → 字段级 old 侧为 MISSING
    cur = bundle(tools=[tool(description="cur 版")])
    new = bundle(tools=[tool(description="factory 新版")])
    result = merge_bundles(old, cur, new)
    conflicts = [c for c in result.conflicts if c.name == "get_quote"]
    assert conflicts, "cur/new 不一致应产生 modify 冲突"
    payload = result.to_json()
    for c in payload["conflicts"]:
        if c["name"] == "get_quote":
            assert c["old"] is None
    json.dumps(payload)  # 不抛 TypeError = 无 deepcopy 出的哨兵泄漏
