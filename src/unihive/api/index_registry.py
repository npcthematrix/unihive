"""代码 → (市场, 类型) 解析工具（无枚举白名单）。

设计背景 (2026-09-20 重构，移除旧 ``INDEX_MARKET_MAP``):
  旧实现用一张 ``code -> market`` 的枚举表判定"代码是不是指数"。A 股存在
  跨市场同代码标的（``000001`` = 上证指数 / 平安银行，``000002`` = 上证A指 /
  万科A），单值表被迫二选一，且把"显式后缀与表冲突"一律报错，导致
  ``sz000001`` 在离线接口里永远读不到平安银行。

  新实现只依赖通达信 vipdoc 目录的**通用前缀约定**（规律，不是逐个代码的
  白名单），市场归属由用户显式后缀或调用入口的意图决定：

  市场已知时，(市场, 前缀) 判定类型无歧义：
    - sh + 000xxx/880xxx/881xxx/930-932xxx → 指数
                          （上证/中证系列、板块指数、中证新号段）
    - sz + 399xxx       → 指数（深证系列）
    - bj + 899xxx       → 指数（北证指数）
    - 其余              → 股票/基金等，按股票处理

  无后缀裸码：
    - 399xxx/899xxx/880xxx/881xxx/930-932xxx → 指数，市场由前缀直接确定
    - 000xxx               → **歧义码**（深市股票 vs 上证/中证指数）：
        * 指数工具入口（intent='index'）→ 按 sh 指数解析
        * 默认（股票/通用入口）         → 按股票，market=None
          交 ``Reader.find_path`` 前缀推断（000 → sz），命中深市股票
        * 需要在通用入口读上证指数 → 显式传 ``sh000001`` / ``000001.SH``
    - 其余                 → 股票，market=None 交 Reader 推断

3 层结构:
    1. ``ParsedSymbol``: 不可变解析结果 DTO
    2. ``parse_symbol``: 统一入口，处理 .SH 后缀 / 旧前缀 / 纯数字
    3. ``to_reader_symbol``: 转换 Reader 需要的 symbol 形态
"""
from __future__ import annotations

from dataclasses import dataclass

# ---- 常量 --------------------------------------------------------------------

INDEX_KIND = "index"
STOCK_KIND = "stock"
VALID_SUFFIXES: frozenset[str] = frozenset({"sh", "sz", "bj"})

#: 市场已知时的指数代码前缀（通达信目录通用约定，非枚举白名单）
#: sh: 000=上证/中证老系列; 880/881=通达信板块指数（标准目录均有落盘）;
#: 930-932=中证新号段（932000 中证A500、930/931 行业/主题系列，文件由
#: 通达信下载到 vipdoc/sh/lday/，本机未下载时返回 no_data）。
#: sz: 399 深证系列；bj: 899 北证系列。
_INDEX_PREFIXES_BY_MARKET: dict[str, tuple[str, ...]] = {
    "sh": ("000", "880", "881", "930", "931", "932"),
    "sz": ("399",),
    "bj": ("899",),
}

#: 无后缀裸码下无歧义指数前缀 → 市场（由主表派生；"000" 除外 ——
#: 000xxx 在 sh（指数）和 sz（股票）下同时存在，必须交给 intent 消歧）
_BARE_INDEX_MARKET_BY_PREFIX: tuple[tuple[str, str], ...] = tuple(
    (prefix, market)
    for market in ("sh", "sz", "bj")
    for prefix in _INDEX_PREFIXES_BY_MARKET.get(market, ())
    if prefix != "000"
)


# ---- DTO ---------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedSymbol:
    """统一解析结果（immutable DTO）。"""
    code: str           # 6 位纯数字代码
    market: str | None  # sh / sz / bj；股票 + 无后缀 时为 None，让 Reader 自行推断
    kind: str           # 'index' / 'stock'
    had_suffix: bool    # 解析时是否带 .SH/.SZ/.BJ 后缀或旧 sh/sz 前缀


# ---- 核心函数 -----------------------------------------------------------------

def _kind_for_market(code: str, market: str) -> str:
    """市场已知时按前缀约定判定类型。"""
    prefixes = _INDEX_PREFIXES_BY_MARKET.get(market, ())
    return INDEX_KIND if code.startswith(prefixes) else STOCK_KIND


def parse_symbol(raw: str | None, *, intent: str = STOCK_KIND) -> ParsedSymbol:
    """统一解析入口。

    支持格式:
        - ``"600036"``: 6 位纯数字，按前缀约定/调用意图决定 kind
        - ``"sh600036"``: 旧前缀格式，市场以前缀为准（始终信任）
        - ``"000300.SH"`` / ``"000300.sh"``: 后缀格式（仅 .SH/.SZ/.BJ）

    Args:
        raw: 用户输入的代码字符串
        intent: 调用入口意图（仅影响 000xxx 裸歧义码）：
            - ``STOCK_KIND``（默认，通用/股票入口）: 000xxx 裸码按深市股票
            - ``INDEX_KIND``（指数工具入口）: 000xxx 裸码按 sh 指数

    Returns:
        ``ParsedSymbol`` 不可变 DTO

    Raises:
        ValueError: 输入为空 / 主体非 6 位数字 / 后缀非法 / intent 非法
    """
    if intent not in (INDEX_KIND, STOCK_KIND):
        raise ValueError(f"intent 仅支持 {INDEX_KIND!r}/{STOCK_KIND!r}，收到 {intent!r}")
    if raw is None:
        raise ValueError("symbol 不能为空")
    s = raw.strip()
    if not s:
        raise ValueError("symbol 不能为空")

    market_hint: str | None = None
    had_suffix = False
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

    # 显式后缀/前缀：市场以用户输入为准，按 (市场, 前缀) 约定判定类型。
    # 不再做"与白名单冲突"检测 —— 同代码跨市场标的是合法存在的。
    if had_suffix:
        return ParsedSymbol(
            code=code,
            market=market_hint,
            kind=_kind_for_market(code, market_hint),
            had_suffix=True,
        )

    # 无后缀：无歧义指数前缀
    for prefix, market in _BARE_INDEX_MARKET_BY_PREFIX:
        if code.startswith(prefix):
            return ParsedSymbol(
                code=code, market=market, kind=INDEX_KIND, had_suffix=False,
            )

    # 无后缀 000xxx：唯一歧义段，按调用入口意图解析
    if code.startswith("000"):
        if intent == INDEX_KIND:
            return ParsedSymbol(
                code=code, market="sh", kind=INDEX_KIND, had_suffix=False,
            )
        return ParsedSymbol(
            code=code, market=None, kind=STOCK_KIND, had_suffix=False,
        )

    # 其余裸码按股票，market=None 交 Reader.find_path 前缀推断
    return ParsedSymbol(
        code=code, market=None, kind=STOCK_KIND, had_suffix=False,
    )


def to_reader_symbol(parsed: ParsedSymbol) -> str:
    """转换为 mootdx2 ``Reader`` 需要的 symbol 形态。

    - 市场已知（指数 / 显式后缀股票）→ ``"sh000300"`` / ``"sz000001"``
    - 市场未知（裸码股票）→ ``"600036"``，交 ``Reader.find_path`` 前缀推断
    """
    return f"{parsed.market}{parsed.code}" if parsed.market else parsed.code


__all__ = [
    "INDEX_KIND",
    "STOCK_KIND",
    "VALID_SUFFIXES",
    "ParsedSymbol",
    "parse_symbol",
    "to_reader_symbol",
]
