"""FUYAO-only 策略 + LRU 驱逐 + execute_cached 行为测试。"""
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone

import pytest

from src.unihive.core import cache_strategy


class TestExecuteCachedEmptyDataGuard:
    """Regression #1: _execute_cached 不应缓存 data=None 的成功响应。
    触发场景：FUYAO HTTP 返回 {"data": null}（节假日无行情、网络抖动）。
    缓存空响应会导致整个 TTL 窗口内的请求都拿到空数据。"""

    async def test_empty_data_response_is_not_cached(self, tmp_path, gateway_server_minimal):
        from dataclasses import dataclass
        from src.unihive.storage.cache import Cache, CacheConfig
        from src.unihive.gateway_server import GatewayServer

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
        key = cache_strategy.cache_key("a_share_prices_snapshot", {"thscodes": "600000.SH"})
        assert await cache.get(key) is None, "空响应不应被缓存"

    async def test_non_empty_data_response_is_cached(self, tmp_path, gateway_server_minimal):
        """对照测试：data 非空时正常缓存。"""
        from src.unihive.storage.cache import Cache, CacheConfig
        from src.unihive.gateway_server import GatewayServer

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
        # 非实时 key + 候选链含远程 fuyao 源才走缓存
        server.config = {
            "cache": {"ttl": {"historical": 60}},
            "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
        }

        resp = await server._execute_cached(
            "hist_tool",
            {"thscodes": "600000.SH"},
            route_key="hist_tool",
            ttl_key="historical",
        )

        assert resp["success"] is True
        assert resp["data"] == {"close": 100.0}
        key = cache_strategy.cache_key("hist_tool", {"thscodes": "600000.SH"})
        cached = await cache.get(key)
        assert cached is not None, "data 非空的 FUYAO 成功响应应被缓存"
        assert cached["data"] == {"close": 100.0}

        await cache.close()

    async def test_miss_response_includes_cache_hit_false(self, tmp_path, gateway_server_minimal):
        """Regression #4: miss 路径必须设 cache_hit=False。
        让消费者区分"缓存端点本次 miss"与"无缓存端点"。"""
        from src.unihive.storage.cache import Cache, CacheConfig
        from src.unihive.gateway_server import GatewayServer

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
        from src.unihive.storage.cache import Cache, CacheConfig
        from src.unihive.gateway_server import GatewayServer

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
        server.config = {
            "cache": {"ttl": {"search": 300}},
            "upstream_tool_mapping": {"search_stock": {"fuyao_meta": "x", "tdx_local": "y"}},
        }

        with caplog.at_level(logging.DEBUG, logger="src.unihive.gateway_server"):
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
        from src.unihive.storage.cache import Cache, CacheConfig
        from src.unihive.gateway_server import GatewayServer

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
        server.config = {
            "cache": {"ttl": {"historical": 60}},
            "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
        }

        # 第一次：miss → router 被调
        resp1 = await server._execute_cached(
            "hist_tool",
            {"thscodes": "600000.SH"},
            route_key="hist_tool",
            ttl_key="historical",
        )
        assert server.router.calls == 1
        assert resp1["cache_hit"] is False
        assert resp1["data"] == {"v": 1}

        # 缓存里不应有 hops 和 cache_hit=True
        key = cache_strategy.cache_key("hist_tool", {"thscodes": "600000.SH"})
        cached = await cache.get(key)
        assert cached["hops"] == [], "缓存里 hops 必须是空"
        assert "cache_hit" not in cached, "缓存里不应预存 cache_hit 字段"

        # 第二次：命中 → router 不被调
        resp2 = await server._execute_cached(
            "hist_tool",
            {"thscodes": "600000.SH"},
            route_key="hist_tool",
            ttl_key="historical",
        )
        assert server.router.calls == 1, "第二次应命中缓存"
        assert resp2["cache_hit"] is True
        assert resp2["data"] == {"v": 1}
        # LOW-4 (2026-09-14 audit): cache hit hops 现在给一个 cache sentinel
        # (source='cache'), 让消费者看到响应走了缓存而非未配置 routing chain。
        # 真实历史 hops 留在缓存里没意义, sentinel 已足够语义化。
        assert len(resp2["hops"]) == 1
        assert resp2["hops"][0]["source"] == "cache"
        assert resp2["hops"][0]["tool_name"] == "hist_tool"

        await cache.close()


