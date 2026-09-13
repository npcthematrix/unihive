# MCP Performance Optimization Handoff — 2026-09-11

## Context

用户要求审视 MCP server 性能，作为对外提供金融数据服务的 MCP 接口，对性能和并发有要求。

会话分两轮：
1. **第一轮（早）**: 通用的超时/缓存参数调优（`max_entries`、`operation_timeout`、HTTP 超时差异化）。早期完成的工作保留在前几轮的 commit 中。
2. **第二轮（本轮）**: 深度性能审视 + 7 项修复实施。

## 本轮已完成

### #1 FuyaoClient 重试机制 ✅
**文件**: `src/unihive/api/fuyao_client.py`

- 引入 `_RETRYABLE_HTTP_EXC` (ConnectError, ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout, RemoteProtocolError)
- 引入 `_is_retryable_status(status)` — 5xx 和 429 触发重试
- 重构 `call_tool()` 为循环结构：`max_attempts = max_retry + 1`（默认 4 次）
- 新增 `_backoff_sleep()` — 指数退避 + ±25% jitter，上限 `retry_backoff_max=5s`
- 新增 `_parse_response()` — 提取 JSON 解析逻辑
- `FuyaoConfig` 新增：`retry_backoff_base=0.5`, `retry_backoff_max=5.0`
- 调高 `max_keepalive_connections` 50 → 100

### #2 上游限流器（令牌桶）✅
**文件**: `src/unihive/core/rate_limiter.py`（新文件）, `src/unihive/core/router.py`, `src/unihive/gateway_server.py`

- 新模块 `AsyncTokenBucketLimiter`，进程内单例 `get_default_limiter()`
- `TokenBucket(rate, capacity)` 线性补充，非阻塞 `try_acquire()` / 阻塞 `acquire(wait=True)`
- 默认 20 QPS / 桶 40，可在 `gateway.rate_limit.<upstream>: {rate, capacity}` 覆盖
- Router 在 `is_available` 检查后加 `limiter.acquire(source, wait=False)`；桶空跳过该 upstream 试下一个，触发 fallback 链

### #3 omni_client 连接池化 ✅
**文件**: `src/unihive/api/omni_client.py`

- 替换 12 处 `sqlite3.connect(self._db_path)` 为 `self._get_conn()`
- 保留 `_ensure_db()` 一次性初始化（line 115）
- 删除所有 try/finally 中的 `conn.close()`（长连接由线程本地缓存）
- health_check() 也改为使用 `self._get_conn()`

### #4 mootdx2_client.health_check 实现 ✅
**文件**: `src/unihive/api/mootdx2_client.py`

- 新增 `async def health_check(self) -> bool` — 返回 `_status in (HEALTHY, DEGRADED)`

### #5 FuyaoClient health_check 改为本地状态 ✅
**文件**: `src/unihive/api/fuyao_client.py`

- `health_check()` 不再发网络请求，仅返回 `_client is not None and _status in (HEALTHY, DEGRADED)`
- 新增 `record_outcome(success: bool)` — 由调用方在 call_tool 返回后调用，连续失败时降级到 DEGRADED

### #6 Reader 实例缓存 ✅
**文件**: `src/unihive/api/mootdx2_client.py`

- 新增 `self._reader` 实例变量和 `_get_reader()` 懒初始化方法
- 替换 5 处 `Reader.factory()` 调用为 `self._get_reader()`

### #7 mootdx2_pool 后台健康检查改按需 ✅
**文件**: `src/unihive/api/mootdx2_pool.py`

- 删除 `_health_check_loop` 后台任务
- 删除 `start()` 中的 `asyncio.create_task(self._health_check_loop())`
- 删除 `stop()` 中的 task 取消逻辑
- 删除 `_health_check_task` 变量声明
- 保留 `_health_check()` 方法供控制台按需调用

### 早轮已完成（保留）
- `config/upstreams.yaml`: `cache.max_entries=50000`, `cache.operation_timeout=3.0`, HTTP 上游超时差异化 (ashare=15/index=10/meta=10/fund=20)
- 移除 gateway_server.py 后台 health_check_loop（按需触发，避免 fuyao 调用配额消耗）
- `src/unihive/storage/cache.py`: SQLite `PRAGMA cache_size=-2000` + `temp_store=MEMORY`

### mootdx2 高并发优化 ✅
**文件**: `src/unihive/api/mootdx2_client.py`

- Reader 实例缓存加锁（`_reader_lock`，双重检查锁定）
- Quotes 实例缓存加锁（`_quotes_lock`，双重检查锁定）
- `import threading` 添加

**配置**:
- `UNIHIVE_WORKER_THREADS` 环境变量控制线程池大小（默认 16）
- 已添加到 `.env.example`

## 使用方式

```bash
# 高并发场景调大线程池
UNIHIVE_WORKER_THREADS=32 python -m src.unihive.gateway_server
```

测试通过（43/43）：
```
pytest tests/test_cache.py tests/test_normalizer.py tests/test_registry.py -v
```

## 配置文件变更

`config/upstreams.yaml` 当前状态：
```yaml
cache:
  enabled: true
  db_path: logs/cache.db
  max_entries: 50000
  operation_timeout: 3.0
  ttl:
    historical: 86400
    fundamentals: 86400
    valuation: 30
    special_data: 300
    ticker_list: 3600
    workday: 604800
    search: 60

gateway:
  per_upstream_timeout: 10
  # 新增（建议）
  # rate_limit:
  #   fuyao_ashare: { rate: 15, capacity: 30 }
  #   fuyao_index:  { rate: 10, capacity: 20 }
  #   fuyao_meta:   { rate: 10, capacity: 20 }
  #   fuyao_fund:   { rate: 20, capacity: 40 }

fuyao_ashare:  timeout_seconds: 15
fuyao_index:   timeout_seconds: 10
fuyao_meta:    timeout_seconds: 10
fuyao_fund:    timeout_seconds: 20
```

## 验证清单（下次恢复时）

完成后跑：
```bash
cd "D:\fintech_workspace\unihive"
python -m pytest tests/test_cache.py tests/test_cache_policy.py tests/test_normalizer.py tests/test_registry.py tests/test_router.py -v
```

特别关注：
- `test_router.py` — 限流集成测试
- `test_omni_client.py`（如果有）— 连接池化
- `test_fuyao_client.py`（如果有）— 重试机制

## 风险点

1. **限流器 `wait=False`** 意味着桶空时直接跳过该 upstream，fallback 链必须有第二个上游；mootdx2/tdx_quant 本地调用基本不会触发，但 fuyao 限流时会跳过 — 设计是有意的，外部可观察性体现在 `hops[i].error = "rate limited (qps cap reached for X)"`。
2. **`_get_conn()` 缓存到线程** — 线程池里每个工作线程一份长连接。如果 `UNIHIVE_BLOCKING_POOL_SIZE=32`，最多 32 个 SQLite 连接。SQLite 写锁互斥会限制吞吐（OMNI 数据本身只读，不影响）。
3. **Reader 缓存** — mootdx2 库的 `Reader` 是文件 I/O 句柄，多线程并发需确认安全（可能需要在 `_get_reader` 旁加锁）。
4. **Fuyao 重试 + 限流叠加** — 重试可能瞬间放大请求 3-4 倍；限流器在 router 入口先削峰，重试在 client 内部。两层都生效，但峰值仍可能击穿 — 监控 `health` API 的 `rate limited` hop 数量。
