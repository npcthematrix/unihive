# get_daily_local L1 可观测性 + single-flight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `MooTDX2Client.get_daily` 暴露 L1 缓存指标并加入 single-flight 去重 first-miss 雪崩，控制台新增 `/l1-cache` 页面实时展示。

**Architecture:**
- Python 侧：`get_daily()` miss 路径包一层 `asyncio.Future` 字典去重；`get_metrics()` 返回 `daily_l1` 子块；新增 `set_upstream_registry()` + `/api/upstreams/<name>/metrics` REST 端点。
- Vue 侧：复用 StatusView 的 cache 卡片样式，新增 `L1CacheView.vue` 拉 `/api/upstreams/mootdx2/metrics`，侧栏加菜单项。

**Tech Stack:** Python 3.10+ / asyncio / cachetools / FastMCP / Vue 3 / Element Plus / TypeScript / vitest

---

## File Structure

**Modify**:
- `src/unihive/api/mootdx2_client.py` — 单 flight + daily_l1 指标块 + stop() 清理
- `src/unihive/utils/console_api.py` — `set_upstream_registry` + `/api/upstreams/<name>/metrics` 路由
- `src/unihive/gateway_server.py` — 注入 upstream registry
- `web/src/router/index.ts` — 新增 `/l1-cache` 路由
- `web/src/layouts/DefaultLayout.vue` — 新增「L1 缓存指标」菜单

**Create**:
- `tests/test_get_daily_l1_observability.py` — 6 个后端单测
- `web/src/api/upstream-metrics.ts` — TS API 客户端
- `web/src/views/L1CacheView.vue` — 控制台页面

---

## Task 1: 添加 single-flight 实例字段 + stop() 清理

**Files:**
- Modify: `src/unihive/api/mootdx2_client.py:155-161` (字段区) + `651-673` (stop 方法)

- [ ] **Step 1: 在 `__init__` 添加 inflight 字段**

紧跟 line 158 `self._DAILY_L1_ENABLED = True` 之后，新增：

```python
# 2026-09-15 single-flight 去重 first-miss 雪崩:
#   key = (reader_symbol_key, "none"), value = asyncio.Future[ToolResult]
# 首个 miss 占位 Future 跑 _get_daily_sync, 后续 miss 共享同一 Future 结果。
# 仅 adjust="none" 路径使用, 与 L1 缓存 key 对齐。
self._daily_l1_inflight: dict[tuple[str, str], asyncio.Future] = {}
self._daily_l1_inflight_lock = asyncio.Lock()  # 索引互斥
```

- [ ] **Step 2: 在 `stop()` 中追加 inflight 清理**

定位 line 651-655 现有 `daily_l1.clear()` 之后，追加：

```python
# 取消所有 in-flight future, 防止 waiter 在 stop 后挂起
for fut in self._daily_l1_inflight.values():
    if not fut.done():
        fut.set_exception(asyncio.CancelledError())
self._daily_l1_inflight.clear()
```

- [ ] **Step 3: 跑现有测试, 确认无回归**

Run: `pytest tests/test_mootdx2_reader_offline.py tests/test_mootdx2_client.py -q`
Expected: 全部通过 (没动 get_daily 逻辑)

- [ ] **Step 4: Commit**

```bash
git add src/unihive/api/mootdx2_client.py
git commit -m "feat(mootdx2): get_daily L1 single-flight 实例字段 + stop 清理"
```

---

## Task 2: 为 single-flight 写 5 个失败测试

**Files:**
- Create: `tests/test_get_daily_l1_observability.py`

- [ ] **Step 1: 写测试文件 (TDD red)**

完整内容：