class TestCleanupExpiredLru:
    """Regression #5: cleanup_expired 必须同时按 TTL 清理 + max_entries LRU 驱逐。
    之前 max_entries=10000 是死配置，cache.db 会无界增长。"""

    async def _make_cache(self, tmp_path, max_entries=10):
        from src.unihive.storage.cache import Cache, CacheConfig
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
        """Refactor 2 (2026-09-08): 超 max_entries 时按最近访问时间删最久未访问的,
        削到 max_entries * 0.9。被 get() 触碰过的 key 应保留, 证明 LRU 真在按访问时间排序。

        关键 invariant: 触碰时间必须严格大于所有未触碰 entry 的 created_at。
        由 _access_times 是 in-memory dict 同步写, get() 返回后 touch 即生效,
        无需 polling / sleep。
        """
        cache = await self._make_cache(tmp_path, max_entries=10)
        try:
            # 插 20 条，全部 ttl=60 (不过期)
            for i in range(20):
                await cache.set(f"k{i:02d}", {"v": i}, ttl=60)
            assert await self._count_entries(cache) == 20

            # 触碰 k00, k01 — 同步写入 _access_times dict (无 SQLite I/O)
            assert await cache.get("k00") == {"v": 0}
            assert await cache.get("k01") == {"v": 1}

            deleted = await cache.cleanup_expired()
            # 目标: 10 * 0.9 = 9, 删 20 - 9 = 11
            assert deleted == 11, f"应删 11 条 (削到 9), got {deleted}"
            assert await self._count_entries(cache) == 9

            # k00, k01 因最近被访问应保留 (in-memory _access_times 严格晚于
            # 所有未触碰 entry 的 created_at)。
            # 未触碰的 18 个 (k02..k19) 按 created_at 排序, 最旧 11 个 (k02..k12)
            # 被驱逐; k13..k19 保留。所以共保留 9 条 (7 + k00 + k01)。
            assert await cache.get("k00") == {"v": 0}, "k00 最近被 get, 应保留"
            assert await cache.get("k01") == {"v": 1}, "k01 最近被 get, 应保留"
            for i in range(2, 13):
                assert await cache.get(f"k{i:02d}") is None, (
                    f"k{i:02d} 未被访问且 created_at 最早, 应被驱逐"
                )
            for i in range(13, 20):
                assert await cache.get(f"k{i:02d}") is not None, (
                    f"k{i:02d} 后插入, 应保留"
                )
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
        for s in ["fuyao_ashare", "fuyao_index", "fuyao_meta", "fuyao_fund"]:
            assert cache_strategy.is_fuyao_source(s) is True

    def test_non_fuyao_rejected(self):
        for s in ["tdx_local", "tokenwave_tdx", "tdx_tq_local", "", None]:
            assert cache_strategy.is_fuyao_source(s) is False

    def test_substring_does_not_match(self):
        """必须以 fuyao_ 开头；仅包含 fuyao 不算。"""
        assert cache_strategy.is_fuyao_source("myfuyao_ashare") is False

class TestExecuteCachedStatsAccounting:
    """Regression #7: _execute_cached 必须显式 record_hit/record_miss。
    之前 gateway 走 cache.get + cache.set 直接路径, _record_* 永远不触发,
    cache_stats.json 永远是 {hits:0, misses:0}."""

    async def test_miss_increments_miss_counter(self, tmp_path, gateway_server_minimal):
        from src.unihive.storage.cache import Cache, CacheConfig
        from src.unihive.gateway_server import GatewayServer

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
        server.config = {
            "cache": {"ttl": {"historical": 60}},
            "upstream_tool_mapping": {"test_tool": {"fuyao_ashare": "x"}},
        }

        # 第一次: miss
        resp1 = await server._execute_cached(
            "test_tool", {"x": "1"}, route_key="test_tool", ttl_key="historical"
        )
        assert cache.misses == 1, f"miss 应=1, got {cache.misses}"
        assert cache.hits == 0

        # 第二次: hit
        resp2 = await server._execute_cached(
            "test_tool", {"x": "1"}, route_key="test_tool", ttl_key="historical"
        )
        assert resp2["cache_hit"] is True
        assert cache.hits == 1, f"hit 应=1, got {cache.hits}"
        assert cache.misses == 1, "hit 不应增加 miss"

        await cache.close()

    async def test_non_caching_path_does_not_count(self, tmp_path, gateway_server_minimal):
        """can_cache=False (无 TTL 或 cache 禁用) 不应计入 hit/miss,
        否则运维误判缓存效果。"""
        from src.unihive.storage.cache import Cache, CacheConfig
        from src.unihive.gateway_server import GatewayServer

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


