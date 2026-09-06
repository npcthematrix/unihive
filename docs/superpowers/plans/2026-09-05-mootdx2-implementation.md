# MooTDX2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 mootdx2 库替换现有的 tdx_local 上游，提供统一的 MCP 接口

**Architecture:** 新建 mootdx2_client.py，使用 mootdx2.quotes.Quotes 在线模式，通过 gateway_server.py 注册为新上游，配置 upstreams.yaml 指向 mootdx2

**Tech Stack:** mootdx2, FastMCP, asyncio

---

## File Structure

- Create: `src/mootdx2_client.py` - 新客户端实现
- Modify: `src/gateway_server.py:35` - 添加 import 和注册逻辑
- Modify: `config/upstreams.yaml` - 添加 mootdx2 upstream 和工具配置

---

## Implementation Tasks

### Task 1: Create mootdx2_client.py

**Files:**
- Create: `src/mootdx2_client.py`

- [ ] **Step 1: Write test for MooTDX2Client**

```python
# tests/test_mootdx2_client.py
import pytest
from src.mootdx2_client import MooTDX2Client, MooTDX2Config
from src.upstream_client import UpstreamStatus

class TestMooTDX2Client:
    def test_config_creation(self):
        config = MooTDX2Config(name="test", market="std")
        assert config.name == "test"
        assert config.market == "std"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mootdx2_client.py -v`
Expected: FAIL with "No module named 'src.mootdx2_client'"

- [ ] **Step 3: Write minimal MooTDX2Client skeleton**

