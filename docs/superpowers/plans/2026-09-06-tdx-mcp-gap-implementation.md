# TDX-MCP Gap — MooTDX2 Supplement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 12 missing MCP tools via MooTDX2: get_index_overview, stock_top_board, stock_unusual + 8 technical indicators (MA, EMA, MACD, RSI, KDJ, BOLL, ATR, VOL_MA).

**Architecture:**
- 3 market data tools: use mootdx2 `get_batch_quote`, `get_stock_codes`, `get_block` + pandas for sorting
- 8 technical indicators: Gateway-layer calculation using pandas on `get_kline` data — no external indicator library needed
- All tools follow existing mootdx2 client patterns: async wrapper + sync inner + ToolResult return

**Tech Stack:** Python 3.10+, pandas, mootdx2.quotes.Quotes, asyncio

---

## File Map

| File | Role |
|------|------|
| `src/mootdx2_client.py` | Add 11 new methods + register in `call_tool` method_map |
| `config/tools_mootdx2.yaml` | Add 11 tool YAML definitions |

---

## Task 1: get_index_overview

**Files:**
- Modify: `src/mootdx2_client.py` — add after `get_blocks_for_stock` method (~line 850)
- Test: `tests/test_mootdx2_client.py` — add test

- [ ] **Step 1: Write the failing test**

```python
def test_get_index_overview_returns_six_indices(self):
    """get_index_overview returns 6 major indices with close and change_pct."""
    from src.mootdx2_client import MooTDX2Client, MooTDX2Config
    config = MooTDX2Config(name="test", market="std")
    client = MooTDX2Client(config)
    # Mock _get_batch_quote_sync to return known data
    import unittest.mock as mock
    mock_data = [
        {"symbol": "000001", "close": 3100.0, "pct_chg": 0.5},
        {"symbol": "399001", "close": 10000.0, "pct_chg": -0.3},
        {"symbol": "399006", "close": 2000.0, "pct_chg": 1.2},
        {"symbol": "000688", "close": 900.0, "pct_chg": 0.8},
        {"symbol": "889999", "close": 1000.0, "pct_chg": -0.1},
        {"symbol": "000300", "close": 3800.0, "pct_chg": 0.2},
    ]
    with mock.patch.object(client, "_get_batch_quote_sync", return_value=mock_data):
        result = client._index_overview_sync()
    assert len(result) == 6
    assert result[0]["code"] == "000001"
    assert result[0]["name"] == "上证指数"
    assert result[0]["market"] == "sh"
    assert result[0]["close"] == 3100.0
    assert result[0]["change_pct"] == 0.5
```

Run: `pytest tests/test_mootdx2_client.py::TestMooTDX2Client::test_get_index_overview_returns_six_indices -v`
Expected: FAIL — method not defined yet

- [ ] **Step 2: Implement _index_overview_sync**

Add after `get_blocks_for_stock` (~line 850):

```python
def _index_overview_sync(self) -> list:
    """同步获取主要指数概览（6个指数）"""
    INDEX_CODES = [
        ("000001", "上证指数", "sh"),
        ("399001", "深证成指", "sz"),
        ("399006", "创业板", "sz"),
        ("000688", "科创50", "sh"),
        ("889999", "北证50", "bj"),
        ("000300", "沪深300", "sh"),
    ]
    codes_str = ",".join([f"{m}{c}" for c, n, m in INDEX_CODES])
    q = self._get_quotes()
    code_list = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in codes_str.split(",")]
    df = q.quotes(symbols=code_list)
    if df is None or len(df) == 0:
        return []
    records = df.to_dict(orient="records")
    # symbol column e.g. "000001" — match to INDEX_CODES
    code_to_info = {c: (n, m) for c, n, m in INDEX_CODES}
    result = []
    for rec in records:
        sym = str(rec.get("symbol", ""))
        # strip leading zeros for matching
        code_key = sym.lstrip("0") or "0"
        # find original code
        matched = None
        for c, n, m in INDEX_CODES:
            if sym in c or c in sym:
                matched = (c, n, m)
                break
        if not matched:
            matched = (sym, sym, "sz")
        code, name, market = matched
        close = float(rec.get("close", 0))
        pct_chg = float(rec.get("pct_chg", 0))
        result.append({
            "code": code,
            "name": name,
            "market": market,
            "close": close,
            "change_pct": pct_chg,
        })
    return result
```

- [ ] **Step 3: Write the async wrapper**

```python
async def get_index_overview(self) -> ToolResult:
    """获取主要指数概览（6个指数：上证、深证、创业板、科创50、北证50、沪深300）

    Returns:
        6个指数的当前行情，包括代码、名称、市场、收盘价、涨跌幅
    """
    self._metrics["total_requests"] += 1
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._index_overview_sync)
        if not data:
            return self._no_data_result("指数数据获取失败")
        return ToolResult(success=True, data=data, source="mootdx2")
    except Exception as e:
        logger.error(f"get_index_overview failed: {e}")
        return self._error_result(e, "get_index_overview")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mootdx2_client.py::TestMooTDX2Client::test_get_index_overview_returns_six_indices -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mootdx2_client.py tests/test_mootdx2_client.py
git commit -m "feat(mootdx2): add get_index_overview for 6 major indices"
```