class TestExecuteCachedSingleFlight:
    """HIGH 1 (2026-09-07 4th-round audit): _execute_cached cache miss 后
    到 router.route() 之间必须有 per-key single-flight, 否则同 key 并发 miss
    会让 router 被调 N 次, 上游负载翻倍。
    _execute_cached 走 cache.get + cache.set 直接路径, 这一段没有 cache 层
    保护, 必须在 gateway 层补。"""

    async def test_concurrent_miss_only_routes_once(self, tmp_path, gateway_server_minimal):
        """5 个并发同 key miss → router 只调 1 次。"""
        from src.unihive.storage.cache import Cache, CacheConfig
        from src.unihive.gateway_server import GatewayServer

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        @dataclass
        class FakeResult:
            success: bool = True
            data: dict = None
            source: str = "fuyao_ashare"
            hops: list = None
            error: str | None = None

            def __post_init__(self):
                if self.hops is None:
                    self.hops = []

        class FakeRouter:
            def __init__(self):
                self.calls = 0

            async def route(self, route_key, params):
                self.calls += 1
                # 慢路由: 模拟上游延迟, 确保并发 miss 真的同时进入窗口
                await asyncio.sleep(0.1)
                return FakeResult(data={"v": self.calls})

        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter()
        server.config = {
            "cache": {"ttl": {"historical": 60}},
            "upstream_tool_mapping": {"t": {"fuyao_ashare": "x"}},
        }

        # 5 个并发同 key 请求
        results = await asyncio.gather(*[
            server._execute_cached(
                "t", {"k": "v"}, route_key="t", ttl_key="historical"
            )
            for _ in range(5)
        ])

        # 关键断言: 单飞, router 只调 1 次
        assert server.router.calls == 1, (
            f"单飞失败: 应只调 1 次 router, got {server.router.calls}"
        )
        # 所有 waiter 拿到 leader 的结果 (data={"v": 1})
        for r in results:
            assert r["success"] is True
            assert r["data"] == {"v": 1}, "waiter 应拿到 leader 的结果"
            assert r["cache_hit"] is False, "waiter 跟 leader 同窗口, 也算 miss"
        # 单飞下只记 1 次 miss (waiter 不重复计)
        assert cache.misses == 1
        assert cache.hits == 0

        await cache.close()

    async def test_single_flight_does_not_leak_after_completion(
        self, tmp_path, gateway_server_minimal
    ):
        """leader 完成 → in_flight dict 必须清掉, 否则下一次同 key 请求
        复用旧 future, 拿到错误结果。"""
        from src.unihive.storage.cache import Cache, CacheConfig
        from src.unihive.gateway_server import GatewayServer

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        @dataclass
        class FakeResult:
            success: bool = True
            data: dict = None
            source: str = "fuyao_ashare"
            hops: list = None
            error: str | None = None

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

        # 第一次 (cold miss + cache.set)
        await server._execute_cached(
            "t", {"k": "v"}, route_key="t", ttl_key="realtime_quote"
        )
        assert server.router.calls == 1

        # 清除 cache, 强制下次再 miss
        key = cache_strategy.cache_key("t", {"k": "v"})
        await cache.delete(key)

        # 第二次: in_flight 必须已清, 这次是新 leader
        await server._execute_cached(
            "t", {"k": "v"}, route_key="t", ttl_key="realtime_quote"
        )
        assert server.router.calls == 2, (
            "in_flight 没清导致 waiter 复用旧 future, 没触发新路由"
        )

        await cache.close()


