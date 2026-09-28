# MOOTDX2 第三轮审计 · Batch 1 未提交 Handoff (2026-09-28)

## 上下文

- 上游: `docs/handoffs/2026-09-28-mootdx2-offline-round3-audit.md` (51 项审计, 用户裁决 D1=A/D2=B/D3=B/D4=A, 决定只 ship 批次 1, **每个批次 1 PR**)
- 设计原则: 批量接口**不加 L1 缓存** (见 `feedback_no_l1_on_batch_interfaces`), 单 symbol L1 在 sync 内核复用
- 截止本会话上下文临界 98% 停止;改动已落盘,**未跑测试 / 未 commit**

## Batch 1 范围 (8 项 + D4)

| ID | 类别 | 改动点 |
|---|---|---|
| **C1** | HIGH | 批异常吞: `_get_daily_batch_local_sync` 返三元组, 结构性失败不再静默 |
| **C2** | CRITICAL | `get_minute`/`get_fzline` ValueError 透传 (旧 `try/except ValueError → return []`) |
| **H2-func** | HIGH | `get_minute` suffix 白名单 ('1'/'5'), 拒绝 '2'/'15' 等 |
| **H3-func** | HIGH | `get_minute` `_normalize_date_window` 拒绝非法日历日 |
| **H4-func** | HIGH | 批内 000xxx warn 不论 records 空否都透传 |
| **H5-func** | HIGH | 批内 per-code `limit` 参数 (默认 0=不限) |
| **H1-silent** | HIGH | `search_stock` 双源 (tdxdir + board.db) 不可用 → `_SearchDataUnavailable` 显式 no_data |
| **M-silent4** | MED | `get_index_kline_local` limit=0 显式校验 (走错工具的静默返全集) |
| **D4** | DECISION | KeyError/AttributeError/TypeError → `INTERNAL_ERROR` (代码 bug 而非参数错) |

## 已修改文件 (未提交)

### 1. `src/unihive/models/mootdx2_errors.py`

- `ErrorClassifier.classify` (line 121-145): 在 ValueError → INVALID_PARAM 后, 插入 KeyError/AttributeError/TypeError → INTERNAL_ERROR 分支, 带语义化 `logger.error` 便于按类型过滤

### 2. `src/unihive/api/mootdx2_client.py`

**(a) 新增 `_SearchDataUnavailable` 异常类** (line 411 之前插入, MooTDX2Config 后 MooTDX2Client 前):

```python
class _SearchDataUnavailable(Exception):
    """search_stock 双源 (tdxdir + board.db) 都不可用, async 入口包装为 no_data。"""
```

**(b) `_get_daily_batch_local_sync`** (line ~2299):
- 新增 `limit: int = 0` 参数
- 返回类型改为 `tuple[dict[str, list[dict]], list[str], bool]`
- `try/except ValueError → INVALID_SYMBOL warn`
- `try/except (OSError, struct.error) → file_error warn + has_structural_errors=True`
- `try/except Exception: raise` (不再吞)
- 000xxx warn 不论 records 空否都 append

**(c) `get_daily_batch_local`** (async wrapper, line ~2476):
- 新增 `limit` 参数
- `has_structural_errors and not results` → `ToolResult(success=False, ...)` 含全部 warning
- 部分失败 → `ToolResult(success=True, data=results, error_detail={"warn": ...})`

**(d) `_get_minute_sync`** (line ~2551):
- 删 `try/except ValueError → return []`
- 加 `suffix_str in ("1", "5")` 白名单校验, raise ValueError
- 加 `_normalize_date_window(start_date, end_date)` 校验, raise ValueError
- 后续 `to_reader_symbol(parse_symbol(code))` 让 ValueError 自然透传

**(e) `_get_fzline_sync`** (line ~2619):
- 删 `try/except ValueError → return []`, `to_reader_symbol(parse_symbol(code))` 透传

**(f) `_search_stock_sync`** (line ~3236 前):
- 函数开头加前置守卫: `has_tdx` / `db_path.exists()` 都 false → raise `_SearchDataUnavailable`
- `reader.block()` 失败 `logger.debug` → `logger.warning` (运维可见)

