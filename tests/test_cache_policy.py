"""新增缓存测试：单飞保护 + FUYAO-only 策略。"""
import asyncio
from dataclasses import dataclass

import pytest


class TestSingleFlight:
    """同一 key 并发 miss 只触发一次 factory。"""

    async def test_concurrent_miss_single_flight(self, temp_cache):
        calls = []

        async def factory():
            calls.append(1)
            await asyncio.sleep(0.1)
            return {"v": len(calls)}

        results = await asyncio.gather(*[
            temp_cache.get_or_set("thunder", ttl=60, factory=factory)
            for _ in range(20)
        ])

        # 关键断言：20 个并发只触发一次 factory
        assert len(calls) == 1
        for value, hit in results:
            assert value == {"v": 1}
            # 单飞的 waiter 既不算 hit（缓存里还没东西），也不算 miss（没触发新计算）
            assert hit is False

    async def test_waiter_does_not_record_miss(self, temp_cache):
        """单飞的 waiter 不应重复计 miss，避免命中率被稀释。"""
        calls = []

        async def factory():
            calls.append(1)
            await asyncio.sleep(0.1)
            return "x"

        await asyncio.gather(*[
            temp_cache.get_or_set("k", ttl=60, factory=factory)
            for _ in range(10)
        ])

        # 1 miss (发起者), 0 hit
        assert temp_cache.misses == 1
        assert temp_cache.hits == 0

    async def test_subsequent_call_after_warmup_records_hit(self, temp_cache):
        """单飞计算完成后，下次访问应走 hit。"""
        async def factory():
            return {"a": 1}

        await temp_cache.get_or_set("k", ttl=60, factory=factory)
        # miss 已计 1

        value, hit = await temp_cache.get_or_set("k", ttl=60, factory=factory)
        assert hit is True
        assert value == {"a": 1}
        assert temp_cache.misses == 1
        assert temp_cache.hits == 1

    async def test_factory_exception_propagates_to_waiters(self, temp_cache):
        """factory 抛异常时所有 waiter 都收到同一异常。"""
        async def factory():
            await asyncio.sleep(0.05)
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await asyncio.gather(*[
                temp_cache.get_or_set("k", ttl=60, factory=factory)
                for _ in range(5)
            ])

        # factory 失败时不写缓存
        assert await temp_cache.get("k") is None

    async def test_cache_set_failure_does_not_break_waiters(self, temp_cache):
        """Regression #2: cache.set 失败不应让所有 waiter 收到异常。
        上游 factory 已成功返回，缓存层只是写不进去。"""
        async def factory():
            return {"v": 1}

        # 模拟 cache.set 抛异常（DB 锁、磁盘满、aiosqlite bug）
        original_set = temp_cache.set

        async def broken_set(*args, **kwargs):
            raise RuntimeError("sqlite locked")

        temp_cache.set = broken_set

        # 5 个 waiter 都不应收到异常 — 应都拿到 {"v": 1}
        results = await asyncio.gather(*[
            temp_cache.get_or_set("k", ttl=60, factory=factory)
            for _ in range(5)
        ])
        for value, hit in results:
            assert value == {"v": 1}
            assert hit is False

        # 清理：恢复 set，后续测试不受影响
        temp_cache.set = original_set