class TestWalMode:
    """WAL mode: 解决 console (同步 sqlite3) 与 gateway (aiosqlite) 同时读写
    同一 cache.db 时的 "database is locked" 问题。"""

    async def test_wal_mode_is_active(self, tmp_path):
        """initialize 后 journal_mode 必须是 wal, 不是默认的 delete."""
        from src.unihive.storage.cache import Cache, CacheConfig
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
        from src.unihive.storage.cache import Cache, CacheConfig

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
        assert data["hits"] == 0 and data["misses"] == 3, (
            f"close 后 stats 应已落盘, got {data}"
        )
        # updated_at/pid 让控制台能识别统计是否来自当前进程
        assert isinstance(data["updated_at"], int)
        assert isinstance(data["pid"], int)

    async def test_hot_path_flushes_via_background_task(self, tmp_path):
        """越过 STATS_FLUSH_INTERVAL 时, record_hit/record_miss 必须
        通过 create_task 调度而不是阻塞同步调用。"""
        import json
        from src.unihive.storage.cache import Cache, CacheConfig

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
        assert data["hits"] == cache.STATS_FLUSH_INTERVAL and data["misses"] == 0, (
            f"热路径 async flush 应已落盘, got {data}"
        )
        assert isinstance(data["updated_at"], int)
        assert isinstance(data["pid"], int)

        await cache.close()


class TestCacheScopePolicy:
    """新缓存范围策略：
    - 实时行情(realtime_quote) 不缓存
    - 本地终端/本地库(mootdx2/tdx_quant/thsdk/omni) 不缓存
    - 仅远程 fuyao_* 源在非实时 key 下缓存
    """

    async def _server(self, tmp_path, gateway_server_minimal, result_source, config):
        from src.unihive.storage.cache import Cache, CacheConfig
        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "c.db")))
        await cache.initialize()

        @dataclass
        class FakeResult:
            success: bool = True
            data: object = None
            error: str | None = None
            source: str | None = result_source
            hops: list = None
            def __post_init__(self):
                if self.data is None:
                    self.data = {"v": 1}
                if self.hops is None:
                    self.hops = []

        class FakeRouter:
            def __init__(self):
                self.calls = 0
            async def route(self, route_key, params):
                self.calls += 1
                return FakeResult()

        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter()
        server.config = config
        return server, cache

    async def test_realtime_quote_never_cached(self, tmp_path, gateway_server_minimal):
        cfg = {
            "cache": {"ttl": {"realtime_quote": 10}},
            "upstream_tool_mapping": {"q": {"fuyao_ashare": "x"}},
        }
        server, cache = await self._server(tmp_path, gateway_server_minimal, "fuyao_ashare", cfg)
        for _ in range(2):
            await server._execute_cached("q", {"k": "v"}, route_key="q", ttl_key="realtime_quote")
        # 实时行情：每次都路由，不缓存
        assert server.router.calls == 2
        assert cache.misses == 0 and cache.hits == 0
        await cache.close()

    async def test_local_source_not_cached(self, tmp_path, gateway_server_minimal):
        """链上只有本地源时，即使配了 TTL 也不缓存。"""
        cfg = {
            "cache": {"ttl": {"historical": 60}},
            "upstream_tool_mapping": {"k": {"mootdx2": "x", "tdx_quant": "y"}},
        }
        server, cache = await self._server(tmp_path, gateway_server_minimal, "mootdx2", cfg)
        for _ in range(2):
            await server._execute_cached("k", {"k": "v"}, route_key="k", ttl_key="historical")
        assert server.router.calls == 2, "本地终端数据不应缓存"
        await cache.close()

    async def test_local_priority_does_not_pollute_fuyao_cache(
        self, tmp_path, gateway_server_minimal
    ):
        """链上 [mootdx2, fuyao_ashare]，本地优先命中时，不能把本地结果写进 fuyao 缓存键。"""
        cfg = {
            "cache": {"ttl": {"historical": 60}},
            "upstream_tool_mapping": {"k": {"mootdx2": "x", "fuyao_ashare": "y"}},
        }
        # 实际命中本地源
        server, cache = await self._server(tmp_path, gateway_server_minimal, "mootdx2", cfg)
        await server._execute_cached("k", {"k": "v"}, route_key="k", ttl_key="historical")
        key = cache_strategy.cache_key("k", {"k": "v"})
        assert await cache.get(key) is None, "本地命中结果不得写入缓存"
        await cache.close()

    async def test_fuyao_historical_cached(self, tmp_path, gateway_server_minimal):
        cfg = {
            "cache": {"ttl": {"historical": 60}},
            "upstream_tool_mapping": {"k": {"fuyao_ashare": "x"}},
        }
        server, cache = await self._server(tmp_path, gateway_server_minimal, "fuyao_ashare", cfg)
        await server._execute_cached("k", {"k": "v"}, route_key="k", ttl_key="historical")
        r2 = await server._execute_cached("k", {"k": "v"}, route_key="k", ttl_key="historical")
        assert server.router.calls == 1, "远程 fuyao 非实时响应应被缓存"
        assert r2["cache_hit"] is True
        await cache.close()


