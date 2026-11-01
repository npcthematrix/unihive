#!/usr/bin/env python3
"""TQ (tqcenter) 能力探测 — 判断本地客户端属于哪一代、缺哪些接口。

为什么要这个脚本:
  上游 tqcenter 随通达信客户端走, 没有独立的版本号可读 (TPythClient.dll 的
  FileVersion 是空的, tqcenter.py 里也没有 __version__)。唯一可靠的判据是
  **函数集合** —— 官方更新日志按版本列出了新增函数, 拿本地函数表去比对,
  就能反推版本代际, 并直接回答"能不能用批量接口"。

两种模式:
  静态 (默认)  AST 解析 tqcenter.py 源码, 不 import、不加载 DLL、不建连。
              网关运行期间随时可跑, 零副作用。
  动态 (--live) 真 import + initialize + 实测吞吐。会占用一个 TQ 策略实例,
              **请先停网关**, 否则可能与网关注册的实例冲突
              (官方文档: 同一位置的 py 文件不能同时运行)。

用法:
  python scripts/probe_tq_capabilities.py
  python scripts/probe_tq_capabilities.py --tdx-root D:/new_tdx_mock
  python scripts/probe_tq_capabilities.py --live

退出码:
  0  静态检查完成 / 动态检查全部通过
  1  tqcenter.py 找不到或解析失败
  2  动态模式下 initialize 失败
"""
from __future__ import annotations

import argparse
import ast
import sys
import time
from pathlib import Path
from typing import Any

# 官方更新日志里各版本"新增"的函数 —— 用它们反推版本代际。
# 顺序有意义: 从旧到新, 逐代看是否全部命中。
VERSION_MILESTONES: list[tuple[str, str, tuple[str, ...]]] = [
    ("1.2.x", "2026-05-22", ("get_pricevol", "formula_get_all", "formula_get_info")),
    ("1.2.2", "2026-09-30", ("get_sector_list_ds", "get_domain_sorthq")),
    ("1.3.0", "2026-10-16", ("get_stock_mark", "formula_by_src", "formula_by_file")),
    ("1.3.1", "2026-10-23", ("get_misc_data",)),
    ("1.3.2", "2026-10-30", ("del_user_block", "get_exmin_data")),
]

# 批量 / 高吞吐接口: 能把 N 次单只调用压成 1 次, 直接绕过 DLL 的单连接吞吐上限。
BATCH_APIS: tuple[tuple[str, str], ...] = (
    ("get_market_snapshot_batch", "批量快照 (03_14)"),
    ("get_more_info_batch", "批量更多信息 (03_16)"),
    ("get_call_auction_batch", "批量集合竞价 (12_05)"),
    ("get_tick_data", "分笔行情 (TdxAiData)"),
    ("get_minute_data", "指定日分时 (TdxAiData)"),
    ("get_global_data", "市场整体情况 (TdxAiData)"),
)

# 订阅 / 缓存类, 与并发策略相关。
MISC_APIS: tuple[tuple[str, str], ...] = (
    ("subscribe_hq", "实时行情订阅 (上限 100 只)"),
    ("refresh_cache", "刷新行情缓存"),
    ("refresh_kline", "刷新 K 线缓存"),
)


def _resolve_tdx_root(explicit: str | None) -> Path:
    """--tdx-root > config/upstreams.yaml 的 tdx_quant.tdx_root。"""
    if explicit:
        return Path(explicit)
    cfg = Path(__file__).resolve().parents[1] / "config" / "upstreams.yaml"
    if cfg.is_file():
        try:
            import yaml  # noqa: PLC0415 — 可选依赖, 缺了就走下面的报错

            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            root = (data.get("upstreams") or {}).get("tdx_quant", {}).get("tdx_root")
            if root:
                return Path(root)
        except Exception:
            pass
    raise SystemExit(
        "无法确定 tdx_root: 请用 --tdx-root 指定, 或确认 config/upstreams.yaml "
        "里 upstreams.tdx_quant.tdx_root 存在"
    )


def _tqcenter_path(tdx_root: Path) -> Path:
    """客户端里 TQ 支撑文件的位置。

    解析顺序与 ``models/tdx_quant_config.py::tqcenter_dir`` 保持一致:
    先 ``PYPlugins/sys``, 不存在才回落到 ``PYPlugins/user``。网关实际 import
    的是前者, 所以探测也必须读同一个文件 —— 两者在新版里可能不同步。
    """
    sys_dir = tdx_root / "PYPlugins" / "sys" / "tqcenter.py"
    if sys_dir.is_file():
        return sys_dir
    return tdx_root / "PYPlugins" / "user" / "tqcenter.py"