class TestExecuteCachedEmptyDataGuard:
    """Regression #1: _execute_cached 不应缓存 data=None 的成功响应。
    触发场景：FUYAO HTTP 返回 {"data": null}（节假日无行情、网络抖动）。
    缓存空响应会导致整个 TTL 窗口内的请求都拿到空数据。"""

    async def test_empty_data_response_is_not_cached(self, tmp_path, gateway_server_minimal):
        from dataclasses import dataclass
        from src.cache import Cache, CacheConfig
        from src.gateway_server import GatewayServer

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        @dataclass
        class FakeResult:
            success: bool
            data: object = None
            error: str | None = None
            source: str | None = None
            hops: list = None  # type: ignore

            def __post_init__(self):
                if self.hops is None:
                    self.hops = []

        class FakeRouter:
            def __init__(self, result):
                self._result = result
                self.calls = 0

            async def route(self, route_key, params):
                self.calls += 1
                return self._result

        # bypass GatewayServer.__init__ — 只需要属性，不跑 initialize()
        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter(FakeResult(
            success=True, data=None, source="fuyao_ashare",
        ))
        server.config = {
            "cache": {
                "ttl": {"realtime_quote": 10},
            },
        }

        resp = await server._execute_cached(
            "a_share_prices_snapshot",
            {"thscodes": "600000.SH"},
            route_key="a_share_prices_snapshot",
            ttl_key="realtime_quote",
        )

        # router 被调过一次（没有命中缓存）
        assert server.router.calls == 1
        # 响应被返回
        assert resp["success"] is True
        assert resp["data"] is None
        # 但缓存里没有这条记录
        key = server._cache_key("a_share_prices_snapshot", {"thscodes": "600000.SH"})
        assert await cache.get(key) is None, "空响应不应被缓存"

    async def test_non_empty_data_response_is_cached(self, tmp_path, gateway_server_minimal):
        """对照测试：data 非空时正常缓存。"""
        from src.cache import Cache, CacheConfig
        from src.gateway_server import GatewayServer

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        @dataclass
        class FakeResult:
            success: bool
            data: object = None
            error: str | None = None
            source: str | None = None
            hops: list = None  # type: ignore

            def __post_init__(self):
                if self.hops is None:
                    self.hops = []

        class FakeRouter:
            def __init__(self):
                self.calls = 0

            async def route(self, route_key, params):
                self.calls += 1
                return FakeResult(
                    success=True,
                    data={"close": 100.0},
                    source="fuyao_ashare",
                )

        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter()
        server.config = {"cache": {"ttl": {"realtime_quote": 10}}}

        resp = await server._execute_cached(
            "a_share_prices_snapshot",
            {"thscodes": "600000.SH"},
            route_key="a_share_prices_snapshot",
            ttl_key="realtime_quote",
        )

        assert resp["success"] is True
        assert resp["data"] == {"close": 100.0}
        key = server._cache_key("a_share_prices_snapshot", {"thscodes": "600000.SH"})
        cached = await cache.get(key)
        assert cached is not None, "data 非空的 FUYAO 成功响应应被缓存"
        assert cached["data"] == {"close": 100.0}

        await cache.close()

    async def test_miss_response_includes_cache_hit_false(self, tmp_path, gateway_server_minimal):
        """Regression #4: miss 路径必须设 cache_hit=False。
        让消费者区分"缓存端点本次 miss"与"无缓存端点"。"""
        from src.cache import Cache, CacheConfig
        from src.gateway_server import GatewayServer

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        @dataclass
        class FakeResult:
            success: bool
            data: object = None
            error: str | None = None
            source: str | None = None
            hops: list = None  # type: ignore

            def __post_init__(self):
                if self.hops is None:
                    self.hops = []

        class FakeRouter:
            async def route(self, route_key, params):
                return FakeResult(success=True, data={"v": 1}, source="fuyao_ashare", hops=[])

        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter()
        server.config = {"cache": {"ttl": {"realtime_quote": 10}}}

        resp = await server._execute_cached(
            "a_share_prices_snapshot",
            {"thscodes": "600000.SH"},
            route_key="a_share_prices_snapshot",
            ttl_key="realtime_quote",
        )

        assert resp.get("cache_hit") is False, "miss 路径必须显式设 cache_hit=False"

        await cache.close()

    async def test_miss_response_includes_hops_from_router(self, tmp_path, caplog, gateway_server_minimal):
        """Regression #3: miss 路径响应应包含 router 返回的 hops 列表。
        hops 仍是请求级元数据（不进缓存），但消费者拿得到。
        debug 日志也记录一份。"""
        import logging
        from src.cache import Cache, CacheConfig
        from src.gateway_server import GatewayServer

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        @dataclass
        class FakeHop:
            source: str
            tool_name: str = ""
            duration_ms: int = 0
            success: bool = True
            error: str | None = None

        @dataclass
        class FakeResult:
            success: bool
            data: object = None
            error: str | None = None
            source: str | None = None
            hops: list = None  # type: ignore

            def __post_init__(self):
                if self.hops is None:
                    self.hops = []

        class FakeRouter:
            async def route(self, route_key, params):
                return FakeResult(
                    success=True,
                    data={"v": 1},
                    source="fuyao_meta",
                    hops=[FakeHop(source="fuyao_meta"), FakeHop(source="tdx_local")],
                )

        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter()
        server.config = {"cache": {"ttl": {"search": 300}}}

        with caplog.at_level(logging.DEBUG, logger="src.gateway_server"):
            resp = await server._execute_cached(
                "search_stock",
                {"keyword": "茅台"},
                route_key="search_stock",
                ttl_key="search",
            )

        # 响应里 hops 是真实来源列表
        assert resp["hops"] == ["fuyao_meta", "tdx_local"], (
            f"hops 应该是 router 返回的源列表, got {resp.get('hops')}"
        )
        # debug 日志记录了 routing 详情
        assert any("route result" in r.message for r in caplog.records), (
            "miss 路径应输出 debug 日志记录 routing hops"
        )

        await cache.close()

    async def test_cached_response_strips_hops_and_cache_hit(self, tmp_path, gateway_server_minimal):
        """Regression #3+#4: 缓存里不应存 hops（请求级）和 cache_hit=True。
        命中的响应里由 _execute_cached 重算 cache_hit=True，
        hops 从缓存读时是 []（这次请求没有真实路由）。"""
        from src.cache import Cache, CacheConfig
        from src.gateway_server import GatewayServer

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        @dataclass
        class FakeHop:
            source: str
            tool_name: str = ""
            duration_ms: int = 0
            success: bool = True
            error: str | None = None

        @dataclass
        class FakeResult:
            success: bool
            data: object = None
            error: str | None = None
            source: str | None = None
            hops: list = None  # type: ignore

            def __post_init__(self):
                if self.hops is None:
                    self.hops = []

        class FakeRouter:
            def __init__(self):
                self.calls = 0

            async def route(self, route_key, params):
                self.calls += 1
                return FakeResult(
                    success=True,
                    data={"v": self.calls},
                    source="fuyao_ashare",
                    hops=[FakeHop(source="fuyao_ashare")],
                )

        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter()
        server.config = {"cache": {"ttl": {"realtime_quote": 10}}}

        # 第一次：miss → router 被调
        resp1 = await server._execute_cached(
            "a_share_prices_snapshot",
            {"thscodes": "600000.SH"},
            route_key="a_share_prices_snapshot",
            ttl_key="realtime_quote",
        )
        assert server.router.calls == 1
        assert resp1["cache_hit"] is False
        assert resp1["data"] == {"v": 1}

        # 缓存里不应有 hops 和 cache_hit=True
        key = server._cache_key("a_share_prices_snapshot", {"thscodes": "600000.SH"})
        cached = await cache.get(key)
        assert cached["hops"] == [], "缓存里 hops 必须是空"
        assert "cache_hit" not in cached, "缓存里不应预存 cache_hit 字段"

        # 第二次：命中 → router 不被调
        resp2 = await server._execute_cached(
            "a_share_prices_snapshot",
            {"thscodes": "600000.SH"},
            route_key="a_share_prices_snapshot",
            ttl_key="realtime_quote",
        )
        assert server.router.calls == 1, "第二次应命中缓存"
        assert resp2["cache_hit"] is True
        assert resp2["data"] == {"v": 1}
        assert resp2["hops"] == [], "命中响应 hops 是空（本次未路由）"

        await cache.close()