@dataclass
class _WarnFakeResult:
    success: bool
    data: object = None
    error: str | None = None
    source: str | None = None
    hops: list = None  # type: ignore
    warn: str | None = None

    def __post_init__(self):
        if self.hops is None:
            self.hops = []


WARN_TEXT = "代码 000001 为跨市场歧义码，本次按深市股票解析"


class TestExecuteCachedWarnPassthrough:
    """成功路径的 warn（消歧/no_data 提示）必须进入 HTTP 信封。"""

    async def test_warn_field_in_direct_response(self, tmp_path, gateway_server_minimal):
        from src.unihive.storage.cache import Cache, CacheConfig

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        class FakeRouter:
            async def route(self, route_key, params):
                return _WarnFakeResult(
                    success=True, data=[{"x": 1}], source="mootdx2", warn=WARN_TEXT,
                )

        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter()
        server.config = {"cache": {"ttl": {"realtime_quote": 10}}}

        resp = await server._execute_cached(
            "get_daily_local", {"code": "000001"},
            route_key="get_daily_local", ttl_key="realtime_quote",
        )
        assert resp["success"] is True
        assert resp["warn"] == WARN_TEXT
        await cache.close()

    async def test_warn_replayed_on_mysql_cache_hit(self, tmp_path, gateway_server_minimal):
        """fuyao 历史类响应：warn 随 {**resp} 入 MySQL 缓存，命中时原样带回。"""
        import asyncio as _a
        from src.unihive.storage.cache import Cache, CacheConfig

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()

        class FakeRouter:
            def __init__(self):
                self.calls = 0

            async def route(self, route_key, params):
                self.calls += 1
                return _WarnFakeResult(
                    success=True, data={"close": 100.0},
                    source="fuyao_ashare", warn=WARN_TEXT,
                )

        server = gateway_server_minimal
        server.cache = cache
        router = FakeRouter()
        server.router = router
        server.config = {
            "cache": {"ttl": {"historical": 60}},
            "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
        }
        r1 = await server._execute_cached(
            "hist_tool", {"thscodes": "600000.SH"},
            route_key="hist_tool", ttl_key="historical",
        )
        r2 = await server._execute_cached(
            "hist_tool", {"thscodes": "600000.SH"},
            route_key="hist_tool", ttl_key="historical",
        )
        assert r1["warn"] == WARN_TEXT
        assert router.calls == 1
        assert r2["cache_hit"] is True
        assert r2["warn"] == WARN_TEXT
        await cache.close()

    async def test_warn_replayed_to_single_flight_waiter(self, tmp_path, gateway_server_minimal):
        """并发同 key miss：waiter 复用 leader 的响应，warn 不能丢。"""
        from src.unihive.storage.cache import Cache, CacheConfig

        cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
        await cache.initialize()
        gate = asyncio.Event()

        class FakeRouter:
            def __init__(self):
                self.calls = 0

            async def route(self, route_key, params):
                self.calls += 1
                await gate.wait()
                return _WarnFakeResult(
                    success=True, data={"close": 100.0},
                    source="fuyao_ashare", warn=WARN_TEXT,
                )

        server = gateway_server_minimal
        server.cache = cache
        router = FakeRouter()
        server.router = router
        server.config = {
            "cache": {"ttl": {"historical": 60}},
            "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
        }

        t1 = asyncio.create_task(server._execute_cached(
            "hist_tool", {"thscodes": "600000.SH"},
            route_key="hist_tool", ttl_key="historical",
        ))
        await asyncio.sleep(0.1)
        t2 = asyncio.create_task(server._execute_cached(
            "hist_tool", {"thscodes": "600000.SH"},
            route_key="hist_tool", ttl_key="historical",
        ))
        await asyncio.sleep(0.1)
        gate.set()
        r1, r2 = await asyncio.gather(t1, t2)

        assert router.calls == 1, "waiter 必须复用 leader future，不重复路由"
        assert r1["warn"] == WARN_TEXT
        assert r2["warn"] == WARN_TEXT
        await cache.close()


