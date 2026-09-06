# Fuyao API 参数说明

> 来源：[同花顺金融数据 API](https://fuyao.aicubes.cn/llms-full.txt)
> 本文档补充 `config/upstreams.yaml` 中 Fuyao 接口的参数语义说明，供 AI 调用时参考。

---

## 目录

- [行情数据](#行情数据)
- [特色数据（涨跌停/热榜/龙虎榜）](#特色数据)
- [财务报表](#财务报表)
- [指数数据](#指数数据)
- [集合竞价](#集合竞价)
- [除复权因子](#除复权因子)
- [标的检索与列表](#标的检索与列表)

---

## 行情数据

### `a_share_prices_snapshot` — A 股行情快照

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `thscodes` | string | 否 | 逗号分隔的 thscode 列表，如 `600519.SH,000001.SZ`。给定时忽略分页参数，批量取数。 | 遍历全市场分页返回 |
| `limit` | integer | 否 | 分页大小，仅在省略 `thscodes` 时生效。 | `100` |
| `offset` | integer | 否 | 分页偏移，仅在省略 `thscodes` 时生效。 | `0` |

### `a_share_prices_historical` — A 股历史 K 线

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `thscode` | string | **是** | 单只标的 thscode，**不支持逗号**，多标的请分次请求。如 `600519.SH`。 | — |
| `start_date` | string | 否 | 起始日期，格式 `yyyy-MM-dd`。与 `end_date` 配对使用。 | — |
| `end_date` | string | 否 | 结束日期，格式 `yyyy-MM-dd`。与 `start_date` 配对使用。 | — |
| `adjust` | string | 否 | 复权方式：`none`（不复权）/ `forward`（前复权）/ `backward`（后复权）。 | `forward` |
| `interval` | string | 否 | K 线周期：`1d`（日）/ `1w`（周）/ `1M`（月）/ `5m`/`15m`/`30m`/`60m`（分钟）。 | `1d` |

---

## 特色数据

> 面向盘面复盘和短线情绪分析。

### `a_share_special_data_skyrocket_list` — A 股飙升榜（火箭榜）

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `period` | enum | 否 | 榜单周期：`day` 日榜 / `hour` 小时榜。 | `day` |

**说明**：`period=day` 返回日榜，`period=hour` 返回小时榜。省略时默认日榜。

### `a_share_special_data_hot_stock_list` — A 股热股榜单

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `period` | enum | 否 | 榜单周期：`day` 24 小时级别 / `hour` 小时级别。 | `day` |

### `a_share_special_data_hot_stock_list_history` — 历史热股排行

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `date` | string | **是** | 目标自然日，格式 `yyyy-MM-dd`；只支持一年内数据。 | — |

### `a_share_special_data_hot_stock_rank_trend` — 个股排名走势

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `thscode` | string | **是** | 单只 A 股标的，含交易所后缀，如 `300034.SZ`。 | — |
| `start_date` | string | **是** | 起始自然日，格式 `yyyy-MM-dd`。 | — |
| `end_date` | string | **是** | 结束自然日，需 `>= start_date`。 | — |

### `a_share_special_data_limit_up_pool` — 涨停股票池

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `date_ms` | long | 否 | 交易日 Unix 毫秒戳（Asia/Shanghai 00:00:00）；省略时默认当天。 | 当前自然日 |
| `page` | integer | 否 | 页码，从 1 开始。 | `1` |
| `size` | integer | 否 | 分页大小，范围 `1~200`。 | `50` |
| `sort_field` | enum | 否 | 排序字段：`last_price` / `continue_day_cnt` / `seal_money` / `limit_up_time`。 | `last_price` |
| `sort_dir` | enum | 否 | 排序方向：`asc` / `desc`。 | `desc` |

### `a_share_special_data_limit_down_pool` — 跌停股票池

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `date_ms` | long | 否 | 交易日毫秒时间戳。 | 当前自然日 |
| `page` | integer | 否 | 页码，从 1 开始。 | `1` |
| `size` | integer | 否 | 分页大小，范围 `1~200`。 | `50` |
| `sort_field` | enum | 否 | 排序字段：`last_limit_time` / `first_limit_time` / `last_price` / `price_change_ratio_pct` / `turnover_ratio_pct`。 | `last_limit_time` |
| `sort_dir` | enum | 否 | 排序方向：`asc` / `desc`。 | `desc` |

### `a_share_special_data_limit_break_pool` — 炸板股票池

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `date_ms` | long | 否 | 交易日毫秒时间戳。 | 当前自然日 |
| `page` | integer | 否 | 页码，从 1 开始。 | `1` |
| `size` | integer | 否 | 分页大小，范围 `1~200`。 | `50` |
| `sort_field` | enum | 否 | 排序字段：`price_change_ratio_pct` / `open_times` / `last_price` / `turnover_ratio_pct` / `turnover`。 | `price_change_ratio_pct` |
| `sort_dir` | enum | 否 | 排序方向：`asc` / `desc`。 | `desc` |

### `a_share_special_data_limit_up_ladder` — 连板天梯

无入参。返回近 30 个交易日的连板梯队矩阵（每个板位最多 4 只）。

### `a_share_special_data_dragon_tiger_list` — 龙虎榜榜单

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `board_type` | enum | 否 | 榜单类型：`all` 全部 / `org` 机构榜 / `hot_money` 游资榜。 | `all` |
| `date` | string | 否 | 目标交易日，格式 `yyyy-MM-dd`；省略时默认最近可用交易日。 | 最近可用交易日 |

---

## 财务报表

> 三个接口共用参数契约：利润表 / 资产负债表 / 现金流量表。

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `thscode` | string | **是** | 单只标的 thscode，**不支持逗号**。如 `600519.SH` / `000858.SZ` / `430047.BJ`。 | — |
| `period` | enum | **是** | 报告期类型：`annual`（仅年报）/ `quarterly`（每个季度末）。 | `annual` |
| `limit` | integer | 否 | 最近 N 期模式，范围 `[1, 20]`；**与 `start`/`end` 互斥**。 | `4` |
| `start` | long | 否 | 时间区间模式起始毫秒戳，需与 `end` 同传；窗口不超过 10 年。 | — |
| `end` | long | 否 | 时间区间模式结束毫秒戳，需 `end >= start`。 | — |

**取数模式**（二选一，互斥）：

- **最近 N 期**：不传 `start`/`end`，返回最近 `limit` 期，按 `period_end` 降序。
- **时间区间**：同时传 `start` + `end`，返回闭区间内全部报告期。

---

## 指数数据

### `a_share_index_catalog` — 同花顺指数目录

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `tag` | string | 否 | 标签白名单：`cn_concept`（A 股概念）/ `region`（区域指数）/ `tszs`（特色指数）/ `industry`（行业指数）。大小写不敏感。 | `cn_concept` |

### `a_share_index_constituents` — 指数成分股

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `thscode` | string | **是** | 指数 thscode，如 `886042.TI`（同花顺概念）或 `000300.SH`（沪深 300）。**不支持逗号**。 | — |

### `a_share_index_prices_snapshot` — 指数行情快照

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `thscodes` | string | **是** | 逗号分隔的指数 thscode 列表，如 `000001.SH,399001.SZ,886042.TI`。 | — |

---

## 集合竞价

### `a_share_auction_snapshot` — A 股集合竞价快照

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `thscodes` | string | 否 | 逗号分隔的 thscode 列表，如 `600519.SH,000001.SZ`。省略时返回全市场。 | 全市场 |

### `a_share_auction_short_term_benchmark` — 短线风向标竞价基准

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `date` | string | 否 | 目标日期，格式 `yyyy-MM-dd`；省略时默认当天。 | 当天 |

---

## 除复权因子

### `a_share_corporate_actions_adjustment_factors` — 复权因子

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `thscode` | string | **是** | 单只标的 thscode，**不支持逗号**。 | — |

返回现金分红（`dividend_per_share`）、送股比例（`per_share_bonus`）原始事件流，供客户端自行推导复权因子。

---

## 标的检索与列表

### `meta_tickers_list` — 标的列表获取

| 参数 | 类型 | 必填 | 说明 | 默认值 |
|------|------|------|------|--------|
| `type` | string | 否 | 资产类型，支持单值或逗号分隔多值。可选值：`a-share`（A 股）/ `a-share-index`（指数）/ `fund-otc`（场外公募）/ `fund-etf`（ETF）/ `fund-lof`（LOF）。省略时返回全部类型。 | 全部类型 |
| `exchange` | string | 否 | 交易所过滤：`SH` / `SZ` / `BJ`。省略时返回全部交易所。 | 全部 |
| `limit` | integer | 否 | 单页条数，最大 `10000`。 | `1000` |
| `offset` | integer | 否 | 分页偏移。 | `0` |

---

## 通用说明

### thscode 格式

富乐接口使用带交易所后缀的标准代码格式：`{ticker}.{exchange}`

| 交易所 | 后缀 | 示例 |
|--------|------|------|
| 上海 | `.SH` | `600519.SH` |
| 深圳 | `.SZ` | `000001.SZ` |
| 北京 | `.BJ` | `430047.BJ` |
| 同花顺指数 | `.TI` | `886042.TI` |

### 时间格式

- **自然日**：`yyyy-MM-dd`，如 `2026-06-21`
- **毫秒时间戳**：Unix 毫秒，如 `1748102400000`
- **日期时间**：统一使用 `Asia/Shanghai` 时区

### 错误码

| code | 含义 |
|------|------|
| `1001` | 缺少必填参数 |
| `1002` | 参数格式错误（如日期格式、thscode 含逗号） |
| `1003` | 参数取值越界（如 limit <= 0、超过接口上限） |
| `1004` | 参数冲突（如同时传 start/end 与 limit） |
| `2001` | API Key 缺失或无效 |
