# Proxy Settings — 行情类 MOOTDX2 → TDX_QUANT 透明代理

**Date**: 2026-09-14
**Status**: Draft
**Author**: Claude + user (brainstorming session)

## 1. Background

`mootdx2` (通达信 TCP) 和 `tdx_quant` (通达信本地 DLL via TdxW.exe) 都提供行情类工具。在 TdxW.exe 已登录、本地 DLL 链路稳定的场景下,tdx_quant 比 mootdx2 (走远程 TDX 服务器,延迟高、易抖动) **更稳定可靠**。

用户希望: **当 TDX_QUANT 上线时,让原本走 mootdx2 的行情类请求优先走 tdx_quant;tdx_quant 不可用时再降级回 mootdx2**。

但两个上游的接口命名/参数/返回格式都有差异,**不能直接透明转发**。需要一个适配层。

## 2. Goal

提供"代理设置"功能,允许用户在 console 启用一个**透明代理适配器**,使 7 个行情类工具的 mootdx2 请求在 TDX_QUANT HEALTHY 时自动走 tdx_quant,失败/不可用时降级回 mootdx2。

## 3. Scope

### 3.1 代理覆盖的 7 个工具

| MOOTDX2 tool | TDX_QUANT tool | 备注 |
|--------------|----------------|------|
| `get_quote` | `get_market_snapshot` | 单股快照 |
| `get_batch_quote` | `get_security_snapshot` | tdx_quant 天然批量,反而是优化 |
| `get_kline` | `get_market_data` | period 映射 |
| `get_index_kline` | `get_market_data` | 同上 |
| `get_index_all` | `get_market_data` | 同上 |
| `get_index_bars` | `get_market_data` | 同上 |
| `get_minute_data` | `get_minute_time_data` | 今日分时 |

### 3.2 **不**代理的工具

- `get_trade` / `get_trade_history` (逐笔成交) — TDX_QUANT 无对应能力
- 板块/财务/技术指标类 — 用户范围外,继续走 mootdx2

## 4. Design

### 4.1 配置存储

新建 `config/proxy_settings.yaml`:

```yaml
proxy:
  enabled: false               # 总开关。开启后 tdx_quant 优先,失败自动降级 mootdx2
```

**写死**到代码 (不放 yaml): 工具映射表、period 映射表、参数名映射、字段 normalize。这些是协议级约定,不应让用户改。

**降级策略写死**: 启用代理后,tdx_quant 任何失败(UNAVAILABLE/调用异常/返回错误)一律降级回 mootdx2。这与用户启用代理的初衷(图稳定)一致,不需要让用户在 UI 选"严格模式"——那是"关闭代理"的语义。

### 4.2 适配器

新建 `src/unihive/core/proxy_adapter.py`,提供:

```python
class Mootdx2ToTdxQuantAdapter:
    """mootdx2 行情工具 → tdx_quant 适配器."""

    # 工具名映射 (写死)
    TOOL_MAP = {
        "get_quote": "get_market_snapshot",
        "get_batch_quote": "get_security_snapshot",
        "get_kline": "get_market_data",
        "get_index_kline": "get_market_data",
        "get_index_all": "get_market_data",
        "get_index_bars": "get_market_data",
        "get_minute_data": "get_minute_time_data",
    }

    # period 映射 (写死)
    PERIOD_MAP = {
        "day": "1d", "week": "1w", "month": "1mon",
        "minute1": "1m", "minute5": "5m", "minute15": "15m",
        "minute30": "30m", "minute60": "1h",
    }

    async def adapt(self, mootdx2_tool: str, params: dict) -> tuple[str, dict, callable]:
        """返回 (tdx_quant_tool, transformed_params, response_normalizer)."""

    def supports(self, mootdx2_tool: str) -> bool:
        return mootdx2_tool in self.TOOL_MAP
```

**职责**:
- 工具名映射
- 参数名改写 (`code` → `stock_code`, `codes` → `stock_list[]`)
- period 类型映射
- 返回字段 normalize (tdx_quant 的 `{Now, LastClose}` → mootdx2 的 `{close, open}`)
- 股票代码 normalize (复用 `src/core/normalizer.py` 双向转换)

### 4.3 Router 集成

修改 `src/unihive/core/router.py` 的 `_call_with_fallback`:

```python
async def _call_with_fallback(self, gateway_tool, params):
    # 1. 查 proxy settings (热加载)
    if self._proxy.is_enabled() and self._proxy.adapter.supports(gateway_tool):
        tdx_tool, tdx_params, normalizer = self._proxy.adapter.adapt(gateway_tool, params)

        # 2. 优先试 tdx_quant
        tdx_client = self._get_upstream("tdx_quant")
        if tdx_client and tdx_client.is_available:
            try:
                result = await tdx_client.call_tool(tdx_tool, tdx_params)
                if result.success:
                    return normalizer(result.data), "tdx_quant"
            except Exception as e:
                logger.warning(f"proxy tdx_quant failed: {e}")

        # 3. 降级 (如配置允许)
        if not self._proxy.fallback_to_mootdx2:
            return ToolResult(success=False, error="proxy enabled but tdx_quant unavailable and fallback disabled")

    # 4. 原 mootdx2 链路 (走原 fallback chain)
    return await self._original_fallback(gateway_tool, params)
```

**关键不变量**:
- 用户**无感**: 调用 `get_quote` 的入参/出参格式与原 mootdx2 一致
- 失败透明: tdx_quant 任何失败 → 自动降级 (除非 `fallback_to_mootdx2=false`)
- 健康检查: 只在 `is_available`(HEALTHY/DEGRADED) 时尝试 tdx_quant — round 10 修复后 DEGRADED 仍可响应,合理利用

