"""
工具注册器 - 从 YAML spec 动态生成 FastMCP tool 函数
"""
import inspect
import logging
from typing import Any, Callable

from .normalizer import Normalizer

logger = logging.getLogger(__name__)

_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}

_DEFAULT_MAP: dict[str, Any] = {
    "str": "",
    "int": 0,
    "float": 0.0,
    "bool": False,
}


def _build_parameter(p: dict) -> inspect.Parameter:
    name = p["name"]
    ann = _TYPE_MAP.get(p.get("type", "str"), str)
    if p.get("required", True):
        return inspect.Parameter(
            name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ann
        )
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
        return Normalizer.normalize_code(value)
    return value


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
        normalized: dict = {}
        for p in param_specs:
            v = kwargs.get(p["name"])
            v = _normalize_param(p["name"], v, param_specs)
            if v is None or v == "":
                continue
            normalized[p["name"]] = v
        if dangerous:
            logger.warning("DANGEROUS call: %s args=%s", name, normalized)
        return await server._execute_cached(
            name, normalized, route_key=route_key, ttl_key=ttl_key
        )

    _runtime.__signature__ = sig
    _runtime.__annotations__ = {
        p["name"]: _TYPE_MAP.get(p.get("type", "str"), str) for p in param_specs
    }
    _runtime.__annotations__["return"] = dict
    _runtime.__name__ = name
    _runtime.__doc__ = description
    return _runtime


def register_tools_from_config(
    server: Any,
    specs: list[dict],
) -> list[str]:
    """注册所有 spec 为 FastMCP tool。返回注册的 name 列表。"""
    registered: list[str] = []
    for spec in specs:
        fn = build_tool_function(spec, server)
        server.mcp.tool()(fn)
        registered.append(spec["name"])
    return registered


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
