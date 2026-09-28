# MOOTDX2 第三轮审计 · Batch 2b 未提交 Handoff (2026-09-28)

## 上下文

- 上游: `docs/handoffs/2026-09-28-mootdx2-offline-round3-audit.md` §八 批次 2
- 已 ship (commit `ef26e42`): L1Slot 抽象 + 8 slot 实例创建 + H2-perf daily L1 双层
  + get_metrics/reset/stop 适配 + _get_daily_sync 返回 FULL records + batch 切窗
- 本会话上下文临界 96% 停止; 改动**部分落盘未 commit**

## Batch 2b 范围 (2 项未 ship)

### H1-perf: 8 个单 symbol 离线工具 L1 async wrapper 接入

8 工具对应的 `_l1_*` slot 实例已在 ef26a commit 中创建, 现在需要把 async wrapper
改造成"查 L1 → miss 时跑 sync → 写 L1"模板。每个工具的 cache_key 不同, 见下表。

| 工具 | cache_key | 现有 async 行号 | 现有 sync 行号 |
|---|---|---|---|
| `get_minute` | `(suffix, code, start_date, end_date)` | `:2720` | `:2612` |
| `get_fzline` | `(code,)` | `:2774` (估算) | `:2749` |
| `get_index_kline_local` | `(code, type)` | `:2858` | `:1640` |
| `get_index_minute_local` | `(code, suffix, start_date, end_date)` | `:3011` | `:2975` |
| `get_index_fzline_local` | `(code,)` | `:3095` | `:3087` |
| `search_stock` | `(keyword_lower,)` | `:3343` | `:3242` |
| `indicator_atr` | `(code, type, limit)` | `:3477` | `:3441` |
| `_scan_blk_files_sync` (block cache M-perf3) | 文件级 `(dir_mtime,)` | `:1975` | n/a (同步 helper, 由调用方决定) |

### H4-silent: top_board / unusual cache records-only

- 文件 `mootdx2_client.py:3756-3774` (stock_top_board)
- 文件 `mootdx2_client.py:3790-3805` (stock_unusual)
- 现状: `self._top_board_l1[cache_key] = result` (存整 ToolResult)
- 改造: 改存 records (`self._l1_top_board.put(cache_key, data)`),
  命中时重组 ToolResult (`ToolResult(success=True, data=copy_list(data), source="mootdx2")`).
- `_with_warn` line 3714+: 已 dict() copy error_detail, 但 **data 与缓存共享引用**;
  需 `data=list(result.data) if result.data else result.data` 复制

## 已修改文件 (未提交)

### 1. `tests/test_mootdx2_round3_batch2_l1.py`

- 6 个 `@pytest.mark.skip` 装饰器**已移除** (line 163, 199 的装饰器)
- TestEightToolsL1 的 5 个 K线参数化 + search_stock 共 6 测试**现在会 RED**
  (impl 还未做), 跑测试会出现 6 失败

### 2. `src/unihive/api/mootdx2_client.py`

**未修改**。下批需要:
- 8 个 async wrapper 加 L1 check/write 模板
- `_with_warn` 加 data 列表复制
- top_board / unusual 改 records-only

## 验证状态

- ❌ 测试未跑 (本会话停止前)
- ❌ commit 未做

## 下一步 (新会话续做)

### 阶段 1: 跑测试确认 RED

```bash
.venv/Scripts/python.exe -m pytest tests/test_mootdx2_round3_batch2_l1.py -v
```

预期: 6 失败 (5 个 K线参数化 + search_stock) + 7 通过。

### 阶段 2: 写最小化 Get 模板 (8 工具共享)

```python
async def get_minute(self, code, suffix="1", start_date="", end_date=""):
    self._metrics["total_requests"] += 1
    cache_key = (suffix, code, start_date, end_date)
    if self._l1_minute.enabled:
        cached = self._l1_minute.get(cache_key)
        if cached is not None:
            return ToolResult(success=True, data=cached, source="mootdx2")
    try:
        data = await self._run_blocking(self._get_minute_sync, code, suffix, start_date, end_date)
        if data and self._l1_minute.enabled:
            self._l1_minute.put(cache_key, data)
        if data:
            return ToolResult(success=True, data=data, source="mootdx2")
        return self._no_data_result(...)
    except Exception as e:
        return self._error_result(e, f"get_minute({code})")
```

每个工具的差异仅在 cache_key 和 sync 调用签名。

### 阶段 3: 接入 8 工具

1. **get_minute** (line 2720)
2. **get_fzline** (line ~2774) — cache_key `(code,)`, suffix 固定 5
3. **get_index_kline_local** (line 2858) — cache_key `(code, type)`, 注意 `type` 参数门控 (已经有 max_limit + type 校验)
4. **get_index_minute_local** (line 3011)
5. **get_index_fzline_local** (line 3095)
6. **search_stock** (line 3343) — cache_key `(keyword.lower(),)`; 现有 H1-silent 已加双源守卫, L1 命中要跳过 Reader 调用 + 双源检查
7. **indicator_atr** (line 3477) — cache_key `(code, type, limit)`; 内部先查 daily L1 复用 K线
9. `_scan_blk_files_sync` (M-perf3) — 缓存 `dir_mtime` 级别; 调用方需要明确

