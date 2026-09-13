"""股票代码标准化和数据规范化"""
import re


class Normalizer:
    """股票代码规范化"""

    # 市场代码映射
    MARKET_MAP = {
        "sh": "1",  # 上海
        "sz": "0",  # 深圳
        "bj": "8",  # 北京
    }

    @staticmethod
    def normalize_code(code: str) -> str:
        """将各种格式的股票代码标准化"""
        if not code:
            return code
        code = code.strip()
        code = re.sub(r'\s+', '', code)
        lower = code.lower()
        # 如果是 6 位纯数字，添加交易所前缀
        if re.match(r'^\d{6}$', code):
            if code.startswith(('0', '3')):
                return f"sz{code}"
            elif code.startswith(('6',)):
                return f"sh{code}"
            elif code.startswith(('8', '4')):
                return f"bj{code}"
        # 已有前缀（大小写均可），统一小写
        if re.match(r'^(sh|sz|bj)\d{6}$', lower):
            return lower
        return code

    @staticmethod
    def to_gateway_response(
        success: bool,
        data=None,
        error: str | None = None,
        source: str | None = None,
        hops: list = None,
    ) -> dict:
        """标准化网关响应格式"""
        return {
            "success": success,
            "data": data,
            "error": error,
            "source": source,
            "hops": hops or [],
        }
