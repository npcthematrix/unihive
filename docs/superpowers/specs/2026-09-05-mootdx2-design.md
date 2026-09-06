# MooTDX2 MCP 接口设计

## 目标

用 mootdx2 替换现有的 tdx_local 上游，统一使用 mootdx2 库提供数据服务。

## 命名规范

参考现有 tdx_local 接口命名，使用 `get_` 前缀。

## 接口列表

| 工具名 | 功能 | mootdx2 对应接口 |
|--------|------|------------------|
| `get_quote` | 实时行情 | `Quotes.quotes()` |
| `get_batch_quote` | 批量行情 | `Quotes.quotes()` |
| `get_kline` | K线 | `Quotes.bars()` |
| `get_kline_history` | 历史K线 | `Quotes.bars()` |
| `get_kline_all` | 全量K线 | `Quotes.bars()` |
| `get_index_kline` | 指数K线 | `Quotes.index()` |
| `get_minute_data` | 今日分时 | `Quotes.minute()` |
| `get_trade` | 逐笔成交 | `Quotes.transaction()` |
| `get_trade_history` | 历史逐笔 | `Quotes.transactions()` |
| `get_code_list` | 全量代码列表 | `Quotes.stock_count()` |
| `get_stock_codes` | 股票代码(分页) | 本地实现 |
| `get_etf_codes` | ETF代码列表 | 本地实现 |
| `get_etf_list` | ETF列表 | 本地实现 |
| `get_market_count` | 市场证券数量 | `Quotes.stock_count()` |
| `get_workday` | 交易日查询 | 本地实现 |
| `get_workday_range` | 交易日范围 | 本地实现 |
| `get_index_all` | 全指K线(含成分股) | `Quotes.index()` |
| `get_income` | 盘后收益 | `Quotes.income()` |

## 参数映射

### 股票代码
- 参数名: `code`
- normalize: `code` (自动转为 sh/sz 前缀)

### K线类型 (type)
| 值 | 说明 |
|----|------|
| `day` | 日线 |
| `week` | 周线 |
| `month` | 月线 |
| `minute1` | 1分钟 |
| `minute5` | 5分钟 |
| `minute15` | 15分钟 |
| `minute30` | 30分钟 |
| `minute60` | 60分钟 |

### frequency 映射 (mootdx2)
| type | frequency |
|------|-----------|
| day | 9 |
| week | 5 |
| month | 6 |
| minute1 | 8 |
| minute5 | 0 |
| minute15 | 1 |
| minute30 | 2 |
| minute60 | 3 |

## 实现方式

1. 新建 `src/mootdx2_client.py`
2. 使用 `mootdx2.quotes.Quotes` (在线模式)
3. 配置新增 upstream: `mootdx2`
4. 移除 tdx_local 上游配置

## 数据格式

返回格式统一为:
```json
{
  "success": true,
  "data": [...],
  "source": "mootdx2"
}
```
