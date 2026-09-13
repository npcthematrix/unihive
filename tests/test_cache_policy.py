"""FUYAO-only 策略 + LRU 驱逐 + execute_cached 行为测试。"""
import asyncio
from dataclasses import dataclass

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
        assert data == {"hits": 0, "misses": 3}, (
            f"close 后 stats 应已落盘, got {data}"
        )

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
        assert data == {"hits": cache.STATS_FLUSH_INTERVAL, "misses": 0}, (
            f"热路径 async flush 应已落盘, got {data}"
        )

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