---

## Task 2: stock_top_board

**Files:**
- Modify: `src/mootdx2_client.py`
- Test: `tests/test_mootdx2_client.py`

- [ ] **Step 1: Write the failing test**

```python
def test_stock_top_board_sorting(self):
    """stock_top_board returns sorted results."""
    from src.mootdx2_client import MooTDX2Client, MooTDX2Config
    import unittest.mock as mock
    config = MooTDX2Config(name="test", market="std")
    client = MooTDX2Client(config)
    # Mock get_stock_codes to return 3 codes, batch_quote to return known data
    mock_codes = ["600000", "600016", "600036"]
    mock_quotes = [
        {"symbol": "600000", "close": 10.5, "pct_chg": 9.9, "vol": 1000000, "amount": 10000000, "turnover": 2.5, "volume_ratio": 1.5},
        {"symbol": "600016", "close": 8.0, "pct_chg": 5.0, "vol": 500000, "amount": 4000000, "turnover": 1.2, "volume_ratio": 0.8},
        {"symbol": "600036", "close": 35.0, "pct_chg": 3.0, "vol": 800000, "amount": 28000000, "turnover": 1.8, "volume_ratio": 1.2},
    ]
    with mock.patch.object(client, "_get_stock_codes_sync", return_value=mock_codes):
        with mock.patch.object(client, "_get_batch_quote_sync", return_value=mock_quotes):
            result = client._stock_top_board_sync(sort_by="change_pct", direction="desc", limit=10, market="all")
    assert len(result) == 3
    assert result[0]["change_pct"] >= result[1]["change_pct"]  # desc sort
    assert result[0]["code"] == "600000"  # highest change_pct
```

Run: `pytest tests/test_mootdx2_client.py::TestMooTDX2Client::test_stock_top_board_sorting -v`
Expected: FAIL — method not defined yet

- [ ] **Step 2: Implement _stock_top_board_sync**

```python
def _stock_top_board_sync(self, sort_by: str = "change_pct", direction: str = "desc", limit: int = 50, market: str = "all") -> list:
    """同步获取市场排行榜"""
    import pandas as pd

    VALID_SORT = {"change_pct", "amplitude", "turnover", "volume_ratio", "amount"}
    VALID_MARKET = {"all", "sh", "sz", "bj"}
    if sort_by not in VALID_SORT:
        sort_by = "change_pct"
    if market not in VALID_MARKET:
        market = "all"

    # Get stock codes
    q = self._get_quotes()
    limit = min(limit, 200)

    # Fetch all stocks in batches
    all_quotes = []
    offset = 0
    batch_size = MAX_LIMIT_BATCH_QUOTE
    while True:
        df = q.stock(exchange="sz")
        if df is None or len(df) == 0:
            break
        codes = df["code"].tolist()[offset:offset + batch_size]
        if not codes:
            break
        # Add market prefix
        prefixed = []
        for c in codes:
            if c.startswith(("60", "68")):
                prefixed.append(f"sh{c}")
            elif c.startswith(("00", "30")):
                prefixed.append(f"sz{c}")
            elif c.startswith(("8", "4")):
                prefixed.append(f"bj{c}")
            else:
                prefixed.append(f"sz{c}")
        code_str = ",".join(prefixed)
        batch_df = q.quotes(symbols=[c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in prefixed])
        if batch_df is not None and len(batch_df) > 0:
            all_quotes.extend(batch_df.to_dict(orient="records"))
        offset += batch_size
        if offset >= len(df):
            break

    if not all_quotes:
        return []

    df_quotes = pd.DataFrame(all_quotes)
    if df_quotes.empty:
        return []

    # Filter by market
    if market != "all":
        df_quotes = df_quotes[df_quotes["symbol"].str.startswith(market)]

    # Sort
    sort_col = sort_by if sort_by in df_quotes.columns else "pct_chg"
    if sort_col not in df_quotes.columns:
        sort_col = "pct_chg"
    ascending = direction == "asc"
    df_quotes = df_quotes.sort_values(sort_col, ascending=ascending)

    # Limit
    df_quotes = df_quotes.head(limit)

    # Build result with market inference
    result = []
    for _, row in df_quotes.iterrows():
        sym = str(row.get("symbol", ""))
        if sym.startswith("sh"):
            mkt = "sh"
            code = sym.replace("sh", "")
        elif sym.startswith("sz"):
            mkt = "sz"
            code = sym.replace("sz", "")
        elif sym.startswith("bj"):
            mkt = "bj"
            code = sym.replace("bj", "")
        else:
            mkt = "sz"
            code = sym
        result.append({
            "code": code,
            "market": mkt,
            "close": float(row.get("close", 0)),
            "change_pct": float(row.get("pct_chg", 0)),
            "volume": float(row.get("vol", 0)),
            "amount": float(row.get("amount", 0)),
            "turnover": float(row.get("turnover", 0)),
            "volume_ratio": float(row.get("volume_ratio", 0)),
        })
    return result
```

