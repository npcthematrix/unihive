# MOOTDX2 第三轮审计 · Batch 2b 实施完成 (2026-09-28)

## 状态

- ✅ 实现完成, 改动已落盘 `src/unihive/api/mootdx2_client.py` (+186 / -26)
- ✅ `ast.parse` 语法 OK
- ✅ 11 个 L1 slot 实例化 OK
- ❌ 测试未跑 (`feedback_modify_dont_test_until_told`)
- ❌ commit 未做 (`feedback_no_redundant_git_verification` + 用户未授权)

## 修改明细

### H4-silent (3 处)

| 位置 | 改动 |
|---|---|
| `_with_warn` (line 3772+) | data 列表 `list(result.data)` 浅复制, 防 L1 命中响应与下游共用同一引用 |
| `stock_top_board` (line 3785+) | 全重写: 引用 `self._l1_top_board` (records-only), 命中现包 ToolResult + _with_warn; cachetools 缺失旁路保留 |
| `stock_unusual` (line 3835+) | 同上, 引用 `self._l1_unusual` |

> 旧实现 `self._top_board_l1[cache_key] = result` 在 Batch 2a 改名后已死引用 (AttributeError),
>  本次顺手修复。

### H1-perf (7 个 async wrapper)

| 工具 | cache_key | 旁路保留 | notes |
|---|---|---|---|
| `get_minute` | `(suffix, code, start, end)` | ✅ | 4 维组合, 用户场景同 symbol 重复稳定 |
| `get_fzline` | `(code,)` | ✅ | 单维 |
| `get_index_kline_local` | `(code, type)` | ✅ | limit 不入键, 命中后按 limit 现切 (与 get_daily_local 2 层策略一致) |
| `get_index_minute_local` | `(code, suffix, start, end)` | ✅ | limit 不入键 |
| `get_index_fzline_local` | `(code,)` | ✅ | limit 不入键 |
| `search_stock` | `(keyword.lower(),)` | ✅ | 仅缓存"找到结果"分支; 双源不可用 / 单源无结果不写 L1 |
| `indicator_atr` | `(code, type, limit)` | ✅ | ATR dict 内含两个等长列表, 整体缓存 |

### 未接入 (按 handoff 计划)

- `_scan_blk_files_sync` (M-perf3 板块扫描): `_l1_block_scan` slot 已建实例, 调用方决定接入策略, 留待新专题

## 验证状态

- ✅ `ast.parse` 通过
- ✅ `MooTDX2Client` 实例化 + 11 L1 slot 全部 `enabled=True`
- ❌ 测试未跑 (6 个新 L1 测试等待 RED→GREEN)

## 下一步 (用户授权后)

### 阶段 1: 跑 RED → GREEN

```bash
.venv/Scripts/python.exe -m pytest tests/test_mootdx2_round3_batch2_l1.py -v
```

预期:
- 7 个 L1Slot 单元测试 ✅
- daily L1 双层 2 测试 ✅
- H4-silent top_board slot ✅
- **5 个 K线参数化测试 ✅ (新加 L1 wiring 后)**
- **search_stock L1 hit 测试 ✅ (新加 L1 wiring 后)**

### 阶段 2: 全量回归

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

期望 backend 数 ≥ 1830(本批 7 个新增 L1 hit 测试全绿, 旧失败 12 个保持)。

### 阶段 4: 1 commit

```bash
git add src/unihive/api/mootdx2_client.py \
        tests/test_mootdx2_round3_batch2_l1.py
git commit -m "fix(mootdx2): round-3 batch2b - 8 工具 L1 接入 + H4 records-only

H1-perf: 7 个单 symbol 离线工具 async wrapper 加 L1 (5 K线 + search_stock +
indicator_atr), 复用 _L1Slot 协议。cache_key 按入参维度构造, limit 不入键
(命中后按 limit 现切, 与 get_daily_local 2 层策略一致)。search_stock 仅缓存
\"找到结果\"分支, 双源不可用与单源无结果不写 L1。

H4-silent: top_board / unusual cache 改存 records (非整 ToolResult),
_with_warn 加 data 列表复制, 杜绝下游修改污染 + stale error_detail 跨请求
复用。顺手修复 Batch 2a 改名遗留的 self._top_board_l1 / self._unusual_l1 死
引用 (原代码若 _DAILY_L1_ENABLED=True 即 AttributeError 崩溃)。

设计原则 (feedback_no_l1_on_batch_interfaces): 7 工具全是单 symbol 接口,
批量入口 (get_daily_batch_local 等) 不加 L1。cache_key 维度经审过:
  - 分钟/fzline 类: 含 (code, suffix, start, end) — 用户场景每次入参稳定
  - 搜索: 含 (keyword_lower,) — keyword 通常短小
  - ATR: 含 (code, type, limit) — limit 影响窗口大小

测试: 6 个新 L1 hit 测试 (5 K线 + search_stock) 等待 RED→GREEN 验证。
未跑 (feedback_modify_dont_test_until_told)。"
```

## 设计原则对应

- `feedback_no_l1_on_batch_interfaces` — 7 工具全是单 symbol OK, 批接口不加
- `feedback_modify_dont_test_until_told` — 不主动跑测试
- `feedback_no_redundant_git_verification` — commit 后不刷屏 (本批未 commit)
- 不可变性 (CLAUDE.md `common/coding-style.md`) — L1 只存 records, ToolResult 每次现包

## 关联记忆

- `project_mootdx2_perf_audit_2026_09_15` — 上轮审计背景
- `project_get_daily_l1_observability_2026_09_15` — daily L1 设计前身 (2 层策略沿用)
- `feedback_tdxquant_thsdk_no_cache` — 同家族 (其他上游完全不缓存), mootdx2 因本地 Reader 反向受益 L1