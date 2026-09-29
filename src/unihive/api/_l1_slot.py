"""单工具 L1 缓存槽抽象 — 2026-09-28 Round-3 Batch 2 引入, 同日简化。

设计动机:
- 旧码把整 ``ToolResult`` 塞进缓存, 命中后 ``_with_warn`` 复用 ``result.data``
  同一引用, 下游修改污染缓存; ``error_detail`` 也跨请求共享, stale 风险。
- 本抽象对外只承诺缓存 **records** (list[dict] 或基础类型), ToolResult 每次
  重包, 引用自动隔离。

公共协议 (供 ``mootdx2_client`` 各 async wrapper 接入):
- ``get(key) -> records | None``: 命中 hits++, miss None 且 misses++
- ``put(key, records)``: 仅非空 records 写入 (防雪崩)
- ``clear()``: 全清, 供 console 控制
- ``__len__``: 状态查询

``cachetools`` 是 pyproject 硬依赖, 不做缺失降级 (旧 no-op 哨兵已删)。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from cachetools import TTLCache

logger = logging.getLogger(__name__)


class _L1Slot:
    """单工具 L1 缓存槽。线程不安全 (单 cache 进程内只服务一个 MooTDX2Client 实例)。

    Args:
        name: 槽名, 用于 metrics 标识 ("daily" / "index_kline" / "search" ...)
        maxsize: ``TTLCache.maxsize`` 条目上限, LRU 淘汰
        ttl: ``TTLCache.ttl`` 过期秒数, None=无TTL

    Notes:
        - ``put`` 仅非空 ``records`` 写入 (None / [] / 空 dict 不缓存, 防缓存雪崩)
        - ``hits``/``misses`` 计数器裸 ``+=`` (GIL 原子, 单线程 async 上下文足够)
    """

    __slots__ = ("_name", "_cache", "hits", "misses")

    def __init__(self, name: str, maxsize: int, ttl: Optional[float]) -> None:
        self._name = name
        self.hits = 0
        self.misses = 0
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        logger.debug(f"L1 slot {name!r} initialized: maxsize={maxsize}, ttl={ttl}")

    @property
    def name(self) -> str:
        return self._name

    def get(self, key: Any) -> Any:
        v = self._cache.get(key)
        if v is not None:
            self.hits += 1
            return v
        self.misses += 1
        return None

    def put(self, key: Any, records: Any) -> None:
        """仅非空 records 写入 (None / [] / 空 dict 不缓存)."""
        if not records:
            return
        self._cache[key] = records

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