# ===== 日期(参数)感知 TTL（2026-09-21）=====

_TZ = timezone(timedelta(hours=8))

# 冻结用时钟: 工作日 (周一 2026-09-14) 上午连续竞价时段 10:00。
# 此区间内短档 TTL 必须返回配置原值; 不冻结的话用例在周末/盘后跑
# 会拿到「到下一变化边界」秒数, 随时钟漂移而失败。
_IN_SESSION_NOW = datetime(2026, 9, 14, 10, 0, tzinfo=_TZ).timestamp()


def _midnight_ms(day: date) -> int:
    """上海(北京)零点毫秒戳, 与 registry._date_str_to_ms 同口径。"""
    return int(datetime.combine(day, dtime(), tzinfo=_TZ).timestamp() * 1000)


class TestAsOfDateParsing:
    """归一化后日期落在不同键/格式, as_of_date 需统一解析。"""

    def test_date_ms_integer(self):
        day = date(2026, 9, 18)
        assert cache_strategy.as_of_date({"date_ms": _midnight_ms(day)}) == day

    def test_date_string_dashed(self):
        assert cache_strategy.as_of_date({"date": "2026-09-18"}) == date(2026, 9, 18)

    def test_end_date_eight_digits(self):
        assert cache_strategy.as_of_date({"end_date": "20260918"}) == date(2026, 9, 18)

    def test_key_precedence_date_ms_first(self):
        # 同一次调用不会同时出现, 但优先级确定: date_ms > date > end_date > start_date
        params = {
            "date_ms": _midnight_ms(date(2026, 9, 18)),
            "date": "2026-09-10",
            "end_date": "20260905",
        }
        assert cache_strategy.as_of_date(params) == date(2026, 9, 18)

    def test_no_recognised_date(self):
        assert cache_strategy.as_of_date({"thscode": "600519.SH"}) is None

    def test_bad_values_ignored(self):
        assert cache_strategy._coerce_as_of_date("not-a-date") is None
        assert cache_strategy._coerce_as_of_date(True) is None
        assert cache_strategy._coerce_as_of_date("2026-13-99") is None


class TestFrozenSnapshot:
    """is_frozen_snapshot / is_historical_special_data 语义。"""

    TODAY = date(2026, 9, 21)

    def test_past_date_is_frozen(self):
        params = {"date_ms": _midnight_ms(self.TODAY - timedelta(days=1))}
        assert cache_strategy.is_frozen_snapshot(params, today=self.TODAY) is True

    def test_today_not_frozen(self):
        params = {"date": "2026-09-21"}
        assert cache_strategy.is_frozen_snapshot(params, today=self.TODAY) is False

    def test_future_not_frozen(self):
        params = {"date": "2026-09-30"}
        assert cache_strategy.is_frozen_snapshot(params, today=self.TODAY) is False

    def test_no_date_not_frozen(self):
        # 日期留空 → 工具默认当天, 不可按冻结档
        assert cache_strategy.is_frozen_snapshot({}, today=self.TODAY) is False

    def test_historical_special_data_gate(self):
        past = {"date_ms": _midnight_ms(self.TODAY - timedelta(days=2))}
        assert cache_strategy.is_historical_special_data("special_data", past, today=self.TODAY)
        # 当天/无日期不算
        assert not cache_strategy.is_historical_special_data(
            "special_data", {"date_ms": _midnight_ms(self.TODAY)}, today=self.TODAY
        )
        assert not cache_strategy.is_historical_special_data("special_data", {}, today=self.TODAY)
        # historical 键即使 end_date 在过去也不提升（前复权K线会变）
        assert not cache_strategy.is_historical_special_data(
            "historical", {"end_date": "20260918"}, today=self.TODAY
        )


def _factory_ttl_config() -> dict:
    """出厂 cache.ttl（与 upstreams.yaml 同步）+ 纯 fuyao 路由链。"""
    return {
        "cache": {"ttl": {
            "historical": 86400,
            "fundamentals": 86400,
            "valuation": 30,
            "special_data": 300,
            "ticker_list": 604800,
            "workday": 15552000,
            "search": 60,
            "historical_immutable": 15552000,
            "fund_data": 604800,
            "fund_holdings": 86400,
            "fund_history": 86400,
        }},
        "upstream_tool_mapping": {"pool_tool": {"fuyao_ashare": "x"}},
    }


