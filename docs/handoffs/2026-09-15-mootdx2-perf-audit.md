# MOOTDX2 MCP Tool 性能审视

- **日期**: 2026-09-15
- **审视对象**: `src/unihive/api/mootdx2_client.py` (2632 lines, 34 tool), 配套 `mootdx2_pool.py` / `core/router.py` / `core/cache_strategy.py` / `config/tools_mootdx2.yaml`
- **方式**: 代码静态审视 (无 benchmark 运行)
- **延续参考**: 2026-09-15 `get_daily_local` L1 + single-flight P0 已落地 (memory: `project_get_daily_l1_observability_2026_09_15.md`); 二次审视对 fuyao/tdx_quant 已 audit 过 (memory: `project_mcp_second_pass_audit_2026_09_15.md`)

---

## 0. 摘要

| 维度 | 现状 |
|---|---|
| 工具总数 | 34 (24 在线 + 4 离线 + 6 复合) |
| 调用模板 | 100% `loop.run_in_executor(get_blocking_executor(), self._sync, ...)` (30+ 处) |
| L1 内存缓存 | 仅 `get_daily_local` (TTLCache + single-flight, 2026-09-15 P0) |
| L2 SQLite 缓存 | `cache_strategy.py` 显式禁止本地源 (policy 正确) |
| 已 audit 项 | 0 (mootdx2 是 4 个上游里**唯一**未做 perf 审视的) |

**核心结论**: MOOTDX2 路径有两个**架构级浪费**:
1. 33 个工具缺 L1 内存缓存 (行情类高频重复读) — P0 候选
2. 2 个全市场扫描 (`stock_top_board` / `stock_unusual`) 走 N 个 50 批量 roundtrip, 缺结果缓存 — P1 候选

---

## 1. 调用栈热点地图

```
GatewayServer.call_tool
└─ Router._call_with_timeout (asyncio.wait_for, 默认 10s/upstream)
   └─ MooTDX2Client.call_tool (catch 异常 → 降级 status)
      └─ get_XXX (37 处, 100% 同模板)
         └─ loop.run_in_executor(get_blocking_executor(), _sync, ...)
            └─ _XXX_sync (同步阻塞, 调 mootdx2 Quotes/Reader)
```

每调用 = 一次线程调度 (~10-100µs) + 一次 `asyncio.get_event_loop()` lookup (~5µs) + mootdx2 库调用 (1-50ms, 视网络/磁盘)。

---

## 2. HIGH 优先级

### H1 — 33/34 工具缺 L1 内存缓存 (只有 `get_daily_local` 有)

**位置**: `src/unihive/api/mootdx2_client.py:1704-1863` (仅 `get_daily` 一个走 L1); 其它 `get_quote/get_kline/get_batch_quote/get_minute_data/get_index_overview/get_workday/get_workday_range/...` 全部 0 缓存。

**问题**:
- 行情类工具 (`get_quote`, `get_minute_data`, `get_index_overview`, `get_batch_quote`) 是典型 "同一 symbol 短时间内多次查" 场景 (AI agent 多步分析时常触发)
- 离线工具 `get_daily_local` 已经 2026-09-15 ship L1, 但 `get_minute_local` / `get_fzline_local` / `get_index_kline_local` 同为本地文件读取, 完全没缓存
- `cache_strategy.py:90-92` 显式写 "本地终端/本地库本身获取足够快, 加缓存纯属多余" — 这个论断**对单次延迟成立**, 但**对热点不成立**: 高频重复读时 L1 命中能省 10-50ms 磁盘读取 / 5-100ms 网络往返

**影响**:
- AI agent 场景: "分析 600519 走势" → 调用 `get_quote` + `get_kline` + `get_minute_data` + `get_index_overview` (≥4 次网络往返), 顺序依赖时延
- `get_quote` 缺缓存意味着 "每查一次 → 走一次 TCP 5 档行情" 即使 1 秒前刚查过同一只股票

**建议 (参考已落地的 get_daily_local 模式)**:

