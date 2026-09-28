# MOOTDX2 离线接口 · 第四轮审视 (Round-4 Audit)

日期: 2026-09-28
专题: Batch 2b 实施后的整体复审(功能逻辑 + 性能 + 静默失败)
状态: **报告已交付,未修改任何代码,等待用户裁决 ship 哪些批次**

---

## 背景

Round-3 (2026-09-28) ship 了 Batch 1 (8 contract fixes + D4) + Batch 2a (L1Slot 抽象 + daily 双层) + Batch 2b (7 工具 L1 接入 + H4 records-only refactor),共 ~16 项发现已落地。

本轮是 Round-4,聚焦 **Batch 2b 实施后的回归 + 新漏洞扫描**,共 3 个并行 agent:

1. **Agent 1 (code-reviewer)** — 缓存键设计 / 引用共享 / 错误路径 / 测试覆盖 → `round4-agent1-codereview.md` (11 项)
2. **Agent 2 (performance-optimizer)** — TTL / maxsize / cardinality / Reader 闸门 / metrics 锁 → `round4-agent2-perf.md` (13 项)
3. **Agent 3 (silent-failure-hunter)** — L1 命中遮蔽 warn/error / 缓存写入位置 / 边界条件 → `round4-agent3-silent.md` (12 项)

合并去重后:

| 视角 | CRITICAL | HIGH | MEDIUM | LOW | 合计 |
|---|---|---|---|---|---|
| Agent 1 | 1 | 3 | 4 | 3 | 11 |
| Agent 2 | 0 | 3 | 6 | 4 | 13 |
| Agent 3 | 1 | 3 | 3 | 5 | 12 |
| **去重合计** | **2** | **5** | **6** | **3** | **16** |

跨视角交叉印证 (Round-4 同根问题):
- **`_with_warn` data 复制不完整** — A1-CR1 (hit 路径) + A3-C1 (None data) → **最严重**
- **`get_index_kline_local` 缓存合同** — A1-CR1 (引用) + A1-H1 (key 归一化) + A1-M1 (miss 写入复制) → **同一接口 3 视角**
- **缓存键基数爆炸** — A2-H1 (日期窗) + A2-H2 (limit) → 与 Round-3 H2-perf 同根
- **`search_stock` 入口校验** — A1-H3 + A3-H1 (同发现,2 视角)

---

## 一、CRITICAL (2 条)

### CR1. `_with_warn` 对 None data 崩溃

- **视角**: silent-failure
- **文件**: `src/unihive/api/mootdx2_client.py:3921`
- **现状**:
  ```python
  safe_data = list(result.data) if isinstance(result.data, list) else result.data
  ```
- **失败场景**: 任何返回 `success=True, data=None` 的路径进入 `_with_warn` 时,`list(None)` 抛 `TypeError: 'NoneType' object is not iterable`。
- **触发路径**: `stock_top_board` L1 hit 后调用 `_with_warn(cached, warn)` (line 3953);任何 wrapper 走 `_with_warn(... data=None)` 均崩溃。
- **建议修复**:
  ```python
  if result.data is None:
      safe_data = None
  elif isinstance(result.data, list):
      safe_data = list(result.data)
  else:
      safe_data = result.data  # dict/generator 等透传
  ```
- **Cross-ref**: Batch 2b H4-silent 引入的回归

### CR2. `get_index_kline_local` L1 hit 路径引用共享 — 下游修改污染缓存

- **视角**: code-reviewer (silent-failure 部分印证)
- **文件**: `src/unihive/api/mootdx2_client.py:3027-3034`
- **现状**:
  ```python
  cached_records = self._l1_index_kline.get(cache_key)
  if cached_records is not None:
      sliced = (
          cached_records[-limit:] if limit and len(cached_records) > limit
          else cached_records  # ← 当 limit >= len 时直接返回原引用!
      )
  ```
- **失败场景**:
  1. 第一次请求 `get_index_kline_local(code="sh000001", type="day", limit=10000)` — 文件 5000 条
  2. L1 写入 5000 条 records 全量
  3. 第二次同参数 → `limit and len(cached_records) > limit` 为 False → `sliced = cached_records` (同一引用)
  4. 下游 `result.data.append(...)` / `pop(...)` / `clear()` → **污染 L1 缓存**
