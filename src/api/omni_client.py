"""OMNIDATA 上游客户端 - 从本地 SQLite 读取板块数据"""

import asyncio
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .upstream_client import ToolResult, UpstreamClient, UpstreamStatus


# Timeout for sync operations (seconds)
SYNC_TIMEOUT_SECONDS = 300


@dataclass
class OmniConfig:
    """OMNIDATA 配置"""
    name: str = "omni"
    db_path: str = "./data/board.db"
    enabled: bool = True


class OmniClient:
    """OMNIDATA 上游客户端 - 从 SQLite 读取板块数据"""

    def __init__(self, config: OmniConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._db_path = config.db_path
        self._ensure_db()

        # 指标收集
        self._metrics = {
            "total_requests": 0,
            "total_errors": 0,
        }

    def _ensure_db(self) -> None:
        """确保数据库和表结构存在"""
        db_path = Path(self._db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self._db_path)
        try:
            # sectors 表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    board_type TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    stock_count INTEGER DEFAULT 0,
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source, code)
                )
            """)

            # sector_stocks 表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sector_stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sector_id INTEGER NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT,
                    rank INTEGER,
                    update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(sector_id, stock_code),
                    FOREIGN KEY (sector_id) REFERENCES sectors(id) ON DELETE CASCADE
                )
            """)

            # sector_sync_log 表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sector_sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    board_type TEXT,
                    status TEXT NOT NULL,
                    message TEXT,
                    start_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    end_time DATETIME,
                    record_count INTEGER DEFAULT 0
                )
            """)

            # 创建索引
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sectors_source_type ON sectors(source, board_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sectors_name ON sectors(name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sector_stocks_sector ON sector_stocks(sector_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sector_stocks_code ON sector_stocks(stock_code)"
            )

            conn.commit()
        finally:
            conn.close()

    def _insert_running_log(self, source: str, board_type: str) -> int:
        """Insert a new running log entry, return log_id."""
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "INSERT INTO sector_sync_log (source, board_type, status, start_time) VALUES (?, ?, 'running', datetime('now'))",
                (source, board_type),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def _update_log(self, log_id: int, status: str, message: str, record_count: int) -> None:
        """Update log entry with result."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE sector_sync_log SET status = ?, message = ?, end_time = datetime('now'), record_count = ? WHERE id = ?",
                (status, message, record_count, log_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_stats(self) -> list[dict]:
        """Get sector statistics grouped by (source, board_type)."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("""
                SELECT source, board_type, COUNT(*) as sector_count, SUM(stock_count) as total_stocks
                FROM sectors GROUP BY source, board_type
            """)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_recent_logs(self, limit: int = 10) -> list[dict]:
        """Get recent sync logs."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("""
                SELECT * FROM sector_sync_log ORDER BY start_time DESC LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def query_sectors(
        self,
        source: str = "",
        board_type: str = "",
        keyword: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """Query sectors with pagination and keyword search."""
        # Validate pagination parameters
        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 1
        if page_size > 500:
            page_size = 500

        # Source aliases: tqquant -> TDX
        source_aliases = {"tqquant": ("TDX", "tqquant")}

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            conditions = []
            params = []
            if source:
                aliases = source_aliases.get(source, (source,))
                if len(aliases) == 1:
                    conditions.append("source = ?")
                    params.append(aliases[0])
                else:
                    placeholders = " OR ".join(["source = ?"] * len(aliases))
                    conditions.append(f"({placeholders})")
                    params.extend(aliases)
            if board_type:
                conditions.append("board_type = ?")
                params.append(board_type)
            if keyword:
                conditions.append("(name LIKE ? OR code LIKE ?)")
                params.extend([f"%{keyword}%", f"%{keyword}%"])

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # Total count
            cursor = conn.execute(f"SELECT COUNT(*) as cnt FROM sectors WHERE {where_clause}", params)
            total_count = cursor.fetchone()["cnt"]

            # Pagination
            offset = (page - 1) * page_size
            cursor = conn.execute(f"""
                SELECT id, source, board_type, code, name, stock_count, update_time
                FROM sectors WHERE {where_clause}
                ORDER BY name LIMIT ? OFFSET ?
            """, params + [page_size, offset])

            sectors = [dict(row) for row in cursor.fetchall()]
            return {
                "sectors": sectors,
                "total": len(sectors),
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
            }
        finally:
            conn.close()

    async def run_sync(self, source: str = "all", board_type: str = "all") -> dict:
        """Execute sync as the single entry point for both HTTP and CLI.

        Returns:
            {"status": "success"|"failed", "message": str, "record_count": int, "log_id": int}
        """
        log_id = self._insert_running_log(source, board_type)

        try:
            result = await asyncio.wait_for(
                self._dispatch_sync(source, board_type),
                timeout=SYNC_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            result = {"status": "failed", "message": "同步超时 (5分钟)", "record_count": 0, "stocks": 0}
        except Exception as e:
            result = {"status": "failed", "message": str(e)[:200], "record_count": 0, "stocks": 0}

        # Update log with result
        record_count = result.get("stocks", 0)
        self._update_log(log_id, result["status"], result.get("message", ""), record_count)

        return {
            "status": result["status"],
            "message": result.get("message", ""),
            "record_count": record_count,
            "log_id": log_id,
        }

    async def _dispatch_sync(self, source: str, board_type: str) -> dict:
        """Dispatch sync to appropriate source."""
        from src.sync.board_sync import sync_fuyao_index, sync_tqquant

        if source in ("all", "fuyao"):
            result = await sync_fuyao_index(board_type)
            if source == "fuyao":
                return result
        if source in ("all", "TDX", "tdxquant", "tqquant"):
            result = await sync_tqquant(board_type)
            return result
        return {"status": "failed", "message": f"Unknown source: {source}", "stocks": 0}

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

    async def start(self) -> bool:
        """初始化客户端"""
        db_path = Path(self._db_path)
        if db_path.exists():
            self._status = UpstreamStatus.HEALTHY
        else:
            self._status = UpstreamStatus.DEGRADED
        return True

    async def stop(self) -> None:
        """关闭客户端"""
        self._status = UpstreamStatus.UNKNOWN

    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> ToolResult:
        """调用工具"""
        self._metrics["total_requests"] += 1

        try:
            method = getattr(self, f"_tool_{tool_name}", None)
            if not method:
                return ToolResult(
                    success=False,
                    error=f"Unknown tool: {tool_name}",
                )
            return await method(params)
        except Exception as e:
            self._metrics["total_errors"] += 1
            return ToolResult(
                success=False,
                error=str(e)[:200],
            )

    async def list_tools(self) -> list[dict]:
        """列出可用工具"""
        return [
            {"name": "omni_list_sectors", "description": "获取板块列表"},
            {"name": "omni_get_sector_stocks", "description": "获取板块成分股"},
            {"name": "omni_get_stock_sectors", "description": "获取股票所属板块"},
            {"name": "omni_search_board", "description": "搜索板块"},
            {"name": "omni_get_sync_status", "description": "获取同步状态"},
        ]

    async def health_check(self) -> bool:
        """健康检查"""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute("SELECT 1").fetchone()
            conn.close()
            return True
        except Exception:
            return False

    # ========== 工具实现 ==========

    async def _tool_omni_list_sectors(
        self, params: dict[str, Any]
    ) -> ToolResult:
        """获取板块列表"""
        source = params.get("source", "all")
        board_type = params.get("board_type", "all")
        keyword = params.get("keyword", "")

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            query = "SELECT id, source, board_type, code, name, stock_count FROM sectors WHERE 1=1"
            args = []

            if source and source != "all":
                query += " AND source = ?"
                args.append(source)

            if board_type and board_type != "all":
                query += " AND board_type = ?"
                args.append(board_type)

            if keyword:
                query += " AND (name LIKE ? OR code LIKE ?)"
                args.extend([f"%{keyword}%", f"%{keyword}%"])

            query += " ORDER BY source, board_type, name"

            cursor = conn.execute(query, args)
            rows = cursor.fetchall()

            data = [
                {
                    "id": row["id"],
                    "source": row["source"],
                    "board_type": row["board_type"],
                    "code": row["code"],
                    "name": row["name"],
                    "stock_count": row["stock_count"],
                }
                for row in rows
            ]

            return ToolResult(success=True, data=data, source=self.name)
        finally:
            conn.close()

    async def _tool_omni_get_sector_stocks(
        self, params: dict[str, Any]
    ) -> ToolResult:
        """获取板块成分股 (MCP 工具入口, 内部走 get_sector_stocks 统一查询)"""
        sector_code = params.get("sector_code", "")
        sector_name = params.get("sector_name", "")
        source = params.get("source", "all")

        if not sector_code and not sector_name:
            return ToolResult(
                success=False,
                error="sector_code or sector_name is required",
            )

        # MCP 路径兼容按 name 查: 先把 name 转成 code/source, 再走统一查询
        if not sector_code:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    "SELECT code, source FROM sectors WHERE name = ? LIMIT 1",
                    (sector_name,),
                )
                row = cursor.fetchone()
            finally:
                conn.close()
            if not row:
                return ToolResult(success=True, data=[], error="Sector not found")
            sector_code = row["code"]
            source = row["source"]

        result = self.get_sector_stocks(
            sector_code=sector_code,
            source=source if source != "all" else None,
        )
        if result is None:
            return ToolResult(success=True, data=[], error="Sector not found")
        return ToolResult(success=True, data=result["stocks"], source=self.name)

    def get_sector_stocks(
        self,
        sector_id: int | None = None,
        sector_code: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any] | None:
        """公开查询入口: 板块元数据 + 成分股列表

        Args:
            sector_id: 板块主键 (推荐, 无歧义)
            sector_code: 板块代码 (需配合 source 消歧)
            source: 数据源 (fuyao / TDX 等), 用 code 查询时必填

        Returns:
            {"sector": {...}, "stocks": [...]} 或 None (板块不存在).
            sector 字段: id / source / board_type / code / name / stock_count.
            stocks 字段: [{stock_code, stock_name, rank}, ...]

        Raises:
            ValueError: sector_id 和 sector_code 都未提供.
        """
        if not sector_id and not sector_code:
            raise ValueError("sector_id or sector_code is required")

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            if sector_id:
                cursor = conn.execute(
                    "SELECT id, source, board_type, code, name, stock_count "
                    "FROM sectors WHERE id = ?",
                    (sector_id,),
                )
            else:
                query = (
                    "SELECT id, source, board_type, code, name, stock_count "
                    "FROM sectors WHERE code = ?"
                )
                args: list[Any] = [sector_code]
                if source:
                    query += " AND source = ?"
                    args.append(source)
                cursor = conn.execute(query, args)

            sector = cursor.fetchone()
            if not sector:
                return None

            cursor = conn.execute(
                "SELECT stock_code, stock_name, rank FROM sector_stocks "
                "WHERE sector_id = ? ORDER BY rank ASC, stock_name ASC",
                (sector["id"],),
            )
            stocks = [dict(row) for row in cursor.fetchall()]
            return {"sector": dict(sector), "stocks": stocks}
        finally:
            conn.close()

    async def _tool_omni_get_stock_sectors(
        self, params: dict[str, Any]
    ) -> ToolResult:
        """获取股票所属板块"""
        stock_code = params.get("stock_code", "")

        if not stock_code:
            return ToolResult(
                success=False,
                error="stock_code is required",
            )

        # 归一化股票代码（去除市场前缀）
        stock_code = stock_code.upper().replace("SH", "").replace("SZ", "").replace("BJ", "")

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                """
                SELECT s.source, s.board_type, s.code, s.name
                FROM sectors s
                INNER JOIN sector_stocks ss ON s.id = ss.sector_id
                WHERE ss.stock_code = ?
                ORDER BY s.source, s.board_type, s.name
                """,
                (stock_code,),
            )
            rows = cursor.fetchall()

            data = [
                {
                    "source": row["source"],
                    "board_type": row["board_type"],
                    "code": row["code"],
                    "name": row["name"],
                }
                for row in rows
            ]

            return ToolResult(success=True, data=data, source=self.name)
        finally:
            conn.close()

    async def _tool_omni_search_board(
        self, params: dict[str, Any]
    ) -> ToolResult:
        """搜索板块"""
        keyword = params.get("keyword", "")

        if not keyword:
            return ToolResult(
                success=False,
                error="keyword is required",
            )

        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                """
                SELECT id, source, board_type, code, name, stock_count
                FROM sectors
                WHERE name LIKE ? OR code LIKE ?
                ORDER BY name
                LIMIT 50
                """,
                (f"%{keyword}%", f"%{keyword}%"),
            )
            rows = cursor.fetchall()

            data = [
                {
                    "id": row["id"],
                    "source": row["source"],
                    "board_type": row["board_type"],
                    "code": row["code"],
                    "name": row["name"],
                    "stock_count": row["stock_count"],
                }
                for row in rows
            ]

            return ToolResult(success=True, data=data, source=self.name)
        finally:
            conn.close()

    async def _tool_omni_get_sync_status(
        self, params: dict[str, Any]
    ) -> ToolResult:
        """Get sync status - now delegates to public methods."""
        data = {
            "recent_logs": self.get_recent_logs(limit=10),
            "stats": self.get_stats(),
        }
        return ToolResult(success=True, data=data, source=self.name)