class TestEffectiveTtl:
    """effective_ttl 三档解析。"""

    TODAY = date(2026, 9, 21)

    def test_past_special_data_promoted_to_immutable(self):
        cfg = _factory_ttl_config()
        params = {"date_ms": _midnight_ms(self.TODAY - timedelta(days=1))}
        assert cache_strategy.effective_ttl(cfg, "pool_tool", "special_data", params) == 15552000

    def test_today_special_data_keeps_short_ttl(self):
        cfg = _factory_ttl_config()
        params = {"date_ms": _midnight_ms(self.TODAY)}
        assert cache_strategy.effective_ttl(
            cfg, "pool_tool", "special_data", params, now=_IN_SESSION_NOW
        ) == 300

    def test_missing_date_special_data_keeps_short_ttl(self):
        cfg = _factory_ttl_config()
        assert cache_strategy.effective_ttl(
            cfg, "pool_tool", "special_data", {}, now=_IN_SESSION_NOW
        ) == 300

    def test_historical_key_with_past_end_date_not_promoted(self):
        cfg = _factory_ttl_config()
        params = {"end_date": "20260918"}
        assert cache_strategy.effective_ttl(cfg, "hist", "historical", params) == 86400

    def test_fuyao_tool_override_wins_over_promotion(self):
        cfg = _factory_ttl_config()
        # 手工给该工具 60s，即使查过去交易日也尊重手工值
        cfg["fuyao_cache"] = {"tools": {"pool_tool": 60}}
        params = {"date_ms": _midnight_ms(self.TODAY - timedelta(days=5))}
        assert cache_strategy.effective_ttl(cfg, "pool_tool", "special_data", params) == 60

    def test_missing_immutable_falls_back_to_special_data(self):
        cfg = _factory_ttl_config()
        del cfg["cache"]["ttl"]["historical_immutable"]
        params = {"date_ms": _midnight_ms(self.TODAY - timedelta(days=1))}
        # 注: 日期恰为"上一个交易日", 冻结档缺失时回退到 special_data 短档;
        # 锁死在盘中时钟, 避免周末回退值变成到零点的秒数。
        assert cache_strategy.effective_ttl(
            cfg, "pool_tool", "special_data", params, now=_IN_SESSION_NOW
        ) == 300

    def test_zero_base_ttl_returns_none(self):
        """0 = 显式不缓存: 不得进入时段感知换算被提升为长 TTL。"""
        cfg = _factory_ttl_config()
        cfg["cache"]["ttl"]["special_data"] = 0
        assert cache_strategy.effective_ttl(
            cfg, "pool_tool", "special_data", {}, now=_IN_SESSION_NOW
        ) is None

    def test_negative_base_ttl_returns_none(self):
        cfg = _factory_ttl_config()
        cfg["cache"]["ttl"]["special_data"] = -30
        assert cache_strategy.effective_ttl(
            cfg, "pool_tool", "special_data", {}, now=_IN_SESSION_NOW
        ) is None

    def test_workday_and_fund_keys_resolve(self):
        cfg = _factory_ttl_config()
        assert cache_strategy.effective_ttl(cfg, "cal", "workday", {}) == 15552000
        assert cache_strategy.effective_ttl(cfg, "fp", "fund_data", {}) == 604800
        assert cache_strategy.effective_ttl(cfg, "fh", "fund_holdings", {}) == 86400

    def test_unknown_key_returns_none(self):
        cfg = _factory_ttl_config()
        assert cache_strategy.effective_ttl(cfg, "x", "index_data", {}) is None


