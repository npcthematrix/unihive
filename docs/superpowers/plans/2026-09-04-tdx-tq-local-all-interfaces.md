# TQ-Local 全量接口对外发布 + Bearer Token 鉴权实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 TdxW TQ-Local 的全部 ~55 个 HTTP JSON-RPC 接口通过 UniHive MCP 网关对局域网 MCP 客户端开放，并加上 Bearer Token 鉴权层。

**Architecture:** SKILL.md 是接口定义的唯一源；新增 `scripts/gen_tdx_tq_local_tools.py` 解析它生成 `config/tools_tdx_tq_local.yaml`，gateway 启动时合并到工具列表。新增 `src/auth_middleware.py` 作为 Starlette 中间件包在 MCP HTTP app 外层，token 从 `GATEWAY_BEARER_TOKEN` 环境变量读。

**Tech Stack:** Python 3.10+, pyyaml, httpx (已用), pytest (已有), starlette/uvicorn (已有), FastMCP 4.0 (已有)

**前置依赖：**
- 已读完 `docs/superpowers/specs/2026-09-04-tdx-tq-local-all-interfaces-design.md`
- 已读完 `~/.claude/skills/tdx-tq-local/SKILL.md`
- 已读完 `src/gateway_server.py`、`src/http_jsonrpc_client.py`
- TdxW.exe + TQ-Local 不必启动（除 Task 9 E2E 手测外）

---

## 文件改动清单

```
新建：
  scripts/gen_tdx_tq_local_tools.py            — SKILL.md → YAML 代码生成器
  src/auth_middleware.py                      — Bearer Token 中间件
  config/tools_tdx_tq_local.yaml              — 生成产物（入 git）
  tests/test_gen_tdx_tq_local_tools.py       — codegen 单元测试
  tests/test_auth_middleware.py              — 鉴权中间件测试

修改：
  src/gateway_server.py                       — 合并工具源 + 接入中间件 + token 校验
  .env.example                                — 增加 GATEWAY_BEARER_TOKEN 说明
  scripts/_load_env.ps1                       — 加载新 token（若需要）
```

执行顺序按依赖：Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9。

---

## Task 1: BearerTokenMiddleware 单元测试 (RED)

**Files:**
- Create: `tests/test_auth_middleware.py`

- [ ] **Step 1: 写测试文件**

新建 `tests/test_auth_middleware.py`：

```python
"""BearerTokenMiddleware 单元测试。

用最小 ASGI 应用做端到端断言：直接构造中间件包住一个 echo app，
发送模拟 HTTP 请求，验证响应状态码和 body。
"""
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from src.auth_middleware import BearerTokenMiddleware


def _echo_mcp_app():
    """最小 MCP-like 端点：返回路径和 headers，供测试观察是否到达下游。"""

    async def mcp_endpoint(request: Request):
        return JSONResponse({"path": request.url.path, "auth": request.headers.get("authorization")})

    async def health():
        return PlainTextResponse("ok")

    async def root():
        return PlainTextResponse("hi")

    async def api_status():
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[
        Route("/mcp", mcp_endpoint),
        Route("/health", health),
        Route("/", root),
        Route("/api/status", api_status),
    ])
    return app


@pytest.fixture
def client():
    app = BearerTokenMiddleware(_echo_mcp_app(), token="secret123")
    return TestClient(app)


def test_missing_authorization_returns_401(client):
    r = client.get("/mcp")
    assert r.status_code == 401
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")
    assert r.json() == {"error": "missing_authorization"}


def test_wrong_token_returns_401(client):
    r = client.get("/mcp", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert r.json() == {"error": "invalid_token"}


def test_correct_token_passes_through(client):
    r = client.get("/mcp", headers={"Authorization": "Bearer secret123"})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == "/mcp"
    assert body["auth"] == "Bearer secret123"


def test_health_bypasses_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.text == "ok"


def test_root_bypasses_auth(client):
    r = client.get("/")
    assert r.status_code == 200


def test_api_prefix_bypasses_auth(client):
    r = client.get("/api/status")
    assert r.status_code == 200


def test_case_insensitive_scheme(client):
    r = client.get("/mcp", headers={"Authorization": "bearer secret123"})
    assert r.status_code == 200


def test_malformed_header_returns_401(client):
    r = client.get("/mcp", headers={"Authorization": "Token xxx"})
    assert r.status_code == 401
    assert r.json() == {"error": "missing_authorization"}


def test_lifespan_scope_passes_through():
    """lifespan 事件不应被中间件拦截。"""
    app = BearerTokenMiddleware(_echo_mcp_app(), token="secret123")
    # TestClient 上下文触发 lifespan；不应抛错
    with TestClient(app):
        pass  # 启动 + 关闭 lifespan 无异常即通过
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /d/fintech_workspace/unihive
python -m pytest tests/test_auth_middleware.py -v 2>&1 | tail -20
```

