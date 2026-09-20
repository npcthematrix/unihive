"""Round-12 H2: TTL=0 是「显式不缓存」, 绝不能落成永久缓存。

旧链路缺陷:
  _execute_cached 只判 ``ttl is not None`` → 0 通过判定;
  Cache._set_impl 用 ``int(now + ttl) if ttl else None`` → 0 落成
  expires_at=NULL, 读侧 ``if expires_at and ...`` 永不判过期 = 永久缓存。

修复三层:
  1. cache_strategy.normalize_ttl 纯函数: ≤0 / 非法类型 → None;
  2. _execute_cached 解析 TTL 后归一化;
  3. Cache.set 防御: ttl ≤ 0 / 非数值 / bool 一律不写入。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.unihive.core import cache_strategy
from src.unihive.storage.cache import Cache, CacheConfig


# ---------------------------------------------------------------------------
# 1. normalize_ttl 纯函数
# ---------------------------------------------------------------------------


class TestNormalizeTtl:
    def test_none_passthrough(self):
        assert cache_strategy.normalize_ttl(None) is None

    @pytest.mark.parametrize("value", [0, -1, -100])
    def test_non_positive_means_no_cache(self, value):
        # 合同 (fuyao_cache 模型 docstring): 0 = 显式不缓存
        assert cache_strategy.normalize_ttl(value) is None

    def test_positive_int_passthrough(self):
        assert cache_strategy.normalize_ttl(60) == 60

    def test_positive_float_floored_to_int(self):
        assert cache_strategy.normalize_ttl(60.9) == 60

    @pytest.mark.parametrize("bad", ["60", True, False, object(), 1j])
    def test_garbage_rejected(self, bad):
        with pytest.raises((TypeError, ValueError)):
            cache_strategy.normalize_ttl(bad)


# ---------------------------------------------------------------------------
# 3. Cache.set 纵深防御
# ---------------------------------------------------------------------------


@pytest.fixture
async def cache(tmp_path):
    c = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
    await c.initialize()
    yield c
    await c.close()


class TestCacheSetTtlGuard:
    async def test_zero_ttl_is_noop(self, cache):
        assert await cache.set("k", {"v": 1}, ttl=0) is False
        assert await cache.get("k") is None

    async def test_negative_ttl_is_noop(self, cache):
        assert await cache.set("k", {"v": 1}, ttl=-5) is False
        assert await cache.get("k") is None

    async def test_bool_ttl_is_noop(self, cache):
        assert await cache.set("k", {"v": 1}, ttl=True) is False
        assert await cache.get("k") is None

    async def test_positive_ttl_still_writes(self, cache):
        assert await cache.set("k", {"v": 1}, ttl=60) is True
        assert await cache.get("k") == {"v": 1}


# ---------------------------------------------------------------------------
# 2. _execute_cached: TTL=0 端到端不缓存 (全局档 + fuyao override)
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    success: bool = True
    data: object = None
    error: str | None = None
    source: str | None = None
    hops: list | None = None

    def __post_init__(self):
        if self.hops is None:
            self.hops = []


class _FakeRouter:
    def __init__(self, data):
        self.calls = 0
        self._data = data

    async def route(self, route_key, params):
        self.calls += 1
        return _FakeResult(success=True, data=self._data, source="fuyao_ashare")


async def _server_with(gateway_server_minimal, tmp_path, config, router):
    cache = Cache(CacheConfig(enabled=True, db_path=str(tmp_path / "cache.db")))
    await cache.initialize()
    server = gateway_server_minimal
    server.cache = cache
    server.router = router
    server.config = config
    return server, cache


class TestExecuteCachedZeroTtl:
    async def test_global_ttl_zero_not_cached(self, tmp_path, gateway_server_minimal):
        router = _FakeRouter({"close": 100.0})
        server, cache = await _server_with(
            gateway_server_minimal,
            tmp_path,
            {
                "cache": {"ttl": {"historical": 0}},
                "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
            },
            router,
        )
        for _ in range(2):
            resp = await server._execute_cached(
                "hist_tool", {"thscodes": "600000.SH"},
                route_key="hist_tool", ttl_key="historical",
            )
            assert resp["success"] is True
        # 0 = 不缓存: 每次都必须真正路由
        assert router.calls == 2
        key = cache_strategy.cache_key("hist_tool", {"thscodes": "600000.SH"})
        assert await cache.get(key) is None

    async def test_negative_ttl_not_cached(self, tmp_path, gateway_server_minimal):
        router = _FakeRouter({"close": 100.0})
        server, cache = await _server_with(
            gateway_server_minimal,
            tmp_path,
            {
                "cache": {"ttl": {"historical": -30}},
                "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
            },
            router,
        )
        await server._execute_cached(
            "hist_tool", {"thscodes": "600000.SH"},
            route_key="hist_tool", ttl_key="historical",
        )
        assert router.calls == 1
        key = cache_strategy.cache_key("hist_tool", {"thscodes": "600000.SH"})
        assert await cache.get(key) is None

    async def test_fuyao_tool_override_zero_not_cached(self, tmp_path, gateway_server_minimal):
        router = _FakeRouter({"close": 100.0})
        server, cache = await _server_with(
            gateway_server_minimal,
            tmp_path,
            {
                "cache": {"ttl": {"historical": 3600}},
                "fuyao_cache": {"tools": {"hist_tool": 0}},
                "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
            },
            router,
        )
        for _ in range(2):
            await server._execute_cached(
                "hist_tool", {"thscodes": "600000.SH"},
                route_key="hist_tool", ttl_key="historical",
            )
        assert router.calls == 2
        key = cache_strategy.cache_key("hist_tool", {"thscodes": "600000.SH"})
        assert await cache.get(key) is None

    async def test_fuyao_garbage_ttl_degrades_to_no_cache(
        self, tmp_path, gateway_server_minimal
    ):
        """fuyao override 非数值 (绕过 PUT 门的 YAML/手改库行) 不应变 500。"""
        router = _FakeRouter({"close": 100.0})
        server, cache = await _server_with(
            gateway_server_minimal,
            tmp_path,
            {
                "cache": {"ttl": {"historical": 3600}},
                "fuyao_cache": {"tools": {"hist_tool": "1h"}},
                "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
            },
            router,
        )
        resp = await server._execute_cached(
            "hist_tool", {"thscodes": "600000.SH"},
            route_key="hist_tool", ttl_key="historical",
        )
        assert resp["success"] is True
        assert router.calls == 1
        key = cache_strategy.cache_key("hist_tool", {"thscodes": "600000.SH"})
        assert await cache.get(key) is None

    async def test_positive_ttl_still_caches(self, tmp_path, gateway_server_minimal):
        """对照: 正数 TTL 行为不变 (第二次命中缓存, router 只调一次)。"""
        router = _FakeRouter({"close": 100.0})
        server, _ = await _server_with(
            gateway_server_minimal,
            tmp_path,
            {
                "cache": {"ttl": {"historical": 60}},
                "upstream_tool_mapping": {"hist_tool": {"fuyao_ashare": "x"}},
            },
            router,
        )
        for _ in range(2):
            await server._execute_cached(
                "hist_tool", {"thscodes": "600000.SH"},
                route_key="hist_tool", ttl_key="historical",
            )
        assert router.calls == 1
