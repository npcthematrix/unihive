"""股票代码标准化和数据规范化"""
import re

# 港股代码固定为 5 位（不足左侧补零），如 700 → 00700
HK_CODE_LEN = 5


def _pad_hk_code(digits: str) -> str:
    return digits.zfill(HK_CODE_LEN)


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
        """将各种格式的股票代码标准化。

        A 股：6 位数字按号段补 sh/sz/bj 前缀；已带前缀则归一为小写。
        港股：1~5 位数字，支持 THS 后缀格式 ``00700.HK`` 与通达信扩展行情
        前缀格式 ``hk00700``，统一输出 ``hk`` + 5 位补零代码；1~5 位裸码
        也按港股处理（6 位裸码仍是 A 股）。
        """
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
        # 港股：1~5 位数字 + .HK 后缀（THS/fuyao 格式）
        m = re.match(r'^(\d{1,5})\.hk$', lower)
        if m:
            return f"hk{_pad_hk_code(m.group(1))}"
        # 港股：已带 hk 前缀，代码补零并统一小写
        m = re.match(r'^hk(\d{1,5})$', lower)
        if m:
            return f"hk{_pad_hk_code(m.group(1))}"
        # 港股：1~5 位裸码（6 位裸码在上方按 A 股处理）
        if re.match(r'^\d{1,5}$', code):
            return f"hk{_pad_hk_code(code)}"
        # 已有 A 股前缀（大小写均可），统一小写
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
        warn: str | None = None,
        error_kind: str | None = None,
    ) -> dict:
        """标准化网关响应格式。

        ``warn`` 为成功路径的非阻断提示（消歧/no_data/truncated 等），
        为 None 时不出现在信封中，保持既有消费者看到的字段集合不变。
        ``error_kind`` 为全链失败时的网关级错误类别 (仅失败路径)。
        """
        resp = {
            "success": success,
            "data": data,
            "error": error,
            "source": source,
            "hops": hops or [],
        }
        if warn is not None:
            resp["warn"] = warn
        if error_kind is not None:
            resp["error_kind"] = error_kind
        return resp
