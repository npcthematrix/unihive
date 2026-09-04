# 危险工具确认门移除 — 设计文档

**项目**: UNIHIVE MCP Gateway
**时间**: 2026-09-04
**状态**: 设计稿 (待实施)
**父文档**: [`2026-09-04-unihive-mcp-review.md`](./2026-09-04-unihive-mcp-review.md) — T-3 引入的 confirm 门在此撤销

## 1. 背景

`c63d05f feat(registry): gate dangerous tools behind an explicit confirm flag` 在 `src/registry.py` 给 18 个 dangerous tool 加了一道运行时确认门：调用方必须传 `confirm=true`，否则工具返回 `requires_confirmation: True` 信封，**不触达上游**。

**用户决策 (2026-09-04)**：撤掉这道门。任何入口 (LLM agent / stdio / HTTP JSON-RPC / 程序化调用) 调用危险工具都直接执行，不再要求 `confirm=true`。

**撤销理由**：
- confirm 门是 per-call 摩擦，调用方常忘记传导致工具被静默拒绝
- 调用方应自行负责"是否要调"（运维侧责任，而非网关侧）
- 真正的安全约束应在部署层：哪些 caller 允许调危险工具、用什么凭据、调到什么粒度

**保留什么**：
- `dangerous: true` 标记本身：仍驱动 (a) 工具描述前缀 `⚠️ DANGER`，(b) 每次调用写一条 WARNING 审计日志
- console UI 的危险工具红徽章

**撤掉什么**：
- `confirm` 参数（从所有 18 个 dangerous tool 的 public 签名中删除）
- `requires_confirmation` 信封返回路径
- `_CONFIRM_DOC` 长篇说明（"必须先向用户说明...confirm=true...未确认的调用会被网关拒绝"）
- `disable_dangerous` operator 侧 kill switch（与 confirm 门正交，但用户要求一并简化）
- `TestDangerousConfirmation` 整套测试

## 2. 用户决策记录

| 问题 | 选择 |
|------|------|
| 哪些入口需要 confirm？ | **所有入口都不要**（撤掉整道门） |
| public signature 保留 `confirm` 参数吗？ | **完全删除**（clean schema） |
| 需要什么审计记录？ | **保留现有 WARNING 日志**（不加新 audit 表/接口） |
| 保留 `disable_dangerous` kill switch 吗？ | **撤掉**（与 confirm 门一并简化） |
| 描述里保留 ⚠️ DANGER 徽章吗？ | **保留徽章 + 简短一句**（"⚠️ DANGER X (高风险写操作，操作不可逆)"） |

## 3. 行为对比

### 3.1 当前 (撤回前)

```python
# 注册时:
async def _runtime(**kwargs):
    if dangerous and not kwargs.get("confirm"):
        logger.warning("DANGEROUS call rejected as unconfirmed: %s", name)
        return _confirmation_required(name)        # ← 拦截, 不触达上游
    ...

# 调用方:
await mcp.call("delete_sector", {"sector": "自选"})
# → {"success": false, "error": "...confirm=true...", "requires_confirmation": true}

await mcp.call("delete_sector", {"sector": "自选", "confirm": True})
# → 真删除, 透传上游响应
```

### 3.2 撤回后

```python
# 注册时:
async def _runtime(**kwargs):
    if dangerous:
        logger.warning("DANGEROUS call: %s args=%s", name, normalized)
    return await server._execute_cached(name, normalized, ...)

# 调用方:
await mcp.call("delete_sector", {"sector": "自选"})
# → WARNING 日志 + 真删除 + 透传上游响应
# 签名中无 confirm, 调用方无需也不可传
```

## 4. 组件变更

### 4.1 `src/registry.py`

**删除**：
- 常量 `_CONFIRM_PARAM`、`_CONFIRM_SPEC`、`_CONFIRM_DOC`
- 函数 `_confirmation_required()`
- `register_tools_from_config()` 的 `disable_dangerous: bool = False` 参数及其内部 `if spec.get("dangerous") and disable_dangerous` 分支
- `build_tool_function()` 中的 `signature_specs = param_specs + [_CONFIRM_SPEC] if dangerous else param_specs` 逻辑（直接用 `param_specs`）
- `_runtime` 函数体开头的 `if dangerous and not kwargs.get(_CONFIRM_PARAM): return _confirmation_required(name)` 守卫
- `__doc__` 末尾的 `_CONFIRM_DOC` 整段拼接——危险工具的 `__doc__` 直接用 YAML 里的 `description` 原样（YAML 已自带 ⚠️ DANGER 前缀）

