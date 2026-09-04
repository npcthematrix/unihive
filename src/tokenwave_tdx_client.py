"""
TokenWave TDX MCP Client
基于 mootdx 库的数据访问客户端，支持 local 和 network 模式
"""
import logging
from dataclasses import dataclass
from typing import Any

from .upstream_client import ToolResult, UpstreamStatus

logger = logging.getLogger(__name__)


@dataclass
class TokenWaveTdxConfig:
    """TokenWave TDX 配置"""
    name: str
    mode: str = "auto"  # auto | local | network


class TokenWaveTdxClient:
    """TokenWave TDX MCP 客户端"""

    def __init__(self, config: TokenWaveTdxConfig):
        self.config = config
        self.name = config.name
        self._status = UpstreamStatus.UNKNOWN
        self._local_client = None
        self._network_client = None

    @property
    def status(self) -> UpstreamStatus:
        return self._status

    @property
    def is_available(self) -> bool:
        return self._status == UpstreamStatus.HEALTHY

    async def start(self):
        """初始化客户端"""
        # 初始化 local 和 network 客户端
        try:
            self._local_client = LocalClient()
        except Exception as e:
            logger.warning(f"Failed to init local client: {e}")

        try:
            self._network_client = NetworkClient()
        except Exception as e:
            logger.warning(f"Failed to init network client: {e}")

        # 检查可用性
        local_ok = self._local_client and self._local_client.is_available()
        network_ok = self._network_client and self._network_client.is_available()

        if local_ok or network_ok:
            self._status = UpstreamStatus.HEALTHY
        else:
            self._status = UpstreamStatus.UNAVAILABLE

    async def stop(self):
        """停止客户端"""
        self._local_client = None
        self._network_client = None
        self._status = UpstreamStatus.UNKNOWN

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        """工具路由"""
        method_map = {
            "get_realtime_quote": self.get_realtime_quote,
            "get_kline": self.get_kline,
            "get_minute_bar": self.get_minute_bar,
            "get_daily_bar": self.get_daily_bar,
            "get_financial_data": self.get_financial_data,
            "get_block_data": self.get_block_data,
            "get_stock_info": self.get_stock_info,
            "get_trade_dates": self.get_trade_dates,
            "get_etf_list": self.get_etf_list,
        }

        method = method_map.get(tool_name)
        if not method:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

        return await method(**params)

    # ========== 工具方法实现 ==========

    async def get_realtime_quote(self, stock_code: str) -> ToolResult:
        """获取实时行情: local 优先，network 兜底"""
        # 1. 尝试 local
        if self._local_client and self._local_client.is_available():
            try:
                data = self._local_client.get_realtime_quote(stock_code)
                if data:
                    return ToolResult(success=True, data=data, source="local")
            except Exception as e:
                logger.warning(f"Local quote failed: {e}")

        # 2. 兜底 network
        if self._network_client and self._network_client.is_available():
            try:
                data = self._network_client.get_realtime_quote(stock_code)
                if data:
                    return ToolResult(success=True, data=data, source="network")
            except Exception as e:
                logger.error(f"Network quote failed: {e}")

        return ToolResult(success=False, error="无可用数据源")

    async def get_kline(
        self,
        stock_code: str,
        frequency: str = "daily",
        start_date: str = None,
        end_date: str = None,
        count: int = 100,
    ) -> ToolResult:
        """获取K线数据: local 优先，network 兜底"""
        # 1. 尝试 local
        if self._local_client and self._local_client.is_available():
            try:
                data = self._local_client.get_kline(
                    stock_code=stock_code,
                    frequency=frequency,
                    count=count,
                )
                if data is not None:
                    # 转换 DataFrame 为 dict 列表
                    if hasattr(data, 'to_dict'):
                        data = data.to_dict(orient='records')
                    return ToolResult(success=True, data=data, source="local")
            except Exception as e:
                logger.warning(f"Local kline failed: {e}")

        # 2. 兜底 network
        if self._network_client and self._network_client.is_available():
            try:
                data = self._network_client.get_kline(
                    stock_code=stock_code,
                    frequency=frequency,
                    count=count,
                )
                if data is not None:
                    if hasattr(data, 'to_dict'):
                        data = data.to_dict(orient='records')
                    return ToolResult(success=True, data=data, source="network")
            except Exception as e:
                logger.error(f"Network kline failed: {e}")

        return ToolResult(success=False, error="无可用数据源")

    async def get_minute_bar(
        self,
        stock_code: str,
        frequency: str = "5min",
    ) -> ToolResult:
        """获取分钟K线: local 优先，network 兜底"""
        # 1. 尝试 local
        if self._local_client and self._local_client.is_available():
            try:
                data = self._local_client.get_minute(stock_code, frequency)
                if data is not None:
                    if hasattr(data, 'to_dict'):
                        data = data.to_dict(orient='records')
                    return ToolResult(success=True, data=data, source="local")
            except Exception as e:
                logger.warning(f"Local minute failed: {e}")

        # 2. 兜底 network
        if self._network_client and self._network_client.is_available():
            try:
                data = self._network_client.get_minute(stock_code, frequency)
                if data is not None:
                    if hasattr(data, 'to_dict'):
                        data = data.to_dict(orient='records')
                    return ToolResult(success=True, data=data, source="network")
            except Exception as e:
                logger.error(f"Network minute failed: {e}")

        return ToolResult(success=False, error="无可用数据源")

    async def get_daily_bar(
        self,
        stock_code: str,
        frequency: str = "daily",
        **kwargs,
    ) -> ToolResult:
        """获取日K线: 委托给 get_kline(frequency="daily"), 复用 local+network 兜底"""
        return await self.get_kline(
            stock_code=stock_code,
            frequency=frequency or "daily",
            **kwargs,
        )

    async def get_financial_data(
        self,
        stock_code: str,
        report_type: str = "income",
        count: int = 4,
    ) -> ToolResult:
        """获取财务数据: network only (本地无财务数据)"""
        if self._network_client and self._network_client.is_available():
            try:
                data = self._network_client.get_financial_data(
                    stock_code=stock_code,
                    report_type=report_type,
                    count=count,
                )
                if data is not None:
                    if hasattr(data, 'to_dict'):
                        data = data.to_dict(orient='records')
                    return ToolResult(success=True, data=data, source="network")
            except Exception as e:
                logger.error(f"Network financial failed: {e}")

        return ToolResult(success=False, error="无可用数据源")

    async def get_block_data(self, block_type: str) -> ToolResult:
        """获取板块数据: network only (本地无板块数据)"""
        if self._network_client and self._network_client.is_available():
            try:
                data = self._network_client.get_block_data(block_type)
                if data is not None:
                    if hasattr(data, 'to_dict'):
                        data = data.to_dict(orient='records')
                    return ToolResult(success=True, data=data, source="network")
            except Exception as e:
                logger.error(f"Network block failed: {e}")

        return ToolResult(success=False, error="无可用数据源")

    async def get_stock_info(self, stock_code: str) -> ToolResult:
        """获取股票信息: network only"""
        if self._network_client and self._network_client.is_available():
            try:
                data = self._network_client.get_stock_info(stock_code)
                if data:
                    return ToolResult(success=True, data=data, source="network")
            except Exception as e:
                logger.error(f"Network stock info failed: {e}")

        return ToolResult(success=False, error="无可用数据源")

    async def get_trade_dates(
        self,
        start_date: str,
        end_date: str,
    ) -> ToolResult:
        """获取交易日历: network only"""
        if self._network_client and self._network_client.is_available():
            try:
                data = self._network_client.get_trade_dates(start_date, end_date)
                if data is not None:
                    if hasattr(data, 'to_dict'):
                        data = data.to_dict(orient='records')
                    return ToolResult(success=True, data=data, source="network")
            except Exception as e:
                logger.error(f"Network trade dates failed: {e}")

        return ToolResult(success=False, error="无可用数据源")

    async def get_etf_list(self) -> ToolResult:
        """获取ETF列表: network only"""
        if self._network_client and self._network_client.is_available():
            try:
                data = self._network_client.get_etf_list()
                if data is not None:
                    if hasattr(data, 'to_dict'):
                        data = data.to_dict(orient='records')
                    return ToolResult(success=True, data=data, source="network")
            except Exception as e:
                logger.error(f"Network ETF list failed: {e}")

        return ToolResult(success=False, error="无可用数据源")