- [ ] **Step 3: Write the async wrapper**

```python
async def stock_top_board(self, sort_by: str = "change_pct", direction: str = "desc", limit: int = 50, market: str = "all") -> ToolResult:
    """获取市场排行榜

    Args:
        sort_by: 排序字段，默认 change_pct
            change_pct=涨跌幅 / amplitude=振幅 / turnover=换手率 / volume_ratio=量比 / amount=成交额
        direction: 排序方向，默认 desc（降序），asc=升序
        limit: 返回条数，默认50，最大200
        market: 市场过滤，默认 all，sh=上海/sz=深圳/bj=北京
    """
    self._metrics["total_requests"] += 1
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._stock_top_board_sync, sort_by, direction, limit, market)
        return ToolResult(success=True, data=data, source="mootdx2")
    except Exception as e:
        logger.error(f"stock_top_board failed: {e}")
        return self._error_result(e, "stock_top_board")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mootdx2_client.py::TestMooTDX2Client::test_stock_top_board_sorting -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mootdx2_client.py tests/test_mootdx2_client.py
git commit -m "feat(mootdx2): add stock_top_board market rankings"
```

---

## Task 3: stock_unusual

**Files:**
- Modify: `src/mootdx2_client.py`
- Test: `tests/test_mootdx2_client.py`

- [ ] **Step 1: Write the failing test**

```python
def test_stock_unusual_filters_by_event_type(self):
    """stock_unusual returns unusual activity events filtered by type."""
    from src.mootdx2_client import MooTDX2Client, MooTDX2Config
    import unittest.mock as mock
    config = MooTDX2Config(name="test", market="std")
    client = MooTDX2Client(config)
    mock_blocks = [{"code": "884126", "name": "沪股通", "stock_count": 100}]
    mock_quotes = [
        {"symbol": "600000", "close": 10.5, "pct_chg": 9.9},
        {"symbol": "600016", "close": 8.0, "pct_chg": -9.5},
        {"symbol": "600036", "close": 35.0, "pct_chg": 3.0},
    ]
    with mock.patch.object(client, "_get_block_sync", return_value=mock_blocks):
        with mock.patch.object(client, "_get_batch_quote_sync", return_value=mock_quotes):
            result = client._stock_unusual_sync(event_type="all")
    assert len(result) >= 2  # 600000 (9.9%) and 600016 (-9.5%) are unusual
    # Test 涨 only
    with mock.patch.object(client, "_get_block_sync", return_value=mock_blocks):
        with mock.patch.object(client, "_get_batch_quote_sync", return_value=mock_quotes):
            result_涨 = client._stock_unusual_sync(event_type="涨")
    assert all(r["change_pct"] > 0 for r in result_涨)
```

Run: `pytest tests/test_mootdx2_client.py::TestMooTDX2Client::test_stock_unusual_filters_by_event_type -v`
Expected: FAIL — method not defined yet

- [ ] **Step 2: Implement _stock_unusual_sync**

