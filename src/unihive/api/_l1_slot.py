"""单工具 L1 缓存槽抽象 — 2026-09-28 Round-3 Batch 2 引入。

设计动机:
- 离线接口 12 个工具里, 只有 ``get_daily_local`` 有 L1; 其余 8 个单 symbol
  工具 (``get_minute``/``get_fzline``/``get_index_kline_local``/``get_index_minute_local``/
  ``get_index_fzline_local``/``search_stock``/``indicator_atr``/``_scan_blk_files_sync``)
  全部 0 缓存, 高频分析场景重复触发 Reader 加载, 浪费 10-200ms / call。
- 旧码把整 ``ToolResult`` 塞进 ``_top_board_l1`` / ``_unusual_l1``,
  命中后 ``_with_warn`` 复用 ``result.data`` 同一引用, 下游修改污染缓存;
  ``error_detail`` 也跨请求共享, stale 风险。
- 本抽象对外只承诺缓存 **records** (list[dict] 或基础类型), ToolResult 每次
  重包, 引用自动隔离。

公共协议 (供 ``mootdx2_client`` 各 async wrapper 接入):
- ``get(key) -> records | None``: 命中 hits++, miss None 且 misses++
- ``put(key, records)``: 仅非空 records 写入 (防雪崩)
- ``clear()``: 全清, 供 console 控制
- ``enabled`` / ``__len__``: 状态查询

``cachetools`` 缺失时降级为 no-op 哨兵 (不抛, 业务侧用 ``slot.enabled``
判断或直接走原始 sync 路径, 二者行为一致)。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class _L1Slot:
    """单工具 L1 缓存槽。线程不安全 (单 cache 进程内只服务一个 MooTDX2Client 实例)。

    Args:
        name: 槽名, 用于 metrics 标识 ("daily" / "minute" / "search" ...)
        maxsize: ``TTLCache.maxsize`` 条目上限, LRU 淘汰
        ttl: ``TTLCache.ttl`` 过期秒数, None=无TTL

    Notes:
        - ``cachetools`` 缺失时 ``enabled=False``, get/put/clear 全 no-op
        - ``put`` 仅非空 ``records`` 写入 (None / [] / 空 dict 不缓存, 防缓存雪崩)
        - ``hits``/``misses`` 计数器裸 ``+=`` (GIL 原子, 单线程 async 上下文足够)
    """

    __slots__ = ("_name", "_cache", "_enabled", "hits", "misses")

    def __init__(self, name: str, maxsize: int, ttl: Optional[float]) -> None:
        self._name = name
        self.hits = 0
        self.misses = 0
        try:
            from cachetools import TTLCache  # type: ignore[import-not-found]
            # maxsize ≤ 0 表示"不启" (cachetools 会拒绝, 提前短路)
            if maxsize <= 0:
                self._cache: Any = None
                self._enabled = False
                logger.debug(f"L1 slot {name!r} disabled (maxsize={maxsize})")
                return
            self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
            self._enabled = True
            logger.debug(
                f"L1 slot {name!r} enabled: maxsize={maxsize}, ttl={ttl}"
            )
        except ImportError:
            self._cache = None
            self._enabled = False
            logger.warning(
                f"cachetools not installed, L1 slot {name!r} disabled"
            )

    @property
    def name(self) -> str:
        return self._name

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get(self, key: Any) -> Any:
        if not self._enabled:
            return None
        v = self._cache.get(key)
        if v is not None:
            self.hits += 1
            return v
        self.misses += 1
        return None

    def put(self, key: Any, records: Any) -> None:
        """仅非空 records 写入 (None / [] / 空 dict 不缓存)."""
        if not self._enabled:
            return
        if not records:
            return
        self._cache[key] = records

    def clear(self) -> None:
        if self._enabled:
            self._cache.clear()

    def __len__(self) -> int:
        if not self._enabled:
            return 0
        return len(self._cache)