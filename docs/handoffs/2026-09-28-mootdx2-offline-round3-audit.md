# mootdx2 离线接口 · 第三轮审视 (Round-3 Audit)

日期: 2026-09-28
专题: 12 个 `data_source_type: offline` 工具 + 6 个横切文件的第三轮审视
状态: **报告已交付,未修改任何代码,等待用户裁决 ship 哪些批次**

## 背景

mootdx2 上游 12 个 offline 工具通过 `mootdx2.reader.Reader` 读取本地 `vipdoc/` 二进制
文件(不需网络连接 TDX 服务器)。前两轮审计已 ship 大量改进:

- **2026-09-15 mootdx2 性能审视**(doc: `2026-09-15-mootdx2-perf-audit.md`)
  ship 了 `H2-a (top_board/unusual L1 10s)` + `H3 (to_thread)` + `M3 (search_stock 向量化)`
- **2026-09-19 mootdx2 HIGH 四项**(doc: `2026-09-19-mootdx2-high-fixes-h1-h4.md`)
  ship 了"假方法 q.stock/etf/income"、"FLAT code 列"、"指数前缀回贴"、"FREQ_MAP 统一",
  以及第二批"批量行情 BJ 裸码"、"中文板块名"、"tdxdir 前置哨兵"、"参数门控"、
  "daily meta tuple"、"L1 UnboundLocalError"
- **2026-09-15 get_daily L1 可观测性 + single-flight**(doc: `project_get_daily_l1_observability_2026_09_15.md`)
  ship 了 get_daily_local 的 L1 内存缓存 + 单飞 + 控制台 `/l1-cache` 页
- **2026-09-27 per-source pool 拆分**(doc: `project_route_a_per_source_pool_split_2026_09_27.md`)
  共享池拆为 mootdx2(8, unihive-mootdx2) / tdx_quant(10) / thsdk(1)

本轮是第三轮,范围包括所有 12 个 offline 工具 + 横切基础设施。共 51 条发现
(去重后),其中多视角交叉印证的高价值问题集中在 4 个领域。

## 12 个 offline 工具清单

| 工具 | 行号 | 数据源 | L1 现状 |
|---|---|---|---|
| `get_workday` | `mootdx2_client.py:1864` | Reader.workday | 无 |
| `get_workday_range` | `:1900` | Reader.workday | 无 |
| `get_custom_sector_list` | `:1996` | Reader.block + .blk 文件名 | 无 |
| `get_custom_sector_stocks` | `:2054` | Reader.block + .blk | 无 |
| `search_stock` | `:3236` | Reader.block + SQLite | 无 |
| `get_daily_local` | `get_daily`:2347 | Reader.daily (.day) | **有** (2026-09-15 ship) |
| `get_daily_batch_local` | `:2476` + sync:2299 | Reader.daily (20 只批量) | 形同虚设 (sync 绕过 async L1) |
| `get_minute_local` | `get_minute`:2550 | Reader.minute (.lc1) | 无 |
| `get_fzline_local` | `get_fzline`:2615 | Reader.fzline (.lc5) | 无 |
| `get_index_kline_local` | `:2774` + sync:2729 | Reader.daily (指数) | 无 |
| `get_index_minute_local` | `:2921` + sync:2885 | Reader.minute (指数 .lc1) | 无 |
| `get_index_fzline_local` | `:3005` + sync:2997 | Reader.fzline (指数 .lc5) | 无 |

L1 覆盖率: **1 / 12** (仅 `get_daily_local`)。Reader 类共 8 个工具
无缓存。

## 横切文件

- `src/unihive/api/mootdx2_client.py` (3679 行) — 主客户端
- `src/unihive/api/index_registry.py` (189 行) — `parse_symbol` / `to_reader_symbol` / `INDEX_KIND` 等
- `src/unihive/api/tdx_reader_patch.py` (112 行) — Reader 实例级 bj 路径补丁
- `src/unihive/api/mootdx2_pool.py` (291 行) — ⚠️ 全部死代码,0 调用方
- `src/unihive/models/mootdx2_errors.py` (182 行) — `MooTDXErrorType` / `error_to_result` 映射
- `src/unihive/models/mootdx2_config.py` (299 行) — `MooTDX2Config` dataclass

## 审视方法

