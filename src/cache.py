"""
缓存模块 - 基于 SQLite 的请求缓存
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


@dataclass
class CacheConfig:
    """缓存配置"""
    enabled: bool = True
    db_path: str = "logs/cache.db"
    max_entries: int = 10000


class Cache:
    """SQLite 缓存实现"""

    STATS_FILE = "logs/cache_stats.json"
    STATS_FLUSH_INTERVAL = 50

    def __init__(self, config: CacheConfig):
        self.config = config
        self.enabled = config.enabled
        self._engine = None
        self._session_factory = None
        self._lock = asyncio.Lock()
        self._initialized = False
        self._hits = 0
        self._misses = 0
        self._dirty_events = 0

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    def hit_rate(self) -> float | None:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else None

    def _persist_stats(self):
        try:
            from pathlib import Path
            Path(self.STATS_FILE).parent.mkdir(parents=True, exist_ok=True)
            tmp = self.STATS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"hits": self._hits, "misses": self._misses}, f)
            Path(tmp).replace(self.STATS_FILE)
            self._dirty_events = 0
        except Exception as e:
            logger.warning(f"Failed to persist cache stats: {e}")

    def _record_hit(self):
        self._hits += 1
        self._dirty_events += 1
        if self._dirty_events >= self.STATS_FLUSH_INTERVAL:
            self._persist_stats()

    def _record_miss(self):
        self._misses += 1
        self._dirty_events += 1
        if self._dirty_events >= self.STATS_FLUSH_INTERVAL:
            self._persist_stats()

    async def initialize(self):
        """异步初始化数据库连接"""
        if not self.enabled or self._initialized:
            return

        try:
            db_path = Path(self.config.db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

            self._engine = create_async_engine(
                f"sqlite+aiosqlite:///{self.config.db_path}",
                echo=False,
            )
            self._session_factory = sessionmaker(
                self._engine, class_=AsyncSession, expire_on_commit=False
            )

            # 创建表
            async with self._session_factory() as session:
                await session.execute(text("""
                    CREATE TABLE IF NOT EXISTS cache (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        expires_at INTEGER,
                        created_at INTEGER NOT NULL
                    )
                """))
                await session.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_expires_at ON cache(expires_at)
                """))
                await session.commit()

            self._initialized = True
            logger.info(f"Cache initialized at {self.config.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize cache: {e}")
            self.enabled = False

    async def get_or_set(
        self,
        key: str,
        ttl: int | None,
        factory,
    ) -> tuple[Any, bool]:
        """命中返回 (value, True)；未命中调 factory() 算结果并缓存，返回 (value, False)。

        factory 应是 async callable，无参，返回要缓存的值。失败不缓存。
        """
        cached = await self.get(key)
        if cached is not None:
            self._record_hit()
            return cached, True

        self._record_miss()
        value = await factory()
        if value is not None:
            await self.set(key, value, ttl=ttl)
        return value, False

    async def get(self, key: str) -> Any | None:
        """获取缓存值"""
        if not self.enabled:
            return None

        async with self._lock:
            try:
                async with self._session_factory() as session:
                    result = await session.execute(
                        text("SELECT value, expires_at FROM cache WHERE key = :key"),
                        {"key": key}
                    )
                    row = result.fetchone()

                    if row is None:
                        return None

                    value_str, expires_at = row

                    # 检查是否过期
                    if expires_at and expires_at < time.time():
                        await session.execute(
                            text("DELETE FROM cache WHERE key = :key"),
                            {"key": key}
                        )
                        await session.commit()
                        return None

                    return json.loads(value_str)

            except Exception as e:
                logger.error(f"Cache get error: {e}")
                return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """设置缓存值"""
        if not self.enabled:
            return False

        async with self._lock:
            try:
                async with self._session_factory() as session:
                    value_str = json.dumps(value)
                    expires_at = int(time.time() + ttl) if ttl else None

                    await session.execute(
                        text("""
                            INSERT OR REPLACE INTO cache (key, value, expires_at, created_at)
                            VALUES (:key, :value, :expires_at, :created_at)
                        """),
                        {
                            "key": key,
                            "value": value_str,
                            "expires_at": expires_at,
                            "created_at": int(time.time())
                        }
                    )
                    await session.commit()
                    return True

            except Exception as e:
                logger.error(f"Cache set error: {e}")
                return False

    async def delete(self, key: str) -> bool:
        """删除缓存项"""
        if not self.enabled:
            return False

        async with self._lock:
            try:
                async with self._session_factory() as session:
                    await session.execute(
                        text("DELETE FROM cache WHERE key = :key"),
                        {"key": key}
                    )
                    await session.commit()
                    return True

            except Exception as e:
                logger.error(f"Cache delete error: {e}")
                return False

    async def cleanup_expired(self) -> int:
        """清理过期缓存，返回删除的条目数"""
        if not self.enabled:
            return 0

        async with self._lock:
            try:
                async with self._session_factory() as session:
                    result = await session.execute(
                        text("""
                            DELETE FROM cache
                            WHERE expires_at IS NOT NULL AND expires_at < :now
                        """),
                        {"now": time.time()}
                    )
                    await session.commit()
                    deleted = result.rowcount
                    if deleted > 0:
                        logger.info(f"Cleaned up {deleted} expired cache entries")
                    return deleted

            except Exception as e:
                logger.error(f"Cache cleanup error: {e}")
                return 0

    async def clear(self) -> bool:
        """清空所有缓存"""
        if not self.enabled:
            return False

        async with self._lock:
            try:
                async with self._session_factory() as session:
                    await session.execute(text("DELETE FROM cache"))
                    await session.commit()
                    logger.info("Cache cleared")
                    return True

            except Exception as e:
                logger.error(f"Cache clear error: {e}")
                return False

    async def close(self):
        """关闭数据库连接"""
        if self._dirty_events > 0:
            self._persist_stats()
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("Cache closed")

    @staticmethod
    def _create_cache_table_sql() -> str:
        """创建缓存表的 SQL"""
        return """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expires_at INTEGER,
                created_at INTEGER NOT NULL,
                INDEX idx_expires_at (expires_at)
            )
        """
