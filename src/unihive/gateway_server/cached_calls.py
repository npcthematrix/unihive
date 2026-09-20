"""CachedExecutionMixin — 路由 + 缓存包装 (_execute_cached)。

2026-09-19 god-module 拆分时从 gateway_server.py 原样迁入 (行为保持)。
只缓存远程 HTTP 同花顺源 (fuyao_*) 的非实时响应, 带 per-key single-flight;
详见方法内历史审计注释。
"""
import asyncio
import logging

from ..core.normalizer import Normalizer

logger = logging.getLogger(__name__)


class CachedExecutionMixin:
    """_execute_cached 方法簇 (GatewayServer 组装时混入)。"""

    async def _execute_cached(
        self,
        name: str,
        params: dict,
        route_key: str,
        ttl_key: str | None = None,
        data_source_type: str = "online",
    ) -> dict:
        """路由 + 缓存包装。

        缓存策略：
        - 只缓存"远程 HTTP 同花顺源"(fuyao_*) 的非实时响应。实时行情不缓存；
          本地终端/本地库（mootdx2/tdx_quant/thsdk/omni）数据访问本身够快，
          再套缓存反而多余。
        - 读缓存/单飞前提：该工具路由候选链上存在 fuyao_* 源；实际写缓存还要
          求本次确实命中 fuyao（本地源优先时不写，避免本地结果污染 fuyao 缓存）。
        - 命中路径：直接返回缓存的响应，附 cache_hit=True。
        - 失效路径：调一次路由；失败响应也只算一次，不双倍路由。
        - hops 是请求级元数据，不进缓存。

        M2 (2026-09-07 5th-round audit): _active_requests 计数挪到外层
        (整个 _execute_cached 入口 + 出口), waiter 在 await single-flight
        future 时也算在 _active_requests 里, stop() 优雅关闭会等它们。
        Pre-fix: 计数只在 _route_and_respond 里, waiter 不计。
        """
        async with self._requests_lock:
            self._active_requests += 1
        try:
            # 缓存策略判定下沉到 src.core.cache_strategy 纯函数, 见 plan
            # warm-imagining-quilt.md (Refactor 1)。本地 import 避免模块
            # 顶层循环依赖。
            from ..core import cache_strategy

            # FUYAO 嵌套 TTL 优先于全局 cache.ttl[ttl_key] — 前端可按 endpoint
            # / 工具粒度覆盖。若 fuyao_cache 未配置, 回退到 ttl_key 全局。
            # Round-12 H2 + 簇E: 整段 TTL 解析容错。TTL=0 合同语义是
            # 「显式不缓存」, 旧链路只判 is not None, 0 透传到 Cache.set
            # 被 `if ttl` 判假 → expires_at=NULL → 永久缓存; fuyao override
            # 的非数值 (绕过 PUT pydantic 门的 YAML/手改库行) 会抛带字段
            # 名的 ValueError。两类问题都在此降级为不缓存 + warning, 不把
            # 配置错变成请求 500。
            ttl: int | None = None
            try:
                fuyao_ttl = cache_strategy.fuyao_ttl_for(self.config, name)
                ttl = fuyao_ttl if fuyao_ttl is not None else (
                    cache_strategy.ttl_for(self.config, ttl_key) if ttl_key else None
                )
                if ttl is not None:
                    ttl = cache_strategy.normalize_ttl(ttl)
            except (TypeError, ValueError):
                # 坏值细节 (含字段名) 在异常消息里, exc_info 带进日志
                logger.warning(
                    "[%s] invalid cache TTL config (ttl_key=%r); "
                    "caching disabled for this call",
                    name, ttl_key, exc_info=True,
                )
                ttl = None
            cache_enabled = self.cache is not None and self.cache.enabled
            # 候选路由链（与 Router.route 解析顺序一致）：显式 routing.chain
            # 优先，否则从 upstream_tool_mapping 推导。
            routing_cfg = (self.config.get("routing") or {}).get(route_key)
            if isinstance(routing_cfg, dict) and "chain" in routing_cfg:
                chain = list(routing_cfg.get("chain") or [])
            else:
                chain = list(
                    (self.config.get("upstream_tool_mapping") or {})
                    .get(route_key, {})
                    .keys()
                )
            # 是否走缓存读/单飞：有 TTL、非实时、链上存在远程 fuyao 源。
            can_read_cache = (
                ttl is not None
                and cache_enabled
                and not cache_strategy.is_realtime_ttl(ttl_key)
                and cache_strategy.chain_has_remote_http(chain)
            )
            # 向后兼容：原变量名在下方逻辑中代表"本次走缓存路径"。
            can_cache = can_read_cache
            # hit/miss 统计跟踪范围与读缓存前提一致。
            tracks_stats = can_read_cache

            key: str | None = None
            if can_cache:
                key = cache_strategy.cache_key(name, params)
                cached = await self.cache.get(key)
                if cached is not None:
                    # LOW-4 (2026-09-14 audit): hops 不进缓存 (写时清空),
                    # 命中时给一个 cache sentinel hop 让消费者看到响应走了
                    # 缓存而非未配置 routing chain。不可变拷贝避免污染持久化对象。
                    self.cache.record_hit()
                    # DEBUG 而非 INFO: 缓存命中是常态, 每请求刷 INFO 会让 FileHandler
                    # 在事件循环线程同步写盘; 100+ QPS 时放大成可观察的 latency。
                    logger.debug(f"[{name}] cache hit (key={key})")
                    return {
                        **cached,
                        "hops": [
                            {
                                "source": "cache",
                                "tool_name": name,
                                "duration_ms": 0,
                                "success": True,
                                "error": None,
                            }
                        ],
                        "cache_hit": True,
                    }

            if tracks_stats and not can_cache:
                # 计 miss 但不走单飞：例如实时 key、链上无远程 fuyao 源。
                # 每个请求各自路由, 用于运维观测命中率。
                self.cache.record_miss()

            # 路由 + 缓存写入的内部闭包 (不再管 _active_requests)
            async def _route_and_respond() -> dict:
                # MED 3 (2026-09-07 4th-round audit): 总超时, 防止上游
                # (特别是 MooTDX2 同步库走 run_in_executor, 0 个内部 timeout)
                # 卡死整个 request。MooTDX2 call_tool 内部无 timeout, executor
                # 任务挂死会拖到 stop() 等不到 _active_requests==0。
                result = await asyncio.wait_for(
                    self.router.route(route_key, params),
                    timeout=self.DEFAULT_REQUEST_TIMEOUT,
                )
                # hops 是请求级诊断信息，留在响应里随返回消费者；
                # 进缓存的副本中我们设 []，避免历史 hops 被复用。
                logger.debug(
                    f"[{name}] route result: success={result.success} "
                    f"source={result.source} hops={[h.source for h in result.hops]}"
                )
                resp = Normalizer.to_gateway_response(
                    success=result.success,
                    data=result.data,
                    error=result.error,
                    source=result.source,
                    hops=[h.source for h in result.hops],  # 真实 hops 留给消费者
                    # 成功路径的非阻断提示（消歧/no_data/truncated）随信封透传，
                    # 并随 {**resp} 一起进入缓存，命中路径经 **cached 自动带回。
                    warn=getattr(result, "warn", None),
                )
                # 仅当本次实际命中远程 fuyao 源才写缓存：若链上本地源优先并成功，
                # 不能把本地结果按 fuyao 的 key 缓存，否则后续 fuyao 直查会误命中。
                if (
                    can_cache
                    and key is not None
                    and result.success
                    and result.data is not None
                    and cache_strategy.is_fuyao_source(result.source)
                ):
                    # 缓存里只存稳定数据：hops（请求级）入空，cache_hit 在 setdefault 之前写
                    await self.cache.set(key, {**resp, "hops": []}, ttl=ttl)
                # 标记 miss，让消费者区分"缓存端点本次未命中"与"无缓存端点"
                resp.setdefault("cache_hit", False)
                return resp

            # HIGH 1 (2026-09-07 4th-round audit): cache miss 后到 route 之间
            # 必须 per-key single-flight, 否则同 key 并发 miss 让 router 被调
            # N 次、上游负载翻倍。_execute_cached 走 cache.get + cache.set
            # 直接路径, 这一段没有 in-flight 保护, 必须在 gateway 层补。
            # waiter 复用 leader 的 future, leader 失败则 waiter 升级为
            # 新 leader 自己再试一次 (fallback 行为).
            if can_cache and key is not None:
                existing = self._in_flight_requests.get(key)
                if existing is not None and not existing.done():
                    try:
                        cached_resp = await existing
                        return {
                            **cached_resp,
                            "hops": cached_resp.get("hops", []),
                            "cache_hit": False,
                        }
                    except BaseException:
                        pass  # leader 失败, 自己升级为新 leader
                # leader: 跑路由 + 写缓存, 失败要传播给 waiter
                future = asyncio.get_running_loop().create_future()
                self._in_flight_requests[key] = future
                # miss 只对发起者记一次, waiter 复用 leader 的 future 不重复计
                self.cache.record_miss()
                try:
                    resp = await _route_and_respond()
                    future.set_result(resp)
                    return resp
                except BaseException as exc:
                    future.set_exception(exc)
                    raise
                finally:
                    # 先 set_result/set_exception 再 pop, waiter 拿到结果时
                    # in_flight 已清理, 下次同 key 是新 leader.
                    self._in_flight_requests.pop(key, None)

            # can_cache=False 走无单飞直接路径
            return await _route_and_respond()
        finally:
            async with self._requests_lock:
                self._active_requests -= 1