3 个并行 agent 分视角审计:
1. **code-reviewer** — 功能逻辑 / 合同一致性 / 错误信封
2. **performance-optimizer** — 阻塞池 / IO / DataFrame / L1 / fastpath
3. **silent-failure-hunter** — 空 DataFrame / 文件 IO 错误 / 异常吞掉 / L1 stale / 路径泄露

每个 agent 输出 markdown 报告,本文件做合并去重 + 严重度排序。

---

## 一、合并发现统计

| 视角 | CRITICAL | HIGH | MEDIUM | LOW | 合计 |
|---|---|---|---|---|---|
| 功能逻辑 | 0 | 5 | 4 | 5 | 14 |
| 性能 | 0 | 3 | 6 | 7 | 16 |
| 静默失败 | 2 | 4 | 5 | 10 | 21 |
| **去重合计** | **2** | **12** | **15** | **22** | **51** |

**多视角交叉印证**:
- **L1 缓存合同/语义**:功能 H1 + 性能 H2 + 静默 H4 (3 视角) → **最严重**
- **`get_minute/get_fzline` 解析容错**:功能 H2/H3 + 静默 C2 (2 视角) → **合同分裂**
- **`get_daily_batch_local` 语义**:功能 H4/H5 + 静默 C1 (2 视角) → **批工具误用**
- **`search_stock` 双 silent fallback**:功能 MED + 静默 H1 (2 视角) → **部署诊断**

---

## 二、CRITICAL (2 条)

### C1. `get_daily_batch_local` 逐只吞异常,全部失败仍 `success=True`
- **视角**: 静默失败
- **文件**: `src/unihive/api/mootdx2_client.py:2336-2345`
- **现状**:
  ```python
  for code in code_list:
      try:
          records, meta = self._get_daily_sync(code, adjust, start_date, end_date)
          results[key] = records
          if not records and meta.get("warn"):
              warnings.append(f"{code}: {meta['warn']}")
      except ValueError as e:
          warnings.append(f"{code}: invalid_symbol - {e}")
      except Exception as e:
          warnings.append(f"{code}: {str(e)}")  # ← 任何异常吞进 warn
  return results, warnings
  ```
- **失败场景**:
  - `get_daily_batch_local("sh600036,bj920001,sh430047")`,`bj920001` 因 `PermissionError`/`struct.error` 失败
    → 失败那只从 `data` 消失,只塞进 `error_detail.warn` 字符串
  - 整批 20 只全部失败 → 仍 `success=True, data={}`,无 metrics、无 `_record_error`
- **影响**: 数据完整性误导。LLM 看到 `success=True` 以为全集拿全;损坏文件被永久静默。
- **建议**:
  1. 拆 `except Exception` 为 `OSError/struct.error → _error_result`
  2. 全失败改 `success=False` + `error_type=INTERNAL_ERROR`
  3. 收集 `_record_error` 进 metrics

### C2. `get_minute`/`get_fzline` 把非法代码 `ValueError` 当成 no_data
- **视角**: 静默失败
- **文件**: `mootdx2_client.py:2568-2571`、`:2626-2629`
- **现状**:
  ```python
  try:
      sym = to_reader_symbol(parse_symbol(code))
  except ValueError:
      return []   # ← 静默吞掉,转成"无数据"
  ```
  外层 `get_minute` (`:2608-2610`) 返 `_no_data_result(f"未找到 {code} 的{suffix}分钟数据")`
- **失败场景**: 用户传 `get_minute(code="abc")` → `parse_symbol` 抛 `ValueError` → `_get_minute_sync` 返 `[]` → `get_minute` 返 `success=True, data=null`, `error_type=NO_DATA`, `message="未找到 abc 的1分钟数据"`
- **对比**: `get_daily` 让 `ValueError` 透传到外层 (`:2446`),正确转 `INVALID_SYMBOL`
- **影响**: 用户拿到 `_no_data` 后以为本地数据没下载,实际是代码格式错
- **建议**: 删 `try/except ValueError → return []`,让 `ValueError` 自然透传到外层 `get_minute`/`get_fzline` 的 `except Exception`,经 `_error_result` → `INVALID_PARAM`

---

## 三、HIGH (12 条)

### 3.1 L1 缓存合同/语义 (3 视角交叉)

