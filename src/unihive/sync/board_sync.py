"""板块数据同步模块

从 fuyao ths-index 接口拉取板块数据并存储到 SQLite

Usage:
    python -m src.sync.board_sync --source all --full
    python -m src.sync.board_sync --source fuyao --type industry
    python -m src.sync.board_sync --source fuyao --type concept
    python -m src.sync.board_sync --status
"""

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from ..api.fuyao_client import FuyaoConfig, FuyaoClient
from ..api.omni_client import OmniConfig, OmniClient

# 加载 .env 文件
load_dotenv()

# 获取 API Key
def get_fuyao_api_key() -> str:
    """从环境变量或配置文件获取 Fuyao API Key"""
    return os.environ.get("FUYAO_API_KEY", "")


DB_PATH = "./data/board.db"


# 每日快照/条件情绪池：名称带时间指代（昨日/昨曾/近期/最近/近端/即将解禁/
# 近已解禁），语义只相对于"今天"成立，同步后立刻开始过时，不属于静态板块。
# 涨跌停等实时情绪池由 fuyao special_data 工具（涨停池/跌停池）提供，不入板块库。
SNAPSHOT_NAME_RE = re.compile(r"昨|近期|最近|近端|即将解禁|近已解禁")


def is_snapshot_sector(name: str) -> bool:
    """判断是否为每日快照类条件板块（同步时排除，不入库）。"""
    return bool(SNAPSHOT_NAME_RE.search(str(name or "")))


# tqcenter get_stock_list 官方板块分类码 —— 板块类型直接取自接口本身,
# 不再按板块代码前缀猜测:
#   '11' 缺省行业板块 / '12' 概念板块 / '13' 风格板块 / '14' 地区板块
TQ_SECTOR_CATEGORY_CODES: tuple[tuple[str, str], ...] = (
    ("industry", "11"),
    ("concept", "12"),
    ("style", "13"),
    ("region", "14"),
)


def is_a_share_code(code: str) -> bool:
    """判断是否为合法的 6 位 A 股代码（用于拦截脏数据写库）。"""
    return isinstance(code, str) and len(code) == 6 and code.isdigit()


def normalize_stock_entry(stock) -> tuple[str, str]:
    """把 tqcenter 板块成分股条目归一化成 (6 位代码, 名称)。

    tqcenter get_stock_list_in_sector 随 list_type 返回不同形态:
      - list_type=0: ["600519.SH", "000001.SZ", ...] 纯代码字符串
      - list_type=1: [{"Code": "600519.SH", "Name": "贵州茅台"}, ...]
        (键名与 get_sector_list 一致, 为 Code / Name)

    历史 bug: 旧实现直接 for stock_code, stock_name in stocks
    解包, dict 条目会被解成键名 "Code" / "Name" 并写进库,
    于是每个 TDX 板块在 sector_stocks 里只剩一条 stock_code='Code' 脏数据。
    """
    if isinstance(stock, dict):
        raw_code = stock.get("Code") or stock.get("code") or stock.get("thscode") or ""
        raw_name = stock.get("Name") or stock.get("name") or ""
    elif isinstance(stock, (list, tuple)):
        raw_code = stock[0] if len(stock) > 0 else ""
        raw_name = stock[1] if len(stock) > 1 else ""
    else:
        raw_code, raw_name = stock, ""

    code = str(raw_code or "").strip().upper()
    if "." in code:
        code = code.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    return code.strip(), str(raw_name or "").strip()


