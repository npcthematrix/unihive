"""mootdx2 ``StdReader.find_path`` 运行时补丁：补齐 bj（北交所）支持。

补丁背景 (2026-09-20, mootdx2 1.5.4 实测源码):
    通达信标准本地布局三个市场的文件名都带市场前缀::

        vipdoc/sh/lday/sh600000.day
        vipdoc/sz/lday/sz000001.day
        vipdoc/bj/lday/bj430047.day   # 北交所股票
        vipdoc/bj/lday/bj899050.day   # 北证指数
        vipdoc/bj/minline/bj430047.lc1

    但上游 ``find_path`` 只对 sh/sz 补市场前缀，bj 分支既不识别显式
    ``bj`` 前缀（``get_stock_market`` 只认 sh/sz，未知前缀默认 sh，
    拼出 ``shbj430047`` 垃圾路径），也不给裸码补 ``bj`` 前缀
    （找 ``vipdoc/bj/lday/430047.day``，与实际落盘命名不符），导致
    全部北交所标的（含 899 北证指数）离线接口端到端不可读。
    此外 ``get_stock_market`` 的 ``('5','6','9','7') → sh`` 兜底把
    北交所 2024 新号段 920xxx 误判到 sh。

补丁策略（不改第三方库源码，实例级 MethodType 绑定，幂等）：
    - 显式 sh/sz/bj 前缀：始终信任
    - 裸码：88x 按上游约定归 sh（板块指数），920xxx 归 bj，
      其余委托 ``get_stock_market``（4/8 开头 → bj 等）
    - 三个市场统一按 ``{market}{code}`` 拼文件名
    - ``#`` 扩展市场（ds）与 ``debug=True`` 行为保持与上游一致
"""
from __future__ import annotations

from pathlib import Path
from types import MethodType

_PATCH_FLAG = "_unihive_bj_patch"
_EXPLICIT_MARKETS = ("sh", "sz", "bj")


def _infer_market(code: str) -> str:
    """裸码推断市场（修正上游两个 bj 缺口后委托 get_stock_market）。"""
    # 与上游 find_path 原始的 startswith('88') 特判一致：
    # 880/881 通达信板块指数文件落在 vipdoc/sh/
    if code.startswith("88"):
        return "sh"
    # 北交所 2024 启用的 920 号段：上游 9→sh 兜底会误判
    if code.startswith("920"):
        return "bj"
    from mootdx2.utils import get_stock_market

    return get_stock_market(code, True)


def _find_path_patched(
    self, symbol=None, subdir: str = "lday", suffix=None, **kwargs
):
    """替换 mootdx2 ``StdReader.find_path``（签名与返回契约保持一致）。"""
    raw = str(symbol or "").strip().lower()

    # 扩展市场（如 77#... 大盘 / 板块扩展码）保持该实例所属类的上游原逻辑
    if "#" in raw:
        original = _ORIGINAL_FIND_PATH.get(type(self))
        if original is None:
            return None
        return original(self, symbol, subdir, suffix, **kwargs)

    if raw[:2] in _EXPLICIT_MARKETS:
        market = raw[:2]
        code = raw[2:]
    else:
        code = raw
        market = _infer_market(code)

    # 标准 TDX 布局：三个市场的文件名均带市场前缀
    file_symbol = f"{market}{code}"

    suffixes = suffix if isinstance(suffix, list) else [suffix]

    if kwargs.get("debug"):
        return market, file_symbol, suffixes

    for ex_ in suffixes:
        vipdoc = (
            Path(self.tdxdir)
            / "vipdoc"
            / market
            / subdir
            / f"{file_symbol}.{str(ex_).strip('.')}"
        )
        if vipdoc.exists():
            return vipdoc
    return None


# 补丁函数引用的各 Reader 子类的上游原始实现（patch 时按类捕获，
# 避免循环 import mootdx2；按类保存以防未来出现第二种 Reader 子类）
_ORIGINAL_FIND_PATH: dict[type, object] = {}


def patch_reader_bj_paths(reader) -> None:
    """给 Reader 实例打 bj 路径补丁（幂等；线程安全由调用方单例锁保证）。"""
    if getattr(reader, _PATCH_FLAG, False):
        return
    # 能力检测：只有类上带 find_path 的 StdReader 才需要/可以补丁。
    # 裸 object()/MagicMock 等替身没有该方法 → 静默跳过
    # （区分"不是 Reader"与"实例级 find_path 覆盖"：只认类级方法）。
    cls = type(reader)
    original = getattr(cls, "find_path", None)
    if original is None:
        return
    _ORIGINAL_FIND_PATH.setdefault(cls, original)
    reader.find_path = MethodType(_find_path_patched, reader)
    setattr(reader, _PATCH_FLAG, True)


__all__ = ["patch_reader_bj_paths"]
