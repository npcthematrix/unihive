# Handoff: TQ-Local 全量接口 + Bearer Token 实现

**Date**: 2026-09-04
**Plan**: `docs/superpowers/plans/2026-09-04-tdx-tq-local-all-interfaces.md`
**Spec**: `docs/superpowers/specs/2026-09-04-tdx-tq-local-all-interfaces-design.md`

---

## 已完成（commit）

| Task | 内容 | Commit |
|------|------|--------|
| T1 | RED tests: `tests/test_auth_middleware.py`（9 tests） | (合并在 T2) |
| T2 | GREEN impl: `src/auth_middleware.py` | `31ab916 feat(auth): BearerTokenMiddleware with public path bypass` |

pytest: `9 passed in 0.09s`

---

## 未实现（T3-T9）

按 plan 顺序：

| Task | 内容 | Files |
|------|------|-------|
| T3 | Token 配置 + 启动校验 | `src/gateway_server.py`, `.env.example` |
| T4 | Codegen 测试 RED | `tests/test_gen_tdx_tq_local_tools.py` |
| T5 | Codegen 脚本 GREEN | `scripts/gen_tdx_tq_local_tools.py` |
| T6 | 对真实 SKILL.md 生成 | `config/tools_tdx_tq_local.yaml`（新建）|
| T7 | Gateway 工具源合并 | `src/gateway_server.py` + 新测试 |
| T8 | dangerous 覆盖完整性测试 | append `tests/test_gen_tdx_tq_local_tools.py` |
| T9 | 端到端手测（需 TdxW） | 无文件改动 |

---

## 已应用的 Plan bugfixes（必须保留）

### 1. `BearerTokenMiddleware._is_public` 改用显式分支

```python
def _is_public(self, path: str) -> bool:
    for p in self.PUBLIC_PATHS:
        if p == "/":
            if path == "/":
                return True
        elif p.endswith("/"):
            if path.startswith(p):
                return True
        else:
            if path == p:
                return True
    return False
```

**原 plan 版有 bug**：`path.startswith(p.rstrip("/"))`，对 `p="/"` 会得到空串，导致 `"".startswith("")` 永远 True → 所有路径都被放行。

### 2. tests fixture 端点缺 `request: Request`

Starlette 要求 endpoint 接收 `request` 参数，否则 `TypeError: takes 0 positional arguments but 1 was given`。所有 `async def health()/root()/api_status()` 已加 `request: Request`。

---

## 当前状态

- branch: `master`
- working tree: **clean**
- 最后 5 提交：
  - `31ab916` feat(auth): BearerTokenMiddleware with public path bypass
  - `fa79480` docs(plan): TQ-Local 全量接口 + Bearer Token 鉴权实现计划
  - `3ebf79a` docs(spec): TQ-Local 全量接口对外发布 + Bearer Token 鉴权设计
  - `0bb0a03` feat(console): config panel with subtabs, sections, search, JSON viewer
  - `04cd48b` feat(console): probe http_jsonrpc upstreams via JSON-RPC ping

- 进程：上次会话结束后 gateway/console 都已停。TdxW.exe + TQ-Local 仍在 PID 16632 监听 17709。

---

## 恢复步骤

新 session 后：

```bash
cd /d/fintech_workspace/unihive
git log --oneline -5                                      # 确认 commit 链
python -m pytest tests/test_auth_middleware.py -v         # 确认 9 tests PASS
# 启 console + gateway:
GATEWAY_BEARER_TOKEN=<新生成的 token> ./scripts/start_console.ps1
```

继续 dispatch：
- 加载 `superpowers:subagent-driven-development`
- 从 T3 开始（plan 路径见上）
- 提供完整 task text 给 subagent（不要让它读 plan 文件）
- 每次 implementer 后跑 spec compliance + code quality 双 review

---

## 关键决策回顾（避免 brainstorm 重复）

1. 受众：家庭/局域网
2. 交易接口：全暴露（无额外护栏）
3. 注册：脚本从 SKILL.md codegen
4. 鉴权：全网关 Bearer Token 中间件
5. 命名：tqcenter 原名（无前缀）
6. 现有 5 个工具：直接覆盖
7. cache：不加
8. 危险推断：方法名前缀/全名匹配（见 plan T5 `_DANGEROUS_PREFIXES` 等常量）