def _parse_methods(source: str) -> set[str]:
    """AST 提取 tqcenter.py 里所有 def 名字 (类方法和模块函数都收)。

    用 AST 而不是 import: import 会执行模块体, 模块体里有
    ``ctypes.CDLL(TPythClient.dll)``, 会在网关运行期间多加载一份 DLL。
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
    return names


def _mark(ok: bool) -> str:
    return "[有]" if ok else "[无]"


def static_report(tdx_root: Path) -> int:
    path = _tqcenter_path(tdx_root)
    if not path.is_file():
        print(f"找不到 tqcenter.py: {path}", file=sys.stderr)
        return 1
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        methods = _parse_methods(source)
    except SyntaxError as exc:
        print(f"解析 tqcenter.py 失败: {exc}", file=sys.stderr)
        return 1

    st = path.stat()
    print("== 安装信息 ==")
    print(f"  tqcenter.py : {path}")
    print(f"  大小 / 修改 : {st.st_size / 1024:.1f} KB / "
          f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(st.st_mtime))}")
    dll = path.parents[1] / "TPythClient.dll"
    if dll.is_file():
        d = dll.stat()
        print(f"  TPythClient : {d.st_size / 1024:.0f} KB / "
              f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(d.st_mtime))}")
    print(f"  函数总数    : {len(methods)}")

    print("\n== 版本代际 (按官方更新日志的新增函数反推) ==")
    inferred = "早于 1.2.x"
    for version, date, funcs in VERSION_MILESTONES:
        hits = [f for f in funcs if f in methods]
        state = "命中" if len(hits) == len(funcs) else f"缺 {len(funcs) - len(hits)}/{len(funcs)}"
        print(f"  {version:<6} ({date})  {state}")
        if len(hits) == len(funcs):
            inferred = version
    print(f"  → 推断本地为 {inferred} 代")

    print("\n== 批量 / 高吞吐接口 ==")
    for name, desc in BATCH_APIS:
        print(f"  {_mark(name in methods)} {name:<28} {desc}")

    print("\n== 订阅 / 缓存接口 ==")
    for name, desc in MISC_APIS:
        print(f"  {_mark(name in methods)} {name:<28} {desc}")

    missing_batch = [n for n, _ in BATCH_APIS if n not in methods]
    if missing_batch:
        print(f"\n结论: 缺 {len(missing_batch)} 个批量接口, "
              f"当前只能按单只调用 —— 每次调用都要独占一次 DLL 往返。")
        print("      要拿到批量能力需升级客户端到 1.3.x (内测版 / 量化模拟版)。")
    else:
        print("\n结论: 批量接口齐全, 可以把 N 次单只调用合并成 1 次。")
    return 0


def live_report(tdx_root: Path) -> int:
    """真连一次并实测吞吐。会占用一个 TQ 策略实例, 跑前请停网关。

    股票池从 ``get_stock_list('5')`` (所有 A 股) 现取, 不用写死的测试代码 ——
    600001/600002 这类代码不存在, 会一路返回空结果, 测出来的耗时全是错误路径。
    """
    # sys.path 顺序与网关一致: sys 优先, 回落 user
    user_dir = _tqcenter_path(tdx_root).parent
    if str(user_dir) not in sys.path:
        sys.path.insert(0, str(user_dir))
    try:
        from tqcenter import tq  # noqa: PLC0415
    except Exception as exc:
        print(f"import tqcenter 失败: {exc}", file=sys.stderr)
        return 1

    # 传本文件路径而不是 tdx_root: 让探针成为独立的策略实例, 不与网关撞名。
    try:
        tq.initialize(__file__)
    except Exception as exc:
        print(f"initialize 失败: {exc}", file=sys.stderr)
        return 2
    print("initialize 成功\n")

    pool = list(tq.get_stock_list("5") or [])
    if not pool:
        print("get_stock_list('5') 返回空 —— 客户端可能未登录或行情未就绪",
              file=sys.stderr)
        return 2
    print(f"股票池: {len(pool)} 只 (示例 {pool[:3]})\n")

    has_batch = hasattr(tq, "get_market_snapshot_batch")
    print(f"{'数量':>5}  {'单只循环':>10}  {'批量一次':>10}  {'提速':>7}  说明")
    print("-" * 62)
    for n in (8, 50, 200):
        codes = pool[:n]

        t0 = time.perf_counter()
        singles = [tq.get_market_snapshot(stock_code=c) for c in codes]
        single_ms = (time.perf_counter() - t0) * 1000
        ok_single = sum(1 for r in singles if r)

        if not has_batch:
            print(f"{n:>5}  {single_ms:>9.0f}ms  {'—':>10}  {'—':>7}  "
                  f"成功 {ok_single}/{n}")
            continue

        t0 = time.perf_counter()
        batch = tq.get_market_snapshot_batch(stock_list=codes)
        batch_ms = (time.perf_counter() - t0) * 1000
        ok_batch = len(batch) if isinstance(batch, dict) else 0

        # 批量若大面积失败, 提速比毫无意义, 单独标注出来
        if ok_batch < n * 0.9:
            note = f"批量仅 {ok_batch}/{n} 有数据, 提速比不可信"
            ratio = "—"
        else:
            note = f"成功 {ok_single}/{n} vs {ok_batch}/{n}"
            ratio = f"{single_ms / batch_ms:.1f}x" if batch_ms > 0 else "—"
        print(f"{n:>5}  {single_ms:>9.0f}ms  {batch_ms:>9.0f}ms  "
              f"{ratio:>7}  {note}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="探测本地 TQ (tqcenter) 的能力与版本代际")
    ap.add_argument("--tdx-root", help="通达信安装根目录 (默认读 config/upstreams.yaml)")
    ap.add_argument("--live", action="store_true",
                    help="真连一次并实测吞吐 (需先停网关)")
    args = ap.parse_args()

    tdx_root = _resolve_tdx_root(args.tdx_root)
    if args.live:
        return live_report(tdx_root)
    return static_report(tdx_root)


if __name__ == "__main__":
    sys.exit(main())