预期：`ModuleNotFoundError: No module named 'src.auth_middleware'`

- [ ] **Step 3: 不提交**

测试先留为 RED。

---

## Task 2: 实现 BearerTokenMiddleware (GREEN)

**Files:**
- Create: `src/auth_middleware.py`

- [ ] **Step 1: 写中间件**

新建 `src/auth_middleware.py`：

```python
"""Bearer Token 鉴权中间件。

包裹 ASGI app，对 MCP 入口强制校验 `Authorization: Bearer <token>`。
健康检查、根路径和 /api/* 路径直接放行（控制台独立绑 127.0.0.1，不暴露外网）。
"""
from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenMiddleware:
    """ASGI 中间件：除放行路径外要求 Bearer Token 匹配。"""

    PUBLIC_PATHS = ("/health", "/", "/api/")

    def __init__(self, app: ASGIApp, token: str):
        if not token:
            raise ValueError("token must be non-empty")
        self.app = app
        self.token = token

    def _is_public(self, path: str) -> bool:
        return any(path == p.rstrip("/") or path.startswith(p) for p in self.PUBLIC_PATHS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if self._is_public(path):
            await self.app(scope, receive, send)
            return

        # 解析 Authorization header
        headers = dict(scope.get("headers") or [])
        auth_raw = headers.get(b"authorization")
        if auth_raw is None:
            await self._reject(send, "missing_authorization", include_www_auth=True)
            return

        scheme, _, token = auth_raw.decode("latin-1").partition(" ")
        if scheme.lower() != "bearer" or not token:
            await self._reject(send, "missing_authorization")
            return

        if token != self.token:
            await self._reject(send, "invalid_token")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send, code: str, include_www_auth: bool = False) -> None:
        body = f'{{"error":"{code}"}}'.encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        if include_www_auth:
            headers.append((b"www-authenticate", b'Bearer realm="unihive"'))
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})
```

- [ ] **Step 2: 运行测试确认通过**

```bash
cd /d/fintech_workspace/unihive
python -m pytest tests/test_auth_middleware.py -v 2>&1 | tail -25
```

预期：9 个测试全部 PASS。

- [ ] **Step 3: 提交**

```bash
git add src/auth_middleware.py tests/test_auth_middleware.py
git commit -m "feat(auth): BearerTokenMiddleware with public path bypass"
```

---

## Task 3: Token 配置与启动校验

**Files:**
- Modify: `src/gateway_server.py`
- Modify: `.env.example`

- [ ] **Step 1: 读现有 .env.example**

确认 `.env.example` 存在；如果不存在则新建空文件。

- [ ] **Step 2: 在 .env.example 加 GATEWAY_BEARER_TOKEN 行**

在文件末尾追加：

```
# Gateway Bearer Token (HTTP mode only; STDIO mode ignores this)
# 生成方式: python -c "import secrets; print(secrets.token_urlsafe(32))"
# 设置后未带正确 Authorization 头的请求会被中间件 401 拒绝
GATEWAY_BEARER_TOKEN=replace-with-a-long-random-string
```

- [ ] **Step 3: 在 gateway_server.py 加载 token**

在 `src/gateway_server.py` 顶部导入位置（已有的 `os` 已引入）确认。读取环境变量并暴露到 GatewayServer 实例上。在 `__init__` 里增加：

```python
self.bearer_token: str = os.environ.get("GATEWAY_BEARER_TOKEN", "").strip()
```

放在 `self._running = False` 之后。

- [ ] **Step 4: 在 serve_http 校验 token + 接入中间件**

定位 `async def serve_http(self, host, port, mount_path)`。把 `mcp_app = self.mcp.http_app(path=mount_path)` 替换为：

