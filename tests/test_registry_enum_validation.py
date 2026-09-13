"""Regression #6: AI 工具调用必须有 enum + description 暴露给 schema，
且 _runtime 必须拒绝 enum 越界 / 必填缺失，避免 silent fallback 给错数据。"""
import asyncio
import inspect
from typing import Literal

import pytest

from src.unihive.core.registry import (
    _annotation_for,
    _build_parameter,
    build_signature,
    _validate_and_normalize,
    build_tool_function,
)


# ========== schema 暴露 ==========

def test_annotation_for_enum_uses_literal():
    """enum 字段必须让 annotation 变成 Literal[...]，FastMCP 会转成 JSON Schema enum。"""
    ann = _annotation_for({"name": "block_type", "type": "str", "enum": ["industry", "concept", "region"]})
    # Literal[...] 在 typing.get_origin/get_args 下可以识别
    from typing import get_args, get_origin
    assert get_origin(ann) is Literal
    assert get_args(ann) == ("industry", "concept", "region")


def test_annotation_for_description_uses_field():
    """description 必须挂在 Annotated[..., Field(...)] 上，Pydantic/FastMCP 会提取。"""
    ann = _annotation_for({"name": "x", "type": "str", "description": "test desc"})
    from typing import get_args
    # Annotated 的 metadata 包含 Field
    metadata = get_args(ann)[1:]
    assert any(metadata)


def test_signature_preserves_enum_and_required():
    """required + enum 参数必须显式必填 (无 default)。"""
    sig = build_signature([
        {"name": "block_type", "type": "str", "required": True, "enum": ["industry", "concept"]},
    ])
    p = sig.parameters["block_type"]
    assert p.annotation == Literal["industry", "concept"]
    assert p.default is inspect.Parameter.empty


def test_signature_optional_enum_has_none_default():
    """optional + enum 时 default=None, 不要 default="invalid_value" 被静默吃掉。"""
    sig = build_signature([
        {"name": "frequency", "type": "str", "required": False, "enum": ["daily", "weekly"]},
    ])
    p = sig.parameters["frequency"]
    assert p.default is None


# ========== 运行时校验 ==========

def _ok():
    return {"call": lambda name, params: asyncio.sleep(0, result={"ok": True, "params": params})}


def test_validate_enum_out_of_bounds_raises():
    """enum 越界必须抛 ValueError, 不让 silent fallback 误导用户。"""
    with pytest.raises(ValueError, match="invalid value for 'block_type'"):
        _validate_and_normalize(
            "moo_block_data",
            [{"name": "block_type", "type": "str", "required": True,
              "enum": ["industry", "concept", "region"]}],
            {"block_type": "area"},
        )


def test_validate_required_missing_raises():
    """必填字段传 None / "" / 不传都抛 ValueError."""
    spec = [{"name": "stock_code", "type": "str", "required": True, "normalize": "code"}]
    with pytest.raises(ValueError, match="required parameter 'stock_code'"):
        _validate_and_normalize("moo_realtime_quote", spec, {})
    with pytest.raises(ValueError, match="required parameter 'stock_code'"):
        _validate_and_normalize("moo_realtime_quote", spec, {"stock_code": ""})
    with pytest.raises(ValueError, match="required parameter 'stock_code'"):
        _validate_and_normalize("moo_realtime_quote", spec, {"stock_code": None})


def test_validate_optional_missing_skipped():
    """optional 字段缺失/空不应抛。"""
    spec = [{"name": "frequency", "type": "str", "required": False,
             "enum": ["daily", "weekly"]}]
    assert _validate_and_normalize("moo_kline", spec, {}) == {}
    assert _validate_and_normalize("moo_kline", spec, {"frequency": ""}) == {}


def test_validate_enum_in_bounds_passes():
    """enum 在范围内, 必填 + optional 都通过。"""
    spec = [
        {"name": "block_type", "type": "str", "required": True,
         "enum": ["industry", "concept", "region"]},
        {"name": "count", "type": "int", "required": False},
    ]
    result = _validate_and_normalize("moo_block_data", spec,
                                     {"block_type": "industry", "count": 10})
    assert result == {"block_type": "industry", "count": 10}


# ========== build_tool_function 端到端 ==========

def test_build_tool_function_exposes_enum_and_description_in_signature():
    """build_tool_function 生成的函数必须有正确 signature 暴露给 FastMCP."""
    spec = {
        "name": "moo_block_data",
        "description": "板块数据",
        "routing": "moo_block_data",
        "params": [
            {"name": "block_type", "type": "str", "required": True,
             "enum": ["industry", "concept", "region"],
             "description": "板块类型"},
        ],
    }
    fn = build_tool_function(spec, server=None)
    sig = fn.__signature__
    p = sig.parameters["block_type"]
    # 拆 Annotated 包装, 找到内层的 Literal
    from typing import Annotated, Literal, get_args, get_origin
    ann = p.annotation
    if get_origin(ann) is Annotated:
        inner = get_args(ann)[0]
    else:
        inner = ann
    assert get_origin(inner) is Literal
    assert get_args(inner) == ("industry", "concept", "region")


def test_build_tool_function_rejects_invalid_enum_at_call_time():
    """调用 build_tool_function 生成的工具时，enum 越界必须抛 ValueError。"""
    spec = {
        "name": "moo_block_data",
        "description": "板块数据",
        "routing": "moo_block_data",
        "params": [
            {"name": "block_type", "type": "str", "required": True,
             "enum": ["industry", "concept", "region"]},
        ],
    }
    fn = build_tool_function(spec, server=None)
    with pytest.raises(ValueError, match="invalid value for 'block_type'"):
        asyncio.run(fn(block_type="area"))