#### H1-功能. `get_daily_batch_local` docstring 谎称"复用 L1"
- **文件**: `mootdx2_client.py:2299-2345`、`:2476-2548`
- **现状**: docstring 明确"复用 L1 缓存"。但 `_get_daily_batch_local_sync` 通过 `self._get_daily_sync(...)` 直接拉数据,而 `_get_daily_sync` 是同步内核,**不在 `get_daily` async wrapper 的 L1 检查路径里**(`get_daily` 行 2382-2445 才是 L1 命中/写回的真正场所)。批量入口下 N 只 = N 次 Reader 加载,L1 完全失效。
- **测试覆盖**: `test_get_daily_batch_local_success` / `_two_codes` 只 mock `Reader.daily` 验证同步路径,无 L1 验证。
- **影响**: 批量接口高频调用时 Reader 并发压力与单只调用相同;docstring 与行为冲突。
- **建议** (修订后 — 见后文 §四 关键设计原则):
  把 L1 缓存逻辑下沉到 `_get_daily_sync` 内层(让 sync 函数也读/写 `self._daily_l1`),
  **不要**给批接口加一层 `(code_tuple, adjust, start, end) → records` 缓存。

#### H2-性能. `get_daily` L1 key 含 (start_date, end_date) → 同 symbol 多份全量拷贝
- **文件**: `mootdx2_client.py:2398`、`:2410-2419`、`:2421-2444`
- **现状**: cache_key = `(today, reader_symbol_key, actual_adjust, start_date or "", end_date or "")`。`_get_daily_sync` 写 L1 时**整文件全量 records**(line 2283-2297),不做日期窗截断。
- **失败场景**: 同一日连发 `get_daily("sh600519", start_date="2026-01-01")` 和 `get_daily("sh600519", start_date="2026-06-01")` → L1 内 sh600519 有 2 份 250KB 全量拷贝
- **实测容量**: `TTLCache(maxsize=2048) × 250KB ≈ 500MB`。50 热点 × 4 常见 start_date = 200 entries ≈ 50MB;实际数据 pattern 通常 start_date 频繁变化(UI 滚动拉取近 1 周 / 近 1 月 / 近 3 月 / 全部 = 4 个),单 symbol 占 1MB → maxsize 2048 名义上 2000 个 symbol 限额实际只能容 ~500 个热点
- **建议**: 改为 2 层缓存:
  - L1 主层: key=`(today, reader_symbol, adjust)`,value=全量 records
  - 命中后用 `_filter_records_by_date` 在 records 上做 O(N) dict 过滤 (line 648-663 现成实现)
  - 写 L1 时存**全量**,hit 路径统一出口切窗

#### H4-静默. L1 缓存整 ToolResult,可能命中 stale `error_detail` + data 引用共享
- **文件**: `mootdx2_client.py:3636-3648`、`:3664-3676`、`:3587-3601`
- **现状**:
  - `_top_board_l1[cache_key] = result` 缓存整 `ToolResult`
  - `_with_warn` 返回的 ToolResult 的 `data` list **与缓存对象是同一引用**(line 3587-3601 用 `data=result.data`),下游修改会污染 L1
- **影响**: stale empty / 内部 error 跨请求复用;数据修改污染缓存
- **建议**: cache 只存 `data`(纯 records),返回时重组 ToolResult;`_with_warn` 用 `list(result.data)` 拷贝

### 3.2 `get_minute`/`get_fzline` 合同分裂 (2 视角交叉)

#### H2-功能. `get_minute` 静默吞掉非法 suffix
- **文件**: `mootdx2_client.py:2563`、`:2590-2613`
- **现状**: `suffix_int = 1 if str(suffix)=="1" else 5`。任何非 "1" 入参(suffix='2'/'0'/''/'minute1'/'five') 都被静默映射成 5 分钟线 → 返 `.lc5` 数据,与用户输入不符。
- **对比**: 同模块 `get_index_minute_local:2940` 已做 `if str(suffix) not in ("1","5"): return invalid_param`,合同不一致。
- **建议**: 在 `get_minute` 入口对 suffix 做白名单校验,与 `get_index_minute` 一致。

