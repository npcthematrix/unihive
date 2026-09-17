"""指数代码 → 市场索引表 + 解析工具。

2026-09-14 audit (round 11):
  mootdx2 ``Reader.find_path`` 内部用 ``get_stock_market`` 按代码前缀推断
  市场（``00/30/39 → sz``、``6/5/9/7 → sh``），会把 SH 宽基指数
  （000300 / 000905 / 000016 等）错放到 ``vipdoc/sz/lday/``。

  本模块在调用 Reader 前强制走显式映射，把指数走查表、个股走前缀推断
  两条路径彻底分开；同时拒绝"前缀兜底猜测" —— 未登记的 6 位代码直接
  ``raise ValueError``，由外层 ToolResult 转 ``invalid_symbol``。

  设计原则 (round 11 user review):
    - 宁可少不可猜：新指数必须先登记到 ``INDEX_MARKET_MAP`` 再使用。
    - 后缀冲突（如 ``000300.SZ``）一律 raise，禁止静默接受。
    - 不依赖代码位数/开头自动推断交易所归属 —— 指数归属是"人为规定"
      的，不是编码规律。

3 层结构:
    1. ``INDEX_MARKET_MAP``: 唯一权威表
    2. ``parse_symbol``: 统一入口，处理 .SH 后缀 / 旧前缀 / 纯数字
    3. ``classify_symbol``: 便捷接口，返回 ``(kind, reader_symbol)``
"""
from __future__ import annotations

from dataclasses import dataclass

# ---- 常量 --------------------------------------------------------------------

INDEX_KIND = "index"
STOCK_KIND = "stock"
VALID_SUFFIXES: frozenset[str] = frozenset({"sh", "sz", "bj"})

# 指数代码 → 交易所归属（sh/sz/bj），本地 vipdoc 目录布局
# 维护规则：只覆盖项目实际会调到的指数；新指数必须先登记再使用
# 表格按代码数值升序排列，方便人工核对
INDEX_MARKET_MAP: dict[str, str] = {
    # SH 宽基 / 规模指数（上交所 / 中证发布，按 SH 目录存）
    "000001": "sh",   # 上证指数
    "000002": "sh",   # 上证 A 股
    "000003": "sh",   # 上证 B 股
    "000016": "sh",   # 上证 50
    "000300": "sh",   # 沪深 300
    "000688": "sh",   # 科创 50
    "000852": "sh",   # 中证 1000
    "000903": "sh",   # 中证 100
    "000905": "sh",   # 中证 500
    # SZ 综指 / 成分指数
    "399001": "sz",   # 深证成指
    "399002": "sz",   # 深成指 R
    "399006": "sz",   # 创业板指
    "399330": "sz",   # 深证 100
    "399905": "sz",   # 中证 500 (SZ 镜像)
    # BJ
    "899999": "bj",   # 北证 50（占位，按实际下载情况再补）
}


# ---- DTO ---------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedSymbol:
    """统一解析结果（immutable DTO）。"""
    code: str           # 6 位纯数字代码
    market: str | None  # sh / sz / bj；stock + 无后缀 时为 None，让 Reader 自行推断
    kind: str           # 'index' / 'stock'
    had_suffix: bool    # 解析时是否带 .SH/.SZ/.BJ 后缀或旧 sh/sz 前缀


# ---- 核心函数 -----------------------------------------------------------------

def resolve_index_market(code: str) -> str:
    """查表返回指数市场。表外直接抛 ``ValueError``（**禁止前缀兜底**）。

    Args:
        code: 6 位纯数字代码

    Returns:
        'sh' / 'sz' / 'bj'

    Raises:
        ValueError: code 不在 ``INDEX_MARKET_MAP`` 中
    """
    code = (code or "").strip()
    if code not in INDEX_MARKET_MAP:
        raise ValueError(
            f"指数代码 {code!r} 不在 INDEX_MARKET_MAP 中，"
            f"请补充映射或确认是否为股票代码（禁止前缀兜底猜测）"
        )
    return INDEX_MARKET_MAP[code]


