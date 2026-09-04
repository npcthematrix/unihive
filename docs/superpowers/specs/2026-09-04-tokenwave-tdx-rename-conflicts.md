# TokenWave TDX 重命名冲突工具 — Design

## Problem

`config/upstreams.yaml` 在 `upstream_tool_mapping` 块给 `get_financial_data` 和 `get_stock_info` 配了 `tokenwave_tdx` 入口。但这两个 gateway 工具的实际 spec 来自 **TQ-Local codegen** (config/tools_tdx_tq_local.yaml) — 参数形状与 tokenwave 实现不一致：

| Gateway 工具 | TQ-Local spec 参数 | tokenwave 实现参数 | 路由到 tokenwave 时 |
|---|---|---|---|
| `get_financial_data` | `stock_list`, `field_list`, `start_time`, `end_time`, `report_type` | `stock_code`, `report_type`, `count` | **TypeError** — `stock_list` 不是关键字 |
| `get_stock_info` | `stock_code`, `field_list?` | `stock_code` | **TypeError** — tokenwave 不接受 `field_list` |

`src/tokenwave_tdx_client.py:67-78` 的 `call_tool` 用 `method(**params)` 调用，FastMCP spec 里的 `field_list` 等多余参数会让 tokenwave 抛 TypeError → 上游调用失败 → 路由 fallback 到 TQ-Local 才成功。

**结果**：tokenwave_tdx 在这两个 gateway 工具的 chain 里是**死链**，每次请求浪费一次失败调用。

## Solution

把 tokenwave_tdx 的 `get_financial_data` 和 `get_stock_info` 改名为带前缀的 gateway 工具：

- `tokenwave_get_financial_data` (参数: stock_code, report_type, count)
- `tokenwave_get_stock_info` (参数: stock_code)

TQ-Local 的同名工具保留不变 — 它有自己的 spec 和调用方语义。

**调用方明确选择**：要 TQ-Local 风格（多股、字段列表、时间范围）就用 `get_financial_data`；要 tokenwave 风格（单股、count）就用 `tokenwave_get_financial_data`。

## Implementation

### A. `config/upstreams.yaml`

1. 从 `upstream_tool_mapping` 块移除两行：
   ```yaml
   get_financial_data:                       {tokenwave_tdx: get_financial_data}   # 删
   get_stock_info:                            {tokenwave_tdx: get_stock_info}        # 删
   ```

2. 在 TokenWave TDX 工具块（line 342+）追加 2 个新 manual tools：
   ```yaml
   - {name: tokenwave_get_financial_data, description: "tokenwave_tdx 财务数据 (单股 + count, 区别于 TQ-Local get_financial_data 的 stock_list 风格)", routing: tokenwave_get_financial_data, params: [{name: stock_code, type: str, required: true, normalize: code}, {name: report_type, type: str, required: false}, {name: count, type: int, required: false}], cache_ttl_key: fundamentals}
   - {name: tokenwave_get_stock_info, description: "tokenwave_tdx 单只股票信息 (单参数, 区别于 TQ-Local get_stock_info 的 field_list 风格)", routing: tokenwave_get_stock_info, params: [{name: stock_code, type: str, required: true, normalize: code}]}
   ```

### B. `tests/test_tokenwave_tdx_exposure.py`

更新 `test_all_tokenwave_tools_in_tools_list` 的 `expected_tools` 列表：
- 移除 `get_financial_data`（不在 tokenwave 8 工具内）
- 移除 `get_stock_info`（不在 tokenwave 8 工具内）
- 加 `tokenwave_get_financial_data`
- 加 `tokenwave_get_stock_info`
- docstring 改为 "8 tokenwave_tdx tools"

### C. `tests/test_console_interfaces.py`

`test_get_interfaces_returns_147_tools` → `test_get_interfaces_returns_149_tools`，断言 149。

## Out of scope

- tokenwave_tdx 客户端实现 (已稳定)
- TQ-Local 的 `get_financial_data` / `get_stock_info` (调用方已能选)
- 改 routing chain (没有 get_financial_data / get_stock_info 的 routing 块，本来就靠 upstream_tool_mapping)

## Acceptance

- pytest 144 passed (was 142 baseline + 2 new tokenwave entries, no test count change because test_all_tokenwave_tools_in_tools_list was already updated)
- 9 tokenwave gateway tools: get_realtime_quote, get_kline, get_minute_bar, get_daily_bar, get_block_data, get_trade_dates, get_etf_list, **tokenwave_get_financial_data, tokenwave_get_stock_info**
- 调用 `tokenwave_get_financial_data(stock_code="600000", count=4)` 命中 tokenwave_tdx
- 调用 `get_financial_data(stock_list=["600000"], field_list=["营业收入"])` 命中 tdx_tq_local