#### H3-功能. `get_minute` 跳过日期窗校验与归一化
- **文件**: `mootdx2_client.py:2551-2588`、`:2590-2613`
- **现状**: 与 `get_index_minute_local:2947` 显式调用 `_normalize_date_window` 不同,`get_minute` / `_get_minute_sync` 直接用 `df.index >= start_date`。
- **后果**:
    1. `start_date='20260901'`(YYYYMMDD 紧凑串) 在 pandas DatetimeIndex 比较中行为未定义,通常返回空 df;`get_index_minute_local` docstring 明确"兼容 YYYYMMDD",而 `get_minute` 实际不接受
    2. `start_date='2026-13-01'`(非法日历日) 静默空集,而不像 `get_minute_data` 那样返 `invalid_param`
- **测试**: `test_get_minute_suffix_1/5` 完全不传 start_date/end_date,日期路径零覆盖。
- **建议**: `_get_minute_sync` 入口补 `_normalize_date_window`(或同步版),非法格式 → `invalid_param`。

### 3.3 `get_daily_batch_local` 语义 (2 视角交叉)

#### H4-功能. `get_daily_batch_local` 丢失裸 000xxx 消歧提示
- **文件**: `mootdx2_client.py:2299-2345`
- **现状**: 单只 `get_daily('000001')` 会在 `meta.warn` 写歧义码提示(`_daily_request_warn`,行 2271);`get_daily_batch_local` 调 `_get_daily_sync` 时拿到的 `meta` 包含 `warn`,但同步函数 `results[key] = records` 只记了 records,**完全没有把 `meta["warn"]` 透传到外层 warning 列表**(只有 records 为空且 warn 非空时才进 warnings)。
- **失败场景**: 批量传 `["000001", "600519"]`,`000001` 返回平安银行数据,无任何 warn;`600519` 正常;用户被"跨市场数据"误导。
- **建议**: 把 `meta["warn"]` 无论 records 是否为空都拼入 `warnings`(例如 `f"{code}: {meta['warn']}"`)。

#### H5-功能. `get_daily_batch_local` 缺每只 limit,与 `batch_bars` 不一致
- **文件**: `mootdx2_client.py:2476-2548`
- **现状**: `batch_bars`(行 1530-1562)限 `MAX_LIMIT_BATCH_BARS_CODES=20` 且每只 `MAX_LIMIT_BATCH_BARS=500` 根;`get_daily_batch_local` 限 20 只但**没有每只 limit**,会拿每只股票的全量日线(本机日线可达 5000+ 根 × 20 = 10 万行 JSON)。docstring "≤20 只" 是误导。
- **建议**: 在 YAML 合同与代码里统一加 `limit` 参数(默认 500 / max 1000),与 `batch_bars` 对齐。

### 3.4 8 个离线工具缺 L1

#### H1-性能. 8/12 Reader 离线工具缺 L1 缓存(2026-09-15 H1 残余 + 用户裁决排除批量入口)
- **文件**:
  - `mootdx2_client.py:2550` (`get_minute`)
  - `mootdx2_client.py:2615` (`get_fzline`)
  - `mootdx2_client.py:2661` (`get_index_kline_local`)
  - `mootdx2_client.py:2853` (`get_index_minute_local`)
  - `mootdx2_client.py:2996` (`get_index_fzline_local`)
  - `mootdx2_client.py:3151` (`search_stock`)
  - `mootdx2_client.py:3251` (`get_stock_info` 离线 K 线/分时部分)
  - `mootdx2_client.py:3314` (`indicator_atr`)
  - `mootdx2_client.py` 自定义板块 `_scan_blk_files_sync`
- **用户裁决(2026-09-28)**: **批量入口(`get_daily_batch_local`)不加 L1**;
  让底层单 symbol L1 做去重,批量入口只负责聚合。memory:
  `feedback_no_l1_on_batch_interfaces.md`。
- **现状**: `get_daily_local` 已 ship L1 (`2026-09-15` P0),剩余 8 个单 symbol 工具 + 1 个 sync helper 全部 0 缓存。
  - 单次 `reader.daily()` ~10-30ms (250-500KB 二进制解析)
  - 单次 `reader.minute()` ~5-20ms
  - 单次 `reader.fzline()` ~5-15ms
  - `reader.block()` 全市场 ~50-200ms + ~5000 行 DataFrame
