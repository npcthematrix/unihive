# MOOTDX2 Round-4 Performance Audit (2026-09-28)

## 范围

- 11 个 L1 slot (`_l1_daily`, `_l1_top_board`, `_l1_unusual`, `_l1_minute`, `_l1_fzline`, `_l1_index_kline`, `_l1_index_minute`, `_l1_index_fzline`, `_l1_search`, `_l1_indicator_atr`, `_l1_block_scan`)
- TTL / maxsize 配置
- cache_key 基数风险
- Reader gate 交互
- get_metrics 线程安全

---

## 一、CRITICAL (0)

无 CRITICAL 发现。Batch 2b 已修复 C1/C2/H4-silent，核心缓存合同已就位。

---

## 二、HIGH (3)

### H1. `get_minute` / `get_index_minute` cache_key 日期窗基数爆炸

| 文件 | 行号 | 问题 |
|---|---|---|
| `mootdx2_client.py` | 2738 | `get_minute` key: `(suffix, code, start_date, end_date)` |
| `mootdx2_client.py` | 3178 | `get_index_minute_local` key: `(code, suffix, start_date, end_date)` |

**现状**:
- 用户场景: 同一股票 `get_minute(code="sh600519", start_date="2026-09-01")` 和 `get_minute(code="sh600519", start_date="2026-09-15")` 视为不同 key
- TDX 本地文件固定内容, 日期窗是**读取后过滤**, 不影响文件内容
- maxsize=256, 假设 50 热点股 × 5 常见日期窗 = 250 entries → **易触发 LRU 淘汰**

**量化**:
- 单只股票分钟线 ~240KB (.lc1 全量)
- 256 maxsize × 240KB ≈ **61MB** 理论上限
- 但实际 key 组合远超 256: 50 symbols × (start_date∈{ "", "2026-09-01", "2026-09-15", "2026-09-20", "2026-09-25" }) × (end_date∈{ "", "2026-09-28" }) = 500+ combinations

**影响**:
- 高频调用的股票 L1 命中率 < 50%, 重复触发 Reader
- 与 daily L1 的 H2-perf 同一问题: 日期窗应命中后过滤, 不应作为 key 组成部分

**建议**:
- 与 daily L1 双层方案一致: key 只含 `(suffix, code)`, 命中后用 `_filter_records_by_date` (现有实现 line 648-663) 过滤
- 或改为 `(suffix, code, today)` 日级 key, 兼容日内请求共享同一缓存

**Cross-ref**: Round-3 H2-perf (daily L1 key 内存放大)

---

### H2. `indicator_atr` cache_key 含 limit ∈ [1, 1000] 组合爆炸

| 文件 | 行号 | 问题 |
|---|---|---|
| `mootdx2_client.py` | 3676 | `indicator_atr` key: `(code, type, limit)` |

**现状**:
- limit 入 key, 1000 股票 × 8 类型 × 1000 limit = **800 万组合**
- maxsize 仅 64, 几乎必然 LRU 淘汰

**量化**:
- 单 ATR 计算结果 ~1KB (dict 含 atr/dates 两列表)
- 64 maxsize × 1KB ≈ **64KB** 内存, 本应够用
- 但 key 组合爆炸导致命中率 ≈ 0%

**影响**:
- L1 完全失效, 每次请求重算 ATR (Python 循环 ~30ms vs pandas ~30µs, Round-3 M-perf5)

**建议**:
- 移除 limit 从 key: `(code, type)` → 命中后按 limit 切片返回
- 与 index_kline / index_fzline 的 limit 处理策略一致 (Round-3 Batch 2b 已落地该模式)

**Cross-ref**: Round-3 M-perf5 (ATR Python 循环)

---

### H3. Reader 闸门 4 vs 池 8 错配 (Round-3 H3-perf 遗留)

| 文件 | 行号 | 问题 |
|---|---|---|
| `mootdx2_client.py` | 492 | `_READER_SEMAPHORE_LIMIT = 4` |
| `blocking_pool.py` | 31 | `_MOOTDX2_DEFAULT_SIZE = 8` |

**现状**:
- 线程池 8 worker, 但信号量钉死 4 并发 Reader
- **4 worker 永远闲置** (50% 浪费)

**量化**:
- mootdx2 pool 8 worker, 每 worker ~10ms 任务
- 理论吞吐: 8 × 100/s = 800/s
- 实际: 4 × 100/s = 400/s

**影响**:
- 批量场景 (get_daily_batch_local 20 只) 并发受限, 串行化

**建议**:
- 将 `_READER_SEMAPHORE_LIMIT` 默认值改为 8 (与 pool worker 1:1)
- 或保持 4 但在 doc 里明确 "池容量 8 留余量给 probe/close 等非 Reader 调用"

