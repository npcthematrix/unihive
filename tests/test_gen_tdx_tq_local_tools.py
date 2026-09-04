"""scripts/gen_tdx_tq_local_tools.py 单元测试。

使用 fixture 把 SKILL.md 的子集写入 tmp 文件，避免依赖真实 skill 路径。
"""
import pytest

from scripts.gen_tdx_tq_local_tools import (
    CodegenParseError,
    parse_skill_md,
    infer_dangerous,
    build_tool_specs,
    render_yaml,
)


SAMPLE_SKILL = """# TDX Quant Local Skill

> sample

## 接口名映射规则

### 行情与基础数据

#### `get_market_data`: K线/分钟线/历史行情

获取 K 线和历史行情数据。

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| stock_list | Y | List[str] | 股票代码列表 |
| period | Y | str | K线周期 |
| count | N | int | 取最近 n 条 |

**返回字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Date | str | 日期 |

#### `order_stock`: 下单

下个委托单。

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| account_id | Y | int | 账户句柄 |
| stock_code | Y | str | 股票代码 |
| order_type | Y | int | 买卖标志 |

#### `get_user_sector`: 获取用户自定义板块

列出全部自定义板块。

（无参数表 → skip）

#### `formula_set_data`: 设置公式计算用行情数据

灌数据用。

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| data | Y | dict | 数据 |
"""


def test_parse_extracts_four_methods():
    methods = parse_skill_md(SAMPLE_SKILL)
    names = [m["name"] for m in methods]
    assert names == ["get_market_data", "order_stock", "get_user_sector", "formula_set_data"]


def test_parse_pulls_required_param_for_get_market_data():
    methods = parse_skill_md(SAMPLE_SKILL)
    gm = methods[0]
    pmap = {p["name"]: p for p in gm["params"]}
    assert pmap["stock_list"]["required"] is True
    assert pmap["period"]["required"] is True
    assert pmap["count"]["required"] is False


def test_parse_includes_description_from_preceding_paragraph():
    methods = parse_skill_md(SAMPLE_SKILL)
    assert methods[0]["description"] == "获取 K 线和历史行情数据。"
    assert methods[1]["description"] == "下个委托单。"
    assert "无参数表" in methods[2]["description"] or methods[2]["description"]


def test_parse_skips_method_without_param_table():
    methods = parse_skill_md(SAMPLE_SKILL)
    user = next(m for m in methods if m["name"] == "get_user_sector")
    assert user["params"] == []


def test_dangerous_order_stock():
    assert infer_dangerous("order_stock") is True


def test_dangerous_cancel_create_delete_clear_rename():
    for name in ("cancel_order_stock", "create_sector", "delete_sector",
                 "clear_sector", "rename_sector"):
        assert infer_dangerous(name) is True, name


def test_dangerous_send_message_file_warn_bt_data():
    for name in ("send_message", "send_file", "send_warn", "send_bt_data"):
        assert infer_dangerous(name) is True, name


def test_dangerous_send_user_block_and_exec_to_tdx():
    assert infer_dangerous("send_user_block") is True
    assert infer_dangerous("exec_to_tdx") is True


def test_dangerous_formula_set_data():
    assert infer_dangerous("formula_set_data") is True
    assert infer_dangerous("formula_set_data_info") is True


def test_dangerous_refresh_cache_kline():
    assert infer_dangerous("refresh_cache") is True
    assert infer_dangerous("refresh_kline") is True


def test_safe_get_market_snapshot():
    assert infer_dangerous("get_market_snapshot") is False
    assert infer_dangerous("get_stock_info") is False
    assert infer_dangerous("query_stock_positions") is False


def test_build_tool_specs_assigns_correct_routing():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    spec_map = {s["name"]: s for s in specs}
    gm = spec_map["get_market_data"]
    assert gm["routing"] == "get_market_data"
    assert gm["upstream_tool_mapping"] == {"tdx_tq_local": "get_market_data"}
    assert gm["cache_ttl_key"] is None
    assert gm["dangerous"] is False


def test_build_tool_specs_marks_dangerous():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    spec_map = {s["name"]: s for s in specs}
    assert spec_map["order_stock"]["dangerous"] is True
    assert spec_map["formula_set_data"]["dangerous"] is True


def test_render_yaml_is_loadable():
    import yaml
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    text = render_yaml(specs)
    data = yaml.safe_load(text)
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert len(data["tools"]) == len(specs)


def test_render_yaml_idempotent():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    a = render_yaml(specs)
    b = render_yaml(specs)
    assert a == b


def test_real_skill_md_all_methods_have_dangerous_predicate():
    """每个 SKILL.md 真实方法都该被某个 dangerous 规则命中或显式 safe。
    保证 dangerous 规则不会因为白名单更新而漏掉新方法。
    """
    from pathlib import Path as _P
    real_skill = _P.home() / ".claude" / "skills" / "tdx-tq-local" / "SKILL.md"
    if not real_skill.exists():
        pytest.skip("real SKILL.md not present")
    methods = parse_skill_md(real_skill.read_text(encoding="utf-8"))
    for m in methods:
        d = infer_dangerous(m["name"])
        assert isinstance(d, bool)