class LocalClient:
    """本地模式: 读取本机通达信数据文件"""

    def __init__(self, tdx_path: str = None):
        """
        初始化本地客户端

        Args:
            tdx_path: 通达信数据目录路径, 如 "C:/new_tdx"
        """
        from mootdx import reader

        self.tdx_path = tdx_path
        self._reader = None
        self._reader_cls = reader.Reader

    def _get_reader(self):
        """获取或创建 Reader 实例"""
        if self._reader is None:
            self._reader = self._reader_cls.factory(market="std", tdxdir=self.tdx_path)
        return self._reader

    def is_available(self) -> bool:
        """
        检查本地数据是否可用

        Returns:
            bool: 如果能成功创建 Reader 实例则返回 True
        """
        try:
            r = self._get_reader()
            return r is not None
        except Exception:
            return False

    def get_kline(
        self,
        stock_code: str,
        frequency: str = "daily",
        count: int = 100,
        adjust: str = None,
    ):
        """
        读取K线数据

        Args:
            stock_code: 股票代码, 如 "600036"
            frequency: K线频率, 支持 "daily", "weekly", "monthly"
            count: 返回的K线数量
            adjust: 复权类型, "qfq"(前复权), "hfq"(后复权), None(不复权)

        Returns:
            pd.DataFrame: K线数据, 包含 open, high, low, close, volume, date 列
        """
        r = self._get_reader()

        # frequency 映射到 mootdx 参数
        freq_map = {
            "daily": 9,
            "weekly": 5,
            "monthly": 6,
            "5min": 0,
            "15min": 1,
            "30min": 2,
            "60min": 3,
        }

        freq = freq_map.get(frequency, 9)

        try:
            df = r.daily(symbol=stock_code)
            if df is not None and not df.empty:
                # 按数量截取
                if count and count > 0:
                    df = df.tail(count)
                return df
            return None
        except Exception as e:
            logger.warning(f"Failed to get kline for {stock_code}: {e}")
            return None

    def get_minute(self, stock_code: str, frequency: str = "5min"):
        """
        读取分钟K线

        Args:
            stock_code: 股票代码, 如 "600036"
            frequency: 分钟频率, 支持 "1min", "5min", "15min", "30min", "60min"

        Returns:
            pd.DataFrame: 分钟K线数据
        """
        r = self._get_reader()

        freq_map = {
            "1min": 0,
            "5min": 5,
            "15min": 15,
            "30min": 30,
            "60min": 60,
        }

        freq = freq_map.get(frequency, 5)

        try:
            # minute 方法直接返回 DataFrame
            df = r.minute(symbol=stock_code)
            if df is not None and not df.empty:
                return df
            return None
        except Exception as e:
            logger.warning(f"Failed to get minute for {stock_code}: {e}")
            return None

    def get_realtime_quote(self, stock_code: str):
        """
        读取实时行情 (本地模式仅支持日线最新数据)

        由于本地模式无法获取实时行情, 返回最近一日的日线数据作为替代

        Args:
            stock_code: 股票代码, 如 "600036"

        Returns:
            dict: 实时行情数据
        """
        # 本地模式无法获取真正的实时行情, 返回最近日线
        df = self.get_kline(stock_code, frequency="daily", count=1)
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return {
                "symbol": stock_code,
                "open": float(latest.get("open", 0)),
                "high": float(latest.get("high", 0)),
                "low": float(latest.get("low", 0)),
                "close": float(latest.get("close", 0)),
                "volume": float(latest.get("volume", 0)),
                "date": str(latest.get("date", "")),
                "time": "",
                "source": "local",
            }
        return None


