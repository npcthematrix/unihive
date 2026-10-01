"""港股代码 → 扩展行情离线文件 token 解析（无枚举白名单）。

通达信把港股等"扩展市场"数据放在与 A 股不同的目录，且日线记录格式不同：

    {tdxdir}/vipdoc/ds/lday/{market}#{token}.day

其中 market 是扩展市场编号：
    - ``31`` 港股个股（token 为 5 位数字，如 00700 = 腾讯控股）
    - ``27`` 港股 / 中华交易服务指数（token 为字母开头符号，如 HSI 恒生指数、
      CES120 中华120、HZ5014 等跨市场/板块指数）

A 股（parse_symbol）始终是 6 位数字、走 vipdoc/{sh,sz,bj}，与本模块互斥：
    - 港股个股接收 1~5 位数字（裸码即按港股，6 位才是 A 股）
    - 港股指数 token 必须字母开头（市场 27 无纯数字指数文件）

支持的输入写法：
    个股: 00700.HK / hk00700 / HK00700 / 00700 / 700.HK（左侧补零到 5 位）
    指数: HSI.HK / hkHSI / CES120.HK / hkCES120
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HK_PREFIX = "hk"
HK_SUFFIX = ".hk"

#: 扩展市场编号（ds/lday 文件名前缀）
HK_STOCK_MARKET = "31"
HK_INDEX_MARKET = "27"

#: ds/lday 相对 tdxdir 的路径段
HK_DAY_PATH_SEGMENTS: tuple[str, ...] = ("vipdoc", "ds", "lday")

HK_STOCK_CODE_LEN = 5

#: 市场 27 指数 token：字母开头、字母数字 1~10 位（HSI / CES120 / HZ5014）
HK_INDEX_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,9}$")


@dataclass(frozen=True)
class ParsedHk:
    """港股解析结果（immutable DTO）。"""

    token: str          # 文件内 token：个股=5 位数字 "00700"；指数=大写 "HSI"
    market: str         # "31"（个股）/ "27"（指数）
    key: str            # 结果 dict / L1 键："hk00700" / "hkHSI"
    filename: str       # "31#00700.day" / "27#HSI.day"
    is_index: bool


def _split_hk_wrapper(raw: str) -> tuple[str, bool]:
    """剥 ``hk`` 前缀 / ``.HK`` 后缀，返回 (inner, had_wrapper)。

    无港股指派包装时返回 (去空白原文, False)，交调用方按裸码规则判定。
    """
    s = str(raw).strip()
    low = s.lower()
    if low.endswith(HK_SUFFIX) and len(s) > len(HK_SUFFIX):
        return s[: -len(HK_SUFFIX)].strip(), True
    if low.startswith(HK_PREFIX) and len(s) > len(HK_PREFIX):
        return s[len(HK_PREFIX):].strip(), True
    return s, False


def parse_hk_stock(raw: str | None) -> ParsedHk | None:
    """解析港股个股；非港股个股返回 ``None``（让调用方回退 A 股解析）。

    - 显式 ``hk``/``.HK`` 包装 + 1~5 位数字 → 港股个股
    - 裸 1~5 位数字 → 港股个股（A 股严格 6 位，故无歧义）
    - 显式包装但主体非数字（如 ``hkHSI``）→ ValueError：那是港股指数

    Raises:
        ValueError: 显式港股指派但主体不是 1~5 位数字
    """
    if raw is None:
        return None
    inner, wrapped = _split_hk_wrapper(raw)
    if not inner:
        return None

    is_stock_code = inner.isdigit() and 1 <= len(inner) <= HK_STOCK_CODE_LEN
    if not is_stock_code:
        if wrapped:
            raise ValueError(
                f"港股代码 {raw!r} 主体 {inner!r} 不是 1~5 位数字；"
                f"港股指数（如 HSI）请在指数工具中查询"
            )
        return None

    code = inner.zfill(HK_STOCK_CODE_LEN)
    return ParsedHk(
        token=code,
        market=HK_STOCK_MARKET,
        key=f"hk{code}",
        filename=f"{HK_STOCK_MARKET}#{code}.day",
        is_index=False,
    )


def parse_hk_index(raw: str | None) -> ParsedHk | None:
    """解析港股 / 中华交易指数；非港股指数返回 ``None``。

    - 显式 ``hk``/``.HK`` 包装 + 字母开头 token（HSI / CES120 / HZ5014）→ 港股指数
    - 显式包装但主体为数字（如 ``00700.HK``）→ ValueError：那是港股个股
    - 无包装裸码 → 返回 None（不与 A 股裸码争抢）

    Raises:
        ValueError: 显式港股指派但 token 不被市场 27 接受
    """
    if raw is None:
        return None
    inner, wrapped = _split_hk_wrapper(raw)
    if not wrapped or not inner:
        return None

    if inner.isdigit():
        raise ValueError(
            f"{raw!r} 是港股个股代码（数字）；港股个股日K请使用 get_daily_local"
        )
    if not HK_INDEX_TOKEN_RE.match(inner):
        raise ValueError(
            f"港股指数代码 {raw!r} 主体 {inner!r} 非法，需字母开头的 1~10 位"
            f"字母数字（如 HSI / CES120 / HZ5014）"
        )

    token = inner.upper()
    return ParsedHk(
        token=token,
        market=HK_INDEX_MARKET,
        key=f"hk{token}",
        filename=f"{HK_INDEX_MARKET}#{token}.day",
        is_index=True,
    )


def hk_day_path_segments(parsed: ParsedHk) -> tuple[str, ...]:
    """返回 ds/lday 下含文件名的完整相对路径段。"""
    return (*HK_DAY_PATH_SEGMENTS, parsed.filename)


__all__ = [
    "HK_STOCK_MARKET",
    "HK_INDEX_MARKET",
    "HK_DAY_PATH_SEGMENTS",
    "HK_STOCK_CODE_LEN",
    "ParsedHk",
    "parse_hk_stock",
    "parse_hk_index",
    "hk_day_path_segments",
]
