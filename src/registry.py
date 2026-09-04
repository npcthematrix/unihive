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


_CONFIRM_PARAM = "confirm"

_CONFIRM_SPEC: dict = {
    "name": _CONFIRM_PARAM,
    "type": "bool",
    "required": False,
}

_CONFIRM_DOC = (
    "\n\n⚠️ 这是高风险写操作。默认不执行：必须先向用户说明本次调用的具体影响并取得同意，"
    "然后重新调用并传 confirm=true。未确认的调用会被网关拒绝，不会触达上游。"
)


def _confirmation_required(name: str) -> dict:
    return {
        "success": False,
        "data": None,
        "error": (
            f"{name} 是高风险操作，已被网关拦截。请先向用户说明影响并取得同意，"
            f"再以 confirm=true 重新调用。"
        ),
        "source": None,
        "hops": [],
        "requires_confirmation": True,
    }


def build_tool_function(spec: dict, server: Any) -> Callable:
    """从 spec 构造带正确 signature/annotations/doc 的 async 函数。

    server 必须有 _execute_cached(name, params, route_key, ttl_key) 方法。
    dangerous=True 的 spec 会额外获得一个 confirm 参数作为运行时确认门。
    """
    name: str = spec["name"]
    description: str = spec.get("description", "")
    route_key: str = spec.get("routing", name)
    ttl_key: str | None = spec.get("cache_ttl_key")
    param_specs: list[dict] = spec.get("params", [])
    dangerous: bool = spec.get("dangerous", False)

    # confirm 只进签名, 不进 param_specs, 所以永远不会被转发给上游
    signature_specs = param_specs + [_CONFIRM_SPEC] if dangerous else param_specs
    sig = build_signature(signature_specs)

    async def _runtime(**kwargs) -> dict:
        if dangerous and not kwargs.get(_CONFIRM_PARAM):
            logger.warning("DANGEROUS call rejected as unconfirmed: %s", name)
            return _confirmation_required(name)
        normalized: dict = {}
        for p in param_specs:
            v = kwargs.get(p["name"])
            v = _normalize_param(p["name"], v, param_specs)
            if v is None or v == "":
                continue
            normalized[p["name"]] = v
        if dangerous:
            logger.warning(
                "DANGEROUS call: %s args=%s", name, normalized
            )
        return await server._execute_cached(
            name, normalized, route_key=route_key, ttl_key=ttl_key
        )

    # 让 FastMCP 通过 inspect.signature() 拿到正确 schema
    _runtime.__signature__ = sig
    _runtime.__annotations__ = {
        p["name"]: _TYPE_MAP.get(p.get("type", "str"), str) for p in signature_specs
    }
    _runtime.__annotations__["return"] = dict
    _runtime.__name__ = name
    _runtime.__doc__ = description + _CONFIRM_DOC if dangerous else description
    return _runtime


def register_tools_from_config(
    server: Any,
    specs: list[dict],
    *,
    disable_dangerous: bool = False,
) -> list[str]:
    """注册所有 spec 为 FastMCP tool。返回注册的 name 列表。"""
    registered: list[str] = []
    for spec in specs:
        if spec.get("dangerous") and disable_dangerous:
            logger.info("Skipping dangerous tool: %s", spec.get("name"))
            continue
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