- **影响**: AI agent 场景"分析 600519" → 至少 5 次 Reader 调用,同对话里大部分重复触发
- **建议**: 抽 `L1CacheHelper`(cachetools.TTLCache + key-builder 协议 + metrics 复用) 类,让 8 个工具按以下模板接入:

  | 工具 | TTL | key | maxsize |
  |---|---|---|---|
  | `get_minute_local` | 3600s+交易时段锚 | `(code, suffix, start, end)` | 256 |
  | `get_fzline_local` | 同上, suffix=5 | `(code, start, end)` | 128 |
  | `get_index_kline_local` | 3600s | `(code, type)` | 256 |
  | `get_index_minute_local` / `get_index_fzline_local` | 3600s | `(code, suffix, start, end)` | 128 |
  | `search_stock` | 600s | `(keyword_lowercase,)` | 128 |
  | `indicator_atr` | 600s | `(code, type, limit)` (内部先查 K 线 L1) | 64 |
  | `_scan_blk_files_sync` | 600s 或 `(dir_mtime,)` | 文件级失效 | 8 |

### 3.5 Reader 实例 + 信号量闸 vs Pool 错配

#### H3-性能. Reader 单例 + 4 并发闸 vs 8 worker 池
- **文件**:
  - `mootdx2_client.py:480` (`_reader_semaphore = asyncio.Semaphore(4)`)
  - `mootdx2_client.py:665-691` (`_get_reader` 单例 + 双重 lock)
  - `core/blocking_pool.py:31` (`_MOOTDX2_DEFAULT_SIZE = 8`)
  - `tdx_reader_patch.py:96-109` (实例级 bj 路径补丁)
- **现状**:
  - mootdx2 pool 容量 8 worker,但闸门钉死 4 并发 Reader → 池里 4 worker 永远闲置
  - 4 并发共用同一个 `self._reader` 实例 → mootdx2 `StdReader` 内部是否有线程安全未实测
  - 实例级 bj 路径补丁通过 `MethodType` 绑定到 `_reader` 上,若 Reader 内部使用非 GIL 释放的 C 扩展,多线程并发调用同一个 Reader 触发 race
- **影响**:
  - 当前 4 worker 闲置 ≈ 50% 浪费吞吐
  - 若 Reader 非线程安全,4 并发 reader 在某些 binary 解析路径上 race
- **建议**:
  - 短期: 把 `UNIHIVE_MOOTDX2_READER_LIMIT` 默认值与 pool worker 数解耦 → 默认 8
  - 中期: 调研 mootdx2 `StdReader` 是否线程安全(`mootdx2/reader/std.py` 源码 + 线程压测);
    若不安全,每个 worker 持一个 Reader 实例(`_PERSISTENT_READERS` + `threading.local`),池容量与 Reader 实例数 1:1
  - 不要做的事: 不要把 `_reader_semaphore` 提到 32/64 不做实测

### 3.6 `search_stock` 双 silent fallback

#### H1-静默. `search_stock` 双 silent fallback — 无 tdxdir + 无 board.db 双重失败时返 `success=True, data=[]`
- **文件**: `mootdx2_client.py:3152-3230`
- **现状**:
  - `_get_tdxdir()` 返 None → 跳过 `reader.block()`,无日志(实际 `logger.debug` 因 tdxdir 为空根本不进入 reader 调用路径)
  - 进 `_search_stock_from_sqlite`,`db_path.exists()` 为 False → `logger.warning("...board.db not found")` 但 warning 后 return `[]`,然后 `search_stock` 直接 `success=True, data=[]`
  - 若 `reader.block()` 抛 FileNotFoundError 等:`logger.debug` 级别静默 + 进 SQLite,仍 silent failure
- **影响**: 部署缺数据时所有搜索结果"成功"为空,运维以为搜索功能坏了;无任何监控告警
- **建议**:
  1. 把 reader.block() 失败的 `logger.debug` 升为 `warning`
  2. tdxdir 为空 + board.db 也不存在时直接 `success=False, error_type=TDX_NOT_INSTALLED`
  3. `search_stock` 仅在 tdxdir 与 board.db 都有但确实没匹配时才用 `_no_data_result`

### 3.7 文件缺失 vs 空文件不可区分

#### H2-静默. `_index_minute_file_message` 等文案对 0 字节文件撒谎
- **文件**: `mootdx2_client.py:2895-2919`、`:2729-2747`、`:2843-2848`
- **现状**:
  - `Reader.daily/minute/fzline` 对"文件不存在"和"0 字节空文件"都返空 DataFrame
  - 文案说"不存在",空文件用户查"数据未下载",实际是磁盘满或下载中断
  - `_probe_tdxdir` 只在启动期探一次 `.day`,不探 `.lc5`