| 工具 | TTL | L1 key 形状 | 优先级 |
|---|---|---|---|
| `get_quote` | 1-2s | `(code,)` | HIGH |
| `get_batch_quote` | 1-2s | `(tuple(sorted_codes),)` | HIGH |
| `get_minute_data` | 5s | `(code,)` | HIGH |
| `get_index_overview` | 5s | `()` | HIGH (固定 6 指数) |
| `get_kline` (day/week/month) | 600s | `(code, type, limit)` | MED |
| `get_index_kline_local` | 7200s | `(code,)` | MED (复用 L1) |
| `get_minute_local` | 7200s | `(code, suffix)` | MED |
| `get_fzline_local` | 7200s | `(code,)` | MED |
| `get_workday` / `get_workday_range` | 86400s | `(date, count)` | LOW |
| `get_index_bars` | 600s | `(symbol, frequency, start, offset)` | LOW |

**实现模板**: 抽取 `L1CacheHelper` 类 (cachetools.TTLCache + 单飞 dict + Lock), 让 L1 上线不再需要重复 boilerplate。

---

### H2 — `stock_top_board` / `stock_unusual` 全市场扫描 100+ 批量 roundtrip

**位置**: 
- `src/unihive/api/mootdx2_client.py:2534-2609` (`_stock_top_board_sync`)
- `src/unihive/api/mootdx2_client.py:2430-2532` (`_stock_unusual_sync`)

**问题**:
- `_stock_top_board_sync`: 遍历 SH + SZ 全量股票代码 (~5000+ 只), 按 50/批调 `q.quotes(symbols=...)` → 约 **100 次批量 roundtrip**, 每次 50-200ms → **5-20s** 总耗时 (已接近 10s upstream timeout 上限)
- `_stock_unusual_sync`: 取前 50 概念板块, 每板块取前 500 股票 → 同模式 ~10 次 roundtrip
- 每次调用都**重新跑**同样的全市场扫描; 即使是同一用户的连续两次"今日涨幅排行", 第二次也全量重跑

**影响**:
- 单次 latency 突破 router timeout (10s) → 上游降级, router 走 fallback 链 (`stock_top_board` 无 fallback → 直接失败)
- AI agent 场景: 用户问"今日涨停" → 5s+ → 再问"成交额排行" → 又 5s+ (结果几乎不变, 行情没动)

**建议**:
1. **短 TTL 结果缓存 (P0, 必须做)**: 加 L1 缓存 key=`(sort_by, direction, limit, market)`, TTL=10-30s
   - 行情 10s 内变化 < 0.5%, 用户感知不到差异
   - 同样的 10s 窗口内重复查询 → 0ms 命中
2. **拆分扫描并行化 (P1)**: 100 个 50-stock batch 当前是串行; 用 `asyncio.to_thread` + `asyncio.gather` 并发 4-8 批 → 总耗时降到 1-3s
3. **避免扫描 BJ**: bj 股票数 < 200, 但 `q.stock(exchange="bj")` 在 mootdx2 0.x 版本可能不支持; 当前实现根本没扫描 bj (`for exchange in ["sz", "sh"]` line 2549), 这是 correctness 问题 (memory 中已知 BJ 限流), 但与 perf 无关
4. **预扫快照文件**: mootdx2 的 `q.snapshot()` 一次返回全市场快照 (vs N 次 quotes(symbols=...)); 如果库支持, 单次调用 50-200ms 内能拿 5000 只股票 → 数量级加速

---

### H3 — `loop.run_in_executor` + `asyncio.get_event_loop()` 是 deprecated 模式

**位置**: `mootdx2_client.py` 30+ 处, 例:
- `get_quote` (line 845-846)
- `get_kline` (line 891-892)
- `get_batch_quote` (line 918-919)
- `get_batch_bars` (line 955-956)
- ... 所有 _sync 包装的方法都这样

**问题**:
```python
loop = asyncio.get_event_loop()                       # ① deprecated in 3.10+
data = await loop.run_in_executor(                    # ②
    get_blocking_executor(), self._sync, code, ...
)
```

- `asyncio.get_event_loop()` 从 Python 3.10 起被 deprecate, 推荐 `asyncio.get_running_loop()` 或直接 `asyncio.to_thread()`
- Python 3.12+ 在没有 running loop 的线程里调 `get_event_loop()` 会 emit `DeprecationWarning` 并最终 `RuntimeError`
- 每调用多一次 loop 查找 (~5-10µs) + 一次 dict 引用 (~1µs), 虽小但 37 处累计可观测

