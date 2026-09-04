# TokenWave TDX MCP Tools 设计

## 背景

基于 mootdx Python 库创建 MCP 工具，与 tdx-mcp-server (stdio)、tdx_tq_local (HTTP JSON-RPC) 并列，提供第三个数据源选择。

## 架构

### 集成方式
- 新增 `type: python` 支持，直接在网关进程内调用 mootdx
- 在 `src/tokenwave_tdx_client.py` 中实现，模式 `fuyao_client.py`
- 网关优先级：tdx_local → tokenwave_tdx → tdx_tq_local → fuyao_*

### 命名策略
- 工具命名与现有网关统一（如 `get_realtime_quote`, `get_kline`）
- 调用方无需关心数据源，网关按优先级自动选择

## 工具设计

### 1. get_realtime_quote
- **覆盖**: 实时盘口、五档行情
- **模式**: local 优先，network 兜底
- **参数**:
  - `stock_code` (str, required): 股票代码，如 "600519"
- **返回**: 实时行情数据

### 2. get_kline
- **覆盖**: K线历史 (日/周/月)
- **模式**: local 优先，network 兜底
- **参数**:
  - `stock_code` (str, required): 股票代码
  - `frequency` (str, optional): "daily" | "weekly" | "monthly"，默认 "daily"
  - `start_date` (str, optional): 开始日期 YYYYMMDD
  - `end_date` (str, optional): 结束日期 YYYYMMDD
  - `count` (int, optional): 返回条数
- **返回**: K线数据

### 3. get_minute_bar
- **覆盖**: 分钟K线
- **模式**: local 优先，network 兜底
- **参数**:
  - `stock_code` (str, required): 股票代码
  - `frequency` (str, optional): "1min" | "5min" | "15min" | "30min" | "60min"，默认 "5min"
- **返回**: 分钟K线数据

### 4. get_financial_data
- **覆盖**: 财务数据
- **模式**: network only (本地不支持)
- **参数**:
  - `stock_code` (str, required): 股票代码
  - `report_type` (str, optional): "income" | "balance" | "cashflow"，默认 "income"
  - `count` (int, optional): 返回期数，默认 4
- **返回**: 财务数据

### 5. get_block_data
- **覆盖**: 板块/概念数据
- **模式**: network only
- **参数**:
  - `block_type` (str, required): "industry" | "concept"
- **返回**: 板块/概念列表及成分股

### 6. get_stock_info
- **覆盖**: 股票基本信息
- **模式**: local 优先，network 兜底
- **参数**:
  - `stock_code` (str, required): 股票代码
- **返回**: 股票基本信息

### 7. get_trade_dates
- **覆盖**: 交易日历
- **模式**: local 优先，network 兜底
- **参数**:
  - `start_date` (str, required): 开始日期
  - `end_date` (str, required): 结束日期
- **返回**: 交易日列表

### 8. get_etf_list
- **覆盖**: ETF 列表
- **模式**: local 优先，network 兜底
- **参数**: 无
- **返回**: ETF 列表

## 返回格式

所有工具返回统一结构：

```json
{
  "success": true,
  "data": {...},
  "meta": {
    "mode": "local|network",
    "request_time": "2026-09-04T12:00:00Z",
    "data_time": "2026-09-04T11:30:00Z",
    "local_last_updated": "2026-09-03"
  }
}
```

- `mode`: 数据来源模式
- `request_time`: 请求时间
- `data_time`: 数据时间
- `local_last_updated`: 仅 local 模式有，表示本地数据最后更新时间

## 错误处理

| 场景 | 返回 |
|------|------|
| 本机未安装通达信 | `{success: false, error: "本地数据不可用，请确认通达信已安装"}` |
| 非交易日无实时数据 | `{success: true, data: {...}, meta: {status: "closed"}}` |
| 网络超时 | 自动重试 3 次，超限后返回 `{success: false, error: "网络请求失败"}` |
| 股票代码不存在 | `{success: false, error: "股票代码不存在"}` |

## 实现计划

### Phase 1: 核心封装
- 创建 `src/tokenwave_tdx_client.py`
- 实现 LocalClient 和 NetworkClient
- 封装 8 个工具方法

### Phase 2: 网关集成
- 修改 `gateway_server.py` 支持 `type: python`
- 新增 upstream 配置支持

### Phase 3: 配置更新
- 更新 `config/upstreams.yaml`
- 添加 tokenwave_tdx upstream

## 验收标准

- [ ] 8 个工具全部可用
- [ ] local/network 模式自动切换
- [ ] 返回结构包含 meta 信息
- [ ] 错误场景返回结构化错误
- [ ] 与现有工具统一命名，无冲突
