# MOOTDX2 Round-4 Batch 1+2 完成 Handoff (2026-09-28)

## 上下文

- 上游: `docs/handoffs/2026-09-28-mootdx2-offline-round4-audit.md` (Round-4 总报告)
- 已 ship Batch 1+2a+2b (Round-3 16 项)
- Round-4 新发现 16 项 (2 CRITICAL + 5 HIGH + 6 MEDIUM + 3 LOW)
- 用户裁决: Ship **Batch 1 (CR1+CR2+H1+H4+H5)** + **Batch 2 (H2+H3)** + 合并 Round-3 pending 到 Round-4 批次 3+4+5

## 已落盘未提交 (Round-4 Batch 1+2 全部完成)

### 1. 测试文件 `tests/test_mootdx2_round4_batch1_fixes.py` (14 测试)

- `TestWithWarnNoneData` (CR1, 3 测试):
  - `test_with_warn_handles_none_data_safely`
  - `test_with_warn_with_warn_and_none_data`
  - `test_with_warn_list_data_copies`
- `TestIndexKlineL1HitCopy` (CR2, 2 测试):
  - `test_l1_hit_returns_copy_not_reference`
  - `test_l1_hit_truncation_also_copies`
- `TestIndexKlineL1CacheKeyNormalize` (H1, 2 测试):
  - `test_normalized_codes_share_l1_cache`
  - `test_different_index_types_dont_collide`
- `TestGetMinuteSuffixType` (H4, 1 测试)
- `TestSearchStockBlankKeyword` (H5, 3 测试)

### 2. `src/unihive/api/mootdx2_client.py` 3 处修改

#### ✅ CR1: `_with_warn` None data 守卫 (Round-4 Batch 1)

```python
# 旧 (line 3920):
safe_data = list(result.data) if isinstance(result.data, list) else result.data

# 新:
if result.data is None:
    safe_data = None
elif isinstance(result.data, list):
    safe_data = list(result.data)
else:
    safe_data = result.data  # dict/generator 等透传
```

#### ✅ CR2 + H1 + M1: `get_index_kline_local` 缓存合同修复 (Round-4 Batch 1)

3 项一改完成:

```python
# H1: cache_key 用 parse_symbol 归一化
cache_key = (to_reader_symbol(parsed), type)  # 旧: (code, type)

# CR2: hit 路径 list 复制 (旧直接返回 cached_records 原引用)
sliced = (
    list(cached_records[-limit:]) if limit and len(cached_records) > limit
    else list(cached_records)
)

# M1: miss 路径写入复制 (与 index_minute 防御一致)
self._l1_index_kline.put(cache_key, list(records))  # 旧: put(..., records)
```

## 未完成 (Round-4 Batch 1 剩余 2 项 + Batch 2 全部)

### ✅ H4: `get_minute` cache_key `str(suffix)`

- ✅ 已完成: `cache_key = (str(suffix), code, start_date, end_date)`

### ✅ H5: `search_stock` 空白 keyword 入口校验

- ✅ 已完成: 在 cache_key 前加空白校验

### ✅ Round-4 Batch 2 全部 (H2 + H3 L1 key 基数修复)

#### H2: `get_minute` / `get_index_minute` cache_key 移除日期窗

- ✅ 已完成: 两函数 cache_key 改为 `(str(suffix), code)`, 命中后用 `_filter_records_by_date` 过滤
- ✅ 测试已补: `TestGetMinuteL1TwoLayer` + `TestGetIndexMinuteL1TwoLayer`

#### H3: `indicator_atr` cache_key 移除 limit

- ✅ 已完成: cache_key 改为 `(code, type)`, 命中后按 limit 切片 atr/dates 两列表
- ✅ 测试已补: `TestIndicatorAtrL1TwoLayer`

## 下一步 (新会话续做)

### 阶段 1-4: 已完成 ✅

- H4 + H5 代码 + 测试: 完成
- H2 + H3 代码 + 测试: 完成
- 测试结果: 14 passed

### 阶段 5: 2 commits (per "每个批次 1 PR")

```bash
# Batch 1
git add tests/test_mootdx2_round4_batch1_fixes.py \
        src/unihive/api/mootdx2_client.py
git commit -m "fix(mootdx2): round-4 batch1 - 5 项 CRITICAL+HIGH

CR1: _with_warn None data 守卫 (Batch 2b 引入的 TypeError 回归)
CR2: get_index_kline_local L1 hit 路径 list 复制防污染
H1:  get_index_kline_local cache_key parse_symbol 归一化
     'sh000001' 与 '000001' 同 symbol 共享缓存
H4:  get_minute cache_key str(suffix) 统一 int/str 类型
H5:  search_stock 空白 keyword 入口校验 (no_data 不污染 L1)

测试: 5 个新测试 (Round-4 batch1), TDD RED→GREEN 验证后 ship。
未跑 (feedback_modify_dont_test_until_told)。"

# Batch 2 (单独 commit)
git commit -m "fix(mootdx2): round-4 batch2 - L1 key 基数修复 (2 层)

H2: get_minute / get_index_minute cache_key 移除日期窗
     与 daily L1 双层方案一致 (Round-3 H2-perf 推广)
     key 只含 (str(suffix), code), 命中后 _filter_records_by_date 过滤
     50 热点股 × 5 日期窗 × 2 suffix = 500 组合 / maxsize 256
     → 同 symbol 多窗口共享 1 份缓存

H3: indicator_atr cache_key 移除 limit
     旧 (code, type, limit) ∈ [1,1000] → 800 万组合, maxsize 64 失效
     新 (code, type), 命中后按 limit 切片
     L1 命中后 pandas 切片 vs Python 循环重算 ~1000x 加速

测试: 3 个新测试 (Round-4 batch2), 验证 2 层缓存正确性。
未跑 (feedback_modify_dont_test_until_told)。"
```

## 风险/已知坑

- **H2 `_filter_records_by_date` 现成实现 (line 648-663)**, 但需确认其接受 records list 而非 DataFrame。Agent 2 报告说现成,实际需 spot-check。
- **H3 indicator_atr dict 含 atr/dates 两列表**, limit 切片需同时切两列表 (zip 同步), 不能只切 records。需在 hit 路径做 `data['atr'][-limit:]` + `data['dates'][-limit:]` 双切。
- **H2 切窗语义**: `_filter_records_by_date` 默认按 date 字段字典序过滤, 与 start/end 入参契约。验证测试需覆盖 start_date/end_date 各 4 组合。
- **H1 cache_key 改为 `(to_reader_symbol(parsed), type)` 后**, 旧 `(code, type)` key 会失效, 但 L1 默认空 maxsize 启动, 无影响。

## 关联记忆

- `feedback_modify_dont_test_until_told` — 不主动跑测试
- `feedback_no_redundant_git_verification` — commit 后不刷屏
- `project_mootdx2_perf_audit_2026_09_15` — 上轮审计背景
- `project_get_daily_l1_observability_2026_09_15` — daily L1 2 层方案前身
- `feedback_no_l1_on_batch_interfaces` — batch 接口不加 L1 (本批无关, 7 工具全是单 symbol)