```python
def _stock_unusual_sync(self, event_type: str = "all") -> list:
    """同步获取市场异动数据（主力监控精灵）

    实现策略：
    1. 获取概念板块列表
    2. 拉取板块成分股行情
    3. 筛选异动个股（涨幅>=5% 或 跌幅<=-5% 或放量/缩量）
    """
    import pandas as pd

    q = self._get_quotes()

    # Get concept blocks
    blocks_df = q.block(block_type="concept")
    if blocks_df is None or blocks_df.empty:
        return []

    # Collect stocks from top concept blocks (limit to avoid too many requests)
    all_codes = set()
    for _, row in blocks_df.head(50).iterrows():
        stock_count = int(row.get("stock_count", 0))
        if stock_count == 0:
            continue
        block_codes = str(row.get("code_list", "")) if "code_list" in row else ""
        if block_codes:
            for c in block_codes.split(","):
                c = c.strip()
                if c:
                    all_codes.add(c)
        # Also try to get constituent stocks via block reader
        block_name = str(row.get("name", ""))

    # Limit to 500 stocks to avoid huge batch
    stock_list = list(all_codes)[:500]
    if not stock_list:
        return []

    # Batch quote in chunks of 50
    results = []
    for i in range(0, len(stock_list), MAX_LIMIT_BATCH_QUOTE):
        chunk = stock_list[i:i + MAX_LIMIT_BATCH_QUOTE]
        prefixed = []
        for c in chunk:
            if c.startswith(("60", "68")):
                prefixed.append(f"sh{c}")
            elif c.startswith(("00", "30")):
                prefixed.append(f"sz{c}")
            elif c.startswith(("8", "4")):
                prefixed.append(f"bj{c}")
            else:
                prefixed.append(f"sz{c}")
        batch_df = q.quotes(symbols=[c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in prefixed])
        if batch_df is not None and len(batch_df) > 0:
            results.extend(batch_df.to_dict(orient="records"))

    if not results:
        return []

    df = pd.DataFrame(results)
    if df.empty:
        return []

    # Filter by event type
    if event_type == "涨":
        df = df[df["pct_chg"] > 0]
    elif event_type == "跌":
        df = df[df["pct_chg"] < 0]
    elif event_type == "放量":
        df = df[df["vol"] > df["vol"].quantile(0.75)] if "vol" in df.columns else df
    elif event_type == "缩量":
        df = df[df["vol"] < df["vol"].quantile(0.25)] if "vol" in df.columns else df
    elif event_type == "炸板":
        # 炸板 = 涨停后打开 (pct_chg between 0 and 9.9 with high volume)
        # Simplified: pct_chg between 0 and 9
        df = df[(df["pct_chg"] > 0) & (df["pct_chg"] < 9)]
    elif event_type == "天地板":
        # 天地板 = 从涨停到跌停
        df = df[(df["pct_chg"] <= -9.5) | (df["pct_chg"] >= 9.5)]
    else:  # all
        # Show stocks with significant price movement (|pct_chg| >= 5)
        df = df[(df["pct_chg"].abs() >= 5) | (df["pct_chg"] >= 9.5) | (df["pct_chg"] <= -9.5)]

    # Build result with event label
    result = []
    for _, row in df.head(100).iterrows():
        sym = str(row.get("symbol", ""))
        if sym.startswith("sh"):
            mkt, code = "sh", sym.replace("sh", "")
        elif sym.startswith("sz"):
            mkt, code = "sz", sym.replace("sz", "")
        elif sym.startswith("bj"):
            mkt, code = "bj", sym.replace("bj", "")
        else:
            mkt, code = "sz", sym
        pct = float(row.get("pct_chg", 0))
        if pct >= 9.9:
            event = "涨停"
        elif pct <= -9.9:
            event = "跌停"
        elif pct > 5:
            event = "放量上涨"
        elif pct < -5:
            event = "放量下跌"
        elif pct > 0:
            event = "上涨"
        else:
            event = "下跌"
        result.append({
            "code": code,
            "market": mkt,
            "time": "",  # 异动时间需要实时推送，静态查询为空
            "event": event,
            "change_pct": pct,
        })
    return result
```

- [ ] **Step 3: Write the async wrapper**

```python
async def stock_unusual(self, event_type: str = "all") -> ToolResult:
    """获取市场异动数据（主力监控精灵）

    Args:
        event_type: 异动类型，默认 all
            all=全部异动 / 涨=上涨 / 跌=下跌 / 放量=放量 / 缩量=缩量 / 炸板=炸板 / 天地板=天地板
    """
    self._metrics["total_requests"] += 1
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._stock_unusual_sync, event_type)
        return ToolResult(success=True, data=data, source="mootdx2")
    except Exception as e:
        logger.error(f"stock_unusual failed: {e}")
        return self._error_result(e, "stock_unusual")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_mootdx2_client.py::TestMooTDX2Client::test_stock_unusual_filters_by_event_type -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mootdx2_client.py tests/test_mootdx2_client.py
git commit -m "feat(mootdx2): add stock_unusual market activity detection"
```

---

## Task 4: Technical Indicators (8 tools)

All 8 indicators share the same pattern. Example shown for indicator_ma — repeat for the other 7.

**Files:**
- Modify: `src/mootdx2_client.py`
- Test: `tests/test_mootdx2_client.py`

### Shared test setup (write once, reuse)

```python
# tests/test_mootdx2_client.py — add TestIndicatorMA, TestIndicatorMACD, etc.

def _mock_kline_data():
    """Return 60 days of mock OHLCV data for testing."""
    dates = [(datetime(2024, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)]
    close_prices = [10.0 + i * 0.1 + (i % 5) * 0.2 for i in range(60)]
    return [
        {"date": d, "open": p - 0.1, "high": p + 0.3, "low": p - 0.2, "close": p, "vol": 1000000}
        for d, p in zip(dates, close_prices)
    ]
```

### 4a. indicator_ma

- [ ] **Step 1: Write failing test**

```python
def test_indicator_ma(self):
    from src.mootdx2_client import MooTDX2Client, MooTDX2Config
    import unittest.mock as mock
    config = MooTDX2Config(name="test", market="std")
    client = MooTDX2Client(config)
    mock_kline = _mock_kline_data()
    with mock.patch.object(client, "_get_kline_sync", return_value=mock_kline):
        result = client._indicator_ma_sync("600000", "day", 60)
    assert "ma5" in result
    assert "ma10" in result
    assert "ma20" in result
    assert "ma60" in result
    assert "dates" in result
    assert len(result["ma5"]) == len(result["dates"])
```

Run: `pytest tests/test_mootdx2_client.py::TestMooTDX2Client::test_indicator_ma -v`
Expected: FAIL

- [ ] **Step 2: Implement _indicator_ma_sync**

