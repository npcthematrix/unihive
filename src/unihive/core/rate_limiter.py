"""异步令牌桶限流器。

fuyao / thsdk 等互联网接口通常有调用配额限制 (QPS 或 QPD)。
短时间高并发会触发上游 429/限流，导致正常请求失败。

令牌桶算法:
- 桶容量 = burst (允许瞬时突发)
- 持续补充速率 = rate (req/sec)，线性填充
- acquire() 时若桶有令牌则扣减返回 True，否则 await 到下次补充。

设计要点:
- 完全异步，无锁 (单 asyncio 进程内 dict 操作天然原子)
- 失败时 acquire() 返回 False，由调用方决定重试/降级
- 支持全局共享 (process-wide) + per-key 桶 (按 upstream/tool 分桶)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TokenBucket:
    """单桶令牌桶。

    rate: 持续补充速率 (tokens/sec)
    capacity: 桶容量 (允许瞬时突发上限)
    """
    rate: float
    capacity: float
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)

    def __post_init__(self):
        self._tokens = self.capacity  # 初始满桶
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        """按经过时间补充令牌。"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def try_acquire(self, n: int = 1) -> bool:
        """非阻塞尝试获取 n 个令牌。成功 True，失败 False。"""
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def time_to_available(self, n: int = 1) -> float:
        """获取 n 个令牌还需等待多少秒。"""
        self._refill()
        if self._tokens >= n:
            return 0.0
        deficit = n - self._tokens
        return deficit / self.rate if self.rate > 0 else float("inf")


class AsyncTokenBucketLimiter:
    """per-key 令牌桶限流器。

    用法:
        limiter = AsyncTokenBucketLimiter(default_rate=20, default_capacity=40)
        if await limiter.acquire("fuyao_ashare"):
            await client.call_tool(...)
        else:
            return ToolResult(success=False, error="rate limited")

    default_rate / default_capacity 给所有未单独配置的 key 兜底；
    configure(key, rate, capacity) 给特定上游 (如 fuyao_ashare) 配置
    更高/更低的配额。
    """

    def __init__(self, default_rate: float = 20.0, default_capacity: float = 40.0):
        self.default_rate = default_rate
        self.default_capacity = default_capacity
        self._buckets: dict[str, TokenBucket] = {}
        self._configs: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    def configure(self, key: str, rate: float, capacity: float | None = None) -> None:
        """为指定 key 配置限流参数。capacity 默认 = rate * 2。"""
        if capacity is None:
            capacity = rate * 2
        self._configs[key] = (rate, capacity)
        # 已存在的桶不重建，新参数对老桶生效需要等下次 _get_or_create
        # 简化：直接重建桶（清空令牌），让新限流立即生效
        self._buckets.pop(key, None)

    async def _get_bucket(self, key: str) -> TokenBucket:
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is not None:
                return bucket
            rate, capacity = self._configs.get(key, (self.default_rate, self.default_capacity))
            bucket = TokenBucket(rate=rate, capacity=capacity)
            self._buckets[key] = bucket
            return bucket

    async def acquire(self, key: str, n: int = 1, wait: bool = False) -> bool:
        """获取 n 个令牌。

        wait=False: 桶空直接返回 False（不阻塞，由调用方决定重试/降级）。
        wait=True: 桶空 await 到令牌可用，最多等 timeout 秒。
        """
        bucket = await self._get_bucket(key)
        if bucket.try_acquire(n):
            return True
        if not wait:
            return False
        wait_seconds = bucket.time_to_available(n)
        # 上限 5s 防止上游永久不可用时无限等
        await asyncio.sleep(min(wait_seconds, 5.0))
        return bucket.try_acquire(n)

    def get_stats(self, key: str) -> dict:
        """获取指定 key 的限流状态 (监控/调试用)。"""
        bucket = self._buckets.get(key)
        if bucket is None:
            rate, capacity = self._configs.get(key, (self.default_rate, self.default_capacity))
            return {"rate": rate, "capacity": capacity, "tokens": capacity}
        bucket._refill()
        return {
            "rate": bucket.rate,
            "capacity": bucket.capacity,
            "tokens": round(bucket._tokens, 2),
        }


# 进程内单例
_default_limiter: AsyncTokenBucketLimiter | None = None


def get_default_limiter() -> AsyncTokenBucketLimiter:
    """获取进程内共享的限流器（默认 20 QPS / 桶 40）。"""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = AsyncTokenBucketLimiter(default_rate=20.0, default_capacity=40.0)
    return _default_limiter
