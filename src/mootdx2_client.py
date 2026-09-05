"""MooTDX2 MCP Client 基于 mootdx2 库"""
import asyncio
import logging
from dataclasses import dataclass

from .upstream_client import ToolResult, UpstreamStatus

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


@dataclass
class MooTDX2Config:
    """MooTDX2 配置"""
    name: str
    market: str = "std"


class MooTDX2Client:
    """MooTDX2 MCP 客户端"""

    def __init__(self, config: MooTDX2Config):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._quotes = None
        self._quotes_cls = None  # 延迟导入

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
        if df is not None and not df.empty:
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
        }

        method = method_map.get(tool_name)
        if not method:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

        return await method(**params)

    async def get_quote(self, code: str) -> ToolResult:
        """获取实时行情"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_quote_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_quote failed: {e}")
            return ToolResult(success=False, error=str(e))

    def _get_kline_sync(self, code: str, type: str = "day", limit: int = 100):
        """同步获取K线"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        freq = FREQ_MAP.get(type, 9)
        df = q.bars(symbol=sym, frequency=freq, offset=limit)
        if df is not None and not df.empty:
            if limit and limit > 0:
                df = df.tail(limit)
            return df.to_dict(orient="records")
        return None

    async def get_kline(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """获取K线"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_kline_sync, code, type, limit)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_kline failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== 批量行情 ==========
    def _get_batch_quote_sync(self, codes: str):
        """同步批量行情"""
        q = self._get_quotes()
        code_list = [c.strip().lower().replace("sh", "").replace("sz", "").replace("bj", "") for c in codes.split(",")]
        df = q.quotes(symbols=code_list)
        if df is not None and not df.empty:
            return df.to_dict(orient="records")
        return None

    async def get_batch_quote(self, codes: str) -> ToolResult:
        """获取批量行情"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_batch_quote_sync, codes)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_batch_quote failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== 今日分时 ==========
    def _get_minute_data_sync(self, code: str):
        """同步今日分时"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.minute(symbol=sym)
        if df is not None and not df.empty:
            return df.to_dict(orient="records")
        return None

    async def get_minute_data(self, code: str) -> ToolResult:
        """获取今日分时"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_minute_data_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_minute_data failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== 逐笔成交 ==========
    def _get_trade_sync(self, code: str):
        """同步逐笔成交"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.transaction(symbol=sym)
        if df is not None and not df.empty:
            return df.to_dict(orient="records")
        return None

    async def get_trade(self, code: str) -> ToolResult:
        """获取逐笔成交"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_trade_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_trade failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== 历史逐笔 ==========
    def _get_trade_history_sync(self, code: str, date: str, start: int, count: int):
        """同步历史逐笔"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.transactions(symbol=sym, date=date, start=start, offset=count)
        if df is not None and not df.empty:
            return df.to_dict(orient="records")
        return None

    async def get_trade_history(self, code: str, date: str, start: int = 0, count: int = 100) -> ToolResult:
        """获取历史逐笔"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_trade_history_sync, code, date, start, count)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_trade_history failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== 指数K线 ==========
    def _get_index_kline_sync(self, code: str, type: str):
        """同步指数K线"""
        q = self._get_quotes()
        freq = FREQ_MAP.get(type, 9)
        df = q.index(symbol=code, frequency=freq)
        if df is not None and not df.empty:
            return df.to_dict(orient="records")
        return None

    async def get_index_kline(self, code: str, type: str = "day") -> ToolResult:
        """获取指数K线"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_index_kline_sync, code, type)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_index_kline failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== 全量代码列表 ==========
    def _get_code_list_sync(self, exchange: str):
        """同步全量代码列表"""
        q = self._get_quotes()
        df = q.stock(exchange=exchange)
        if df is not None and not df.empty:
            return df.to_dict(orient="records")
        return None

    async def get_code_list(self, exchange: str) -> ToolResult:
        """获取全量代码列表"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_code_list_sync, exchange)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_code_list failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== 股票代码列表 ==========
    def _get_stock_codes_sync(self, limit: int, prefix: bool):
        """同步股票代码列表"""
        q = self._get_quotes()
        df = q.stock(exchange="sz")
        if df is not None and not df.empty:
            codes = df["code"].tolist()[:limit]
            if prefix:
                return [f"sz{c}" for c in codes]
            return codes
        return None

    async def get_stock_codes(self, limit: int = 100, prefix: bool = False) -> ToolResult:
        """获取股票代码列表"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_stock_codes_sync, limit, prefix)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_stock_codes failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== ETF代码列表 ==========
    def _get_etf_codes_sync(self, limit: int, prefix: bool):
        """同步ETF代码列表"""
        q = self._get_quotes()
        df = q.etf(exchange="sz")
        if df is not None and not df.empty:
            codes = df["code"].tolist()[:limit]
            if prefix:
                return [f"sz{c}" for c in codes]
            return codes
        return None

    async def get_etf_codes(self, limit: int = 100, prefix: bool = False) -> ToolResult:
        """获取ETF代码列表"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_etf_codes_sync, limit, prefix)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_etf_codes failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== ETF列表 ==========
    def _get_etf_list_sync(self, exchange: str, limit: int):
        """同步ETF列表"""
        q = self._get_quotes()
        df = q.etf(exchange=exchange)
        if df is not None and not df.empty:
            result = df.head(limit).to_dict(orient="records")
            return result
        return None

    async def get_etf_list(self, exchange: str = "sz", limit: int = 100) -> ToolResult:
        """获取ETF列表"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_etf_list_sync, exchange, limit)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_etf_list failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== 市场证券数量 ==========
    def _get_market_count_sync(self, market: int):
        """同步市场证券数量"""
        q = self._get_quotes()
        count = q.stock_count(market=market)
        return {"market": market, "count": count}

    async def get_market_count(self, market: int = 0) -> ToolResult:
        """获取市场证券数量"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_market_count_sync, market)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_market_count failed: {e}")
            return ToolResult(success=False, error=str(e))

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
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_workday_sync, date, count)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_workday failed: {e}")
            return ToolResult(success=False, error=str(e))

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
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_workday_range_sync, start, end)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_workday_range failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== 全指K线 ==========
    def _get_index_all_sync(self, code: str, type: str, limit: int):
        """同步全指K线"""
        q = self._get_quotes()
        freq = FREQ_MAP.get(type, 9)
        df = q.index(symbol=code, frequency=freq)
        if df is not None and not df.empty:
            if limit and limit > 0:
                df = df.tail(limit)
            return df.to_dict(orient="records")
        return None

    async def get_index_all(self, code: str, type: str = "day", limit: int = 100) -> ToolResult:
        """获取全指K线"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_index_all_sync, code, type, limit)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_index_all failed: {e}")
            return ToolResult(success=False, error=str(e))

    # ========== 盘后收益 ==========
    def _get_income_sync(self, code: str):
        """同步盘后收益"""
        q = self._get_quotes()
        sym = code.lower().replace("sh", "").replace("sz", "").replace("bj", "")
        df = q.income(symbol=sym)
        if df is not None and not df.empty:
            return df.to_dict(orient="records")
        return None

    async def get_income(self, code: str, days: int = 30, start_date: str = "") -> ToolResult:
        """获取盘后收益"""
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self._get_income_sync, code)
            if data:
                return ToolResult(success=True, data=data, source="mootdx2")
            return ToolResult(success=False, error="No data")
        except Exception as e:
            logger.error(f"get_income failed: {e}")
            return ToolResult(success=False, error=str(e))