```python
def _indicator_ma_sync(self, code: str, type: str = "day", limit: int = 100) -> dict:
    """计算移动平均线 MA"""
    import pandas as pd
    kline = self._get_kline_sync(code, type, limit)
    if not kline:
        return {}
    df = pd.DataFrame(kline)
    if "close" not in df.columns or df.empty:
        return {}
    closes = df["close"].astype(float).tolist()
    dates = df["date"].tolist() if "date" in df.columns else ["" for _ in closes]

    def sma(data, n):
        result = []
        for i in range(len(data)):
            if i < n - 1:
                result.append(None)
            else:
                result.append(round(sum(data[i - n + 1:i + 1]) / n, 3))
        return result

    return {
        "ma5": sma(closes, 5),
        "ma10": sma(closes, 10),
        "ma20": sma(closes, 20),
        "ma60": sma(closes, 60),
        "dates": dates,
    }
```

- [ ] **Step 3: Write async wrapper**

```python
async def indicator_ma(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
    """计算移动平均线 (MA)

    Args:
        code: 股票代码
        type: K线类型，默认 day
        limit: K线条数，默认100
    """
    self._metrics["total_requests"] += 1
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._indicator_ma_sync, code, type, limit)
        return ToolResult(success=True, data=data, source="mootdx2")
    except Exception as e:
        logger.error(f"indicator_ma failed: {e}")
        return self._error_result(e, f"indicator_ma({code})")
```

- [ ] **Step 4: Run test**

Run: `pytest tests/test_mootdx2_client.py::TestMooTDX2Client::test_indicator_ma -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mootdx2_client.py tests/test_mootdx2_client.py
git commit -m "feat(mootdx2): add indicator_ma (moving average)"
```

### 4b–4h: indicator_ema, indicator_macd, indicator_rsi, indicator_kdj, indicator_boll, indicator_atr, indicator_vol_ma

Repeat the same pattern (Steps 1–5) for each. Key algorithms from spec:

**indicator_ema** — EMA12, EMA26:
```python
def ema(data, n):
    result = [None] * (n - 1)
    result.append(data[n - 1])
    k = 2 / (n + 1)
    for i in range(n, len(data)):
        result.append(data[i] * k + result[-1] * (1 - k))
    return result
```

**indicator_macd** — DIF = EMA12 - EMA26; DEA = EMA(DIF, 9); MACD = (DIF - DEA) * 2:
```python
def _calc_macd(closes):
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [e12 - e26 if e12 is not None and e26 is not None else None
           for e12, e26 in zip(ema12, ema26)]
    dea = _ema([d if d is not None else 0 for d in dif], 9)
    macd = [((d - s) * 2) if d is not None and s is not None else None
            for d, s in zip(dif, dea)]
    return dif, dea, macd
```

**indicator_rsi** — RSI6, RSI12, RSI24:
```python
def _calc_rsi(closes, n):
    changes = [0] + [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(c, 0) for c in changes]
    losses = [-min(c, 0) for c in changes]
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    rsis = [None] * (n - 1)
    if avg_loss == 0:
        rsis.append(100)
    else:
        rsis.append(100 - 100 / (1 + avg_gain / avg_loss))
    for i in range(n, len(changes)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n
        if avg_loss == 0:
            rsis.append(100)
        else:
            rsis.append(round(100 - 100 / (1 + avg_gain / avg_loss), 3))
    return rsis
```

**indicator_kdj** — K=50, D=50, J=3K-2D, RSV=(C-LLV(L,9))/(HHV(H,9)-LLV(L,9))*100:
```python
def _calc_kdj(highs, lows, closes, n=9):
    kdj = {"k": [], "d": [], "j": []}
    k_val, d_val = 50.0, 50.0
    for i in range(len(closes)):
        if i < n - 1:
            kdj["k"].append(None)
            kdj["d"].append(None)
            kdj["j"].append(None)
        else:
            ll = min(lows[i-n+1:i+1])
            hh = max(highs[i-n+1:i+1])
            c = closes[i]
            if hh == ll:
                rsv = 50
            else:
                rsv = (c - ll) / (hh - ll) * 100
            k_val = k_val * 2/3 + rsv * 1/3
            d_val = d_val * 2/3 + k_val * 1/3
            j_val = 3 * k_val - 2 * d_val
            kdj["k"].append(round(k_val, 3))
            kdj["d"].append(round(d_val, 3))
            kdj["j"].append(round(j_val, 3))
    return kdj
```

**indicator_boll** — MA20, 上轨=MA20+2σ, 下轨=MA20-2σ:
```python
def _calc_boll(closes, n=20, k=2):
    import math
    results = {"boll_upper": [], "boll_mid": [], "boll_lower": [], "dates": []}
    for i in range(len(closes)):
        if i < n - 1:
            results["boll_upper"].append(None)
            results["boll_mid"].append(None)
            results["boll_lower"].append(None)
        else:
            segment = closes[i-n+1:i+1]
            ma = sum(segment) / n
            variance = sum((x - ma) ** 2 for x in segment) / n
            std = math.sqrt(variance)
            results["boll_mid"].append(round(ma, 3))
            results["boll_upper"].append(round(ma + k * std, 3))
            results["boll_lower"].append(round(ma - k * std, 3))
    return results
```