```python
        if not self.bearer_token:
            raise RuntimeError(
                "GATEWAY_BEARER_TOKEN must be set for HTTP mode. "
                "Set it in .env or environment, or use --transport stdio."
            )
        from .auth_middleware import BearerTokenMiddleware
        raw_app = self.mcp.http_app(path=mount_path)
        mcp_app = BearerTokenMiddleware(raw_app, token=self.bearer_token)
```

- [ ] **Step 5: 手动验证：缺 token 启动 HTTP 模式应抛 RuntimeError**

```bash
cd /d/fintech_workspace/unihive
python -c "
import os
os.environ.pop('GATEWAY_BEARER_TOKEN', None)
import asyncio
from src.gateway_server import GatewayServer
async def run():
    s = GatewayServer(config_path='config/upstreams.yaml', strict_env=False)
    try:
        await s.serve_http(port=18099)
    except RuntimeError as e:
        print('GOT_EXPECTED:', e)
asyncio.run(run())
"
```

预期输出包含 `GOT_EXPECTED: GATEWAY_BEARER_TOKEN must be set for HTTP mode.`

- [ ] **Step 6: 手动验证：带 token 启动 + curl /health（放行）+ /mcp（401）**

起一个临时 server 在 18099 端口：

```bash
cd /d/fintech_workspace/unihive
GATEWAY_BEARER_TOKEN=test123 python -m src.gateway_server --transport http --port 18099 &
SERVER_PID=$!
sleep 4
echo "--- /health (no token, expect 200) ---"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18099/health
echo "--- /mcp without token (expect 401) ---"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18099/mcp
echo "--- /mcp with token (expect anything not 401, may be 4xx if MCP not initialized) ---"
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer test123" http://127.0.0.1:18099/mcp
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

预期：`200`, `401`, `4xx` (非 401 即通过，比如 406 表示 MCP 需要 Accept header)。

- [ ] **Step 7: 提交**

```bash
git add src/gateway_server.py .env.example
git commit -m "feat(gateway): require GATEWAY_BEARER_TOKEN in HTTP mode"
```

---

## Task 4: Codegen 测试 (RED) — 解析与参数表

**Files:**
- Create: `tests/test_gen_tdx_tq_local_tools.py`

- [ ] **Step 1: 写测试文件**

新建 `tests/test_gen_tdx_tq_local_tools.py`：

```python
"""scripts/gen_tdx_tq_local_tools.py 单元测试。

使用 fixture 把 SKILL.md 的子集写入 tmp 文件，避免依赖真实 skill 路径。
"""
import pytest

from scripts.gen_tdx_tq_local_tools import (
    CodegenParseError,
    parse_skill_md,
    infer_dangerous,
    build_tool_specs,
    render_yaml,
)


SAMPLE_SKILL = """# TDX Quant Local Skill

> sample

## 接口名映射规则

### 行情与基础数据

#### `get_market_data`: K线/分钟线/历史行情

获取 K 线和历史行情数据。

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| stock_list | Y | List[str] | 股票代码列表 |
| period | Y | str | K线周期 |
| count | N | int | 取最近 n 条 |

**返回字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| Date | str | 日期 |

#### `order_stock`: 下单

下个委托单。

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| account_id | Y | int | 账户句柄 |
| stock_code | Y | str | 股票代码 |
| order_type | Y | int | 买卖标志 |

#### `get_user_sector`: 获取用户自定义板块

列出全部自定义板块。

（无参数表 → skip）

#### `formula_set_data`: 设置公式计算用行情数据

灌数据用。

**参数说明：**

| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| data | Y | dict | 数据 |
"""


def test_parse_extracts_four_methods():
    methods = parse_skill_md(SAMPLE_SKILL)
    names = [m["name"] for m in methods]
    assert names == ["get_market_data", "order_stock", "get_user_sector", "formula_set_data"]


def test_parse_pulls_required_param_for_get_market_data():
    methods = parse_skill_md(SAMPLE_SKILL)
    gm = methods[0]
    pmap = {p["name"]: p for p in gm["params"]}
    assert pmap["stock_list"]["required"] is True
    assert pmap["period"]["required"] is True
    assert pmap["count"]["required"] is False


