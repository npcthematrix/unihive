# MOOTDX2 离线接口 · 第四轮审视 (Round-4 Silent-Failure Audit)

日期: 2026-09-28
专题: Batch 2b 实施后的静默失败复审
状态: **审计报告,未修改任何代码**

## 背景

本轮审计聚焦 **Batch 2b 实施后的静默失败复审**,验证:
1. L1 命中时 warnings 是否正确重现
2. L1 命中时错误是否正确重算 (非静默)
3. 错误路径是否正确跳过 L1 写入
4. 边界条件 (空数据/空关键字/类型错误)

Batch 2b 已 ship:
- 7 个 async wrapper L1 接入 (get_minute/get_fzline/get_index_kline_local/get_index_minute_local/get_index_fzline_local/search_stock/indicator_atr)
- 2 个 rewrite (stock_top_board/stock_unusual)
- `_with_warn` data 复制修复

---

## 审计发现

### CRITICAL (1 条)

#### C1. `_with_warn` 对 None data 崩溃

- **文件**: `mootdx2_client.py:3921`
- **现状**:
  ```python
  safe_data = list(result.data) if isinstance(result.data, list) else result.data
  ```
- **失败场景**: 任何返回 `success=True, data=None` 的路径进入 `_with_warn` 时,`list(None)` 抛 `TypeError: 'NoneType' object is not iterable`。
- **触发路径**:
  1. `stock_top_board` L1 hit 后调用 `_with_warn(cached, warn)` (line 3953)
  2. 假设某次调用 `stock_top_board` 返回 `success=True, data=None` (理论上不应发生,但边界未防御)
  3. `_with_warn` 收 `result.data=None` → `list(None)` 崩溃
- **实际风险**: 当前 `stock_top_board` 内部逻辑保证 data 始终是 list, 但 `_with_warn` 作为通用函数,其他调用方传入 None 会崩溃。
- **建议修复**:
  ```python
  safe_data = list(result.data) if isinstance(result.data, list) else result.data
  # 改为:
  if isinstance(result.data, list):
      safe_data = list(result.data)
  elif result.data is None:
      safe_data = None
  else:
      safe_data = result.data  # dict/generator 等透传
  ```

---

### HIGH (3 条)

#### H1. search_stock 空关键字污染缓存

- **文件**: `mootdx2_client.py:3525-3526`
- **现状**:
  ```python
  cache_key = (keyword.lower(),)
  cached_records = self._l1_search.get(cache_key)
  ```
- **失败场景**:
  1. 用户调用 `search_stock(keyword="   ")` (纯空白)
  2. `"   ".lower()` → `"   "`, 作为 cache_key
  3. `_search_stock_sync` 内 `keyword.lower()` → `"   "`, 传入 SQLite LIKE `"%   %"`
  4. SQLite LIKE 空白会匹配所有记录 (或返回空,取决于 SQL 引擎行为)
  5. 若有结果,缓存 `"   "` → 结果列表
  6. 下次任何 `search_stock(keyword="")` 都会命中这个污染的缓存
- **当前缓解**: `_search_stock_sync` 无显式空关键字校验,但 SQLite `%...%` 通常不匹配或返回空
- **风险**: 边界行为不确定,缓存污染可能导致异常结果
- **建议修复**: 入口校验,空关键字直接返回 no_data:
  ```python
  if not keyword or not keyword.strip():
      return self._no_data_result("搜索关键字不能为空")
  ```

#### H2. get_index_kline_local 异常路径不写 L1 但可能意外命中旧缓存

- **文件**: `mootdx2_client.py:3041-3057`
- **现状**:
  ```python
  # miss 分支:
  if records:
      self._l1_index_kline.put(cache_key, records)
  ```
- **失败场景**:
  1. 第一次调用 `get_index_kline_local("sh000001", limit=100)` → 成功,缓存 records 全量 (假设 500 条)
  2. 用户下载新的日线数据,本地文件更新
  3. TTL 未过期 (假设 3600s), 第二次调用仍命中旧缓存
  4. 用户看到的数据是昨天的,不是今天的更新