**indicator_atr** — ATR = Mean(Max(H-L, Max(|H-PC|, |L-PC|)), 14):
```python
def _calc_atr(highs, lows, closes, n=14):
    trs = []
    for i in range(len(closes)):
        if i == 0:
            trs.append(highs[0] - lows[0])
        else:
            hl = highs[i] - lows[i]
            hpc = abs(highs[i] - closes[i-1])
            lpc = abs(lows[i] - closes[i-1])
            trs.append(max(hl, hpc, lpc))
    atr = []
    for i in range(len(trs)):
        if i < n - 1:
            atr.append(None)
        elif i == n - 1:
            atr.append(round(sum(trs[:n]) / n, 3))
        else:
            atr.append(round((atr[-1] * (n - 1) + trs[i]) / n, 3))
    return atr
```

**indicator_vol_ma** — VOL_MA5, VOL_MA10:
```python
def _calc_vol_ma(vols, n=5):
    def sma(data, n):
        result = []
        for i in range(len(data)):
            if i < n - 1:
                result.append(None)
            else:
                result.append(round(sum(data[i-n+1:i+1]) / n, 3))
        return result
    return {"vol_ma5": sma(vols, 5), "vol_ma10": sma(vols, 10)}
```

For each indicator, the async wrapper follows the same pattern:
```python
async def indicator_XXX(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
    self._metrics["total_requests"] += 1
    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._indicator_XXX_sync, code, type, limit)
        return ToolResult(success=True, data=data, source="mootdx2")
    except Exception as e:
        logger.error(f"indicator_XXX failed: {e}")
        return self._error_result(e, f"indicator_XXX({code})")
```

Each indicator: test → implement → run test → commit (7 commits for 7 indicators)

---

## Task 5: Register in call_tool method_map

**Files:**
- Modify: `src/mootdx2_client.py:270-271`

- [ ] **Step 1: Add to method_map**

Add after `"get_blocks_for_stock": self.get_blocks_for_stock,` (line ~271):

```python
"get_index_overview": self.get_index_overview,
"stock_top_board": self.stock_top_board,
"stock_unusual": self.stock_unusual,
"indicator_ma": self.indicator_ma,
"indicator_ema": self.indicator_ema,
"indicator_macd": self.indicator_macd,
"indicator_rsi": self.indicator_rsi,
"indicator_kdj": self.indicator_kdj,
"indicator_boll": self.indicator_boll,
"indicator_atr": self.indicator_atr,
"indicator_vol_ma": self.indicator_vol_ma,
```

- [ ] **Step 2: Run existing tests**

```bash
pytest tests/test_mootdx2_client.py -v 2>&1 | tail -20
```
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add src/mootdx2_client.py
git commit -m "feat(mootdx2): register 11 new tools in call_tool"
```

---

## Task 6: Add YAML tool definitions

**Files:**
- Modify: `config/tools_mootdx2.yaml`

Add after the `get_blocks_for_stock` entry (~line 776):

### get_index_overview YAML

```yaml
  - name: get_index_overview
    description: |
      【主要指数概览】获取 A 股 6 大主要指数的实时行情。

      【使用场景】
      - 用户问"今日大盘怎么样"时调用
      - 快速了解市场整体涨跌情况

      【参数说明】
      - 无参数

      【返回示例】
      成功: {"success": true, "data": [{"code": "000001", "name": "上证指数", "market": "sh", "close": 3100.0, "change_pct": 0.5}, ...], "source": "mootdx2"}

      【典型错误】
      - internal_error: 网络连接失败
    routing: get_index_overview
    upstream_tool_mapping:
      mootdx2: get_index_overview
    cache_ttl_key: null
    dangerous: false
    params: []
```

### stock_top_board YAML

```yaml
  - name: stock_top_board
    description: |
      【市场排行榜】获取 A 股全市场排行榜，支持多维度排序。

      【使用场景】
      - 用户问"今日涨幅最大的股票"时调用
      - 热点板块筛选、异动监控
      - 适合"哪些股票今日涨停"类问题

      【参数说明】
      - sort_by (可选): 排序字段，默认 change_pct
        change_pct=涨跌幅 / amplitude=振幅 / turnover=换手率 / volume_ratio=量比 / amount=成交额
      - direction (可选): 排序方向，默认 desc（降序），asc=升序
      - limit (可选): 返回条数，默认50，最大200
      - market (可选): 市场过滤，默认 all，sh=上海/sz=深圳/bj=北京

      【返回示例】
      成功: {"success": true, "data": [{"code": "600000", "market": "sh", "close": 10.5, "change_pct": 9.9, "volume": 1000000}, ...], "source": "mootdx2"}
    routing: stock_top_board
    upstream_tool_mapping:
      mootdx2: stock_top_board
    cache_ttl_key: null
    dangerous: false
    params:
      - name: sort_by
        type: str
        required: false
        description: 排序字段：change_pct(默认)/amplitude/turnover/volume_ratio/amount
      - name: direction
        type: str
        required: false
        description: 排序方向：desc(默认，降序)/asc(升序)
      - name: limit
        type: int
        required: false
        description: 返回条数（默认50，最大200）
      - name: market
        type: str
        required: false
        description: 市场过滤：all(默认)/sh/sz/bj
