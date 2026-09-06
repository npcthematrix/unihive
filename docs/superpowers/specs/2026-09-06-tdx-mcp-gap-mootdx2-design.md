# TDX-MCP-Server → MooTDX2 补齐接口设计

## 背景

TDX-MCP-Server（eltdx TCP 协议）有 23 个工具，其中部分工具 MooTDX2 尚未实现。本设计补充这 4 类共 12 个接口：

| 工具 | 类型 | 数据源 |
|------|------|--------|
| `get_index_overview` | 行情 | mootdx2 quotes |
| `stock_top_board` | 行情 | mootdx2 quotes 批量 |
| `stock_unusual` | 异动数据 | mootdx2 → TDX SECTOR/板块接口 |
| 8个技术指标 | 计算 | K线数据 + pandas |

---

## 接口设计

### 1. get_index_overview

返回主要指数概览（6个指数）。

**参数：** 无

**返回示例：**
```json
{
  "success": true,
  "data": [
    {"code": "000001", "name": "上证指数", "market": "sh", "close": 3100.0, "change_pct": 0.5},
    {"code": "399001", "name": "深证成指", "market": "sz", "close": 10000.0, "change_pct": -0.3},
    {"code": "399006", "name": "创业板", "market": "sz", "close": 2000.0, "change_pct": 1.2},
    {"code": "000688", "name": "科创50", "market": "sh", "close": 900.0, "change_pct": 0.8},
    {"code": "889999", "name": "北证50", "market": "bj", "close": 1000.0, "change_pct": -0.1},
    {"code": "000300", "name": "沪深300", "market": "sh", "close": 3800.0, "change_pct": 0.2}
  ],
  "source": "mootdx2"
}
```

**实现：** 用 `get_batch_quote` 并发拉 6 个指数代码，返回含涨跌幅的字典列表。

---

### 2. stock_top_board

市场排行榜，支持多种排序类型。

**参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `sort_by` | str | 否 | "change_pct" | 排序字段：change_pct / amplitude / turnover / volume_ratio / amount |
| `direction` | str | 否 | "desc" | desc=降序 / asc=升序 |
| `limit` | int | 否 | 50 | 返回条数，最大200 |
| `market` | str | 否 | "all" | 市场过滤：all/sh/sz/bj |

**返回示例：**
```json
{
  "success": true,
  "data": [
    {"code": "600000", "name": "浦发银行", "market": "sh", "close": 10.5, "change_pct": 9.9, "volume": 1000000},
    ...
  ],
  "source": "mootdx2"
}
```

**实现：**
1. 用 `get_stock_codes` 获取全市场代码（limit=10000）
2. 用 `get_batch_quote` 批量拉行情（分块50）
3. 在 Python 层按指定字段排序并截取

---

### 3. stock_unusual

主力监控精灵/市场异动数据。

**参数：**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `event_type` | str | 否 | "all" | 异动类型：all/涨/跌/放量/缩量/炸板/天地板 |

**返回示例：**
```json
{
  "success": true,
  "data": [
    {"code": "600000", "name": "浦发银行", "time": "10:30:00", "event": "放量上涨", "change_pct": 5.2},
    ...
  ],
  "source": "mootdx2"
}
```

**实现方案：**
mootdx2 的 Quotes API 不直接暴露"主力监控精灵"接口。实现分两步：
1. **板块异动探测**：用 `get_block(block_type="concept")` 获取概念板块，然后拉板块内股票的涨跌幅，筛选出异动个股
2. **涨停板监控**：拉取所有涨停/跌停股票（change_pct >= 9.9% 或 <= -9.9%）

如果 TDX 网络协议支持 `SECTOR` 命令获取异动数据，则优先用 `send_raw_pkg` 实现。

---

### 4. 技术指标（8个）

计算基于 K 线数据，在 Gateway 层用 pandas 实现，无需外部指标库。

每个指标工具参数统一为：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `code` | str | 是 | — | 股票代码 |
| `type` | str | 否 | "day" | K线类型：day/week/month/minute1/5/15/30/60 |
| `limit` | int | 否 | 100 | K线条数，默认100 |

#### 4a. indicator_ma (移动平均线)

返回 MA5/MA10/MA20/MA60。

```json
{
  "success": true,
  "data": {
    "ma5": [10.1, 10.2, ...],
    "ma10": [10.0, 10.1, ...],
    "ma20": [9.8, 9.9, ...],
    "ma60": [9.5, 9.6, ...],
    "dates": ["2024-01-01", ...]
  },
  "source": "mootdx2"
}
```

#### 4b. indicator_ema (指数移动平均线)

返回 EMA12/EMA26。

#### 4c. indicator_macd (MACD)

返回 dif/dea/macd（柱状图）。

```json
{
  "success": true,
  "data": {
    "dif": [...],
    "dea": [...],
    "macd": [...],
    "dates": [...]
  },
  "source": "mootdx2"
}
```

#### 4d. indicator_rsi (RSI)

返回 RSI6/RSI12/RSI24。

#### 4e. indicator_kdj (KDJ)

返回 K/D/J 值。

#### 4f. indicator_boll (BOLL)

返回 boll_upper/boll_mid/boll_lower。

#### 4g. indicator_atr (ATR)

返回 ATR 值（真实波幅）。

#### 4h. indicator_vol_ma (成交量均线)

返回 VOL_MA5/VOL_MA10（成交量简单移动平均）。

---

## 实现架构

### 依赖

- `pandas` — 用于指标计算（已有）
- `numpy` — 用于 EMA 等指数计算（需确认是否已安装）
- `get_kline` — 拉取原始 K 线数据

### 指标计算规则

| 指标 | 算法 | 参数 |
|------|------|------|
| MA | SMA，简单移动平均 | n=5/10/20/60 |
| EMA | 指数加权移动平均 | n=12/26 |
| MACD | EMA(c,12) - EMA(c,26), Signal=EMA(diff,9), Hist=diff-signal | 标准 |
| RSI | 100 - 100/(1+RS), RS=均值(涨)/均值(跌) | n=6/12/24 |
| KDJ | RSV=(C-LLV(L,9))/(HHV(H,9)-LLV(L,9))*100; K=D; J=3K-2D | 标准9日 |
| BOLL | 中轨=MA20, 上轨=MA20+2σ, 下轨=MA20-2σ | n=20, k=2 |
| ATR | Max(H-L, Max(|H-PC|, |L-PC|)) 的均值 | n=14 |
| VOL_MA | 成交量简单移动平均 | n=5/10 |

---

## 文件变更

| 文件 | 变更 |
|------|------|
| `src/mootdx2_client.py` | 添加 `get_index_overview`, `stock_top_board`, `stock_unusual` 方法 + 8个指标计算方法 + 注册到 `call_tool` |
| `config/tools_mootdx2.yaml` | 添加 12 个工具定义（Agent instruction manual 标准） |

---

## 测试策略

1. **单元测试**：每个指标用已知数据验证计算结果
2. **集成测试**：调用真实 API 验证 `get_index_overview` 和 `stock_top_board`
3. **边界测试**：`stock_top_board` 排序字段不存在、K 线条数不足计算均线时的降级处理
