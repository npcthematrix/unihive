# get_daily_local 内存缓存 — 设计稿

**日期**: 2026-09-15
**状态**: 设计 (待实现)
**目标工具**: `MooTDX2Client.get_daily` (gateway 名 `get_daily_local`)

## 症状

- 用户报告 `get_daily_local` 在多客户端高并发下"随机慢没规律"
- 已确认使用场景: 多客户端 + 高并发 + 99% 不复权 + 高重复 (同 symbol > 50%) + 全量 TDX 5000+

## 方案 (思路 3 子集)

**仅缓存 adjust=none 的 K线结果 (list[dict])，TTL 2h, maxsize 2048。**

### 实现

- 组件: `cachetools.TTLCache(maxsize=2048, ttl=7200)`
- 依赖: 加回 `cachetools>=5.3,<6.0` 到 `pyproject.toml`
- key: `(reader_symbol, "none")` — 只缓存不复权
- value: `records` (list[dict])
- start_date/end_date 过滤在缓存 records 上做 (dict 切片, O(N))

### 数据流

```
get_daily 入口
  ↓
calc cache_key = (reader_symbol, "none")
  ↓
cache.get(key) 命中?
  ├─ YES → in-memory filter by start/end date → return
  └─ NO  → 走原 _get_daily_sync → cache.set(key, records) → return
```

### 约束 (不变更)

- 复权 (qfq/hfq) **不进此缓存** — 99% 场景不复权 + 避免复权因子变更风险
- 不动 Reader / Quotes 实例数
- 不动线程池
- 不动 SQLite
- 不动 IO 路径

### 观测

- 加 4 个轻量计数器: hits / misses / evictions / expired
- 暴露到 `MooTDX2Client.get_metrics()` → 控制台 /api/status
- **回滚开关**: 删除 1 行 `cache.set` 即恢复原行为

### 测试

- 现有 580 测试全部通过 (无回归)
- 加 1 个 test: 第一次 miss → 第二次同 symbol 命中
- 加 1 个 test: 缓存空结果**不**写入

## 风险评估

- **低风险**: 只在 `get_daily` 入口加 dict 查 + 命中后切片
- **失败模式**: cache 行为完全旁路 → 走原路径 (与现版本完全一致)
- **内存上限**: 2048 × 4800 条 × ~200 字节 ≈ 200MB (maxsize 强约束)

## 不做的

- 思路 1 (线程池拆分) — 治标不治本
- 思路 2 (Reader 池 2-3 实例) — 全量 TDX 内存代价 400-1500MB, 不推荐
- 复权缓存 — 99% 不复权, 价值低
- 改 Reader/Quotes 并发模型
- 写 SQLite