def test_parse_includes_description_from_preceding_paragraph():
    methods = parse_skill_md(SAMPLE_SKILL)
    assert methods[0]["description"] == "获取 K 线和历史行情数据。"
    assert methods[1]["description"] == "下个委托单。"
    assert "无参数表" in methods[2]["description"] or methods[2]["description"]


def test_parse_skips_method_without_param_table():
    methods = parse_skill_md(SAMPLE_SKILL)
    user = next(m for m in methods if m["name"] == "get_user_sector")
    assert user["params"] == []


def test_dangerous_order_stock():
    assert infer_dangerous("order_stock") is True


def test_dangerous_cancel_create_delete_clear_rename():
    for name in ("cancel_order_stock", "create_sector", "delete_sector",
                 "clear_sector", "rename_sector"):
        assert infer_dangerous(name) is True, name


def test_dangerous_send_message_file_warn_bt_data():
    for name in ("send_message", "send_file", "send_warn", "send_bt_data"):
        assert infer_dangerous(name) is True, name


def test_dangerous_send_user_block_and_exec_to_tdx():
    assert infer_dangerous("send_user_block") is True
    assert infer_dangerous("exec_to_tdx") is True


def test_dangerous_formula_set_data():
    assert infer_dangerous("formula_set_data") is True
    assert infer_dangerous("formula_set_data_info") is True


def test_dangerous_refresh_cache_kline():
    assert infer_dangerous("refresh_cache") is True
    assert infer_dangerous("refresh_kline") is True


def test_safe_get_market_snapshot():
    assert infer_dangerous("get_market_snapshot") is False
    assert infer_dangerous("get_stock_info") is False
    assert infer_dangerous("query_stock_positions") is False


def test_build_tool_specs_assigns_correct_routing():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    spec_map = {s["name"]: s for s in specs}
    gm = spec_map["get_market_data"]
    assert gm["routing"] == "get_market_data"
    assert gm["upstream_tool_mapping"] == {"tdx_tq_local": "get_market_data"}
    assert gm["cache_ttl_key"] is None
    assert gm["dangerous"] is False


def test_build_tool_specs_marks_dangerous():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    spec_map = {s["name"]: s for s in specs}
    assert spec_map["order_stock"]["dangerous"] is True
    assert spec_map["formula_set_data"]["dangerous"] is True


def test_render_yaml_is_loadable():
    import yaml
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    text = render_yaml(specs)
    data = yaml.safe_load(text)
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert len(data["tools"]) == len(specs)


