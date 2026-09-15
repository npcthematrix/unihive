# get_daily_local L1 可观测性 + single-flight — 设计稿

**日期**: 2026-09-15
**状态**: 设计 (待实施)
**关联**: 思路3 子集 (867c759) 的后续观察性补强

## 背景

`MooTDX2Client.get_daily` (gateway 名 `get_daily_local`) 在 867c759 引入 L1 内存缓存
(`TTLCache(maxsize=2048, ttl=7200)`) 后，承诺了 4 个观测指标
(hits / misses / evictions / expired) 但**只实现了 hits / misses 两个**，且**未暴露到任何接口**。

用户报告"随机慢没规律"，无法判断是 miss 命中失败、并发雪崩、还是 LRU 抖动。
本次 P0 修复：(A) 暴露完整指标；(C) single-flight 去重 first-miss 雪崩；新增控制台页。

## 目标 / 非目标

**目标**:
1. 用户在控制台直接看到 L1 命中率、当前 size、inflight 数
2. 同 symbol 并发 first-miss 只跑一次 `_get_daily_sync`
3. 改动可平滑回滚（单文件 + ≤ 200 行）

**非目标**（保留为后续 P1+）:
- 不动 maxsize / ttl（保留 2048 / 7200）
- 不加负缓存 / 复权缓存 / parse_symbol LRU
- 不改 Reader / Quotes / 线程池
- 不改路由 / 限流 / 缓存策略
- 不加前端 vitest 新 case（沿用现有 vue 套件）

## 组件 1：Python 指标 + single-flight

**文件**: `src/unihive/api/mootdx2_client.py`

### 1.1 实例字段扩展

现有 (`__init__` line 153-158)：

```python
self._daily_l1 = TTLCache(maxsize=2048, ttl=7200.0)
self._daily_l1_hits = 0
self._daily_l1_misses = 0
self._DAILY_L1_ENABLED = True
```

新增（紧跟其后）：

```python
# 仅 adjust=none 路径使用的去重字典 (single-flight):
#   key = (reader_symbol, "none"), value = asyncio.Future[ToolResult]
# 首个 miss 占位 Future 跑 _get_daily_sync, 后续 miss 共享同一 Future 结果。
self._daily_l1_inflight: dict[tuple[str, str], asyncio.Future] = {}
self._daily_l1_inflight_lock = asyncio.Lock()  # 索引互斥
```

### 1.2 `get_daily()` 数据流变更

替换 line 1655-1720 区域（cache 命中路径不变），miss 路径改为：

```text
parse_symbol(code)                              [现状]
 ↓
cache_key = (reader_symbol_key, adjust)
 ↓
cached = self._daily_l1.get(cache_key)
 ├─ hit → hits++ → filter_by_date → return     [不变]
 └─ miss → misses++ ↓
 if adjust == "none" and parsed_for_key:
     async with inflight_lock:
         existing = _daily_l1_inflight.get(cache_key)
         if existing:
             future = existing; is_leader = False
         else:
             future = loop.create_future()
             _daily_l1_inflight[cache_key] = future
             is_leader = True
     if not is_leader:
         return await future               ← 共享结果
 ↓
try:
    data = await run_in_executor(self._get_daily_sync, ...)
    result = build ToolResult
    if adjust == "none" and data and cacheable:
        _daily_l1[cache_key] = data
    return result
finally:
    if is_leader:
        if not future.done():
            future.set_result(result)        # 写完缓存再唤醒 waiter
        async with inflight_lock:
            _daily_l1_inflight.pop(cache_key, None)
```

**关键不变量**：
- leader 异常 / 取消 → `future.set_exception(...)`，waiter 拿到同一异常
- leader 拿到空 data（`[]`）→ 仍 `set_result(no_data_result(...))`，waiter 不挂起
- non-leader 路径 / `adjust != "none"` / `parse_symbol` 失败 → 完全旁路，不进 inflight dict

### 1.3 `get_metrics()` 扩展

替换 line 328-333：

```python
def get_metrics(self) -> dict:
    daily_l1_block: dict
    if self._DAILY_L1_ENABLED:
        hits = self._daily_l1_hits
        misses = self._daily_l1_misses
        total = hits + misses
        daily_l1_block = {
            "enabled": True,
            "hits": hits,
            "misses": misses,
            "hit_rate": (hits / total) if total > 0 else None,
            "size": len(self._daily_l1),
            "maxsize": 2048,
            "ttl_seconds": 7200,
            "inflight_keys": len(self._daily_l1_inflight),
        }
    else:
        daily_l1_block = {"enabled": False}
    return {
        **self._metrics,
        "daily_l1": daily_l1_block,
        "pool_stats": self.config.pool.get_stats() if self.config.pool else {},
    }
```