**Cross-ref**: Round-3 H3-perf, 用户 D2 决策 "不动, 若 race 观测回头处理"

---

## 三、MEDIUM (6)

### M1. `get_fzline` / `get_index_fzline` maxsize=128 可能偏小

| Slot | maxsize | TTL | key 维度 |
|---|---|---|---|
| `_l1_fzline` | 128 | next 9am | `(code,)` |
| `_l1_index_fzline` | 128 | next 9am | `(code,)` |

**现状**:
- 5 分钟 K 线单维 key, 但 A 股 5000+ 股票, 128 只能缓存 2.5%
- 指数更少 (~300), 128 可覆盖 ~40%

**影响**:
- 股票 fzline LRU 淘汰频繁, 命中率低

**建议**:
- `_l1_fzline` maxsize 提升至 512 或 1024
- 内存增量: 500 × 250KB ≈ 125MB (可接受)

**Cross-ref**: Round-3 LOW (`_top_board_l1 maxsize=128 易淘汰`)

---

### M2. `get_search` maxsize=128, keyword 基数 vs 命中率

| Slot | maxsize | TTL | key |
|---|---|---|---|
| `_l1_search` | 128 | 600s | `(keyword.lower(),)` |

**现状**:
- 搜索关键词离散度高 (股票名称/代码/拼音/概念)
- 600s TTL, 典型使用: 用户搜索 "茅台" → "600519" → 分析, 不重复搜同一词
- 128 maxsize 对"高频关键词"场景够用

**评估**:
- 128 合理, 关键词搜索本应低频
- 600s TTL 合理 (搜索结果相对稳定)

**结论**: **OK, 无需改动**

---

### M3. `_l1_block_scan` 实例化但未接入

| 文件 | 行号 | 现状 |
|---|---|---|
| `mootdx2_client.py` | 521 | `_l1_block_scan = _L1Slot("block_scan", maxsize=8, ttl=600)` |
| - | - | **0 调用方** |

**现状**:
- Batch 2b handoff 明确 "调用方决定接入策略, 留待新专题"
- 8 maxsize, TTL 600s

**影响**:
- 浪费 1 个 _L1Slot 实例 (~100 bytes)
- 无实质影响

**建议**:
- 要么接入 `_scan_blk_files_sync` 调用方
- 要么删除 (if 预留未来使用, 加 TODO 注释)

**Cross-ref**: Round-3 LOW (`mootdx2_pool.py 291 行死代码`)

---

### M4. get_metrics hits/misses 计数器裸 `+=` 无锁

| 文件 | 行号 | 问题 |
|---|---|---|
| `_l1_slot.py` | 85-87 | `self.hits += 1` / `self.misses += 1` |

**现状**:
- 单线程 async 上下文, GIL 原子操作
- Round-3 LOW 标记, 未修复

**评估**:
- 当前单 MooTDX2Client 实例场景, **无实际风险**
- 若未来多实例共享 _L1Slot → race condition

**建议**:
- 当前 OK, 但建议加 `threading.Lock` 防御多实例场景
- 或 doc 注释 "单实例专用, 多实例需独立 slot"

**Cross-ref**: Round-3 LOW (L1 metrics 裸 `+=` 无锁)

---

### M5. L1 miss 路径开销可量化

| 操作 | 估算耗时 |
|---|---|
| tuple creation (`cache_key = (suffix, code, start, end)`) | ~1µs |
| dict lookup (`slot._cache.get(key)`) | ~0.5µs |
| miss counter increment | ~0.1µs |
| **总计每 miss** | **~2µs** |

**评估**:
- 相比 Reader 加载 10-200ms, **可忽略**
- 无需优化

---

### M6. TTL=0 / disabled slot 一致性

| 文件 | 行号 | 机制 |
|---|---|---|
| `_l1_slot.py` | 54-59 | maxsize ≤ 0 → `enabled=False` |
| `_l1_slot.py` | 80-88 | `get()` 检查 `enabled` 跳过 |
| 各 wrapper | - | `if not slot.enabled:` 旁路分支 |

**评估**:
- **一致性 OK**: 所有 11 个 wrapper 都有 `if not slot.enabled:` 旁路分支
- cachetools 缺失时降级行为一致

**结论**: **OK**

---

## 四、LOW (4)

### L1. `get_daily` TTL 动态计算 "次日 9 点" 写后不感知

| 文件 | 行号 | 问题 |
|---|---|---|
| `mootdx2_client.py` | 462-465 | `ttl_seconds = (tomorrow_9am - now).total_seconds()` (初始化时算一次) |

**现状**:
- TTL 在 `__init__` 时计算一次, 写入 slot
- 跨午夜不更新, 次日 9 点前 TTL 可能 < 实际剩余时间