- **建议**:
  1. `Reader.daily/minute/fzline` 调用前先 `Path(...).exists() and stat().st_size > 32`(TDX 文件头 32 字节,小于此即损坏)
  2. 文件存在但 < 32 字节返 `_error_result(FILE_CORRUPTED)`(可在 `MooTDXErrorType` 加一个)
  3. `_index_minute_file_message` 措辞改"文件缺失或损坏"

### 3.8 `get_stock_info` partial failure

#### H3-静默. `_get_stock_info_sync` 把"在线挂"+"离线挂"合并为 no_data
- **文件**: `mootdx2_client.py:3251-3312`
- **现状**: 在线 quote 失败 quote=None、离线 daily/minute 失败返空 list,聚合后用户看到 `quote=null, kline=[], minute=[]` → "无数据"
- **失败场景**:
  - TDX TCP 断 + vipdoc 误删 → 全空,误报"无数据"
  - TDX 通 + vipdoc 缺该股票 `.day` → `quote={...}, kline=[], minute=[]` → success 分支(因 quote 非 None),但用户看到 `kline=[]` 不知道是文件缺失还是该股票真没数据
- **建议**: data 字典加 `meta: {quote_status, kline_status, minute_status}` 三个字段,分别取 `"ok"|"no_data"|"error"`

---

## 四、MEDIUM (15 条,节选高价值)

| ID | 视角 | 类别 | 简述 | 位置 |
|---|---|---|---|---|
| M-func1 | 功能 | consistency | `get_daily_batch_local` 返回 key 格式不统一 (3 种) | `mootdx2_client.py:2328-2333` |
| M-func2 | 功能 | consistency | `get_daily_batch_local` warn 缺 `recoverable: True` | `:2542` |
| M-func3 | 功能 | consistency | `get_index_fzline_local` 不支持日期窗 (与 index_minute 不对等) | `:2997-3046` |
| M-func4 | 功能 | consistency | 股票版 `get_fzline` 也不支持日期窗 | `:2619-2641` |
| M-perf1 | 性能 | concurrency | `_get_daily_batch_local_sync` 串行 20 次,可降 ~100ms | `:2299-2345` |
| M-perf2 | 性能 | memory | `Reader.daily()` 全量加载 +3 次 df 拷贝 (bool indexing/reset/to_dict) | `:2243-2297` |
| M-perf3 | 性能 | cache | `search_stock` `reader.block()` 整文件每次重读 50-200ms | `:3151-3194` |
| M-perf4 | 性能 | perf | `_minute_records` 逐行 strftime + pop 冗余 ~200µs | `:2863-2883` |
| M-perf5 | 性能 | perf | `indicator_atr` 嵌套 Python 循环,可 pandas 化 (~30µs) | `:3314-3349` |
| M-perf6 | 性能 | perf | `parse_symbol` 同次 miss 调 2 次 (~30µs) | `:2384,2266` |
| M-silent1 | 静默 | info_leak | `_error_result` `str(exc)[:100]` 暴露用户路径 | `mootdx2_errors.py:94-105` |
| M-silent2 | 静默 | wrong_type | `ValueError/KeyError/TypeError` 都标 `INVALID_PARAM`,bug 失真 | `mootdx2_errors.py:121-127` |
| M-silent3 | 静默 | consistency | `_with_warn` 返回 ToolResult 的 data 与缓存共享引用 | `:3587-3601` |
| M-silent4 | 静默 | contract | `get_index_kline_local` limit=0 静默返全集 | `:2769-2772,2808` |
| M-silent5 | 静默 | silent | V7.73+ `Customize.search` 失败仅 INFO,板块名 silent | `:631-645` |

---

## 五、LOW (22 条)

不展开,涵盖:

- 性能: `mootdx2_pool.py` 291 行死代码、`_top_board_l1` maxsize=128 易淘汰、`get_daily` TTL 写后不感知实际 9 点过期、`_filter_records_by_date` 不必要 list 拷贝、L1 写入 records 空校验弱、`parsed_for_key` 已 ship 修复、`cache_key` 每次调 `datetime.date.today()`
- 静默: `tdx_reader_patch` 升级后无 runtime 告警、L1 metrics 裸 `+=` 无锁、`_normalize_date_window` int 入参、`_probe_tdxdir` 一次性 stale probe、`no_data_result` `success=True` 但 error 非空(LLM 按 success 分流会忽略 NO_DATA)、`_run_blocking` 计数器 unprotected、`recoverable` 默认 `True` 让 router 盲目重试代码 bug、`mootdx2_pool._create_connection` error_count 未递增