### 1.4 `stop()` 清理

在 line 651-655 现有 `daily_l1.clear()` 之后追加：

```python
# 取消所有 in-flight future, 防止 waiter 在 stop 后挂起
for fut in self._daily_l1_inflight.values():
    if not fut.done():
        fut.set_exception(asyncio.CancelledError())
self._daily_l1_inflight.clear()
```

## 组件 2：REST API 暴露

**文件**: `src/unihive/utils/console_api.py`

### 2.1 模块级注册表

新增（与 line 40 `set_omni_client` 对称）：

```python
_upstream_registry: dict[str, object] = {}

def set_upstream_registry(registry: dict[str, object]) -> None:
    """gateway_server 在 upstreams 装配完成后调用一次。
    注册表只用于查找 client 引用, 不持有任何 client 内部状态。"""
    _upstream_registry.clear()
    _upstream_registry.update(registry)


def _get_upstream_client(name: str) -> object | None:
    return _upstream_registry.get(name)
```

### 2.2 路由 + handler

在 line 827-830 现有 Route 注册附近新增：

```python
Route("/api/upstreams/<name>/metrics", upstream_metrics_api),

async def upstream_metrics_api(req: Request) -> JSONResponse:
    """返回某个上游 client 的运行时指标 (目前仅 mootdx2 实现 get_metrics())。
    404: 上游名未注册
    501: 上游客户端未实现 get_metrics()
    """
    name = req.path_params["name"]
    client = _get_upstream_client(name)
    if client is None:
        return JSONResponse(
            {"error": f"upstream '{name}' not registered"},
            status_code=404,
        )
    getter = getattr(client, "get_metrics", None)
    if not callable(getter):
        return JSONResponse(
            {"error": f"upstream '{name}' does not expose metrics"},
            status_code=501,
        )
    try:
        metrics = getter()
    except Exception as e:
        logger.warning(f"upstream_metrics_api({name}) raised: {e}")
        return JSONResponse(
            {"error": str(e)[:200]},
            status_code=500,
        )
    return JSONResponse({
        "upstream": name,
        "metrics": metrics,
        "timestamp": int(time.time()),
    })
```

### 2.3 gateway_server 注入

**文件**: `src/unihive/gateway_server.py`

在 line 494 (`self.upstreams[name] = client`) 装配完成后、所有上游 `start()` 之前插入：

```python
from .utils import console_api
console_api.set_upstream_registry(self.upstreams)
```

## 组件 3：Vue3 控制台页

### 3.1 路由

**文件**: `web/src/router/index.ts`，line 22-46 子路由数组中插入（按"上游状态"之后、"MCP APIs"之前的位置）：

```typescript
{
  path: 'l1-cache',
  name: 'l1-cache',
  component: () => import('@/views/L1CacheView.vue'),
  meta: { title: 'L1 缓存指标' },
},
```

### 3.2 侧栏菜单

**文件**: `web/src/layouts/DefaultLayout.vue`，line 121-127 MENU 数组插入：

```typescript
{ path: '/l1-cache', title: 'L1 缓存指标', icon: DataLine },
```

并修改 line 70 的 imports：

```typescript
import { ..., DataLine } from '@element-plus/icons-vue'
```

### 3.3 API 客户端

**新文件**: `web/src/api/upstream-metrics.ts`

```typescript
import { http } from './client'

export interface DailyL1Stats {
  enabled: boolean
  hits?: number
  misses?: number
  hit_rate?: number | null
  size?: number
  maxsize?: number
  ttl_seconds?: number
  inflight_keys?: number
}

export interface UpstreamMetrics {
  total_requests?: number
  total_errors?: number
  error_counts?: Record<string, number>
  pool_stats?: Record<string, unknown>
  daily_l1?: DailyL1Stats
  [k: string]: unknown
}

export interface UpstreamMetricsResponse {
  upstream: string
  metrics: UpstreamMetrics
  timestamp: number
}

export async function getUpstreamMetrics(
  name: string,
): Promise<UpstreamMetricsResponse> {
  const { data } = await http.get<UpstreamMetricsResponse>(
    `/api/upstreams/${encodeURIComponent(name)}/metrics`,
  )
  return data
}
```

### 3.4 视图

**新文件**: `web/src/views/L1CacheView.vue`

