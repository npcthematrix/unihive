"""SQLite 配置薄覆盖层 (data/config.db)。

设计见 docs/design/2026-09-18-sqlite-config-overlay.md: YAML 是出厂基线,
本库只存"与出厂不同"的点路径覆盖, 例如::

    upstreams.fuyao.enabled = false
    fuyao_cache.tools.get_a_share_history = 60

表:

- ``config_overlay(path PRIMARY KEY, value_json, revision, updated_at)``
- ``config_meta(key PRIMARY KEY, value)`` — 全局整数 ``revision`` (乐观锁)
  与 ``schema_version``。

写入是事务化的: 一次 ``apply_changes`` 内的多条 upsert/delete 要么全成,
要么因 revision 冲突整笔回滚 (RevisionConflict)。每次实际变更把全局
revision +1 并写进受影响行; 无净变化 (值相同 / 删除不存在的行) 不 bump。

I/O 全部走 aiosqlite, 复用 storage/cache.py 的 WAL / busy_timeout 既有
约定 (busy_timeout=5000 在 DBAPI 层给锁等待兜底, 无需再包 wait_for)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_REVISION_KEY = "revision"

# 点路径: 非空段, 段内只允许标识符字符。路径同时是 SQLite 主键和
# config_merge 的下钻路径, 收紧字符集可以杜绝注入/误写深层结构。
_PATH_RE = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS config_overlay (
        path       TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        revision   INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
]

_UPSERT_SQL = text(
    """
    INSERT INTO config_overlay (path, value_json, revision, updated_at)
    VALUES (:path, :value_json, :revision, :updated_at)
    ON CONFLICT(path) DO UPDATE SET
        value_json = excluded.value_json,
        revision   = excluded.revision,
        updated_at = excluded.updated_at
    """
)
_GET_SQL = text(
    "SELECT path, value_json, revision, updated_at "
    "FROM config_overlay WHERE path = :path"
)
_LIST_SQL = text(
    "SELECT path, value_json, revision, updated_at "
    "FROM config_overlay WHERE path LIKE :prefix ESCAPE '\\' ORDER BY path"
)
_LIST_ALL_SQL = text(
    "SELECT path, value_json, revision, updated_at "
    "FROM config_overlay ORDER BY path"
)
_DELETE_SQL = text("DELETE FROM config_overlay WHERE path = :path")
_RESET_SQL = text("DELETE FROM config_overlay")
_META_GET_SQL = text("SELECT value FROM config_meta WHERE key = :key")
_META_SET_SQL = text(
    "INSERT INTO config_meta (key, value) VALUES (:key, :value) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)


class RevisionConflict(Exception):
    """乐观锁冲突: 调用方持有的 revision 已过期。"""

    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Config revision conflict: expected {expected}, actual {actual}"
        )


@dataclass(frozen=True)
class OverlayRow:
    """一行覆盖 (path 为点路径, value 为反序列化后的 JSON 值)。"""

    path: str
    value: Any
    revision: int
    updated_at: str


def validate_path(path: str) -> None:
    """校验点路径形态, 不合法抛 ValueError。"""
    if not isinstance(path, str) or not _PATH_RE.fullmatch(path):
        raise ValueError(
            f"Invalid config overlay path {path!r}: expected dot-separated "
            "identifier segments ([A-Za-z0-9_-]+)"
        )


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class ConfigStore:
    """配置覆盖层存储。一个 db 文件一个实例, 生命周期跟随网关。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._engine: Any = None
        self._session_factory: Any = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            await asyncio.to_thread(
                Path(self.db_path).parent.mkdir, parents=True, exist_ok=True
            )
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}",
                echo=False,
            )

            @event.listens_for(engine.sync_engine, "connect")
            def _set_pragmas(dbapi_conn: Any, _record: Any) -> None:
                cursor = dbapi_conn.cursor()
                try:
                    # WAL 持久属性只需设一次; busy_timeout/synchronous 是
                    # 连接级, NullPool 下每个新连接都要重申。
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA busy_timeout=5000")
                finally:
                    cursor.close()

            self._engine = engine
            self._session_factory = sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            async with self._session_factory() as session:
                for ddl in _DDL:
                    await session.execute(text(ddl))
                await session.execute(
                    _META_SET_SQL,
                    {"key": "schema_version", "value": str(SCHEMA_VERSION)},
                )
                await session.execute(
                    text(
                        "INSERT OR IGNORE INTO config_meta(key, value) "
                        "VALUES (:key, :value)"
                    ),
                    {"key": _REVISION_KEY, "value": "0"},
                )
                await session.commit()
            self._initialized = True
            logger.info("ConfigStore initialized at %s", self.db_path)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None
        self._initialized = False

    async def _session(self) -> AsyncSession:
        await self.initialize()
        return self._session_factory()

    async def revision(self) -> int:
        async with await self._session() as session:
            value = await session.execute(
                _META_GET_SQL, {"key": _REVISION_KEY}
            )
            return int(value.scalar_one())

    @staticmethod
    def _row(row: Any) -> OverlayRow:
        return OverlayRow(
            path=row[0],
            value=json.loads(row[1]),
            revision=int(row[2]),
            updated_at=row[3],
        )

    async def get_overlay(self, path: str) -> OverlayRow | None:
        validate_path(path)
        async with await self._session() as session:
            result = await session.execute(_GET_SQL, {"path": path})
            row = result.first()
            return self._row(row) if row is not None else None

    async def list_overlays(self, *, prefix: str | None = None) -> list[OverlayRow]:
        async with await self._session() as session:
            if prefix is None:
                result = await session.execute(_LIST_ALL_SQL)
            else:
                if not prefix:
                    raise ValueError("prefix must be non-empty or None")
                # 段边界由调用方传 '.xxx.' 形式保证; 转义 LIKE 元字符
                # (前缀里的 _ 是单字符通配, 不转义会误匹配)。
                escaped = (
                    prefix.replace("\\", r"\\")
                    .replace("%", r"\%")
                    .replace("_", r"\_")
                )
                result = await session.execute(
                    _LIST_SQL, {"prefix": escaped + "%"}
                )
            return [self._row(r) for r in result.all()]

    async def upsert_overlay(
        self,
        path: str,
        value: Any,
        *,
        expected_revision: int | None = None,
    ) -> int:
        return await self.apply_changes({path: value}, (), expected_revision)

    async def delete_overlay(
        self,
        path: str,
        *,
        expected_revision: int | None = None,
    ) -> int:
        return await self.apply_changes({}, (path,), expected_revision)

    async def reset(self, *, expected_revision: int | None = None) -> int:
        """清空全部覆盖 (恢复出厂), 返回新 revision。"""
        async with await self._session() as session:
            current = await self._current_revision(session)
            self._check_expected(expected_revision, current)
            result = await session.execute(_RESET_SQL)
            changed = (result.rowcount or 0) > 0
            new_revision = current
            if changed:
                new_revision = current + 1
                await self._set_revision(session, new_revision)
            await session.commit()
            return new_revision

    async def apply_changes(
        self,
        upserts: dict[str, Any],
        deletes: Iterable[str] = (),
        expected_revision: int | None = None,
    ) -> int:
        """事务化批量变更, 返回 (可能 bump 后的) 新全局 revision。

        - 与现值 JSON 相同的 upsert 不产生变更, 不 bump;
        - 删除不存在的 path 不 bump;
        - expected_revision 非 None 且与当前不符 → RevisionConflict, 整笔回滚。
        """
        for path in upserts:
            validate_path(path)
        delete_paths = list(deletes)
        for path in delete_paths:
            validate_path(path)

        async with await self._session() as session:
            current = await self._current_revision(session)
            self._check_expected(expected_revision, current)

            changed = False
            # 先解析最终是否有净变化, 再决定 revision; 但行 SQL 可以先执行,
            # 冲突/回滚时随事务一起撤销。
            for path, value in upserts.items():
                payload = _dumps(value)
                existing = await session.execute(_GET_SQL, {"path": path})
                row = existing.first()
                if row is not None and row[1] == payload:
                    continue
                await session.execute(
                    _UPSERT_SQL,
                    {
                        "path": path,
                        "value_json": payload,
                        # 占位, 提交前按最终 new_revision 统一改写
                        "revision": current + 1,
                        "updated_at": _utc_now(),
                    },
                )
                changed = True

            for path in delete_paths:
                result = await session.execute(_DELETE_SQL, {"path": path})
                if (result.rowcount or 0) > 0:
                    changed = True

            if not changed:
                await session.commit()
                return current

            new_revision = current + 1
            await self._set_revision(session, new_revision)
            # 行内 revision 统一记为本次 revision (current+1 已一致,
            # 这里仅为语义显式)。
            await session.commit()
            return new_revision

    @staticmethod
    async def _current_revision(session: AsyncSession) -> int:
        result = await session.execute(
            _META_GET_SQL, {"key": _REVISION_KEY}
        )
        return int(result.scalar_one())

    @staticmethod
    async def _set_revision(session: AsyncSession, revision: int) -> None:
        await session.execute(
            _META_SET_SQL, {"key": _REVISION_KEY, "value": str(revision)}
        )

    @staticmethod
    def _check_expected(expected: int | None, current: int) -> None:
        if expected is not None and expected != current:
            raise RevisionConflict(expected=expected, actual=current)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