- **对比**: `get_index_minute_local` 用 `list(cached_records)` 安全 ✓
- **建议修复**: `sliced = list(cached_records[-limit:]) if limit and len(cached_records) > limit else list(cached_records)`
- **Cross-ref**: Batch 2b H4-silent 修复不完整 (records-only 是对的, hit 路径仍有引用共享)

---

## 二、HIGH (5 条)

### H1. `get_index_kline_local` 缓存键未归一化 — "sh000001" 与 "000001" 同 symbol 不同 key

- **视角**: code-reviewer
- **文件**: `mootdx2_client.py:3022`
- **现状**: `cache_key = (code, type)` — code 是用户原始输入
- **失败场景**: `get_index_kline_local("sh000001", "day")` 与 `get_index_kline_local("000001", "day")` 返回相同数据但 key 不同,缓存浪费
- **建议**: 用 `parse_symbol` 归一化结果建 key, `cache_key = (to_reader_symbol(parsed), type)`

### H2. `get_minute` / `get_index_minute` cache_key 含日期窗 → cardinality 爆炸

- **视角**: performance
- **文件**: `mootdx2_client.py:2738`, `:3178`
- **现状**: key = `(suffix, code, start_date, end_date)`
- **量化**: 50 热点股 × 5 日期窗 × 2 suffix = 500 组合 / maxsize 256 → LRU 频繁淘汰,命中率 < 50%
- **建议**: 与 daily L1 2 层方案一致 — key 只含 `(suffix, code)`,命中后 `_filter_records_by_date` 过滤
- **Cross-ref**: Round-3 H2-perf 同根

### H3. `indicator_atr` cache_key 含 limit ∈ [1,1000] → 800 万组合 / maxsize 64 完全失效

- **视角**: performance
- **文件**: `mootdx2_client.py:3676`
- **现状**: key = `(code, type, limit)`
- **量化**: 800 万组合, maxsize 64, 命中率 ≈ 0%;每次重算 (Python 循环 ~30ms vs pandas ~30µs)
- **建议**: 移除 limit 从 key,`(code, type)` 命中后按 limit 切片 (与 index_kline 2 层策略一致)
- **Cross-ref**: Round-3 M-perf5 (ATR Python 循环)

### H4. `get_minute` cache_key suffix int/str 类型不一致

- **视角**: code-reviewer
- **文件**: `mootdx2_client.py:2738`
- **现状**: `cache_key = (suffix, code, start_date, end_date)` — suffix 入参 int/str 都接受
- **失败场景**: `get_minute(code="600036", suffix="1")` 与 `get_minute(code="600036", suffix=1)` 等价但 key 不同
- **建议**: `cache_key = (str(suffix), code, start_date, end_date)`

### H5. `search_stock` 空白 keyword 污染缓存

- **视角**: code-reviewer + silent-failure (2 视角)
- **文件**: `mootdx2_client.py:3525`
- **现状**: `cache_key = (keyword.lower(),)` 无空白校验
- **失败场景**: `search_stock(keyword="   ")` → SQLite LIKE `"%   %"` 行为未定义,可能污染缓存
- **建议**: 入口校验 `if not keyword or not keyword.strip(): return self._no_data_result("搜索关键字不能为空")`
- **Cross-ref**: Round-3 H1-silent "双源不可用显式化" 补充

---

## 三、MEDIUM (6 条)

### M1. `get_index_kline_local` miss 路径写入未浅复制

- **视角**: code-reviewer
- **文件**: `mootdx2_client.py:3047`
- **现状**: `self._l1_index_kline.put(cache_key, records)` — records 直接写入
- **对比**: `get_index_minute_local` (line 3205) 用 `list(records)` 安全
- **建议**: `self._l1_index_kline.put(cache_key, list(records))`
- **与 CR2 同根**: 防御性浅复制

### M2. `get_minute` / `get_fzline` L1 hit 无 warn 重算 — 与 index 版行为不一致

- **视角**: silent-failure
- **文件**: `mootdx2_client.py:2741-2743`, `2819-2822`
- **现状**: L1 hit 直接 `return ToolResult(success=True, data=cached_records, ...)` 无 warn 重算
- **对比**: `get_index_*_local` hit 时重算 truncated warn
- **现状评估**: 当前 minute/fzline sync 函数不支持 limit 参数,无截断风险 → LOW 级别可接受,但建议对齐接口语义

