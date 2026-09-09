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
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.api.fuyao_client import FuyaoConfig, FuyaoClient
from src.api.omni_client import OmniConfig, OmniClient


DB_PATH = "./data/board.db"


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

        conn.commit()
    finally:
        conn.close()


def log_sync_start(source: str, board_type: str) -> int:
    """记录同步开始"""
    conn = get_db_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO sector_sync_log (source, board_type, status, start_time) VALUES (?, ?, 'running', datetime('now'))",
            (source, board_type),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def log_sync_end(log_id: int, status: str, message: str, record_count: int):
    """记录同步结束"""
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE sector_sync_log SET status = ?, message = ?, end_time = datetime('now'), record_count = ? WHERE id = ?",
            (status, message, record_count, log_id),
        )
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
        api_key="",
    )
    client = FuyaoClient(config)
    await client.start()

    total_sectors = 0
    total_stocks = 0

    try:
        for tag in tags:
            # 确定 board_type
            bt = "industry" if tag == "industry" else "concept"
            log_id = log_sync_start("fuyao", bt)

            try:
                print(f"  拉取 {bt} 板块列表 (tag={tag})...")

                # 1. 获取板块列表
                result = await client.call_tool(
                    "a_share_index_catalog", {"tag": tag}
                )

                if not result.success or not result.data:
                    log_sync_end(log_id, "failed", f"No data for tag {tag}", 0)
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
                        cursor_check = conn.execute(
                            "SELECT id FROM sectors WHERE source = 'fuyao' AND code = ?",
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
                                ("fuyao", bt, code_6, name),
                            )
                            sector_id = cursor.lastrowid

                        # 拉取成分股
                        try:
                            await asyncio.sleep(0.3)  # 限速

                            stocks_result = await client.call_tool(
                                "a_share_index_constituents", {"thscode": code}
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

                                # 批量插入成分股
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
                                        except sqlite3.IntegrityError:
                                            pass

                                # 更新 stock_count
                                conn.execute(
                                    "UPDATE sectors SET stock_count = ? WHERE id = ?",
                                    (len(stocks), sector_id),
                                )

                                total_stocks += len(stocks)
                                print(
                                    f"    [{i+1}/{len(boards)}] {name}: {len(stocks)} 只股票"
                                )

                        except Exception as e:
                            print(f"      拉取成分股失败: {e}")
                            continue

                        total_sectors += 1
                        conn.commit()

                finally:
                    conn.close()

                log_sync_end(
                    log_id, "success", f"Synced {total_sectors} sectors", total_stocks
                )

            except Exception as e:
                log_sync_end(log_id, "failed", str(e), 0)
                print(f"  同步失败: {e}")

    finally:
        await client.stop()

    return {
        "source": "fuyao",
        "board_type": board_type,
        "sectors": total_sectors,
        "stocks": total_stocks,
    }


async def sync_akshare(board_type: str = "all") -> dict:
    """从 AKSHARE 同花顺接口同步板块数据

    使用 akshare 的 _ths 接口获取同花顺板块数据

    Args:
        board_type: industry / concept / all

    Returns:
        sync result dict
    """
    import pandas as pd

    total_sectors = 0
    total_stocks = 0

    try:
        import akshare as ak
    except ImportError:
        return {
            "source": "akshare",
            "board_type": board_type,
            "sectors": 0,
            "stocks": 0,
            "error": "akshare 未安装，请运行: pip install akshare",
        }

    # 同步行业板块
    if board_type in ("all", "industry"):
        log_id = log_sync_start("akshare", "industry")

        try:
            print(f"  AKSHARE 同步行业板块...")

            # 获取同花顺行业板块列表
            df = ak.stock_board_industry_name_ths()
            print(f"    找到 {len(df)} 个行业板块")

            if df is not None and len(df) > 0:
                conn = get_db_connection()
                try:
                    # 清空旧数据
                    conn.execute(
                        "DELETE FROM sectors WHERE source = 'akshare' AND board_type = 'industry'"
                    )
                    conn.commit()

                    # 遍历获取成分股
                    for i, row in df.iterrows():
                        code = str(row.get("板块代码", "")).strip()
                        name = str(row.get("板块名称", "")).strip()

                        if not code or not name:
                            continue

                        # 插入板块
                        cursor = conn.execute(
                            "INSERT INTO sectors (source, board_type, code, name, stock_count) VALUES (?, ?, ?, ?, 0)",
                            ("akshare", "industry", code, name),
                        )
                        sector_id = cursor.lastrowid

                        # 拉取成分股
                        try:
                            time.sleep(1)  # 限速，避免被封

                            df_stocks = ak.stock_board_industry_cons_ths(symbol=code)
                            if df_stocks is not None and len(df_stocks) > 0:
                                for rank, (_, stock_row) in enumerate(df_stocks.iterrows(), 1):
                                    stock_code = str(stock_row.get("代码", "")).strip()
                                    stock_name = str(stock_row.get("名称", "")).strip()

                                    if stock_code and len(stock_code) == 6:
                                        try:
                                            conn.execute(
                                                "INSERT INTO sector_stocks (sector_id, stock_code, stock_name, rank) VALUES (?, ?, ?, ?)",
                                                (sector_id, stock_code, stock_name, rank),
                                            )
                                        except sqlite3.IntegrityError:
                                            pass

                                conn.execute(
                                    "UPDATE sectors SET stock_count = ? WHERE id = ?",
                                    (len(df_stocks), sector_id),
                                )
                                total_stocks += len(df_stocks)
                                print(f"    [{i+1}/{len(df)}] {name}: {len(df_stocks)} 只股票")

                        except Exception as e:
                            print(f"      拉取成分股失败: {e}")
                            continue

                        total_sectors += 1
                        conn.commit()

                finally:
                    conn.close()

                log_sync_end(log_id, "success", f"Synced {total_sectors} industry sectors", total_stocks)

        except Exception as e:
            log_sync_end(log_id, "failed", str(e), 0)
            print(f"  同步行业板块失败: {e}")

    # 同步概念板块
    if board_type in ("all", "concept"):
        log_id = log_sync_start("akshare", "concept")

        try:
            print(f"  AKSHARE 同步概念板块...")

            # 获取同花顺概念板块列表
            df = ak.stock_board_concept_name_ths()
            print(f"    找到 {len(df)} 个概念板块")

            if df is not None and len(df) > 0:
                conn = get_db_connection()
                try:
                    # 清空旧数据
                    conn.execute(
                        "DELETE FROM sectors WHERE source = 'akshare' AND board_type = 'concept'"
                    )
                    conn.commit()

                    # 遍历获取成分股
                    for i, row in df.iterrows():
                        code = str(row.get("板块代码", "")).strip()
                        name = str(row.get("板块名称", "")).strip()

                        if not code or not name:
                            continue

                        # 插入板块
                        cursor = conn.execute(
                            "INSERT INTO sectors (source, board_type, code, name, stock_count) VALUES (?, ?, ?, ?, 0)",
                            ("akshare", "concept", code, name),
                        )
                        sector_id = cursor.lastrowid

                        # 拉取成分股 - 支持板块代码或板块名称
                        try:
                            time.sleep(1)  # 限速

                            # 优先使用板块代码
                            df_stocks = ak.stock_board_concept_cons_ths(symbol=code)
                            if df_stocks is None or len(df_stocks) == 0:
                                # 尝试使用板块名称
                                df_stocks = ak.stock_board_concept_cons_ths(symbol=name)

                            if df_stocks is not None and len(df_stocks) > 0:
                                for rank, (_, stock_row) in enumerate(df_stocks.iterrows(), 1):
                                    # 尝试多个可能的列名
                                    stock_code = (
                                        str(stock_row.get("代码", "")).strip() or
                                        str(stock_row.get("股票代码", "")).strip() or
                                        str(stock_row.get("symbol", "")).strip()
                                    )
                                    stock_name = (
                                        str(stock_row.get("名称", "")).strip() or
                                        str(stock_row.get("股票名称", "")).strip() or
                                        str(stock_row.get("name", "")).strip()
                                    )

                                    if stock_code and len(stock_code) == 6:
                                        try:
                                            conn.execute(
                                                "INSERT INTO sector_stocks (sector_id, stock_code, stock_name, rank) VALUES (?, ?, ?, ?)",
                                                (sector_id, stock_code, stock_name, rank),
                                            )
                                        except sqlite3.IntegrityError:
                                            pass

                                conn.execute(
                                    "UPDATE sectors SET stock_count = ? WHERE id = ?",
                                    (len(df_stocks), sector_id),
                                )
                                total_stocks += len(df_stocks)
                                print(f"    [{i+1}/{len(df)}] {name}: {len(df_stocks)} 只股票")

                        except Exception as e:
                            print(f"      拉取成分股失败: {e}")
                            continue

                        total_sectors += 1
                        conn.commit()

                finally:
                    conn.close()

                log_sync_end(log_id, "success", f"Synced {total_sectors} concept sectors", total_stocks)

        except Exception as e:
            log_sync_end(log_id, "failed", str(e), 0)
            print(f"  同步概念板块失败: {e}")

    return {
        "source": "akshare",
        "board_type": board_type,
        "sectors": total_sectors,
        "stocks": total_stocks,
    }


async def sync_tqquant(board_type: str = "all") -> dict:
    """从 TQ-Quant (通达信客户端) 同步板块数据

    使用 tdx_quant 上游客户端获取板块数据

    Args:
        board_type: industry / concept / all

    Returns:
        sync result dict
    """
    import sqlite3
    import yaml

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
                "source": "TDX",
                "board_type": board_type,
                "sectors": 0,
                "stocks": 0,
                "error": "tdx_quant 未在 upstreams.yaml 中配置",
            }

        # TDX 数据源需要通过网关调用，不支持子进程方式
        # 返回错误提示用户
        return {
            "source": "TDX",
            "board_type": board_type,
            "sectors": 0,
            "stocks": 0,
            "error": "TDX 数据源暂不支持子进程同步。请使用 fuyao 或 mootdx2 数据源。",
        }

        # 以下代码暂不执行（未来实现网关内同步时使用）
        from src.api.tdx_quant_client import TdxQuantClient
        from src.models.tdx_quant_config import TdxQuantConfig, TdxQuantSettings
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
            "source": "TDX",
            "board_type": board_type,
            "sectors": 0,
            "stocks": 0,
            "error": f"创建 tdx_quant 客户端失败: {e}",
        }

    try:
        # 调用 get_sector_list 获取板块列表
        result = await client.call_tool("get_sector_list", {"list_type": 1})

        if result is None:
            return {
                "source": "TDX",
                "board_type": board_type,
                "sectors": 0,
                "stocks": 0,
                "error": "获取板块列表返回为空，请检查通达信客户端是否开启",
            }

        if not result.success or not result.data:
            err_msg = result.error if result and hasattr(result, 'error') else 'Unknown error'
            return {
                "source": "TDX",
                "board_type": board_type,
                "sectors": 0,
                "stocks": 0,
                "error": f"获取板块列表失败: {err_msg}",
            }

        all_sectors = result.data if isinstance(result.data, list) else []

        if not all_sectors:
            return {
                "source": "TDX",
                "board_type": board_type,
                "sectors": 0,
                "stocks": 0,
                "error": "获取板块列表为空，请确保通达信客户端已打开",
            }

        # 分类板块
        industry_sectors = []
        concept_sectors = []

        for item in all_sectors:
            code = item.get("Code", "") or item.get("code", "")
            name = item.get("Name", "") or item.get("name", "")

            if not code or not name:
                continue

            # 881xxx = 行业板块, 880xxx = 概念/地域板块
            if code.startswith("881"):
                industry_sectors.append({"code": code, "name": name})
            else:
                concept_sectors.append({"code": code, "name": name})

        print(f"    找到 {len(industry_sectors)} 个行业板块, {len(concept_sectors)} 个概念板块")

        conn = get_db_connection()
        try:
            # 同步行业板块 (如果指定)
            if board_type in ("all", "industry") and industry_sectors:
                log_id = log_sync_start("TDX", "industry")

                try:
                    conn.execute(
                        "DELETE FROM sectors WHERE source = 'tqquant' AND board_type = 'industry'"
                    )
                    conn.commit()

                    for sector in industry_sectors:
                        code = sector["code"]
                        name = sector["name"]

                        # 获取成分股
                        try:
                            stocks_result = await client.call_tool("get_stock_list_in_sector", {"block_code": code, "list_type": 1})
                            stocks = stocks_result.data if stocks_result and stocks_result.success and stocks_result.data else []
                            stock_count = len(stocks) if stocks else 0

                            cursor = conn.execute(
                                "INSERT INTO sectors (source, board_type, code, name, stock_count) VALUES (?, ?, ?, ?, ?)",
                                ("TDX", "industry", code, name, stock_count),
                            )
                            sector_id = cursor.lastrowid

                            if stocks:
                                for rank, (stock_code, stock_name) in enumerate(stocks, 1):
                                    # 转换为 6 位代码
                                    stock_code_6 = stock_code.replace(".SH", "").replace(".SZ", "")
                                    try:
                                        conn.execute(
                                            "INSERT INTO sector_stocks (sector_id, stock_code, stock_name, rank) VALUES (?, ?, ?, ?)",
                                            (sector_id, stock_code_6, stock_name, rank),
                                        )
                                    except sqlite3.IntegrityError:
                                        pass

                            total_sectors += 1
                            total_stocks += stock_count
                            print(f"    {name}: {stock_count} 只股票")

                        except Exception as e:
                            print(f"      拉取 {name} 成分股失败: {e}")
                            continue

                    conn.commit()
                    log_sync_end(log_id, "success", f"Synced {len(industry_sectors)} sectors", total_stocks)

                except Exception as e:
                    log_sync_end(log_id, "failed", str(e), 0)
                    print(f"  同步行业板块失败: {e}")

            # 同步概念板块 (如果指定)
            if board_type in ("all", "concept") and concept_sectors:
                log_id = log_sync_start("TDX", "concept")

                try:
                    conn.execute(
                        "DELETE FROM sectors WHERE source = 'tqquant' AND board_type = 'concept'"
                    )
                    conn.commit()

                    for sector in concept_sectors:
                        code = sector["code"]
                        name = sector["name"]

                        # 获取成分股
                        try:
                            stocks_result = await client.call_tool("get_stock_list_in_sector", {"block_code": code, "list_type": 1})
                            stocks = stocks_result.data if stocks_result and stocks_result.success and stocks_result.data else []
                            stock_count = len(stocks) if stocks else 0

                            cursor = conn.execute(
                                "INSERT INTO sectors (source, board_type, code, name, stock_count) VALUES (?, ?, ?, ?, ?)",
                                ("TDX", "concept", code, name, stock_count),
                            )
                            sector_id = cursor.lastrowid

                            if stocks:
                                for rank, (stock_code, stock_name) in enumerate(stocks, 1):
                                    stock_code_6 = stock_code.replace(".SH", "").replace(".SZ", "")
                                    try:
                                        conn.execute(
                                            "INSERT INTO sector_stocks (sector_id, stock_code, stock_name, rank) VALUES (?, ?, ?, ?)",
                                            (sector_id, stock_code_6, stock_name, rank),
                                        )
                                    except sqlite3.IntegrityError:
                                        pass

                            total_sectors += 1
                            total_stocks += stock_count
                            print(f"    {name}: {stock_count} 只股票")

                        except Exception as e:
                            print(f"      拉取 {name} 成分股失败: {e}")
                            continue

                    conn.commit()
                    log_sync_end(log_id, "success", f"Synced {len(concept_sectors)} sectors", total_stocks)

                except Exception as e:
                    log_sync_end(log_id, "failed", str(e), 0)
                    print(f"  同步概念板块失败: {e}")

        finally:
            conn.close()
            await client.stop()

    except Exception as e:
        try:
            await client.stop()
        except:
            pass
        return {
            "source": "TDX",
            "board_type": board_type,
            "sectors": total_sectors,
            "stocks": total_stocks,
            "error": str(e),
        }

    try:
        await client.stop()
    except:
        pass

    return {
        "source": "TDX",
        "board_type": board_type,
        "sectors": total_sectors,
        "stocks": total_stocks,
    }


