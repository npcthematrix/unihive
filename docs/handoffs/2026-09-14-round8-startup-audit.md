# Round 8 Startup Audit Handoff — 2026-09-14

## Context

用户要求审视 MCP GATEWAY 和 CONSOLE 的启动逻辑,这是启动路径审计的第 8 轮 (round 8)。

dispatch 了 3 个并行 review 代理覆盖三个角度:

1. **gateway_server.py 启动路径** — `GatewayServer.start/serve_http/stop` + MCP factory + tool loader + registry + config_loader + start_gateway.ps1
2. **console_api.py + 上游 client init** — `/api/*` endpoints + 各 upstream client init + console_auth + auth_middleware
3. **console.html + static/console/ 前端启动** — SPA 模板 + CSS + JS 加载 / auth gate / fetchJSON

## 本轮结果

### 已修复 ✅ (MED-1/2/3)

#### MED-1: router 无 routing chain 错误消息 ✅
**文件**: `src/unihive/core/router.py:119-128`

错误消息从 `"No routing chain defined for {tool}"` 扩展为带排查指引:

```python
error=(
    f"No routing chain defined for {gateway_tool}; "
    f"check config/upstreams.yaml routing section "
    f"and ensure the tool has a chain list."
),
```

#### MED-2: MooTDX2Client.start() 跑连通性探测 ✅
**文件**: `src/unihive/api/mootdx2_client.py:299-348`

- 旧实现: `start()` 无条件 `_status = UpstreamStatus.HEALTHY`, TDX 不可达时直到首次请求才报错
- 新实现: 跑一次 `q.quotes(symbols=["000001"])` 探测 + 2.5s 超时
  - 探测成功 (非空 DataFrame) → HEALTHY
  - 探测返回空 / 超时 / 抛异常 → DEGRADED + log warning
- 新增 `_probe_startup_once()` 单独方法,便于单测

设计取舍: 失败时选 DEGRADED 而非 UNAVAILABLE, 因为 TDX server 可能临时不通,后续健康检查可以再回升。

#### MED-3: OmniClient DB 缺失 → UNAVAILABLE ✅
**文件**: `src/unihive/api/omni_client.py:328-345`

- 旧: DB 不存在时设 DEGRADED, 配合 `is_available=True` (DEGRADED 也算 available) 让 router 还在 chain 里试 → 所有 omni 工具都失败
- 新: 设 UNAVAILABLE, `is_available=False`, router 直接跳过
- 补 `import logging` + `logger = logging.getLogger(__name__)` (文件原本缺)
- log warning 带 actionable hint: `Run python -m src.unihive.sync.board_sync to populate.`

### 已 declined (HIGH-1/2/3 + MED-5/7 + LOW-4/5) ⏸

详见 [[feedback_round8_high_deferred]] (已存为 user memory)。原因:

- **H1** Session cookie 缺 Secure/SameSite — 本机 HTTP 单用户场景无实质风险
- **H2** 登录端点无限流 — 单用户 + 本机不构成暴力破解面
- **H3** probe coalesce "unknown" 语义歧义 — UI 显示稍模糊但不影响功能
- **H4/H5** start()/stop() 缺幂等保护 — 单进程单实例启动不会触发
- **M5** stdio 模式仍 import console_api — 启动开销 +0.5s 量级
- **M7** console_api `load_config` 浅拷贝 — 内部 caller 不修改嵌套 dict
- **L2** Windows 不注册 SIGTERM — 已知限制, stdio 模式下父进程行为兜底

剩余 LOW 项 (console.html 无 cache-bust, CSS FOUC, loading skeleton, error 消息可能泄漏, L4/L5 console.js 待复核) 也一并留待续。

## 测试覆盖

新文件: `tests/test_round8_audit.py` — 10 测试覆盖 MED-1/2/3:

- `TestRouterNoChainErrorMessage` (2): 错误消息含 `upstreams.yaml` 和 tool name
- `TestMooTDX2StartProbe` (4): success → HEALTHY; 空/超时/异常 → DEGRADED (含 2.5s 超时验证,实际触发不阻塞测试)
- `TestOmniStartStatus` (4): DB 存在 → HEALTHY; DB 缺失 → UNAVAILABLE + log hint + is_available=False

### 测试结果

- round8 审计相关测试: 10/10 passed
- 排除已知 flaky 文件 (test_tdx_quant_client.py + test_mootdx2_reader_offline.py):
  - 基线 (无本轮改动): 316 passed, 50 failed
  - 应用本轮改动: 323 passed, 43 failed
  - **净改进**: +7 passed, -7 failed, 0 regression

## 部署注意事项

MED-2 (MooTDX2 start probe) 会在每次启动时增加最多 2.5s 延迟 (TDX server 不可达时走满超时)。生产环境:

- TDX server 可达: 探测几百毫秒返回,启动延迟增加 <1s
- TDX server 不可达: 启动 +2.5s,但 status=DEGRADED 提醒运维介入,避免首次请求才暴露

## 待续 (后续审计 round 候选)

- console_api 写操作端点 (POST /api/cache/clear 等) 的 CSRF / Origin 校验
- console.html 渲染前 auth gate (server-side 重定向 + 前端 race)
- Console JS error 消息泄漏后端路径
- console.html 本身的 cache-bust (CSS/JS 有 `?v=...`, HTML 没有)