**建议 (P1, 一次性 refactor)**:
```python
# 新模板 (Python 3.9+)
data = await asyncio.to_thread(self._sync, code)
```
- `asyncio.to_thread()` 内部用 `loop.run_in_executor` + `get_running_loop()`, 是官方推荐
- 减 2 行代码 × 30+ 处 = -60 行
- 性能差异: < 5µs/调用, 但代码清洁度收益大

**兼容性**: `asyncio.to_thread()` 需要 Python 3.9+, 项目声明 3.10+ (`pyproject.toml`), 完全兼容。

**注意**: 这只是 "deprecation" 不是 bug, 不影响功能。**不要为了这个阻塞 P0 性能 fix**; 与 H1/H2 同 commit 一起做可以。

---

## 3. MED 优先级

### M1 — `get_stock_info` 3 次同步调用可并行

**位置**: `src/unihive/api/mootdx2_client.py:2319-2353` (`_get_stock_info_sync`)

**问题**: 顺序执行 `q.quotes()` + `reader.daily()` + `reader.minute()`, 总耗时 = sum of 3。
- 行情 (网络): 50-150ms
- 日线 (本地文件): 5-20ms
- 分时 (本地文件): 5-20ms
- 合计 ~60-190ms

**建议**: 改成 `asyncio.gather(self._q_quotes, self._r_daily, self._r_minute)`, 总耗时 = max(~150ms 网络)。
- 用户感知: "股票详情页" 加载从 100ms+ 降到 50ms+
- 收益 30-50% 单次延迟

---

### M2 — 股票代码标准化重复 14+ 处

**位置**:
- `_get_quote_sync` (line 529): `sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")`
- `_get_kline_sync` (line 857)
- `_get_batch_quote_sync` (line 908) — 列表推导式
- `_get_batch_bars_sync` (line 942) — 列表推导式
- `_get_minute_data_sync` (line 968)
- `_get_trade_sync` (line 990)
- `_get_trade_history_sync` (line 1013)
- `_get_income_sync` (line 1299)
- `_get_f10_sync` (line 1565)
- `_get_f10_company_sync` (line 1593)
- `_get_minutes_sync` (line 1620)
- `_get_minute_sync` (line 1878)
- `_get_fzline_sync` (line 1935)
- `_get_xdxr_sync` (line 2145)
- `_get_k_data_sync` (line 2194)
- `_stock_unusual_sync` (line 2472) — 列表推导式
- `_stock_top_board_sync` (line 2567) — 列表推导式
- `index_registry.py` 已有 `parse_symbol()` (strict 版本, 有 ValueError)

**问题**:
- 14 处 `code.lower().replace(...)` + 3 处列表推导式 = 17 处复制粘贴
- 这套"低质量清洗" 不做错误检查, 输入 `"sh6"` (缺 5 位) 会变成 `"6"`, 然后传给 mootdx2 失败 → **错误信息不可读** ("代码不存在")
- 真实用户输入形式多样: `sh600000`, `600000.SH`, `600000.SH`, `600000` (6 位)
- 与 `parse_symbol()` (strict) 并存, 两套规则容易漂移

**建议 (P1 refactor)**:
- 引入 `_normalize_code_loose(code: str) -> str` 单一工具 (放 index_registry 或 util)
- 所有 `_sync` 函数调它, 替换 17 处
- 与 `parse_symbol()` 的区别: 前者仅 strip prefix, 后者解析 kind/market 并 raise
- 行为不变 (输入输出不变), 仅去重 + 后续好维护

**不要做的事**: 不要把所有 `_sync` 改用 `parse_symbol()`, 因为行情工具对 "sh600000" / "600000.SH" / "600000" 都应宽容接受 (返回行情), 不是 raise。`get_daily_local` 用 `parse_symbol` 是因为后续要按 kind/market 走不同 IO 路径, 行情工具不需要。

---

### M3 — `search_stock` 用 `iterrows()` 而非向量化

**位置**: `src/unihive/api/mootdx2_client.py:2234-2255` (`_search_stock_sync` 走 `reader.block()` 分支)

