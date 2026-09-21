"""控制台实体编辑的纯函数层 (P3)。

职责 (不做 IO, 不做语义 lint):

- 工具 patch 的字段白名单与**描述层 / 契约层**分层;
- 边界形态校验 (类型、必填、命名、值域);
- 把可编辑字段同步回实体行的抽取列与完整 ``spec`` —— DB 里 spec_json
  是装配的唯一来源, 列只服务检索/面板, 两者必须一致。

语义级校验 (悬空 mapping / 未知上游 / enum 与上游契约冲突 / 路由链一致)
由 :mod:`unihive.core.config_lint` 在写入门与热重注册前负责。
"""
from __future__ import annotations

import copy
import re
from typing import Any, Iterable

#: 描述层: 普通保存, 即写即热生效。
TOOL_DESCRIPTION_FIELDS = frozenset(
    {"description", "params", "cache_ttl_key", "publish_mode"}
)
#: 契约层: 改变对外 MCP 契约, 需 confirm_dangerous + lint + 热重注册。
TOOL_CONTRACT_FIELDS = frozenset({"name", "routing", "requires_env", "mapping"})
TOOL_WRITABLE_FIELDS = TOOL_DESCRIPTION_FIELDS | TOOL_CONTRACT_FIELDS

PUBLISH_MODES = frozenset({"default", "hidden"})

#: params 条目允许出现的键 (与 config/tools_*.yaml 出厂形态一致)。
#: 多余键过去被静默透传进 spec, 控制台可借此向对外 MCP 契约夹带任意字段
#: (含疑似密钥键); H6 起未知键直接拒绝。
PARAM_WRITABLE_KEYS = frozenset(
    {"name", "type", "required", "description", "enum", "normalize"}
)

#: 对外 MCP 工具名 (与 293 个现存工具名一致: 字母开头, 字母数字下划线)。
_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: 上游连接块里这两个键是实体列, 混进 config_json 会造成双事实源。
UPSTREAM_RESERVED_CONFIG_KEYS = frozenset({"enabled", "type"})


class EntityEditError(ValueError):
    """编辑入参非法 (字段不在白名单 / 类型或形态不合法)。"""


class DangerousConfirmationRequired(ValueError):
    """patch 触及契约层字段但请求未带 confirm_dangerous=true。"""

    def __init__(self, contract_fields: list[str]):
        self.contract_fields = list(contract_fields)
        super().__init__(
            "contract-layer field(s) require confirm_dangerous=true: "
            + ", ".join(self.contract_fields)
        )


def contract_fields_in_patch(patch: dict[str, Any]) -> set[str]:
    """patch 触及的契约层字段集合 (空集 = 纯描述层编辑)。"""
    return TOOL_CONTRACT_FIELDS & set(patch)


def apply_tool_patch(
    row: dict[str, Any],
    patch: dict[str, Any],
    *,
    existing_names: Iterable[str] = (),
) -> dict[str, Any]:
    """把白名单 patch 应用到工具实体行, 返回**新行** (输入不被修改)。

    ``row`` 需含 name/routing/description/cache_ttl_key/requires_env/
    publish_mode/mapping/spec 键 (即 store.get_tool 的返回形态)。
    改名时调用方需传入 ``existing_names`` (含其余全部工具名) 做撞名校验。
    抽取列与 ``spec`` 同步更新; 非白名单字段原样保留。
    """
    if not isinstance(patch, dict) or not patch:
        raise EntityEditError("tool patch must be a non-empty mapping")
    unknown = set(patch) - TOOL_WRITABLE_FIELDS
    if unknown:
        raise EntityEditError(
            f"non-writable tool field(s): {', '.join(sorted(unknown))}"
        )

    out = copy.deepcopy(row)
    spec = out["spec"]
    if not isinstance(spec, dict):
        raise EntityEditError("stored tool spec is not a mapping")

    if "description" in patch:
        description = patch["description"]
        if not isinstance(description, str):
            raise EntityEditError("description must be a string")
        out["description"] = description
        spec["description"] = description

    if "params" in patch:
        params = _validate_params(patch["params"])
        spec["params"] = copy.deepcopy(params)

    if "cache_ttl_key" in patch:
        ttl = patch["cache_ttl_key"]
        if ttl is not None and (not isinstance(ttl, str) or not ttl):
            raise EntityEditError("cache_ttl_key must be a non-empty string or null")
        out["cache_ttl_key"] = ttl
        if ttl is None:
            spec.pop("cache_ttl_key", None)
        else:
            spec["cache_ttl_key"] = ttl

    if "publish_mode" in patch:
        mode = patch["publish_mode"]
        if mode not in PUBLISH_MODES:
            raise EntityEditError(
                f"publish_mode must be one of {sorted(PUBLISH_MODES)}, got {mode!r}"
            )
        out["publish_mode"] = mode

    # --- 契约层 ---
    if "requires_env" in patch:
        env = patch["requires_env"]
        if env is not None and not (isinstance(env, str) and _ENV_VAR_RE.fullmatch(env)):
            raise EntityEditError(
                f"requires_env must be a valid env var name or null, got {env!r}"
            )
        out["requires_env"] = env
        if env is None:
            spec.pop("requires_env", None)
        else:
            spec["requires_env"] = env

    if "mapping" in patch:
        new_mapping = _validate_mapping(patch["mapping"])
        out["mapping"] = new_mapping
        # 抽取列与 spec 必须一致: DB 里 spec 是装配唯一来源。空 mapping 时
        # 顶层条目会被 assembler 丢弃, spec 里残留的 stale upstream_tool_mapping
        # 会让 config_lint has_self_mapping 误判为真, 屏蔽 T001/M003。
        if new_mapping:
            spec["upstream_tool_mapping"] = copy.deepcopy(new_mapping)
        else:
            spec.pop("upstream_tool_mapping", None)

    if "routing" in patch:
        routing = patch["routing"]
        if not isinstance(routing, str) or not routing:
            raise EntityEditError("routing must be a non-empty string")
        out["routing"] = routing
        spec["routing"] = routing

    if "name" in patch:
        new_name = patch["name"]
        if not isinstance(new_name, str) or not _TOOL_NAME_RE.fullmatch(new_name):
            raise EntityEditError(
                f"tool name must match {_TOOL_NAME_RE.pattern}, got {new_name!r}"
            )
        if new_name != row["name"] and new_name in set(existing_names):
            raise EntityEditError(f"tool name already exists: {new_name!r}")
        out["name"] = new_name
        spec["name"] = new_name

    return out