### M3. `_l1_fzline` / `_l1_index_fzline` maxsize=128 偏小

- **视角**: performance
- **现状**: 5 分钟 K 线单维 key, A 股 5000+ 只,128 只覆盖 2.5%
- **建议**: `_l1_fzline` 提升至 512 (~125MB);`_l1_index_fzline` 128 OK (指数 ~300 只覆盖 40%)
- **Cross-ref**: Round-3 LOW `_top_board_l1 maxsize=128 易淘汰`

### M4. `_l1_block_scan` 实例化但未接入

- **视角**: performance
- **文件**: `mootdx2_client.py:521`
- **现状**: Batch 2b 创建实例但 0 调用方
- **建议**: 二选一 — 接入 `_scan_blk_files_sync` 或删除并 TODO 注释

### M5. get_metrics hits/misses `+=` 无锁

- **视角**: performance
- **文件**: `src/unihive/api/_l1_slot.py:85-87`
- **现状**: 单线程 async 上下文, GIL 原子操作, 当前无风险
- **建议**: 加 `threading.Lock` 防御多实例场景;或 doc 注释 "单实例专用"
- **Cross-ref**: Round-3 LOW (L1 metrics 裸 `+=` 无锁)

### M6. L1 TTL 跨日不感知 (写后不重算)

- **视角**: silent-failure
- **现状**: TTL 在 `__init__` 时计算一次 (次日 9 点);跨午夜不更新
- **影响**: 09:00 后首次 miss 会重算 TTL,但无显式日志
- **建议**: 接受现状,加 INFO 日志便于观测 (Round-3 讨论过)

---

## 四、LOW (3 条)

### L1. `_l1_block_scan` mtime 模式未推广到其他 L1 slot

- **视角**: silent-failure
- **说明**: Round-3 提到 `_l1_block_scan` 用 `(dir_mtime,)` 模式,但其他 7 个 L1 slot 未采用
- **风险**: 文件更新后 TTL 内仍返回旧数据
- **评估**: TTL 较短,可接受;**建议而非强制**

### L2. M-silent1 (Round-3 遗留) 仍未修复 — `_error_result` `str(exc)[:100]` 暴露用户路径

- **视角**: silent-failure
- **文件**: `src/unihive/models/mootdx2_errors.py:94-105`
- **现状**: Round-3 用户裁决 D4 涉及 M-silent2 (异常分类),未涉及 M-silent1
- **评估**: 遗留问题,本轮未处理

### L3. L1 metrics 键名与 reset 命名风格

- **文件**: `mootdx2_client.py:963-973` vs `1015-1027`
- **现状**: metrics 键 `minute_l1`, slot.name `minute` — 风格不统一
- **建议**: 统一命名 (cosmetic)

---

## 五、Round-3 仍 pending (Batches 3+4+5)

Round-3 ship Batch 1+2a+2b 共 16 项,**仍有 35 项 pending**,按 Round-3 §八 推荐批次:

### Batch 3 (合同一致性,半天)

- M-func1: `get_daily_batch_local` 返回 key 格式不统一 (3 种) — `mootdx2_client.py:2328-2333`
- M-func2: `get_daily_batch_local` warn 缺 `recoverable: True` — `:2542`
- M-func3: `get_index_fzline_local` 不支持日期窗 — `:2997-3046`
- M-func4: 股票版 `get_fzline` 不支持日期窗 — `:2619-2641`
- M-perf1: `_get_daily_batch_local_sync` 串行 20 次 — `:2299-2345`

### Batch 4 (HIGH/MED 收尾)

- H2-silent: `_index_minute_file_message` 等文案对 0 字节文件撒谎 — `:2895-2919` 等
- H3-silent: `get_stock_info` partial failure (online quote OK + offline 空) 合并 no_data — `:3251-3312`
- M-silent1: `_error_result` `str(exc)[:100]` 路径泄露 — `mootdx2_errors.py:94-105` (同 Round-4 L2)
- M-silent2: 异常分类细化 — 已 ship D4 部分,补完 KeyError/AttributeError/TypeError → INTERNAL_ERROR

### Batch 5 (LOW 收尾)