class TestDateAwareCachedWrites:
    """_execute_cached 写库时 TTL 随日期变（过去→冻结档，当天/留空→短档）。"""

    async def _server_with_spy(self, tmp_path, gateway_server_minimal):
        from src.unihive.storage.cache import Cache, CacheConfig

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
            async def route(self, route_key, params):
                return FakeResult(data={"rows": []})

        server = gateway_server_minimal
        server.cache = cache
        server.router = FakeRouter()
        server.config = _factory_ttl_config()

        captured: dict = {}
        orig_set = cache.set

        async def spy_set(key, value, ttl=None):
            captured["ttl"] = ttl
            return await orig_set(key, value, ttl=ttl)

        cache.set = spy_set
        return server, cache, captured

    async def test_past_date_writes_immutable_ttl(self, tmp_path, gateway_server_minimal):
        server, cache, captured = await self._server_with_spy(tmp_path, gateway_server_minimal)
        past = cache_strategy.shanghai_today() - timedelta(days=3)
        await server._execute_cached(
            "pool_tool", {"date_ms": _midnight_ms(past)},
            route_key="pool_tool", ttl_key="special_data",
        )
        assert captured["ttl"] == 15552000
        await cache.close()

    async def test_today_date_writes_short_ttl(
        self, tmp_path, gateway_server_minimal, monkeypatch
    ):
        # 锁死在工作日盘中: 短档写配置原值 300s (周末会写成到零点的秒数)。
        monkeypatch.setattr(cache_strategy.time, "time", lambda: _IN_SESSION_NOW)
        server, cache, captured = await self._server_with_spy(tmp_path, gateway_server_minimal)
        today = cache_strategy.shanghai_today()
        await server._execute_cached(
            "pool_tool", {"date_ms": _midnight_ms(today)},
            route_key="pool_tool", ttl_key="special_data",
        )
        assert captured["ttl"] == 300
        await cache.close()

    async def test_missing_date_writes_short_ttl(
        self, tmp_path, gateway_server_minimal, monkeypatch
    ):
        monkeypatch.setattr(cache_strategy.time, "time", lambda: _IN_SESSION_NOW)
        server, cache, captured = await self._server_with_spy(tmp_path, gateway_server_minimal)
        await server._execute_cached(
            "pool_tool", {},
            route_key="pool_tool", ttl_key="special_data",
        )
        assert captured["ttl"] == 300
        await cache.close()


class TestIntradaySessionTtl:
    """时段感知短档: 六个时段分支 + 周末 (2026-09-14=周一 ... 09-20=周日)。"""

    def _at(self, day: int, hour: int, minute: int = 0) -> float:
        return datetime(2026, 9, day, hour, minute, tzinfo=_TZ).timestamp()

    def test_pre_open_waits_for_morning_open(self):
        # 08:00 → 9:30 = 5400s
        assert cache_strategy.intraday_session_ttl(300, now=self._at(14, 8)) == 5400

    def test_morning_session_uses_configured_short_ttl(self):
        assert cache_strategy.intraday_session_ttl(300, now=self._at(14, 10)) == 300

    def test_lunch_break_waits_for_afternoon_open(self):
        # 12:00 → 13:00 = 3600s
        assert cache_strategy.intraday_session_ttl(300, now=self._at(14, 12)) == 3600

    def test_afternoon_session_uses_configured_short_ttl(self):
        assert cache_strategy.intraday_session_ttl(300, now=self._at(14, 14)) == 300

    def test_post_close_waits_until_midnight(self):
        # 16:00 → 次日零点 = 8h = 28800s
        assert cache_strategy.intraday_session_ttl(300, now=self._at(14, 16)) == 28800

    def test_saturday_caches_to_midnight(self):
        # 周六 10:00 → 次日零点 = 14h = 50400s
        assert cache_strategy.intraday_session_ttl(300, now=self._at(19, 10)) == 50400

    def test_sunday_evening_caches_to_midnight(self):
        # 周日 23:00 → 零点 = 3600s
        assert cache_strategy.intraday_session_ttl(300, now=self._at(20, 23)) == 3600

    def test_floor_is_one_second_near_boundary(self):
        # 紧贴 9:30 边界时下限 1s, 不返回 0 (0 另有"不缓存"语义)
        near = datetime(2026, 9, 14, 9, 29, 59, tzinfo=_TZ).timestamp()
        assert cache_strategy.intraday_session_ttl(300, now=near) == 1

    def test_index_data_sixty_second_short_ttl_same_branches(self):
        # 另一短档 index_data=60s: 盘中返回配置原值, 午间仍等到 13:00
        assert cache_strategy.intraday_session_ttl(60, now=self._at(14, 10)) == 60
        assert cache_strategy.intraday_session_ttl(60, now=self._at(14, 12)) == 3600