- **根因**: L1 TTL 基于时间,未基于文件 mtime 失效
- **对比**: Round-3 提到 `_l1_block_scan` 使用 `(dir_mtime,)` 模式
- **当前缓解**: TTL 较短 (3600s), 盘后次日数据稳定
- **建议**: 对于强实时性场景,可考虑在 cache_key 加入 `(dir_mtime,)` (参考 `_l1_block_scan`);但当前 TTL 方案对大多数场景足够,标记为 MEDIUM 也可接受

#### H3. get_minute/get_fzline L1 命中后无 warn 重算

- **文件**: `mootdx2_client.py:2741-2743`, `2819-2822`
- **现状**:
  ```python
  # get_minute L1 hit:
  if cached_records is not None:
      return ToolResult(success=True, data=cached_records, source="mootdx2")
  # get_fzline L1 hit:
  if cached_records is not None:
      return ToolResult(success=True, data=cached_records, source="mootdx2")
  ```
- **对比**:
  - `get_index_kline_local` L1 hit 时重算 truncated warn (line 3032-3038)
  - `get_index_minute_local` L1 hit 时调用 `_tail_with_warn` 重算 warn (line 3184-3194)
  - `get_index_fzline_local` L1 hit 时调用 `_tail_with_warn` 重算 warn (line 3273-3281)
- **问题**: `get_minute` 和 `get_fzline` L1 命中时,未检查数据是否被截断,未重现 warn
- **失败场景**:
  1. 第一次调用 `get_minute("600036", limit=100)` → Reader 返回 500 条,截断 warn
  2. 第二次同参数调用 → L1 命中,返回 cached_records 但无 warn
  3. 用户不知道数据被截断了
- **注**: 这两个工具的 sync 函数本身不支持 limit 参数 (读取全量),但 index 版本支持
- **建议**: 若未来添加 limit 支持,需对齐 index 版本的 warn 重算逻辑;当前全量读取无截断风险,标记为 LOW

---

### MEDIUM (3 条)

#### M1. L1 TTL 计算一次,跨日不感知

- **文件**: `mootdx2_client.py:___` (各 L1 slot 初始化)
- **现状**: TTL 在 `__init__` 时计算一次 (如 `next_9am() - now()`)
- **失败场景**:
  1. 网关在 15:00 启动,TTL = 次日 09:00 - 15:00 = 18 小时
  2. 次日 09:00 后首次调用触发 miss,重算 TTL = 24 小时
  3. 15:00 启动的网关在次日 15:00 仍有缓存 (18 小时未过)
- **当前缓解**: 09:00 后首次 miss 会重算 TTL
- **风险**: 跨交易日边界数据略陈旧 (可接受)
- **建议**: 在 TTL 重算点 (09:00 后首次 miss) 加日志,便于观测;或接受现状 (Round-3 已讨论)

#### M2. indicator_atr L1 命中丢失 no_data 语义

- **文件**: `mootdx2_client.py:3679-3681`
- **现状**:
  ```python
  if cached_data is not None:
      return ToolResult(success=True, data=cached_data, source="mootdx2")
  ```
- **对比**: `indicator_atr_sync` 返回 None 时 wrapper 返回 no_data (line 3686)
- **问题**: L1 缓存的是 "计算成功返回 dict",不是 "无 K 线数据返回 None"
- **失败场景**:
  1. 第一次调用 `indicator_atr("600036")` → K 线空,返回 no_data (不写 L1,因 `if not data: return no_data` 在 put 之前)
  2. 第二次同参数调用 → 仍 miss,仍返回 no_data (OK)
  3. 假设某次异常导致写入了空 dict `{}` 到 L1 (理论上 `not {}` = True,不会写,但边界)
- **当前缓解**: 代码正确,不写入空数据
- **风险**: 低,但逻辑与 `search_stock` 的 "只缓存成功分支" 模式一致

