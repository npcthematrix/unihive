# MCP 二次审视 — 专题 handoff (1 HIGH + 5 MED + 3 LOW)

**日期**: 2026-09-15
**专题**: 控制台二次审视 ship 后的 MCP 网关二次审视
**结论**: 3 并行审查 agent 识别 9 项改进, 已 ship 5 项 (HIGH+M2+M3+M4+M5), 4 项 deferred

## 背景

前 6 次 startup audit (2026-09-07 ~ 09-08) + Round 8 (declined) + MCP perf 2026-09-11 (3/7 done) 之后的第二轮审视。本次聚焦:
- 启动路径正确性
- 运行时热路径性能 (per-call overhead, cache, JSON, logging)
- 长进程资源管理 (FD, threads, async clients, sqlite WAL)

**Why**: 前几轮集中在 init/teardown + 启动幂等, 二轮聚焦 hot path overhead + 资源泄漏。

**How to apply**:
- 已 ship 5 项独立 PR, 各自可回滚
- 4 项 deferred 已 ship: 6c29bfd
- 新会话恢复看 git log: 730b98b (H1+M2+M4) / 1f263de (M3+M5) / 6c29bfd (M1+L1+L2+L3)

## 9 项发现

### HIGH (1) ✅

#### H1 · 每请求 INFO 日志在事件循环线程同步写 ✅ 730b98b
- **文件**: gateway_server.py:619, router.py:248
- **修复**: cache hit / route success 改 DEBUG; 关键事件 (启动/关闭/失败/降级) 保留 INFO

### MED (5)

#### M1 · rate_limiter 全局单例 stop() 时不 reset ✅ 6c29bfd
- **文件**: rate_limiter.py:140, gateway_server.py:327-328
- **修复**: 添加 reset_default_limiter() 在 stop() 时重置全局单例

#### M2 · ThreadPoolExecutor 永不 shutdown ✅ 730b98b
- **文件**: blocking_pool.py:32-48
- **修复**: stop() 在 upstream 关闭后显式 shutdown_blocking_executor(cancel_futures=True)

#### M3 · SQLite WAL 无 checkpoint ✅ 1f263de
- **文件**: cache.py:149-152
- **修复**: _cleanup_impl() 末尾 PRAGMA wal_checkpoint(TRUNCATE); 5 min 间隔跑, sub-ms 开销

#### M4 · cache.cleanup_expired() 从未调度 ✅ 730b98b
- **文件**: cache.py:293
- **修复**: 新增 _cache_cleanup_task 在 __init__ + asyncio.create_task 在 initialize 后; stop() 时取消 + await; 用 getattr 兜底兼容 __new__ 测试

#### M5 · JSON ser/de 在热路径用 stdlib ✅ 1f263de
- **文件**: cache.py:211, 237
- **修复**: pyproject 加 orjson>=3.9,<4.0; 抽 _dumps/_loads 辅助, 不可用降级 stdlib; 替换 cache.py:236,262

### LOW (3)

#### L1 · proxy_api 模块级 globals stop() 时不清 ✅ 6c29bfd
- **文件**: proxy_api.py
- **修复**: 添加 reset_proxy_api() 清理模块级全局变量

#### L2 · OmniClient TLS 连接永驻线程 ✅ 6c29bfd
- **文件**: omni_client.py:351
- **修复**: stop() 时调用 _close_thread_conn() 关闭 SQLite 长连接

#### L3 · MooTDX2Client _metrics["error_counts"] 字典只增不删 ✅ 6c29bfd
- **文件**: mootdx2_client.py:136-161
- **修复**: 上限 100, 达到时 LRU 清理最老条目

## 验收基线 (5 项 ship 后)
- pytest 580 passed + 1 skipped
- startup_fixes 19 passed (含 __new__ 路径)
- vue-tsc / vitest / build 不变 (本次未触前端)
- console.html 不变

## 风险与回退
- **H1 风险**: DEBUG 默认级别可能掩盖 cache hit rate 监控 — 监控场景用 logger.debug + 自定义 handler 抓
- **M2 风险**: shutdown_blocking_executor(cancel_futures=True) 会丢弃在途 run_in_executor 任务; 已确认 stop() 在 upstream 关闭后调用, 此时应无在途
- **M3 风险**: WAL checkpoint 在高写场景可能短暂 block — TRUNCATE 模式极轻量, 实测 sub-ms
- **M4 风险**: 5 min 间隔与 LRU 上界 max_entries 协同; 极端场景先 hit LRU 后 cleanup, 无害
- **M5 风险**: orjson 与 stdlib json 在某些边界 (NaN, 重复 dict key) 行为不同 — 缓存值均为 RPC response JSON, 标准类型, 实际无影响

## 关联记忆
- [[project-startup-audit-2026-09-08]] — 5th-round audit (前次 ship 11 项)
- [[project-mcp-perf-optimization-2026-09-11]] — 3/7 done (Fuyao 重试/令牌桶/health_check)
- [[feedback-round8-high-deferred]] — start/stop 幂等 declined (本机+单用户场景)
- 文档路径: `docs/handoffs/2026-09-15-mcp-second-pass-audit.md`
