"""
缓存模块 - 基于 SQLite 的请求缓存
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# JSON 序列化走 orjson (2026-09-15 MCP 二次审视 M5):
# - 比 stdlib json 快 3-5×, 无需中间 str, 直接返回 bytes
# - datetime/UUID 等类型原生支持 (option=OPT_NAIVE_UTC 等)
# - 不可用时降级 stdlib (如 pip install 失败的极小概率场景)
try:
    import orjson  # type: ignore[import-untyped]
    _HAS_ORJSON = True
except ImportError:
    orjson = None  # type: ignore[assignment]
    _HAS_ORJSON = False


def _dumps(value: Any) -> str:
    """序列化。orjson 返回 bytes, 解码成 str (sqlite TEXT 列兼容)。"""
    if _HAS_ORJSON:
        return orjson.dumps(value).decode("utf-8")
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value_str: str) -> Any:
    """反序列化。"""
    if _HAS_ORJSON:
        return orjson.loads(value_str)
    return json.loads(value_str)

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
        # LRU 簿记 (进程本地, 不持久化): key → 最近访问时间 (time.time() float)。
        # 热路径 O(1) dict 写, 零磁盘 I/O。evict 时与 SQL 的 created_at 合并:
        # _access_times 里有则用, 否则 fallback 到 created_at。进程重启
        # 后 _access_times 空, 行为平滑退化到 FIFO (跟 refactor 前一样)。
        self._access_times: dict[str, float] = {}

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

    def _stats_path(self) -> Path:
        """STATS_FILE 相对路径锚定**项目根** (Round-12: 与启动 CWD 无关,
        避免在 CWD 下另写 logs/cache_stats.json 与 cache.db 裂脑);
        绝对路径 (含测试对实例属性的覆盖) 原样返回。"""
        candidate = Path(self.STATS_FILE)
        if candidate.is_absolute():
            return candidate
        # 延迟 import: storage → core 保持软依赖
        from ..core.config_db import project_root
        return project_root() / candidate

    def _persist_stats_sync(self):
        # 同步版, 给 to_thread 调度或非 loop 上下文兜底。
        # 写盘失败只 warning, 不向 record_hit/miss 热路径抛 (无 loop 时
        # _maybe_persist_stats 直接同步调本方法)。
        try:
            stats_path = self._stats_path()
            stats_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = stats_path.with_name(stats_path.name + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                # updated_at/pid: 让控制台能识别文件是否来自当前网关进程
                # (重启后计数器从 0 开始, 旧文件可能被误当成实时值)。
                json.dump(
                    {
                        "hits": self._hits,
                        "misses": self._misses,
                        "updated_at": int(time.time()),
                        "pid": os.getpid(),
                    },
                    f,
                )
            tmp_path.replace(stats_path)
        except OSError as e:
            logger.warning(f"Failed to persist cache stats: {e}")
        # 无论成败都清零计数: 目录长期不可写时避免每个 hit 都重试打盘
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
                # MED-3 (2026-09-14 audit): created_at 索引让 cleanup_expired
                # 触发 LRU eviction 时的 SELECT key, created_at ... ORDER BY
                # created_at ASC LIMIT N 走索引 (免全表 scan+sort)。max_entries
                # 触发频率越高收益越大。
                await session.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_created_at ON cache(created_at)
                """))
                # WAL 模式: 解决 console (同步 sqlite3) 与 gateway (aiosqlite)
                # 同时读写同一 cache.db 时的 "database is locked" 问题。
                await session.execute(text("PRAGMA journal_mode=WAL"))
                await session.execute(text("PRAGMA synchronous=NORMAL"))
                # 性能优化: 页缓存 2MB，临时表放内存
                await session.execute(text("PRAGMA cache_size=-2000"))
                await session.execute(text("PRAGMA temp_store=MEMORY"))
                await session.commit()

            self._initialized = True
            logger.info(f"Cache initialized at {self.config.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize cache: {e}")
            self.enabled = False
            # L6 (round 9 audit): engine 已建但 CREATE TABLE/PRAGMA 失败时,
            # 旧实现只置 enabled=False, _engine 留挂着 → 同实例重试
            # initialize() 泄漏第二个 SQLAlchemy engine。dispose + 置 None。
            if self._engine is not None:
                try:
                    await asyncio.wait_for(self._engine.dispose(), timeout=2.0)
                except Exception as dispose_err:
                    logger.warning(f"cache engine dispose failed: {dispose_err}")
                self._engine = None
            self._session_factory = None

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
        # 读路径不持全局锁：aiosqlite 把所有语句投递到单连接线程串行执行，
        # 读不会与底层写并发交错；WAL 也允许读写并发。这里去掉锁让热点 key
        # 的并发读不再互相排队（热路径吞吐瓶颈）。LRU 簿记 _access_times 是
        # 普通 dict，asyncio 单线程下赋值天然原子，无需加锁。
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
                    # 惰性删除走独立短事务（持锁），避免与 LRU cleanup 的 DELETE 竞争
                    async with self._lock:
                        async with self._session_factory() as del_session:
                            await del_session.execute(
                                text("DELETE FROM cache WHERE key = :key"),
                                {"key": key}
                            )
                            await del_session.commit()
                    self._access_times.pop(key, None)
                    return None

                self._access_times[key] = time.time()
                return _loads(value_str)

        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """设置缓存值。 M1: 包 operation_timeout。

        Round-12 H2 纵深防御: ttl ≤ 0 (合同语义 = 不缓存) / bool / 非数值
        一律拒绝写入。旧实现 ``int(now + ttl) if ttl else None`` 会把 0
        落成 expires_at=NULL, 读侧 ``if expires_at and ...`` 永不判过期,
        该 key 沦为永久缓存。调用方 (_execute_cached) 已先行归一化, 这里
        保证任何其他调用路径也无法写出永久行。
        """
        if not self.enabled:
            return False
        if ttl is not None:
            # 延迟 import: storage 层不依赖 core 包加载顺序
            from ..core.cache_strategy import normalize_ttl
            try:
                ttl = normalize_ttl(ttl)
            except (TypeError, ValueError) as exc:
                logger.warning("Cache.set refused invalid ttl=%r for key=%s: %s", ttl, key, exc)
                return False
            if ttl is None:
                logger.debug("Cache.set skipped: ttl<=0 means no-cache for key=%s", key)
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
                    value_str = _dumps(value)
                    now = time.time()
                    expires_at = int(now + ttl) if ttl is not None else None

                    await session.execute(
                        text("""
                            INSERT OR REPLACE INTO cache (key, value, expires_at, created_at)
                            VALUES (:key, :value, :expires_at, :created_at)
                        """),
                        {
                            "key": key,
                            "value": value_str,
                            "expires_at": expires_at,
                            "created_at": now,
                        }
                    )
                    await session.commit()
                    # set 视为最近活动, 重置 LRU 时间戳
                    self._access_times[key] = now
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
                    self._access_times.pop(key, None)
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
                        # MED-3 (2026-09-14 audit): LIMIT N ORDER BY created_at
                        # 走 idx_created_at 索引, 不再 SELECT 全表。SQL 拿
                        # "created_at 升序的前 evict_n + buffer 行" 当候选
                        # (buffer=evict_n*0.5 防 _access_times 里某些行的
                        # 真实 LRU 时间比候选新, 把它们错杀), Python 侧用
                        # _access_times dict 合并后重新排序, 取最旧的
                        # evict_n 行。
                        buffer = max(evict_n // 2, 100)
                        fetch_n = evict_n + buffer
                        rows_result = await session.execute(
                            text(
                                "SELECT key, created_at FROM cache "
                                "ORDER BY created_at ASC LIMIT :n"
                            ),
                            {"n": fetch_n},
                        )
                        candidates = [
                            (self._access_times.get(key, created_at), key)
                            for key, created_at in rows_result.fetchall()
                        ]
                        candidates.sort()
                        evict_keys = [key for _, key in candidates[:evict_n]]
                        if evict_keys:
                            stmt = text(
                                "DELETE FROM cache WHERE key IN :keys"
                            ).bindparams(bindparam("keys", expanding=True))
                            await session.execute(
                                stmt, {"keys": evict_keys}
                            )
                            await session.commit()
                            for key in evict_keys:
                                self._access_times.pop(key, None)
                            deleted_total += len(evict_keys)
                            logger.warning(
                                f"Cache over max_entries ({count} > "
                                f"{max_entries}); LRU evicted "
                                f"{len(evict_keys)} least-recently-accessed "
                                f"entries"
                            )

                    if deleted_total > 0:
                        logger.info(f"Cleaned up {deleted_total} cache entries")

                    # WAL checkpoint (2026-09-15 MCP 二次审视 M3):
                    # PRAGMA wal_checkpoint(TRUNCATE) 把 WAL 内容写回主库并
                    # 截断 -wal 文件, 防长时间运行持续增长。每次清理 tick 跑
                    # 一次 (5 min 间隔) 开销可忽略。
                    try:
                        ckpt = await session.execute(
                            text("PRAGMA wal_checkpoint(TRUNCATE)")
                        )
                        # 返回 (busy, log_pages, checkpointed_pages)
                        row = ckpt.fetchone()
                        if row and row[1]:
                            logger.debug(
                                f"WAL checkpoint: {row[1]} pages -> "
                                f"{row[2]} checkpointed"
                            )
                    except Exception as e:
                        logger.debug(f"WAL checkpoint skipped: {e}")

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
                    self._access_times.clear()
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