### 阶段 4: H4-silent records-only 改造

- `stock_top_board` 现有 (line 3756+):
  ```python
  cached = self._top_board_l1.get(cache_key)  # 旧 TTLCache
  ...
  self._top_board_l1[cache_key] = result  # 存 ToolResult
  ```
  改为:
  ```python
  cached_records = self._l1_top_board.get(cache_key)
  if cached_records is not None:
      # 每次新包 ToolResult, 避免引用共享
      fresh = ToolResult(success=True, data=list(cached_records), source="mootdx2")
      return self._with_warn(fresh, warn)
  ...
  data = await self._run_blocking(...)
  self._l1_top_board.put(cache_key, data)  # 存 records
  result = ToolResult(success=True, data=data, source="mootdx2")
  return self._with_warn(result, warn)
  ```

- `_with_warn` (line 3714):
  ```python
  if not warn or not result.success:
      return result
  detail = dict(result.error_detail or {})
  detail["warn"] = warn
  ...
  # 加: data 复制防污染
  new_result = ToolResult(
      success=result.success,
      data=list(result.data) if result.data else result.data,
      source=result.source,
      error=result.error,
      error_detail=detail,
      duration_ms=result.duration_ms,
  )
  return new_result
  ```

- `stock_unusual` (line 3790+) 同 top_board 改造

### 阶段 5: 跑测试 + 全量验证

```bash
.venv/Scripts/python.exe -m pytest tests/test_mootdx2_round3_batch2_l1.py -v
# 期望 13/13 全 GREEN

.venv/Scripts/python.exe -m pytest tests/ -q
# 期望 backend 通过数 = 1832 (= 1825 + 本批新增 7); 12 预存在失败不变
```

### 阶段 6: 1 commit (per "每个批次 1 PR" 用户决策)

```bash
git add tests/test_mootdx2_round3_batch2_l1.py \
        src/unihive/api/mootdx2_client.py
git commit -m "fix(mootdx2): round-3 batch2b - 8 工具缓存接入 + H4 records-only

H1-perf: 8 个单 symbol 离线工具 L1 async wrapper 接入 (5 K线 + search_stock +
indicator_atr + block_scan), 复用 _L1Slot 协议。cache_key 按入参维度构造。

H4-silent: top_board / unusual cache 改存 records (非整 ToolResult),
_with_warn 加 data 列表复制, 杜绝下游修改污染 + stale error_detail 跨请求复用。

设计原则 (feedback_no_l1_on_batch_interfaces): 8 工具全是单 symbol 接口,
批量入口不加 L1。cache_key 维度经审过:
  - 分钟/fzline 类: 含 (code, suffix, start_date, end_date) — 用户场景每次入参稳定
  - 搜索: 含 (keyword_lower,) — keyword 通常短小
  - 板块扫描: 含 dir_mtime — 文件级失效

测试: 13/13 全 GREEN (L1Slot 单元 + daily 2 层 + 8 工具 L1 hit + records-only)"
```

## 风险/已知坑

- **get_minute (line 2720) suffix='5' 路径**: 旧码 suffix='5' 走 fzline (.lc5),
  现在 cache_key 含 suffix, suffix='5' 的 cache 与 suffix='1' 的 cache 完全隔离 (OK)
- **search_stock (line 3343) H1-silent 已加双源守卫**: L1 命中需要先检查双源,
  还是让守卫在 L1 miss 路径兜底? 推荐: 双源守卫在 L1 路径**之前**(业务前置),
  L1 只缓存成功路径的结果
- **indicator_atr**: 内部可能查 daily L1, 自调用; 需注意死锁 (asyncio 同一 task 内
  调用应该 OK, 因为 L1.get/put 都是同步)
- **_with_warn 复制**: ToolResult dataclass 可能 frozen, 需 inspect; 如果不能直接
  替换字段, 改用 `dataclasses.replace`
- **测试 RED → GREEN 路径**: 5 个 K线参数化测试需要确保 mock 真实覆盖到 L1 命中;
  如果某工具实现 L1 但 cache_key 不一致, 第二次会 miss → 测试失败

## 关联记忆

- `feedback_no_l1_on_batch_interfaces` — 设计原则, 8 工具全是单 symbol OK
- `feedback_modify_dont_test_until_told` — 不主动跑测试
- `feedback_no_redundant_git_verification` — commit 后不刷屏
- `project_get_daily_l1_observability_2026_09_15` — daily L1 设计前身
- `project_mootdx2_perf_audit_2026_09_15` — 上轮审计背景