```python
"""get_daily L1 缓存可观测性 + single-flight 测试。

覆盖: 命中路径 / 首次 miss 单飞 / 并发雪崩去重 / adjust=qfq 旁路 /
leader 异常时 waiter 共享异常。
"""
import asyncio
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from src.unihive.api.mootdx2_client import MooTDX2Client, MooTDX2Config


@pytest.fixture
def client():
    return MooTDX2Client(MooTDX2Config(name="test", market="std"))


@pytest.fixture
def mock_setup(client):
    """client + mock Reader + 返回 2 条记录的 DataFrame."""
    client.config.settings = MagicMock()
    client.config.settings.tdxdir = "/fake/tdx"
    mock_df = pd.DataFrame(
        {"close": [10.0, 11.0]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )
    mock_reader = MagicMock()
    mock_reader.daily.return_value = mock_df
    return client, mock_reader, mock_df


def test_hit_path_does_not_call_reader_again(mock_setup):
    """两次相同请求, 第 2 次 _get_daily_sync 调用计数 = 0 (走 L1)."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        r1 = asyncio.run(client.get_daily("600036"))
        assert r1.success is True
        assert mock_reader.daily.call_count == 1

        r2 = asyncio.run(client.get_daily("600036"))
        assert r2.success is True
        # 第 2 次命中 L1, 不再调 reader
        assert mock_reader.daily.call_count == 1
        assert client._daily_l1_hits == 1
        assert client._daily_l1_misses == 1


def test_concurrent_miss_single_flight(mock_setup):
    """5 个并发同 symbol 请求, reader.daily 仅被调 1 次, 5 个结果一致."""
    client, mock_reader, _ = mock_setup

    async def run_concurrent():
        return await asyncio.gather(*[client.get_daily("600036") for _ in range(5)])

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        results = asyncio.run(run_concurrent())

    assert all(r.success for r in results)
    assert mock_reader.daily.call_count == 1
    # 5 个返回同一 records 列表引用 (single-flight 共享结果)
    assert all(r.data is results[0].data for r in results)


def test_first_miss_writes_to_l1(mock_setup):
    """首次 miss 后, _daily_l1 有 1 条; 同 key 再请求命中."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        asyncio.run(client.get_daily("600036"))
    assert len(client._daily_l1) == 1
    # 缓存 key 形状 = (reader_symbol, "none")
    key = next(iter(client._daily_l1.keys()))
    assert key == ("600036", "none")


def test_qfq_bypasses_l1_and_inflight(mock_setup):
    """adjust=qfq 不进 L1 也不进 inflight, 两次调用 reader 两次."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        asyncio.run(client.get_daily("600036", adjust="qfq"))
        asyncio.run(client.get_daily("600036", adjust="qfq"))
    assert mock_reader.daily.call_count == 2
    assert len(client._daily_l1) == 0
    assert len(client._daily_l1_inflight) == 0


def test_leader_exception_shared_by_waiters(mock_setup):
    """leader 抛异常, waiter 拿到同一异常 (走 get_daily 现有 error 路径)."""
    client, mock_reader, _ = mock_setup
    mock_reader.daily.side_effect = RuntimeError("boom")

    async def run_concurrent():
        return await asyncio.gather(
            *[client.get_daily("600036") for _ in range(3)],
            return_exceptions=True,
        )

    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        results = asyncio.run(run_concurrent())

    # 3 个结果都是 ToolResult (失败), 不是裸 exception
    from src.unihive.api.upstream_client import ToolResult
    assert all(isinstance(r, ToolResult) for r in results)
    assert all(not r.success for r in results)
    # reader 只被调 1 次
    assert mock_reader.daily.call_count == 1
    # inflight 已清理
    assert len(client._daily_l1_inflight) == 0
```

- [ ] **Step 2: 跑测试, 确认 5 个全 FAIL (因 single-flight 未实现)**

Run: `pytest tests/test_get_daily_l1_observability.py -v`
Expected: 5 个测试失败 (其中 `test_hit_path_does_not_call_reader_again` 应通过 — 是 baseline, `test_qfq_bypasses_l1_and_inflight` 也应通过 — 现有 qfq 不缓存, 其余 3 个 fail)

如果只有 baseline 通过, OK 继续。如果全 fail, 可能是 fixture 问题, 检查 mock_setup 是否正确返回。

- [ ] **Step 3: Commit (red)**

```bash
git add tests/test_get_daily_l1_observability.py
git commit -m "test(mootdx2): get_daily L1 single-flight 失败测试 (TDD red)"
```

---

## Task 3: 实现 single-flight 逻辑

**Files:**
- Modify: `src/unihive/api/mootdx2_client.py:1654-1720` (get_daily miss 路径)

- [ ] **Step 1: 替换 get_daily miss 路径**

完整替换 line 1686-1720 区域（cache hit 路径 line 1681-1684 不变）。新实现：

