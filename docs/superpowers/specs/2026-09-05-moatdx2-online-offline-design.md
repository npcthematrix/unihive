# MooTDX2 接口在线/离线分组设计方案

## 1. 背景

MooTDX2 封装了两套数据接口：
- **在线接口（Quotes）**：基于 TCP 网络连接通达信服务器，需要网络
- **离线接口（Reader）**：读取本地 `vipdoc` 目录二进制文件，不需要网络

需要在工具文档中清晰标注接口类型，便于 MCP 网关调用方理解各接口的网络依赖。

## 2. 分组标准

| 类型 | 数据来源 | 网络依赖 |
|------|----------|----------|
| **在线** | `mootdx2.quotes.Quotes` | 需要连接 TDX 服务器 |
| **离线** | `mootdx.reader.Reader` | 读本地文件，不需要网络 |

## 3. MooTDX2 接口分类

### 在线接口（Quotes）

| 工具名 | 说明 |
|--------|------|
| get_quote | 实时行情（五档） |
| get_kline | K线（日/周/月/分钟） |
| get_batch_quote | 批量行情 |
| get_minute_data | 今日分时 |
| get_trade | 逐笔成交 |
| get_trade_history | 历史逐笔 |
| get_index_kline | 指数K线 |
| get_index_bars | 指数K线（指定起止） |
| get_index_all | 全指K线 |
| get_code_list | 全量代码列表 |
| get_stock_codes | 股票代码列表 |
| get_etf_codes | ETF代码列表 |
| get_etf_list | ETF列表 |
| get_market_count | 市场证券数量 |
| get_workday | 交易日查询 |
| get_workday_range | 交易日范围 |
| get_income | 盘后收益 |
| get_block | 板块数据 |
| get_f10 | F10基础数据 |
| get_f10_company | F10公司概况 |
| get_xdxr | 除权除息数据 |

### 离线接口（Reader）

| 工具名 | 说明 |
|--------|------|
| get_k_data | K线数据（本地日线文件） |
| get_minutes | 分钟K线（本地1分钟文件） |
| search_stock | 搜索股票（本地block板块） |
| get_stock_info | 聚合股票信息（混合：快照在线+K线/分时离线） |

**注意**：`get_stock_info` 是聚合接口，包含在线（quote）和离线（kline/minute）两部分数据。

## 4. 配置

在 `config/upstreams.yaml` mootdx2 upstream 中新增 `tdxdir` 配置：

```yaml
mootdx2:
  enabled: true
  type: "mootdx2"
  market: "std"
  tdxdir: ""  # 通达信本地路径，空则自动探索
```

- `tdxdir`：通达信安装目录下的 `vipdoc` 目录路径
- 为空时：mootdx 库自动探测默认安装路径

## 5. 实现方式

### 5.1 配置文件修改

在 `config/upstreams.yaml` 的 tools 列表中，用分组注释标题分隔：

```yaml
  # === MooTDX2 在线接口 (21) ===
  - {name: get_quote, ...}
  - {name: get_kline, ...}
  ...

  # === MooTDX2 离线接口 (4) ===
  - {name: get_k_data, ...}
  - {name: get_minutes, ...}
  - {name: search_stock, ...}
  - {name: get_stock_info, ...}
```

### 5.2 代码修改

离线接口方法在 `MooTDX2Client` 中使用 `Reader.factory()` 调用：

```python
from mootdx.reader import Reader

def _get_k_data_offline(self, code: str, start_date: str = "", end_date: str = ""):
    reader = Reader.factory(market="std", tdxdir=self.config.settings.tdxdir)
    df = reader.daily(symbol=code)
    # 处理日期范围过滤
    return df.to_dict(orient="records")
```

### 5.3 tdxdir 配置传递

`MooTDX2Settings` 新增 `tdxdir` 字段，通过环境变量 `MOOTDX2_TDXDIR` 覆盖。

## 6. 兼容性承诺

- 分组仅为文档标注，不影响接口行为
- 新增接口默认归入在线组
- `get_stock_info` 作为混合接口，单独标注