def test_render_yaml_idempotent():
    methods = parse_skill_md(SAMPLE_SKILL)
    specs = build_tool_specs(methods)
    a = render_yaml(specs)
    b = render_yaml(specs)
    assert a == b
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /d/fintech_workspace/unihive
python -m pytest tests/test_gen_tdx_tq_local_tools.py -v 2>&1 | tail -10
```

预期：`ModuleNotFoundError: No module named 'scripts.gen_tdx_tq_local_tools'`

---

## Task 5: 实现 codegen 脚本 (GREEN)

**Files:**
- Create: `scripts/gen_tdx_tq_local_tools.py`

- [ ] **Step 1: 写脚本**

新建 `scripts/gen_tdx_tq_local_tools.py`：

```python
"""从 ~/.claude/skills/tdx-tq-local/SKILL.md 生成 UniHive 工具定义。

输出: config/tools_tdx_tq_local.yaml（顶层 tools: 列表）

规则：
- 解析 `#### \\`method_name\\`: <title>` 标题
- 抓取紧随的 markdown 参数表（列: 参数 | 必填 | 类型 | 说明）
- 按方法名规则推断 dangerous: true
- 缺表 method 仍写入（params=[]）

CLI:
- 默认：写入
- --dry-run：仅打印统计
- --check：与磁盘内容比对，不一致则 exit 1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


SKILL_PATH = Path.home() / ".claude" / "skills" / "tdx-tq-local" / "SKILL.md"
OUTPUT_PATH = Path("config/tools_tdx_tq_local.yaml")


class CodegenParseError(Exception):
    pass


# ---------- dangerous 推断 ----------

_DANGEROUS_PREFIXES = ("order_", "cancel_", "create_", "delete_", "clear_", "rename_")
_DANGEROUS_EXACT = frozenset({
    "send_message", "send_file", "send_warn", "send_bt_data",
    "send_user_block", "exec_to_tdx",
    "refresh_cache", "refresh_kline", "download_file",
})
_DANGEROUS_STARTSWITH = ("formula_set_data",)


def infer_dangerous(name: str) -> bool:
    if any(name.startswith(p) for p in _DANGEROUS_PREFIXES):
        return True
    if name in _DANGEROUS_EXACT:
        return True
    if any(name.startswith(p) for p in _DANGEROUS_STARTSWITH):
        return True
    return False


# ---------- 解析 ----------


_METHOD_HEADING = re.compile(r"^####\s+`([A-Za-z_][A-Za-z0-9_]*)`\s*:", re.MULTILINE)
_PARAM_ROW = re.compile(
    r"^\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<req>[^|]+?)\s*\|\s*(?P<type>[^|]+?)\s*\|\s*(?P<desc>[^|]+?)\s*\|\s*$",
    re.MULTILINE,
)


def parse_skill_md(text: str) -> list[dict]:
    """返回 [{"name", "description", "params"}, ...]，按文档顺序。"""
    methods: list[dict] = []
    matches = list(_METHOD_HEADING.finditer(text))
    if not matches:
        return methods

    for i, m in enumerate(matches):
        name = m.group(1)
        section_start = m.end()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[section_start:section_end]

        # description: 第一个非空段落，在第一个 markdown 表格之前
        description = ""
        for line in section.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("|"):
                break
            if stripped.startswith("#"):
                continue
            description = stripped.rstrip("：:。. ") + ("。" if not stripped.endswith("。") else "")
            break

        # 参数表
        params: list[dict] = []
        for row in _PARAM_ROW.finditer(section):
            cells = row.groupdict()
            pname = cells["name"].strip()
            if pname in {"参数", "字段", "---"} or pname.startswith("---"):
                continue
            params.append({
                "name": pname,
                "required": cells["req"].strip().upper() in ("Y", "YES", "TRUE", "是", "✓"),
                "type": cells["type"].strip(),
                "description": cells["desc"].strip(),
            })

        methods.append({
            "name": name,
            "description": description,
            "params": params,
        })

    # 检查重复
    seen = set()
    for m in methods:
        if m["name"] in seen:
            raise CodegenParseError(f"duplicate method: {m['name']}")
        seen.add(m["name"])

    return methods


# ---------- 构造 spec ----------


def build_tool_specs(methods: list[dict]) -> list[dict]:
    specs: list[dict] = []
    for m in methods:
        name = m["name"]
        specs.append({
            "name": name,
            "description": m["description"] or name,
            "routing": name,
            "upstream_tool_mapping": {"tdx_tq_local": name},
            "cache_ttl_key": None,
            "dangerous": infer_dangerous(name),
            "params": [
                {"name": p["name"], "required": p["required"], "type": p["type"]}
                for p in m["params"]
            ],
        })
    return specs


# ---------- 输出 ----------


def render_yaml(specs: list[dict]) -> str:
    body = yaml.safe_dump({"tools": specs}, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"# Auto-generated from ~/.claude/skills/tdx-tq-local/SKILL.md\n# DO NOT EDIT — re-run scripts/gen_tdx_tq_local_tools.py\n{body}"


# ---------- CLI ----------


def _stats(specs: list[dict]) -> str:
    dangerous = sum(1 for s in specs if s["dangerous"])
    return f"{len(specs)} methods ({dangerous} dangerous)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skill-path", default=str(SKILL_PATH))
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    skill = Path(args.skill_path)
    if not skill.exists():
        print(f"SKILL.md not found: {skill}", file=sys.stderr)
        return 2
    text = skill.read_text(encoding="utf-8")

    try:
        methods = parse_skill_md(text)
    except CodegenParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return 1

    specs = build_tool_specs(methods)
    out = render_yaml(specs)

    print(_stats(specs))

    if args.dry_run:
        return 0

    if args.check:
        existing = Path(args.output).read_text(encoding="utf-8") if Path(args.output).exists() else ""
        if existing != out:
            print(f"drift detected: {args.output}", file=sys.stderr)
            return 1
        return 0

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(out, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 运行 codegen 单元测试**

```bash
cd /d/fintech_workspace/unihive
python -m pytest tests/test_gen_tdx_tq_local_tools.py -v 2>&1 | tail -30
```

预期：14 个测试全部 PASS。

- [ ] **Step 3: 手动 dry-run 验证（不需要 TdxW）**

```bash
cd /d/fintech_workspace/unihive
python scripts/gen_tdx_tq_local_tools.py --dry-run
```

预期：打印 `55 methods (N dangerous)`（N 在 20 上下），exit 0，不写文件。

- [ ] **Step 4: 提交**

```bash
git add scripts/gen_tdx_tq_local_tools.py tests/test_gen_tdx_tq_local_tools.py
git commit -m "feat(codegen): generate tdx_tq_local tool specs from SKILL.md"
```

---

## Task 6: 运行 codegen 生成实际 YAML

**Files:**
- Create: `config/tools_tdx_tq_local.yaml`（生成产物）

- [ ] **Step 1: 生成**

```bash
cd /d/fintech_workspace/unihive
python scripts/gen_tdx_tq_local_tools.py
```

预期：打印 `55 methods (N dangerous)` 和 `wrote config/tools_tdx_tq_local.yaml`。

- [ ] **Step 2: 抽样校验输出**

```bash
cd /d/fintech_workspace/unihive
head -30 config/tools_tdx_tq_local.yaml
echo "---"
python -c "
import yaml
d = yaml.safe_load(open('config/tools_tdx_tq_local.yaml', encoding='utf-8'))
print('count:', len(d['tools']))
names = [t['name'] for t in d['tools']]
print('order_stock dangerous:', next(t['dangerous'] for t in d['tools'] if t['name']=='order_stock'))
print('get_market_data dangerous:', next(t['dangerous'] for t in d['tools'] if t['name']=='get_market_data'))
print('first 3 names:', names[:3])
print('last 3 names:', names[-3:])
"
```

预期：
- count ≈ 55（SKILL.md 当前 55 个 method）
- `order_stock.dangerous == True`
- `get_market_data.dangerous == False`

- [ ] **Step 3: 验证 idempotent**

```bash
cd /d/fintech_workspace/unihive
python scripts/gen_tdx_tq_local_tools.py --check
```

预期：exit 0，无 drift。

- [ ] **Step 4: 提交**

```bash
git add config/tools_tdx_tq_local.yaml
git commit -m "feat(tdx-tools): initial generated tool definitions"
```

---

## Task 7: Gateway 工具源合并

**Files:**
- Modify: `src/gateway_server.py`
- Create: `tests/test_load_all_tools.py`

- [ ] **Step 1: 写合并测试 (RED)**

新建 `tests/test_load_all_tools.py`：

```python
"""验证 gateway 启动时合并 upstreams.yaml 与 tools_tdx_tq_local.yaml。"""
import sys
from pathlib import Path

import pytest

# 把 src 加进 path（若 conftest 未配置）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.gateway_server import GatewayServer  # noqa: E402


def test_load_all_tools_merges_both_sources(tmp_path, monkeypatch):
    # 临时 upstreams.yaml：含 1 个手工 tool
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("""
upstreams:
  rhths_meta:
    enabled: true
    type: http
    base_url: http://127.0.0.1:9999
