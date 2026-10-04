# 2026-10-05 Router 限流可观测性端点 (未提交部分)

## 背景

2026-10-04 排查 `get_daily_local` 报 `rate limited (qps cap reached for mootdx2)` 异常时:

- 配置层 (`config.db.gateway`) 无 `rate_limit` 字段,mootdx2 走默认 20 QPS / 桶 40
- 代码层只有 `_apply_gateway_tuning` 一处 `limiter.configure()`,读的是空 config
- 唯一 `AsyncTokenBucketLimiter(...)` 实例化是单例,固定 20/40
- 4 calls in 22ms 全失败说明运行时 bucket 状态异常,但纯日志+静态无法定位真正诱因
  (同窗口 `gateway.out` 0 条, `gateway.log` 仅 4 条 router ERROR, 数学上需要
   bucket 接近 0 且 rate ≈ 0,配置层不可能 → 必有未走 router 层的本地调用扣桶)
- 用户核心质疑成立: 本地不该走限流。结构性修复 + 加可观测端点

## 已完成 (commit `2513285`,未 push)

**修复**: 显式白名单 `_REQUIRES_RATE_LIMIT = frozenset({fuyao_ashare, fuyao_index, fuyao_meta, fuyao_fund})`

- 文件: `src/unihive/core/router.py`
  - 顶部新增模块级 frozenset + 注释(2026-10-04 事件留痕)
  - `_execute_with_fallback` 限流分支包一层 `if source in _REQUIRES_RATE_LIMIT:`
- 测试: `tests/test_router_rate_limit_bypass.py` (9 cases, 全过)
- 全套: 1866 passed (含我 9 个),3 failed — pre-existing `_build_arg_parser`
  import 失败, 与本改动无关 (`git stash` 后单跑那两个测试也过; 单跑时直接
  `from src.unihive.gateway_server import _build_arg_parser` 也报 ImportError)

## 待完成: `/api/runtime/rate-limiter` 可观测端点

**目的**: 暴露运行时 limiter 真实状态, 未来同类问题直接看 runtime 而不是靠日志猜

### 实现位置

1. `src/unihive/utils/console_api.py`
   - 新增 `async def rate_limiter_status_api(req) -> JSONResponse`
   - 从 `GatewayServer.limiter` (或 fallback `get_default_limiter()`) 读取
   - 对白名单每个 key 调 `limiter.get_stats(key)` → `{rate, capacity, tokens}`
   - 也遍历 `limiter._buckets.keys()` 拿到运行时实际出现过的 key (含非白名单)
   - 返回结构:
     ```json
     {
       "rate_limit_required": ["fuyao_ashare", ...],
       "default": {"rate": 20.0, "capacity": 40.0},
       "stats": {
         "fuyao_ashare": {"rate": 5.0, "capacity": 10.0, "tokens": 9.3},
         "mootdx2":      {"rate": 20.0, "capacity": 40.0, "tokens": 40.0},
         ...
       }
     }
     ```
   - 注册路由 (放在 `/api/runtime/pool-config` 旁边, console_api.py 约 2363 行):
     ```python
     Route("/api/runtime/rate-limiter", rate_limiter_status_api),
     ```
2. `tests/test_console_api_rate_limiter_status.py` (新文件)
   - 用 TrackingLimiter / AsyncTokenBucketLimiter 注入 fixture
   - 测 GET 返回正确 stats 字段、白名单与运行时 key 都覆盖

### 约束

- 不破坏 auth: `/api/runtime/*` 沿用现有 cookie auth
- 不改 limiter 行为: 只读,不动状态
- 返回结构兼容前端: 查 console 现有 `/api/runtime` 调用方

## 当前 upstream 类型清单 (白名单维护参考)

| upstream | type | 限流 |
|---|---|---|
| mootdx2 | mootdx2 | skip (本地 TCP) |
| tdx_quant | tdx_quant | skip (本地 HTTP :17709) |
| omni | omni | skip (本地 SQLite) |
| thsdk | thsdk | skip (本地 SDK) |
| THS-LOCAL-GATEWAY | ths_local_gateway | skip (本地 HTTP :17281, 当前 enabled=0) |
| fuyao_ashare / fuyao_index / fuyao_meta / fuyao_fund | http | **limit** (aicubes.cn) |

加新 upstream 时: 远程必须显式 `_REQUIRES_RATE_LIMIT.add(name)`, 本地自动豁免。

## 规则备忘

- 本地 main 已超 origin/main 195 commits, PUSH 网络不通
- 跑全套可能撞 pre-existing 失败 `_build_arg_parser` (3 个 test),别误判回归
- 上下文余量 <25% 时该停就停,别硬撑
- 改完代码等用户指令再跑测试 (CLAUDE.md "改配置不要马上测试")