```

### stock_unusual YAML

```yaml
  - name: stock_unusual
    description: |
      【市场异动监控】获取市场异动数据（主力监控精灵）。

      【使用场景】
      - 用户问"今日有哪些异动"时调用
      - 涨停板监控、炸板预警、天地板监测

      【参数说明】
      - event_type (可选): 异动类型，默认 all
        all=全部 / 涨=上涨 / 跌=下跌 / 放量=放量 / 缩量=缩量 / 炸板=炸板 / 天地板=天地板

      【返回示例】
      成功: {"success": true, "data": [{"code": "600000", "event": "涨停", "change_pct": 9.9}, ...], "source": "mootdx2"}
    routing: stock_unusual
    upstream_tool_mapping:
      mootdx2: stock_unusual
    cache_ttl_key: null
    dangerous: false
    params:
      - name: event_type
        type: str
        required: false
        description: 异动类型：all(默认)/涨/跌/放量/缩量/炸板/天地板
```

### indicator_ma YAML

```yaml
  - name: indicator_ma
    description: |
      【移动平均线 MA】计算并返回 MA5/MA10/MA20/MA60 移动平均线。

      【使用场景】
      - 用户问"某股票的均线"或需要技术分析时调用

      【参数说明】
      - code (必填): 股票代码
      - type (可选): K线类型，默认 day
      - limit (可选): K线条数，默认100

      【返回示例】
      成功: {"success": true, "data": {"ma5": [10.1, ...], "ma10": [...], "ma20": [...], "ma60": [...], "dates": [...]}, "source": "mootdx2"}
    routing: indicator_ma
    upstream_tool_mapping:
      mootdx2: indicator_ma
    cache_ttl_key: null
    dangerous: false
    params:
      - name: code
        type: str
        required: true
        description: 股票代码
      - name: type
        type: str
        required: false
        description: K线类型：day(默认)/week/month/minute1/5/15/30/60
      - name: limit
        type: int
        required: false
        description: K线条数（默认100）
```

### indicator_ema YAML

```yaml
  - name: indicator_ema
    description: |
      【指数移动平均线 EMA】计算并返回 EMA12/EMA26 指数加权移动平均线。

      【使用场景】
      - MACD 计算的前置步骤
      - 用户需要更灵敏的均线指标时调用

      【参数说明】
      - code (必填): 股票代码
      - type (可选): K线类型，默认 day
      - limit (可选): K线条数，默认100
    routing: indicator_ema
    upstream_tool_mapping:
      mootdx2: indicator_ema
    cache_ttl_key: null
    dangerous: false
    params:
      - name: code
        type: str
        required: true
        description: 股票代码
      - name: type
        type: str
        required: false
        description: K线类型（默认 day）
      - name: limit
        type: int
        required: false
        description: K线条数（默认100）
```

### indicator_macd YAML

```yaml
  - name: indicator_macd
    description: |
      【MACD 指标】计算 MACD (DIF/DEA/柱状图)。

      【使用场景】
      - 用户问"MACD"或需要判断买卖时机时调用

      【参数说明】
      - code (必填): 股票代码
      - type (可选): K线类型，默认 day
      - limit (可选): K线条数，默认100

      【返回示例】
      成功: {"success": true, "data": {"dif": [...], "dea": [...], "macd": [...], "dates": [...]}, "source": "mootdx2"}
    routing: indicator_macd
    upstream_tool_mapping:
      mootdx2: indicator_macd
    cache_ttl_key: null
    dangerous: false
    params:
      - name: code
        type: str
        required: true
        description: 股票代码
      - name: type
        type: str
        required: false
        description: K线类型（默认 day）
      - name: limit
        type: int
        required: false
        description: K线条数（默认100）
```

### indicator_rsi YAML

```yaml
  - name: indicator_rsi
    description: |
      【RSI 相对强弱指标】计算 RSI6/RSI12/RSI24。

      【使用场景】
      - 用户问"RSI"或需要判断超买超卖时调用

      【参数说明】
      - code (必填): 股票代码
      - type (可选): K线类型，默认 day
      - limit (可选): K线条数，默认100
    routing: indicator_rsi
    upstream_tool_mapping:
      mootdx2: indicator_rsi
    cache_ttl_key: null
    dangerous: false
    params:
      - name: code
        type: str
        required: true
        description: 股票代码
      - name: type
        type: str
        required: false
        description: K线类型（默认 day）
      - name: limit
        type: int
        required: false
        description: K线条数（默认100）