```python
            # miss → misses++ → 尝试 single-flight
            self._daily_l1_misses += 1
            cache_hit = False

            # 仅 adjust=none + 解析成功 → 走 single-flight 去重
            inflight_future = None
            is_leader = False
            if self._DAILY_L1_ENABLED and adjust == "none" and parsed_for_key is not None:
                async with self._daily_l1_inflight_lock:
                    existing = self._daily_l1_inflight.get(
                        (reader_symbol_key, adjust)
                    )
                    if existing is not None:
                        inflight_future = existing
                    else:
                        inflight_future = (
                            asyncio.get_running_loop().create_future()
                        )
                        self._daily_l1_inflight[
                            (reader_symbol_key, adjust)
                        ] = inflight_future
                        is_leader = True

            if inflight_future is not None and not is_leader:
                # waiter: 共享 leader 的 ToolResult
                shared = await inflight_future
                return shared

            # leader 路径 (or 非 none 路径 / inflight 旁路)
            try:
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(
                    get_blocking_executor(),
                    self._get_daily_sync,
                    code,
                    adjust,
                    start_date,
                    end_date,
                )
                meta = getattr(self, "_last_daily_meta", {"kind": STOCK_KIND, "warn": None})
                result = self._build_daily_tool_result(
                    code=code,
                    data=data,
                    meta=meta,
                    adjust=adjust,
                    parsed_for_key=parsed_for_key,
                    reader_symbol_key=reader_symbol_key,
                )
                # 写 L1 (仅 adjust=none + 有数据 + 解析成功)
                if (
                    self._DAILY_L1_ENABLED
                    and is_leader
                    and adjust == "none"
                    and data
                    and parsed_for_key is not None
                ):
                    self._daily_l1[(reader_symbol_key, adjust)] = data

                if is_leader and inflight_future is not None:
                    if not inflight_future.done():
                        inflight_future.set_result(result)

                return result
            finally:
                if is_leader and inflight_future is not None:
                    # 清理 inflight 字典 (异常路径也清理)
                    if not inflight_future.done():
                        # leader 路径中途异常 → 通过 set_exception 传播给 waiter
                        # 兜底: 万一 finally 之前已经 set_result, 这里跳过
                        inflight_future.set_exception(
                            asyncio.CancelledError("leader cleanup")
                        )
                    async with self._daily_l1_inflight_lock:
                        self._daily_l1_inflight.pop(
                            (reader_symbol_key, adjust), None
                        )
```

- [ ] **Step 2: 抽取 `_build_daily_tool_result` 助手**

为避免 get_daily 函数过长,把现有 line 1690-1720 的 ToolResult 构造逻辑抽出来。新增方法（紧跟 get_daily 之后）：

```python
    def _build_daily_tool_result(
        self,
        code: str,
        data: list,
        meta: dict,
        adjust: str,
        parsed_for_key,
        reader_symbol_key: str,
    ) -> ToolResult:
        """构建 get_daily 的 ToolResult, 区分正常数据 / no_data / 指数缺失等。"""
        from .index_registry import parse_symbol, INDEX_KIND
        if data:
            result = ToolResult(success=True, data=data, source="mootdx2")
            if meta.get("warn"):
                result.error_detail = {
                    "warn": meta["warn"],
                    "recoverable": True,
                }
            return result
        # 无数据: kind=index 给更明确的提示
        if meta.get("kind") == INDEX_KIND:
            try:
                parsed = parse_symbol(code)
                msg = (
                    f"指数 {parsed.code} 本地无 .day 文件"
                    f"（可能未下载完整本地数据）"
                )
            except ValueError:
                msg = "指数本地无 .day 文件（可能未下载完整本地数据）"
        else:
            msg = f"未找到 {code} 的日线数据"
        return self._no_data_result(msg)
```

- [ ] **Step 3: 跑测试, 确认全 PASS**

Run: `pytest tests/test_get_daily_l1_observability.py tests/test_mootdx2_reader_offline.py -v`
Expected: 11 个测试全 PASS

- [ ] **Step 4: 跑全量测试, 确认无回归**

Run: `pytest tests/ -q --ignore=tests/test_mootdx2_client.py 2>&1 | tail -10`
Expected: 580+ 测试通过 (除集成 / TDX 真实数据依赖项外)

- [ ] **Step 5: Commit (green)**

```bash
git add src/unihive/api/mootdx2_client.py
git commit -m "feat(mootdx2): get_daily single-flight 去重 first-miss 雪崩"
```

---

## Task 4: get_metrics() 增加 daily_l1 块 + 写测试

**Files:**
- Modify: `src/unihive/api/mootdx2_client.py:328-333`
- Modify: `tests/test_get_daily_l1_observability.py` (追加 test)

- [ ] **Step 1: 追加 test_metrics_includes_daily_l1_block 测试**

在 `tests/test_get_daily_l1_observability.py` 文件末尾追加：