**(g) `search_stock`** async wrapper:
- `_SearchDataUnavailable` → `self._no_data_result(str(e))`
- 单源无结果 (data 空) → `ToolResult(success=True, data=None, error="未找到匹配 X", error_detail={"error_type": no_data, ...})` 与双源不可用分流

**(h) `get_index_kline_local`** (line ~2808):
- 现有 `limit > MAX_LIMIT_KLINE` 校验后, 新增 `limit > 0` 校验 (拒绝 0 / 负数 / bool)

### 3. `tests/test_mootdx2_round3_batch1_fixes.py` (新建, 365 行)

9 测试类, AAA 模式, mock `mootdx2.reader.Reader.factory`:

- `TestBatchPartialFailure` (2 cases) — C1
- `TestMinuteFzlineValueErrorPropagate` (2 cases) — C2
- `TestMinuteSuffixWhitelist` (2 cases) — H2-func
- `TestMinuteDateWindowNormalize` (2 cases) — H3-func
- `TestBatch000xxxWarnPropagate` (1 case) — H4-func
- `TestBatchPerCodeLimit` (1 case) — H5-func
- `TestSearchStockExplicitError` (1 case) — H1-silent
- `TestIndexKlineLocalLimitZero` (2 cases) — M-silent4
- `TestD4ExceptionClassification` (4 cases) — D4 (含 ValueError 回归保护)

## 验证状态

- ❌ 测试未跑 (用户 `feedback_modify_dont_test_until_told`: 让改就只改)
- ❌ commit 未做 (用户 `feedback_no_redundant_git_verification`: 一次 commit + 一次确认即结束)
- ❌ gateway 未重启

## 下一步 (新会话续做)

1. 跑测试:
   ```bash
   .venv/Scripts/python.exe -m pytest tests/test_mootdx2_round3_batch1_fixes.py -v
   ```
2. 修测试 RED → GREEN (若有失败, 优先怀疑是 `_normalize_date_window` 等 helper 的 mock 不全或 get_daily_sync 的 000xxx warn 上游依赖)
3. 跑全量 backend 测试确认无回归:
   ```bash
   .venv/Scripts/python.exe -m pytest tests/ -q
   ```
4. 1 commit (用户决策: 每批次 1 PR):
   ```bash
   git add tests/test_mootdx2_round3_batch1_fixes.py \
           src/unihive/api/mootdx2_client.py \
           src/unihive/models/mootdx2_errors.py
   git commit -m "fix(mootdx2): round-3 batch1 - 8 contract fixes + D4 exception classification"
   ```
5. (可选) gateway 重启验证工具注册无破

## 风险/已知坑

- `_normalize_date_window` 在 `mootdx2_client.py` 模块级定义; 测试 mock 只 mock `mootdx2.reader.Reader.factory`, 不影响该 helper, OK
- `get_daily_sync` 内部对 '000xxx' 的跨市场歧义消歧 warn 是基于 `to_reader_symbol`/`parse_symbol` 真实返回的, 单测中 `parse_symbol('000001')` 可能没歧义 → H4 测试可能需要更精准 mock `_get_daily_sync` 而非 `reader.daily`; **首次跑测试若 H4 失败, 把 mock 改为 patch `client._get_daily_sync`**
- `_no_data_result` 是 MooTDX2Client 既有 helper (见 `models/mootdx2_errors.py` no_data_result), async wrapper 已使用, 无需新加

## 关联记忆

- `feedback_no_l1_on_batch_interfaces` — D1 设计原则
- `feedback_modify_dont_test_until_told` — 不主动跑测试
- `feedback_no_redundant_git_verification` — commit 后不刷屏
- `project_mootdx2_perf_audit_2026_09_15` — 上轮审计背景
- `project_mootdx2_quotes_kwarg_bug_2026_09_19` — DEGRADED 真因 (与本批无关, 但说明 mock 时别给空 dataframe)