**影响**:
- 极小: TDX 文件夜间更新, 次日 9 点前数据已过期, TTL 长短不影响

**评估**: **可接受, 无需改动**

---

### L2. `_filter_records_by_date` 不必要 list 拷贝

| 文件 | 行号 | 问题 |
|---|---|---|
| `mootdx2_client.py` | ~650 | `_filter_records_by_date` 返回新 list |

**现状**:
- 函数返回新列表, 调用方可能再次切片
- 对 250KB DataFrame 影响 ~1ms

**影响**: 微小

**评估**: **可接受**

---

### L3. cache_key 每次调用 `datetime.date.today()`

| 文件 | 行号 | 问题 |
|---|---|---|
| `mootdx2_client.py` | 2382 | daily L1 key 含 `today` (已在 H2-perf 讨论) |

**现状**:
- 已改为双层方案: key 无 today, 命中后过滤

**结论**: **已解决**

---

### L4. daily L1 maxsize=2048, 单条目 250KB → 512MB 上限

| Slot | maxsize | 单条目大小 | 理论上限 |
|---|---|---|---|
| `_l1_daily` | 2048 | ~250KB | **512MB** |

**现状**:
- 实际 key 已改为 `(symbol, adjust)`, 不含日期窗
- 50 热点 × 2 adjust = 100 entries → **25MB** 实际内存

**评估**: **OK, 内存受控**

---

## 五、get_metrics 完整性验证

**验证结果**: 11 个 slot 全部在 `get_metrics` 中暴露 ✅

| slot | metrics key | 代码行 |
|---|---|---|
| `_l1_daily` | `daily_l1` | 953-956 |
| `_l1_top_board` | `top_board_l1` | 957-959 |
| `_l1_unusual` | `unusual_l1` | 960-962 |
| `_l1_minute` | `minute_l1` | 964 |
| `_l1_fzline` | `fzline_l1` | 965 |
| `_l1_index_kline` | `index_kline_l1` | 966 |
| `_l1_index_minute` | `index_minute_l1` | 967 |
| `_l1_index_fzline` | `index_fzline_l1` | 968 |
| `_l1_search` | `search_l1` | 969 |
| `_l1_indicator_atr` | `indicator_atr_l1` | 970 |
| `_l1_block_scan` | `block_scan_l1` | 971 |

---

## 六、Reader Gate 交互验证

**验证结果**: L1 命中后正确 bypass `_reader_gate` ✅

所有 7 个 wrapper 验证:

| 工具 | 行号 | L1 命中 return |
|---|---|---|
| `get_minute` | 2740-2743 | 直接 `return ToolResult(...)`, 无 `_reader_gate` |
| `get_fzline` | 2819-2822 | 同上 |
| `get_index_kline_local` | 3024-3039 | 同上 |
| `get_index_minute_local` | 3180-3195 | 同上 |
| `get_index_fzline_local` | 3270-3282 | 同上 |
| `search_stock` | 3528-3531 | 同上 |
| `indicator_atr` | 3678-3681 | 同上 |
| `stock_top_board` | 3950-3956 | 同上 |

**结论**: **OK, L1 命中不走 Reader 闸门**

---

## 七、批量并行化验证

**验证结果**: get_daily_batch_local 仍是串行 ❌

| 文件 | 行号 | 现状 |
|---|---|---|
| `mootdx2_client.py` | ~2320 | `_get_daily_batch_local_sync` 串行 for 循环 |

**Cross-ref**: Round-3 M-perf1 未修复

---

## 八、汇总

| 严重度 | 数量 | 关键问题 |
|---|---|---|
| CRITICAL | 0 | - |
| HIGH | 3 | minute/index_minute key 含日期窗爆炸; indicator_atr limit 入 key 爆炸; Reader 闸门 4 vs 池 8 |
| MEDIUM | 6 | fzline maxsize 偏小; _l1_block_scan 未接入; metrics 无锁; miss 开销可忽略; TTL disabled 一致; search OK |
| LOW | 4 | TTL 写后不感知; list 拷贝; daily today key; daily 内存 |

**建议落地**:
1. **H1 + H2**: 修复 minute/index_minute/indicator_atr 的 key 基数问题 (移除日期窗/limit 从 key)
2. **M3**: 删除或接入 `_l1_block_scan`
3. **M4**: 加 Lock 或 doc 注释防御多实例

**与 Round-3 关系**:
- H1 = Round-3 H2-perf 同一问题 (日期窗入 key)
- H2 = Round-3 M-perf5 因果 (key 爆炸导致 L1 失效, Python 循环重算)
- H3 = Round-3 H3-perf 遗留, 用户 D2 决定不动
- M3 = Round-3 LOW (`mootdx2_pool.py 死代码` 同类)
- M4 = Round-3 LOW (`+=` 无锁)
