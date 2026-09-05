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
