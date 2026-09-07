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
    # M1 (2026-09-07 5th-round audit): 单次 SQLite 操作超时, 防止 aiosqlite
    # 卡死 (例如 console 同进程 sqlite3 锁了 DB) 时让 request 永久 hang。
    # 超时后返回安全默认值 (None / False / 0) + log warning, 下次重试。
    operation_timeout: float = 5.0


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
        # Single-flight: 同一 key 并发 miss 时只触发一次 factory。
        # 详见 get_or_set。
        self._in_flight: dict[str, asyncio.Future] = {}

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    def hit_rate(self) -> float | None:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else None

    async def _persist_stats_async(self):
        """异步刷 stats 到 disk, 不阻塞 event loop.
        文件 I/O 用 asyncio.to_thread 放后台线程."""
        try:
            await asyncio.to_thread(self._persist_stats_sync)
        except Exception as e:
            logger.warning(f"Failed to persist cache stats: {e}")

    def _persist_stats_sync(self):
        # 同步版, 给 to_thread 调度或非 loop 上下文兜底
        from pathlib import Path
        Path(self.STATS_FILE).parent.mkdir(parents=True, exist_ok=True)
        tmp = self.STATS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"hits": self._hits, "misses": self._misses}, f)
        Path(tmp).replace(self.STATS_FILE)
        self._dirty_events = 0

    def _maybe_persist_stats(self):
        """在事件循环里调度一次异步刷 stats；无 loop 时降级为同步。

        热路径(record_hit/record_miss)是同步方法:
        - 在 gateway 事件循环里 → create_task 把文件 I/O 丢后台线程,
          不阻塞下一个请求处理。
        - 在脚本/测试上下文(没运行 loop)→ 直接同步刷, 避免丢 stats。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._persist_stats_sync()
            return
        loop.create_task(self._persist_stats_async())

    def record_hit(self):
        """记一次缓存命中。gateway 热路径直接调用。"""
        self._hits += 1
        self._dirty_events += 1
        if self._dirty_events >= self.STATS_FLUSH_INTERVAL:
            self._maybe_persist_stats()

    def record_miss(self):
        """记一次缓存未命中。gateway 热路径直接调用。"""
        self._misses += 1
        self._dirty_events += 1
        if self._dirty_events >= self.STATS_FLUSH_INTERVAL:
            self._maybe_persist_stats()

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
                # WAL 模式: 解决 console (同步 sqlite3) 与 gateway (aiosqlite)
                # 同时读写同一 cache.db 时的 "database is locked" 问题。
                await session.execute(text("PRAGMA journal_mode=WAL"))
                await session.execute(text("PRAGMA synchronous=NORMAL"))
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

        factory 应是 async callable，无参，返回要缓存的值。失败（None）不缓存。

        Single-flight：同一 key 并发 miss 只触发一次 factory，其余 waiter 复用同一结果。
        """
        if not self.enabled:
            value = await factory()
            return value, False

        cached = await self.get(key)
        if cached is not None:
            self.record_hit()
            return cached, True

        # 未命中：若已有 in-flight 计算，复用它（避免 thundering herd）。
        existing = self._in_flight.get(key)
        if existing is not None and not existing.done():
            value = await existing
            return value, False

        # 本次 miss 记账只对发起者记录；waiter 不重复记。
        self.record_miss()

        future = asyncio.get_running_loop().create_future()
        self._in_flight[key] = future

        async def _runner():
            try:
                value = await factory()
                if value is not None:
                    # 缓存层故障不应让上游成功响应"看起来失败"。
                    # 下次同 key 请求会再次 miss + factory，业务可恢复。
                    try:
                        await self.set(key, value, ttl=ttl)
                    except Exception as e:
                        logger.warning(f"cache.set failed for {key}: {e}")
                future.set_result(value)
                return value
            except BaseException as exc:
                future.set_exception(exc)
                raise
            finally:
                # 先 set_result/set_exception 再 pop，waiter 才能在 in-flight
                # 仍登记期间正确走到 await 分支。
                self._in_flight.pop(key, None)

        asyncio.create_task(_runner())

        value = await future
        return value, False

    async def get(self, key: str) -> Any | None:
        """获取缓存值。 M1: 包 operation_timeout 防 SQLite 卡死。"""
        if not self.enabled:
            return None
        try:
            return await asyncio.wait_for(
                self._get_impl(key), timeout=self.config.operation_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Cache get timed out after {self.config.operation_timeout}s "
                f"for key={key}; returning None"
            )
            return None

    async def _get_impl(self, key: str) -> Any | None:
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
        """设置缓存值。 M1: 包 operation_timeout。"""
        if not self.enabled:
            return False
        try:
            return await asyncio.wait_for(
                self._set_impl(key, value, ttl),
                timeout=self.config.operation_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Cache set timed out after {self.config.operation_timeout}s "
                f"for key={key}; returning False"
            )
            return False

    async def _set_impl(self, key: str, value: Any, ttl: int | None = None) -> bool:
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
        """删除缓存项。 M1: 包 operation_timeout。"""
        if not self.enabled:
            return False
        try:
            return await asyncio.wait_for(
                self._delete_impl(key), timeout=self.config.operation_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Cache delete timed out after {self.config.operation_timeout}s "
                f"for key={key}; returning False"
            )
            return False

    async def _delete_impl(self, key: str) -> bool:
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
        """清理过期 + LRU 驱逐。 M1: 包 operation_timeout。"""
        if not self.enabled:
            return 0
        try:
            return await asyncio.wait_for(
                self._cleanup_impl(), timeout=self.config.operation_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Cache cleanup timed out after "
                f"{self.config.operation_timeout}s; returning 0"
            )
            return 0

    async def _cleanup_impl(self) -> int:
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
                    deleted_total = result.rowcount or 0

                    count_row = await session.execute(
                        text("SELECT COUNT(*) FROM cache")
                    )
                    count = count_row.scalar() or 0

                    max_entries = self.config.max_entries
                    if count > max_entries:
                        target = int(max_entries * 0.9)
                        evict_n = count - target
                        evict_result = await session.execute(
                            text("""
                                DELETE FROM cache WHERE key IN (
                                    SELECT key FROM cache
                                    ORDER BY created_at ASC
                                    LIMIT :n
                                )
                            """),
                            {"n": evict_n},
                        )
                        await session.commit()
                        evict_count = evict_result.rowcount or 0
                        deleted_total += evict_count
                        logger.warning(
                            f"Cache over max_entries ({count} > {max_entries}); "
                            f"LRU evicted {evict_count} oldest entries"
                        )

                    if deleted_total > 0:
                        logger.info(f"Cleaned up {deleted_total} cache entries")
                    return deleted_total

            except Exception as e:
                logger.error(f"Cache cleanup error: {e}")
                return 0

    async def clear(self) -> bool:
        """清空所有缓存。 M1: 包 operation_timeout。"""
        if not self.enabled:
            return False
        try:
            return await asyncio.wait_for(
                self._clear_impl(), timeout=self.config.operation_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Cache clear timed out after "
                f"{self.config.operation_timeout}s; returning False"
            )
            return False

    async def _clear_impl(self) -> bool:
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
            # 关闭前必须把未刷的 stats 落盘；这里是 await 安全的 (close 本身是 async)。
            await self._persist_stats_async()
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