class TestCleanupExpiredLru:
    """Regression #5: cleanup_expired 必须同时按 TTL 清理 + max_entries LRU 驱逐。
    之前 max_entries=10000 是死配置，cache.db 会无界增长。"""

    async def _make_cache(self, tmp_path, max_entries=10):
        from src.cache import Cache, CacheConfig
        cache = Cache(CacheConfig(
            enabled=True,
            db_path=str(tmp_path / "cache.db"),
            max_entries=max_entries,
        ))
        await cache.initialize()
        return cache

    async def _count_entries(self, cache) -> int:
        from sqlalchemy import text
        async with cache._session_factory() as session:
            row = await session.execute(text("SELECT COUNT(*) FROM cache"))
            return row.scalar() or 0

    async def test_under_limit_no_eviction(self, tmp_path):
        """不超过 max_entries 时，cleanup 不应删任何项。"""
        cache = await self._make_cache(tmp_path, max_entries=10)
        try:
            for i in range(5):
                await cache.set(f"k{i}", {"v": i}, ttl=60)
            assert await self._count_entries(cache) == 5

            deleted = await cache.cleanup_expired()
            assert deleted == 0
            assert await self._count_entries(cache) == 5
        finally:
            await cache.close()

    async def test_expired_entries_cleaned_first(self, tmp_path):
        """过期项必须在 LRU 之前先清。"""
        import asyncio
        cache = await self._make_cache(tmp_path, max_entries=100)
        try:
            # 5 条已过期 (ttl=1s, 睡 1.1s)
            for i in range(5):
                await cache.set(f"exp{i}", {"v": i}, ttl=1)
            await asyncio.sleep(1.1)
            # 3 条新插入 (ttl=60)
            for i in range(3):
                await cache.set(f"new{i}", {"v": i}, ttl=60)
            assert await self._count_entries(cache) == 8

            deleted = await cache.cleanup_expired()
            assert deleted == 5, f"应删 5 条过期项, got {deleted}"
            assert await self._count_entries(cache) == 3
            # 留下的全是新的
            for i in range(3):
                assert await cache.get(f"new{i}") is not None
        finally:
            await cache.close()

    async def test_lru_eviction_above_limit(self, tmp_path):
        """超 max_entries 时按 created_at 删最旧的，削到 max_entries * 0.9。"""
        cache = await self._make_cache(tmp_path, max_entries=10)
        try:
            # 插 20 条，全部 ttl=60 (不过期)
            for i in range(20):
                await cache.set(f"k{i:02d}", {"v": i}, ttl=60)
            assert await self._count_entries(cache) == 20

            deleted = await cache.cleanup_expired()
            # 目标: 10 * 0.9 = 9, 删 20 - 9 = 11
            assert deleted == 11, f"应删 11 条 (削到 9), got {deleted}"
            assert await self._count_entries(cache) == 9

            # 最早插入的 (k00..k10) 应被驱逐，k11..k19 留下
            for i in range(11):
                assert await cache.get(f"k{i:02d}") is None, f"k{i:02d} 应被驱逐"
            for i in range(11, 20):
                assert await cache.get(f"k{i:02d}") is not None, f"k{i:02d} 应保留"
        finally:
            await cache.close()

    async def test_lru_eviction_logs_warning(self, tmp_path, caplog):
        """触发 LRU 驱逐时必须 log warning，让运维能发现 cache 压力。"""
        import logging
        cache = await self._make_cache(tmp_path, max_entries=5)
        try:
            for i in range(20):
                await cache.set(f"k{i}", {"v": i}, ttl=60)

            with caplog.at_level(logging.WARNING, logger="src.cache"):
                await cache.cleanup_expired()

            warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert any("over max_entries" in r.message for r in warnings), (
                f"应触发 over max_entries warning, got: {[r.message for r in warnings]}"
            )
        finally:
            await cache.close()

    async def test_cleanup_under_max_after_expiry(self, tmp_path):
        """过期清理后若 ≤ max_entries，不应再触发 LRU 驱逐。"""
        import asyncio
        cache = await self._make_cache(tmp_path, max_entries=10)
        try:
            # 12 条 ttl=1s
            for i in range(12):
                await cache.set(f"k{i}", {"v": i}, ttl=1)
            await asyncio.sleep(1.1)
            assert await self._count_entries(cache) == 12

            deleted = await cache.cleanup_expired()
            # 12 条全过期，全删；清理后 0 条 ≤ 10，不再 LRU
            assert deleted == 12
            assert await self._count_entries(cache) == 0
        finally:
            await cache.close()


