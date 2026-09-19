"""FQGate OpenAPI → 工具实体行的实时导入 (P4, 纯函数, 不做 IO)。

场景: THS-LOCAL-GATEWAY 升级后新增/改了 fqgate_* 工具, 在控制台直接从上游
活着的 ``/openapi.json`` + ``tools/list`` 重新生成工具契约, 以 ``origin='user'``
实体行写进 config.db 并热注册到对外 /mcp; 同时导出与
``config/tools_ths_local.yaml`` 同形的 YAML 片段, 评审并入出厂模板后, 下一次
出厂更新三方合并会把这些行自动转为 builtin (cur==new 收敛)。

本模块是 ``scripts/gen_ths_local_tools.py`` 的服务端移植, 转换语义必须与离线
generator 逐字段一致 (logs/ 快照存在时由 parity 测试锁死):

- REST OpenAPI 的 query/body 参数是**权威契约**; 上游 MCP 层 inputSchema
  不可信 (数组误标 string、required/默认值不符), 只用它补 MCP 独有的
  ``requestTimeoutMs``;
- ``$ref`` / nullable ``oneOf`` 收敛、数组元素形状提示、枚举透传;
- 34 个老工具的 description 没有 REST 提示, 走 :data:`MANUAL_MAP`。
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

#: 导入目标上游的注册名 (spec 内 upstream_tool_mapping 的键)。
FQGATE_UPSTREAM_NAME = "THS-LOCAL-GATEWAY"
#: 导入行的来源记账 (与出厂 tools_*.yaml 区分; removed 提示只认同源行)。
IMPORT_SOURCE_FILE = "openapi-import"
#: spec.data_source_type 固定值 (fqgate 工具全部在线)。
ONLINE_DATA_SOURCE = "online"

ACTION_ADDED = "added"
ACTION_CHANGED = "changed"
ACTION_UNCHANGED = "unchanged"

#: 差异判定的契约字段 (publish_mode / cache_ttl_key / requires_env 是本地
#: 门控列, 导入时保留 cur, 不参与"上游是否改了契约"的判定)。
_SIGNATURE_FIELDS = ("routing", "description", "mapping", "spec")

#: OpenAPI 类型 → 网关 spec 类型 (与离线 generator 同表)。
TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "array",
}

# 34 个老工具无 description REST 提示, 人工对齐 (已逐一核对路径存在)。
# 与 scripts/gen_ths_local_tools.py:MANUAL_MAP 保持同步。
MANUAL_MAP: dict[str, tuple[str, str]] = {
    "fqgate_market_market_health": ("GET", "/v1/market/health"),
    "fqgate_market_search_symbols": ("POST", "/v1/market/catalog/search-symbols"),
    "fqgate_market_market_data_block": ("POST", "/v1/market/realtime/block"),
    "fqgate_market_market_data_bond": ("POST", "/v1/market/realtime/bond"),
    "fqgate_market_market_data_cn": ("POST", "/v1/market/realtime/cn"),
    "fqgate_market_market_data_forex": ("POST", "/v1/market/realtime/forex"),
    "fqgate_market_market_data_fund": ("POST", "/v1/market/realtime/fund"),
    "fqgate_market_market_data_future": ("POST", "/v1/market/realtime/future"),
    "fqgate_market_market_data_hk": ("POST", "/v1/market/realtime/hk"),
    "fqgate_market_market_data_index": ("POST", "/v1/market/realtime/index"),
    "fqgate_market_quote": ("POST", "/v1/market/realtime/quote"),
    "fqgate_market_security_trading_status": ("POST", "/v1/market/realtime/trading-status"),
    "fqgate_market_market_data_uk": ("POST", "/v1/market/realtime/uk"),
    "fqgate_market_market_data_us": ("POST", "/v1/market/realtime/us"),
    "fqgate_market_cached_login": ("POST", "/v1/market/session/cached-login"),
    "fqgate_market_qr_login_begin": ("POST", "/v1/market/session/qr/begin"),
    "fqgate_market_intraday": ("POST", "/v1/market/history/intraday"),
    "fqgate_market_klines": ("POST", "/v1/market/history/klines"),
    "fqgate_market_news": ("POST", "/v1/market/information/news"),
    "fqgate_market_level2_best_ask_queue": ("POST", "/v1/market/level2/best-ask-queue"),
    "fqgate_market_level2_best_bid_queue": ("POST", "/v1/market/level2/best-bid-queue"),
    "fqgate_market_bid_ask_rank": ("POST", "/v1/market/level2/bid-ask-rank"),
    "fqgate_market_level2_buy_cancellations": ("POST", "/v1/market/level2/cancellations/buy"),
    "fqgate_market_level2_sell_cancellations": ("POST", "/v1/market/level2/cancellations/sell"),
    "fqgate_market_level2_depth": ("POST", "/v1/market/level2/depth"),
    "fqgate_market_enhanced_transactions": ("POST", "/v1/market/level2/enhanced-transactions"),
    "fqgate_market_order_detail_by_sequence": ("POST", "/v1/market/level2/order-detail-by-sequence"),
    "fqgate_market_level2_orders": ("POST", "/v1/market/level2/orders"),
    "fqgate_market_level2_transactions": ("POST", "/v1/market/level2/transactions"),
    "fqgate_market_order_book_ask": ("POST", "/v1/market/order-book/ask"),
    "fqgate_market_order_book_bid": ("POST", "/v1/market/order-book/bid"),
    "fqgate_market_wencai_base": ("POST", "/v1/market/selection/wencai-base"),
    "fqgate_market_logout": ("DELETE", "/v1/market/session"),
    "fqgate_market_clear_cached_login": ("DELETE", "/v1/market/session/saved-login"),
}

_POST_HINT_RE = re.compile(r"POST\s+(/v1/[^\s，。]+)")
_WS_RE = re.compile(r"\s+")


# ----------------------------------------------------------------------
# 数据类型
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class UnmappedTool:
    """上游 MCP 工具无法对应到 REST 路径, 本次跳过 (不进实体, 不热注册)。"""

    name: str
    reason: str

    def to_json(self) -> dict[str, str]:
        return {"name": self.name, "reason": self.reason}


@dataclass(frozen=True)
class ImportToolChange:
    """单个工具在本次导入中的处置。"""

    name: str
    action: str
    changed_fields: tuple[str, ...]
    entity: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "action": self.action,
            "changed_fields": list(self.changed_fields),
        }


@dataclass(frozen=True)
class ImportPlan:
    """cur 实体 ↔ 上游实时转换结果的差异台账。"""

    changes: tuple[ImportToolChange, ...]
    unmapped: tuple[UnmappedTool, ...] = ()
    # cur 中同源历史导入行 (openapi-import + 仅映射本上游) 但上游已不再列出。
    removed: tuple[str, ...] = ()

    @property
    def added(self) -> tuple[ImportToolChange, ...]:
        return tuple(c for c in self.changes if c.action == ACTION_ADDED)

    @property
    def changed(self) -> tuple[ImportToolChange, ...]:
        return tuple(c for c in self.changes if c.action == ACTION_CHANGED)

    @property
    def unchanged(self) -> tuple[ImportToolChange, ...]:
        return tuple(c for c in self.changes if c.action == ACTION_UNCHANGED)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed)

    def change_entities(
        self, selected_names: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """added+changed 的实体行 (导入顺序); selected_names 非空时做白名单。"""
        entities = [c.entity for c in self.changes if c.action in
                    (ACTION_ADDED, ACTION_CHANGED)]
        if selected_names is None:
            return entities
        change_names = {c.name for c in self.changes if c.action in
                        (ACTION_ADDED, ACTION_CHANGED)}
        unknown = selected_names - change_names
        if unknown:
            raise ValueError(
                "selected tools not in added/changed set: " + ", ".join(sorted(unknown))
            )
        return [e for e in entities if e["name"] in selected_names]

    def summary(self) -> dict[str, int]:
        return {
            "added": len(self.added),
            "changed": len(self.changed),
            "unchanged": len(self.unchanged),
            "removed": len(self.removed),
            "unmapped": len(self.unmapped),
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "changes": [c.to_json() for c in self.changes],
            "unmapped": [u.to_json() for u in self.unmapped],
            "removed": list(self.removed),
        }


# ----------------------------------------------------------------------
# OpenAPI schema 解析 (移植自 scripts/gen_ths_local_tools.py)
# ----------------------------------------------------------------------


def resolve_schema(schemas: dict[str, Any], schema: Any, depth: int = 0) -> dict[str, Any]:
    """解 $ref / 剥 nullable / 收敛 oneOf。

    返回 ``{type, description, enum, items, example, properties, required}``
    子集; depth>8 或非 dict 返 {} 防御 $ref 环。
    """
    if depth > 8 or not isinstance(schema, dict):
        return {}
    if "$ref" in schema:
        return resolve_schema(
            schemas, schemas.get(schema["$ref"].split("/")[-1], {}), depth + 1
        )
    out = {
        k: v
        for k, v in schema.items()
        if k in ("type", "description", "enum", "items", "example",
                 "oneOf", "properties", "required")
    }
    if "properties" in out:
        out["properties"] = {
            k: resolve_schema(schemas, v, depth + 1)
            for k, v in out["properties"].items()
        }
    if "$ref" in schema.get("items", {}) or "oneOf" in schema.get("items", {}):
        out["items"] = resolve_schema(schemas, schema["items"], depth + 1)
    elif isinstance(schema.get("items"), dict):
        item = schema["items"]
        out["items"] = {
            k: v for k, v in item.items()
            if k in ("type", "description", "example")
        }
    if "oneOf" in out:
        branches = [resolve_schema(schemas, b, depth + 1) for b in out["oneOf"]]
        non_null = [b for b in branches if b.get("type") not in (None, "null")]
        if non_null:
            merged = non_null[0]
            merged.setdefault("description", out.get("description", ""))
            if out.get("description") and out["description"] not in (
                merged.get("description") or ""
            ):
                merged["description"] = out["description"] or merged.get("description")
            return merged
    if isinstance(out.get("type"), list):
        types = [t for t in out["type"] if t != "null"]
        out["type"] = types[0] if types else "string"
    if out.get("type") is None and "$ref" not in schema:
        out["type"] = "object"  # 兜底
    return out


def _shape_hint(item: dict[str, Any]) -> str:
    """数组元素的形状提示, 拼进参数描述。"""
    if not isinstance(item, dict):
        return ""
    if item.get("type") in ("string", "integer", "number", "boolean"):
        example = item.get("example")
        return (
            f'元素为 {item["type"]}'
            + (f" (如 {example})"
               if example is not None and not isinstance(example, (dict, list))
               else "")
        )
    props = item.get("properties") or {}
    if props:
        parts = []
        for key, value in props.items():
            desc = (value.get("description") or "").rstrip("。")
            parts.append(f"{key}={desc}" if desc else key)
        return "元素为对象: {" + "; ".join(parts) + "}"
    return ""


def _norm_desc(desc: Any) -> str:
    return _WS_RE.sub(" ", (desc or "").strip())


# ----------------------------------------------------------------------
# OpenAPI + MCP tools/list → 工具实体行
# ----------------------------------------------------------------------


def convert_openapi_tools(
    openapi: dict[str, Any],
    mcp_tools: list[dict[str, Any]],
    *,
    upstream_label: str = FQGATE_UPSTREAM_NAME,
    name_prefix: str = "fqgate_",
    manual_map: dict[str, tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], list[UnmappedTool]]:
    """把活的 OpenAPI 文档 + 上游原始 tools/list 转成工具实体行。

    返回 ``(entities, unmapped)``: entities 按工具名排序, 行形态与
    ``NormalizedTemplate.to_json()`` 的 tools 元素同形 (含
    origin='user' / source_file='openapi-import'), 可直接喂导入合并与
    ConfigStore.upsert_tools; unmapped 为找不到 REST 路径的上游工具。
    """
    manual_map = MANUAL_MAP if manual_map is None else manual_map
    schemas = _validate_openapi(openapi)
    paths = openapi["paths"]

    raw_by_name: dict[str, dict[str, Any]] = {}
    for tool in mcp_tools:
        name = tool.get("name")
        if isinstance(name, str) and name.startswith(name_prefix):
            raw_by_name[name] = tool

    rest_map, unmapped = _resolve_rest_paths(raw_by_name, paths, manual_map)

    entities: list[dict[str, Any]] = []
    for name in sorted(rest_map):
        method, path = rest_map[name]
        operation = paths[path].get(method.lower())
        if not isinstance(operation, dict):
            unmapped.append(UnmappedTool(
                name, f"operation {method} {path} not found in OpenAPI"
            ))
            continue
        params = _build_params(schemas, operation, raw_by_name[name])
        description = raw_by_name[name].get("description") or ""
        spec = {
            "name": name,
            "description": description,
            "routing": name,
            "params": params,
            "upstream_tool_mapping": {upstream_label: name},
            "data_source_type": ONLINE_DATA_SOURCE,
        }
        entities.append({
            "name": name,
            "routing": name,
            "description": description,
            "cache_ttl_key": None,
            "requires_env": None,
            "publish_mode": "default",
            "mapping": {upstream_label: name},
            "spec": spec,
            "source_file": IMPORT_SOURCE_FILE,
            "origin": "user",
        })
    return entities, unmapped


def _validate_openapi(openapi: Any) -> dict[str, Any]:
    if not isinstance(openapi, dict):
        raise ValueError("openapi document must be an object")
    if not isinstance(openapi.get("paths"), dict):
        raise ValueError("openapi document missing 'paths' object")
    schemas = ((openapi.get("components") or {}).get("schemas"))
    if not isinstance(schemas, dict):
        raise ValueError("openapi document missing components.schemas")
    return schemas


def _resolve_rest_paths(
    raw_by_name: dict[str, dict[str, Any]],
    paths: dict[str, Any],
    manual_map: dict[str, tuple[str, str]],
) -> tuple[dict[str, tuple[str, str]], list[UnmappedTool]]:
    """工具名 → (HTTP method, path)。description 的 POST 提示优先, MANUAL_MAP 兜底。"""
    rest_map: dict[str, tuple[str, str]] = {}
    unmapped: list[UnmappedTool] = []
    for name, tool in raw_by_name.items():
        match = _POST_HINT_RE.search(tool.get("description") or "")
        path = match.group(1).rstrip("。，") if match else None
        if path and path in paths:
            rest_map[name] = ("POST", path)
    for name, pair in manual_map.items():
        if name not in raw_by_name or name in rest_map:
            continue
        if pair[1] not in paths:
            unmapped.append(UnmappedTool(
                name, f"manual-mapped path missing from OpenAPI: {pair[0]} {pair[1]}"
            ))
            continue
        rest_map.setdefault(name, pair)
    for name in raw_by_name:
        if name not in rest_map and not any(u.name == name for u in unmapped):
            unmapped.append(UnmappedTool(
                name, "no REST path hint in MCP description and no manual mapping"
            ))
    return rest_map, unmapped


def _build_params(
    schemas: dict[str, Any],
    operation: dict[str, Any],
    raw_tool: dict[str, Any],
) -> list[dict[str, Any]]:
    """REST query/body 参数 (权威) + MCP 层独有参数 (requestTimeoutMs) 末尾追加。"""
    import json

    params: list[dict[str, Any]] = []
    for prm in operation.get("parameters") or []:
        if not isinstance(prm, dict) or prm.get("in") != "query":
            continue
        resolved = resolve_schema(schemas, prm.get("schema"))
        params.append({
            "name": prm["name"],
            "type": TYPE_MAP.get(resolved.get("type"), "str"),
            "required": bool(prm.get("required", False)),
            "description": _norm_desc(
                prm.get("description") or resolved.get("description")
            ),
        })

    media = (
        (operation.get("requestBody") or {})
        .get("content", {})
        .get("application/json", {})
    )
    body_example = media.get("example") or {}
    body_schema = media.get("schema")
    if body_schema:
        resolved = resolve_schema(schemas, body_schema)
        required = set(resolved.get("required") or [])
        for key, value in (resolved.get("properties") or {}).items():
            desc = _norm_desc(value.get("description"))
            hint = _shape_hint(value.get("items") or {})
            if hint:
                sep = "" if desc.endswith("。") else ("，" if desc else "")
                desc = desc + sep + hint
            example = value.get("example")
            if not isinstance(example, list):
                example = body_example.get(key)
            if isinstance(example, list) and example and isinstance(
                example[0], (dict, int)
            ):
                desc += "，如 " + json.dumps(example, ensure_ascii=False)
            param = {
                "name": key,
                "type": TYPE_MAP.get(value.get("type"), "str"),
                "required": key in required,
                "description": desc,
            }
            if value.get("enum"):
                param["enum"] = value["enum"]
            params.append(param)

    mcp_props = ((raw_tool.get("inputSchema") or {}).get("properties") or {})
    mcp_required = set((raw_tool.get("inputSchema") or {}).get("required") or [])
    rest_names = {p["name"] for p in params}
    for key, value in mcp_props.items():
        if key in rest_names:
            continue
        desc = _norm_desc(value.get("description"))
        if key == "requestTimeoutMs":
            desc += "，取值 ≥1"
        params.append({
            "name": key,
            "type": TYPE_MAP.get(value.get("type"), "str"),
            "required": key in mcp_required,
            "description": desc,
        })
    return params


# ----------------------------------------------------------------------
# cur 实体 ↔ 导入实体差异 / 合并
# ----------------------------------------------------------------------


def plan_openapi_import(
    cur_bundle: dict[str, Any],
    imported_entities: list[dict[str, Any]],
    *,
    upstream_label: str = FQGATE_UPSTREAM_NAME,
    imported_source: str = IMPORT_SOURCE_FILE,
    unmapped: list[UnmappedTool] | tuple[UnmappedTool, ...] = (),
) -> ImportPlan:
    """对比库内现状与实时导入实体, 产出差异台账 (纯计算, 不 mutate 输入)。

    ``unmapped`` 是转换器给出的"上游有工具但找不到 REST 路径"清单, 不参与
    added/changed/removed 判定, 只记账供控制台提示。
    """
    cur_tools = {t["name"]: t for t in cur_bundle.get("tools") or []}
    changes: list[ImportToolChange] = []
    imported_names: set[str] = set()
    for entity in imported_entities:
        name = entity["name"]
        imported_names.add(name)
        cur = cur_tools.get(name)
        if cur is None:
            action, changed_fields = ACTION_ADDED, ()
        else:
            changed_fields = tuple(
                field for field in _SIGNATURE_FIELDS
                if cur.get(field) != entity.get(field)
            )
            action = ACTION_CHANGED if changed_fields else ACTION_UNCHANGED
        changes.append(ImportToolChange(name, action, changed_fields, entity))

    removed = tuple(
        name
        for name, row in cur_tools.items()
        if row.get("source_file") == imported_source
        and name not in imported_names
        and set((row.get("mapping") or {}).keys()) <= {upstream_label}
    )
    return ImportPlan(
        changes=tuple(changes),
        unmapped=tuple(unmapped),
        removed=removed,
    )


def merge_imported_tools(
    cur_bundle: dict[str, Any],
    entities: list[dict[str, Any]],
) -> dict[str, Any]:
    """把导入实体并入 cur bundle (深拷贝, 不 mutate 输入)。

    - 新工具追加到 tools 末尾, position = max(position)+1;
    - 已存在行: spec/routing/description/mapping/source_file 用导入值,
      但保留 cur 的 position 与本地门控列 (cache_ttl_key / requires_env /
      publish_mode) —— 用户的 hidden/TTL/env 门控不能被一次刷新冲掉;
      保留的非空门控列按 _sync_tool_spec 同构语义回写 spec;
    - sections / upstreams 与未涉及工具原样保留。
    """
    merged = copy.deepcopy(cur_bundle)
    tools = list(merged.get("tools") or [])
    index = {t["name"]: i for i, t in enumerate(tools)}
    next_position = max((t.get("position", i) for i, t in enumerate(tools)),
                        default=-1) + 1

    for entity in entities:
        name = entity["name"]
        if name in index:
            row = tools[index[name]]
            updated = {k: copy.deepcopy(v) for k, v in entity.items()}
            updated["position"] = row.get("position")
            # 本地门控列保留 cur 值 (导入实体本身恒为 None/'default')。
            for preserved in ("cache_ttl_key", "requires_env", "publish_mode"):
                updated[preserved] = row.get(preserved)
            _sync_preserved_to_spec(updated)
            tools[index[name]] = updated
        else:
            updated = copy.deepcopy(entity)
            updated["position"] = next_position
            next_position += 1
            tools.append(updated)

    merged["tools"] = tools
    return merged


def _sync_preserved_to_spec(entity: dict[str, Any]) -> None:
    """保留的 cache_ttl_key/requires_env 回写 spec (与 config_upgrade 同语义)。"""
    spec = entity.get("spec")
    if not isinstance(spec, dict):
        return
    for key in ("cache_ttl_key", "requires_env"):
        value = entity.get(key)
        if value is None:
            if spec.get(key) is not None:
                spec.pop(key, None)
        else:
            spec[key] = value


# ----------------------------------------------------------------------
# YAML 片段导出 (评审后并入出厂模板 tools_ths_local.yaml)
# ----------------------------------------------------------------------

def _yaml_quote(value: str) -> str:
    return (
        '"'
        + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        + '"'
    )


def _yaml_scalar(value: Any) -> str:
    text = str(value)
    return text if re.fullmatch(r"[A-Za-z0-9_.-]+", text) else _yaml_quote(text)


def _spec_line(spec: dict[str, Any]) -> str:
    """与 scripts/gen_ths_local_tools.py:spec_line 同形的单行 flow YAML。"""
    param_strs = []
    for param in spec.get("params") or []:
        enum = ""
        if param.get("enum"):
            enum = (", enum: ["
                    + ", ".join(_yaml_scalar(v) for v in param["enum"]) + "]")
        param_strs.append(
            "{name: %s, type: %s, required: %s%s, description: %s}"
            % (
                param["name"],
                param["type"],
                "true" if param.get("required") else "false",
                enum,
                _yaml_quote(param.get("description", "")),
            )
        )
    mapping = spec.get("upstream_tool_mapping") or {}
    mapping_str = ", ".join(f"{key}: {value}" for key, value in mapping.items())
    return (
        "  - {name: %s, description: %s, routing: %s, params: [%s], "
        "upstream_tool_mapping: {%s}, data_source_type: %s}"
        % (
            spec["name"],
            _yaml_quote(spec.get("description", "")),
            spec.get("routing", spec["name"]),
            ", ".join(param_strs),
            mapping_str,
            spec.get("data_source_type", ONLINE_DATA_SOURCE),
        )
    )


def render_yaml_fragment(entities: list[dict[str, Any]]) -> str:
    """把导入实体渲染为可并入 tools_ths_local.yaml 的 flow-YAML 片段。"""
    if not entities:
        return ""
    lines = [
        f"# FQGate OpenAPI 导入片段 ({len(entities)} 工具, origin='user')",
        "# 评审后并入 config/tools_ths_local.yaml; 经出厂更新吸收后实体自动转为 builtin。",
        "tools:",
    ]
    lines.extend(_spec_line(e["spec"]) for e in entities)
    return "\n".join(lines) + "\n"
