"""tests/test_gen_tdx_quant_tools.py — codegen 脚本测试。"""
import yaml

from scripts.gen_tdx_quant_tools import (
    parse_skill_md,
    infer_dangerous,
    infer_cache_ttl_key,
    build_tool_specs,
    render_yaml,
)


SAMPLE_SKILL = """# TdxQuant Skill Sample

> sample

## 二、行情数据接口

### 2.1 获取K线行情 `get_market_data`

获取 K 线和历史行情数据。

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| stock_list | Y | List[str] | 股票代码列表 |
| period | Y | str | K线周期，如 `1m`/`5m`/`1d` |
| count | N | int | 取最近 n 条 |
| dividend_type | N | str | 复权：`none`/`front`/`back`

**返回字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Close | str | 收盘价 |

### 2.2 获取实时行情快照 `get_market_snapshot`

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| stock_code | Y | str | 证券代码 |
| field_list | N | List[str] | 指定字段 |

### 4.4 自定义板块管理

#### 创建板块 `create_sector`

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| sector_name | Y | str | 板块名 |

#### 下单 `order_stock`

下单。

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| account_id | Y | str | 账户句柄 |
| code | Y | str | 证券代码 |
"""


def test_parse_skill_md_extracts_methods():
    methods = parse_skill_md(SAMPLE_SKILL)
    names = [m["name"] for m in methods]
    assert "get_market_data" in names
    assert "get_market_snapshot" in names
    assert "create_sector" in names
    assert "order_stock" in names


def test_parse_skill_md_extracts_params():
    methods = parse_skill_md(SAMPLE_SKILL)
    gmd = next(m for m in methods if m["name"] == "get_market_data")
    param_names = [p["name"] for p in gmd["params"]]
    assert "stock_list" in param_names
    assert "period" in param_names
    assert "count" in param_names
    assert "Close" not in param_names


def test_infer_dangerous_for_write_ops():
    assert infer_dangerous("order_stock") is True
    assert infer_dangerous("cancel_order_stock") is True
    assert infer_dangerous("create_sector") is True
    assert infer_dangerous("delete_sector") is True
    assert infer_dangerous("send_message") is True
    assert infer_dangerous("refresh_cache") is True
    assert infer_dangerous("formula_zb") is True


def test_infer_dangerous_for_read_ops():
    assert infer_dangerous("get_market_data") is False
    assert infer_dangerous("get_market_snapshot") is False
    assert infer_dangerous("get_user_sector") is False


def test_infer_cache_ttl_key_realtime():
    assert infer_cache_ttl_key("get_market_snapshot") == "realtime_quote"
    assert infer_cache_ttl_key("get_more_info") == "realtime_quote"
    assert infer_cache_ttl_key("get_gp_one_data") == "realtime_quote"


def test_infer_cache_ttl_key_historical():
    assert infer_cache_ttl_key("get_market_data") == "historical"
    assert infer_cache_ttl_key("get_divid_factors") == "historical"


def test_infer_cache_ttl_key_fundamentals():
    assert infer_cache_ttl_key("get_financial_data") == "fundamentals"
    assert infer_cache_ttl_key("get_stock_info") == "fundamentals"
    assert infer_cache_ttl_key("get_gpjy_value_by_date") == "fundamentals"


def test_infer_cache_ttl_key_ticker_list():
    assert infer_cache_ttl_key("get_stock_list") == "ticker_list"
    assert infer_cache_ttl_key("get_user_sector") == "ticker_list"


def test_infer_cache_ttl_key_workday():
    assert infer_cache_ttl_key("get_trading_dates") == "workday"
    assert infer_cache_ttl_key("get_trading_calendar") == "workday"


def test_infer_cache_ttl_key_null_for_writes():
    assert infer_cache_ttl_key("order_stock") is None
    assert infer_cache_ttl_key("send_message") is None
    assert infer_cache_ttl_key("subscribe_hq") is None
    assert infer_cache_ttl_key("formula_zb") is None


def test_build_tool_specs_uses_tdx_quant_upstream():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    for spec in specs:
        assert spec["upstream_tool_mapping"] == {"tdx_quant": spec["name"]}


def test_render_yaml_includes_do_not_edit_header():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    out = render_yaml(specs)
    assert "DO NOT EDIT" in out
    assert "gen_tdx_quant_tools.py" in out
    parsed = yaml.safe_load(out)
    assert "tools" in parsed
    assert len(parsed["tools"]) == len(specs)


def test_codegen_roundtrip_idempotent():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs1 = build_tool_specs(methods)
    specs2 = build_tool_specs(methods)
    assert specs1 == specs2