class TestIsFuyaoSource:
    """FUYAO-only 策略的源头判断。"""

    def test_fuyao_prefixes(self):
        from src.gateway_server import GatewayServer
        for s in ["fuyao_ashare", "fuyao_index", "fuyao_meta", "fuyao_fund"]:
            assert GatewayServer._is_fuyao_source(s) is True

    def test_non_fuyao_rejected(self):
        from src.gateway_server import GatewayServer
        for s in ["tdx_local", "tokenwave_tdx", "tdx_tq_local", "", None]:
            assert GatewayServer._is_fuyao_source(s) is False

    def test_substring_does_not_match(self):
        """必须以 fuyao_ 开头；仅包含 fuyao 不算。"""
        from src.gateway_server import GatewayServer
        assert GatewayServer._is_fuyao_source("myfuyao_ashare") is False

class TestExecuteCachedStatsAccounting:
    """Regression #7: _execute_cached 必须显式 record_hit/record_miss。
    之前 gateway 走 cache.get + cache.set 直接路径, _record_* 永远不触发,
    cache_stats.json 永远是 {hits:0, misses:0}."""

    async def test_miss_increments_miss_counter(self, tmp_path, gateway_server_minimal):
        from src.cache import Cache, CacheConfig
        from src.gateway_server import GatewayServer

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        @dataclass
        class FakeResult:
            success: bool = True
            data: object = None
            error: str | None = None
            source: str | None = "fuyao_ashare"
            hops: list = None  # type: ignore
            def __post_init__(self):
                if self.hops is None:
                    self.hops = []

        class FakeRouter:
            def __init__(self):
                self.calls = 0
            async def route(self, route_key, params):
                self.calls += 1
                return FakeResult(data={"v": self.calls})

        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter()
        server.config = {"cache": {"ttl": {"realtime_quote": 10}}}

        # 第一次: miss
        resp1 = await server._execute_cached(
            "test_tool", {"x": "1"}, route_key="test_tool", ttl_key="realtime_quote"
        )
        assert cache.misses == 1, f"miss 应=1, got {cache.misses}"
        assert cache.hits == 0

        # 第二次: hit
        resp2 = await server._execute_cached(
            "test_tool", {"x": "1"}, route_key="test_tool", ttl_key="realtime_quote"
        )
        assert resp2["cache_hit"] is True
        assert cache.hits == 1, f"hit 应=1, got {cache.hits}"
        assert cache.misses == 1, "hit 不应增加 miss"

        await cache.close()

    async def test_non_caching_path_does_not_count(self, tmp_path, gateway_server_minimal):
        """can_cache=False (无 TTL 或 cache 禁用) 不应计入 hit/miss,
        否则运维误判缓存效果。"""
        from src.cache import Cache, CacheConfig
        from src.gateway_server import GatewayServer

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        @dataclass
        class FakeResult:
            success: bool = True
            data: object = None
            error: str | None = None
            source: str | None = None
            hops: list = None  # type: ignore

            def __post_init__(self):
                if self.hops is None:
                    self.hops = []

        class FakeRouter:
            async def route(self, route_key, params):
                return FakeResult(data={"v": 1}, source="fuyao_ashare")

        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter()
        server.config = {"cache": {"ttl": {}}}  # 空 ttl 配置

        # ttl_key="not_configured" → _ttl_for 返回 None → can_cache=False
        await server._execute_cached(
            "t", {"k": "v"}, route_key="t", ttl_key="not_configured"
        )
        assert cache.misses == 0, "can_cache=False 不应计 miss"
        assert cache.hits == 0, "can_cache=False 不应计 hit"

        await cache.close()