**保留**：
- `_TYPE_MAP`、`_DEFAULT_MAP`、`_build_parameter`、`build_signature`、`_normalize_param` 工具函数
- `build_tool_function()` 中 `dangerous: bool = spec.get("dangerous", False)` 解析
- `_runtime` 中 `if dangerous: logger.warning(...)` 审计日志
- `validate_specs()` 不动（与 confirm 门无关）

**最终形态** (核心 30 行)：

```python
def build_tool_function(spec: dict, server: Any) -> Callable:
    name: str = spec["name"]
    description: str = spec.get("description", "")
    route_key: str = spec.get("routing", name)
    ttl_key: str | None = spec.get("cache_ttl_key")
    param_specs: list[dict] = spec.get("params", [])
    dangerous: bool = spec.get("dangerous", False)

    sig = build_signature(param_specs)

    async def _runtime(**kwargs) -> dict:
        normalized: dict = {}
        for p in param_specs:
            v = kwargs.get(p["name"])
            v = _normalize_param(p["name"], v, param_specs)
            if v is None or v == "":
                continue
            normalized[p["name"]] = v
        if dangerous:
            logger.warning("DANGEROUS call: %s args=%s", name, normalized)
        return await server._execute_cached(
            name, normalized, route_key=route_key, ttl_key=ttl_key
        )

    _runtime.__signature__ = sig
    _runtime.__annotations__ = {
        p["name"]: _TYPE_MAP.get(p.get("type", "str"), str) for p in param_specs
    }
    _runtime.__annotations__["return"] = dict
    _runtime.__name__ = name
    _runtime.__doc__ = description
    return _runtime


def register_tools_from_config(server: Any, specs: list[dict]) -> list[str]:
    """注册所有 spec 为 FastMCP tool。返回注册的 name 列表。"""
    registered: list[str] = []
    for spec in specs:
        fn = build_tool_function(spec, server)
        server.mcp.tool()(fn)
        registered.append(spec["name"])
    return registered
```

### 4.2 `src/gateway_server.py`

**删除**：
- `disable_dangerous = self.config.get("disable_dangerous", False)` 读取（约 181 行）
- `register_tools_from_config(self, specs, disable_dangerous=disable_dangerous)` 调用
- 注册日志行中的 `(dangerous=..., disabled=disable_dangerous)` 字段

**保留**：其余不变。

### 4.3 `config/upstreams.yaml` / `config/tools_tdx_tq_local.yaml`

**保留**：`dangerous: true` 标记（18 处，1 在 upstreams.yaml + 17 在 tools_tdx_tq_local.yaml）。仍驱动 ⚠️ 徽章与 WARNING 日志。

**当前状态盘点**（写代码前要读一遍）：
- **4 个工具已有完整中文描述 + ⚠️ DANGER 前缀**：`tdx_call`（upstreams.yaml:307）、`send_user_block` / `create_sector` / `delete_sector`（tools_tdx_tq_local.yaml）
- **14 个工具的 `description` 字段是 placeholder**（值就是工具名本身）：`refresh_cache` / `refresh_kline` / `download_file` / `send_message` / `send_file` / `send_warn` / `send_bt_data` / `exec_to_tdx` / `rename_sector` / `clear_sector` / `formula_set_data` / `formula_set_data_info` / `order_stock` / `cancel_order_stock` —— 调用方看不到"危险"信号

**修订**（双管齐下）：
1. **YAML 侧**：14 个 placeholder 描述全部补上 `⚠️ DANGER <tool_name> (高风险写操作，操作不可逆)`，与已有 4 个的措辞风格一致
2. **运行时侧**：`build_tool_function` 不再拼接 docstring 后缀——危险工具的 `__doc__` 就是 YAML 的 `description` 原样，4 个已有工具保持原描述不变，14 个新补的工具自带 ⚠️

**不删** 已有的 `description` 字段值（YAML 描述是 ground truth；不再运行时覆盖，避免双重拼接或意外截断）。

### 4.4 `tests/test_registry.py`

**删除**：
- `TestDangerousConfirmation` 整个 class（8 个测试方法）
- `TestRegisterToolsFromConfig::test_skips_dangerous_when_disabled`
- `TestRegisterToolsFromConfig::test_includes_dangerous_by_default`

**修改**：
- `TestBuildToolFunction::test_dangerous_logs_warning`：去掉 `confirm=True` 参数；断言 WARNING 日志内容**不含** "rejected" 字样

**新增**：
- `test_dangerous_tool_signature_has_no_confirm`：构造 dangerous spec，断言 `inspect.signature(fn).parameters` 中无 `confirm`
- `test_dangerous_tool_calls_execute_cached_immediately`：构造 dangerous spec，不传 confirm，断言直接进入 `_execute_cached`（不像撤回前那样返回 `requires_confirmation: True`）