def parse_symbol(raw: str | None) -> ParsedSymbol:
    """统一解析入口。

    支持格式:
        - ``"600036"``: 6 位纯数字，按 ``INDEX_MARKET_MAP`` 决定 kind
        - ``"sh600036"``: 旧前缀格式，按前缀得 market，kind 走查表
        - ``"000300.SH"`` / ``"000300.sh"``: 后缀格式（仅 .SH/.SZ/.BJ）

    Args:
        raw: 用户输入的代码字符串

    Returns:
        ``ParsedSymbol`` 不可变 DTO

    Raises:
        ValueError: 输入为空 / 主体非 6 位数字 / 后缀非法 / 后缀与表冲突
    """
    if raw is None:
        raise ValueError("symbol 不能为空")
    s = raw.strip()
    if not s:
        raise ValueError("symbol 不能为空")

    had_suffix = False
    market_hint: str | None = None
    body = s

    # 1) 后缀 ".SH/.SZ/.BJ"（大小写均可）
    if "." in s:
        head, _, tail = s.rpartition(".")
        tail_lower = tail.lower()
        if tail_lower not in VALID_SUFFIXES:
            raise ValueError(
                f"symbol 后缀 .{tail} 无效，仅支持 .SH/.SZ/.BJ"
            )
        body = head.strip()
        market_hint = tail_lower
        had_suffix = True
    # 2) 旧前缀 "sh600036"
    elif s[:2].lower() in VALID_SUFFIXES:
        market_hint = s[:2].lower()
        body = s[2:]
        had_suffix = True

    # body 现在应该是 6 位数字
    if not (body.isdigit() and len(body) == 6):
        raise ValueError(
            f"symbol {raw!r} 解析后主体 {body!r} 不是 6 位数字"
        )

    code = body
    in_index_map = code in INDEX_MARKET_MAP

    if had_suffix:
        # 用户显式给了交易所线索：
        #   - 命中指数表 → 以表为准，**冲突检测**
        #   - 未命中 → 当作股票，信任用户后缀（不做前缀推断）
        if in_index_map:
            expected = INDEX_MARKET_MAP[code]
            if market_hint != expected:
                raise ValueError(
                    f"指数 {code} 后缀 .{market_hint.upper()} "
                    f"与 INDEX_MARKET_MAP 记录 .{expected.upper()} 冲突"
                )
            return ParsedSymbol(
                code=code, market=expected,
                kind=INDEX_KIND, had_suffix=True,
            )
        return ParsedSymbol(
            code=code, market=market_hint,
            kind=STOCK_KIND, had_suffix=True,
        )

    # 无后缀：先看是否在指数表
    if in_index_map:
        return ParsedSymbol(
            code=code, market=INDEX_MARKET_MAP[code],
            kind=INDEX_KIND, had_suffix=False,
        )
    # 6 位纯数字但不在指数表 → 视为股票；market=None 让 Reader 自行推断
    return ParsedSymbol(
        code=code, market=None,
        kind=STOCK_KIND, had_suffix=False,
    )


def classify_symbol(raw: str) -> tuple[str, str]:
    """便捷接口：返回 ``(kind, reader_symbol)``。

    - ``kind='index'`` → ``reader_symbol`` 形如 ``"sh000300"``
      （market + code 拼接，Reader 直接命中 ``vipdoc/sh/lday/sh000300.day``）
    - ``kind='stock'`` → ``reader_symbol`` 形如 ``"600036"``
      （不带前缀，让 ``Reader.find_path`` 按前缀推断）

    Args:
        raw: 用户输入

    Returns:
        ``(kind, reader_symbol)``

    Raises:
        ValueError: 透传 ``parse_symbol`` 的所有错误
    """
    p = parse_symbol(raw)
    if p.kind == INDEX_KIND:
        return p.kind, f"{p.market}{p.code}"
    return p.kind, p.code


__all__ = [
    "INDEX_KIND",
    "STOCK_KIND",
    "VALID_SUFFIXES",
    "INDEX_MARKET_MAP",
    "ParsedSymbol",
    "resolve_index_market",
    "parse_symbol",
    "classify_symbol",
]