### 4.4 Console UI

`console.html` 配置信息标签页 → 新增子标签 **"代理设置"**:

```
[配置信息] [上游] [工具映射] [工具规格] [缓存] [代理设置]
```

子标签内容:
- **总开关**: checkbox "启用 TDX_QUANT 代理" (默认关闭)
- **覆盖范围表格** (只读): 列出 7 个被代理的 mootdx2 工具 + 对应 tdx_quant 工具
- **状态指示**: 当前 TDX_QUANT upstream 状态 (HEALTHY/DEGRADED/UNAVAILABLE),决定代理是否实际生效
- **说明文字**: "启用后,以下行情类工具将优先走 TDX_QUANT (本地 DLL),失败时自动降级到 MOOTDX2。"

**不**暴露: 工具映射编辑、period 映射编辑。这些是协议级约定,改错会让请求 100% 失败。

### 4.5 后端 API

新增 `src/unihive/utils/proxy_api.py`,挂在现有 console_api 路由上:

- `GET /api/proxy/settings` — 返回当前设置 + 适配器覆盖范围 + 当前 tdx_quant 状态
- `PUT /api/proxy/settings` — body `{enabled: bool}`,持久化到 yaml,触发 router 热 reload

**鉴权**: 复用现有 `console_auth` cookie middleware (`/api/*` 走鉴权)。

**持久化**: `config/proxy_settings.yaml` 文件读写,原子写 (tmp + rename)。

### 4.6 热 reload

`config_loader.py` 已实现 YAML 文件 watch。proxy_settings.yaml 加入 watch 列表后,router 在 yaml mtime 变化时自动重读设置。

**生效时机**: 下一个请求(无重启)。改动期间 in-flight 请求继续用旧设置。

## 5. Testing

### 5.1 Unit tests

- `tests/test_proxy_adapter.py`:
  - `test_adapt_get_quote` 参数名 `code` → `stock_code`,工具名改写
  - `test_adapt_get_kline_period_map` 8 种 period 全部正确映射
  - `test_adapt_get_batch_quote_to_list` `codes` 字符串切分到 `stock_list[]`
  - `test_supports_returns_false_for_trade` 逐笔成交不被代理
  - `test_response_normalize` tdx_quant `{Now, LastClose}` → mootdx2 `{close, open}`

- `tests/test_proxy_router.py`:
  - `test_proxy_disabled_falls_through_to_mootdx2`
  - `test_proxy_enabled_routes_to_tdx_quant_when_available`
  - `test_proxy_enabled_falls_back_when_tdx_quant_unavailable`
  - `test_proxy_enabled_falls_back_to_mootdx2_when_tdx_down` (合并 "no_fallback" 行为 — 现在永远降级)
  - `test_proxy_does_not_affect_trade_tools`

- `tests/test_proxy_api.py`:
  - `test_get_settings_unauthenticated_rejected`
  - `test_put_settings_persists_and_triggers_reload`
  - `test_atomic_yaml_write`

### 5.2 E2E

跳过浏览器 E2E(用户偏好 no Playwright 默认,见 feedback_no_playwright_default)。改用:
- `pytest tests/test_proxy_*.py` 全绿作为 CI 门
- 手动验证: console UI 改开关后,`curl -X POST http://127.0.0.1:18080/mcp` 调一次 `get_quote`,日志确认走的路径 (`tdx_quant` 或 `mootdx2`)

### 5.3 Coverage

目标 80%+,新代码重点:
- adapter 转换逻辑 (输入/输出边界)
- router 分支 (proxy on/off × tdx_quant 健康状态)
- API 鉴权 + 持久化

## 6. Risks

| Risk | Mitigation |
|------|-----------|
| TdxW.exe 未登录时 tdx_quant 返回 `[]`,代理误判成功 | adapter 检查 `result.success` + 非空 data,失败则降级 |
| 适配器字段映射遗漏字段导致用户看到缺字段 | normalize 函数单元测试覆盖所有字段 |
| 用户误改 yaml 导致启动失败 | yaml 解析失败时 fallback 到默认 (`enabled: false`),记 ERROR log |
| Console UI 误操作开启但 TDX_QUANT UNAVAILABLE | UI 显示当前 TDX_QUANT 状态,UNAVAILABLE 时开关变灰 + tooltip 提示 |

## 7. Out of Scope

- 适配器扩展到 fuyao / omni / thsdk 上游 — 仅做 mootdx2 → tdx_quant
- 工具映射 UI 编辑 — 写死到代码
- 板块类/财务类/技术指标类工具代理 — 用户范围外
- 跨会话代理决策记忆(学习用户偏好) — YAGNI

## 8. Acceptance Criteria

1. Console "代理设置" 子标签可见,可开关 "启用 TDX_QUANT 代理"
2. 开启后,调 `get_quote(600519.SH)` 在 tdx_quant 健康时返回结果,日志显示走的 tdx_quant
3. 关闭 tdx_quant 模拟场景下(force UNAVAILABLE),开启代理仍能成功 (降级到 mootdx2)
4. 关闭代理开关 → 行为与原版一致,所有请求走 mootdx2
5. `config/proxy_settings.yaml` 持久化生效,重启 gateway 后仍保持
6. 鉴权生效: 未登录访问 `/api/proxy/settings` 返 401
7. 7 个被代理工具的返回字段格式与原 mootdx2 一致(用户无感)
8. `get_trade` / `get_trade_history` 不被代理
