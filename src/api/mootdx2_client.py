"""MooTDX2 MCP Client 基于 mootdx2 库"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .upstream_client import ToolResult, UpstreamStatus
from ..models.mootdx2_errors import (
    ErrorClassifier,
    MooTDXErrorType,
    error_to_result,
    no_data_result,
)


class SectorDataError(Exception):
    """板块数据相关错误的领域异常

    携带 MooTDXErrorType + 中文消息，由 async wrapper 转 ToolResult。
    """

    def __init__(self, error_type: "MooTDXErrorType", message: str):
        self.error_type = error_type
        self.message = message
        super().__init__(message)


if TYPE_CHECKING:
    from ..models.mootdx2_config import MooTDX2Settings
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

    def _get_tdxdir(self) -> str | None:
        """获取 tdxdir，如果没有配置则返回 None 让 mootdx2 自动探测"""
        if self.config.settings:
            tdxdir = self.config.settings.tdxdir
            if tdxdir:
                return tdxdir
        return None

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

    def _sector_error_result(self, exc: "SectorDataError") -> ToolResult:
        """将 SectorDataError 转换为 ToolResult（recoverable=False，配置/业务错误）"""
        self._metrics["total_errors"] += 1
        error_type = exc.error_type.value
        self._metrics["error_counts"][error_type] = self._metrics["error_counts"].get(error_type, 0) + 1
        return ToolResult(
            success=False,
            error=exc.message,
            error_detail={
                "error_type": error_type,
                "message": exc.message,
                "recoverable": False,
            },
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
            # 从配置获取超时设置
            timeout = 30  # 默认 30 秒
            if self.config.settings:
                timeout = max(
                    self.config.settings.connect_timeout_seconds,
                    self.config.settings.read_timeout_seconds,
                ) * 3  # 给足够的重试时间
            self._quotes = Quotes.factory(
                market=self.config.market,
                multithread=True,
                bestip=True,
                timeout=timeout,
            )
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
        # P1 (2026-09-09 7th-round audit): 与 UpstreamClient / OmniClient 对齐,
        # DEGRADED 仍可响应, router 不应直接跳过 fallback。
        return self._status in (UpstreamStatus.HEALTHY, UpstreamStatus.DEGRADED)

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
            # 新增接口
            "get_f10": self.get_f10,
            "get_f10_company": self.get_f10_company,
            "get_minutes": self.get_minutes,
            "get_index_bars": self.get_index_bars,
            "get_xdxr": self.get_xdxr,
            "get_stock_all": self.get_stock_all,
            "get_k_data": self.get_k_data,
            "search_stock": self.search_stock,
            "get_stock_info": self.get_stock_info,
            "get_custom_sector_list": self.get_custom_sector_list,
            "get_custom_sector_stocks": self.get_custom_sector_stocks,
            "get_index_overview": self.get_index_overview,
            "stock_top_board": self.stock_top_board,
            "stock_unusual": self.stock_unusual,
            "indicator_atr": self.indicator_atr,
            # Reader 离线接口（本地 .day / .lc1 / .lc5 文件）
            "get_daily": self.get_daily,
            "get_minute": self.get_minute,
            "get_fzline": self.get_fzline,
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

    # ========== 自定义板块 ==========
    def _scan_blk_files_sync(self) -> list[dict]:
        """直接扫描 T0002/blocknew/*.blk 文件（不依赖 cfg 索引）

        适用于 V7.73+ 通达信：cfg 索引格式可能更新，但 .blk 文件本身仍可读。
        策略：先尝试 cfg 解析（拿到 blockname → blk_file 映射），
        cfg 解析失败时退化为用 .blk 文件名作为 sector_name。

        .blk 文件格式：每行 = market_id(1=SH / 0=SZ / 2=BJ) + 6位股票代码
        例如：'1600000' 表示 SH 600000，'0000001' 表示 SZ 000001。
        """
        tdxdir = self._get_tdxdir()
        blocknew_dir = Path(tdxdir) / "T0002" / "blocknew"
        if not blocknew_dir.exists():
            logger.info(
                f"custom sector dir missing (V7.73+ TDX?): {blocknew_dir}"
            )
            return []

        # 1. 尝试从 cfg 解析 sector_name → blk_file 映射
        name_by_blk: dict[str, str] = {}
        try:
            from mootdx2.tools.customize import Customize
            customize = Customize(tdxdir=tdxdir)
            grouped = customize.search(group=True)
            # mootdx2 返回 DataFrame，列: blockname, block_type, stock_count, code_list
            if grouped is not None and len(grouped) > 0:
                # 检查是否是 DataFrame
                if hasattr(grouped, 'iterrows'):
                    # DataFrame 格式
                    for _, row in grouped.iterrows():
                        blk_file = str(row.get('block_type', '')).strip()
                        name = str(row.get('blockname', '')).strip()
                        if blk_file and name:
                            name_by_blk[blk_file] = name
                else:
                    # 列表格式（旧版本兼容）
                    for entry in grouped or []:
                        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                            name = str(entry[0]).strip()
                            blk_file = entry[2] if len(entry) >= 3 else None
                            if blk_file:
                                name_by_blk[str(blk_file).strip()] = name
        except Exception as e:
            logger.info(
                f"Customize.search failed (V7.73 cfg may be incompatible): {e}; "
                f"falling back to .blk filename as sector name"
            )

        # 2. 扫描 .blk 文件，统计每板块的股票数
        market_prefix_map = {"1": "sh", "0": "sz", "2": "bj"}
        results = []
        for blk_path in sorted(blocknew_dir.glob("*.blk")):
            blk_name = blk_path.stem
            display_name = name_by_blk.get(blk_name, blk_name)
            try:
                content = blk_path.read_text(encoding="gbk", errors="ignore")
            except Exception:
                content = blk_path.read_text(encoding="utf-8", errors="ignore")
            codes = []
            for line in content.splitlines():
                line = line.strip()
                if len(line) >= 7 and line[0].isdigit():
                    code = line[1:]
                    if code.isdigit() and len(code) == 6:
                        codes.append(code)
            results.append({
                "sector_name": display_name,
                "sector_type": "custom",
                "stock_count": len(codes),
                "blk_file": blk_name,
            })
        return results

    def _get_custom_sector_list_sync(self) -> list:
        """同步读取自定义板块列表（T0002/blocknew/）

        Returns:
            list[dict]: [{"sector_name", "sector_type": "custom", "stock_count"}, ...]

        V7.73+ 通达信 cfg 索引可能更新，直接扫描 .blk 文件更稳定。
        """
        return self._scan_blk_files_sync()

    async def get_custom_sector_list(self) -> ToolResult:
        """获取自定义板块列表（用户在 TDX 客户端手动维护）

        数据源：{tdxdir}/T0002/blocknew/blocknew.cfg + *.blk
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_custom_sector_list_sync)
            return ToolResult(success=True, data=data, source="mootdx2")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_custom_sector_list failed: {e}")
            return self._error_result(e, "get_custom_sector_list()")

    def _get_custom_sector_stocks_sync(self, sector_code: str, market: str = "auto") -> list:
        """同步读取自定义板块成分股

        Args:
            sector_code: 板块名称（用户自定义）或 .blk 文件名（不含扩展名）
            market: 市场（保留参数，对齐其它工具签名；内部按 .blk 文件的 market_id 前缀自动推断）

        V7.73+ 通达信 cfg 索引可能更新，直接扫描 .blk 文件更稳定。
        """
        tdxdir = self._get_tdxdir()
        blocknew_dir = Path(tdxdir) / "T0002" / "blocknew"
        if not blocknew_dir.exists():
            logger.info(
                f"custom sector dir missing (V7.73+ TDX?): {blocknew_dir}"
            )
            return []

        # 1. 尝试 cfg 解析拿到 blk_file
        blk_file = None
        try:
            from mootdx2.tools.customize import Customize
            customize = Customize(tdxdir=tdxdir)
            grouped = customize.search(group=True)
            for entry in grouped or []:
                if isinstance(entry, (list, tuple)) and len(entry) >= 3:
                    if str(entry[0]).strip() == sector_code:
                        blk_file = str(entry[2]).strip()
                        break
        except Exception as e:
            logger.info(
                f"Customize.search(name=) failed (V7.73 cfg may be incompatible): {e}; "
                f"treating sector_code as .blk filename"
            )

        # 2. cfg 失败或未匹配时，直接把 sector_code 当作 .blk 文件名
        if blk_file is None:
            blk_file = sector_code

        blk_path = blocknew_dir / f"{blk_file}.blk"
        if not blk_path.exists():
            logger.info(
                f"custom sector '{sector_code}' not found (blk={blk_path} missing)"
            )
            return []

        # 3. 解析 .blk 文件：每行 = market_id + 6位代码
        market_prefix_map = {"1": "sh", "0": "sz", "2": "bj"}
        try:
            content = blk_path.read_text(encoding="gbk", errors="ignore")
        except Exception:
            content = blk_path.read_text(encoding="utf-8", errors="ignore")

        result = []
        for line in content.splitlines():
            line = line.strip()
            if len(line) < 7 or not line[0].isdigit():
                continue
            prefix = line[0]
            code = line[1:]
            if not (code.isdigit() and len(code) == 6):
                continue
            m = market_prefix_map.get(prefix, "sz")
            result.append({"code": code, "market": m})
        return result

    async def get_custom_sector_stocks(
        self, sector_code: str, market: str = "auto"
    ) -> ToolResult:
        """获取自定义板块的成分股

        Args:
            sector_code: 自定义板块名（用户在 TDX 客户端命名的标签）
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, self._get_custom_sector_stocks_sync, sector_code, market
            )
            return ToolResult(success=True, data=data, source="mootdx2")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_custom_sector_stocks failed: {e}")
            return self._error_result(e, f"get_custom_sector_stocks({sector_code})")

    # ========== 指数概览 ==========
    def _index_overview_sync(self) -> list:
        """同步获取主要指数概览（6个指数）"""
        INDEX_CODES = [
            ("000001", "上证指数", "sh"),
            ("399001", "深证成指", "sz"),
            ("399006", "创业板", "sz"),
            ("000688", "科创50", "sh"),
            ("889999", "北证50", "bj"),
            ("000300", "沪深300", "sh"),
        ]
        q = self._get_quotes()
        # Build code list with market prefixes
        code_list = []
        for c, n, m in INDEX_CODES:
            if m == "sh":
                code_list.append(f"sh{c}")
            elif m == "sz":
                code_list.append(f"sz{c}")
            else:
                code_list.append(f"bj{c}")
        # Strip prefixes for quotes API
        stripped = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in code_list]
        df = q.quotes(symbols=stripped)
        if df is None or len(df) == 0:
            return []
        records = df.to_dict(orient="records")
        # Match back by position (order is preserved)
        result = []
        for i, rec in enumerate(records):
            if i >= len(INDEX_CODES):
                break
            code, name, market = INDEX_CODES[i]
            close = float(rec.get("close", 0))
            pct_chg = float(rec.get("pct_chg", 0))
            result.append({
                "code": code,
                "name": name,
                "market": market,
                "close": close,
                "change_pct": pct_chg,
            })
        return result

    async def get_index_overview(self) -> ToolResult:
        """获取主要指数概览（6个指数：上证、深证、创业板、科创50、北证50、沪深300）"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._index_overview_sync)
            if not data:
                return self._no_data_result("指数数据获取失败")
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"get_index_overview failed: {e}")
            return self._error_result(e, "get_index_overview")

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

    # ========== 离线日线（Reader，TDX 本地 .day 文件） ==========
    def _get_daily_sync(
        self, code: str, adjust: str = "none", start_date: str = "", end_date: str = ""
    ) -> list:
        """同步读取离线日线数据。

        Args:
            code: 6 位股票代码（不带市场前缀）
            adjust: 复权方式 none / qfq / hfq（默认不复权）
            start_date: 起始日期 yyyy-MM-dd（可选）
            end_date: 结束日期 yyyy-MM-dd（可选）
        """
        tdxdir = self._get_tdxdir()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        from mootdx2.reader import Reader
        reader = Reader.factory(market="std", tdxdir=tdxdir)
        df = reader.daily(symbol=sym, adjust=adjust)
        if df is None or df.empty:
            return []

        if start_date:
            df = df[df.index >= start_date] if hasattr(df.index, "__ge__") else df
        if end_date:
            df = df[df.index <= end_date] if hasattr(df.index, "__le__") else df

        records = df.reset_index().to_dict(orient="records")
        for r in records:
            if "index" in r:
                idx = r.pop("index")
                r["date"] = str(idx)[:10]
        return records

    async def get_daily(
        self, code: str, adjust: str = "none", start_date: str = "", end_date: str = ""
    ) -> ToolResult:
        """获取股票日线数据（离线，TDX 本地 .day 文件）

        Args:
            code: 股票代码（6 位数字，可带 sh/sz/bj 前缀）
            adjust: 复权方式
                - 'none': 不复权（默认）
                - 'qfq': 前复权
                - 'hfq': 后复权
            start_date: 起始日期 yyyy-MM-dd（可选）
            end_date: 结束日期 yyyy-MM-dd（可选）

        数据源：{tdxdir}/vipdoc/sh/lday/sh600036.day 等本地二进制文件
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, self._get_daily_sync, code, adjust, start_date, end_date
            )
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result(f"未找到 {code} 的日线数据")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_daily failed: {e}")
            return self._error_result(e, f"get_daily({code})")

    # ========== 离线分钟线（Reader，TDX 本地 .lc1/.lc5 文件） ==========
    def _get_minute_sync(
        self, code: str, suffix: str = "1", start_date: str = "", end_date: str = ""
    ) -> list:
        """同步读取离线分钟线数据。

        Args:
            code: 6 位股票代码
            suffix: '1' = 1 分钟线（minline/.lc1）/ '5' = 5 分钟线（fzline/.lc5）
            start_date: 起始日期（可选）
            end_date: 结束日期（可选）
        """
        tdxdir = self._get_tdxdir()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        suffix_int = 1 if str(suffix) == "1" else 5

        from mootdx2.reader import Reader
        reader = Reader.factory(market="std", tdxdir=tdxdir)
        df = reader.minute(symbol=sym, suffix=suffix_int)
        if df is None or df.empty:
            return []

        if start_date:
            df = df[df.index >= start_date] if hasattr(df.index, "__ge__") else df
        if end_date:
            df = df[df.index <= end_date] if hasattr(df.index, "__le__") else df

        records = df.reset_index().to_dict(orient="records")
        for r in records:
            if "index" in r:
                idx = r.pop("index")
                r["datetime"] = str(idx)
        return records

    async def get_minute(
        self, code: str, suffix: str = "1", start_date: str = "", end_date: str = ""
    ) -> ToolResult:
        """获取股票分钟线数据（离线，TDX 本地 .lc1/.lc5 文件）

        Args:
            code: 股票代码（6 位数字）
            suffix: '1' = 1 分钟线（默认）/ '5' = 5 分钟线
            start_date: 起始日期（可选）
            end_date: 结束日期（可选）

        数据源：{tdxdir}/vipdoc/sh/minline/sh600036.lc1
              或 {tdxdir}/vipdoc/sh/fzline/sh600036.lc5
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None, self._get_minute_sync, code, suffix, start_date, end_date
            )
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result(f"未找到 {code} 的{suffix}分钟数据")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_minute failed: {e}")
            return self._error_result(e, f"get_minute({code})")

    # ========== 离线分时线（Reader.fzline，本地 .lc5 文件） ==========
    def _get_fzline_sync(self, code: str) -> list:
        """同步读取离线分时线数据。

        Args:
            code: 6 位股票代码
        """
        tdxdir = self._get_tdxdir()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        from mootdx2.reader import Reader
        reader = Reader.factory(market="std", tdxdir=tdxdir)
        df = reader.fzline(symbol=sym)
        if df is None or (hasattr(df, "empty") and df.empty):
            return []
        if isinstance(df, bool):
            return []

        records = df.reset_index().to_dict(orient="records")
        for r in records:
            if "index" in r:
                idx = r.pop("index")
                r["datetime"] = str(idx)
        return records

    async def get_fzline(self, code: str) -> ToolResult:
        """获取股票分时线数据（离线，TDX 本地 .lc5 文件）

        Args:
            code: 股票代码（6 位数字）

        数据源：{tdxdir}/vipdoc/sh/fzline/sh600036.lc5
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_fzline_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return self._no_data_result(f"未找到 {code} 的分时线数据")
        except SectorDataError as e:
            return self._sector_error_result(e)
        except Exception as e:
            logger.error(f"get_fzline failed: {e}")
            return self._error_result(e, f"get_fzline({code})")

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
        """同步搜索股票（基于本地 block 板块文件）

        V7.73-64 注意事项：mootdx2 `reader.block()` 接口在新版通达信已废弃
        （官方 dat 文件迁到 infoharbor_block.dat / spblock.dat，mootdx2 未适配）。
        本方法在 V7.73 环境下会返回空列表，由 router 串行 fallback 到 fuyao_meta。
        """
        try:
            from mootdx2.reader import Reader
            tdxdir = self._get_tdxdir()
            if not tdxdir:
                return []
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
            # V7.73 下 reader.block() 会报"板块文件不存在"，
            # 降级为 warning，让 router fallback 到 fuyao_meta 搜索。
            logger.warning(
                f"search_stock fallback (likely V7.73+ TDX): {e}"
            )
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
            from mootdx2.reader import Reader
            tdxdir = self._get_tdxdir()
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

    # ========== ATR 指标 ==========
    def _indicator_atr_sync(self, code: str, type: str = "day", limit: int = 100) -> dict:
        """同步计算ATR指标"""
        import pandas as pd
        kline = self._get_kline_sync(code, type, limit)
        if not kline:
            return {}
        df = pd.DataFrame(kline)
        if df.empty or "close" not in df.columns:
            return {}
        highs = df["high"].astype(float).tolist() if "high" in df.columns else df["close"].astype(float).tolist()
        lows = df["low"].astype(float).tolist() if "low" in df.columns else df["close"].astype(float).tolist()
        closes = df["close"].astype(float).tolist()
        dates = df["date"].tolist() if "date" in df.columns else ["" for _ in closes]

        n = 14
        trs = []
        for i in range(len(closes)):
            if i == 0:
                trs.append(highs[0] - lows[0])
            else:
                hl = highs[i] - lows[i]
                hpc = abs(highs[i] - closes[i - 1])
                lpc = abs(lows[i] - closes[i - 1])
                trs.append(max(hl, hpc, lpc))

        atr = []
        for i in range(len(trs)):
            if i < n - 1:
                atr.append(None)
            elif i == n - 1:
                atr.append(round(sum(trs[:n]) / n, 3))
            else:
                atr.append(round((atr[-1] * (n - 1) + trs[i]) / n, 3))

        return {"atr": atr, "dates": dates}

    async def indicator_atr(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """计算ATR指标（平均真实波幅）

        Args:
            code: 股票代码
            type: K线类型 (day/week/month/minute1/5/15/30/60)
            limit: 返回条数，默认100，最大1000
        """
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._indicator_atr_sync, code, type, limit)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"indicator_atr failed: {e}")
            return self._error_result(e, f"indicator_atr({code})")

    # ========== 市场异动（主力监控精灵） ==========
    def _stock_unusual_sync(self, event_type: str = "all") -> list:
        """同步获取市场异动数据"""
        import pandas as pd

        q = self._get_quotes()

        # Get concept blocks
        blocks_df = q.block(block_type="concept")
        if blocks_df is None or blocks_df.empty:
            return []

        # Collect stock codes from blocks (limited to avoid too many requests)
        all_codes = set()
        for _, row in blocks_df.head(50).iterrows():
            block_name = str(row.get("name", ""))
            # Use stock_count as hint — we need actual stock codes
            # Since q.block() returns code_list in newer mootdx2, check for it
            if "code_list" in row and pd.notna(row["code_list"]):
                codes = str(row["code_list"]).split(",")
                for c in codes:
                    c = c.strip()
                    if c:
                        all_codes.add(c)

        stock_list = list(all_codes)[:500]
        if not stock_list:
            return []

        # Batch quote
        results = []
        for i in range(0, len(stock_list), MAX_LIMIT_BATCH_QUOTE):
            chunk = stock_list[i:i + MAX_LIMIT_BATCH_QUOTE]
            prefixed = []
            for c in chunk:
                if str(c).startswith(("60", "68")):
                    prefixed.append(f"sh{c}")
                elif str(c).startswith(("00", "30")):
                    prefixed.append(f"sz{c}")
                elif str(c).startswith(("8", "4")):
                    prefixed.append(f"bj{c}")
                else:
                    prefixed.append(f"sz{c}")
            stripped = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in prefixed]
            batch_df = q.quotes(symbols=stripped)
            if batch_df is not None and len(batch_df) > 0:
                results.extend(batch_df.to_dict(orient="records"))

        if not results:
            return []

        df = pd.DataFrame(results)
        if df.empty:
            return []

        # Filter by event type
        if event_type == "涨":
            df = df[df["pct_chg"] > 0]
        elif event_type == "跌":
            df = df[df["pct_chg"] < 0]
        elif event_type == "放量":
            df = df[df["vol"] > df["vol"].quantile(0.75)] if "vol" in df.columns else df
        elif event_type == "缩量":
            df = df[df["vol"] < df["vol"].quantile(0.25)] if "vol" in df.columns else df
        elif event_type == "炸板":
            df = df[(df["pct_chg"] > 0) & (df["pct_chg"] < 9)]
        elif event_type == "天地板":
            df = df[(df["pct_chg"] <= -9.5) | (df["pct_chg"] >= 9.5)]
        else:  # all
            df = df[(df["pct_chg"].abs() >= 5) | (df["pct_chg"] >= 9.5) | (df["pct_chg"] <= -9.5)]

        # Build result with event label
        result = []
        for _, row in df.head(100).iterrows():
            sym = str(row.get("symbol", ""))
            if sym.startswith("sh"):
                mkt, code = "sh", sym.replace("sh", "")
            elif sym.startswith("sz"):
                mkt, code = "sz", sym.replace("sz", "")
            elif sym.startswith("bj"):
                mkt, code = "bj", sym.replace("bj", "")
            else:
                mkt, code = "sz", sym
            pct = float(row.get("pct_chg", 0))
            if pct >= 9.9:
                event = "涨停"
            elif pct <= -9.9:
                event = "跌停"
            elif pct > 5:
                event = "放量上涨"
            elif pct < -5:
                event = "放量下跌"
            elif pct > 0:
                event = "上涨"
            else:
                event = "下跌"
            result.append({
                "code": code,
                "market": mkt,
                "time": "",
                "event": event,
                "change_pct": pct,
            })
        return result

    def _stock_top_board_sync(self, sort_by: str = "change_pct", direction: str = "desc", limit: int = 50, market: str = "all") -> list:
        """同步获取市场排行榜"""
        import pandas as pd

        VALID_SORT = {"change_pct", "amplitude", "turnover", "volume_ratio", "amount"}
        VALID_MARKET = {"all", "sh", "sz", "bj"}
        if sort_by not in VALID_SORT:
            sort_by = "change_pct"
        if market not in VALID_MARKET:
            market = "all"
        limit = min(limit, 200)

        q = self._get_quotes()
        all_quotes = []

        for exchange in ["sz", "sh"]:
            df_stock = q.stock(exchange=exchange)
            if df_stock is None or df_stock.empty:
                continue
            codes = df_stock["code"].tolist()
            prefixed = []
            for c in codes:
                cs = str(c)
                if cs.startswith(("60", "68")):
                    prefixed.append(f"sh{cs}")
                elif cs.startswith(("00", "30")):
                    prefixed.append(f"sz{cs}")
                elif cs.startswith(("8", "4")):
                    prefixed.append(f"bj{cs}")
                else:
                    prefixed.append(f"sz{cs}")
            for i in range(0, len(prefixed), MAX_LIMIT_BATCH_QUOTE):
                chunk = prefixed[i:i + MAX_LIMIT_BATCH_QUOTE]
                stripped = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in chunk]
                batch_df = q.quotes(symbols=stripped)
                if batch_df is not None and len(batch_df) > 0:
                    all_quotes.extend(batch_df.to_dict(orient="records"))

        if not all_quotes:
            return []

        df_quotes = pd.DataFrame(all_quotes)
        if df_quotes.empty:
            return []

        if market != "all":
            df_quotes = df_quotes[df_quotes["symbol"].str.startswith(market)]

        sort_col = sort_by if sort_by in df_quotes.columns else "pct_chg"
        if sort_col not in df_quotes.columns:
            sort_col = "pct_chg"
        ascending = direction == "asc"
        df_quotes = df_quotes.sort_values(sort_col, ascending=ascending).head(limit)

        result = []
        for _, row in df_quotes.iterrows():
            sym = str(row.get("symbol", ""))
            if sym.startswith("sh"):
                mkt, code = "sh", sym.replace("sh", "")
            elif sym.startswith("sz"):
                mkt, code = "sz", sym.replace("sz", "")
            elif sym.startswith("bj"):
                mkt, code = "bj", sym.replace("bj", "")
            else:
                mkt, code = "sz", sym
            result.append({
                "code": code,
                "market": mkt,
                "close": float(row.get("close", 0)),
                "change_pct": float(row.get("pct_chg", 0)),
                "volume": float(row.get("vol", 0)),
                "amount": float(row.get("amount", 0)),
                "turnover": float(row.get("turnover", 0)),
                "volume_ratio": float(row.get("volume_ratio", 0)),
            })
        return result

    async def stock_top_board(self, sort_by: str = "change_pct", direction: str = "desc", limit: int = 50, market: str = "all") -> ToolResult:
        """获取市场排行榜"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._stock_top_board_sync, sort_by, direction, limit, market)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"stock_top_board failed: {e}")
            return self._error_result(e, "stock_top_board")

    async def stock_unusual(self, event_type: str = "all") -> ToolResult:
        """获取市场异动数据（主力监控精灵）"""
        self._metrics["total_requests"] += 1
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._stock_unusual_sync, event_type)
            return ToolResult(success=True, data=data, source="mootdx2")
        except Exception as e:
            logger.error(f"stock_unusual failed: {e}")
            return self._error_result(e, "stock_unusual")
