# TokenWave TDX 暴露修复 + 控制台数据更新 — Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉 tokenwave_tdx 上游连接但 8 个工具实际未被 gateway 暴露的架构缺口，并同步更新管理控制台 MCP APIs 标签页的分组与描述以反映最新上游/工具现状。

**Architecture:**
- 改 `config/upstreams.yaml`：补 3 个缺失 tool 入口 + 调整 2 个 routing 链 + 更新误导性描述
- 改 `console.html`：补 `tokenwave_tdx` 到 `GROUP_ORDER` / `GROUP_META`，重新校对 8 个分组的描述文案

**Tech Stack:** PyYAML, vanilla JS in `console.html`, pytest.

---

## Problem

经过三轮迭代（rhths→fuyao 重命名 / TQ-Local codegen 集成 / TokenWave TDX 新增），控制台与上游现状出现了多处不同步：

### 1. 架构缺口：tokenwave_tdx 上游已连接但 8 个接口不通过 gateway 暴露

经排查 (`src/gateway_server.py:169-194`, `src/router.py:54-92`, `src/registry.py:95-105`, `src/tool_loader.py:11-38`)，tokenwave_tdx 的 8 个 tool 当前状态：

| Gateway tool 名 | 在 `tools:` list? | 在 routing chain? | upstream_tool_mapping | 实际可被外部调用? | 实际命中哪个上游? |
|---|---|---|---|---|---|
| `get_realtime_quote` | ❌ | ✅ `[tdx_local, fuyao_ashare]` | `{tokenwave_tdx}` | ✅ | ❌ **绕过 tokenwave_tdx** |
| `get_kline` | ✅ | ❌ | `{tdx_local}` 在 line 141, `{tokenwave_tdx}` 在 line 246 (后写赢) | ✅ | ✅ tokenwave_tdx |
| `get_minute_bar` | ❌ | ✅ `[tdx_local]` | `{tokenwave_tdx}` | ✅ | ❌ **绕过 tokenwave_tdx** |
| `get_financial_data` | ❌ | ❌ | `{tokenwave_tdx}` | ❌ **不暴露** | — |
| `get_block_data` | ❌ | ❌ | `{tokenwave_tdx}` | ❌ **不暴露** | — |
| `get_stock_info` | ❌ (TQ-Local 有同名) | ❌ | `{tokenwave_tdx}` | ✅ (TQ-Local codegen 注册) | ✅ tokenwave_tdx |
| `get_trade_dates` | ❌ | ❌ | `{tokenwave_tdx}` | ❌ **不暴露** | — |
| `get_etf_list` | ✅ | ❌ | `{tdx_local}` line 156, `{tokenwave_tdx}` line 252 (后写赢) | ✅ | ✅ tokenwave_tdx |

**结论**：8 个 tokenwave_tdx tool 中，3 个未暴露 (get_financial_data / get_block_data / get_trade_dates)，2 个被 routing chain 绕过 (get_realtime_quote / get_minute_bar)。与最初设计意图 "8 个工具全部可用" 不一致。

### 2. 控制台数据不同步

`console.html:1004-1013` 的 `GROUP_ORDER` / `GROUP_META` 是写死的 JS 常量，自 2026-09-04 rhths→fuyao 重命名后未再更新：

- 缺少 `tokenwave_tdx` 分组（status 标签页会显示该上游，但 MCP APIs 侧栏不会）
- `tdx_tq_local` 描述 "含 20 个高风险工具 (write ops)" 已过时（实测 17 个）
- 接入指南标签页的 MCP APIs 跳转列表同样使用 `GROUP_ORDER`（line 1705），需同步更新

`status` 标签页 (API 动态读取) 和 `config` 标签页 (API 完整返回 YAML) 已自动跟上，**不需要改动**。

---

## Solution

### A. 修 tokenwave_tdx 架构缺口 (`config/upstreams.yaml`)

#### A.1 在 `tools:` 列表补 3 个缺失 tool 入口

在 `# === Tushare (3) ===` 之前新增一个分组：

```yaml
  # === TokenWave TDX (8) ===
  - {name: get_financial_data, description: "财务数据 (tokenwave_tdx, network only — 本地不支持)", routing: get_financial_data, params: [{name: stock_code, type: str, required: true, normalize: code}, {name: report_type, type: str, required: false}, {name: count, type: int, required: false}], cache_ttl_key: fundamentals}
  - {name: get_block_data, description: "板块/概念数据 (tokenwave_tdx, network only)", routing: get_block_data, params: [{name: block_type, type: str, required: true}], cache_ttl_key: sector}
  - {name: get_trade_dates, description: "交易日历 (tokenwave_tdx, local 优先)", routing: get_trade_dates, params: [{name: start_date, type: str, required: true}, {name: end_date, type: str, required: true}], cache_ttl_key: workday}
```

