"""tqcenter 策略子进程入口。

官方 1.3.0 起允许同一个 py 同时跑多个实例, 但 tqcenter.tq 是**类级单例**
(一份 run_id / _initialized)。同一解释器里无法挂第二条 DLL 连接, 必须
每个策略实例一个 OS 进程, 各自 initialize 一次。

本模块必须是顶层可 pickle 的 spawn target (Windows 默认 spawn)。
"""
from __future__ import annotations

import sys
import traceback
from typing import Any


def worker_main(conn: Any, spec: dict[str, Any]) -> None:
    """子进程主循环: initialize → recv call/stop → getattr(tq, method)(**params)。"""
    tqcenter_dir = str(spec.get("tqcenter_dir") or "")
    strategy_path = str(spec.get("strategy_path") or "")
    if tqcenter_dir and tqcenter_dir not in sys.path:
        sys.path.insert(0, tqcenter_dir)
    tq = None
    try:
        import tqcenter  # noqa: PLC0415 — 子进程内才加载 DLL

        tq = tqcenter.tq
        tq.initialize(strategy_path)
        conn.send({"ok": True, "event": "ready"})
    except Exception as exc:  # noqa: BLE001 — 必须回报父进程, 不能让 spawn 默默挂
        try:
            conn.send({
                "ok": False,
                "event": "ready",
                "error": str(exc),
                "tb": traceback.format_exc(),
            })
        except Exception:
            pass
        return

    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            break
        if not msg or msg.get("op") == "stop":
            break
        if msg.get("op") != "call":
            try:
                conn.send({"ok": False, "error": f"unknown op {msg.get('op')!r}"})
            except Exception:
                break
            continue
        method = msg.get("method") or ""
        params = msg.get("params") or {}
        try:
            result = getattr(tq, method)(**params)
            conn.send({"ok": True, "data": result})
        except Exception as exc:  # noqa: BLE001
            try:
                conn.send({
                    "ok": False,
                    "error": str(exc),
                    "tb": traceback.format_exc(),
                })
            except Exception:
                break
    if tq is not None:
        try:
            tq.close()
        except Exception:
            pass
