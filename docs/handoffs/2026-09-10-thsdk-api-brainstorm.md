# THSDK API 暴露方案 — 头脑风暴 2026-09-10

## TL;DR ✅ 已完成 (方案 C)

已添加 **27 个新工具**，全面覆盖 THSDK 能力。

---

## 背景

THSDK 暴露约 **60+** 个原生 API。本文档分析哪些适合对外发布为 MCP 工具。

## 已有实现 (12 个)

### 自选 Watchlist (12/12 已实现)

| 工具 | 说明 | 门控 |
|---|---|---|
| `ths_get_account_watchlist` | 获取主自选 | requires_env |
| `ths_get_account_watchlist_groups` | 获取分组列表 | requires_env |
| `ths_create_account_watchlist_group` | 创建分组 | confirm=true |
| `ths_delete_account_watchlist_group` | 删除分组 | confirm=true |
| `ths_rename_account_watchlist_group` | 重命名分组 | confirm=true |
| `ths_add_account_watchlist_group_securities` | 添加证券到分组 | requires_env |
| `ths_remove_account_watchlist_group_securities` | 从分组移除 | requires_env |
| `ths_replace_account_watchlist_group_securities` | 替换分组证券 | confirm=true |
| `ths_add_account_watchlist_securities` | 添加到主自选 | requires_env |
| `ths_remove_account_watchlist_securities` | 从主自选移除 | requires_env |
| `ths_replace_account_watchlist_securities` | 替换主自选 | confirm=true |
| `ths_clear_account_watchlist` | 清空主自选 | confirm=true |

---

## 新增工具 (27 个，方案 C 全面暴露)

### 板块 Blocks (6 个)

| 工具 | 说明 |
|---|---|
| `ths_list_block_descriptions` | 板块描述 |
| `ths_list_security_block_memberships` | 证券所属板块 |
| `ths_get_security_concept_tags` | 概念标签 |
| `ths_get_security_industry` | 行业分类 |
| `ths_list_industry_children` | 子行业列表 |
| `ths_rank_block_securities` | 板块内排序 |

### 问财 WenCai (3 个)

| 工具 | 说明 |
|---|---|
| `ths_query_wencai` | 问财查询 |
| `ths_query_wencai_securities` | 问财选股 |
| `ths_list_wencai_hot_blocks` | 问财热点板块 |

### 热度 Popularity (2 个)

| 工具 | 说明 |
|---|---|
| `ths_rank_securities_by_popularity` | 热度排行 |
| `ths_get_security_popularity_rank` | 个股热度 |

### 新闻 News (2 个)

| 工具 | 说明 |
|---|---|
| `ths_list_hot_event_news` | 热点事件新闻 |
| `ths_list_security_news_events` | 个股新闻事件 |

### 实时统计 Realtime (3 个)

| 工具 | 说明 |
|---|---|
| `ths_calculate_security_realtime_statistics` | 实时统计 |
| `ths_get_security_short_term_highlights` | 短线亮点 |
| `ths_list_security_price_volume_levels` | 量价分布 |

### 公司行为 Corporate (2 个)

| 工具 | 说明 |
|---|---|
| `ths_list_security_corporate_actions` | 公司行为 |
| `ths_list_security_financial_snapshots` | 财务快照 |

### 涨跌停 Limit (2 个)

| 工具 | 说明 |
|---|---|
| `ths_analyze_security_limit_up` | 涨停分析 |
| `ths_get_security_price_limit_events` | 涨跌停事件 |

### 跨市场 Cross-Market (3 个)

| 工具 | 说明 |
|---|---|
| `ths_list_futures_related_securities` | 期货关联证券 |
| `ths_list_security_ah_relations` | AH股关联 |
| `ths_list_security_futures_relations` | 期股关联 |

### 搜索 Search (2 个)

| 工具 | 说明 |
|---|---|
| `ths_search_securities` | 证券搜索 |
| `ths_resolve_securities` | 证券解析 |

### 行情增强 Quotes (5 个)

| 工具 | 说明 |
|---|---|
| `ths_list_security_ticks` | 逐笔成交 |
| `ths_list_security_intraday_bars` | 分时Bar |
| `ths_list_security_order_books` | 订单簿 |
| `ths_list_security_daily_capital_flows` | 每日资金流向 |
| `ths_get_market_security_names` | 证券名称列表 |

---

## 文件变更

- `config/tools_ths.yaml`: 添加 27 个新工具定义
- `src/api/thsdk_client.py`: 添加 27 个 `_tool_ths_*` 方法实现

---

## 待验证

- 启动 MCP 网关，确认所有新工具可调用
- 测试核心差异化能力（问财、热度、板块）