**测试计数**：126 → -10 (删) + 2 (加) = **118 passed**。

### 4.5 `docs/superpowers/specs/2026-09-04-unihive-mcp-review.md`

**修订**："P3 future work" 一节里关于 confirm 门的描述（"18 个 dangerous tool 的生成签名新增 `confirm: bool = False`..."）改为：

> ~~tdx_call 等危险 tool 在 FastMCP 层加独立 confirmation hook~~ → **已撤回 (2026-09-04)**
>
> 设计见 [`2026-09-04-dangerous-tool-confirmation-removal-design.md`](./2026-09-04-dangerous-tool-confirmation-removal-design.md)。危险工具仅保留 (a) 描述前缀 `⚠️ DANGER`，(b) 每次调用写 WARNING 审计日志。运行时无确认门，签名中无 `confirm` 参数。

**T-3 严重度条目不动**——它当时识别的问题（"危险 tool 无 ⚠️ 警告"）仍通过徽章+日志解决。

## 5. 数据流

### 5.1 危险工具调用（撤回后）

```
caller (LLM agent / HTTP / stdio)
  → JSON-RPC: tools/call {name: "delete_sector", arguments: {sector: "自选"}}
    → FastMCP 路由 → _runtime(sector="自选")
      → logger.warning("DANGEROUS call: delete_sector args={'sector': '自选'}")
        → server._execute_cached("delete_sector", {"sector": "自选"}, ...)
          → 上游 RPC → 真实删除
      ← 上游响应信封
    ← JSON-RPC 响应
```

### 5.2 安全工具调用（不变）

```
caller → JSON-RPC → _runtime(...) → _execute_cached(...) → 上游
```

两条路径**结构相同**，仅差 `if dangerous: logger.warning(...)` 一行。

## 6. 错误处理

- **上游失败**：原样透传 `_execute_cached` 返回的 `{success: False, error: ...}`
- **签名校验失败**：FastMCP 层的 JSON Schema 校验在调用前拦截，与现有行为一致
- **空字符串/None 参数**：`_runtime` 仍按 `if v is None or v == "": continue` 跳过，与现有行为一致
- **新的失败模式**：无。撤回的是拦截，不是引入新路径

## 7. 安全姿态变更 (Security Posture Change)

**撤回前**：网关注册层是危险工具的最后一道闸门——LLM agent 即使被 prompt injection 攻击，也无法直接调 `delete_sector`；必须先让用户显式传 `confirm=true`。

**撤回后**：网关层不再拦截。安全完全依赖：
- **部署层**：哪些 caller 接入网关、用什么 token、token 是否绑 scope（当前 `GATEWAY_BEARER_TOKEN` 是单 token 不分 scope）
- **上游层**：TQ 交易终端本身是否有二次确认 / OTP / 风控
- **运维层**：WARNING 日志人工 review、异常行为告警

**已知遗留风险**（用户接受）：
- 持有 `GATEWAY_BEARER_TOKEN` 的任何程序都能调 `delete_sector`
- LLM agent 被 prompt injection 后可借 token 直接执行破坏性操作
- 不在本文档范围内，但应在后续单独的"部署安全加固"任务中处理（不在本 PR 解决）

## 8. 兼容性

- **Breaking change**：所有 18 个 dangerous tool 的 MCP 协议 schema 中 `confirm` 参数消失
- **影响方**：
  - LLM agent prompt / function calling 模板：若硬编码 `confirm: True`，会变成"未识别参数"。需在调用方移除。**建议**在 PR description / docs 中明确告知。
  - 旧测试 / 旧 client：未升级前对 18 个 tool 的调用不会立即失败（FastMCP 默认忽略未声明参数），但 confirm 的副作用不再是放行，而是无害噪声。
- **回滚**：revert 提交即可恢复 confirm 门。

## 9. 验收标准

- [ ] `src/registry.py` 中无 `_CONFIRM_*` 常量、无 `_confirmation_required` 函数、无 `disable_dangerous` 关键字
- [ ] 18 个 dangerous tool 的 `inspect.signature` 中无 `confirm` 参数
- [ ] 18 个 dangerous tool 的调用不传 `confirm` 也能直接进入 `_execute_cached`（用单元测试断言 `server.calls` 非空）
- [ ] 每次 dangerous tool 调用仍产生一条 WARNING 日志
- [ ] 18 个 dangerous tool 的描述仍以 `⚠️ DANGER` 开头（或后接简短警告）
- [ ] `pytest -q` 输出 `118 passed`
- [ ] 旧 review spec 的 P3 段已标注"已撤回"并指向新 spec
- [ ] stdio 启动无新增 ERROR/WARNING（仅保留 dangerous tool 的运行时审计 WARNING，与撤回前数量一致）
