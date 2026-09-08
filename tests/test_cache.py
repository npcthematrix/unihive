"""Cache 单元测试"""
import asyncio
import time

import pytest


class TestCacheBasics:
    async def test_set_and_get(self, temp_cache):
        await temp_cache.set("k1", {"v": 1})
        assert await temp_cache.get("k1") == {"v": 1}

    async def test_missing_key_returns_none(self, temp_cache):
        assert await temp_cache.get("nope") is None

    async def test_overwrite(self, temp_cache):
        await temp_cache.set("k", 1)
        await temp_cache.set("k", 2)
        assert await temp_cache.get("k") == 2

    async def test_delete(self, temp_cache):
        await temp_cache.set("k", 1)
        assert await temp_cache.delete("k") is True
        assert await temp_cache.get("k") is None

    async def test_clear(self, temp_cache):
        await temp_cache.set("a", 1)
        await temp_cache.set("b", 2)
        await temp_cache.clear()
        assert await temp_cache.get("a") is None
        assert await temp_cache.get("b") is None


class TestCacheTTL:
    async def test_expired_key_returns_none(self, temp_cache):
        await temp_cache.set("k", 1, ttl=1)
        assert await temp_cache.get("k") == 1
        await asyncio.sleep(1.2)
        assert await temp_cache.get("k") is None

    async def test_no_ttl_means_never_expires(self, temp_cache):
        await temp_cache.set("k", "forever", ttl=None)
        # 等一会不应当过期
        await asyncio.sleep(0.1)
        assert await temp_cache.get("k") == "forever"

    async def test_cleanup_expired(self, temp_cache):
        await temp_cache.set("old", "x", ttl=1)
        await temp_cache.set("new", "y", ttl=60)
        await asyncio.sleep(1.2)
        deleted = await temp_cache.cleanup_expired()
        assert deleted >= 1
        assert await temp_cache.get("old") is None
        assert await temp_cache.get("new") == "y"


class TestCacheDisabled:
    async def test_disabled_returns_none(self, tmp_path):
        from src.storage.cache import Cache, CacheConfig

        cfg = CacheConfig(enabled=False, db_path=str(tmp_path / "c.db"))
        c = Cache(cfg)
        await c.initialize()
        assert await c.get("k") is None
        assert await c.set("k", 1) is False
        await c.close()


