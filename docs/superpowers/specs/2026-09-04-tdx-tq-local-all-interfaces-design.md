# TQ-Local 全量接口对外发布 — 设计

**日期**: 2026-09-04
**状态**: 待用户审核

---

## 目标

把 TdxW 内置 TQ-Local HTTP JSON-RPC 服务（端口 17709）暴露的全部 ~55 个接口，
通过 UniHive MCP 网关对家庭/局域网内的多设备 MCP 客户端开放，并通过 Bearer Token 鉴权。

不重新实现接口，不修改 TdxW 客户端；只让 UniHive 把这些方法原样转发为 MCP tool。

---

## 背景

UniHive 已在 commit `745108e` 中通过新增的 `HttpJsonRpcClient` 把 5 个 TQ-Local 接口
（`get_user_sector`, `get_stock_list_in_sector`, `send_user_block`,
`create_sector`, `delete_sector`）注册为 MCP 工具。commit `04cd48b` 给
控制台 `/api/status` 加了 `http_jsonrpc` 探测分支，验证端到端可用（19 只自选股
真实返回）。

本设计把这一机制扩展到 TQ-Local skill 文档列出的全部接口（含行情/财务/板块/
公式/交易/本地交互），并补上之前缺失的鉴权层。

源文件 `~/.claude/skills/tdx-tq-local/SKILL.md` v1.0.12 列出 55 个 method，
分 6 大类：行情与基础数据、刷新与本地交互、板块管理、专业数据、公式接口、交易。

---

## 已澄清的关键决策（来自 brainstorming）

| # | 决策点 | 选择 |
|---|--------|------|
| 1 | 受众 | 家庭/局域网内多设备 |
| 2 | 交易接口 | 全暴露（含 `order_stock`/`cancel_order_stock`） |
| 3 | 注册方式 | 脚本从 SKILL.md 代码生成 |
| 4 | 鉴权 | 全网关 Bearer Token 中间件 |
| 5 | 命名 | 平铺（tqcenter 原名，不加前缀） |
| 6 | 现有 5 个手工工具 | 直接覆盖替换 |
| 7 | 交易接口额外护栏 | 不加（仅 token + 局域网） |
| 8 | 缓存 | 全部不缓存 |

---

## 架构

```
                ┌──────────────────────────┐
                │  ~/.claude/skills/       │
                │  tdx-tq-local/SKILL.md   │  ← 源（人维护）
                │  (55+ methods)           │
                └────────────┬─────────────┘
                             │ parse (codegen)
                             ▼
                ┌──────────────────────────┐
                │ scripts/                 │
                │ gen_tdx_tq_local_tools.py│  ← 新增
                └────────────┬─────────────┘
                             │ write
                             ▼
                ┌──────────────────────────┐
                │ config/                  │
                │ tools_tdx_tq_local.yaml  │  ← 新生成，入 git
                └────────────┬─────────────┘
                             │ load + merge at startup
                             ▼
┌─────────┐    ┌─────────────────────┐    ┌──────────────────────┐
│ MCP     │    │  Gateway Server     │    │ config/upstreams.yaml│
│ Clients │◀──▶│  (18081, Streamable │◀──▶│  (手工，含 token 配置)│
│         │    │   HTTP + Bearer)    │    └──────────────────────┘
└─────────┘    └──────────┬──────────┘
                ▲         │ JSON-RPC POST
                │ Auth    ▼
                │ MW ┌──────────────────┐
                └───┤ TdxW.exe :17709  │
                    │ (TQ-Local)       │
                    └──────────────────┘
```

---

## 组件

### C1. `scripts/gen_tdx_tq_local_tools.py`（新，~250 行）

输入：`~/.claude/skills/tdx-tq-local/SKILL.md`
输出：`config/tools_tdx_tq_local.yaml`

解析流程：
1. 按 `^#### \`(.+?)\`:` 正则提取每个 method 标题
2. 抓取标题下方首个 markdown 表格 → 参数表（列：`参数 | 必填 | 类型 | 说明`）
3. 抓取标题前一段无表格文字 → description 兜底
4. 推断 `dangerous: true` 的规则（前缀/全名匹配）：
   - `order_*`, `cancel_*`, `create_*`, `delete_*`, `clear_*`, `rename_*`
   - `send_user_block`, `exec_to_tdx`, `formula_set_data*`
   - `send_message`, `send_file`, `send_warn`, `send_bt_data`, `download_file`
   - `refresh_cache`, `refresh_kline`（写本地缓存）