**问题**:
```python
for _, row in df_block.iterrows():   # ← N 行 Python 循环
    code = str(row.get("code", ""))
    name = row.get("name", "")
    ...
    if kw in code or kw in name.lower():
        ...
```

- pandas `iterrows()` 单行 ~100-500µs, ~5000 只股票 → 0.5-2.5s
- 应改向量化: `df_block[df_block["code"].str.contains(kw, case=False) | df_block["name"].str.contains(kw, case=False)]` → ~5-10ms

**建议 (P1, 单点 fix)**:
```python
mask = (
    df_block["code"].astype(str).str.contains(kw, case=False, na=False)
    | df_block["name"].astype(str).str.contains(kw, case=False, na=False)
)
return [
    {"code": str(r["code"]), "name": r["name"], "market": _infer_market(r["code"])}
    for r in df_block[mask].head(20).to_dict("records")
]
```
- 单次 latency 0.5-2.5s → 5-10ms (50-500x 加速)
- fallback 到 SQLite 路径**已经用了 LIKE %, OK**

---

### M4 — `_scan_blk_files_sync` 每次调用读所有 .blk 文件

**位置**: `src/unihive/api/mootdx2_client.py:1319-1390` (`_scan_blk_files_sync`), 间接被 `get_custom_sector_list` 调

**问题**:
- `blocknew_dir.glob("*.blk")` + 每个文件 `read_text(encoding="gbk")` 全量读 → N 文件 × 每文件 ~10-50ms = 50-500ms
- 自定义板块变更频率极低 (用户偶尔加分组), 但每次调用都全量扫
- AI agent 场景: "我有几个分组" 多次问 → 每次都扫盘

**建议 (P2)**:
- 加 L1 缓存 key=`()` (无参数), TTL=60s (板块变更低频但非零)
- 或加文件 mtime 缓存: `(mtime_of_dir,)` 作为 cache key, 文件没变就不重读

---

### M5 — `get_daily` 缓存命中路径仍走 `parse_symbol()` 两次

**位置**: `src/unihive/api/mootdx2_client.py:1736` 和 `_get_daily_sync` 内 line 1666 (重新调 `parse_symbol`)

**问题**:
```python
# 入口 line 1736:
parsed_for_key = parse_symbol(code)            # ① 用于 cache_key
...
# 命中后 line 1800 调用 _get_daily_sync
# _get_daily_sync line 1666 内部又调一次:
parsed = parse_symbol(code)                    # ② 用于 IO 路径
```

- L1 命中时 `_get_daily_sync` 不被调用 (走 line 1755 的 filtered 分支), 这部分没问题
- L1 miss 时 `parse_symbol(code)` 被调用 **2 次** (一次在 `get_daily` 入口, 一次在 `_get_daily_sync` 内)
- 每次 `parse_symbol` ~10-30µs (含正则), 单次 2 次调用 20-60µs, 微优化但**容易修**

**建议 (P3, 顺手做)**:
- `get_daily` 解析后把 `parsed_for_key` 传给 `_get_daily_sync(code, parsed, adjust, start, end)`
- 或 `_get_daily_sync` 接受预先解析的 `parsed`

---

### M6 — `_indicator_atr_sync` 用 Python 列表 ATR 计算

**位置**: `src/unihive/api/mootdx2_client.py:2376-2410`

**问题**:
- 调 `_get_kline_sync` 拉 100 条 K 线 (走 network/disk), 然后**没缓存** kline 结果
- ATR 用 2 个 for 循环 + 手动累积 — Python 列表, ~100 行循环 ~50-200µs
- 同 symbol 重复查询 ATR (AI agent "分析波动率" 多次) → 每次重拉 K 线

**建议 (P2)**:
- 短期: 给 `_get_kline_sync` 加 L1 (K线 day/week/month TTL=600s, 见 H1)
- 长期: ATR 算法改 pandas `rolling(14).mean()` 或 numpy 向量化 (~10x 加速单次计算)
- **优先级**: 不高, ATR 不是高频工具

---

## 4. LOW 优先级

### L1 — `mootdx2_pool.py` `ConnectionPool` 和 `RetryHelper` 是死代码