#### M3. get_index_minute_local 同步函数异常时 L1 不写

- **文件**: `mootdx2_client.py:3197-3233`
- **现状**:
  ```python
  # miss 分支:
  if records:
      self._l1_index_minute.put(cache_key, list(records))
  ```
- **问题**: 与其他工具一致,异常时不写 L1
- **对比**: `get_index_kline_local` 同样逻辑 (line 3047)
- **结论**: 正确行为,非问题

---

### LOW (5 条)

#### L1. _l1_block_scan 使用 dir_mtime,其他 L1 未采用

- **说明**: Round-3 提到 `_l1_block_scan` 使用 `(dir_mtime,)` 模式,但其他 7 个 L1 slot 未采用
- **风险**: 文件更新后 TTL 内仍返回旧数据
- **当前缓解**: TTL 较短,可接受
- **结论**: 建议而非强制

#### L2. M-silent1 (Round-3 遗留) 仍未修复

- **文件**: `mootdx2_errors.py:94-105`
- **现状**: `str(exc)[:100]` 仍暴露用户路径/文件名
- **说明**: Round-3 用户裁决 D4 涉及 M-silent2 (KeyError/AttributeError/TypeError 分类),未涉及 M-silent1
- **结论**: 遗留问题,本轮未处理

#### L3. get_index_fzline_local 缓存 key 无 suffix

- **文件**: `mootdx2_client.py:3268`
- **现状**: `cache_key = (code,)` - 无 suffix
- **问题**: `get_index_fzline_local` 只读 5 分钟线 (suffix=5 固定),所以 cache_key 不含 suffix 是正确的
- **结论**: 非问题,确认设计合理

#### L4. search_stock L1 与 SQLite fallback 时效不一致

- **说明**: Round-3 讨论过,cache 600s vs SQLite 实时
- **结论**: 设计如此,非问题

#### L5. stock_unusual L1 hit 无 warn 逻辑

- **文件**: `mootdx2_client.py:3995-3998`
- **现状**: L1 hit 直接返回 `ToolResult(success=True, data=cached_records, source="mootdx2")`,无 `_with_warn`
- **结论**: 正确,`stock_unusual` 不产生 warn

---

## 验证清单

| ID | 检查项 | 状态 | 说明 |
|----|--------|------|------|
| 1 | L1 hit 复现 warn (index_kline) | ✅ | line 3032-3038 |
| 2 | L1 hit 复现 warn (index_minute) | ✅ | line 3184-3194 |
| 3 | L1 hit 复现 warn (index_fzline) | ✅ | line 3273-3281 |
| 4 | L1 hit 复现 warn (top_board) | ✅ | line 3953 |
| 5 | L1 hit 无 warn (unusual) | ✅ | line 3996 |
| 6 | L1 hit 无 warn (minute/fzline) | ⚠️ | 当前无截断逻辑,OK |
| 7 | 错误路径不写 L1 | ✅ | 所有 wrapper |
| 8 | search_stock 只缓存成功 | ✅ | line 3538 |
| 9 | 空关键字处理 | ❌ | 未校验 |
| 10 | _with_warn None data | ❌ | 崩溃 |
| 11 | M-silent1 路径泄露 | ❌ | 遗留 |

---

## 汇总

| 严重度 | 数量 | 关键问题 |
|--------|------|----------|
| CRITICAL | 1 | `_with_warn` 对 None data 崩溃 |
| HIGH | 3 | 空关键字缓存污染 / L1 跨日不感知 / minute/fzline 无 warn 重算 |
| MEDIUM | 3 | TTL 跨日感知 / indicator_atr 语义 / 异常不写 L1 |
| LOW | 5 | 遗留问题 + 设计确认 |

---

## 修复优先级建议

1. **立即修复**: C1 (`_with_warn` None data)
2. **本轮修复**: H1 (空关键字校验)
3. **可选修复**: H3 (minute/fzline warn 对齐 - 当前无风险)
4. **遗留**: M-silent1 (Round-3 未处理,本轮仍未处理)