class TestWalMode:
    """WAL mode: 解决 console (同步 sqlite3) 与 gateway (aiosqlite) 同时读写
    同一 cache.db 时的 "database is locked" 问题。"""

    async def test_wal_mode_is_active(self, tmp_path):
        """initialize 后 journal_mode 必须是 wal, 不是默认的 delete."""
        from src.cache import Cache, CacheConfig
        from sqlalchemy import text

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()
        try:
            async with cache._session_factory() as session:
                row = await session.execute(text("PRAGMA journal_mode"))
                mode = row.scalar()
            assert mode.lower() == "wal", f"journal_mode 应是 wal, got {mode!r}"

            # 副作用: 应存在 wal/shm 旁文件 (aio 关闭后才一定存在, 所以这里只查 mode)
        finally:
            await cache.close()


class TestAsyncStatsPersistence:
    """stats 落盘走 asyncio.to_thread，不阻塞 event loop 热路径。
    close() 必须把未刷的 dirty stats 落盘再退出。"""

    async def test_close_flushes_dirty_stats_to_disk(self, tmp_path):
        """close() 在 dirty events < STATS_FLUSH_INTERVAL 时也应刷盘。
        否则 gateway 进程重启会丢这批累积的计数。"""
        from src.cache import Cache, CacheConfig

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        # 把 STATS_FILE 写到临时目录, 别污染 logs/
        cache.STATS_FILE = str(tmp_path / "cache_stats.json")

        # 只记 3 次 miss (远低于 STATS_FLUSH_INTERVAL=50), 不会触发 hot flush
        cache.record_miss()
        cache.record_miss()
        cache.record_miss()
        assert cache._dirty_events == 3

        await cache.close()

        import json
        with open(cache.STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        assert data == {"hits": 0, "misses": 3}, (
            f"close 后 stats 应已落盘, got {data}"
        )

    async def test_hot_path_flushes_via_background_task(self, tmp_path):
        """越过 STATS_FLUSH_INTERVAL 时, record_hit/record_miss 必须
        通过 create_task 调度而不是阻塞同步调用。"""
        import json
        from src.cache import Cache, CacheConfig

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        cache.STATS_FILE = str(tmp_path / "cache_stats.json")

        # 触发 hot flush 边界
        for _ in range(cache.STATS_FLUSH_INTERVAL):
            cache.record_hit()

        # create_task 是 fire-and-forget; 给后台线程一点点时间落盘
        for _ in range(20):
            if cache._dirty_events == 0:
                break
            await asyncio.sleep(0.05)

        assert cache._dirty_events == 0, "async flush 应清掉 dirty_events"

        with open(cache.STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        assert data == {"hits": cache.STATS_FLUSH_INTERVAL, "misses": 0}, (
            f"热路径 async flush 应已落盘, got {data}"
        )

        await cache.close()
