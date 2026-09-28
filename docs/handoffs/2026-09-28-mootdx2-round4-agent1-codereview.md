# MOOTDX2 Round-4 代码审查报告 (Batch 2b 实施后)

日期: 2026-09-28
审查范围: Batch 2b 新增 7 个 L1 wrapper + H4-silent refactor
状态: AUDIT-ONLY, 未修改代码

---

## 审查发现汇总

| 严重度 | 数量 |
|---|---|
| CRITICAL | 1 |
| HIGH | 3 |
| MEDIUM | 4 |
| LOW | 3 |
| **合计** | **11** |

---

## 一、CRITICAL (1 条)

### CR1. `get_index_kline_local` L1 hit 路径存在引用共享 — 下游修改污染缓存

- **文件**: `src/unihive/api/mootdx2_client.py:3027-3030`
- **代码片段**:
  ```python
  cached_records = self._l1_index_kline.get(cache_key)
  if cached_records is not None:
      sliced = (
          cached_records[-limit:] if limit and len(cached_records) > limit
          else cached_records  # ← 当 limit >= len 时直接返回原引用！
      )
      result = ToolResult(success=True, data=sliced, source="mootdx2")
  ```
- **失败场景**:
  1. 第一次请求: `get_index_kline_local(code="sh000001", type="day", limit=10000)` — 假设文件有 5000 条
  2. L1 缓存写入 5000 条全量 records
  3. 第二次请求: `get_index_kline_local(code="sh000001", type="day", limit=10000)` (或任何 >=5000 的 limit)
  4. 条件 `limit and len(cached_records) > limit` 为 False → `sliced = cached_records` (同一引用)
  5. 下游对 `result.data` 做 `append()` / `pop()` / `clear()` → **污染 L1 缓存里的原始数据**
- **对比**:
  - `get_index_minute_local` (line 3185): `list(cached_records)` — 安全
  - `get_index_fzline_local` (line 3273): `list(cached_records)` — 安全
  - `get_index_kline_local` (line 3027): 直接引用 — **不安全**
- **建议修复**: 始终做浅复制: `sliced = list(cached_records[-limit:]) if limit and len(cached_records) > limit else list(cached_records)`

---

## 二、HIGH (3 条)

### H1. `get_index_kline_local` 缓存键使用原始输入 — 碰撞风险

- **文件**: `src/unihive/api/mootdx2_client.py:3022`
- **代码片段**:
  ```python
  cache_key = (code, type)  # code 是用户原始输入，未归一化
  ```
- **失败场景**:
  - `get_index_kline_local(code="sh000001", type="day")` → key = `("sh000001", "day")`
  - `get_index_kline_local(code="000001", type="day")` → key = `("000001", "day")`
  - 两者返回相同数据但缓存键不同，浪费缓存空间
- **对比**: `parse_symbol` 在 line 3013 已调用，归一化结果在 `parsed` 变量中，但未被用于缓存键
- **建议修复**: 使用归一化后的 symbol 构建缓存键: `cache_key = (to_reader_symbol(parsed), type)`

### H2. `get_minute` 缓存键类型不一致 — int vs str

- **文件**: `src/unihive/api/mootdx2_client.py:2738`
- **代码片段**:
  ```python
  cache_key = (suffix, code, start_date, end_date)  # suffix 可能是 int 或 str
  ```
- **失败场景**:
  - 调用 A: `get_minute(code="600036", suffix="1")` → key = `("1", "600036", "", "")`
  - 调用 B: `get_minute(code="600036", suffix=1)` → key = `(1, "600036", "", "")`
  - 两者行为等价 (line 2803: `str(suffix) == "1"`) 但缓存键不同
- **建议修复**: 缓存键构建时统一类型: `cache_key = (str(suffix), code, start_date, end_date)`

### H3. `search_stock` 缓存键未处理空白关键字

- **文件**: `src/unihive/api/mootdx2_client.py:3526`
- **代码片段**:
  ```python
  cache_key = (keyword.lower(),)
  ```
- **失败场景**:
  - 用户调用 `search_stock(keyword="   ")` (纯空白) → `keyword.lower()` 返回 `"   "`
  - 进入 sync 路径后 `_search_stock_sync` 可能返回空结果
  - 当前逻辑 (line 3541): 空结果返回 no_data，**不写缓存** — 正确
  - 但如果未来 `_search_stock_sync` 改了行为，可能误缓存空白键
- **建议修复**: 在入口校验空白: `if not keyword.strip(): return self._invalid_param_result("keyword 不能为空")`

---

## 三、MEDIUM (4 条)

### M1. 批量数据写入时缺少浅复制 — 潜在污染

- **文件**: `src/unihive/api/mootdx2_client.py:3047` (get_index_kline_local miss 路径)
- **代码片段**:
  ```python
  if records:
      self._l1_index_kline.put(cache_key, records)  # records 直接写入
  ```