```python


def test_metrics_includes_daily_l1_block(mock_setup):
    """get_metrics() 返回 daily_l1 子块, 字段全齐."""
    client, mock_reader, _ = mock_setup
    with patch("mootdx2.reader.Reader.factory", return_value=mock_reader):
        asyncio.run(client.get_daily("600036"))
        asyncio.run(client.get_daily("600036"))  # 命中

    m = client.get_metrics()
    assert "daily_l1" in m
    block = m["daily_l1"]
    assert block["enabled"] is True
    assert block["hits"] == 1
    assert block["misses"] == 1
    assert block["hit_rate"] == 0.5
    assert block["size"] == 1
    assert block["maxsize"] == 2048
    assert block["ttl_seconds"] == 7200
    assert block["inflight_keys"] == 0  # 全部完成, inflight 已清理
```

- [ ] **Step 2: 跑测试, 确认 FAIL**

Run: `pytest tests/test_get_daily_l1_observability.py::test_metrics_includes_daily_l1_block -v`
Expected: FAIL (daily_l1 字段未实现)

- [ ] **Step 3: 实现 get_metrics() daily_l1 块**

替换 line 328-333：

```python
    def get_metrics(self) -> dict:
        """获取客户端指标 — 含 L1 缓存运行时统计 (2026-09-15)."""
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

- [ ] **Step 4: 跑测试, 确认 PASS**

Run: `pytest tests/test_get_daily_l1_observability.py -v`
Expected: 6 个测试全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/unihive/api/mootdx2_client.py tests/test_get_daily_l1_observability.py
git commit -m "feat(mootdx2): get_metrics() 增加 daily_l1 子块 + 完整字段测试"
```

---

## Task 5: console_api.set_upstream_registry + 新 REST 端点

**Files:**
- Modify: `src/unihive/utils/console_api.py:40-46` (set_omni_client 区域) + line 827-830 (路由区)

- [ ] **Step 1: 增加 set_upstream_registry + 助手**

紧跟 line 46 `set_omni_client` 之后新增：

```python
_upstream_registry: dict[str, object] = {}


def set_upstream_registry(registry: dict[str, object]) -> None:
    """gateway_server 在 upstreams 装配完成后调用一次。
    注册表只用于查找 client 引用, 不持有任何 client 内部状态。
    """
    _upstream_registry.clear()
    _upstream_registry.update(registry)


def _get_upstream_client(name: str) -> object | None:
    return _upstream_registry.get(name)
```

- [ ] **Step 2: 增加 /api/upstreams/<name>/metrics 路由 + handler**

在 line 830 现有 `Route("/api/omni/sector-stocks", omni_sector_stocks_api),` 之后追加：

```python
        Route("/api/upstreams/<name>/metrics", upstream_metrics_api),

        async def upstream_metrics_api(req: Request) -> JSONResponse:
            """返回某个上游 client 的运行时指标 (目前仅 mootdx2 实现 get_metrics())。

            状态码:
              200: 正常返回 metrics dict
              404: 上游名未在 registry 注册
              501: 上游客户端未实现 get_metrics() (例如 tdx_quant / fuyao / omni)
              500: 读取 metrics 时内部异常
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

- [ ] **Step 3: 写 console_api 集成测试**

新文件 `tests/test_console_api_metrics.py`：

```python
"""console_api.upstream_metrics_api + set_upstream_registry 集成测试."""
from src.unihive.utils import console_api


class FakeClient:
    def __init__(self, metrics):
        self._metrics = metrics

    def get_metrics(self):
        return self._metrics


def test_set_registry_and_lookup():
    """set_upstream_registry 后, _get_upstream_client 能找到."""
    fake = FakeClient({"daily_l1": {"hits": 1}})
    console_api.set_upstream_registry({"mootdx2": fake})
    assert console_api._get_upstream_client("mootdx2") is fake
    assert console_api._get_upstream_client("nonexistent") is None


def test_set_registry_overwrites_previous():
    """重复调用 set_upstream_registry 应清空旧引用, 避免泄漏."""
    console_api.set_upstream_registry({"old": FakeClient({})})
    console_api.set_upstream_registry({"new": FakeClient({"x": 1})})
    assert console_api._get_upstream_client("old") is None
    assert console_api._get_upstream_client("new") is not None


class FakeClientNoMetrics:
    pass  # no get_metrics()


def test_get_metrics_returns_501_for_no_get_metrics():
    """未实现 get_metrics() 的 client 应返回 501."""
    console_api.set_upstream_registry({"foo": FakeClientNoMetrics()})
    from starlette.requests import Request as StarletteRequest
    # 直接调 handler 不走 HTTP, 用 Request stub
    from src.unihive.utils.console_api import upstream_metrics_api
    req = StarletteRequest(scope={"type": "http", "path_params": {"name": "foo"}})
    resp = asyncio.run(upstream_metrics_api(req))
    assert resp.status_code == 501


