"""FQGate OpenAPI → origin='user' 工具实体导入的纯函数测试 (P4)。

覆盖:
- schema 解析: $ref / nullable oneOf / 数组元素形状;
- 转换: REST query/body 参数权威、MCP 层独有参数末尾追加、MANUAL_MAP、
  未映射工具隔离、实体行形态 (origin/source_file/mapping);
- 差异: added / changed / unchanged / removed (仅同源历史导入行);
- 合并: 追加 position、保留 cache_ttl_key/requires_env/publish_mode、不 mutate;
- YAML 片段: 与 tools_ths_local.yaml 同形 (yaml.safe_load 可解析、flow 行)。
- 真实快照 parity: logs/_openapi.json + _tools_list.sse 存在时,
  服务端转换必须与出厂 config/tools_ths_local.yaml 87 行逐字段一致。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from unihive.core.openapi_import import (
    FQGATE_UPSTREAM_NAME,
    IMPORT_SOURCE_FILE,
    convert_openapi_tools,
    merge_imported_tools,
    plan_openapi_import,
    render_yaml_fragment,
    resolve_schema,
)

ROOT = Path(__file__).resolve().parent.parent


def _mcp_tool(name: str, description: str = "", props: dict | None = None,
             required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": props or {},
            **({"required": required} if required else {}),
        },
    }


def _minimal_openapi() -> dict:
    return {
        "openapi": "3.1.0",
        "paths": {
            "/v1/market/health": {
                "get": {"summary": "health", "responses": {}},
            },
            "/v1/market/realtime/quote": {
                "post": {
                    "parameters": [
                        {
                            "name": "detail",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "boolean"},
                            "description": "是否返回明细",
                        },
                    ],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/QuoteReq"},
                                "example": {"code": "600519", "market": "USHA"},
                            }
                        }
                    },
                    "responses": {},
                },
            },
        },
        "components": {
            "schemas": {
                "QuoteReq": {
                    "type": "object",
                    "required": ["code", "market"],
                    "properties": {
                        "code": {"type": "string", "description": "证券代码。"},
                        "market": {
                            "oneOf": [
                                {"type": "string", "enum": ["USHA", "USZA"],
                                 "description": "市场代码。"},
                                {"type": "null"},
                            ],
                            "description": "同花顺市场代码。",
                        },
                        "limit": {"type": "integer", "description": "数量。"},
                    },
                },
            }
        },
    }


# ---------------------------------------------------------------- schema

def test_resolve_ref_and_nullable_oneof():
    openapi = _minimal_openapi()
    schemas = openapi["components"]["schemas"]
    resolved = resolve_schema(schemas, {"$ref": "#/components/schemas/QuoteReq"})
    assert "code" in resolved["properties"]

    nullable = resolve_schema(
        schemas, schemas["QuoteReq"]["properties"]["market"]
    )
    assert nullable["type"] == "string"
    assert nullable["enum"] == ["USHA", "USZA"]


def test_resolve_array_items_ref():
    schemas = {
        "Item": {"type": "object", "properties": {"code": {"type": "string"}}},
    }
    resolved = resolve_schema(
        schemas,
        {"type": "array", "items": {"$ref": "#/components/schemas/Item"}},
    )
    assert resolved["type"] == "array"
    assert resolved["items"]["properties"]["code"]["type"] == "string"


def test_resolve_depth_guard_returns_empty():
    assert resolve_schema({}, {"$ref": "#/components/schemas/Missing"}, depth=9) == {}


# -------------------------------------------------------------- convert

def test_convert_basic_entity_shape_and_param_order():
    openapi = _minimal_openapi()
    mcp_tools = [
        _mcp_tool("fqgate_market_health", "健康检查"),
        _mcp_tool(
            "fqgate_quote",
            "实时报价。POST /v1/market/realtime/quote。",
            props={
                # REST 已有的 code 必须被 REST 权威版覆盖 (不重复)
                "code": {"type": "string", "description": "MCP 层误标"},
                "requestTimeoutMs": {
                    "type": "integer",
                    "description": "请求超时时间(毫秒)",
                },
            },
            required=["requestTimeoutMs"],
        ),
        _mcp_tool("fqgate_no_hint", "完全没有 REST 提示"),
        _mcp_tool("other_tool", "非 fqgate 前缀, 忽略"),
    ]
    manual_map = {"fqgate_market_health": ("GET", "/v1/market/health")}

    entities, unmapped = convert_openapi_tools(
        openapi, mcp_tools, manual_map=manual_map
    )

    by_name = {e["name"]: e for e in entities}
    assert set(by_name) == {"fqgate_market_health", "fqgate_quote"}
    assert {u.name for u in unmapped} == {"fqgate_no_hint"}

    health = by_name["fqgate_market_health"]
    assert health["origin"] == "user"
    assert health["source_file"] == IMPORT_SOURCE_FILE
    assert health["routing"] == "fqgate_market_health"
    assert health["mapping"] == {FQGATE_UPSTREAM_NAME: "fqgate_market_health"}
    assert health["cache_ttl_key"] is None
    assert health["requires_env"] is None
    assert health["publish_mode"] == "default"
    assert health["spec"] == {
        "name": "fqgate_market_health",
        "description": "健康检查",
        "routing": "fqgate_market_health",
        "params": [],
        "upstream_tool_mapping": {FQGATE_UPSTREAM_NAME: "fqgate_market_health"},
        "data_source_type": "online",
    }

    quote = by_name["fqgate_quote"]
    param_names = [p["name"] for p in quote["spec"]["params"]]
    # query / body 在前 (按 OpenAPI 出现序), MCP 独有的 requestTimeoutMs 末尾
    assert param_names == ["detail", "code", "market", "limit", "requestTimeoutMs"]
    by_param = {p["name"]: p for p in quote["spec"]["params"]}
    assert by_param["code"]["required"] is True
    assert by_param["code"]["description"] == "证券代码。"
    assert by_param["market"]["required"] is True
    assert by_param["market"]["enum"] == ["USHA", "USZA"]
    assert by_param["limit"]["required"] is False
    assert by_param["limit"]["type"] == "int"
    assert by_param["detail"]["type"] == "bool"
    assert by_param["requestTimeoutMs"]["required"] is True
    assert "取值 ≥1" in by_param["requestTimeoutMs"]["description"]
    # 顶层描述用 MCP 工具描述原文
    assert quote["spec"]["description"] == "实时报价。POST /v1/market/realtime/quote。"
    # 实体按名排序, 输出稳定
    assert [e["name"] for e in entities] == sorted(by_name)


def test_convert_manual_map_path_missing_is_unmapped():
    openapi = _minimal_openapi()
    mcp_tools = [_mcp_tool("fqgate_x", "无提示")]
    manual_map = {"fqgate_x": ("POST", "/v1/market/no-such-path")}

    entities, unmapped = convert_openapi_tools(
        openapi, mcp_tools, manual_map=manual_map
    )
    assert entities == []
    assert len(unmapped) == 1
    assert unmapped[0].name == "fqgate_x"
    assert "path" in unmapped[0].reason


def test_convert_rejects_malformed_openapi():
    mcp_tools = [_mcp_tool("fqgate_market_health")]
    with pytest.raises(ValueError):
        convert_openapi_tools({"paths": {}}, mcp_tools)  # 缺 components/schemas
    with pytest.raises(ValueError):
        convert_openapi_tools("not-a-dict", mcp_tools)  # type: ignore[arg-type]


def test_convert_does_not_mutate_inputs():
    openapi = _minimal_openapi()
    mcp_tools = [_mcp_tool("fqgate_market_health", "健康检查")]
    manual_map = {"fqgate_market_health": ("GET", "/v1/market/health")}
    import copy

    openapi_snap, tools_snap = copy.deepcopy(openapi), copy.deepcopy(mcp_tools)
    convert_openapi_tools(openapi, mcp_tools, manual_map=manual_map)
    assert openapi == openapi_snap
    assert mcp_tools == tools_snap


# ---------------------------------------------------------------- plan

def _entity(name: str, *, spec_params: list[dict] | None = None,
            description: str = "", mapping: dict | None = None) -> dict:
    spec = {
        "name": name,
        "description": description,
        "routing": name,
        "params": spec_params if spec_params is not None else [],
        "upstream_tool_mapping": mapping or {FQGATE_UPSTREAM_NAME: name},
        "data_source_type": "online",
    }
    return {
        "name": name,
        "routing": name,
        "description": description,
        "cache_ttl_key": None,
        "requires_env": None,
        "publish_mode": "default",
        "mapping": mapping or {FQGATE_UPSTREAM_NAME: name},
        "spec": spec,
        "source_file": IMPORT_SOURCE_FILE,
        "origin": "user",
    }


def test_plan_classifies_added_changed_unchanged_and_removed():
    old = _entity("fqgate_same", description="相同")
    changed_old = _entity("fqgate_changed", description="旧描述")
    stale_import = _entity("fqgate_gone", description="历史导入, 上游已删")
    builtin_other = {
        **_entity("fuyao_x"),
        "origin": "builtin",
        "source_file": "upstreams.yaml",
    }
    cur_bundle = {
        "sections": {},
        "upstreams": [],
        "tools": [old, changed_old, stale_import, builtin_other],
    }

    same_new = _entity("fqgate_same", description="相同")
    changed_new = _entity("fqgate_changed", description="新描述")
    added_new = _entity("fqgate_new", description="新工具")

    plan = plan_openapi_import(cur_bundle, [same_new, changed_new, added_new])

    assert [c.name for c in plan.added] == ["fqgate_new"]
    assert [c.name for c in plan.changed] == ["fqgate_changed"]
    assert plan.changed[0].changed_fields == ("description", "spec")
    assert [c.name for c in plan.unchanged] == ["fqgate_same"]
    # 只有 openapi-import 同源历史行且映射仅指向本上游时才提示 removed
    assert plan.removed == ("fqgate_gone",)
    assert {u for u in plan.removed} <= {"fqgate_gone"}

    selected = plan.change_entities()
    assert {e["name"] for e in selected} == {"fqgate_changed", "fqgate_new"}
    only_new = plan.change_entities(selected_names={"fqgate_new"})
    assert [e["name"] for e in only_new] == ["fqgate_new"]
    with pytest.raises(ValueError):
        plan.change_entities(selected_names={"fqgate_not_in_changes"})


def test_plan_removed_ignores_builtin_and_other_upstream_rows():
    builtin = {**_entity("fqgate_promoted"), "origin": "builtin",
               "source_file": "tools_ths_local.yaml"}
    other_mapping = _entity("fqgate_other_backer",
                            mapping={"ELSE": "fqgate_other_backer"})
    cur_bundle = {"sections": {}, "upstreams": [], "tools": [builtin, other_mapping]}
    plan = plan_openapi_import(cur_bundle, [])
    assert plan.removed == ()


# --------------------------------------------------------------- merge

def test_merge_appends_new_and_preserves_local_columns():
    kept = {**_entity("fqgate_kept"), "origin": "builtin",
            "source_file": "tools_ths_local.yaml", "position": 0}
    hidden_old = _entity("fqgate_hidden")
    hidden_old = {
        **hidden_old,
        "position": 1,
        "cache_ttl_key": "realtime_quote",
        "publish_mode": "hidden",
    }
    cur_bundle = {
        "sections": {"cache": {"ttl": {}}},
        "upstreams": [{"name": "THS-LOCAL-GATEWAY", "type": "ths_local_gateway"}],
        "tools": [kept, hidden_old],
    }

    hidden_new = _entity("fqgate_hidden", description="上游改了描述")
    added = _entity("fqgate_added")
    merged = merge_imported_tools(cur_bundle, [hidden_new, added])

    tools = {t["name"]: t for t in merged["tools"]}
    assert merged["sections"] == cur_bundle["sections"]
    assert merged["upstreams"] == cur_bundle["upstreams"]

    # 已存在: 行被 origin='user' 替换, 但 position / 本地门控列保留
    row = tools["fqgate_hidden"]
    assert row["origin"] == "user"
    assert row["position"] == 1
    assert row["publish_mode"] == "hidden"
    assert row["cache_ttl_key"] == "realtime_quote"
    assert row["description"] == "上游改了描述"
    # 保留的抽取列同步回 spec
    assert row["spec"]["cache_ttl_key"] == "realtime_quote"

    # 新增: 追加 position, 默认门控列
    assert tools["fqgate_added"]["position"] == 2
    assert tools["fqgate_added"]["origin"] == "user"
    # 未涉及行原样
    assert tools["fqgate_kept"] == kept


def test_merge_does_not_mutate_cur_bundle():
    import copy

    cur_bundle = {"sections": {}, "upstreams": [], "tools": [_entity("fqgate_a")]}
    snapshot = copy.deepcopy(cur_bundle)
    merge_imported_tools(cur_bundle, [_entity("fqgate_a", description="x"),
                                      _entity("fqgate_b")])
    assert cur_bundle == snapshot


# --------------------------------------------------------- yaml fragment

def test_render_yaml_fragment_matches_factory_line_shape():
    entities = [
        _entity("fqgate_quote", description="报价。", spec_params=[
            {"name": "code", "type": "str", "required": True,
             "description": "证券代码。"},
            {"name": "direction", "type": "str", "required": False,
             "enum": ["asc", "desc"], "description": "方向。"},
        ]),
    ]
    fragment = render_yaml_fragment(entities)

    assert fragment.startswith("#")
    assert "tools:" in fragment
    parsed = yaml.safe_load(fragment)
    spec = parsed["tools"][0]
    assert spec == entities[0]["spec"]

    line = next(line for line in fragment.splitlines() if line.startswith("  - {name:"))
    assert "fqgate_quote" in line
    assert "enum: [asc, desc]" in line
    assert "upstream_tool_mapping: {THS-LOCAL-GATEWAY: fqgate_quote}" in line


def test_render_yaml_fragment_empty():
    assert render_yaml_fragment([]) == ""


# ----------------------------------------------------- real-snapshot parity

SSE_PATH = ROOT / "logs" / "_tools_list.sse"
OPENAPI_PATH = ROOT / "logs" / "_openapi.json"
FACTORY_YAML = ROOT / "config" / "tools_ths_local.yaml"


@pytest.mark.skipif(
    not (SSE_PATH.exists() and OPENAPI_PATH.exists() and FACTORY_YAML.exists()),
    reason="FQGate live snapshots not available in this checkout",
)
def test_server_converter_matches_factory_yaml_parity():
    """服务端转换与离线 generator + 出厂 YAML 逐字段一致 (87 工具, 0 未映射)。"""
    openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))

    mcp_tools: list[dict] = []
    for line in SSE_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("data: "):
            body = json.loads(line[6:])
            mcp_tools.extend((body.get("result") or {}).get("tools") or [])

    entities, unmapped = convert_openapi_tools(openapi, mcp_tools)
    assert not unmapped, [(u.name, u.reason) for u in unmapped]
    assert len(entities) == 87

    factory = yaml.safe_load(FACTORY_YAML.read_text(encoding="utf-8"))
    factory_specs = {t["name"]: t for t in factory["tools"]}
    assert set(factory_specs) == {e["name"] for e in entities}
    for entity in entities:
        assert entity["spec"] == factory_specs[entity["name"]], entity["name"]
        assert entity["mapping"] == {FQGATE_UPSTREAM_NAME: entity["name"]}