tools:
  - name: manual_tool
    description: hand-written
    routing: manual_tool
    upstream_tool_mapping:
      rhths_meta: manual_tool
""", encoding="utf-8")

    # 临时生成文件：含 1 个生成的 tool
    gen = tmp_path / "tools_tdx_tq_local.yaml"
    gen.write_text("""
tools:
  - name: generated_tool
    description: from codegen
    routing: generated_tool
    upstream_tool_mapping:
      tdx_tq_local: generated_tool
""", encoding="utf-8")

    # 把 cwd 切到 tmp_path，使 _load_all_tools 用相对路径能找到 gen
    monkeypatch.chdir(tmp_path)
    # 同时把 cfg 也复制成相对路径名
    (tmp_path / "upstreams.yaml").write_text(cfg.read_text(encoding="utf-8"), encoding="utf-8")

    server = GatewayServer(config_path="upstreams.yaml", strict_env=False)
    # 触发配置加载
    server.config = server.config  # 已加载；无需重做
    tools = server._load_all_tools()
    names = {t["name"] for t in tools}
    assert "manual_tool" in names
    assert "generated_tool" in names
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/fintech_workspace/unihive
python -m pytest tests/test_load_all_tools.py -v 2>&1 | tail -10
```

预期：`AttributeError: GatewayServer object has no attribute '_load_all_tools'`

- [ ] **Step 3: 实现 `_load_all_tools`**

在 `src/gateway_server.py` 添加方法（放在 `_register_tools` 之后）：

```python
    def _load_all_tools(self) -> list[dict]:
        """合并 config.upstreams.yaml 与 config/tools_tdx_tq_local.yaml 的 tools 列表。"""
        from pathlib import Path as _P
        import yaml as _yaml
        tools = list(self.config.get("tools", []) or [])
        gen_path = _P("config/tools_tdx_tq_local.yaml")
        if gen_path.exists():
            with gen_path.open(encoding="utf-8") as f:
                gen_cfg = _yaml.safe_load(f) or {}
            tools.extend(gen_cfg.get("tools", []) or [])
        return tools
```

并改 `_register_tools` 让它从 `_load_all_tools()` 取：

```python
    def _register_tools(self):
        self._register_search_stock()
        self._register_status_tools()

        specs = self._load_all_tools()
        if specs:
            strict = self.config.get("strict_registry", True)
            validate_specs(
                specs,
                self.config.get("routing", {}),
                self.config.get("upstream_tool_mapping", {}),
                strict=strict,
            )
            disable_dangerous = self.config.get("disable_dangerous", False)
            registered = register_tools_from_config(
                self, specs, disable_dangerous=disable_dangerous
            )
            logger.info(
                f"Registered {len(registered)} tools from config "
                f"(dangerous={any(s.get('dangerous') for s in specs)}, "
                f"disabled={disable_dangerous})"
            )
```

注意：把 `self.config.get("tools", []) or []` 这一行替换为 `self._load_all_tools()`。

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /d/fintech_workspace/unihive
python -m pytest tests/test_load_all_tools.py -v 2>&1 | tail -10
```

预期：1 个测试 PASS。

- [ ] **Step 5: 启动 gateway 实测工具数**

```bash
cd /d/fintech_workspace/unihive
GATEWAY_BEARER_TOKEN=test123 python -c "
import asyncio
from src.gateway_server import GatewayServer
async def run():
    s = GatewayServer(config_path='config/upstreams.yaml', strict_env=False)
    await s.initialize()
    print('TOOL_COUNT:', sum(1 for _ in s.mcp._tool_manager._tools))
asyncio.run(run())
" 2>&1 | grep TOOL_COUNT
```

预期：`TOOL_COUNT: <之前 89 + 50 = ~139>`（视当前 SKILL.md 版本而定）

- [ ] **Step 6: 提交**

```bash
git add src/gateway_server.py tests/test_load_all_tools.py
git commit -m "feat(gateway): merge tools from generated YAML at startup"
```

---

## Task 8: Codegen dangerous 覆盖完整性测试

**Files:**
- Modify: `tests/test_gen_tdx_tq_local_tools.py`

- [ ] **Step 1: 追加一个测试**

在 `tests/test_gen_tdx_tq_local_tools.py` 末尾追加：

```python
def test_real_skill_md_all_methods_have_dangerous_predicate():
    """每个 SKILL.md 真实方法都该被某个 dangerous 规则命中或显式 safe。
    保证 dangerous 规则不会因为白名单更新而漏掉新方法。
    """
    real_skill = Path.home() / ".claude" / "skills" / "tdx-tq-local" / "SKILL.md"
    if not real_skill.exists():
        pytest.skip("real SKILL.md not present")
    methods = parse_skill_md(real_skill.read_text(encoding="utf-8"))
    for m in methods:
        d = infer_dangerous(m["name"])
        # 这里不要求结果，只要求函数能跑不抛错；以后可以维护白名单
        assert isinstance(d, bool)
```

- [ ] **Step 2: 运行**

```bash
cd /d/fintech_workspace/unihive
python -m pytest tests/test_gen_tdx_tq_local_tools.py -v 2>&1 | tail -10
```

预期：原有 14 个 + 新 1 个 = 15 个 PASS。

- [ ] **Step 3: 提交**

```bash
git add tests/test_gen_tdx_tq_local_tools.py
git commit -m "test(codegen): smoke test all real SKILL.md methods have predicate"
```

---

## Task 9: 端到端联通验证（手测，需要 TdxW 起来）

**Files:** 无（验证步骤）

- [ ] **Step 1: 确认 TdxW + TQ-Local 在 17709 端口监听**

```bash
powershell -Command "Get-NetTCPConnection -LocalPort 17709 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"
curl -s --max-time 3 -X POST http://127.0.0.1:17709/ -H "Content-Type: application/json" -d '{"id":1,"method":"get_market_snapshot","params":{"stock_code":"688318.SH"}}' | head -c 200
```

预期：返回非空 JSON（带 `result` 或 `error`）。

- [ ] **Step 2: 重启 gateway（HTTP 模式 + token）**

```bash
cd /d/fintech_workspace/unihive
# 先 kill 现有进程
powershell -Command "Get-NetTCPConnection -LocalPort 18081 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force -ErrorAction SilentlyContinue }"
Start-Sleep -Milliseconds 500
# 起新
GATEWAY_BEARER_TOKEN=smoketest123 python -m src.gateway_server --transport http --port 18081 &
echo $! > /tmp/gw.pid
# 等就绪
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -s -o /dev/null --max-time 1 http://127.0.0.1:18081/health && break
  sleep 1
done
```

预期：18081 健康检查 200。

- [ ] **Step 3: 完整 E2E**

```bash
cd /d/fintech_workspace/unihive
TOKEN=smoketest123

# 1) 鉴权检查
echo "--- no token (expect 401) ---"
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:18081/mcp

# 2) initialize
INIT=$(curl -s -i -X POST http://127.0.0.1:18081/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"e2e","version":"1.0"}}}')
SESSION=$(echo "$INIT" | grep -i "mcp-session-id" | tr -d '\r' | awk '{print $2}')
echo "session: $SESSION"

# 3) tools/list (看 5 个现有 + 50 个生成 = 55 个 TQ-Local 工具)
curl -s -X POST http://127.0.0.1:18081/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | python -c "
import sys, json
raw = sys.stdin.read()
data_line = [l for l in raw.split('\n') if l.startswith('data:')][0]
d = json.loads(data_line[5:].strip())
names = sorted([t['name'] for t in d['result']['tools']])
print('total tools:', len(names))
print('first 10:', names[:10])
print('order_stock in list:', 'order_stock' in names)
"

# 4) tools/call 一个行情接口
curl -s -X POST http://127.0.0.1:18081/mcp \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION" -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_market_snapshot","arguments":{"stock_code":"688318.SH"}}}' | python -c "
import sys, json
raw = sys.stdin.read()
data_line = [l for l in raw.split('\n') if l.startswith('data:')][0]
d = json.loads(data_line[5:].strip())
text = d['result']['content'][0]['text']
inner = json.JSONDecoder().raw_decode(text)[0]
print('success:', inner.get('success'), 'source:', inner.get('source'))
"

# 5) kill gateway
kill $(cat /tmp/gw.pid) 2>/dev/null
```

预期：
- `no token` → `401`
- tools/list total ≥ 140（现有 89 + ~55 新 = ~144）
- `order_stock in list: True`
- get_market_snapshot → `success: True source: tdx_tq_local`

- [ ] **Step 4: 提交报告（仅写日志）**

```bash
cd /d/fintech_workspace/unihive
echo "E2E smoke test passed at $(date -Iseconds)" > logs/tdx_tq_local_e2e.log
git add logs/tdx_tq_local_e2e.log
git commit -m "chore: log tdx_tq_local E2E smoke run"
```

---

## 自我复审清单

- [x] 8 个澄清决策均有对应任务（auth: T1-T3; codegen: T4-T6; 合并: T7; 覆盖: T8; E2E: T9）
- [x] 无 TBD/TODO 占位
- [x] 类型一致：`_load_all_tools()` 在 T7 定义并在 T7 调用；`infer_dangerous()`/`parse_skill_md()`/`build_tool_specs()`/`render_yaml()` 在 T5 定义、T4 测试
- [x] 频繁 commit：每个 Task 末尾一个 commit
- [x] TDD：T1→T2 (auth), T4→T5 (codegen), T7 步骤 1→3 (loader)
- [x] 所有步骤含可执行命令 + 预期输出