**位置**: `src/unihive/api/mootdx2_pool.py:39-246` 整体

**问题**:
- `ConnectionPool` 定义了 `_pools`, `get_connection`, `_health_check`, 但 `MooTDX2Client` 用的是自己的 `_get_quotes()` 单例 (line 481-524), `pool` 参数只是 dataclass 字段 (line 113), 实际从未使用
- `RetryHelper` 也没人调 (`execute_with_retry` 0 caller)
- `get_stats()` 在 `get_server_status` 里调, 但 `pool` 默认 None, 走 `{}` 兜底
- 但**MooTDX2Config.pool** 字段还在 (line 113), `get_metrics()` 也调 `pool.get_stats()` (line 399), 整套是"看着像连接池实际是单 Quotes 实例"

**影响**:
- 代码冗余, 误导后续维护者
- 没有真正的连接池 (只有 1 个 Quotes 实例复用, 没多连接)
- 没有真正的重试 (`RetryHelper` 未接)

**建议 (P3, 文档/清理)**:
- 选项 A: 彻底删除 `ConnectionPool` / `RetryHelper`, 简化 `MooTDX2Config` 去掉 `pool` 字段
- 选项 B: 留下类但加 docstring "当前未启用, 由 _get_quotes() 单例取代", 避免误导
- 选项 C: 真正实现: 多 Quotes 实例 round-robin + RetryHelper 接入 call_tool 异常路径

**当前优先级**: LOW, 不阻塞 P0。建议合并到一个 cleanup PR (跟 M2 去重一起)。

---

### L2 — `_metrics["total_requests"] += 1` 在 30+ 处非原子累计

**位置**: `mootdx2_client.py:138-146` 注释说 "total_requests 单 int += 是 GIL 原子的, 不需要 lock" — **正确**

**问题**: 注释正确, 但 `_metrics["total_requests"]` 是 `dict[k] += 1` 而非纯 `int += 1`, dict 索引 + 写回在 GIL 下也是原子的 (CPython 字节码层面), 但**不可移植到 PyPy / nogil Python (3.13t)**。

**建议 (P3)**: 长期若迁移 Python 3.13t, 改成 `self._total_requests: int = 0` 独立字段 + `self._metrics_lock` 保护。当前无需动。

---

### L3 — `_get_quote_sync` 行情校验后丢失原 `vol` 字段

**位置**: `src/unihive/api/mootdx2_client.py:526-542`

**问题**: 单行情返回只 6 个字段 (`symbol`, `close`, `open`, `high`, `low`, `volume`); mootdx2 `q.quotes()` 返回 ~20 字段, 其余 (换手率/市盈率/流通市值等) 被丢弃。

**影响**: 功能缺失, 不是 perf 问题。仅作相关: AI agent 问"换手率"需要换 `get_f10`, 多一次调用。

**建议**: 把 `to_dict(orient="records")` 整行返回, 让调用方选字段。**不在本次 perf 范围**, 留作功能扩展。

---

### L4 — `get_kline` 数据校验 100% 用 `iterrows` 等价物

**位置**: `src/unihive/api/mootdx2_client.py:895`: `valid_data = [r for r in data if _validate_kline_record(r)]`

**问题**: 100-1000 行 Python 列表推导 + 每行 5 个 float 转换 + 比较 → ~0.5-2ms
- 对比 pandas 向量化校验 ~0.1ms
- 收益小, 可不动

---

### L5 — `asyncio.get_event_loop()` 在 stop 路径不存在

**位置**: `src/unihive/api/mootdx2_client.py:741`: `await asyncio.to_thread(close_method)`

**问题**: `stop()` 里 `Quotes.close` 走 `asyncio.to_thread`, 这是正确的 (用 `to_thread` 而非 `run_in_executor`)。但其它 `_sync` 包装全部用 `loop.run_in_executor`, 风格不一致。

**建议**: 跟 H3 一起统一。

---

## 5. Quick Wins vs 较大改动

### Quick Wins (1-2 小时, P0/P1)