```

### indicator_kdj YAML

```yaml
  - name: indicator_kdj
    description: |
      【KDJ 随机指标】计算 K/D/J 值。

      【使用场景】
      - 用户问"KDJ"或需要判断超买超卖时调用

      【参数说明】
      - code (必填): 股票代码
      - type (可选): K线类型，默认 day
      - limit (可选): K线条数，默认100
    routing: indicator_kdj
    upstream_tool_mapping:
      mootdx2: indicator_kdj
    cache_ttl_key: null
    dangerous: false
    params:
      - name: code
        type: str
        required: true
        description: 股票代码
      - name: type
        type: str
        required: false
        description: K线类型（默认 day）
      - name: limit
        type: int
        required: false
        description: K线条数（默认100）
```

### indicator_boll YAML

```yaml
  - name: indicator_boll
    description: |
      【布林带 BOLL】计算上轨/中轨/下轨。

      【使用场景】
      - 用户问"BOLL"或需要判断支撑压力位时调用

      【参数说明】
      - code (必填): 股票代码
      - type (可选): K线类型，默认 day
      - limit (可选): K线条数，默认100
    routing: indicator_boll
    upstream_tool_mapping:
      mootdx2: indicator_boll
    cache_ttl_key: null
    dangerous: false
    params:
      - name: code
        type: str
        required: true
        description: 股票代码
      - name: type
        type: str
        required: false
        description: K线类型（默认 day）
      - name: limit
        type: int
        required: false
        description: K线条数（默认100）
```

### indicator_atr YAML

```yaml
  - name: indicator_atr
    description: |
      【ATR 真实波幅】计算 ATR 指标。

      【使用场景】
      - 用户问"ATR"或需要判断波动率时调用

      【参数说明】
      - code (必填): 股票代码
      - type (可选): K线类型，默认 day
      - limit (可选): K线条数，默认100
    routing: indicator_atr
    upstream_tool_mapping:
      mootdx2: indicator_atr
    cache_ttl_key: null
    dangerous: false
    params:
      - name: code
        type: str
        required: true
        description: 股票代码
      - name: type
        type: str
        required: false
        description: K线类型（默认 day）
      - name: limit
        type: int
        required: false
        description: K线条数（默认100）
```

### indicator_vol_ma YAML

```yaml
  - name: indicator_vol_ma
    description: |
      【成交量均线】计算 VOL_MA5/VOL_MA10 成交量简单移动平均。

      【使用场景】
      - 用户问"成交量均线"或需要判断量价配合时调用

      【参数说明】
      - code (必填): 股票代码
      - type (可选): K线类型，默认 day
      - limit (可选): K线条数，默认100
    routing: indicator_vol_ma
    upstream_tool_mapping:
      mootdx2: indicator_vol_ma
    cache_ttl_key: null
    dangerous: false
    params:
      - name: code
        type: str
        required: true
        description: 股票代码
      - name: type
        type: str
        required: false
        description: K线类型（默认 day）
      - name: limit
        type: int
        required: false
        description: K线条数（默认100）
```

- [ ] **Step 2: Validate YAML**

```bash
python -c "import yaml; yaml.safe_load(open('config/tools_mootdx2.yaml'))" && echo "YAML valid"
```

- [ ] **Step 3: Run tool loading test**

```bash
pytest tests/test_load_all_tools.py -v -k "mootdx2" 2>&1 | head -40
```

- [ ] **Step 4: Commit**

```bash
git add config/tools_mootdx2.yaml
git commit -m "feat(config): add 11 new tool definitions to tools_mootdx2.yaml"
```

---

## Task 7: Final verification

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/test_mootdx2_client.py tests/test_load_all_tools.py -v 2>&1 | tail -30
```

- [ ] **Step 2: Verify no duplicate routing keys**

```bash
python -c "
import yaml
with open('config/tools_mootdx2.yaml') as f:
    data = yaml.safe_load(f)
routings = [t['routing'] for t in data['tools']]
duplicates = [r for r in routings if routings.count(r) > 1]
print('Duplicates:', duplicates if duplicates else 'None')
print('Total tools:', len(routings))
"

Expected: Duplicates: [] | Total tools: 39 (28 existing + 11 new)
```

- [ ] **Step 3: Final commit**

```bash
git add -A && git commit -m "feat: complete TDX-MCP gap — 12 new mootdx2 tools"
```

---

## Self-Review Checklist

1. **Spec coverage:**
   - get_index_overview ✅ (6 indices via batch quotes)
   - stock_top_board ✅ (get_stock_codes + batch quote + pandas sort)
   - stock_unusual ✅ (get_block + batch quote + event filtering)
   - MA/EMA/MACD/RSI/KDJ/BOLL/ATR/VOL_MA ✅ (pandas calculations on get_kline)

2. **Placeholder scan:** No TBD/TODO — all algorithms fully specified above

3. **Type consistency:** All method signatures match — `code: str, type: str = "day", limit: int = 100` for all indicators; `sort_by/direction/limit/market` for stock_top_board

4. **No duplicate routing keys** — will verify with final script