---

## 六、按视角交叉印证的高优先级问题

1. **L1 缓存合同/语义**(3 视角交集):批量说"复用"实际不读、L1 key 内存放大、缓存整 ToolResult stale — 三件事相互绑定,应一并修复
2. **批工具语义**(2 视角):C1 全吞异常 + H4/H5 批内合同分裂 + M-perf1 串行 — 同一接口三层问题
3. **`get_minute/fzline` 容错**(2 视角):C2 ValueError 吞 + H2 suffix 静默 + H3 日历日静默 + M-silent4 limit=0 — 与指数版合同不对等
4. **静默 fallback 矩阵**(2 视角):`search_stock` + `get_stock_info` + `_index_minute_file_message` + `Customize.search` — 部署缺数据时无任何信号

---

## 七、关键设计原则 (用户裁决)

### 7.1 批量接口不加 L1

**原则**: 离线接口的批量入口(如 `get_daily_batch_local`)**不要在接口层级加 L1 缓存**。

**Why**: 批量接口的入参组合(代码集合 × adjust × start_date × end_date × limit 等)差异巨大;
即使 5 个字段、每字段 5 种值也是 5^5=3125 组合 → 同一 symbol 在不同组合下缓存多份全量 records → 内存冗余爆炸。
批量场景的正确做法是**让底层单 symbol 的 L1 做去重**(单 symbol 跨批量调用复用同一份缓存),
批量入口本身只负责聚合,不再加缓存层。

**How to apply**:
- 审视/设计离线缓存时,只对单 symbol 接口(`get_daily_local`/`get_minute_local`/`get_index_kline_local` 等)建议 L1
- 批量入口推荐"去重下沉到 sync 内核":把 L1 读/写逻辑放进 `_get_daily_sync` 内层,
  让批接口通过 sync 调用自然命中单 symbol L1
- 批 docstring 里写"复用 L1"应理解为:调底层 sync 函数时让底层去查/写单 symbol L1,
  而**不是**给批接口再加一层 `(code_tuple, adjust, start, end) → records` 的 cache
- 这条原则与 `feedback_tdxquant_thsdk_no_cache` 一起构成"哪些场景**不**缓存"的家族

### 7.2 daily L1 双层方案

- L1 主层: key=`(today, reader_symbol, adjust)`, value=全量 records
- 命中后用 `_filter_records_by_date` 在 records 上做 O(N) dict 过滤 (line 648-663 现成实现)
- 写 L1 时存**全量**,hit 路径统一出口切窗
- maxsize 2048 保持,单 symbol 占 1MB 不变

### 7.3 单 symbol L1 模板(8 工具接入)

详见 §3.4 H1-性能 表格。**禁止**给任何批量入口单独加 L1。

---

## 八、推荐落地批次

```
批次 1 [1 PR, HIGH/MED 杂,合同层]:
  - C1 (批工具异常处理: OSError/struct.error → _error_result + 全失败 success=False)
  - C2 (get_minute/fzline ValueError 透传)
  - H2-func (suffix 白名单) + H3-func (日期窗归一化)
  - H4-func (批内 000xxx 消歧透传) + H5-func (批内 limit 限制)
  - H1-silent (search_stock error 显式化)
  - M-silent4 (limit=0 校验)

批次 2 [L1 缓存体系,半天]:
  - H1-perf (8 个单 symbol 离线 L1,排除批量入口)
  - H2-perf + H4-silent (daily L1 双层 + 缓存只存 records 不存 ToolResult)
  - M-perf3 (block 缓存) 同 PR

批次 3 [合同一致性,半天]:
  - M-func 1-4 (批/指数/fzline 合同对齐)
  - M-perf1 (批并行化, 用底层单 symbol L1 复用做去重)

批次 4 [HIGH/MED 收尾]:
  - H2-silent (文件空 vs 缺失探测,加 stat().st_size > 32 守卫)
  - H3-silent (stock_info partial status 暴露)
  - M-silent1/M-silent2 (错误脱敏 + 异常分类)

批次 5 [LOW 收尾]:
  - 性能 L1-L7 (pool 死代码、TTL 绝对时间、微优化)
  - 静默 L1-L10 (补丁告警、metrics 锁、recoverable 默认值)
```

