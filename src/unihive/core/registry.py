"""
工具注册器 - 从 YAML spec 动态生成 FastMCP tool 函数
"""
import inspect
import logging
import re
from typing import Annotated, Any, Callable, Literal

from pydantic import Field

from .normalizer import Normalizer

logger = logging.getLogger(__name__)

_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "List[str]": list,
    # FQGate 等上游的数组参数 (securities/fields): 上游 MCP 层的 inputSchema
    # 常把数组误标为 string, 但运行时只收原生 JSON 数组 —— 必须以 list 透传,
    # 回落成 str 会把调用方的数组原样当字符串转发, 上游 REST 层直接 400。
    "array": list,
}

# 可选参数默认值：str 用 ""（_validate_and_normalize 视空串为缺失）；
# int/float/bool 必须 None —— 数值默认 0/0.0、布尔默认 False 会被当成
# 用户显式传入的值转发给上游（FQGate 的可选 requestTimeoutMs 带 minimum: 1,
# 网关塞 0 直接业务 400 "请求内容不完整"）。None 才能与"调用方未提供"区分开,
# 由 _validate_and_normalize 统一丢弃; 用户显式传 0/False 不受影响。
def resolve_python_type(type_str: str | None) -> type | None:
    """spec ``type`` 字符串 → MCP 边界实际使用的 Python 类型。

    - ``_TYPE_MAP`` 中的精确名按表映射;
    - tqcenter codegen 产物的裸 ``List`` / 任意 ``List[...]``
      (含 ``List[List[str]]``) 统一按 JSON 数组透传; ``Dict`` 系按对象透传
      (调用链 ``getattr(tq, tool)(**arguments)`` 原样进 tqcenter,
      无需还原元素类型; 标注成 ``list``/``dict`` 只决定 JSON schema 的
      type: array/object);
    - 未知名返回 None, 调用方回落 str 并由 config_lint 报 W001。
    """
    if type_str in _TYPE_MAP:
        return _TYPE_MAP[type_str]
    if type_str == "List" or (
        isinstance(type_str, str) and type_str.startswith("List[")
    ):
        return list
    if type_str in ("Dict", "dict") or (
        isinstance(type_str, str) and type_str.startswith("Dict[")
    ):
        return dict
    return None


_DEFAULT_MAP: dict[str, Any] = {
    "str": "",
    "int": None,
    "float": None,
    "bool": None,
}

# 日期参数名: 统一归一为 YYYYMMDD (TQ-Local strict; 其他上游也用同格式)
_DATE_PARAM_NAMES = frozenset({"start_time", "end_time", "start_date", "end_date"})

# 字符串但语义上应该是数组的参数: 逗号分隔转列表 (Pydantic 不接受 List[str] 时)
_LIST_PARAM_NAMES = frozenset({"stock_list", "field_list"})


def _annotation_for(p: dict) -> Any:
    """根据 spec 构造类型注解。

    - 有 enum → Literal[*values]，FastMCP 会把它转成 JSON Schema enum
    - 有 description → Annotated[X, Field(description=...)]，Pydantic FastMCP 会提取
    """
    resolved = resolve_python_type(p.get("type", "str"))
    base = resolved if resolved is not None else str
    enum_values = p.get("enum")
    description = p.get("description")
    if enum_values:
        base = Literal[tuple(enum_values)]  # type: ignore[valid-type]
    if description:
        return Annotated[base, Field(description=description)]
    return base


def _build_parameter(p: dict) -> inspect.Parameter:
    name = p["name"]
    ann = _annotation_for(p)
    if p.get("required", True):
        return inspect.Parameter(
            name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ann
        )
    # optional + 有 enum 时 default=None，避免 default="invalid_value" 被静默吃掉
    # 数值/布尔默认 None（见 _DEFAULT_MAP 注释），缺失时不转发给上游
    if p.get("enum"):
        default = None
    else:
        default = _DEFAULT_MAP.get(p.get("type", "str"), None)
    return inspect.Parameter(
        name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ann, default=default
    )


def build_signature(params: list[dict]) -> inspect.Signature:
    # 排序：required 先于 optional，避免 Python "non-default argument follows default argument"
    ordered = sorted(params, key=lambda p: 0 if p.get("required", True) else 1)
    return inspect.Signature(parameters=[_build_parameter(p) for p in ordered])