def _validate_params(params: Any) -> list[dict[str, Any]]:
    if not isinstance(params, list):
        raise EntityEditError("params must be a list")
    seen: set[str] = set()
    for index, param in enumerate(params):
        if not isinstance(param, dict):
            raise EntityEditError(f"params[{index}] must be a mapping")
        unknown = set(param) - PARAM_WRITABLE_KEYS
        if unknown:
            raise EntityEditError(
                f"params[{index}] has unknown key(s): {', '.join(sorted(unknown))}"
            )
        name = param.get("name")
        if not isinstance(name, str) or not name:
            raise EntityEditError(f"params[{index}].name must be a non-empty string")
        if name in seen:
            raise EntityEditError(f"duplicate param name: {name!r}")
        seen.add(name)
        if "type" in param and not (isinstance(param["type"], str) and param["type"]):
            raise EntityEditError(f"params[{index}].type must be a non-empty string")
        if "required" in param and not isinstance(param["required"], bool):
            raise EntityEditError(f"params[{index}].required must be boolean")
        if "description" in param and not isinstance(param["description"], str):
            raise EntityEditError(f"params[{index}].description must be a string")
        if "normalize" in param and not (
            isinstance(param["normalize"], str) and param["normalize"]
        ):
            raise EntityEditError(
                f"params[{index}].normalize must be a non-empty string"
            )
        if "enum" in param:
            enum = param["enum"]
            if not isinstance(enum, list) or any(
                isinstance(v, bool) or not isinstance(v, (str, int, float)) for v in enum
            ):
                raise EntityEditError(
                    f"params[{index}].enum must be a list of string/number"
                )
    return params


def _validate_mapping(mapping: Any) -> dict[str, str]:
    if not isinstance(mapping, dict):
        raise EntityEditError("mapping must be a mapping of {upstream: upstream_method}")
    for key, value in mapping.items():
        if not isinstance(key, str) or not key:
            raise EntityEditError("mapping upstream names must be non-empty strings")
        if not isinstance(value, str) or not value:
            raise EntityEditError(
                f"mapping method for upstream {key!r} must be a non-empty string"
            )
    return copy.deepcopy(mapping)


def validate_upstream_config(config: Any) -> dict[str, Any]:
    """校验上游连接块形态, 返回深拷贝。

    连接块是异型 dict, 这里只守边界 (mapping / 键名 / 列保留键);
    各 type 的专属字段语义由启动期 client 构造与 lint 负责。
    """
    if not isinstance(config, dict):
        raise EntityEditError("upstream config must be a mapping")
    reserved = set(config) & UPSTREAM_RESERVED_CONFIG_KEYS
    if reserved:
        raise EntityEditError(
            f"upstream config must not contain column key(s): {sorted(reserved)}"
        )
    for key in config:
        if not isinstance(key, str) or not key:
            raise EntityEditError("upstream config keys must be non-empty strings")
    return copy.deepcopy(config)