输出 schema 与 `src/registry.py` 兼容：
```yaml
tools:
  - name: get_market_data
    description: "K线/分钟线/历史行情"
    routing: get_market_data
    upstream_tool_mapping:
      tdx_tq_local: get_market_data
    cache_ttl_key: null
    dangerous: false
    params:
      - name: stock_list
        required: true
        type: list[str]
      - name: period
        required: true
        type: str
```

CLI：
- 默认：解析并写入
- `--dry-run`：解析但不写文件，打印统计
- `--check`：CI 用，写入临时文件后 `git diff` 若非空则 exit 1

退出码：`0` 成功，`1` 解析失败，`2` IO 错误。

### C2. `config/tools_tdx_tq_local.yaml`（新，生成）

- 顶层 `tools:` 列表
- 入 git（透明、可审计、diff 友好）
- 跑一次 codegen → diff → review → commit

### C3. `src/gateway_server.py` 改动（小）

启动时合并两个工具源：
```python
def _load_all_tools() -> list[dict]:
    cfg_tools = self.config.get("tools", []) or []
    gen_path = Path("config/tools_tdx_tq_local.yaml")
    if gen_path.exists():
        with gen_path.open(encoding="utf-8") as f:
            gen_cfg = yaml.safe_load(f) or {}
        gen_tools = gen_cfg.get("tools", []) or []
        return cfg_tools + gen_tools
    return cfg_tools
```

合并后传给 `register_tools_from_config`。无外部接口变化。

### C4. `src/auth_middleware.py`（新，~60 行）

```python
class BearerTokenMiddleware:
    """Starlette 中间件：除 /health 与控制台路径外验 Bearer token"""
    PUBLIC_PATHS = ("/health", "/", "/api/")

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if any(path == p or path.startswith(p) for p in self.PUBLIC_PATHS):
            return await self.app(scope, receive, send)
        # 校验 Authorization header
        ...
```

接入位置：`serve_http` 里 `uvicorn.Config(mcp_app, ...)` 前包一层
`BearerTokenMiddleware(mcp_app, token)`。

### C5. Token 配置

- 环境变量：`GATEWAY_BEARER_TOKEN`
- 现有 `scripts/_load_env.ps1` 已统一加载 .env，新增该变量到 `.env.example`
- `gateway_server.py` 启动时检查：HTTP 模式下 token 必须存在；stdio 模式不强求
- 缺失则 `raise RuntimeError("GATEWAY_BEARER_TOKEN must be set for HTTP mode")`

---

## 数据流

### D1. Codegen 流（开发者本地）

```
SKILL.md 改动
   │
   ▼
$ python scripts/gen_tdx_tq_local_tools.py
   │  解析 → 输出 config/tools_tdx_tq_local.yaml
   ▼
git diff config/tools_tdx_tq_local.yaml
   │  人工 review
   ▼
git commit -m "feat(tdx-tools): regenerate from SKILL.md v1.0.13"
```

### D2. 请求流（运行时）

```
MCP Client
   │ POST /mcp  Authorization: Bearer <token>
   ▼
[BearerTokenMiddleware]
   │ 401 if token missing/invalid (skip for public paths)
   ▼
[FastMCP Streamable HTTP app]
   ▼
[registered tool wrapper]
   │ router.route("get_market_data", params)
   ▼
[Router] chain = ["tdx_tq_local"]
   ▼
[HttpJsonRpcClient]
   │ POST http://127.0.0.1:17709/
   │ {"jsonrpc":"2.0","id":N,"method":"...","params":{...}}
   ▼
[TdxW.exe TQ-Local]
   ▼
[HttpJsonRpcClient] → Router → Normalizer.to_gateway_response()
   ▼
{ success, data, error, source, hops }
   ▼
MCP Client
```

---

## 错误处理

### E1. Codegen 解析失败

| 场景 | 行为 |
|------|------|
| SKILL.md 找不到 method 标题 | 打印警告，跳过该 section |
| 参数表列数不对 | 抛 `CodegenParseError` 含行号、原文片段 |
| 必填列缺失 | 同上 |
| 同一 method 重复 | 抛错（保证生成唯一性） |

### E2. Auth 中间件

| 场景 | 行为 |
|------|------|
| `Authorization` 头缺失 | `401` + `WWW-Authenticate: Bearer realm="unihive"` |
| Token 不匹配 | `401` + JSON `{"error":"invalid_token"}` |
| Token 过期 | v1 不实现（局域网低风险） |
| 启动时 token 缺失 | HTTP 模式 `RuntimeError`；stdio 模式仅 warning |
| `/health`, `/`, `/api/*` | 放行（控制台独立绑 127.0.0.1） |
| `/mcp` | 必须带 token |