def _normalize_param(name: str, value: Any, spec: dict) -> Any:
    p = next((x for x in spec if x["name"] == name), None)
    if p is None:
        return value
    if p.get("normalize") == "code" and isinstance(value, str):
        value = Normalizer.normalize_code(value)
    if not isinstance(value, str):
        return value
    # 日期归一: YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD → YYYYMMDD
    if name in _DATE_PARAM_NAMES:
        digits = re.sub(r"\D", "", value)
        if len(digits) == 8:
            return digits
    # CSV 列表: "600000.SH,000001.SZ" → ["600000.SH", "000001.SZ"]
    if name in _LIST_PARAM_NAMES:
        parts = [s.strip() for s in value.split(",") if s.strip()]
        if parts:
            return parts
    return value


def _validate_and_normalize(name: str, param_specs: list[dict], kwargs: dict) -> dict:
    """校验 + 标准化参数。空值按缺失处理，必填缺失/枚举越界抛 ValueError。

    Raises:
        ValueError: 必填缺失、传了空字符串、enum 越界
    """
    normalized: dict = {}
    for p in param_specs:
        v = kwargs.get(p["name"])
        v = _normalize_param(p["name"], v, param_specs)
        # 空值 → 按缺失处理
        if v is None or v == "":
            if p.get("required", True):
                raise ValueError(
                    f"{name}: required parameter {p['name']!r} is missing or empty"
                )
            continue
        # enum 校验
        enum_values = p.get("enum")
        if enum_values and v not in enum_values:
            raise ValueError(
                f"{name}: invalid value for {p['name']!r}: {v!r}, "
                f"expected one of {enum_values}"
            )
        normalized[p["name"]] = v
    return normalized


def build_tool_function(spec: dict, server: Any) -> Callable:
    """从 spec 构造带正确 signature/annotations/doc 的 async 函数。

    server 必须有 _execute_cached(name, params, route_key, ttl_key) 方法。
    dangerous=True 的 spec 会在每次调用时写一条 WARNING 审计日志，但
    不再做任何运行时拦截。
    """
    name: str = spec["name"]
    description: str = spec.get("description", "")
    route_key: str = spec.get("routing", name)
    ttl_key: str | None = spec.get("cache_ttl_key")
    param_specs: list[dict] = spec.get("params", [])
    dangerous: bool = spec.get("dangerous", False)

    sig = build_signature(param_specs)

    async def _runtime(**kwargs) -> dict:
        normalized = _validate_and_normalize(name, param_specs, kwargs)
        if dangerous:
            logger.warning("DANGEROUS call: %s args=%s", name, normalized)
        return await server._execute_cached(
            name, normalized, route_key=route_key, ttl_key=ttl_key
        )

    _runtime.__signature__ = sig
    # annotations 跟随 signature（保留 Literal/Annotated/Field 信息），
    # 否则 FastMCP 看到的就是裸 str，丢失 enum/description
    _runtime.__annotations__ = {
        p["name"]: _annotation_for(p) for p in param_specs
    }
    _runtime.__annotations__["return"] = dict
    _runtime.__name__ = name
    _runtime.__doc__ = description
    return _runtime


def register_tool(server: Any, spec: dict) -> str:
    """注册单个 spec 为 FastMCP tool, 返回工具名。

    启动批量注册与运行时热启用共用此函数, 避免两处注册逻辑漂移。
    FastMCP 4 的 mcp.tool() 直接写入 live local provider, server 启动后
    调用同样即时对 tools/list 生效。
    """
    fn = build_tool_function(spec, server)
    annotations = spec.get("annotations")
    if annotations:
        server.mcp.tool(annotations=annotations)(fn)
    else:
        server.mcp.tool()(fn)
    return spec["name"]


def register_tools_from_config(
    server: Any,
    specs: list[dict],
) -> list[str]:
    """注册所有 spec 为 FastMCP tool。返回注册的 name 列表。"""
    return [register_tool(server, spec) for spec in specs]


def validate_specs(
    specs: list[dict],
    routing_config: dict,
    upstream_tool_mapping: dict,
    *,
    strict: bool = True,
) -> list[str]:
    """校验 spec 中的 routing 都在 routing_config / upstream_tool_mapping 中，
    或 spec 自带 upstream_tool_mapping（self-routing，如 TQ-Local codegen 产物）。"""
    errors: list[str] = []
    for spec in specs:
        name = spec.get("name")
        if not name:
            errors.append("Tool missing 'name'")
            continue
        routing_key = spec.get("routing")
        if not routing_key:
            errors.append(f"Tool {name!r} missing 'routing'")
            continue
        in_routing = routing_key in routing_config
        in_mapping = routing_key in upstream_tool_mapping
        has_self_mapping = bool(spec.get("upstream_tool_mapping"))
        if not (in_routing or in_mapping or has_self_mapping):
            errors.append(
                f"Tool {name!r} routing={routing_key!r} not in routing config nor upstream mapping"
            )
    if strict and errors:
        raise ValueError("Registry validation failed: " + "; ".join(errors))
    return errors