- mootdx2_pool.py 291 行死代码 (0 调用方) — `mootdx2_pool.py`
- `_top_board_l1` maxsize=128 易淘汰 (Round-4 M3 平行)
- TTL 写后不感知 (Round-4 M6 平行)
- `_filter_records_by_date` 不必要 list 拷贝 (Round-4 L4 平行)
- L1 metrics `+=` 无锁 (Round-4 M5 平行)
- `recoverable` 默认 True 让 router 盲目重试代码 bug
- tdx_reader_patch 升级后无 runtime 告警
- `_probe_tdxdir` 一次性 stale probe
- `no_data_result` `success=True` 但 error 非空 (LLM 按 success 分流会忽略 NO_DATA)

---

## 六、推荐落地批次 (Round-4)

```
批次 1 [1 PR, CRITICAL/HIGH 杂,合同层 — 必须先修]:
  - CR1 (_with_warn None data 守卫)
  - CR2 (get_index_kline_local hit 路径 list 复制)
  - H1 (get_index_kline_local cache_key 用 parse_symbol 归一化)
  - H4 (get_minute cache_key str(suffix))
  - H5 (search_stock 空白 keyword 入口校验)

批次 2 [L1 缓存键基数修复,半天]:
  - H2 (get_minute/get_index_minute key 移除日期窗 → 2 层)
  - H3 (indicator_atr key 移除 limit → 2 层)

批次 3 [Round-3 Batch 3 合同一致性,半天]:
  - M-func 1-4 (批/指数/fzline 合同对齐)
  - M-perf1 (批并行化, 依赖批次 2 的单 symbol L1 复用去重)

批次 4 [Round-3 Batch 4 silent + Round-4 配置,半天]:
  - H2-silent (文件空 vs 缺失 stat 守卫)
  - H3-silent (stock_info partial status)
  - M-silent1/M-silent2
  - M1 (get_index_kline_local miss 复制)
  - M3 (fzline maxsize 128→512)
  - M4 (_l1_block_scan 接入或删)
  - M5 (metrics Lock)
  - M6 (TTL 跨日日志)

批次 5 [Round-3 Batch 5 + Round-4 LOW 收尾,半天]:
  - mootdx2_pool.py 死代码删除
  - L1 (mtime 模式推广评估, 或维持)
  - L2 (M-silent1 路径泄露修复)
  - L3 (命名 cosmetic)
  - recoverable 默认值审查
  - tdx_reader_patch 升级告警
  - no_data_result success=True 语义统一
```

---

## 七、与历史审计的关系

| 历史 | 已 ship | Round-4 新增 |
|---|---|---|
| Round-3 Batch 1+2a+2b | 16 项 | **Round-4 16 项** (2 CR + 5 H + 6 M + 3 L) |
| Round-3 H2-perf daily L1 key | Batch 2a (daily 双层) | **H2 推广到 minute/index_minute** (本轮) |
| Round-3 H4-silent (records-only) | Batch 2b (部分) | **CR2 hit 路径复制 + CR1 None data** (本轮扩展) |
| Round-3 H1-silent search_stock | Batch 1 (双源显式化) | **H5 空白 keyword 入口校验** (本轮补充) |
| Round-3 M-perf5 ATR Python 循环 | 未 ship | **H3 key 含 limit 致 L1 失效** (本轮扩展) |
| Round-3 M-silent1 path leakage | 未 ship | **L2 仍未修复** (本轮确认遗留) |

**累计四轮审视聚类**: mootdx2 offline 体系的问题已收敛到 4 条主轴:
1. **L1 缓存合同 (Batch 2 + Round-4 批次 1 + 2)** — 11 个 slot 全合同就位
2. **批工具语义 (Round-3 Batch 3)** — 待 ship
3. **分钟线合同 (Round-3 + Round-4 H2)** — 同根,扩展
4. **静默 fallback 矩阵 (Round-3 Batch 4)** — 待 ship

后续审计建议聚焦这 4 个域,不再全量重审。

---

## 八、用户裁决 (待)

请告知:
1. **ship 哪些批次** (Round-4 Batch 1+2 强烈建议优先 — 含 2 个 CRITICAL)
2. **顺序偏好**
3. **测试策略** (按用户偏好 `feedback_modify_dont_test_until_told`,让改就只改不主动跑)
4. **Round-3 Batch 3+4+5 是否合并到 Round-4 批次 3+4+5** (本报告已合并,二选一)