def test_get_metrics_returns_404_for_unknown():
    console_api.set_upstream_registry({})
    from starlette.requests import Request as StarletteRequest
    from src.unihive.utils.console_api import upstream_metrics_api
    req = StarletteRequest(scope={"type": "http", "path_params": {"name": "x"}})
    resp = asyncio.run(upstream_metrics_api(req))
    assert resp.status_code == 404


def test_get_metrics_returns_200_with_payload():
    fake = FakeClient({"daily_l1": {"hits": 1, "misses": 2}, "total_requests": 3})
    console_api.set_upstream_registry({"mootdx2": fake})
    from starlette.requests import Request as StarletteRequest
    from src.unihive.utils.console_api import upstream_metrics_api
    import json
    req = StarletteRequest(scope={"type": "http", "path_params": {"name": "mootdx2"}})
    resp = asyncio.run(upstream_metrics_api(req))
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["upstream"] == "mootdx2"
    assert body["metrics"]["daily_l1"]["hits"] == 1
    assert "timestamp" in body


import asyncio
```

- [ ] **Step 4: 跑测试, 确认 PASS**

Run: `pytest tests/test_console_api_metrics.py -v`
Expected: 5 个测试全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/unihive/utils/console_api.py tests/test_console_api_metrics.py
git commit -m "feat(console_api): /api/upstreams/<name>/metrics + set_upstream_registry"
```

---

## Task 6: gateway_server 注入 upstream registry

**Files:**
- Modify: `src/unihive/gateway_server.py:490-498` (装配区域)

- [ ] **Step 1: 在装配完成后注入 registry**

紧跟 line 494 `self.upstreams[name] = client` 的循环之后（确认装配循环已结束），插入：

```python
                    # 2026-09-15: 让 console_api 能查到已注册的 upstream client,
                    # 服务 /api/upstreams/<name>/metrics 端点。
                    from .utils import console_api
                    console_api.set_upstream_registry(self.upstreams)
```

**注意**: 必须在所有 `self.upstreams[name] = client` 之后、第一个 `await client.start()` 之前插入,以保证 registry 在任何 HTTP 请求到来前就绪。

- [ ] **Step 2: 手动启动网关, 验证端点**

```bash
# 用 httpx 拉一下新端点
curl -b "unihive_session=..." http://127.0.0.1:18080/api/upstreams/mootdx2/metrics
# 应返回 {"upstream": "mootdx2", "metrics": {"daily_l1": {...}, ...}, "timestamp": ...}
```

如果未登录, 先登录拿 cookie。

- [ ] **Step 3: Commit**

```bash
git add src/unihive/gateway_server.py
git commit -m "feat(gateway): 注入 upstream registry 到 console_api"
```

---

## Task 7: 前端 API 客户端 upstream-metrics.ts

**Files:**
- Create: `web/src/api/upstream-metrics.ts`

- [ ] **Step 1: 写 TS 文件**

完整内容：

```typescript
import { http } from './client'

/** get_daily L1 内存缓存运行时统计 (与后端 MooTDX2Client.get_metrics() 对齐) */
export interface DailyL1Stats {
  enabled: boolean
  hits?: number
  misses?: number
  /** null = 尚未有任何请求 */
  hit_rate?: number | null
  size?: number
  maxsize?: number
  ttl_seconds?: number
  inflight_keys?: number
}

/** 单个 upstream client 的运行时指标 (key 可能随实现扩展) */
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

/** GET /api/upstreams/<name>/metrics — 拉某个上游的运行时指标 */
export async function getUpstreamMetrics(
  name: string,
): Promise<UpstreamMetricsResponse> {
  const { data } = await http.get<UpstreamMetricsResponse>(
    `/api/upstreams/${encodeURIComponent(name)}/metrics`,
  )
  return data
}
```

- [ ] **Step 2: TS 类型检查**

Run: `cd web && pnpm tsc --noEmit --pretty false`
Expected: 无新增错误

- [ ] **Step 3: Commit**

```bash
git add web/src/api/upstream-metrics.ts
git commit -m "feat(web): upstream-metrics API 客户端"
```

---

## Task 8: 前端 L1CacheView.vue

**Files:**
- Create: `web/src/views/L1CacheView.vue`

- [ ] **Step 1: 写视图文件**

完整内容：