cache_ttl_key 取值说明：
- `get_financial_data` → `fundamentals` (已有, 3600s)
- `get_block_data` → `sector` (新增) — 30s, 加到 `cache.ttl` 区块
- `get_trade_dates` → `workday` (已有, 86400s)

#### A.2 调整 2 个 routing chain，让 tokenwave_tdx 优先

```yaml
routing:
  get_realtime_quote:
    chain: ["tokenwave_tdx", "tdx_local", "fuyao_ashare"]  # 加 tokenwave_tdx 优先
    description: "实时行情 (tokenwave_tdx local 优先)"
  get_daily_bar:
    chain: ["tokenwave_tdx", "tdx_local", "fuyao_ashare"]  # 加 tokenwave_tdx 优先
    description: "日K线行情 (tokenwave_tdx local 优先)"
  get_minute_bar:
    chain: ["tokenwave_tdx", "tdx_local"]  # 加 tokenwave_tdx 优先
    description: "分钟K线行情 (tokenwave_tdx local 优先)"
```

#### A.3 更新 3 个 tool 描述 (避免说"TDX"但实际走 tokenwave_tdx)

```yaml
  - {name: get_kline, description: "K线 (tokenwave_tdx 优先 + tdx_local 兜底, day/week/month/minute1/5/15/30/60)", routing: get_kline, params: [...], cache_ttl_key: daily_bar}
  - {name: get_etf_list, description: "ETF 列表 (tokenwave_tdx 优先 + tdx_local 兜底)", routing: get_etf_list, params: [...], cache_ttl_key: etf_list}
  - {name: get_stock_info, description: "单只股票信息 (tokenwave_tdx; TQ-Local 同名工具同名入口)", routing: get_stock_info, params: [...]}
```

**注意**：`get_stock_info` 在 `tools_tdx_tq_local.yaml` (codegen) 也有同名条目，描述里说明是 TQ-Local 注册入口但实际走 tokenwave_tdx。

### B. 更新控制台数据 (`console.html`)

#### B.1 修改 `GROUP_ORDER` 和 `GROUP_META` (line 1004-1013)

**当前 7 个分组 + 加 1 个 tokenwave_tdx = 8 个分组**。

新的 GROUP_ORDER（按"上游类型 → 字母序"排列, tokenwave_tdx 紧跟 tdx_local）：

```js
const GROUP_ORDER = ['tdx_local', 'tdx_tq_local', 'tokenwave_tdx', 'fuyao_ashare', 'fuyao_index', 'fuyao_meta', 'fuyao_fund', 'tushare'];

const GROUP_META = {
    'tdx_local':       { title: '通达信-行情',         desc: '行情/K线/分时/ETF/指数, 含 1 个高风险工具 (tdx_call)' },
    'tdx_tq_local':    { title: '通达信-交易终端',     desc: '行情/自选/交易/公式系统 (本地 HTTP JSON-RPC, 端口 17709), 含 17 个高风险工具 (write ops)' },
    'tokenwave_tdx':   { title: '通达信-MooTDX',       desc: '基于 mootdx 的行情/K线/财务/板块, 8 个接口, local 优先 + network 兜底' },
    'fuyao_ashare':    { title: '同花顺-a-share-mcp',  desc: 'A 股行情/财报/估值/特殊数据, 端点: /mcp/a-share' },
    'fuyao_index':     { title: '同花顺-a-share-index-mcp', desc: '同花顺指数/板块目录/成分股/历史K线, 端点: /mcp/a-share-index' },
    'fuyao_meta':      { title: '同花顺-meta-mcp',     desc: '跨宇宙全量 ticker 列表, 端点: /mcp/meta' },
    'fuyao_fund':      { title: '同花顺-fund-mcp',     desc: '公募基金持仓/业绩/经理/财务, 端点: /mcp/fund' },
    'tushare':         { title: 'Tushare',             desc: '通用 API 透传 (sdk_call / sdk_schema / sdk_search)' },
};
```

**校对说明**：
- `tdx_tq_local` "含 20 个" → "含 17 个"（实测 grep `^  dangerous: true` config/tools_tdx_tq_local.yaml = 17）
- `tushare` 描述 "通用 API 透传" → 列出具体 3 个工具名 (sdk_call / sdk_schema / sdk_search)
- 新增 `tokenwave_tdx` 分组

#### B.2 接入指南标签页 (line 1705-1710)

自动跟随 — 因为 `renderOnboardingInterfaces()` 使用 `GROUP_ORDER`。无需单独改动，但需要在 spec 自审时确认渲染正确。

#### B.3 配置信息 / 上游状态 标签页

无需改动 — `/api/status` 和 `/api/config` 动态读取 YAML，tokenwave_tdx 已自动出现。

### C. 验证