- 两个 el-card：「MooTDX2 L1 缓存」「配置常量」- 6 个 stat-card：命中率 / 命中 / 未命中 / 当前 size / maxsize / inflight keys
- 顶部进度条 + 状态灯 + 刷新时间戳
- 复用 StatusView 已有的 CSS 类（`.cache-grid` / `.cache-hitrate-card` / `.cache-mini-stat` / `.stat-num` / `.text-*`），不引入新样式
- 刷新策略：立即刷新按钮 + 自动刷新 10s switch + visibilitychange 监听（与 StatusView 一致）
- 降级：`daily_l1.enabled=false` → 「L1 缓存未启用」banner；4xx/5xx → 顶部红 banner + 上次快照保留

## 组件 4：测试（仅后端）

**新文件**: `tests/test_get_daily_l1_observability.py`

| # | 用例 | 断言 |
|---|---|---|
| 1 | L1 命中路径不走 single-flight | 两次相同请求，第 2 次 `_get_daily_sync` 调用计数 = 0 |
| 2 | 首次 miss + 并发 5 个同 key 请求 | `_get_daily_sync` 仅被调 1 次；5 个 ToolResult 内容一致 |
| 3 | 首次 miss 后数据写入 L1 | `len(client._daily_l1) == 1`；下次同 key 命中 |
| 4 | adjust=qfq 不进 L1 也不进 inflight | 两次 qfq 请求 `_get_daily_sync` 计数 = 2 |
| 5 | leader 异常时 waiters 拿同一异常 | mock `_get_daily_sync` raise，await 同 key 3 次都拿同一 exception |
| 6 | `get_metrics()` 含 daily_l1 子块 | 字段全齐：`enabled/hits/misses/hit_rate/size/maxsize/ttl_seconds/inflight_keys` |

**沿用 fixture**: `tests/test_mootdx2_reader_offline.py` 里的 `_make_client_with_mock_reader` mock Reader。

## 数据流（端到端）

```
MCP client → /mcp call_tool("get_daily_local", {...})
   ↓ gateway_server router
   ↓ MooTDX2Client.get_daily
   ↓ ┌─ check L1 cache (TTLCache)
   ↓ │  hit → filter → ToolResult
   ↓ │  miss → single-flight → run_executor → write cache → ToolResult
   ↓ └─
[后台观测]
client.get_metrics()  ←—— console_api.upstream_metrics_api  ←—— GET /api/upstreams/mootdx2/metrics
   ↓
web/src/api/upstream-metrics.ts getUpstreamMetrics("mootdx2")
   ↓
L1CacheView.vue 渲染 (10s 自动轮询)
```

## 上线与回滚

**回滚开关**（保留与思路3 一致的低回滚成本）：
- 关 single-flight：删除 `is_leader` 块（≤ 30 行），L1 仍生效
- 关 L1 指标展示：`get_metrics()["daily_l1"]` 改 `{"enabled": False}`，前端自动降级到空 banner
- 关整个功能：revert 单个 commit 即可

**观测**（上线后）：
- 控制台 `/l1-cache` 看 hit_rate / inflight_keys / size 24h 趋势
- `inflight_keys > 0` 长时间 = 有并发雪崩；`size == maxsize` 频繁 = 需要调大 maxsize

**不动**：
- 不改 pyproject.toml 依赖（cachetools 已有）
- 不改 YAML 配置
- 不改路由 / 限流 / 缓存策略
- 不动其他上游

## 风险评估

| 风险 | 缓解 |
|---|---|
| `asyncio.Lock()` 在 `__init__` 创建需 event loop | Python 3.10+ 无需 running loop；项目最低 3.10 |
| `get_metrics()` 抛异常导致 API 500 | handler 内 try/except 包住，返回 500 + 截断错误信息 |
| single-flight leader 拿 no_data 仍需 set_result | finally 块统一处理；waiter 拿到 ToolResult 不会挂起 |
| stop() 后 inflight 未清理导致 waiter 挂起 | stop() 内 set_exception(CancelledError) + clear |
| 前端 4xx/5xx 误吞 | 顶层红 banner + 上次快照保留 + 「重试」按钮 |
| 多个 client 引用旧 registry | set_upstream_registry 每次 clear + update |

## 不做的

- 复权缓存 / 负缓存 / parse_symbol LRU（保留为后续 P1+）
- 调整 LRU maxsize / ttl（保留 2048 / 7200，等指标上线 24h 后再决定）
- 改 Reader / Quotes 实例化与并发模型
- 写 SQLite 缓存
- 新增前端 vitest 单测（沿用现有 vue 套件，按用户决定）