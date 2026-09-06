"""MooTDX2 MCP Client 基于 mootdx2 库"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .upstream_client import ToolResult, UpstreamStatus
from .mootdx2_errors import (
    ErrorClassifier,
    MooTDXErrorType,
    error_to_result,
    no_data_result,
)

if TYPE_CHECKING:
    from .mootdx2_config import MooTDX2Settings
    from .mootdx2_pool import ConnectionPool

logger = logging.getLogger(__name__)

# K线频率映射
FREQ_MAP = {
    "day": 9,
    "week": 5,
    "month": 6,
    "minute1": 8,
    "minute5": 0,
    "minute15": 1,
    "minute30": 2,
    "minute60": 3,
}

# 数据量限制常量
MAX_LIMIT_KLINE = 1000  # K线最大返回条数
MAX_LIMIT_BATCH_QUOTE = 50  # 批量行情最大股票数
MAX_LIMIT_TRADE = 500  # 逐笔成交最大条数
MAX_LIMIT_TRANSACTIONS = 500  # 历史逐笔最大条数
MAX_LIMIT_STOCK_LIST = 10000  # 股票列表最大条数

# 数据量过大错误消息模板
DATA_TOO_LARGE_MSG = "数据量超过{limit}条，请缩小查询范围或增加筛选条件"


def _validate_kline_record(record: dict) -> bool:
    """校验单条K线数据合理性"""
    try:
        close = float(record.get("close", 0))
        open_ = float(record.get("open", 0))
        high = float(record.get("high", 0))
        low = float(record.get("low", 0))
        vol = float(record.get("vol", 0))
        # 收盘价必须 > 0
        if close <= 0:
            return False
        # 最高价 >= 最低价
        if high < low:
            return False
        # 开盘/收盘应该在最高/最低范围内
        if open_ > high or open_ < low:
            return False
        if close > high or close < low:
            return False
        # 成交量 >= 0
        if vol < 0:
            return False
        return True
    except (TypeError, ValueError):
        return False


def _validate_quote_record(record: dict) -> bool:
    """校验单条行情数据合理性"""
    try:
        close = float(record.get("close", 0))
        if close <= 0:
            return False
        return True
    except (TypeError, ValueError):
        return False


@dataclass
class MooTDX2Config:
    """MooTDX2 配置"""
    name: str
    market: str = "std"
    # 新增配置字段
    settings: "MooTDX2Settings | None" = None  # 完整配置
    pool: "ConnectionPool | None" = None  # 连接池


class MooTDX2Client:
    """MooTDX2 MCP 客户端"""

    def __init__(self, config: MooTDX2Config):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._quotes = None
        self._quotes_cls = None  # 延迟导入

        # 指标收集
        self._request_id = 0
        self._metrics = {
            "total_requests": 0,
            "total_errors": 0,
            "error_counts": {},
        }

    def _new_request_id(self) -> str:
        """生成请求 ID"""
        self._request_id += 1
        return f"{self.name}-{int(time.time())}-{self._request_id}"

    def _error_result(self, exc: Exception, context: str) -> ToolResult:
        """将异常转换为错误结果"""
        self._metrics["total_errors"] += 1
        err_info = error_to_result(exc, context)
        error_detail = err_info.get("error", {})
        error_type = error_detail.get("error_type", "internal_error")
        self._metrics["error_counts"][error_type] = self._metrics["error_counts"].get(error_type, 0) + 1

        return ToolResult(
            success=False,
            error=error_detail.get("message", str(exc)[:100]),
            error_detail=error_detail,
        )

    def _no_data_result(self, message: str = "查询成功但无数据") -> ToolResult:
        """返回 no_data 状态（不是错误，是正常业务状态）"""
        return ToolResult(
            success=True,
            data=None,
            error=message,
            error_detail={
                "error_type": MooTDXErrorType.NO_DATA.value,
                "message": message,
                "recoverable": True,
            },
        )

    def get_metrics(self) -> dict:
        """获取客户端指标"""
        return {
            **self._metrics,
            "pool_stats": self.config.pool.get_stats() if self.config.pool else {},
        }

    async def get_metrics_async(self) -> ToolResult:
        """获取服务指标（异步版本供 call_tool 调用）"""
        return ToolResult(
            success=True,
            data=self.get_metrics(),
            source="mootdx2",
        )

    async def get_health(self) -> ToolResult:
        """健康检查接口（轻量 ping）"""
        return ToolResult(
            success=True,
            data={"status": "ok", "source": "mootdx2"},
            source="mootdx2",
        )

    async def get_server_status(self) -> ToolResult:
        """获取服务状态（供 MCP 网关管理台直接读取）"""
        pool_stats = self.config.pool.get_stats() if self.config.pool else {}
        server_health = pool_stats.get("server_health", {})

        # 当前选中的服务器
        current_server = None
        if self.config.pool and self.config.pool.settings.servers:
            for s in self.config.pool.settings.servers:
                key = f"{s.host}:{s.port}"
                if key in server_health and server_health[key].get("healthy"):
                    current_server = {"host": s.host, "port": s.port, "latency_ms": s.latency_ms}
                    break

        return ToolResult(
            success=True,
            data={
                "version": self.config.settings.version if self.config.settings else "1.0.0",
                "status": self._status.value,
                "current_server": current_server,
                "pool_stats": pool_stats,
                "error_counts": self._metrics.get("error_counts", {}),
                "total_requests": self._metrics.get("total_requests", 0),
            },
            source="mootdx2",
        )

    def _get_quotes(self):
        """获取或创建 Quotes 实例"""
        if self._quotes is None:
            from mootdx2.quotes import Quotes
            self._quotes_cls = Quotes
            self._quotes = Quotes.factory(market=self.config.market, multithread=True, bestip=True)
        return self._quotes

    def _get_quote_sync(self, code: str):
        """同步获取实时行情"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.quotes(symbols=[sym])
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            return {
                "symbol": code,
                "close": float(latest.get("close", 0)),
                "open": float(latest.get("open", 0)),
                "high": float(latest.get("high", 0)),
                "low": float(latest.get("low", 0)),
                "volume": float(latest.get("vol", 0)),
                "amount": float(latest.get("amount", 0)),
            }
        return None

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == UpstreamStatus.HEALTHY

    async def start(self):
        self._status = UpstreamStatus.HEALTHY

    async def stop(self):
        self._status = UpstreamStatus.UNKNOWN

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        """工具路由"""
        method_map = {
            "get_quote": self.get_quote,
            "get_kline": self.get_kline,
            "get_batch_quote": self.get_batch_quote,
            "get_minute_data": self.get_minute_data,
            "get_trade": self.get_trade,
            "get_trade_history": self.get_trade_history,
            "get_trade_history_full": self.get_trade_history_full,
            "get_minute_trade_all": self.get_minute_trade_all,
            "get_index_kline": self.get_index_kline,
            "get_code_list": self.get_code_list,
            "get_stock_codes": self.get_stock_codes,
            "get_etf_codes": self.get_etf_codes,
            "get_etf_list": self.get_etf_list,
            "get_market_count": self.get_market_count,
            "get_workday": self.get_workday,
            "get_workday_range": self.get_workday_range,
            "get_index_all": self.get_index_all,
            "get_income": self.get_income,
            "get_metrics": self.get_metrics_async,
            "get_health": self.get_health,
            "get_server_status": self.get_server_status,
            # 新增接口
            "get_block": self.get_block,
            "get_f10": self.get_f10,
            "get_f10_company": self.get_f10_company,
            "get_minutes": self.get_minutes,
            "get_index_bars": self.get_index_bars,
            "get_xdxr": self.get_xdxr,
            "get_stock_all": self.get_stock_all,
            "get_k_data": self.get_k_data,
            "search_stock": self.search_stock,
            "get_stock_info": self.get_stock_info,
            "get_stocks_in_block": self.get_stocks_in_block,
            "get_blocks_for_stock": self.get_blocks_for_stock,
            "stock_unusual": self.stock_unusual,
            "indicator_boll": self.indicator_boll,
        }

        method = method_map.get(tool_name)
        if not method:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

        return await method(**params)

    async def get_quote(self, code: str) -> ToolResult:
        """获取实时行情

        Args:
            code: 股票代码，如 '600000' 或 'sh600000'

        Returns:
            ToolResult: 包含实时行情数据或错误信息
        """
        request_id = self._new_request_id()
        self._metrics["total_requests"] += 1
        logger.info(f"[{request_id}] get_quote({code})")

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_quote_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result(f"股票 {code} 无行情数据（可能停牌或非交易日）")
        except Exception as e:
            logger.error(f"[{request_id}] get_quote failed: {e}")
            return self._error_result(e, f"get_quote({code})")

    def _get_kline_sync(self, code: str, type: str = "day", limit: int = 100):
        """同步获取K线"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        freq = FREQ_MAP.get(type, 9)
        df = q.bars(symbol=sym, frequency=freq, offset=limit)
        if df is not None and len(df) > 0:
            if limit and limit > 0:
                df = df.tail(limit)
            return df.to_dict(orient="records")
        return None

    async def get_kline(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """获取K线

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数，默认100，最大1000

        Returns:
            数据量超过限制时返回错误提示
        """
        # 数据量检查
        if limit > MAX_LIMIT_KLINE:
            return ToolResult(
                success=False,
                error=DATA_TOO_LARGE_MSG.format(limit=MAX_LIMIT_KLINE),
                error_detail={
                    "error_type": MooTDXErrorType.INVALID_PARAM.value,
                    "message": DATA_TOO_LARGE_MSG.format(limit=MAX_LIMIT_KLINE),
                    "recoverable": False,
                },
            )

        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_kline_sync, code, type, limit)
            if data:
                # 数据合理性校验
                valid_data = [r for r in data if _validate_kline_record(r)]
                if not valid_data:
                    return self._no_data_result("K线数据校验失败（数据异常）")
                return ToolResult(success=True, data=valid_data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_kline failed: {e}")
            return self._error_result(e, f"get_kline({code})")

    # ========== 批量行情 ==========
    def _get_batch_quote_sync(self, codes: str):
        """同步批量行情"""
        q = self._get_quotes()
        code_list = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in codes.split(",")]
        df = q.quotes(symbols=code_list)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_batch_quote(self, codes: str) -> ToolResult:
        """获取批量行情"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_batch_quote_sync, codes)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_batch_quote failed: {e}")
            return self._error_result(e, f"get_batch_quote({codes})")

    # ========== 今日分时 ==========
    def _get_minute_data_sync(self, code: str):
        """同步今日分时"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.minute(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_minute_data(self, code: str) -> ToolResult:
        """获取今日分时"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_minute_data_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_minute_data failed: {e}")
            return self._error_result(e, f"get_minute_data({code})")

    # ========== 逐笔成交 ==========
    def _get_trade_sync(self, code: str):
        """同步逐笔成交"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.transaction(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_trade(self, code: str) -> ToolResult:
        """获取逐笔成交"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_trade_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_trade failed: {e}")
            return self._error_result(e, f"get_trade({code})")

    # ========== 历史逐笔 ==========
    def _get_trade_history_sync(self, code: str, date: str, start: int, count: int):
        """同步历史逐笔"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.transactions(symbol=sym, date=date, start=start, offset=count)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_trade_history(self, code: str, date: str, start: int = 0, count: int = 100) -> ToolResult:
        """获取历史逐笔"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_trade_history_sync, code, date, start, count)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_trade_history failed: {e}")
            return self._error_result(e, f"get_trade_history({code})")

    # ========== 指数K线 ==========
    def _get_index_kline_sync(self, code: str, type: str):
        """同步指数K线"""
        q = self._get_quotes()
        freq = FREQ_MAP.get(type, 9)
        df = q.index(symbol=code, frequency=freq)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_index_kline(self, code: str, type: str = "day") -> ToolResult:
        """获取指数K线"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_index_kline_sync, code, type)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_index_kline failed: {e}")
            return self._error_result(e, f"get_index_kline({code})")

    # ========== 全量代码列表 ==========
    def _get_code_list_sync(self, exchange: str):
        """同步全量代码列表"""
        q = self._get_quotes()
        df = q.stock(exchange=exchange)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_code_list(self, exchange: str) -> ToolResult:
        """获取全量代码列表"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_code_list_sync, exchange)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_code_list failed: {e}")
            return self._error_result(e, f"get_code_list({exchange})")

    # ========== 股票代码列表 ==========
    def _get_stock_codes_sync(self, limit: int, prefix: bool):
        """同步股票代码列表"""
        q = self._get_quotes()
        df = q.stock(exchange="sz")
        if df is not None and len(df) > 0:
            codes = df["code"].tolist()[:limit]
            if prefix:
                return [f"sz{c}" for c in codes]
            return codes
        return None

    async def get_stock_codes(self, limit: int = 100, prefix: bool = False) -> ToolResult:
        """获取股票代码列表"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_stock_codes_sync, limit, prefix)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_stock_codes failed: {e}")
            return self._error_result(e, f"get_stock_codes(limit={limit})")

    # ========== ETF代码列表 ==========
    def _get_etf_codes_sync(self, limit: int, prefix: bool):
        """同步ETF代码列表"""
        q = self._get_quotes()
        df = q.etf(exchange="sz")
        if df is not None and len(df) > 0:
            codes = df["code"].tolist()[:limit]
            if prefix:
                return [f"sz{c}" for c in codes]
            return codes
        return None

    async def get_etf_codes(self, limit: int = 100, prefix: bool = False) -> ToolResult:
        """获取ETF代码列表"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_etf_codes_sync, limit, prefix)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_etf_codes failed: {e}")
            return self._error_result(e, f"get_etf_codes(limit={limit})")

    # ========== ETF列表 ==========
    def _get_etf_list_sync(self, exchange: str, limit: int):
        """同步ETF列表"""
        q = self._get_quotes()
        df = q.etf(exchange=exchange)
        if df is not None and len(df) > 0:
            result = df.head(limit).to_dict(orient="records")
            return result
        return None

    async def get_etf_list(self, exchange: str = "sz", limit: int = 100) -> ToolResult:
        """获取ETF列表"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_etf_list_sync, exchange, limit)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_etf_list failed: {e}")
            return self._error_result(e, f"get_etf_list({exchange})")

    # ========== 市场证券数量 ==========
    def _get_market_count_sync(self, market: int):
        """同步市场证券数量"""
        q = self._get_quotes()
        count = q.stock_count(market=market)
        return {"market": market, "count": count}

    async def get_market_count(self, market: int = 0) -> ToolResult:
        """获取市场证券数量"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_market_count_sync, market)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_market_count failed: {e}")
            return self._error_result(e, f"get_market_count({market})")

    # ========== 交易日查询 ==========
    def _get_workday_sync(self, date: str, count: int):
        """同步交易日查询"""
        from datetime import datetime, timedelta
        from mootdx2.const import TRADE_DAY
        # 简化的交易日计算
        try:
            target = datetime.strptime(date, "%Y%m%d")
        except ValueError:
            target = datetime.strptime(date, "%Y-%m-%d")
        # 从目标日期往前找count个交易日
        results = []
        current = target
        while len(results) < count:
            current -= timedelta(days=1)
            if current.weekday() < 5:  # 周一到周五
                results.append(current.strftime("%Y%m%d"))
        results.reverse()
        return results

    async def get_workday(self, date: str, count: int = 1) -> ToolResult:
        """获取交易日"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_workday_sync, date, count)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_workday failed: {e}")
            return self._error_result(e, f"get_workday({date})")

    # ========== 交易日范围 ==========
    def _get_workday_range_sync(self, start: str, end: str):
        """同步交易日范围"""
        from datetime import datetime, timedelta
        try:
            start_dt = datetime.strptime(start, "%Y%m%d")
        except ValueError:
            start_dt = datetime.strptime(start, "%Y-%m-%d")
        try:
            end_dt = datetime.strptime(end, "%Y%m%d")
        except ValueError:
            end_dt = datetime.strptime(end, "%Y-%m-%d")
        results = []
        current = start_dt
        while current <= end_dt:
            if current.weekday() < 5:
                results.append(current.strftime("%Y%m%d"))
            current += timedelta(days=1)
        return results

    async def get_workday_range(self, start: str, end: str) -> ToolResult:
        """获取交易日范围"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_workday_range_sync, start, end)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_workday_range failed: {e}")
            return self._error_result(e, f"get_workday_range({start}-{end})")

    # ========== 全指K线 ==========
    def _get_index_all_sync(self, code: str, type: str, limit: int):
        """同步全指K线"""
        q = self._get_quotes()
        freq = FREQ_MAP.get(type, 9)
        df = q.index(symbol=code, frequency=freq)
        if df is not None and len(df) > 0:
            if limit and limit > 0:
                df = df.tail(limit)
            return df.to_dict(orient="records")
        return None

    async def get_index_all(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """获取全指K线"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_index_all_sync, code, type, limit)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_index_all failed: {e}")
            return self._error_result(e, f"get_index_all({code})")

    # ========== 盘后收益 ==========
    def _get_income_sync(self, code: str):
        """同步盘后收益"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.income(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_income(self, code: str, days: int = 30, start_date: str = "") -> ToolResult:
        """获取盘后收益"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_income_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_income failed: {e}")
            return self._error_result(e, f"get_income({code})")

    # ========== 板块数据 ==========
    def _get_block_sync(self, block_type: str = "block"):
        """同步板块数据"""
        q = self._get_quotes()
        df = q.block(block_type=block_type)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_block(self, block_type: str = "block") -> ToolResult:
        """获取板块数据

        Args:
            block_type: 板块类型，如 'block'（行业板块）、'concept'（概念板块）
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_block_sync, block_type)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_block failed: {e}")
            return self._error_result(e, f"get_block({block_type})")

    # ========== 板块成分股查询 ==========
    def _stocks_in_block_sync(self, block_code: str, block_type: int = 0) -> list:
        """同步获取板块成分股（从本地 block .dat 文件）

        Args:
            block_code: 板块名称（BlockReader只暴露blockname，不暴露数字板块代码）
            block_type: 0=行业(block_ch.dat), 1=概念(block_zs.dat), 2=地区(block_fd.dat)
        """
        tdxdir = self.config.settings.tdxdir if self.config.settings else ""
        if not tdxdir:
            return []

        block_file_map = {0: "block_ch.dat", 1: "block_zs.dat", 2: "block_fd.dat"}
        filename = block_file_map.get(block_type, "block_ch.dat")
        block_path = Path(tdxdir) / "vipdoc" / "block" / filename

        if not block_path.exists():
            return []

        try:
            from tdxpy.reader import BlockReader
            reader = BlockReader()
            # Use GROUP mode: one row per block with comma-separated code_list
            df = reader.get_df(str(block_path), result_type=1)
            if df is None or df.empty:
                return []

            # Find block by exact blockname match
            for _, row in df.iterrows():
                bn = str(row.get("blockname", "")).strip()
                if bn == block_code:
                    code_list = str(row.get("code_list", ""))
                    stock_codes = [c.strip() for c in code_list.split(",") if c.strip()]
                    result = []
                    for code in stock_codes:
                        if code.startswith(("60", "68")):
                            market = "sh"
                        elif code.startswith(("00", "30")):
                            market = "sz"
                        elif code.startswith(("8", "4")):
                            market = "bj"
                        else:
                            market = "sz"
                        result.append({"code": code, "market": market})
                    return result

            return []
        except Exception as e:
            logger.error(f"_stocks_in_block_sync failed: {e}")
            return []

    async def get_stocks_in_block(self, block_code: str, block_type: int = 0) -> ToolResult:
        """获取指定板块的所有成分股

        Args:
            block_code: 板块名称，如 '银行板块'（BlockReader只暴露blockname）
            block_type: 板块类型，默认 0（行业板块）
                0 = 行业板块 (block_ch.dat)
                1 = 概念板块 (block_zs.dat)
                2 = 地区板块 (block_fd.dat)
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._stocks_in_block_sync, block_code, block_type)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"get_stocks_in_block failed: {e}")
            return self._error_result(e, f"get_stocks_in_block({block_code})")

    # ========== 个股所属板块查询 ==========
    def _blocks_for_stock_sync(self, stock_code: str, market: str = "auto") -> list:
        """同步获取股票所属的所有板块（从本地 block .dat 文件）

        Args:
            stock_code: 股票代码，如 '600000'
            market: 市场（auto/auto推断），本参数暂未使用
        """
        tdxdir = self.config.settings.tdxdir if self.config.settings else ""
        if not tdxdir:
            return []

        stock_code = stock_code.lower().replace("sh", "").replace("sz", "").replace("bj", "")

        block_type_names = {0: "industry", 1: "concept", 2: "region"}
        block_files = {0: "block_ch.dat", 1: "block_zs.dat", 2: "block_fd.dat"}

        results = []

        for block_type, filename in block_files.items():
            block_path = Path(tdxdir) / "vipdoc" / "block" / filename
            if not block_path.exists():
                continue
            try:
                from tdxpy.reader import BlockReader
                reader = BlockReader()
                # FLAT mode: one row per (block, stock) pair
                df = reader.get_df(str(block_path), result_type=0)
                if df is None or df.empty:
                    continue
                # Filter rows where code matches stock_code
                matching = df[df["code"] == stock_code]
                for _, row in matching.iterrows():
                    results.append({
                        "block_name": str(row.get("blockname", "")),
                        "block_type": block_type_names.get(block_type, "unknown"),
                        "block_type_code": block_type,
                    })
            except Exception as e:
                logger.error(f"_blocks_for_stock_sync [{filename}]: {e}")
                continue

        return results

    async def get_blocks_for_stock(self, stock_code: str, market: str = "auto") -> ToolResult:
        """获取指定股票所属的所有板块

        Args:
            stock_code: 股票代码，如 '600000'
            market: 市场，默认为 'auto'（自动推断）
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._blocks_for_stock_sync, stock_code, market)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"get_blocks_for_stock failed: {e}")
            return self._error_result(e, f"get_blocks_for_stock({stock_code})")

    # ========== F10 基础数据 ==========
    def _get_f10_sync(self, symbol: str, name: str = ""):
        """同步F10基础数据"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.F10(symbol=sym, name=name)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_f10(self, symbol: str, name: str = "") -> ToolResult:
        """获取F10基础数据

        Args:
            symbol: 股票代码
            name: F10数据类型名称（可选）
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_f10_sync, symbol, name)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_f10 failed: {e}")
            return self._error_result(e, f"get_f10({symbol})")

    # ========== F10 公司概况 ==========
    def _get_f10_company_sync(self, symbol: str):
        """同步F10公司概况"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.F10C(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_f10_company(self, symbol: str) -> ToolResult:
        """获取F10公司概况

        Args:
            symbol: 股票代码
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_f10_company_sync, symbol)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_f10_company failed: {e}")
            return self._error_result(e, f"get_f10_company({symbol})")

    # ========== 分钟K线 ==========
    def _get_minutes_sync(self, symbol: str, date: str = "20191023"):
        """同步分钟K线"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.minutes(symbol=sym, date=date)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_minutes(self, symbol: str, date: str = "20191023") -> ToolResult:
        """获取分钟K线

        Args:
            symbol: 股票代码
            date: 日期，默认为 '20191023'
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_minutes_sync, symbol, date)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_minutes failed: {e}")
            return self._error_result(e, f"get_minutes({symbol})")

    # ========== 指数K线（指定起止） ==========
    def _get_index_bars_sync(self, symbol: str, frequency: int = 9, start: int = 0, offset: int = 800):
        """同步指数K线（指定起止位置）"""
        q = self._get_quotes()
        df = q.index_bars(symbol=symbol, frequency=frequency, start=start, offset=offset)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_index_bars(self, symbol: str, frequency: int = 9, start: int = 0, offset: int = 800) -> ToolResult:
        """获取指数K线（指定起止位置）

        Args:
            symbol: 指数代码，如 '000001'（上证指数）
            frequency: K线频率，默认9（日线），其他：5（周线）、6（月线）
            start: 起始位置
            offset: 偏移量
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_index_bars_sync, symbol, frequency, start, offset)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_index_bars failed: {e}")
            return self._error_result(e, f"get_index_bars({symbol})")

    # ========== 除权除息数据 ==========
    def _get_xdxr_sync(self, symbol: str):
        """同步除权除息数据"""
        q = self._get_quotes()
        sym = symbol.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.xdxr(symbol=sym)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_xdxr(self, symbol: str) -> ToolResult:
        """获取除权除息数据

        Args:
            symbol: 股票代码
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_xdxr_sync, symbol)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_xdxr failed: {e}")
            return self._error_result(e, f"get_xdxr({symbol})")

    # ========== 全部股票列表 ==========
    def _get_stock_all_sync(self):
        """同步全部股票列表"""
        q = self._get_quotes()
        df = q.stock_all()
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_stock_all(self) -> ToolResult:
        """获取全部股票列表"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_stock_all_sync)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_stock_all failed: {e}")
            return self._error_result(e, "get_stock_all")

    # ========== K线数据（指定日期范围） ==========
    def _get_k_data_sync(self, code: str, start_date: str, end_date: str):
        """同步K线数据（指定日期范围）"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.get_k_data(code=sym, start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0:
            return df.to_dict(orient="records")
        return None

    async def get_k_data(self, code: str, start_date: str = "", end_date: str = "") -> ToolResult:
        """获取K线数据（指定日期范围）

        Args:
            code: 股票代码
            start_date: 开始日期，格式 YYYYMMDD 或 YYYY-MM-DD
            end_date: 结束日期，格式 YYYYMMDD 或 YYYY-MM-DD
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_k_data_sync, code, start_date, end_date)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result()
        except Exception as e:
            logger.error(f"get_k_data failed: {e}")
            return self._error_result(e, f"get_k_data({code})")

    # ========== 搜索股票 ==========
    def _search_stock_sync(self, keyword: str):
        """同步搜索股票（基于本地 block 板块文件）"""
        try:
            from mootdx.reader import Reader
            tdxdir = self.config.settings.tdxdir if self.config.settings else ""
            reader = Reader.factory(market="std", tdxdir=tdxdir)
            df_block = reader.block()
            if df_block is None or len(df_block) == 0:
                return []

            kw = keyword.lower()
            result = []
            for _, row in df_block.iterrows():
                code = str(row.get("code", ""))
                name = row.get("name", "")
                if not code or not name:
                    continue
                if kw in code or kw in name.lower():
                    # 判断市场前缀
                    if code.startswith(("60", "68")):
                        market = "sh"
                    elif code.startswith(("00", "30")):
                        market = "sz"
                    elif code.startswith(("8", "4")):
                        market = "bj"
                    else:
                        market = "sz"
                    result.append({
                        "code": code,
                        "market": market,
                        "name": name,
                    })
            return result[:20]
        except Exception as e:
            logger.error(f"search_stock failed: {e}")
            return []

    async def search_stock(self, keyword: str) -> ToolResult:
        """搜索股票（名称/代码/拼音模糊匹配）

        Args:
            keyword: 搜索关键词（股票名称、代码或拼音缩写）
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._search_stock_sync, keyword)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"search_stock failed: {e}")
            return self._error_result(e, f"search_stock({keyword})")

    # ========== 聚合股票信息 ==========
    def _get_stock_info_sync(self, code: str, market: str = "sz", kline_period: str = "day", kline_count: int = 100):
        """同步获取聚合股票信息（快照在线 + K线/分时离线）"""
        try:
            q = self._get_quotes()
            sym = f"{market}{code}".lower()

            # 1. 实时快照（在线）
            quote_df = q.quotes(symbol=[sym])
            quote = quote_df.iloc[0].to_dict() if len(quote_df) > 0 else None

            # 2. K线（离线 - Reader）
            from mootdx.reader import Reader
            tdxdir = self.config.settings.tdxdir if self.config.settings else ""
            reader = Reader.factory(market="std", tdxdir=tdxdir)
            daily_df = reader.daily(symbol=code)
            kline_list = daily_df.tail(kline_count).to_dict(orient="records") if daily_df is not None and len(daily_df) > 0 else []

            # 3. 当日分时（离线 - Reader minute）
            minute_df = reader.minute(symbol=code)
            minute_list = minute_df.to_dict(orient="records") if minute_df is not None and len(minute_df) > 0 else []

            return {
                "code": code,
                "market": market,
                "quote": quote,
                "kline": kline_list,
                "minute": minute_list,
            }
        except Exception as e:
            logger.error(f"get_stock_info({market}{code}) failed: {e}")
            return {
                "code": code,
                "market": market,
                "quote": None,
                "kline": [],
                "minute": [],
            }

    async def get_stock_info(self, code: str, market: str = "sz", kline_period: str = "day", kline_count: int = 100) -> ToolResult:
        """获取聚合股票信息（快照 + K线 + 分时）

        Args:
            code: 股票代码
            market: 市场前缀 (sh/sz/bj)
            kline_period: K线周期 (day/week/month/minute1/5/15/30/60)
            kline_count: K线返回条数
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_stock_info_sync, code, market, kline_period, kline_count)
            if data.get("quote") is None and not data.get("kline") and not data.get("minute"):
                return self._no_data_result(f"股票 {market}{code} 无数据")
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"get_stock_info failed: {e}")
            return self._error_result(e, f"get_stock_info({market}{code})")

    # ========== 成交量移动平均线 ==========
    def _indicator_vol_ma_sync(self, code: str, type: str = "day", limit: int = 100) -> dict:
        """同步成交量移动平均线"""
        import pandas as pd

        kline = self._get_kline_sync(code, type, limit)
        if not kline:
            return {}
        df = pd.DataFrame(kline)
        if df.empty or "vol" not in df.columns:
            return {}
        vols = df["vol"].astype(float).tolist()
        dates = df["date"].tolist() if "date" in df.columns else ["" for _ in vols]

        def sma(data, n):
            result = []
            for i in range(len(data)):
                if i < n - 1:
                    result.append(None)
                else:
                    result.append(round(sum(data[i - n + 1:i + 1]) / n, 3))
            return result

        return {
            "vol_ma5": sma(vols, 5),
            "vol_ma10": sma(vols, 10),
            "dates": dates,
        }

    async def indicator_vol_ma(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """成交量移动平均线（VOL_MA5 和 VOL_MA10）

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数，默认100
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._indicator_vol_ma_sync, code, type, limit)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"indicator_vol_ma failed: {e}")
            return self._error_result(e, f"indicator_vol_ma({code})")

    # ========== MACD 指标 ==========
    def _indicator_macd_sync(self, code: str, type: str = "day", limit: int = 100) -> dict:
        """同步计算 MACD 指标"""
        import pandas as pd
        kline = self._get_kline_sync(code, type, limit)
        if not kline:
            return {}
        df = pd.DataFrame(kline)
        if "close" not in df.columns or df.empty:
            return {}
        closes = df["close"].astype(float).tolist()
        dates = df["date"].tolist() if "date" in df.columns else ["" for _ in closes]

        def ema(data, n):
            result = [None] * (n - 1)
            result.append(round(data[n - 1], 3))
            k = 2 / (n + 1)
            for i in range(n, len(data)):
                val = round(data[i] * k + result[-1] * (1 - k), 3)
                result.append(val)
            return result

        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        dif = [round(e12 - e26, 3) if e12 is not None and e26 is not None else None
               for e12, e26 in zip(ema12, ema26)]
        dea = ema([d if d is not None else 0 for d in dif], 9)
        macd = [round((d - s) * 2, 3) if d is not None and s is not None else None
                for d, s in zip(dif, dea)]

        return {
            "dif": dif,
            "dea": dea,
            "macd": macd,
            "dates": dates,
        }

    async def indicator_macd(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """计算 MACD 指标

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数，默认100，最大1000

        Returns:
            包含 DIF, DEA, MACD 柱状图的数据
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._indicator_macd_sync, code, type, limit)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"indicator_macd failed: {e}")
            return self._error_result(e, f"indicator_macd({code})")

    # ========== RSI 指标 ==========
    def _indicator_rsi_sync(self, code: str, type: str = "day", limit: int = 100) -> dict:
        """同步计算 RSI 指标"""
        import pandas as pd

        kline = self._get_kline_sync(code, type, limit)
        if not kline:
            return {}
        df = pd.DataFrame(kline)
        if "close" not in df.columns or df.empty:
            return {}
        closes = df["close"].astype(float).tolist()
        dates = df["date"].tolist() if "date" in df.columns else ["" for _ in closes]

        def calc_rsi(data, n):
            if len(data) < n + 1:
                return [None] * len(data)
            changes = [0.0] + [round(data[i] - data[i - 1], 3) for i in range(1, len(data))]
            gains = [max(c, 0) for c in changes]
            losses = [-min(c, 0) for c in changes]
            avg_gain = sum(gains[1 : n + 1]) / n
            avg_loss = sum(losses[1 : n + 1]) / n
            rsis = [None] * n
            if avg_loss == 0:
                rsis.append(100.0)
            else:
                rsis.append(round(100 - 100 / (1 + avg_gain / avg_loss), 3))
            for i in range(n + 1, len(changes)):
                avg_gain = (avg_gain * (n - 1) + gains[i]) / n
                avg_loss = (avg_loss * (n - 1) + losses[i]) / n
                if avg_loss == 0:
                    rsis.append(100.0)
                else:
                    rsis.append(round(100 - 100 / (1 + avg_gain / avg_loss), 3))
            return rsis

        return {
            "rsi6": calc_rsi(closes, 6),
            "rsi12": calc_rsi(closes, 12),
            "rsi24": calc_rsi(closes, 24),
            "dates": dates,
        }

    async def indicator_rsi(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """计算 RSI 指标

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数，默认100

        Returns:
            ToolResult: 包含 rsi6, rsi12, rsi24, dates
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._indicator_rsi_sync, code, type, limit)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"indicator_rsi failed: {e}")
            return self._error_result(e, f"indicator_rsi({code})")

    # ========== BOLL 布林带指标 ==========
    def _indicator_boll_sync(self, code: str, type: str = "day", limit: int = 100) -> dict:
        """同步计算BOLL布林带指标

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数

        Returns:
            dict: 包含 boll_upper, boll_mid, boll_lower, dates
        """
        import math
        kline = self._get_kline_sync(code, type, limit)
        if not kline:
            return {}

        try:
            import pandas as pd
            df = pd.DataFrame(kline)
            if "close" not in df.columns or df.empty:
                return {}
            closes = df["close"].astype(float).tolist()
            dates = df["date"].tolist() if "date" in df.columns else ["" for _ in closes]
        except Exception:
            closes = [float(r.get("close", 0)) for r in kline]
            dates = [str(r.get("date", "")) for r in kline]

        n, k = 20, 2
        result = {"boll_upper": [], "boll_mid": [], "boll_lower": [], "dates": dates}

        for i in range(len(closes)):
            if i < n - 1:
                result["boll_upper"].append(None)
                result["boll_mid"].append(None)
                result["boll_lower"].append(None)
            else:
                segment = closes[i - n + 1:i + 1]
                ma = sum(segment) / n
                variance = sum((x - ma) ** 2 for x in segment) / n
                std = math.sqrt(variance)
                result["boll_mid"].append(round(ma, 3))
                result["boll_upper"].append(round(ma + k * std, 3))
                result["boll_lower"].append(round(ma - k * std, 3))

        return result

    async def indicator_boll(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """计算BOLL布林带指标

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数

        Returns:
            ToolResult: 包含 boll_upper, boll_mid, boll_lower, dates
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._indicator_boll_sync, code, type, limit)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"indicator_boll failed: {e}")
            return self._error_result(e, f"indicator_boll({code})")
