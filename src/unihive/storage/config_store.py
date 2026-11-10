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
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ..core.entity_edit import apply_tool_patch, validate_upstream_config
from ..core.template_normalize import find_plaintext_secrets_in

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
_REVISION_KEY = "revision"
_TEMPLATE_VERSION_KEY = "template_version"
_SEEDED_AT_KEY = "seeded_at"

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
    # --- schema v2: 配置全量入库 (实体行 + JSON 列混合) ---
    """
    CREATE TABLE IF NOT EXISTS config_template (
        version      TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        imported_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config_section (
        section    TEXT PRIMARY KEY,
        doc_json    TEXT NOT NULL,
        origin      TEXT NOT NULL DEFAULT 'builtin',
        updated_at  TEXT NOT NULL,
        revision    INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS upstream (
        name        TEXT PRIMARY KEY,
        type        TEXT NOT NULL,
        enabled     INTEGER NOT NULL,
        position    INTEGER NOT NULL,
        config_json TEXT NOT NULL,
        origin      TEXT NOT NULL DEFAULT 'builtin',
        updated_at  TEXT NOT NULL,
        revision    INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool (
        name          TEXT PRIMARY KEY,
        routing       TEXT NOT NULL,
        description   TEXT NOT NULL DEFAULT '',
        cache_ttl_key TEXT,
        requires_env  TEXT,
        publish_mode  TEXT NOT NULL DEFAULT 'default',
        mapping_json  TEXT NOT NULL DEFAULT '{}',
        spec_json     TEXT NOT NULL,
        source_file   TEXT,
        position      INTEGER NOT NULL,
        origin        TEXT NOT NULL DEFAULT 'builtin',
        updated_at    TEXT NOT NULL,
        revision      INTEGER NOT NULL
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


class AlreadySeededError(RuntimeError):
    """config.db 已有实体行, 拒绝非 force 的重复 seed。"""


# --- v2 实体 SQL ---
_TEMPLATE_GET_SQL = text(
    "SELECT version, payload_json FROM config_template ORDER BY imported_at DESC LIMIT 1"
)
_TEMPLATE_UPSERT_SQL = text(
    """
    INSERT INTO config_template (version, payload_json, imported_at)
    VALUES (:version, :payload_json, :imported_at)
    ON CONFLICT(version) DO UPDATE SET
        payload_json = excluded.payload_json,
        imported_at = excluded.imported_at
    """
)
_TEMPLATE_DELETE_SQL = text("DELETE FROM config_template")
_ENTITY_COUNT_SQL = text(
    "SELECT (SELECT COUNT(*) FROM upstream) + (SELECT COUNT(*) FROM tool) "
    "+ (SELECT COUNT(*) FROM config_section)"
)
_SECTION_UPSERT_SQL = text(
    """
    INSERT INTO config_section (section, doc_json, origin, updated_at, revision)
    VALUES (:section, :doc_json, :origin, :updated_at, :revision)
    ON CONFLICT(section) DO UPDATE SET
        doc_json = excluded.doc_json,
        origin = excluded.origin,
        updated_at = excluded.updated_at,
        revision = excluded.revision
    """
)
_SECTION_GET_SQL = text("SELECT doc_json FROM config_section WHERE section = :section")
_UPSTREAM_GET_SQL = text("SELECT enabled FROM upstream WHERE name = :name")
_UPSTREAM_SET_ENABLED_SQL = text(
    "UPDATE upstream SET enabled = :enabled, updated_at = :updated_at, revision = :revision "
    "WHERE name = :name"
)
_BUNDLE_SECTIONS_SQL = text("SELECT section, doc_json FROM config_section ORDER BY section")
_BUNDLE_UPSTREAMS_SQL = text(
    "SELECT name, type, enabled, config_json, origin FROM upstream ORDER BY position"
)
_BUNDLE_TOOLS_SQL = text(
    "SELECT name, routing, description, cache_ttl_key, requires_env, publish_mode, "
    "mapping_json, spec_json, source_file, origin FROM tool ORDER BY position"
)
# 只读实体浏览 (控制台「配置库」面板): 只要元数据, 不读 config_json/spec_json 大列
_ENTITIES_TEMPLATE_SQL = text(
    "SELECT version, imported_at FROM config_template ORDER BY imported_at DESC LIMIT 1"
)
_ENTITIES_UPSTREAMS_SQL = text(
    "SELECT name, type, enabled, origin, updated_at FROM upstream ORDER BY position"
)
_ENTITIES_SECTIONS_SQL = text(
    "SELECT section, origin, updated_at FROM config_section ORDER BY section"
)
_ENTITIES_TOOLS_BY_ORIGIN_SQL = text(
    "SELECT origin, COUNT(*) FROM tool GROUP BY origin"
)
_ENTITIES_TOOLS_TOTAL_SQL = text("SELECT COUNT(*) FROM tool")

# --- P3: 单实体读详情 / 控制台编辑写 ---
_TOOL_GET_FULL_SQL = text(
    "SELECT name, routing, description, cache_ttl_key, requires_env, publish_mode, "
    "mapping_json, spec_json, source_file, position, origin, updated_at "
    "FROM tool WHERE name = :name"
)
_TOOL_NAMES_SQL = text("SELECT name FROM tool")
_TOOL_UPDATE_SQL = text(
    "UPDATE tool SET routing = :routing, description = :description, "
    "cache_ttl_key = :cache_ttl_key, requires_env = :requires_env, "
    "publish_mode = :publish_mode, mapping_json = :mapping_json, spec_json = :spec_json, "
    "origin = 'user', updated_at = :updated_at, revision = :revision WHERE name = :name"
)
_TOOL_INSERT_SQL = text(
    "INSERT INTO tool (name, routing, description, cache_ttl_key, requires_env, "
    "publish_mode, mapping_json, spec_json, source_file, position, origin, "
    "updated_at, revision) VALUES (:name, :routing, :description, :cache_ttl_key, "
    ":requires_env, :publish_mode, :mapping_json, :spec_json, :source_file, "
    ":position, 'user', :updated_at, :revision)"
)
_TOOL_INSERT_OR_REPLACE_SQL = text(
    """
    INSERT INTO tool (name, routing, description, cache_ttl_key, requires_env,
    publish_mode, mapping_json, spec_json, source_file, position, origin,
    updated_at, revision) VALUES (:name, :routing, :description, :cache_ttl_key,
    :requires_env, :publish_mode, :mapping_json, :spec_json, :source_file,
    :position, 'user', :updated_at, :revision)
    ON CONFLICT(name) DO UPDATE SET
        routing = excluded.routing,
        description = excluded.description,
        cache_ttl_key = excluded.cache_ttl_key,
        requires_env = excluded.requires_env,
        publish_mode = excluded.publish_mode,
        mapping_json = excluded.mapping_json,
        spec_json = excluded.spec_json,
        source_file = excluded.source_file,
        origin = 'user',
        updated_at = excluded.updated_at,
        revision = excluded.revision
    """
)
_TOOL_MAX_POSITION_SQL = text("SELECT COALESCE(MAX(position), -1) FROM tool")
_TOOL_DELETE_SQL = text("DELETE FROM tool WHERE name = :name")
_UPSTREAM_GET_FULL_SQL = text(
    "SELECT name, type, enabled, config_json, origin, updated_at "
    "FROM upstream WHERE name = :name"
)
_UPSTREAM_UPDATE_CONFIG_SQL = text(
    "UPDATE upstream SET type = :type, config_json = :config_json, origin = 'user', "
    "updated_at = :updated_at, revision = :revision WHERE name = :name"
)
_SECTION_GET_FULL_SQL = text(
    "SELECT section, doc_json, origin, updated_at "
    "FROM config_section WHERE section = :section"
)
_CLEAR_ENTITY_SQL = [
    text("DELETE FROM tool"),
    text("DELETE FROM upstream"),
    text("DELETE FROM config_section"),
]


class RevisionConflict(Exception):
    """乐观锁冲突: 调用方持有的 revision 已过期。"""

    def __init__(self, expected: int, actual: int):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"config revision 不一致: 页面持有的 revision={expected}, "
            f"库内当前 revision={actual}; 请刷新页面后重试"
        )


def _raise_if_plaintext_secrets(node: Any, path_prefix: str) -> None:
    """实体写入门的明文密钥 fail-closed 守卫 (与 update_upstream_config 同构)。

    只有 ``${VAR}`` 占位符允许出现在疑似密钥键下; 发现明文串直接 ValueError,
    调用方必须在任何 DML 之前调用, 保证泄露内容绝不落库。
    """
    leaked = find_plaintext_secrets_in(node, path_prefix)
    if leaked:
        raise ValueError(
            "Refusing to write plaintext secret field(s): " + ", ".join(leaked)
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


def _spec_with_gate_columns(
    spec: dict[str, Any], cache_ttl_key: str | None, requires_env: str | None
) -> dict[str, Any]:
    """返回 spec 的浅拷贝, 把抽取门控列回写/摘除 (不 mutate 入参)。

    与 core.config_upgrade / entity_edit 的 spec 同步同语义: 抽取列非空时
    spec 内必须带同值键; None 时摘除, 保证 spec ↔ 抽取列恒一致。
    """
    synced = dict(spec)
    for key, value in (("cache_ttl_key", cache_ttl_key),
                       ("requires_env", requires_env)):
        if value is None:
            synced.pop(key, None)
        else:
            synced[key] = value
    return synced


class ConfigStore:
    """配置覆盖层存储。一个 db 文件一个实例, 生命周期跟随网关。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._engine: Any = None
        self._session_factory: Any = None
        self._lock = asyncio.Lock()
        # 进程内写互斥: 协程级串行化, 避免同进程写者去吃 busy_timeout;
        # 跨进程/跨引擎由 BEGIN IMMEDIATE + busy_timeout PRAGMA 兜底。
        self._write_lock = asyncio.Lock()
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
            # isolation_level=None: 关闭驱动隐式事务管理, 由本类显式发
            # BEGIN / BEGIN IMMEDIATE。否则 sqlite3 旧版 deferred 语义下
            # SELECT 不开事务, 乐观锁的 read-revision → Python 比对 → DML
            # 序列在并发下非原子 (revision 折叠/丢更新)。
            engine = create_async_engine(
                f"sqlite+aiosqlite:///{Path(self.db_path).as_posix()}",
                echo=False,
                isolation_level=None,
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
                await session.execute(text("BEGIN IMMEDIATE"))
                try:
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
                except Exception:
                    await session.rollback()
                    raise
            self._initialized = True
            logger.info("ConfigStore initialized at %s", self.db_path)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
        self._engine = None
        self._session_factory = None
        self._initialized = False

    def _open_session(self) -> AsyncSession:
        """直接开 session, 不触发 initialize (initialize 内部专用)。"""
        return self._session_factory()

    async def _session(self) -> AsyncSession:
        await self.initialize()
        return self._open_session()

    @asynccontextmanager
    async def _txn(self, *, immediate: bool = False):
        """显式事务边界。

        - 读事务 (``immediate=False``): 单 ``BEGIN`` 快照, 多条 SELECT
          读到同一版本, 杜绝跨语句撕裂读 (M4);
        - 写事务 (``immediate=True``): 进程内 ``_write_lock`` 串行化 +
          ``BEGIN IMMEDIATE`` 立即取 RESERVED 锁; 调用方必须在事务内重读
          revision 再比对 expected, 使乐观锁检查与写入原子化 (H1/H5)。

        正常退出统一 commit, 异常 rollback; 调用方不再自行 commit。

        L2 (round 9 audit): ``except Exception`` 接不住 ``CancelledError``
        (BaseException)。手工 ``BEGIN IMMEDIATE`` 持有的 RESERVED 写锁在
        取消时会随连接还池滞留, 短期瘫痪 config.db 写路径。改 ``BaseException``
        保证取消也 rollback → 释放锁 → 重抛。
        """
        if immediate:
            async with self._write_lock:
                async with await self._session() as session:
                    await session.execute(text("BEGIN IMMEDIATE"))
                    try:
                        yield session
                        await session.commit()
                    except BaseException:
                        await session.rollback()
                        raise
        else:
            async with await self._session() as session:
                await session.execute(text("BEGIN"))
                try:
                    yield session
                    await session.commit()
                except BaseException:
                    await session.rollback()
                    raise

    async def revision(self) -> int:
        async with self._txn() as session:
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
        async with self._txn() as session:
            result = await session.execute(_GET_SQL, {"path": path})
            row = result.first()
            return self._row(row) if row is not None else None

    async def list_overlays(self, *, prefix: str | None = None) -> list[OverlayRow]:
        async with self._txn() as session:
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
        async with self._txn(immediate=True) as session:
            current = await self._current_revision(session)
            self._check_expected(expected_revision, current)
            result = await session.execute(_RESET_SQL)
            changed = (result.rowcount or 0) > 0
            new_revision = current
            if changed:
                new_revision = current + 1
                await self._set_revision(session, new_revision)
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

        async with self._txn(immediate=True) as session:
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
                return current

            new_revision = current + 1
            await self._set_revision(session, new_revision)
            # 行内 revision 统一记为本次 revision (current+1 已一致,
            # 这里仅为语义显式)。
            return new_revision

    # ------------------------------------------------------------------
    # v2: 全量配置实体 (sections / upstreams / tools + 出厂模板快照)
    # ------------------------------------------------------------------

    async def is_seeded(self) -> bool:
        """config.db 是否已 seed 过实体 (以 template 行/实体行为准)。"""
        async with self._txn() as session:
            total = await session.execute(_ENTITY_COUNT_SQL)
            return int(total.scalar_one()) > 0

    async def seed_from_bundle(
        self,
        version: str,
        bundle: dict[str, Any],
        *,
        force: bool = False,
        expected_revision: int | None = None,
    ) -> int:
        """把归一化后的出厂模板实体全量写入空库 (单事务)。

        - 空库首 seed: revision 保持 0;
        - force=True 重灌: 清空实体后重写, revision +1;
        - 非空库且未 force: :class:`AlreadySeededError`;
        - 同时写 config_template 快照 (P3 三方合并的 old 基准) 与 meta。
        bundle 形态见 NormalizedTemplate.to_json()。
        """
        async with self._txn(immediate=True) as session:
            current = await self._current_revision(session)
            self._check_expected(expected_revision, current)
            existing = int((await session.execute(_ENTITY_COUNT_SQL)).scalar_one())
            if existing and not force:
                raise AlreadySeededError(
                    "config.db already contains config entities; use force=True to reseed"
                )
            if existing:
                for stmt in _CLEAR_ENTITY_SQL:
                    await session.execute(stmt)
            await session.execute(_TEMPLATE_DELETE_SQL)

            now = _utc_now()
            await session.execute(
                _TEMPLATE_UPSERT_SQL,
                {"version": version, "payload_json": _dumps(bundle), "imported_at": now},
            )
            for name, doc in (bundle.get("sections") or {}).items():
                await session.execute(
                    _SECTION_UPSERT_SQL,
                    {
                        "section": name,
                        "doc_json": _dumps(doc),
                        "origin": "builtin",
                        "updated_at": now,
                        "revision": current,
                    },
                )
            for position, upstream in enumerate(bundle.get("upstreams") or []):
                await session.execute(
                    text(
                        "INSERT INTO upstream (name, type, enabled, position, config_json, "
                        "origin, updated_at, revision) VALUES (:name, :type, :enabled, "
                        ":position, :config_json, :origin, :updated_at, :revision)"
                    ),
                    {
                        "name": upstream["name"],
                        "type": upstream["type"],
                        "enabled": 1 if upstream.get("enabled") else 0,
                        "position": position,
                        "config_json": _dumps(upstream.get("config", {})),
                        "origin": upstream.get("origin", "builtin"),
                        "updated_at": now,
                        "revision": current,
                    },
                )
            for position, tool in enumerate(bundle.get("tools") or []):
                await session.execute(
                    text(
                        "INSERT INTO tool (name, routing, description, cache_ttl_key, "
                        "requires_env, publish_mode, mapping_json, spec_json, source_file, "
                        "position, origin, updated_at, revision) VALUES (:name, :routing, "
                        ":description, :cache_ttl_key, :requires_env, :publish_mode, "
                        ":mapping_json, :spec_json, :source_file, :position, :origin, "
                        ":updated_at, :revision)"
                    ),
                    {
                        "name": tool["name"],
                        "routing": tool["routing"],
                        "description": tool.get("description", ""),
                        "cache_ttl_key": tool.get("cache_ttl_key"),
                        "requires_env": tool.get("requires_env"),
                        "publish_mode": tool.get("publish_mode", "default"),
                        "mapping_json": _dumps(tool.get("mapping", {})),
                        "spec_json": _dumps(tool["spec"]),
                        "source_file": tool.get("source_file"),
                        "position": position,
                        "origin": tool.get("origin", "builtin"),
                        "updated_at": now,
                        "revision": current,
                    },
                )

            await session.execute(
                _META_SET_SQL, {"key": _TEMPLATE_VERSION_KEY, "value": version}
            )
            await session.execute(_META_SET_SQL, {"key": _SEEDED_AT_KEY, "value": now})
            new_revision = current + 1 if existing else current
            if new_revision != current:
                await self._set_revision(session, new_revision)
            return new_revision

    async def apply_upgrade_bundle(
        self,
        new_version: str,
        new_bundle: dict[str, Any],
        merged_bundle: dict[str, Any],
        *,
        section_origins: dict[str, str] | None = None,
        expected_revision: int | None = None,
    ) -> int:
        """出厂更新三方合并后的全量替换 (单事务), 返回新 revision。

        - ``new_bundle``: 最新出厂 YAML 重建的模板 bundle, 写进
          config_template 快照 —— 它是下次三方合并的 old 基准 (基准前推);
        - ``merged_bundle``: 合并结果 bundle (保留用户改动), 全量重写
          section/upstream/tool 实体行, 行 origin 取 bundle 内 origin 列
          (section 行由 ``section_origins`` 提供, 缺省 builtin);
        - 仅允许在已 seed 的库上执行 (空库走 seed_from_bundle);
        - 总是 revision +1 并刷新 template_version / seeded_at (快照前推
          本身就是一次配置事实变化); revision 不符抛 RevisionConflict,
          整笔回滚, 实体/快照均不动。
        """
        section_origins = section_origins or {}
        async with self._txn(immediate=True) as session:
            current = await self._current_revision(session)
            self._check_expected(expected_revision, current)
            existing = int((await session.execute(_ENTITY_COUNT_SQL)).scalar_one())
            if not existing:
                raise RuntimeError(
                    "config.db has no seeded entities; run seed_from_bundle first"
                )

            for stmt in _CLEAR_ENTITY_SQL:
                await session.execute(stmt)
            await session.execute(_TEMPLATE_DELETE_SQL)

            now, new_revision = _utc_now(), current + 1
            await session.execute(
                _TEMPLATE_UPSERT_SQL,
                {
                    "version": new_version,
                    "payload_json": _dumps(new_bundle),
                    "imported_at": now,
                },
            )
            for name, doc in (merged_bundle.get("sections") or {}).items():
                await session.execute(
                    _SECTION_UPSERT_SQL,
                    {
                        "section": name,
                        "doc_json": _dumps(doc),
                        "origin": section_origins.get(name, "builtin"),
                        "updated_at": now,
                        "revision": new_revision,
                    },
                )
            for position, upstream in enumerate(merged_bundle.get("upstreams") or []):
                await session.execute(
                    text(
                        "INSERT INTO upstream (name, type, enabled, position, config_json, "
                        "origin, updated_at, revision) VALUES (:name, :type, :enabled, "
                        ":position, :config_json, :origin, :updated_at, :revision)"
                    ),
                    {
                        "name": upstream["name"],
                        "type": upstream["type"],
                        "enabled": 1 if upstream.get("enabled") else 0,
                        "position": position,
                        "config_json": _dumps(upstream.get("config", {})),
                        "origin": upstream.get("origin", "builtin"),
                        "updated_at": now,
                        "revision": new_revision,
                    },
                )
            for position, tool in enumerate(merged_bundle.get("tools") or []):
                await session.execute(
                    text(
                        "INSERT INTO tool (name, routing, description, cache_ttl_key, "
                        "requires_env, publish_mode, mapping_json, spec_json, source_file, "
                        "position, origin, updated_at, revision) VALUES (:name, :routing, "
                        ":description, :cache_ttl_key, :requires_env, :publish_mode, "
                        ":mapping_json, :spec_json, :source_file, :position, :origin, "
                        ":updated_at, :revision)"
                    ),
                    {
                        "name": tool["name"],
                        "routing": tool["routing"],
                        "description": tool.get("description", ""),
                        "cache_ttl_key": tool.get("cache_ttl_key"),
                        "requires_env": tool.get("requires_env"),
                        "publish_mode": tool.get("publish_mode", "default"),
                        "mapping_json": _dumps(tool.get("mapping", {})),
                        "spec_json": _dumps(tool["spec"]),
                        "source_file": tool.get("source_file"),
                        "position": position,
                        "origin": tool.get("origin", "builtin"),
                        "updated_at": now,
                        "revision": new_revision,
                    },
                )

            await session.execute(
                _META_SET_SQL, {"key": _TEMPLATE_VERSION_KEY, "value": new_version}
            )
            await session.execute(_META_SET_SQL, {"key": _SEEDED_AT_KEY, "value": now})
            await self._set_revision(session, new_revision)
            return new_revision

    async def template_snapshot(self) -> tuple[str, dict[str, Any]] | None:
        """返回最近一次 seed 的 (版本 hash, 模板 bundle), 未 seed 返回 None。"""
        async with self._txn() as session:
            row = (await session.execute(_TEMPLATE_GET_SQL)).first()
            if row is None:
                return None
            return row[0], json.loads(row[1])

    async def template_version_seeded(self) -> str | None:
        async with self._txn() as session:
            result = await session.execute(
                _META_GET_SQL, {"key": _TEMPLATE_VERSION_KEY}
            )
            value = result.scalar_one_or_none()
            return value

    async def load_bundle(self) -> dict[str, Any]:
        """从实体表读出当前 bundle (sections/upstreams/tools, 按 seed 顺序)。"""
        bundle, _ = await self.read_bundle()
        return bundle

    async def read_bundle(self) -> tuple[dict[str, Any], int]:
        """同快照读 ``(bundle, revision)``。

        三类实体 SELECT 与 revision SELECT 在同一只读事务内完成 (M4 防撕裂);
        plan/preview 用返回的 revision 作为 apply 时的乐观锁基准, 避免
        "bundle 与 revision 分两次读, 并发写插在缝里" 的窗口 (H5)。
        """
        async with self._txn() as session:
            revision = await self._current_revision(session)
            sections = {
                row[0]: json.loads(row[1])
                for row in (await session.execute(_BUNDLE_SECTIONS_SQL)).all()
            }
            upstreams = [
                {
                    "name": row[0],
                    "type": row[1],
                    "enabled": bool(row[2]),
                    "config": json.loads(row[3]),
                    "origin": row[4],
                }
                for row in (await session.execute(_BUNDLE_UPSTREAMS_SQL)).all()
            ]
            tools = [
                {
                    "name": row[0],
                    "routing": row[1],
                    "description": row[2],
                    "cache_ttl_key": row[3],
                    "requires_env": row[4],
                    "publish_mode": row[5],
                    "mapping": json.loads(row[6]),
                    "spec": json.loads(row[7]),
                    "source_file": row[8],
                    "origin": row[9],
                }
                for row in (await session.execute(_BUNDLE_TOOLS_SQL)).all()
            ]
        return {"sections": sections, "upstreams": upstreams, "tools": tools}, revision

    async def entities_summary(self) -> dict[str, Any]:
        """只读实体浏览摘要 (控制台「配置库」面板数据源)。

        只取实体行元数据 (origin / updated_at), 不读 config_json / spec_json
        大列; 工具行只回按 origin 聚合计数。
        """
        async with self._txn() as session:
            revision = int(
                (
                    await session.execute(
                        _META_GET_SQL, {"key": _REVISION_KEY}
                    )
                ).scalar_one()
            )
            template = (await session.execute(_ENTITIES_TEMPLATE_SQL)).first()
            upstreams = [
                {
                    "name": row[0],
                    "type": row[1],
                    "enabled": bool(row[2]),
                    "origin": row[3],
                    "updated_at": row[4],
                }
                for row in (await session.execute(_ENTITIES_UPSTREAMS_SQL)).all()
            ]
            sections = [
                {"section": row[0], "origin": row[1], "updated_at": row[2]}
                for row in (await session.execute(_ENTITIES_SECTIONS_SQL)).all()
            ]
            tools_by_origin = {
                row[0]: int(row[1])
                for row in (await session.execute(_ENTITIES_TOOLS_BY_ORIGIN_SQL)).all()
            }
            tools_total = int(
                (await session.execute(_ENTITIES_TOOLS_TOTAL_SQL)).scalar_one()
            )
        return {
            "revision": revision,
            "template_version": template[0] if template is not None else None,
            "template_imported_at": template[1] if template is not None else None,
            "upstreams": upstreams,
            "sections": sections,
            "tools": {"total": tools_total, "by_origin": tools_by_origin},
        }

    async def update_upstream_enabled(
        self,
        name: str,
        enabled: bool,
        *,
        expected_revision: int | None = None,
    ) -> int:
        """更新上游启用位; 同值不 bump, 未知上游 KeyError。返回 (可能 bump 后的) revision。"""
        async with self._txn(immediate=True) as session:
            current = await self._current_revision(session)
            self._check_expected(expected_revision, current)
            row = (await session.execute(_UPSTREAM_GET_SQL, {"name": name})).first()
            if row is None:
                raise KeyError(name)
            if bool(row[0]) == bool(enabled):
                return current
            await session.execute(
                _UPSTREAM_SET_ENABLED_SQL,
                {
                    "name": name,
                    "enabled": 1 if enabled else 0,
                    "updated_at": _utc_now(),
                    "revision": current + 1,
                },
            )
            await self._set_revision(session, current + 1)
            return current + 1

    async def upsert_section(
        self,
        section: str,
        doc: Any,
        *,
        origin: str = "builtin",
        expected_revision: int | None = None,
    ) -> int:
        """写入/替换一个 config_section 段; 内容相同不 bump。"""
        if not isinstance(section, str) or not section:
            raise ValueError("section must be a non-empty string")
        # H6: 段内容是任意 JSON, 与 seed 全量扫描同策略 fail-closed 明文密钥。
        _raise_if_plaintext_secrets(doc, f"sections.{section}")
        payload = _dumps(doc)
        async with self._txn(immediate=True) as session:
            current = await self._current_revision(session)
            self._check_expected(expected_revision, current)
            existing = (
                await session.execute(_SECTION_GET_SQL, {"section": section})
            ).scalar_one_or_none()
            if existing == payload:
                return current
            await session.execute(
                _SECTION_UPSERT_SQL,
                {
                    "section": section,
                    "doc_json": payload,
                    "origin": origin,
                    "updated_at": _utc_now(),
                    "revision": current + 1,
                },
            )
            await self._set_revision(session, current + 1)
            return current + 1

    # ------------------------------------------------------------------
    # P3: 单实体详情读 + 控制台编辑写 (origin 翻转 user, spec/列同步)
    # ------------------------------------------------------------------

    async def get_tool(self, name: str) -> dict[str, Any] | None:
        """读单个工具实体 (完整 spec + 抽取列 + 审计列); 不存在返回 None。"""
        async with self._txn() as session:
            row = (
                await session.execute(_TOOL_GET_FULL_SQL, {"name": name})
            ).first()
        if row is None:
            return None
        return {
            "name": row[0],
            "routing": row[1],
            "description": row[2],
            "cache_ttl_key": row[3],
            "requires_env": row[4],
            "publish_mode": row[5],
            "mapping": json.loads(row[6]),
            "spec": json.loads(row[7]),
            "source_file": row[8],
            "position": row[9],
            "origin": row[10],
            "updated_at": row[11],
        }

    async def get_upstream(self, name: str) -> dict[str, Any] | None:
        """读单个上游实体 (完整异型连接块 + 审计列); 不存在返回 None。"""
        async with self._txn() as session:
            row = (
                await session.execute(_UPSTREAM_GET_FULL_SQL, {"name": name})
            ).first()
        if row is None:
            return None
        return {
            "name": row[0],
            "type": row[1],
            "enabled": bool(row[2]),
            "config": json.loads(row[3]),
            "origin": row[4],
            "updated_at": row[5],
        }

    async def get_section(self, section: str) -> dict[str, Any] | None:
        """读单个配置段实体; 不存在返回 None。"""
        async with self._txn() as session:
            row = (
                await session.execute(_SECTION_GET_FULL_SQL, {"section": section})
            ).first()
        if row is None:
            return None
        return {
            "section": row[0],
            "doc": json.loads(row[1]),
            "origin": row[2],
            "updated_at": row[3],
        }

    async def update_tool(
        self,
        name: str,
        patch: dict[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> tuple[str, int]:
        """白名单 patch 更新工具实体, 返回 ``(最终工具名, revision)``。

        - 字段校验/分层/抽取列与 spec 同步见 :func:`entity_edit.apply_tool_patch`;
        - 任何实际变更把行 ``origin`` 翻为 ``user`` 并 bump 全局 revision;
        - 净效果与现值相同 (含改名回同名) 不 bump、不翻 origin;
        - 改名 = 同事务内删旧行/插新行, position 与审计来源保留;
        - 未知工具 ``KeyError``; 过期 revision :class:`RevisionConflict`;
          非法 patch :class:`ValueError` (EntityEditError), 均在写前抛出。
        """
        async with self._txn(immediate=True) as session:
            current = await self._current_revision(session)
            self._check_expected(expected_revision, current)

            row = (await session.execute(_TOOL_GET_FULL_SQL, {"name": name})).first()
            if row is None:
                raise KeyError(name)
            entity = {
                "name": row[0],
                "routing": row[1],
                "description": row[2],
                "cache_ttl_key": row[3],
                "requires_env": row[4],
                "publish_mode": row[5],
                "mapping": json.loads(row[6]),
                "spec": json.loads(row[7]),
                "source_file": row[8],
                "position": row[9],
                "origin": row[10],
            }
            existing_names = {
                r[0] for r in (await session.execute(_TOOL_NAMES_SQL)).all()
            }
            updated = apply_tool_patch(
                entity, patch, existing_names=existing_names
            )

            # H6: 结果 spec (含行内既有键 + 本次 patch 内容) 不得含明文密钥;
            # 存量脏行也借任何一次编辑 fail-closed, 绝不重新落盘。
            _raise_if_plaintext_secrets(
                updated["spec"], f"tools.{updated['name']}.spec"
            )

            if self._tool_entity_unchanged(entity, updated):
                return entity["name"], current

            now, new_revision = _utc_now(), current + 1
            params = {
                "name": updated["name"],
                "routing": updated["routing"],
                "description": updated["description"],
                "cache_ttl_key": updated["cache_ttl_key"],
                "requires_env": updated["requires_env"],
                "publish_mode": updated["publish_mode"],
                "mapping_json": _dumps(updated["mapping"]),
                "spec_json": _dumps(updated["spec"]),
                "source_file": updated.get("source_file"),
                "position": updated.get("position", entity["position"]),
                "updated_at": now,
                "revision": new_revision,
            }
            if updated["name"] == entity["name"]:
                await session.execute(_TOOL_UPDATE_SQL, params)
            else:
                await session.execute(_TOOL_DELETE_SQL, {"name": entity["name"]})
                await session.execute(_TOOL_INSERT_SQL, params)
            await self._set_revision(session, new_revision)
            return updated["name"], new_revision

    async def upsert_tools(
        self,
        entities: list[dict[str, Any]],
        *,
        expected_revision: int | None = None,
    ) -> int:
        """批量写入 ``origin='user'`` 工具实体 (OpenAPI 实时导入专用)。

        单事务, 全批最多 bump 一次全局 revision; expected_revision 过期抛
        :class:`RevisionConflict`, 整笔回滚。

        - **新工具**: position 从当前 ``max(position)+1`` 起按入参顺序追加,
          门控列取实体自带值 (导入实体恒为 None/None/'default');
        - **已存在**: 只刷契约列 (routing/description/mapping/spec/
          source_file), **position 保持, 本地门控列
          (cache_ttl_key/requires_env/publish_mode) 恒保留现值** ——
          OpenAPI 导入只改契约, 用户的 hidden/TTL/env 门控不能被刷新冲掉;
        - **origin 恒写 'user'**, 即使实体自称 builtin;
        - 全批净效果为零 (空列表 / 逐字段一致) 不 bump。

        H6: 导入实体不经 apply_tool_patch, spec 原样落库 —— 全批先过明文
        密钥门, 任一实体含疑似明文密钥则整批拒绝, 零行落库。
        """
        for entity in entities:
            _raise_if_plaintext_secrets(
                entity.get("spec"), f"tools.{entity.get('name')}.spec"
            )
        async with self._txn(immediate=True) as session:
            current = await self._current_revision(session)
            self._check_expected(expected_revision, current)
            if not entities:
                return current

            max_position = int(
                (await session.execute(_TOOL_MAX_POSITION_SQL)).scalar_one()
            )
            now, new_revision, changed = _utc_now(), current + 1, False

            for entity in entities:
                name = entity["name"]
                row = (
                    await session.execute(_TOOL_GET_FULL_SQL, {"name": name})
                ).first()
                mapping_json = _dumps(entity.get("mapping", {}))
                if row is None:
                    # 新行: 门控列取实体值, 同步进 spec 保持抽取列↔spec 不变量。
                    spec = _spec_with_gate_columns(
                        entity["spec"],
                        entity.get("cache_ttl_key"),
                        entity.get("requires_env"),
                    )
                    max_position += 1
                    await session.execute(
                        _TOOL_INSERT_OR_REPLACE_SQL,
                        {
                            "name": name,
                            "routing": entity["routing"],
                            "description": entity.get("description", ""),
                            "cache_ttl_key": entity.get("cache_ttl_key"),
                            "requires_env": entity.get("requires_env"),
                            "publish_mode": entity.get("publish_mode", "default"),
                            "mapping_json": mapping_json,
                            "spec_json": _dumps(spec),
                            "source_file": entity.get("source_file"),
                            "position": max_position,
                            "updated_at": now,
                            "revision": new_revision,
                        },
                    )
                    changed = True
                    continue

                # 已存在: 门控列 + position 以库内现值为准 (ON CONFLICT 不刷
                # position); 保留的抽取列回写进 spec, 与 entity_edit 同语义。
                spec = _spec_with_gate_columns(entity["spec"], row[3], row[4])
                spec_json = _dumps(spec)
                # 净变化只比契约列 + source_file (门控列已保留为现值)。
                unchanged = (
                    entity["routing"] == row[1]
                    and entity.get("description", "") == row[2]
                    and mapping_json == row[6]
                    and spec_json == row[7]
                    and entity.get("source_file") == row[8]
                )
                if unchanged:
                    continue
                await session.execute(
                    _TOOL_INSERT_OR_REPLACE_SQL,
                    {
                        "name": name,
                        "routing": entity["routing"],
                        "description": entity.get("description", ""),
                        "cache_ttl_key": row[3],
                        "requires_env": row[4],
                        "publish_mode": row[5],
                        "mapping_json": mapping_json,
                        "spec_json": spec_json,
                        "source_file": entity.get("source_file"),
                        "position": row[9],
                        "updated_at": now,
                        "revision": new_revision,
                    },
                )
                changed = True

            if not changed:
                return current
            await self._set_revision(session, new_revision)
            return new_revision

    @staticmethod
    def _tool_entity_unchanged(before: dict[str, Any], after: dict[str, Any]) -> bool:
        """改名外所有持久化字段的净变化判定 (origin/updated_at 不参与)。"""
        return (
            after["name"] == before["name"]
            and after["routing"] == before["routing"]
            and after["description"] == before["description"]
            and after["cache_ttl_key"] == before["cache_ttl_key"]
            and after["requires_env"] == before["requires_env"]
            and after["publish_mode"] == before["publish_mode"]
            and after["mapping"] == before["mapping"]
            and after["spec"] == before["spec"]
        )

    async def update_upstream_config(
        self,
        name: str,
        config: dict[str, Any],
        *,
        type: str | None = None,
        expected_revision: int | None = None,
    ) -> int:
        """整体替换上游异型连接块 (可选改类型), 返回 (可能 bump 后的) revision。

        连接块形态边界由 :func:`entity_edit.validate_upstream_config` 守;
        疑似明文密钥字段 (非 ``${VAR}``) 拒绝写入。实际变更翻 origin=user。
        """
        config = validate_upstream_config(config)
        leaked = find_plaintext_secrets_in(config, f"upstreams.{name}")
        if leaked:
            raise ValueError(
                "Refusing to write plaintext secret field(s): " + ", ".join(leaked)
            )
        if type is not None and (not isinstance(type, str) or not type):
            raise ValueError("upstream type must be a non-empty string")

        async with self._txn(immediate=True) as session:
            current = await self._current_revision(session)
            self._check_expected(expected_revision, current)
            row = (
                await session.execute(_UPSTREAM_GET_FULL_SQL, {"name": name})
            ).first()
            if row is None:
                raise KeyError(name)
            current_type, current_config_json = row[1], row[3]
            new_type = type or current_type
            new_config_json = _dumps(config)
            if new_type == current_type and new_config_json == current_config_json:
                return current
            await session.execute(
                _UPSTREAM_UPDATE_CONFIG_SQL,
                {
                    "name": name,
                    "type": new_type,
                    "config_json": new_config_json,
                    "updated_at": _utc_now(),
                    "revision": current + 1,
                },
            )
            await self._set_revision(session, current + 1)
            return current + 1

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
