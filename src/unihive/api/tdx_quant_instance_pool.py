"""多个 TQ 策略实例 (各一进程, 各一个 run_id)。

父进程不加载 TPythClient.dll。超时可以杀掉卡住的子进程再拉起来,
这是线程池做不到的 (asyncio.wait_for 杀不掉已进 DLL 的线程)。
"""
from __future__ import annotations

import logging
import multiprocessing as mp
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..models.tdx_quant_config import MAX_STRATEGY_INSTANCES
from .tdx_quant_worker import worker_main

logger = logging.getLogger(__name__)

_MAX_INSTANCES = MAX_STRATEGY_INSTANCES
_READY_TIMEOUT_SEC = 60.0


class TdxQuantPoolBusy(Exception):
    """所有策略实例都在忙, 新请求应快速失败。"""


class TdxQuantPoolTimeout(TimeoutError):
    """某个策略实例在 timeout 内没有返回; 该实例已被杀掉并重建。"""


@dataclass
class _Worker:
    index: int
    strategy_path: str
    proc: Any = None
    conn: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class TdxQuantInstancePool:
    """N 个 spawn 子进程, 每个 initialize 一个独立 .py 策略路径。"""

    def __init__(
        self,
        *,
        n: int,
        tqcenter_dir: str,
        strategy_paths: list[str],
        ready_timeout_sec: float = _READY_TIMEOUT_SEC,
    ) -> None:
        if n < 1:
            raise ValueError("strategy_instances must be >= 1")
        if n > _MAX_INSTANCES:
            raise ValueError(f"strategy_instances cap is {_MAX_INSTANCES}")
        if len(strategy_paths) != n:
            raise ValueError("strategy_paths length must equal n")
        self._n = n
        self._tqcenter_dir = tqcenter_dir
        self._strategy_paths = list(strategy_paths)
        self._ready_timeout_sec = ready_timeout_sec
        self._ctx = mp.get_context("spawn")
        self._workers: list[_Worker] = [
            _Worker(index=i, strategy_path=strategy_paths[i]) for i in range(n)
        ]

    @property
    def size(self) -> int:
        return self._n

    def snapshot(self) -> dict[str, Any]:
        rows = []
        for w in self._workers:
            alive = bool(w.proc is not None and w.proc.is_alive())
            rows.append({
                "index": w.index,
                "pid": int(w.proc.pid) if alive and w.proc is not None else None,
                "alive": alive,
                "busy": w.lock.locked(),
                "strategy_path": w.strategy_path,
            })
        return {"instances": rows, "size": self._n}

    def start(self) -> None:
        errors: list[str] = []
        for w in self._workers:
            try:
                self._spawn(w)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"instance {w.index}: {exc}")
        if errors:
            self.stop()
            raise RuntimeError("; ".join(errors))

    def stop(self) -> None:
        for w in self._workers:
            self._teardown(w)

    def restart(self) -> None:
        self.stop()
        self.start()

    def call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float,
        sticky: bool = False,
        acquire_timeout: float = 0.05,
    ) -> Any:
        """同步调用。sticky=True 固定打到 instance 0 (公式会话 / 交易)。"""
        idx = self._acquire(sticky=sticky, wait=acquire_timeout)
        w = self._workers[idx]
        try:
            if w.conn is None or w.proc is None or not w.proc.is_alive():
                self._spawn(w)
            w.conn.send({"op": "call", "method": method, "params": params or {}})
            if not w.conn.poll(timeout):
                logger.warning(
                    "tdx_quant instance %s (%s) timed out on %s after %.1fs; respawning",
                    w.index, w.strategy_path, method, timeout,
                )
                self._spawn(w)
                raise TdxQuantPoolTimeout(
                    f"strategy instance {w.index} timed out on {method}"
                )
            reply = w.conn.recv()
            if not reply or not reply.get("ok"):
                raise RuntimeError(reply.get("error") if reply else "empty reply")
            return reply.get("data")
        finally:
            w.lock.release()

    def _acquire(self, *, sticky: bool, wait: float) -> int:
        if sticky:
            if not self._workers[0].lock.acquire(timeout=wait):
                raise TdxQuantPoolBusy("sticky instance 0 is busy")
            return 0
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            for w in self._workers:
                if w.lock.acquire(blocking=False):
                    return w.index
            if time.monotonic() >= deadline:
                raise TdxQuantPoolBusy("all strategy instances busy")
            time.sleep(0.005)

    def _spawn(self, w: _Worker) -> None:
        self._teardown(w)
        parent_conn, child_conn = self._ctx.Pipe()
        spec = {
            "tqcenter_dir": self._tqcenter_dir,
            "strategy_path": w.strategy_path,
        }
        proc = self._ctx.Process(
            target=worker_main,
            args=(child_conn, spec),
            name=f"unihive-tdx-quant-{w.index}",
            daemon=True,
        )
        proc.start()
        child_conn.close()
        w.proc = proc
        w.conn = parent_conn
        if not parent_conn.poll(self._ready_timeout_sec):
            self._teardown(w)
            raise TimeoutError(
                f"strategy instance {w.index} did not become ready in "
                f"{self._ready_timeout_sec:.0f}s"
            )
        reply = parent_conn.recv()
        if not reply or not reply.get("ok"):
            err = (reply or {}).get("error") or "initialize failed"
            self._teardown(w)
            raise RuntimeError(f"instance {w.index} initialize: {err}")

    def _teardown(self, w: _Worker) -> None:
        conn, proc = w.conn, w.proc
        w.conn = None
        w.proc = None
        if conn is not None:
            try:
                if proc is not None and proc.is_alive():
                    conn.send({"op": "stop"})
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        if proc is not None:
            proc.join(timeout=2.0)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=1.0)


def write_strategy_files(base_dir: Path, strategy_id: str, n: int) -> list[str]:
    """为每个实例写一个独立 .py, 作为 tq.initialize(path) 的策略身份。

    initialize 文档用 ``__file__`` 当策略键; 网关以前传入 tdx 安装目录,
    多实例会撞 ErrorId=12 同名策略。各写一个空策略文件可避开。
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in strategy_id) or "gw"
    for i in range(n):
        path = base_dir / f"{safe_id}_{i}.py"
        if not path.exists():
            path.write_text(
                f"# unihive tdx_quant strategy instance {i} ({safe_id})\n",
                encoding="utf-8",
            )
        paths.append(str(path.resolve()))
    return paths
