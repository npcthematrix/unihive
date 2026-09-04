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
        return self._status == UpstreamStatus.AVAILABLE

    async def start(self):
        """初始化客户端"""
        # 初始化 local 和 network 客户端
        pass

    async def stop(self):
        """停止客户端"""
        pass

    async def call_tool(self, tool_name: str, params: dict) -> ToolResult:
        """调用工具"""
        pass


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