---

## 九、兼容性 / 风险

- **8 个单 symbol L1 加法**: 同 daily 一致,日内数据稳定 (TDX 文件不日内更新), TTL 7200s+ 几乎 0 错风险; index 类可能存在盘中点位偏移,建议 TTL=3600s + 同日
- **daily L1 双层改 key**: 全量缓存条目会比原 key 少 (~4x),但单条目大 (250KB), maxsize 2048 应保持
- **批并行化**: 需要确认 mootdx2 Reader 线程安全 (见 H3-性能);如不安全,批内顺序调用回退

---

## 十、与历史审计的关系

| 历史 | 已 ship | 本轮新增 |
|---|---|---|
| `mootdx2_perf_audit_2026_09_15` | H2-a (top_board/unusual L1 10s), M3 (search_stock 向量化) | **8 个单 symbol 离线工具无 L1**(本轮 H1-perf,排除批量入口)、**L1 key 内存放大**(本轮 H2-perf)、**Reader 闸门错配**(本轮 H3-perf) |
| `mootdx2_high_fixes_2026_09_19` | 假方法 / Reader 闸门 / batch_bars 周期码 / parse_symbol 收紧 | **批工具异常吞**(本轮 C1)、**get_minute/fzline 解析分裂**(本轮 C2/H2/H3) |

**累计三轮审视**:mootdx2 offline 体系的合同/性能/错误传播问题已经聚类到
**"L1 缓存 + 批工具 + 分钟线合同"** 三条主轴,建议后续审计集中在这三个域,不再全量重审。

---

## 十一、用户裁决 (2026-09-28)

### 4 个设计决策

| ID | 决策 | 含义 |
|---|---|---|
| D1 | **A** 下沉到 sync 内层 | `_get_daily_sync` 内层读/写 `self._daily_l1`,批入口(`get_daily_batch_local`)零改动,自然复用单 symbol L1 |
| D2 | **B** 接受现状 | Reader 信号量=4 + 单例不动,不调研 mootdx2 `StdReader` 线程安全;若生产观测到 race 再回头 |
| D3 | **B** 改文案不增错误类型 | 文件空/损坏合并到现有 `no_data` 文案("不存在或损坏"),不在 `MooTDXErrorType` 加 `FILE_CORRUPTED` |
| D4 | **A** 异常分类细化 | `KeyError`/`AttributeError`/`TypeError` 单列 `INTERNAL_ERROR`(不再误标 `INVALID_PARAM`),同时 `traceback.format_exc()` 写 logs |

### 影响批次落地的具体改动

- **批次 1**(C1/C2/H2-func/H3-func/H4-func/H5-func/H1-silent/M-silent4):
  - D1 落地:`_get_daily_sync` 入口加 L1 检查/写回,`get_daily_batch_local` 调用路径自然复用
  - D4 落地:`mootdx2_errors.py` 改 `isinstance(exc, (ValueError, KeyError, AttributeError, TypeError))` 分支,
    把 `KeyError/AttributeError/TypeError` 单走 `INTERNAL_ERROR` + `traceback.format_exc()` 到 logs
- **批次 2**(H1-perf 8 单 symbol L1 + H2-perf daily 双层 + H4-silent 缓存只存 records):
  - 配合 D1:`get_daily_local` L1 双层方案直接落地
  - 不动 Reader 闸门(D2)
- **批次 4**(H2-silent 文件空 vs 缺失 + H3-silent stock_info partial + M-silent1/2):
  - D3 落地:`_index_minute_file_message` 等文案改"不存在或损坏",**不**改 `MooTDXErrorType` 枚举
  - D4 落地见上(M-silent2)
- **H3-性能 Reader 闸门**:D2 决定不动,留作"若 race 观测回头处理",doc 注释

### 待用户裁决: ship 范围

5 个批次,详见 doc §八。请告知 ship 哪些批次 + 顺序偏好 + 测试策略,
按用户偏好"让改就只改,不主动跑测试/重启/apply/验证"
(feedback: `feedback_modify_dont_test_until_told`)。