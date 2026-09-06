"""tests/test_registry_tdx_quant.py — registry 加载 tdx_quant yaml 验证。"""
import typing
from pathlib import Path

import yaml

from src.registry import _TYPE_MAP, build_signature, _validate_and_normalize


def test_type_map_includes_list_str():
    assert "List[str]" in _TYPE_MAP
    assert _TYPE_MAP["List[str]"] is list


def test_loads_tdx_quant_yaml_without_error():
    yaml_path = Path("config/tools_tdx_quant.yaml")
    if not yaml_path.exists():
        import pytest
        pytest.skip("tools_tdx_quant.yaml not generated yet")
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    specs = data["tools"]
    assert len(specs) >= 50
    for s in specs:
        assert "name" in s
        assert "routing" in s
        assert "upstream_tool_mapping" in s
        assert s["upstream_tool_mapping"] == {"tdx_quant": s["name"]}


def test_list_str_param_builds_valid_signature():
    spec = [{
        "name": "stock_list",
        "required": True,
        "type": "List[str]",
        "description": "股票代码列表",
    }]
    sig = build_signature(spec)
    p = sig.parameters["stock_list"]
    # _annotation_for wraps with Annotated[list, Field(description=...)]
    # when description present; underlying type must be list
    args = typing.get_args(p.annotation)
    assert args, f"expected Annotated[list, ...], got {p.annotation!r}"
    assert args[0] is list


def test_list_str_param_normalizes_csv_to_list():
    param_specs = [{
        "name": "stock_list",
        "required": True,
        "type": "List[str]",
        "description": "股票代码列表",
    }]
    normalized = _validate_and_normalize(
        "test_tool",
        param_specs,
        {"stock_list": "600519.SH,000001.SZ"},
    )
    assert normalized["stock_list"] == ["600519.SH", "000001.SZ"]


def test_dangerous_tools_present_in_yaml():
    yaml_path = Path("config/tools_tdx_quant.yaml")
    if not yaml_path.exists():
        import pytest
        pytest.skip("tools_tdx_quant.yaml not generated yet")
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    dangerous_names = [s["name"] for s in data["tools"] if s.get("dangerous")]
    assert "order_stock" in dangerous_names
    assert "send_message" in dangerous_names
    assert "create_sector" in dangerous_names