```vue
<template>
  <div class="l1-cache-page">
    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">
            MooTDX2 L1 缓存 <small class="sub">get_daily_local</small>
          </h2>
          <div class="toolbar-actions">
            <span
              class="last-refresh"
              :class="{ failed: loadError, stale: !loadError && ageSec > STALE_THRESHOLD_SEC }"
            >
              {{ refreshLabel }}
            </span>
            <el-button :icon="Refresh" :loading="manualLoading" @click="load(true)">
              立即刷新
            </el-button>
            <el-tooltip content="每 10 秒自动刷新" placement="top">
              <span class="auto-toggle">
                <el-switch v-model="autoRefresh" />
                <span class="auto-label">自动刷新</span>
              </span>
            </el-tooltip>
          </div>
        </div>
      </template>

      <el-alert
        v-if="loadError"
        type="error"
        :title="loadError"
        show-icon
        :closable="false"
        class="error-banner"
      />

      <div v-loading="initialLoading" class="stat-grid">
        <!-- 命中率主卡 -->
        <div class="hitrate-card">
          <div class="hitrate-head">
            <span class="mini-label">命中率</span>
            <span class="hitrate-value">{{ hitRateText }}</span>
          </div>
          <el-progress
            v-if="hitRatePct !== null"
            :percentage="hitRatePct"
            :stroke-width="10"
            :show-text="false"
            :color="hitRateColor"
          />
          <div v-else class="hitrate-bar-empty" />
          <div class="hitrate-meta">
            <span><strong>{{ metrics?.daily_l1?.hits ?? 0 }}</strong> 命中</span>
            <span><strong>{{ metrics?.daily_l1?.misses ?? 0 }}</strong> 未命中</span>
            <span class="hint">{{ hitRateHint }}</span>
          </div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">状态</div>
          <div class="mini-value">
            <span
              class="status-dot"
              :class="metrics?.daily_l1?.enabled ? 'status-online' : 'status-disabled'"
            />
            <span :class="metrics?.daily_l1?.enabled ? 'on' : 'off'">
              {{ metrics?.daily_l1?.enabled ? '启用' : '停用' }}
            </span>
          </div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">当前 size</div>
          <div class="mini-value">{{ sizeText }}</div>
          <div class="mini-hint">/ {{ metrics?.daily_l1?.maxsize ?? 2048 }} 上限</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">Inflight keys</div>
          <div class="mini-value">{{ metrics?.daily_l1?.inflight_keys ?? 0 }}</div>
          <div class="mini-hint">single-flight 在途请求</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">TTL</div>
          <div class="mini-value">{{ ttlText }}</div>
        </div>

        <div class="mini-stat">
          <div class="mini-label">总请求数</div>
          <div class="mini-value">{{ metrics?.total_requests ?? 0 }}</div>
        </div>
      </div>

      <p v-if="metrics && !metrics.daily_l1?.enabled" class="off-hint">
        L1 缓存未启用 (cachetools 缺失或代码已禁用)
      </p>
    </el-card>

    <el-card shadow="never" class="panel-card">
      <template #header>
        <div class="card-toolbar">
          <h2 class="card-title">配置常量 <small class="sub">get_daily_local L1</small></h2>
        </div>
      </template>
      <div class="kv-grid">
        <div class="kv-key">缓存算法</div>
        <div class="kv-val"><code>TTLCache(maxsize=2048, ttl=7200s)</code></div>
        <div class="kv-key">缓存 key</div>
        <div class="kv-val"><code>(reader_symbol, adjust)</code> — 仅 <code>adjust="none"</code> 写入</div>
        <div class="kv-key">value 形状</div>
        <div class="kv-val"><code>list[dict]</code> — get_daily_sync 原始 records</div>
        <div class="kv-key">过滤位置</div>
        <div class="kv-val">start_date / end_date 过滤在缓存 records 上做 (O(N) dict 遍历)</div>
        <div class="kv-key">single-flight</div>
        <div class="kv-val">
          <code>asyncio.Future</code> 字典 + <code>asyncio.Lock</code> 索引互斥,
          同 key 并发 miss 只跑 1 次 _get_daily_sync
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { errorMessage } from '@/api/client'
import { getUpstreamMetrics } from '@/api/upstream-metrics'
import type { UpstreamMetrics } from '@/api/upstream-metrics'

const STALE_THRESHOLD_SEC = 30

const metrics = ref<UpstreamMetrics | null>(null)
const initialLoading = ref(false)
const manualLoading = ref(false)
const loadError = ref('')
const lastFetchSec = ref(0)
const nowSec = ref(Math.floor(Date.now() / 1000))
const autoRefresh = ref(false)

let clockTimer: ReturnType<typeof setInterval> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let inFlight = false

const ageSec = computed(() =>
  lastFetchSec.value ? Math.max(0, nowSec.value - lastFetchSec.value) : 0,
)

const refreshLabel = computed(() => {
  if (loadError.value && lastFetchSec.value === 0) return `刷新失败 (${loadError.value})`
  if (!lastFetchSec.value) return '从未刷新'
  const sec = ageSec.value
  if (sec < 5) return '刚刚刷新'
  if (sec < 60) return `${sec} 秒前刷新`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前刷新`
  return `${Math.floor(sec / 3600)} 小时前刷新`
})

