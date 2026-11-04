"""MCP 工具调用错误缓冲 — 内存环形 + SQLite 溢写 (2026-09-28)。

设计动机:
- 网关运行监控页要展示"最近哪些工具/上游失败了", 当前错误详情只活在
  ``logs/gateway.log`` 的 JSON 行里, 无任何回查接口, 运维只能 grep 日志。
- 本模块在 ``_execute_cached`` 失败出口捕获结构化错误记录 (不含入参),
  先入内存 deque (近期新鲜, 秒级轮询零 I/O), 接近上限时批量写 SQLite
  (历史回溯, 重启不丢)。与 ``storage/cache.py`` 同款 aiosqlite + SQLAlchemy
  async 引擎, 但用独立库 ``logs/errors.db`` 避免与 cache.db 锁竞争。

对外协议 (供 ``cached_calls`` 与 ``console_api`` 调用):
- ``append(record)`` — 追加一条错误 (成功路径不调)。
- ``recent(limit)`` — 内存最近 N 条 (倒序, 仅供近期 tab)。
- ``query(limit, history)`` — 先 flush 内存, 再返回 SQLite 历史 + 内存近期
  合并去重 (倒序)。``history=False`` 时仅内存。
- ``stats()`` — 内存/库内条数, 供摘要卡片。

字段不含入参 (用户裁决: 避免敏感数据; 足够定位"哪个工具/哪个上游/什么错")。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# 内存缓冲上限: 超过阈值批量写 SQLite 后清空。
# 200 条近期错误对秒级轮询足够新鲜, 且单条 JSON 约 0.5-2KB → 峰值 ~400KB 内存。
_MEMORY_MAXLEN = 200
# 触发 flush 的阈值 (留 50 条余量给 flush 前继续 append)。
_FLUSH_THRESHOLD = 150
# SQLite 库内上限, 超过删最旧 (与 cache.db LRU 同款)。
_DB_MAX_ROWS = 5000
# 单次 SQLite 操作超时 (与 cache.db operation_timeout 同款), 防 aiosqlite 卡死。
_OP_TIMEOUT = 5.0


@dataclass
class ErrorRecord:
    """单条工具调用错误记录 (不含入参)。"""
    ts: float  # epoch 秒 (time.time())
    tool: str  # 网关工具名
    route_key: str
    source: str | None  # 最终成功/失败的上游 (全失败时 None)
    success: bool  # False = 全链失败; True 暂不记录 (见捕获点)
    error: str  # 顶层错误消息
    duration_ms: int
    # hops 简化: [{source, success, error, duration_ms}]
    hops: list[dict[str, Any]] = field(default_factory=list)
    # 触发调用时的入参 (2026-09-29 应需求加入; 便于复现)。已做安全拷贝
    # 与截断, 避免引用可变 params 被后续修改, 也防超长值塞爆缓冲。
    params: dict[str, Any] = field(default_factory=dict)
    # 网关级错误类别 (invalid_request/upstream_unavailable/timeout/
    # rate_limited/no_data/internal_error); 供前端错误跟踪页分类筛选/着色。
    error_kind: str | None = None

    def to_row(self) -> dict[str, Any]:
        """供 SQLite INSERT 的行 (hops/params → JSON 串)。"""
        d = asdict(self)
        d["hops_json"] = json.dumps(d.pop("hops"), ensure_ascii=False, default=str)
        d["params_json"] = json.dumps(d.pop("params"), ensure_ascii=False, default=str)
        d["error_kind"] = d.pop("error_kind")
        d["ts"] = _iso(self.ts)
        d["success"] = 1 if d["success"] else 0
        return d


def _iso(epoch: float) -> str:
    """epoch 秒 → UTC ISO 毫秒 (与 JsonFormatter.ts 同款)。"""
    import datetime as _dt
    return (
        _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class ErrorBuffer:
    """内存环形 + SQLite 溢写缓冲。

    线程模型: 网关单事件循环, append 在事件循环线程同步写 deque (无锁);
    flush/query 在事件循环线程 async 操作 SQLite。asyncio 单线程下
    deque.append 与 len 天然原子, 无需 asyncio.Lock。
    """

    DB_PATH = "logs/errors.db"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._memory: deque[ErrorRecord] = deque(maxlen=_MEMORY_MAXLEN)
        self._engine = None
        self._session_factory: sessionmaker | None = None
        self._initialized = False
        self._db_total = 0  # 库内累计 (init 时从 COUNT(*) 读初值)
        path = db_path or self.DB_PATH
        candidate = Path(path)
        self._db_path = candidate if candidate.is_absolute() else self._resolve_relative(candidate)

    @staticmethod
    def _resolve_relative(candidate: Path) -> Path:
        """相对路径锚定项目根 (与 cache.py 同款, 不随启动 CWD 漂移)。"""
        try:
            from ..core.config_db import project_root
            return project_root() / candidate
        except Exception:
            return candidate.resolve()

    async def initialize(self) -> None:
        """异步初始化 SQLite 引擎 + 建表。失败降级为纯内存 (不拒启)。"""
        if self._initialized:
            return
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._engine = create_async_engine(
                f"sqlite+aiosqlite:///{self._db_path}", echo=False,
            )
            self._session_factory = sessionmaker(
                self._engine, class_=AsyncSession, expire_on_commit=False,
            )
            async with self._session_factory() as session:
                await session.execute(text("""
                    CREATE TABLE IF NOT EXISTS errors (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL,
                        tool TEXT,
                        route_key TEXT,
                        source TEXT,
                        success INTEGER,
                        error TEXT,
                        duration_ms,
                        hops_json TEXT,
                        params_json TEXT,
                        error_kind TEXT
                    )
                """))
                # 兼容旧库: 表已存在但缺列时补列 (SQLite 无 ADD COLUMN IF NOT EXISTS)
                cols = {r[1] for r in await session.execute(text("PRAGMA table_info(errors)"))}
                if "params_json" not in cols:
                    await session.execute(text(
                        "ALTER TABLE errors ADD COLUMN params_json TEXT"
                    ))
                if "error_kind" not in cols:
                    await session.execute(text(
                        "ALTER TABLE errors ADD COLUMN error_kind TEXT"
                    ))
                await session.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_errors_ts ON errors(ts DESC)"
                ))
                await session.execute(text("PRAGMA journal_mode=WAL"))
                await session.execute(text("PRAGMA synchronous=NORMAL"))
                await session.commit()
                row = (await session.execute(text("SELECT COUNT(*) FROM errors"))).scalar()
                self._db_total = int(row or 0)
            self._initialized = True
            logger.info(
                "ErrorBuffer initialized at %s (db_total=%d)",
                self._db_path, self._db_total,
            )
        except Exception as e:
            logger.warning("ErrorBuffer init failed (%s); memory-only mode", e)
            self._engine = None
            self._session_factory = None

    def append(self, record: ErrorRecord) -> None:
        """追加一条错误 (事件循环线程同步, 无需锁)。

        触发条件由调用方 (cached_calls) 判定: 仅 ``success=False`` 才调。
        """
        self._memory.append(record)

    async def flush(self) -> int:
        """内存缓冲批量写 SQLite 后清空。返回写入条数。

        在 ``query(history=True)`` 前调, 避免单独的定时 flush 线程。
        """
        if not self._memory or not self._initialized or self._session_factory is None:
            return 0
        if len(self._memory) < _FLUSH_THRESHOLD:
            # 低于阈值不强制 flush; 但 query(history=True) 调用方会先判
            # 是否需要。本方法本身尊重阈值, 避免高频小批量写盘。
            return 0
        return await self._force_flush()

    async def _force_flush(self) -> int:
        """无阈值条件强制 flush (供 query 调用)。"""
        if not self._memory or self._session_factory is None:
            return 0
        # 取快照后立刻清空, 避免 flush 期间新 append 漏写
        snapshot = list(self._memory)
        self._memory.clear()
        rows = [r.to_row() for r in snapshot]
        try:
            async with self._session_factory() as session:
                await asyncio.wait_for(
                    self._insert_batch(session, rows), timeout=_OP_TIMEOUT,
                )
                await session.commit()
            self._db_total += len(rows)
            # LRU 淘汰
            if self._db_total > _DB_MAX_ROWS:
                await self._evict(session=None)
            return len(rows)
        except asyncio.TimeoutError:
            logger.warning("ErrorBuffer flush timed out; %d rows lost", len(rows))
            # 丢回内存? 会与 maxlen 打架; 直接丢弃 (错误日志非关键数据)
            return 0
        except Exception as e:
            logger.warning("ErrorBuffer flush failed (%s); %d rows lost", e, len(rows))
            return 0

    async def _insert_batch(self, session: AsyncSession, rows: list[dict]) -> None:
        await session.execute(
            text("""
                INSERT INTO errors (ts, tool, route_key, source, success,
                                    error, duration_ms, hops_json, params_json,
                                    error_kind)
                VALUES (:ts, :tool, :route_key, :source, :success,
                        :error, :duration_ms, :hops_json, :params_json,
                        :error_kind)
            """),
            rows,
        )

    async def _evict(self, session: AsyncSession | None) -> None:
        """删除最旧行, 保留 _DB_MAX_ROWS 条。"""
        own_session = session is None
        if own_session:
            assert self._session_factory is not None
            session = self._session_factory()
            await session.__aenter__()
        try:
            assert session is not None
            excess = self._db_total - _DB_MAX_ROWS
            if excess > 0:
                await session.execute(text(
                    "DELETE FROM errors WHERE id IN ("
                    "  SELECT id FROM errors ORDER BY ts ASC, id ASC LIMIT :n)"
                ), {"n": excess})
                if own_session:
                    await session.commit()
                self._db_total = _DB_MAX_ROWS
        finally:
            if own_session:
                await session.__aexit__(None, None, None)

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """内存最近 N 条 (倒序)。秒级轮询零 I/O。"""
        items = list(self._memory)[-limit:]
        items.reverse()
        return [_record_to_api(r) for r in items]

    async def query(
        self, limit: int = 50, history: bool = False,
    ) -> list[dict[str, Any]]:
        """返回历史 + 近期合并去重 (倒序)。

        ``history=False``: 仅内存 (近期 tab)。
        ``history=True``: 先 flush, 再读 SQLite 历史, 与内存合并去重。
        """
        if not history:
            return await self.recent(limit)
        await self._force_flush()
        db_items: list[dict[str, Any]] = []
        if self._session_factory is not None:
            try:
                async with self._session_factory() as session:
                    result = await asyncio.wait_for(
                        session.execute(text(
                            "SELECT ts, tool, route_key, source, success, "
                            "error, duration_ms, hops_json, params_json, error_kind "
                            "FROM errors "
                            "ORDER BY id DESC LIMIT :n"
                        ), {"n": limit}),
                        timeout=_OP_TIMEOUT,
                    )
                    for row in result:
                        db_items.append(_row_to_api(row))
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning("ErrorBuffer query failed (%s); memory only", e)
        mem_items = await self.recent(limit)
        # 合并去重 (按 ts+tool+error), 倒序
        seen: set[tuple] = set()
        merged: list[dict[str, Any]] = []
        for item in db_items + mem_items:
            key = (item["ts"], item["tool"], item["error"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        merged.sort(key=lambda x: x["ts_epoch"], reverse=True)
        return merged[:limit]

    def stats(self) -> dict[str, Any]:
        """摘要: 内存/库内条数 + 最近错误时间 + 容量常量 (供前端展示)。"""
        latest_ts = self._memory[-1].ts if self._memory else 0.0
        return {
            "in_memory": len(self._memory),
            "in_db": self._db_total,
            "latest_ts": _iso(latest_ts) if latest_ts else None,
            "memory_cap": _MEMORY_MAXLEN,
            "flush_threshold": _FLUSH_THRESHOLD,
            "db_cap": _DB_MAX_ROWS,
        }

    async def close(self) -> None:
        if self._engine is not None:
            try:
                await asyncio.wait_for(self._engine.dispose(), timeout=2.0)
            except Exception as e:
                logger.warning("ErrorBuffer engine dispose failed: %s", e)
            self._engine = None
            self._session_factory = None


def _record_to_api(r: ErrorRecord) -> dict[str, Any]:
    """ErrorRecord → 前端 API 行 (ts 转 ISO)。"""
    d = asdict(r)
    d["ts"] = _iso(r.ts)
    d["ts_epoch"] = r.ts
    return d


def _row_to_api(row: Any) -> dict[str, Any]:
    """SQLite 行 → 前端 API 行。"""
    hops_raw = row[7]
    params_raw = row[8] if len(row) > 8 else None
    error_kind = row[9] if len(row) > 9 else None
    try:
        hops = json.loads(hops_raw) if hops_raw else []
    except (TypeError, ValueError):
        hops = []
    try:
        params = json.loads(params_raw) if params_raw else {}
    except (TypeError, ValueError):
        params = {}
    # ts 存的是 ISO 串; 回填 epoch 用于排序/去重
    import datetime as _dt
    try:
        ts_epoch = _dt.datetime.fromisoformat(
            str(row[0]).replace("Z", "+00:00")
        ).timestamp()
    except (ValueError, TypeError):
        ts_epoch = 0.0
    return {
        "ts": str(row[0]),
        "ts_epoch": ts_epoch,
        "tool": row[1],
        "route_key": row[2],
        "source": row[3],
        "success": bool(row[4]),
        "error": row[5],
        "duration_ms": row[6],
        "hops": hops,
        "params": params,
        "error_kind": error_kind,
    }