def get_db_connection():
    """获取数据库连接"""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库"""
    conn = get_db_connection()
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

        # 索引
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

        # WAL 模式：读成分股与同步写库不再互斥（delete 模式下读会等写锁，
        # 曾导致弹窗长时间转圈）。journal_mode 是数据库级持久属性，幂等设置。
        conn.execute("PRAGMA journal_mode=WAL")
        conn.commit()
    finally:
        conn.close()


async def sync_fuyao_index(board_type: str = "all") -> dict:
    """从 fuyao ths-index 同步板块数据

    Args:
        board_type: industry / concept / all

    Returns:
        sync result dict
    """
    # 标签映射
    tag_map = {
        "industry": "industry",
        "concept": "cn_concept",
        "all": ["industry", "cn_concept"],
    }

    if board_type == "all":
        tags = tag_map["all"]
    else:
        tags = [tag_map.get(board_type, board_type)]

    # 初始化 fuyao_index 客户端
    config = FuyaoConfig(
        name="fuyao_index",
        base_url="https://fuyao.aicubes.cn/mcp/a-share-index",
        api_key=get_fuyao_api_key(),
    )
    client = FuyaoClient(config)
    await client.start()

    total_sectors = 0
    total_stocks = 0

    try:
        for tag in tags:
            # 确定 board_type
            bt = "industry" if tag == "industry" else "concept"

            try:
                print(f"  拉取 {bt} 板块列表 (tag={tag})...")

                # 1. 获取板块列表
                result = await client.call_tool(
                    "get_a_share_index_catalog_ths_index_list", {"tag": tag}
                )

                if not result.success or not result.data:
                    print(f"  警告: tag={tag} 无数据")
                    continue

                # 解析板块列表
                boards = result.data if isinstance(result.data, list) else []
                print(f"    找到 {len(boards)} 个 {bt} 板块")

                conn = get_db_connection()
                try:
                    # 2. 逐个拉取成分股
                    for i, board in enumerate(boards):
                        # 解析板块信息
                        code = board.get("thscode") or board.get("index_code") or board.get("code", "")
                        name = board.get("name") or board.get("index_name", "")

                        if not code or not name:
                            continue

                        # 提取 6 位代码
                        code_6 = code.split(".")[0] if "." in code else code[:6]

                        # UPSERT 板块：检查存在则更新，不存在则插入
                        # 注意：FUYAO 数据源的 SOURCE 字段存储为 "THS"
                        cursor_check = conn.execute(
                            "SELECT id FROM sectors WHERE source = 'THS' AND code = ?",
                            (code_6,),
                        )
                        existing = cursor_check.fetchone()

                        if existing:
                            sector_id = existing["id"]
                            conn.execute(
                                "UPDATE sectors SET name = ?, stock_count = ?, update_time = datetime('now') WHERE id = ?",
                                (name, 0, sector_id),
                            )
                        else:
                            cursor = conn.execute(
                                "INSERT INTO sectors (source, board_type, code, name, stock_count) VALUES (?, ?, ?, ?, 0)",
                                ("THS", bt, code_6, name),
                            )
                            sector_id = cursor.lastrowid

                        # 拉取成分股
                        try:
                            await asyncio.sleep(0.3)  # 限速

                            stocks_result = await client.call_tool(
                                "get_a_share_index_constituents_ths_stock_list", {"thscode": code}
                            )

                            if stocks_result.success and stocks_result.data:
                                stocks = (
                                    stocks_result.data
                                    if isinstance(stocks_result.data, list)
                                    else []
                                )

                                # 先删除该板块的所有旧成分股
                                conn.execute(
                                    "DELETE FROM sector_stocks WHERE sector_id = ?",
                                    (sector_id,)
                                )

                                # 批量插入成分股；只统计实际入库的合法条目,
                                # 被跳过的脏数据不计入 stock_count（旧实现用
                                # len(stocks) 会虚高）。
                                inserted = 0
                                for rank, stock in enumerate(stocks, 1):
                                    stock_code = (
                                        stock.get("thscode", "")
                                        .split(".")[0]
                                        .strip()
                                    )
                                    stock_name = stock.get("name", "")

                                    if stock_code and len(stock_code) == 6:
                                        try:
                                            conn.execute(
                                                "INSERT INTO sector_stocks (sector_id, stock_code, stock_name, rank) VALUES (?, ?, ?, ?)",
                                                (
                                                    sector_id,
                                                    stock_code,
                                                    stock_name,
                                                    rank,
                                                ),
                                            )
                                            inserted += 1
                                        except sqlite3.IntegrityError:
                                            pass

                                # 更新 stock_count
                                conn.execute(
                                    "UPDATE sectors SET stock_count = ? WHERE id = ?",
                                    (inserted, sector_id),
                                )

                                total_stocks += inserted
                                print(
                                    f"    [{i+1}/{len(boards)}] {name}: {inserted} 只股票"
                                )

                        except Exception as e:
                            print(f"      拉取成分股失败: {e}")
                            continue

                        total_sectors += 1
                        conn.commit()

                finally:
                    conn.close()

            except Exception as e:
                print(f"  同步失败: {e}")

    finally:
        await client.stop()

    if total_sectors > 0:
        return {"status": "success", "message": f"Synced {total_sectors} sectors, {total_stocks} stocks", "stocks": total_stocks}
    else:
        return {"status": "failed", "message": "No sectors synced", "stocks": 0}



async def _sync_one_tq_sector(
    conn: sqlite3.Connection,
    client: Any,
    code: str,
    name: str,
    board_type: str,
) -> int:
    """同步单个板块：取成分股 → UPSERT 板块（类型来自官方分类）→ 重建成分股。

    返回成分股条数。板块按 (source='TDX', code) 唯一匹配，已存在时 UPDATE
    会连带校正 board_type（修复旧版按代码前缀误判的归类）。
    """
    stocks_result = await client.call_tool(
        "get_stock_list_in_sector", {"block_code": code, "list_type": 1}
    )
    stocks = (
        stocks_result.data
        if stocks_result and stocks_result.success and stocks_result.data
        else []
    )
    stock_count = len(stocks) if stocks else 0

    existing = conn.execute(
        "SELECT id FROM sectors WHERE source = 'TDX' AND code = ?",
        (code,),
    ).fetchone()

    if existing:
        sector_id = existing["id"]
        conn.execute(
            "UPDATE sectors SET name = ?, board_type = ?, stock_count = ?, "
            "update_time = datetime('now') WHERE id = ?",
            (name, board_type, stock_count, sector_id),
        )
    else:
        cursor = conn.execute(
            "INSERT INTO sectors (source, board_type, code, name, stock_count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("TDX", board_type, code, name, stock_count),
        )
        sector_id = cursor.lastrowid

    # 先清空该板块旧成分股，再写入本次清单
    conn.execute(
        "DELETE FROM sector_stocks WHERE sector_id = ?",
        (sector_id,),
    )
    for rank, stock in enumerate(stocks, 1):
        stock_code_6, stock_name = normalize_stock_entry(stock)
        if not is_a_share_code(stock_code_6):
            continue
        try:
            conn.execute(
                "INSERT INTO sector_stocks (sector_id, stock_code, stock_name, rank) "
                "VALUES (?, ?, ?, ?)",
                (sector_id, stock_code_6, stock_name, rank),
            )
        except sqlite3.IntegrityError:
            pass
    return stock_count


async def sync_tqquant(board_type: str = "all") -> dict:
    """从 TQ-Quant (通达信客户端) 同步板块数据

    板块清单按官方分类接口 tq.get_stock_list('11'/'12'/'13'/'14') 逐类型
    获取，板块类型即接口给定的分类，不按代码前缀判断。

    Args:
        board_type: industry / concept / style / region / all

    Returns:
        sync result dict
    """
    total_sectors = 0
    total_stocks = 0

    # 读取配置创建 tdx_quant 客户端
    try:
        config_path = Path("config/upstreams.yaml")
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        tdx_cfg = config.get("upstreams", {}).get("tdx_quant", {})
        if not tdx_cfg:
            return {
                "status": "failed",
                "message": "tdx_quant 未在 upstreams.yaml 中配置",
                "source": "TDX",
                "board_type": board_type,
                "sectors": 0,
                "stocks": 0,
            }

        # 初始化 TDX 客户端
        from ..api.tdx_quant_client import TdxQuantClient
        from ..models.tdx_quant_config import TdxQuantConfig, TdxQuantSettings
        settings = TdxQuantSettings(
            tdx_root=tdx_cfg.get("tdx_root", "D:/new_tdx_mock"),
            strategy_id=tdx_cfg.get("strategy_id", "unihive_gateway"),
        )
        tdx_config = TdxQuantConfig(
            name="tdx_quant",
            market="std",
            settings=settings,
        )
        client = TdxQuantClient(tdx_config)
        await client.start()

    except Exception as e:
        return {
            "status": "failed",
            "message": f"创建 tdx_quant 客户端失败: {e}",
            "source": "TDX",
            "board_type": board_type,
            "sectors": 0,
            "stocks": 0,
        }

    # 本次要同步的板块类型：board_type=all → 全部 4 类，否则只同步指定类型
    categories = [
        (bt, tq_code)
        for bt, tq_code in TQ_SECTOR_CATEGORY_CODES
        if board_type == "all" or board_type == bt
    ]
    if not categories:
        await client.stop()
        return {
            "status": "failed",
            "message": f"不支持的板块类型: {board_type}",
            "stocks": 0,
        }

    conn = get_db_connection()
    try:
        # 清理历史已入库的每日快照板块（及其成分股）。直接扫库按名称识别,
        # 不依赖 get_sector_list 清单；分两步显式删除, 不依赖默认关闭的
        # 外键级联。无论本次同步哪一类型都全量清理。
        rows = conn.execute(
            "SELECT id, name FROM sectors WHERE source = 'TDX'"
        ).fetchall()
        snapshot_ids = [
            row["id"] for row in rows if is_snapshot_sector(row["name"])
        ]
        if snapshot_ids:
            placeholders = ",".join("?" * len(snapshot_ids))
            conn.execute(
                f"DELETE FROM sector_stocks WHERE sector_id IN ({placeholders})",
                snapshot_ids,
            )
            conn.execute(
                f"DELETE FROM sectors WHERE id IN ({placeholders})",
                snapshot_ids,
            )
            conn.commit()

        # 逐类型调官方分类接口：板块类型即接口分类，不看代码前缀
        for bt, tq_code in categories:
            result = await client.call_tool(
                "get_stock_list", {"market": tq_code, "list_type": 1}
            )

            if not result or not result.success or not result.data:
                err_msg = result.error if result else "无返回"
                print(f"  获取 {bt} 板块列表失败: {err_msg}")
                continue

            sector_items = result.data if isinstance(result.data, list) else []
            print(f"  {bt}: {len(sector_items)} 个板块")

            for item in sector_items:
                code = item.get("Code", "") or item.get("code", "")
                name = item.get("Name", "") or item.get("name", "")

                if not code or not name:
                    continue
                # 双保险：官方分类清单若混入快照情绪池，也不入库
                if is_snapshot_sector(name):
                    continue

                try:
                    stock_count = await _sync_one_tq_sector(
                        conn, client, code, name, bt
                    )
                except Exception as e:
                    print(f"      同步 {name} 失败: {e}")
                    continue

                total_sectors += 1
                total_stocks += stock_count
                print(f"    [{bt}] {name}: {stock_count} 只股票")

            conn.commit()

    finally:
        conn.close()
        await client.stop()

    if total_sectors > 0 or total_stocks > 0:
        return {"status": "success", "message": f"Synced {total_sectors} sectors, {total_stocks} stocks", "stocks": total_stocks}
    else:
        return {"status": "failed", "message": "No sectors synced", "stocks": 0}



async def main():
    parser = argparse.ArgumentParser(description="板块数据同步工具")
    parser.add_argument(
        "--source",
        type=str,
        default="all",
        choices=["all", "fuyao", "TDX", "tqquant"],
        help="数据源",
    )
    parser.add_argument(
        "--type",
        type=str,
        default="all",
        choices=["all", "industry", "concept", "style", "region"],
        help="板块类型",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="全量同步（先清空再写入）",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="查看同步状态",
    )

    args = parser.parse_args()

    # 初始化数据库
    init_db()

    if args.status:
        # 显示同步状态
        conn = get_db_connection()
        try:
            print("\n=== 最近同步记录 ===")
            cursor = conn.execute(
                "SELECT * FROM sector_sync_log ORDER BY start_time DESC LIMIT 10"
            )
            for row in cursor.fetchall():
                print(
                    f"  {row['source']} | {row['board_type']} | {row['status']} | {row['record_count']} | {row['start_time']}"
                )

            print("\n=== 数据统计 ===")
            cursor = conn.execute(
                """
                SELECT source, board_type, COUNT(*) as sector_count, SUM(stock_count) as total_stocks
                FROM sectors
                GROUP BY source, board_type
                """
            )
            for row in cursor.fetchall():
                print(
                    f"  {row['source']} | {row['board_type']} | {row['sector_count']} 板块 | {row['total_stocks']} 股票"
                )
        finally:
            conn.close()
        return

    # Execute sync via OmniClient (single entry point)
    print(f"开始同步: source={args.source}, type={args.type}")

    omni_config = OmniConfig()
    omni_client = OmniClient(omni_config)
    await omni_client.start()

    result = await omni_client.run_sync(args.source, args.type)
    print(f"\n同步完成: {result}")
    print(json.dumps(result, ensure_ascii=False))

    await omni_client.stop()
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    asyncio.run(main())