const hitRatePct = computed(() => {
  const rate = metrics.value?.daily_l1?.hit_rate
  return rate == null ? null : Number((rate * 100).toFixed(1))
})

const hitRateText = computed(() => {
  const rate = metrics.value?.daily_l1?.hit_rate
  return rate == null ? '—' : `${(rate * 100).toFixed(1)}%`
})

const hitRateHint = computed(() => {
  const l1 = metrics.value?.daily_l1
  if (!l1?.enabled) return l1 ? '缓存已停用' : '尚未拉到指标'
  const total = (l1.hits ?? 0) + (l1.misses ?? 0)
  return total > 0 ? `共 ${total} 次请求` : '网关启动后累计 (尚未请求)'
})

const hitRateColor = computed(() => {
  const pct = hitRatePct.value
  if (pct === null) return undefined
  if (pct > 70) return 'var(--el-color-success)'
  if (pct > 40) return 'var(--el-color-warning)'
  return 'var(--el-color-danger)'
})

const sizeText = computed(() => {
  const s = metrics.value?.daily_l1?.size
  return s == null ? '—' : String(s)
})

const ttlText = computed(() => {
  const t = metrics.value?.daily_l1?.ttl_seconds
  if (t == null) return '—'
  if (t >= 3600) return `${Math.floor(t / 3600)}h`
  if (t >= 60) return `${Math.floor(t / 60)}min`
  return `${t}s`
})

async function load(manual = false): Promise<void> {
  if (inFlight) {
    if (manual) manualLoading.value = true
    return
  }
  inFlight = true
  const firstLoad = metrics.value === null
  if (firstLoad) initialLoading.value = true
  if (manual) manualLoading.value = true
  loadError.value = ''
  try {
    const resp = await getUpstreamMetrics('mootdx2')
    metrics.value = resp.metrics
    lastFetchSec.value = Math.floor(Date.now() / 1000)
  } catch (error) {
    loadError.value = errorMessage(error, '请检查网关是否运行')
    lastFetchSec.value = 0
  } finally {
    initialLoading.value = false
    manualLoading.value = false
    inFlight = false
  }
}

watch(autoRefresh, (on) => {
  if (!on) {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
    return
  }
  pollTimer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return
    void load(false)
  }, 10_000)
})

function onVisibility(): void {
  if (!document.hidden) {
    nowSec.value = Math.floor(Date.now() / 1000)
    if (lastFetchSec.value && nowSec.value - lastFetchSec.value > STALE_THRESHOLD_SEC) {
      void load(false)
    }
  }
}

onMounted(() => {
  void load(true)
  clockTimer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return
    nowSec.value = Math.floor(Date.now() / 1000)
  }, 1000)
  document.addEventListener('visibilitychange', onVisibility)
})

