"""MooTDX2 连接池管理

功能:
- 连接复用
- 定期健康检查
- 自动选择最快服务器
- 指数退避重试 + 抖动
"""
import asyncio
import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .mootdx2_config import MooTDX2Settings, ServerConfig
from .mootdx2_errors import (
    MooTDXError,
    MooTDXErrorType,
    error_to_result,
)

logger = logging.getLogger(__name__)


@dataclass
class PooledConnection:
    """池化连接"""

    server: ServerConfig
    connection: Any  # mootdx2 Quotes 实例
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    healthy: bool = True
    error_count: int = 0


class ConnectionPool:
    """MooTDX2 连接池"""

    def __init__(self, settings: MooTDX2Settings):
        self.settings = settings
        self._pools: dict[str, PooledConnection] = {}  # server_key -> connection
        self._lock = asyncio.Lock()
        self._health_check_task: Optional[asyncio.Task] = None
        self._running = False

        # 指标
        self._stats = {
            "total_requests": 0,
            "total_errors": 0,
            "error_counts": defaultdict(int),
            "server_health": {},
            "latencies": defaultdict(list),  # 最近 100 次延迟
        }

    async def start(self):
        """启动连接池"""
        self._running = True
        # 启动健康检查任务
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        logger.info(f"Connection pool started with {len(self.settings.servers)} servers")

    async def stop(self):
        """停止连接池"""
        self._running = False
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # 关闭所有连接
        async with self._lock:
            for key, conn in self._pools.items():
                try:
                    if hasattr(conn.connection, "close"):
                        conn.connection.close()
                except Exception as e:
                    logger.warning(f"Error closing connection {key}: {e}")
            self._pools.clear()

        logger.info("Connection pool stopped")

    def _get_server_key(self, server: ServerConfig) -> str:
        return f"{server.host}:{server.port}"

    async def _create_connection(self, server: ServerConfig) -> Optional[PooledConnection]:
        """创建新连接"""
        try:
            from mootdx2.quotes import Quotes

            # 创建 Quotes 实例
            quotes = Quotes.factory(
                market=self.settings.market,
                multithread=True,
                bestip=self.settings.auto_select_fastest_server,
            )

            # 测试连接
            # 使用轻量级调用测试
            start = time.time()
            try:
                # 尝试获取市场数量来验证连接
                count = quotes.stock_count(market=0)
                latency_ms = int((time.time() - start) * 1000)
                logger.info(f"Connection test to {server.host}:{server.port} - latency: {latency_ms}ms, count: {count}")
            except Exception as e:
                logger.warning(f"Connection test failed for {server.host}:{server.port}: {e}")
                quotes.close()
                return None

            return PooledConnection(
                server=server,
                connection=quotes,
                healthy=True,
                latency_ms=latency_ms,
            )
        except Exception as e:
            logger.error(f"Failed to create connection to {server.host}:{server.port}: {e}")
            return None

    async def get_connection(self, server: Optional[ServerConfig] = None) -> tuple[Optional[PooledConnection], Optional[MooTDXError]]:
        """获取连接

        Returns:
            (connection, error): 连接和错误
        """
        self._stats["total_requests"] += 1

        # 如果未指定服务器，选择最健康的
        if server is None:
            server = await self._select_best_server()
            if server is None:
                self._stats["total_errors"] += 1
                self._stats["error_counts"][MooTDXErrorType.SERVER_UNAVAILABLE.value] += 1
                return None, MooTDXError(
                    error_type=MooTDXErrorType.SERVER_UNAVAILABLE,
                    message="No available servers in pool",
                    recoverable=True,
                )

        key = self._get_server_key(server)

        async with self._lock:
            conn = self._pools.get(key)

            # 检查连接是否有效
            if conn and conn.healthy:
                conn.last_used_at = time.time()
                return conn, None

            # 创建新连接
            new_conn = await self._create_connection(server)
            if new_conn:
                self._pools[key] = new_conn
                return new_conn, None
            else:
                # 连接失败，标记服务器不健康
                server.healthy = False
                self._stats["total_errors"] += 1
                self._stats["error_counts"][MooTDXErrorType.CONNECTION_ERROR.value] += 1
                return None, MooTDXError(
                    error_type=MooTDXErrorType.CONNECTION_ERROR,
                    message=f"Failed to connect to {server.host}:{server.port}",
                    recoverable=True,
                )

    async def _select_best_server(self) -> Optional[ServerConfig]:
        """选择最优服务器（最健康 + 最低延迟）"""
        async with self._lock:
            # 优先选择健康的服务器
            healthy_servers = [s for s in self.settings.servers if s.healthy]
            if not healthy_servers:
                # 所有服务器都不健康，返回第一个
                return self.settings.servers[0] if self.settings.servers else None

            # 选择延迟最低的
            best = min(healthy_servers, key=lambda s: s.latency_ms or 99999)
            return best

    async def _health_check_loop(self):
        """定期健康检查"""
        while self._running:
            try:
                await asyncio.sleep(30)  # 每 30 秒检查一次
                await self._health_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")

    async def _health_check(self):
        """执行健康检查"""
        logger.debug("Running health check...")

        for server in self.settings.servers:
            key = self._get_server_key(server)
            conn = self._pools.get(key)

            if conn is None:
                continue

            try:
                start = time.time()
                # 轻量级调用
                count = conn.connection.stock_count(market=0)
                latency_ms = int((time.time() - start) * 1000)

                server.latency_ms = latency_ms
                server.healthy = True
                conn.healthy = True

                # 记录延迟
                self._stats["latencies"][key].append(latency_ms)
                if len(self._stats["latencies"][key]) > 100:
                    self._stats["latencies"][key] = self._stats["latencies"][key][-100:]

                self._stats["server_health"][key] = {
                    "healthy": True,
                    "latency_ms": latency_ms,
                }

            except Exception as e:
                logger.warning(f"Health check failed for {server.host}:{server.port}: {e}")
                server.healthy = False
                conn.healthy = False
                conn.error_count += 1

                self._stats["server_health"][key] = {
                    "healthy": False,
                    "error": str(e)[:100],
                }

    def record_error(self, server: ServerConfig, error_type: MooTDXErrorType):
        """记录错误"""
        self._stats["total_errors"] += 1
        self._stats["error_counts"][error_type.value] += 1

        # 标记服务器不健康
        server.healthy = False

        key = self._get_server_key(server)
        if key in self._pools:
            self._pools[key].error_count += 1
            self._pools[key].healthy = False

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        # 计算平均延迟
        all_latencies = []
        for latencies in self._stats["latencies"].values():
            all_latencies.extend(latencies)

        avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0

        return {
            "total_requests": self._stats["total_requests"],
            "total_errors": self._stats["total_errors"],
            "error_counts": dict(self._stats["error_counts"]),
            "avg_latency_ms": round(avg_latency, 2),
            "server_health": self._stats["server_health"],
        }


class RetryHelper:
    """重试帮助类：指数退避 + 抖动"""

    def __init__(self, max_retries: int = 3, backoff_base: float = 0.5, jitter: float = 0.1):
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.jitter = jitter

    def get_delay(self, attempt: int) -> float:
        """计算延迟: exponential backoff + random jitter"""
        delay = self.backoff_base * (2 ** attempt)
        # 添加随机抖动
        jitter_range = delay * self.jitter
        delay += random.uniform(-jitter_range, jitter_range)
        return max(0, delay)

    async def execute_with_retry(
        self,
        func: Callable,
        *args,
        **kwargs,
    ) -> Any:
        """执行函数，可重试"""
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_error = e

                # 判断是否可重试
                from .mootdx2_errors import ErrorClassifier

                error = ErrorClassifier.classify(e)
                if not error.recoverable or attempt >= self.max_retries:
                    raise

                delay = self.get_delay(attempt)
                logger.warning(f"Retry {attempt + 1}/{self.max_retries} after {delay:.2f}s: {e}")
                await asyncio.sleep(delay)

        raise last_error