#### C.1 pytest 验证（不回归）

```bash
pytest -q
# 期望: 136 passed (现有基线)
```

#### C.2 新增回归测试

**`tests/test_tokenwave_tdx_exposure.py`** — 验证 8 个 tokenwave_tdx tool 在 `tools/list` 中可见：

```python
"""Regression: tokenwave_tdx 的 8 个工具都在 gateway tools/list 中暴露。

之前 task 完成了 tokenwave_tdx 客户端 + upstream 接入，但 8 个 tool 没有
被加到 tools: 列表, 3 个完全没暴露, 2 个被 routing chain 绕过。本测试
锁住"全暴露"这个新约束。
"""
from src.tool_loader import load_all_tools
from pathlib import Path

TOKENWAVE_TOOLS = {
    "get_realtime_quote",
    "get_kline",
    "get_minute_bar",
    "get_financial_data",
    "get_block_data",
    "get_stock_info",
    "get_trade_dates",
    "get_etf_list",
}


def test_all_tokenwave_tools_in_tools_list():
    config_path = Path("config/upstreams.yaml")
    import yaml
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    tools = load_all_tools(config_path, cfg)
    names = {t["name"] for t in tools}
    missing = TOKENWAVE_TOOLS - names
    assert missing == set(), f"tokenwave_tdx tools missing from tools: list: {missing}"


def test_realtime_quote_chain_starts_with_tokenwave():
    """get_realtime_quote 的 routing chain 必须以 tokenwave_tdx 开头。"""
    import yaml
    with open("config/upstreams.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    chain = cfg["routing"]["get_realtime_quote"]["chain"]
    assert chain[0] == "tokenwave_tdx", f"get_realtime_quote chain should start with tokenwave_tdx, got {chain}"


def test_minute_bar_chain_starts_with_tokenwave():
    """get_minute_bar 的 routing chain 必须以 tokenwave_tdx 开头。"""
    import yaml
    with open("config/upstreams.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    chain = cfg["routing"]["get_minute_bar"]["chain"]
    assert chain[0] == "tokenwave_tdx", f"get_minute_bar chain should start with tokenwave_tdx, got {chain}"
```

#### C.3 手动 smoke

启动 gateway + console：
```bash
.\scripts\start_all.ps1
```

打开 http://127.0.0.1:18080/console.html, 检查：

1. **上游状态** 标签页 → 应有 8 行 upstream (含 tokenwave_tdx, 状态 "configured")
2. **MCP APIs** 标签页 → 侧栏 8 个分组 (含 "通达信-MooTDX"), 点开看到 8 个 tokenwave_tdx tool
3. **配置信息** → 上游子标签页 → 应有 tokenwave_tdx 行, type=python
4. **接入指南** → MCP APIs 跳转列表 → 包含 "通达信-MooTDX"

Curl 检查 gateway `tools/list`：
```bash
curl -X POST http://127.0.0.1:18081/mcp \
  -H "Authorization: Bearer $GATEWAY_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | jq '.result.tools[].name' | grep -E "get_financial_data|get_block_data|get_trade_dates"
```

期望：3 个新增 tool 都在返回中。

---

## Files affected

- **Modify**: `config/upstreams.yaml` (A.1 / A.2 / A.3)
- **Modify**: `console.html` (B.1 — `GROUP_ORDER` / `GROUP_META`)
- **New**: `tests/test_tokenwave_tdx_exposure.py`

## Backwards compatibility

- gateway tool 列表新增 3 个：`get_financial_data` / `get_block_data` / `get_trade_dates` (有就暴露，没有就不暴露 — 对调用方是新增能力)
- 已有 tool (`get_realtime_quote` / `get_minute_bar` / `get_kline` / `get_stock_info` / `get_etf_list`) 描述更新
- `get_realtime_quote` / `get_minute_bar` 的 routing chain 顺序变化，tokenwave_tdx 优先 — 行为变化但语义保持 (仍可调用其他上游做兜底)
- 控制台侧栏新增分组

## Out of scope

- tokenwave_tdx 客户端本身的实现 (已完成)
- 重新生成 tools_tdx_tq_local.yaml
- 控制台 status 标签页 (自动)
- 控制台 config 标签页 (自动)

## Acceptance criteria

- [ ] `pytest -q` 136+ passed
- [ ] 3 个新增 tool 在 `tools/list` 中可见
- [ ] 5 个已有 tokenwave_tdx tool 实际命中 tokenwave_tdx (路由生效)
- [ ] console.html 侧栏 8 个分组，含 tokenwave_tdx
- [ ] GROUP_META 描述与现状一致 (TQ-Local 高风险数 17, tushare 列出 3 个 sdk 工具名)
- [ ] 接入指南标签页 MCP APIs 跳转列表包含 tokenwave_tdx