- **分析**: `records` 来自 sync 函数返回 (line 3043-3045)，如果 sync 函数内部复用了某个可变对象，缓存写入后可能被污染
- **对比**: `get_index_minute_local` (line 3205): `self._l1_index_minute.put(cache_key, list(records))` — 使用 `list()` 复制
- **建议修复**: 写入前做浅复制: `self._l1_index_kline.put(cache_key, list(records))`

### M2. `get_index_fzline_local` 与 `get_index_minute_local` 行为不一致

- **文件**: `src/unihive/api/mootdx2_client.py:3267-3282` vs `3176-3195`
- **问题**:
  - `get_index_minute_local` (line 3185): `list(cached_records)` — 始终复制
  - `get_index_fzline_local` (line 3273): `list(cached_records)` — 也复制
  - 两者行为已一致，但 docstring 不对称: `get_index_fzline_local` 说"等价于 `get_index_minute_local(code, suffix='5')`" 但未提及 L1 行为
- **建议**: 统一 docstring 说明

### M3. L1 指标报告键名与清除函数不匹配 (命名不一致)

- **文件**: `src/unihive/api/mootdx2_client.py:963-973` vs `1015-1027`
- **问题**:
  - `get_metrics()` 返回键名: `minute_l1`, `fzline_l1`, `index_kline_l1`, ...
  - `reset_l1_caches()` 遍历的 slot 名称: `minute`, `fzline`, `index_kline`, ...
  - 功能正常 (reset 使用 slot.name 属性)，但命名风格不统一可能导致维护困惑
- **建议**: 统一命名风格，或在注释中说明

### M4. `get_fzline` / `get_minute` L1 hit 路径不做任何处理

- **文件**: `src/unihive/api/mootdx2_client.py:2741-2743` (get_minute), `2820-2822` (get_fzline)
- **代码片段**:
  ```python
  cached_records = self._l1_minute.get(cache_key)
  if cached_records is not None:
      return ToolResult(success=True, data=cached_records, source="mootdx2")
  ```
- **分析**:
  - 这两个工具不支持 limit 参数，因此不会发生引用共享问题 (不像 get_index_kline_local)
  - 但与 index 系列不一致: index 系列在 hit 路径会重新计算 warn
- **建议**: 保持现状即可，或添加注释说明差异原因

---

## 四、LOW (3 条)

### L1. `indicator_atr` 缓存键包含 limit — 可能非预期

- **文件**: `src/unihive/api/mootdx2_client.py:3676`
- **代码片段**:
  ```python
  cache_key = (code, type, limit)
  ```
- **分析**: ATR 计算依赖 K 线窗口，limit 影响结果。但 limit=100 和 limit=200 计算出的 ATR 值不同，缓存键包含 limit 是正确的
- **状态**: 非问题，记录在案

### L2. `_l1_block_scan` 已实例化但未接入

- **文件**: `src/unihive/api/mootdx2_client.py:521`
- **代码片段**:
  ```python
  self._l1_block_scan = _L1Slot("block_scan", maxsize=8, ttl=600)
  ```
- **分析**: Batch 2b 文档说明 "调用方决定接入策略, 留待新专题"
- **状态**: 按设计预期

### L3. `get_index_kline_local` miss 路径写入后直接使用原引用

- **文件**: `src/unihive/api/mootdx2_client.py:3047-3048`
- **代码片段**:
  ```python
  self._l1_index_kline.put(cache_key, records)
  result = ToolResult(success=True, data=records, source="mootdx2")  # records 未复制
  ```
- **分析**: 与 M1 相关。如果 records 被修改，缓存也被修改。已在 M1 标记
- **状态**: 与 M1 重复

---

## 五、Cross-reference with Round-3

| Round-4 ID | Round-3 对应 | 说明 |
|---|---|---|
| CR1 | H4-silent | 延伸: 缓存只存 records 是对的，但 hit 路径仍存在引用共享 |
| H1 | H4-silent | 缓存键归一化问题，与 Round-3 H4 缓存合同相关 |
| H2 | M-silent4 | suffix 类型一致性，与 limit=0 校验同属参数标准化 |
| H3 | H1-silent | search_stock 空白处理，是 H1-silent "双源不可用显式化" 的补充 |
| M1 | H4-silent | 写入时复制，与 CR1 同根 |
| M3 | - | 新发现: 指标命名不一致 |

---

## 六、审查结论

**质量评估**: Batch 2b 实现整体良好，L1Slot 抽象清晰，11 个槽的全生命周期管理 (初始化/查询/写入/清除/指标) 已覆盖。

**关键风险**: CR1 (get_index_kline_local 引用共享) 为**数据污染 bug**，需优先修复。

**建议修复优先级**:
1. CR1 — 必须修复
2. H1 — 建议修复 (缓存效率)
3. H2 — 建议修复 (一致性)
4. M1 — 建议修复 (防御性)
5. 其他 — 可选