onBeforeUnmount(() => {
  if (clockTimer) clearInterval(clockTimer)
  if (pollTimer) clearInterval(pollTimer)
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<style scoped>
.l1-cache-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.panel-card {
  border-radius: 8px;
}

.card-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.card-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
  margin-left: 6px;
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.last-refresh {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.last-refresh.failed {
  color: var(--el-color-danger);
}

.last-refresh.stale {
  color: var(--el-color-warning);
}

.auto-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  cursor: default;
}

.error-banner {
  margin-bottom: 12px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  min-height: 96px;
}

.hitrate-card {
  grid-column: span 2;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.hitrate-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.hitrate-value {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.hitrate-bar-empty {
  height: 10px;
  border-radius: 5px;
  background: var(--el-fill-color-light);
}

.hitrate-meta {
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.hitrate-meta .hint {
  color: var(--el-text-color-secondary);
}

.mini-stat {
  padding: 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}

.mini-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}

.mini-value {
  font-size: 18px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 7px;
  font-variant-numeric: tabular-nums;
}

.mini-value .on { color: var(--el-color-success); }
.mini-value .off { color: var(--el-text-color-secondary); }

.mini-hint {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin-top: 2px;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}

.status-online {
  background: var(--el-color-success);
  box-shadow: 0 0 0 3px var(--el-color-success-light-9);
}

.status-disabled {
  background: var(--el-text-color-placeholder);
}

.off-hint {
  margin: 10px 2px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.kv-grid {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: 10px 14px;
}

.kv-key {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.kv-val {
  font-size: 13px;
}

@media (max-width: 700px) {
  .hitrate-card {
    grid-column: span 1;
  }
}
</style>
```

- [ ] **Step 2: TS 类型检查**

Run: `cd web && pnpm tsc --noEmit --pretty false`
Expected: 无新增错误

- [ ] **Step 3: Commit**

```bash
git add web/src/views/L1CacheView.vue
git commit -m "feat(web): L1CacheView 视图"
```

---

## Task 9: 路由 + 菜单集成

**Files:**
- Modify: `web/src/router/index.ts:22-46`
- Modify: `web/src/layouts/DefaultLayout.vue:70` + `121-127`

- [ ] **Step 1: 路由添加**

在 `web/src/router/index.ts` line 22-26 上游状态路由之后插入：

```typescript
      {
        path: 'l1-cache',
        name: 'l1-cache',
        component: () => import('@/views/L1CacheView.vue'),
        meta: { title: 'L1 缓存指标' },
      },
```

- [ ] **Step 2: 菜单添加 + 图标导入**

`web/src/layouts/DefaultLayout.vue` line 70 改为：

```typescript
import { Document, Monitor, Connection, Setting, Grid, DataLine, Fold, Expand } from '@element-plus/icons-vue'
```

line 121-127 MENU 数组中,在 `{ path: '/status', ... }` 之后插入：

```typescript
  { path: '/l1-cache', title: 'L1 缓存指标', icon: DataLine },
```

- [ ] **Step 3: 构建验证**

Run: `cd web && pnpm build 2>&1 | tail -20`
Expected: 构建成功,无 TS / Vite 错误

- [ ] **Step 4: 浏览器目检**

启动网关,登录控制台,侧栏点「L1 缓存指标」:
- 看到 6 个 stat-card (命中率/size/inflight/TTL/总请求)
- 看到「配置常量」卡 (含 key 形状 + single-flight 说明)
- 点立即刷新,看到 last-refresh 更新

- [ ] **Step 5: Commit**

```bash
git add web/src/router/index.ts web/src/layouts/DefaultLayout.vue
git commit -m "feat(web): 侧栏新增 L1 缓存指标入口"
```

---

## Task 10: 端到端验证

- [ ] **Step 1: 跑全量后端测试**

Run: `pytest tests/ -q 2>&1 | tail -5`
Expected: 580+ 通过 (含本轮新增 11 个)

- [ ] **Step 2: 浏览器端到端**

启动网关,登录:
- 调 `get_daily_local` 5 次同 symbol → 看 hits=4 misses=1,size=1,inflight_keys=0
- 调 `get_daily_local` 5 次不同 symbol → 看 hits=0 misses=5,size=5,inflight_keys=0
- 调 `get_daily_local` adjust="qfq" 5 次 → 看 hits/misses 不变 (不进 L1)

- [ ] **Step 3: 关 single-flight 验证 (回滚测试)**

临时把 `is_leader = True` 改为 `is_leader = False` (约 line 1689-1694 区域),重启网关,跑 5 个并发同 symbol:
- 仍能工作 (降级为多次 reader.daily 调用)
- inflight_keys 不为 0 的时间窗口变长
- 改回去

- [ ] **Step 4: 更新 CLAUDE.md / CHANGELOG**

`D:\fintech_workspace\unihive\CLAUDE.md` 第 "数据源" 表的 mootdx2 行旁可补一行注释:"get_daily_local 有 L1 内存缓存 + single-flight, 见控制台 /l1-cache 页"。其他不需要改。

- [ ] **Step 5: Commit (如有 CLAUDE.md 修改)**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 标注 get_daily_local L1 缓存"
```

---

## Self-Review Checklist

- [x] Spec 每一节都有对应 task
- [x] 无 TBD / TODO / 占位符
- [x] 类型一致 (DailyL1Stats / UpstreamMetrics / UpstreamMetricsResponse / ToolResult)
- [x] 行号引用与实际文件一致 (mootdx2_client.py)
- [x] 测试文件命名一致 (`test_get_daily_l1_observability.py` + `test_console_api_metrics.py`)
- [x] API 路径一致 (`/api/upstreams/<name>/metrics`)