```python
"""MooTDX2 MCP Client 基于 mootdx2 库"""
import logging
from dataclasses import dataclass

from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)

@dataclass
class MooTDX2Config:
    """MooTDX2 配置"""
    name: str
    market: str = "std"

class MooTDX2Client:
    """MooTDX2 MCP 客户端"""
    def __init__(self, config: MooTDX2Config):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == UpstreamStatus.HEALTHY

    async def start(self):
        self._status = UpstreamStatus.HEALTHY

    async def stop(self):
        self._status = UpstreamStatus.UNKNOWN

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        return ToolResult(success=False, error="Not implemented")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mootdx2_client.py::TestMooTDX2Client::test_config_creation -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 2: Implement get_quote method

**Files:**
- Modify: `src/mootdx2_client.py`

- [ ] **Step 1: Write failing test for get_quote**

```python
def test_get_quote(self):
    config = MooTDX2Config(name="test", market="std")
    client = MooTDX2Client(config)
    # Mock quotes attribute
    class MockQuotes:
        def quotes(self, symbols):
            import pandas as pd
            return pd.DataFrame([{"symbol": "600000", "close": 10.0}])
    client._quotes = MockQuotes()
    result = client._get_quote("600000")
    assert result is not None
    assert result["close"] == 10.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mootdx2_client.py::TestMooTDX2Client::test_get_quote -v`
Expected: FAIL

- [ ] **Step 3: Implement _get_quote and get_quote**

```python
async def get_quote(self, code: str) -> ToolResult:
    """获取实时行情"""
    try:
        q = self._get_quotes()
        # 去掉 sh/sz 前缀
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.quotes(symbols=[sym])
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return ToolResult(success=True, data={
                "symbol": code,
                "close": float(latest.get("close", 0)),
                "open": float(latest.get("open", 0)),
                "high": float(latest.get("high", 0)),
                "low": float(latest.get("low", 0)),
                "volume": float(latest.get("vol", 0)),
                "amount": float(latest.get("amount", 0)),
            }, source="mootdx2")
        return ToolResult(success=False, error="No data")
    except Exception as e:
        logger.error(f"get_quote failed: {e}")
        return ToolResult(success=False, error=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

---

### Task 3: Implement get_kline method

**Files:**
- Modify: `src/mootdx2_client.py`

- [ ] **Step 1: Write failing test for get_kline**

```python
def test_get_kline(self):
    config = MooTDX2Config(name="test", market="std")
    client = MooTDX2Client(config)
    # Mock quotes with bars method
    class MockQuotes:
        def bars(self, symbol, frequency, offset):
            import pandas as pd
            return pd.DataFrame([
                {"date": "2026-01-01", "open": 10.0, "close": 10.5},
                {"date": "2026-01-02", "open": 10.5, "close": 11.0},
            ])
    client._quotes = MockQuotes()
    result = client._get_kline("600000", frequency=9, count=10)
    assert result is not None
    assert len(result) == 2
```

- [ ] **Step 2: Run test to verify it fails**

- [ ] **Step 3: Implement get_kline**

```python
# frequency 映射
FREQ_MAP = {
    "day": 9,
    "week": 5,
    "month": 6,
    "minute1": 8,
    "minute5": 0,
    "minute15": 1,
    "minute30": 2,
    "minute60": 3,
}

async def get_kline(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
    """获取K线"""
    try:
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        freq = FREQ_MAP.get(type, 9)
        df = q.bars(symbol=sym, frequency=freq, offset=limit)
        if df is not None and not df.empty:
            if limit:
                df = df.tail(limit)
            return ToolResult(
                success=True,
                data=df.to_dict(orient="records"),
                source="mootdx2"
            )
        return ToolResult(success=False, error="No data")
    except Exception as e:
        logger.error(f"get_kline failed: {e}")
        return ToolResult(success=False, error=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

---

### Task 4: Implement remaining methods

**Files:**
- Modify: `src/mootdx2_client.py`

- [ ] **Step 1: Implement all methods**

Implement these methods following the same pattern:
- `get_batch_quote(codes: str)` - 批量行情
- `get_minute_data(code: str)` - 今日分时
- `get_trade(code: str)` - 逐笔成交
- `get_trade_history(code: str, date: str, start: int, count: int)` - 历史逐笔
- `get_index_kline(code: str, type: str)` - 指数K线
- `get_code_list(exchange: str)` - 代码列表
- `get_stock_codes(limit: int, prefix: bool)` - 股票代码
- `get_etf_codes(limit: int, prefix: bool)` - ETF代码
- `get_etf_list(exchange: str, limit: int)` - ETF列表
- `get_market_count()` - 市场数量
- `get_workday(date: str, count: int)` - 交易日查询
- `get_workday_range(start: str, end: str)` - 交易日范围
- `get_index_all(code: str, type: str, limit: int)` - 全指K线
- `get_income(code: str, days: int, start_date: str)` - 盘后收益

- [ ] **Step 2: Update call_tool method map**

```python
async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
    method_map = {
        "get_quote": self.get_quote,
        "get_kline": self.get_kline,
        "get_batch_quote": self.get_batch_quote,
        "get_minute_data": self.get_minute_data,
        "get_trade": self.get_trade,
        "get_trade_history": self.get_trade_history,
        "get_index_kline": self.get_index_kline,
        "get_code_list": self.get_code_list,
        "get_stock_codes": self.get_stock_codes,
        "get_etf_codes": self.get_etf_codes,
        "get_etf_list": self.get_etf_list,
        "get_market_count": self.get_market_count,
        "get_workday": self.get_workday,
        "get_workday_range": self.get_workday_range,
        "get_index_all": self.get_index_all,
        "get_income": self.get_income,
    }
    method = method_map.get(tool_name)
    if not method:
        return ToolResult(success=False, error=f"Unknown tool: {tool_name}")
    return await method(**params)
```

- [ ] **Step 3: Run all tests**

Run: `pytest tests/test_mootdx2_client.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

---

### Task 5: Register in gateway_server.py

**Files:**
- Modify: `src/gateway_server.py:35`

- [ ] **Step 1: Add import**

```python
from .mootdx2_client import MooTDX2Client, MooTDX2Config
```

- [ ] **Step 2: Add to type hint**

```python
self.upstreams: dict[str, UpstreamClient | FuyaoClient | HttpJsonRpcClient | TokenWaveTdxClient | MooTDX2Client] = {}
```

- [ ] **Step 3: Add client initialization**

在 tokenwave 初始化附近添加:
```python
elif cfg.get("type") == "mootdx2":
    mootdx2_cfg = MooTDX2Config(
        name=name,
        market=cfg.get("market", "std"),
    )
    client = MooTDX2Client(mootdx2_cfg)
```

- [ ] **Step 4: Commit**

---

### Task 6: Update upstreams.yaml

**Files:**
- Modify: `config/upstreams.yaml`

- [ ] **Step 1: Add mootdx2 upstream config**

```yaml
  mootdx2:
    enabled: true
    description: "MooTDX2 行情接口"
    type: "mootdx2"
    market: "std"
```

- [ ] **Step 2: Update upstream_tool_mapping**

将所有 `tdx_local:` 改为 `mootdx2:`:
```yaml
upstream_tool_mapping:
  get_quote:                   {mootdx2: get_quote}
  get_kline:                   {mootdx2: get_kline}
  get_batch_quote:             {mootdx2: get_batch_quote}
  get_minute_data:             {mootdx2: get_minute_data}
  get_trade:                   {mootdx2: get_trade}
  get_trade_history:           {mootdx2: get_trade_history}
  get_index_kline:              {mootdx2: get_index_kline}
  get_code_list:               {mootdx2: get_code_list}
  get_stock_codes:             {mootdx2: get_stock_codes}
  get_etf_codes:               {mootdx2: get_etf_codes}
  get_etf_list:                {mootdx2: get_etf_list}
  get_market_count:            {mootdx2: get_market_count}
  get_workday:                 {mootdx2: get_workday}
  get_workday_range:           {mootdx2: get_workday_range}
  get_index_all:               {mootdx2: get_index_all}
  get_income:                  {mootdx2: get_income}
```

- [ ] **Step 3: Disable or remove tdx_local upstream**

```yaml
  tdx_local:
    enabled: false  # 改用 mootdx2
```

- [ ] **Step 4: Commit**

---

### Task 7: Integration test

**Files:**
- Test: `tests/test_mootdx2_integration.py`

- [ ] **Step 1: Write integration test**

```python
import pytest

@pytest.mark.integration
async def test_mootdx2_quote():
    """测试 get_quote 接口"""
    from src.mootdx2_client import MooTDX2Client, MooTDX2Config
    config = MooTDX2Config(name="test", market="std")
    client = MooTDX2Client(config)
    await client.start()
    result = await client.call_tool("get_quote", {"code": "600000"})
    assert result.success is True or "error" in result.data
    await client.stop()
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/test_mootdx2_integration.py -v`

- [ ] **Step 3: Commit**

---

## Summary

After completing all tasks:
1. `src/mootdx2_client.py` provides all 16 tools using mootdx2
2. `gateway_server.py` registers mootdx2 as upstream
3. `upstreams.yaml` points tools to mootdx2 and disables tdx_local

**Plan complete and saved to** `docs/superpowers/plans/2026-09-05-mootdx2-implementation.md`