async def sync_mootdx2(board_type: str = "all") -> dict:
    """从 mootdx2 本地文件同步板块数据

    使用 mootdx2 Reader 读取二进制 block_hy.dat (行业)，
    并自定义解析 infoharbor_block.dat (V7.73 新概念)

    Args:
        board_type: industry / concept / all

    Returns:
        sync result dict
    """
    import os
    import pandas as pd

    # 读取配置获取 TDX 路径
    tdxdir = None
    try:
        import yaml
        config_path = Path("config/upstreams.yaml")
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
                upstreams = config.get("upstreams", {})
                for name, cfg in upstreams.items():
                    if cfg.get("type") == "mootdx2":
                        tdxdir = cfg.get("tdxdir", "c:/new_tdx64")
                        break
    except Exception:
        pass

    if not tdxdir:
        tdxdir = "c:/new_tdx64"

    hq_cache = Path(tdxdir) / "T0002" / "hq_cache"
    infoharbor_path = hq_cache / "infoharbor_block.dat"

    # 读取配置获取 TDX 路径
    # ========== 1. mootdx2 Quotes 读取行业板块 block.dat ==========
    from mootdx2.quotes import Quotes

    total_sectors = 0
    total_stocks = 0

    # 只同步行业，或全部
    if board_type in ("all", "industry"):
        log_id = log_sync_start("mootdx2", "industry")

        try:
            print(f"  读取行业板块 block.dat ...")

            # 使用 Quotes 读取行业板块（block_hy.dat 不存在于本地，需用 Quotes TCP 接口）
            q = Quotes.factory(market="sz", bestip=True)
            df_industry = q.block(tofile="block.dat")
            q.close()

            if df_industry is None or len(df_industry) == 0:
                log_sync_end(log_id, "failed", "No data from block.dat", 0)
            else:
                # 添加 block_type 标记
                df_industry["block_type"] = "industry"

                # 按板块名分组
                sector_groups = df_industry.groupby("blockname")
                print(f"    找到 {len(sector_groups)} 个行业板块, {len(df_industry)} 条股票记录")

                conn = get_db_connection()
                try:
                    # 清空旧行业数据
                    conn.execute(
                        "DELETE FROM sectors WHERE source = 'mootdx2' AND board_type = 'industry'"
                    )
                    conn.commit()

                    # 写入板块和成分股
                    for sector_name, sector_df in sector_groups:
                        clean_name = str(sector_name).strip() if sector_name else f"行业_{total_sectors}"

                        cursor = conn.execute(
                            """INSERT INTO sectors (source, board_type, code, name, stock_count)
                               VALUES (?, ?, ?, ?, ?)""",
                            ("mootdx2", "industry", f"mootdx2_hy_{total_sectors}", clean_name, len(sector_df)),
                        )
                        sector_id = cursor.lastrowid

                        # 写入成分股
                        for rank, (_, row) in enumerate(sector_df.iterrows(), 1):
                            stock_code = str(row.get("code", "")).strip()
                            if stock_code and len(stock_code) == 6:
                                conn.execute(
                                    """INSERT OR IGNORE INTO sector_stocks (sector_id, stock_code, rank)
                                       VALUES (?, ?, ?)""",
                                    (sector_id, stock_code, rank),
                                )

                        total_sectors += 1
                        total_stocks += len(sector_df)

                    conn.commit()
                    print(f"    写入 {total_sectors} 行业板块, {total_stocks} 条股票记录")

                finally:
                    conn.close()

                log_sync_end(log_id, "success", f"Synced {total_sectors} industry sectors", total_stocks)

        except Exception as e:
            log_sync_end(log_id, "failed", str(e), 0)
            print(f"  同步行业板块失败: {e}")

    # ========== 2. 自定义解析 V7.73 infoharbor_block.dat ==========
    # 只同步概念，或全部
    if board_type in ("all", "concept"):
        log_id = log_sync_start("mootdx2", "concept")

        try:
            print(f"  解析 V7.73 概念板块 infoharbor_block.dat ...")

            if not infoharbor_path.exists():
                log_sync_end(log_id, "failed", f"文件不存在: {infoharbor_path}", 0)
                print(f"    文件不存在: {infoharbor_path}")
            else:
                # 解析 infoharbor_block.dat (GBK 文本格式)
                rows = []
                current_block = None

                with open(infoharbor_path, "r", encoding="gbk", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        # #GN_xxx 板块定义行
                        if line.startswith("#GN_"):
                            parts = line.split(",")
                            current_block = parts[0].replace("#GN_", "")
                        elif current_block:
                            items = line.split(",")
                            for item in items:
                                item = item.strip()
                                # 0#深市 1#沪市，提取6位股票代码
                                if "#" in item and len(item.split("#")[-1]) == 6:
                                    code = item.split("#")[-1]
                                    rows.append({"blockname": current_block, "code": code})

                df_concept = pd.DataFrame(rows)

                if len(df_concept) == 0:
                    log_sync_end(log_id, "failed", "infoharbor_block.dat 解析结果为空", 0)
                    print(f"    解析结果为空（文件可能未更新）")
                else:
                    # 按板块名分组
                    sector_groups = df_concept.groupby("blockname")
                    print(f"    找到 {len(sector_groups)} 个概念板块, {len(df_concept)} 条股票记录")

                    conn = get_db_connection()
                    try:
                        # 清空旧概念数据
                        conn.execute(
                            "DELETE FROM sectors WHERE source = 'mootdx2' AND board_type = 'concept'"
                        )
                        conn.commit()

                        concept_sectors = 0
                        concept_stocks = 0

                        # 写入板块和成分股
                        for sector_name, sector_df in sector_groups:
                            clean_name = str(sector_name).strip() if sector_name else f"概念_{concept_sectors}"

                            cursor = conn.execute(
                                """INSERT INTO sectors (source, board_type, code, name, stock_count)
                                   VALUES (?, ?, ?, ?, ?)""",
                                ("mootdx2", "concept", f"mootdx2_gn_{concept_sectors}", clean_name, len(sector_df)),
                            )
                            sector_id = cursor.lastrowid

                            # 写入成分股
                            for rank, (_, row) in enumerate(sector_df.iterrows(), 1):
                                stock_code = str(row.get("code", "")).strip()
                                if stock_code and len(stock_code) == 6:
                                    conn.execute(
                                        """INSERT OR IGNORE INTO sector_stocks (sector_id, stock_code, rank)
                                           VALUES (?, ?, ?)""",
                                        (sector_id, stock_code, rank),
                                    )

                            concept_sectors += 1
                            concept_stocks += len(sector_df)

                        conn.commit()
                        print(f"    写入 {concept_sectors} 概念板块, {concept_stocks} 条股票记录")

                        # 累加到总数
                        total_sectors += concept_sectors
                        total_stocks += concept_stocks

                    finally:
                        conn.close()

                    log_sync_end(log_id, "success", f"Synced {concept_sectors} concept sectors", concept_stocks)

        except Exception as e:
            log_sync_end(log_id, "failed", str(e), 0)
            print(f"  同步概念板块失败: {e}")

    return {
        "source": "mootdx2",
        "board_type": board_type,
        "sectors": total_sectors,
        "stocks": total_stocks,
    }


async def main():
    parser = argparse.ArgumentParser(description="板块数据同步工具")
    parser.add_argument(
        "--source",
        type=str,
        default="all",
        choices=["all", "fuyao", "mootdx2", "TDX", "tqquant", "akshare"],
        help="数据源",
    )
    parser.add_argument(
        "--type",
        type=str,
        default="all",
        choices=["all", "industry", "concept"],
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

    # 执行同步
    print(f"开始同步: source={args.source}, type={args.type}")

    if args.source in ("all", "fuyao"):
        print("\n=== 同步 fuyao ths-index ===")
        result = await sync_fuyao_index(args.type)
        print(f"  完成: {result}")

    if args.source in ("all", "mootdx2"):
        print("\n=== 同步 mootdx2 本地文件 (行业+概念) ===")
        result = await sync_mootdx2(args.type)
        print(f"  完成: {result}")

    if args.source in ("all", "akshare"):
        print("\n=== 同步 AKSHARE 同花顺板块 ===")
        result = await sync_akshare(args.type)
        print(f"  完成: {result}")

    if args.source in ("all", "TDX"):
        print("\n=== 同步 TQ-Quant 通达信板块 ===")
        result = await sync_tqquant(args.type)
        print(f"  完成: {result}")

    print("\n同步完成!")


if __name__ == "__main__":
    asyncio.run(main())