注：`/api/*` 放行是因为 console_server 独立运行且绑 `127.0.0.1`，不会被外网触达；
此处只对 gateway 自身的 `/api/*` 入口（如果未来合并）做白名单。

### E3. 上游调用失败（沿用现有 Router）

- 单上游失败 → 标记 hop success=false
- 全失败 → `{success:false, error:"All upstream failed: {...}"}`
- 超时（10s 默认）→ `{success:false, error:"连接超时"}`
- TQ-Local 返回 `result.error` → 透传 `{success:false, error:<原文>, source}`
- TQ-Local 返回非法 JSON → 上游 degraded（不缓存），错误截断 200 字符

### E4. 日志

- 中间件 401：`logger.warning("[auth] rejected from {ip} path={path}")`
- codegen parse 错误日志到 stderr
- 上游错误：已有 `logger.error` 在 `http_jsonrpc_client.py`

---

## 测试

### T1. Codegen 单元测试 `tests/test_gen_tdx_tq_local_tools.py`

| 用例 | 验证 |
|------|------|
| `test_parses_known_method_get_market_data` | `stock_list/period/count` 正确识别为 required |
| `test_dangerous_inference_order_stock` | `order_stock` → `dangerous: true` |
| `test_safe_inference_get_market_snapshot` | 读类接口 → `dangerous: false` |
| `test_missing_param_table_skipped` | 缺表的 method 不写入，仅警告 |
| `test_generated_yaml_loads_with_safe_load` | 输出能被 yaml.safe_load 解析 |
| `test_idempotent` | 跑两次输出 byte-identical（去掉时间戳） |
| `test_handles_section_heading_no_colon` | 兼容性边界 |
| `test_dangerous_predicate_completeness` | 55 个 method 都被规则覆盖 |

### T2. Auth 中间件单元测试 `tests/test_auth_middleware.py`

用 `httpx.AsyncClient` + `asgi-lifespan` 跑真实 ASGI 调用：

| 用例 | 验证 |
|------|------|
| `test_missing_authorization_returns_401` | `GET /mcp` 无 header → 401 |
| `test_wrong_token_returns_401` | `Bearer wrong` → 401 + `invalid_token` |
| `test_correct_token_passes_through` | 正确 token → 抵达下游 |
| `test_health_bypasses_auth` | `GET /health` 无 token → 200 |
| `test_root_bypasses_auth` | `GET /` 无 token → 200 |
| `test_case_insensitive_scheme` | `bearer xxx` 与 `Bearer xxx` 等价 |
| `test_malformed_header_returns_401` | `Authorization: xxx` 无 `Bearer ` → 401 |
| `test_non_http_scope_passthrough` | lifespan/websocket 事件不受影响 |

### T3. 集成 / E2E

| 类型 | 范围 | CI 策略 |
|------|------|---------|
| 合并 tools 注册 | `test_load_all_tools_merges_configs` | 跑 |
| End-to-end MCP 调用 | 用 `httpx.MockTransport` mock TQ-Local | 跑 |
| 真实 TQ-Local 端到端 | TdxW.exe 起来时手测 | 跳过 CI |

### T4. 覆盖率目标

- codegen：≥ 85%
- auth middleware：≥ 90%
- 集成路径：≥ 75%

---

## 不在本设计范围

- OAuth 2.1 / 多客户端授权（MCP 官方标准） — 后续若有第三方客户端需要时再做
- Token 过期/刷新 — 局域网场景低风险
- 写操作二次确认（confirm_token） — 用户明确不加
- 缓存 — 用户明确不加
- 任何前端 UI 改动 — console.html 暂不展示生成出来的工具列表（仍由 registry 提供）
- 公式接口的高级语义（如选股公式的执行反馈回路） — 接口透传即可，语义留给调用方

---

## 后续跟踪

- SKILL.md 升到 v1.0.13+ 时跑 codegen 并 review diff
- 真正对外开放（公网）时再做 OAuth 2.1 + 限流 + 完整审计
- console.html 可选增强：把生成出来的工具按类别折叠展示

---

## 复审清单（self-review）

- [x] 无 TBD/TODO 占位
- [x] 内部一致：架构图、组件、数据流、错误处理、测试五节互相印证
- [x] 范围聚焦：单一主题（TQ-Local 全量接口发布 + 鉴权），不混入其他
- [x] 无歧义：8 个澄清点都已具体到可执行
- [x] 命名与现有代码一致（`HttpJsonRpcClient`, `register_tools_from_config` 等）