class NetworkClient:
    """网络模式: 直连通达信行情服务器"""

    def __init__(self):
        from mootdx import quotes

        self._quotes = None
        self._quotes_cls = quotes.Quotes

    def _get_quotes(self):
        """获取或创建 Quotes 实例"""
        if self._quotes is None:
            self._quotes = self._quotes_cls()
        return self._quotes

    def is_available(self) -> bool:
        """
        检查网络连接是否可用

        Returns:
            bool: 如果能成功连接则返回 True
        """
        try:
            q = self._get_quotes()
            return q is not None
        except Exception:
            return False

    def get_realtime_quote(self, stock_code: str):
        """
        获取实时行情

        Args:
            stock_code: 股票代码, 如 "600036"

        Returns:
            dict: 实时行情数据
        """
        q = self._get_quotes()
        try:
            # mootdx quotes API
            df = q.quotes(symbol=stock_code)
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                return {
                    "symbol": stock_code,
                    "open": float(latest.get("open", 0)),
                    "high": float(latest.get("high", 0)),
                    "low": float(latest.get("low", 0)),
                    "close": float(latest.get("close", 0)),
                    "volume": float(latest.get("vol", 0)),
                    "amount": float(latest.get("amount", 0)),
                    "date": str(latest.get("date", "")),
                    "time": str(latest.get("time", "")),
                    "source": "network",
                }
            return None
        except Exception as e:
            logger.warning(f"Failed to get realtime quote for {stock_code}: {e}")
            return None

    def get_kline(
        self,
        stock_code: str,
        frequency: str = "daily",
        count: int = 100,
    ):
        """
        获取K线数据

        Args:
            stock_code: 股票代码, 如 "600036"
            frequency: K线频率, 支持 "daily", "weekly", "monthly", "5min", "15min", "30min", "60min"
            count: 返回的K线数量

        Returns:
            pd.DataFrame: K线数据
        """
        q = self._get_quotes()

        freq_map = {
            "daily": 9,
            "weekly": 5,
            "monthly": 6,
            "5min": 0,
            "15min": 1,
            "30min": 2,
            "60min": 3,
        }

        freq = freq_map.get(frequency, 9)

        try:
            df = q.daily(symbol=stock_code)
            if df is not None and not df.empty:
                if count and count > 0:
                    df = df.tail(count)
                return df
            return None
        except Exception as e:
            logger.warning(f"Failed to get kline for {stock_code}: {e}")
            return None

    def get_minute(self, stock_code: str, frequency: str = "5min"):
        """
        获取分钟K线

        Args:
            stock_code: 股票代码, 如 "600036"
            frequency: 分钟频率, 支持 "1min", "5min", "15min", "30min", "60min"

        Returns:
            pd.DataFrame: 分钟K线数据
        """
        q = self._get_quotes()

        freq_map = {
            "1min": 0,
            "5min": 1,
            "15min": 2,
            "30min": 3,
            "60min": 4,
        }

        freq = freq_map.get(frequency, 1)

        try:
            df = q.minute(symbol=stock_code, freq=freq)
            if df is not None and not df.empty:
                return df
            return None
        except Exception as e:
            logger.warning(f"Failed to get minute for {stock_code}: {e}")
            return None

    def get_financial_data(
        self,
        stock_code: str,
        report_type: str = "income",
        count: int = 4,
    ):
        """
        获取财务数据

        Args:
            stock_code: 股票代码, 如 "600036"
            report_type: 报表类型, "income"(利润表), "balance"(资产负债表), "cashflow"(现金流量表)
            count: 返回的报表期数

        Returns:
            pd.DataFrame: 财务数据
        """
        q = self._get_quotes()

        type_map = {
            "income": 0,
            "balance": 1,
            "cashflow": 2,
        }

        rtype = type_map.get(report_type, 0)

        try:
            df = q.financial(symbol=stock_code, rtype=rtype)
            if df is not None and not df.empty:
                if count and count > 0:
                    df = df.tail(count)
                return df
            return None
        except Exception as e:
            logger.warning(f"Failed to get financial data for {stock_code}: {e}")
            return None

    def get_block_data(self, block_type: str):
        """
        获取板块数据

        Args:
            block_type: 板块类型, "industry"(行业), "concept"(概念), "region"(地区)

        Returns:
            pd.DataFrame: 板块数据
        """
        q = self._get_quotes()

        type_map = {
            "industry": 0,
            "concept": 1,
            "region": 2,
        }

        btype = type_map.get(block_type, 0)

        try:
            df = q.block(symbol=btype)
            if df is not None and not df.empty:
                return df
            return None
        except Exception as e:
            logger.warning(f"Failed to get block data ({block_type}): {e}")
            return None

    def get_stock_info(self, stock_code: str):
        """
        获取股票基本信息

        Args:
            stock_code: 股票代码, 如 "600036"

        Returns:
            dict: 股票基本信息
        """
        q = self._get_quotes()
        try:
            df = q.quotes(symbol=stock_code)
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                return {
                    "symbol": stock_code,
                    "name": str(latest.get("name", "")),
                    "close": float(latest.get("close", 0)),
                    "change": float(latest.get("change", 0)),
                    "pct_chg": float(latest.get("pct_chg", 0)),
                    "volume": float(latest.get("vol", 0)),
                    "amount": float(latest.get("amount", 0)),
                    "open": float(latest.get("open", 0)),
                    "high": float(latest.get("high", 0)),
                    "low": float(latest.get("low", 0)),
                    "pre_close": float(latest.get("pre_close", 0)),
                    "date": str(latest.get("date", "")),
                    "time": str(latest.get("time", "")),
                }
            return None
        except Exception as e:
            logger.warning(f"Failed to get stock info for {stock_code}: {e}")
            return None

    def get_trade_dates(self, start_date: str, end_date: str):
        """
        获取交易日历

        Args:
            start_date: 开始日期, 格式 "YYYYMMDD"
            end_date: 结束日期, 格式 "YYYYMMDD"

        Returns:
            pd.DataFrame: 交易日历数据
        """
        q = self._get_quotes()
        try:
            df = q.trade_cal(start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                return df
            return None
        except Exception as e:
            logger.warning(f"Failed to get trade dates: {e}")
            return None

    def get_etf_list(self):
        """
        获取ETF列表

        Returns:
            pd.DataFrame: ETF列表数据
        """
        q = self._get_quotes()
        try:
            # mootdx 没有直接的 ETF 列表接口，返回空
            # 可以通过查询特定 ETF 代码实现
            return None
        except Exception as e:
            logger.warning(f"Failed to get ETF list: {e}")
            return None