1. **H2-a**: `stock_top_board` / `stock_unusual` 加 10s 结果缓存 → 单次 5-20s → 0ms (命中) / 5-20s (miss)
2. **H3**: 30+ 处 `loop.run_in_executor` → `asyncio.to_thread` → 代码清洁度
3. **M3**: `search_stock` `iterrows` → 向量化 → 0.5-2.5s → 5-10ms
4. **M2**: 17 处代码标准化去重 → 维护性

### 中等 (半天)

5. **H1 部分**: `get_quote` / `get_minute_data` / `get_index_overview` / `get_batch_quote` L1 缓存 (复用 `get_daily_local` 模板)
6. **M1**: `get_stock_info` 3 路并行 → 总耗时降到 max 而非 sum
7. **H2-b**: `stock_top_board` 扫描拆 `asyncio.gather` → 5-20s → 1-3s

### 较大 (1-2 天)

8. **H1 完整**: 抽 `L1CacheHelper` 通用类, 给 33 个工具都加上 (按上表分类 TTL)
9. **H2-c**: 调研 mootdx2 是否支持全市场 snapshot 一次拉取
10. **L1**: 决定 ConnectionPool 命运 (删/留/真做)

---

## 6. 不在本次审计范围 (已知)

- **mootdx2 库本身的性能** (vs tdx_quant 库): 不在本 PR 范围
- **MCP 协议层** (FastMCP 调用开销): 已知 ~1ms/tool, 与本次无关
- **SQLite cache 层** (`storage/cache.py`): 本地源不缓存是 policy, 不是 bug
- **Router 行为** (`core/router.py`): 之前已 audit (memory: `project_mcp_second_pass_audit_2026_09_15.md`)
- **TDX 服务器网络抖动 / failover**: 由 `_pin_quotes_servers` + `auto_switch=1` 处理, 性能 OK

---

## 7. 推荐落地顺序

```
批次 1 (P0, 1-2h, 立即做, ROI 高):
  - H2-a (stock_top_board / stock_unusual 10s L1)
  - M3 (search_stock 向量化)
  - H3 (loop.run_in_executor → asyncio.to_thread)

批次 2 (P1, 半天, 接着做):
  - H1 高频子集 (get_quote / get_minute_data / get_index_overview / get_batch_quote)
  - M1 (get_stock_info 3 路并行)
  - M2 (代码标准化去重)

批次 3 (P2, 1-2 天):
  - H1 完整覆盖 (抽 L1CacheHelper)
  - H2-b (扫描并行化)
  - M4 / M5 / M6

批次 4 (P3, 顺手清理):
  - L1 (ConnectionPool 命运)
  - L2 (nogil 兼容预留)
  - L3 / L4 / L5 (低优先级)
```

---

## 8. 兼容性 / 风险

- **L1 缓存一致性**: `get_quote` 1-2s TTL 期间, 用户可能拿到 "略旧" 行情 — AI agent 场景可接受 (同一对话上下文中差别微小); 真实交易路径在 `tdx_quant` 不在 mootdx2, 无影响
- **`stock_top_board` 10s 缓存**: 行情类排行, 10s 内涨跌排名变化 < 5 名, 用户感知不到; 但若 agent 在缓存窗口内做"实时监控"会拿到旧数据 — 文档里加一行"本工具结果最多延迟 10s"
- **兼容性**: `asyncio.to_thread()` 需 Python 3.9+, 项目 pyproject.toml 声明 3.10+, 安全
- **回滚**: 所有缓存加 metrics (`_daily_l1_hits`/`_daily_l1_misses` 模式), 命中率 < 30% 时考虑关闭

---

## 9. 建议后续

1. 给 `get_daily_local` 同款 `get_metrics()` 已包含 `daily_l1` 块 (line 378-400); L1 加到其它工具时复用同一 metrics 结构
2. 控制台 `/l1-cache` 页面 (memory: `project_get_daily_l1_observability_2026_09_15.md`) 已展示 `daily_l1` 块, 后续加 cache 类别时直接复用 (panel 已在 9.14 PR 已 ship, 看 `project_fuyao_cache_panel_audit.md`)
3. 与 memory `project_mcp_perf_optimization_2026_09_11.md` 的 "mootdx2_pool 后台改按需" 链接: 当前审视发现 pool 是 dead code, 与那条 memory 互相印证, 建议合并到一个 cleanup PR