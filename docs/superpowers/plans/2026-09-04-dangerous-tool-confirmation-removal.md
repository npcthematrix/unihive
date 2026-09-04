# 危险工具确认门移除 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the per-call `confirm=true` gate (and the `disable_dangerous` operator kill switch) from the 18 dangerous tools. Tool signature becomes clean, runtime path is identical to safe tools except for a WARNING audit log.

**Architecture:** Delete-only refactor in Python — `build_tool_function` keeps the same shape, drops the confirm branch and the `_CONFIRM_*` constants. Test layer inverts from "8 confirmation tests + 2 disable_dangerous tests" to "2 negative tests asserting no gate". YAML side fixes 14 placeholder descriptions that should have been `⚠️ DANGER` all along.

**Tech Stack:** Python 3.10+, pytest with `asyncio_mode="auto"`, FastMCP tool registration via decorator, JSON-line logging to `logs/gateway.log`.

**Spec:** [`docs/superpowers/specs/2026-09-04-dangerous-tool-confirmation-removal-design.md`](../specs/2026-09-04-dangerous-tool-confirmation-removal-design.md)

---

## File Map

**Modify:**
- `src/registry.py` — drop confirm gate, drop `_CONFIRM_*`, drop `disable_dangerous` param
- `src/gateway_server.py:181-188` — drop `disable_dangerous` config reading + log field
- `config/tools_tdx_tq_local.yaml` — backfill `⚠️ DANGER` description for 14 tools whose description is currently just the tool name
- `tests/test_registry.py` — drop 10 tests, simplify 1, add 2
- `docs/superpowers/specs/2026-09-04-unihive-mcp-review.md:42-45` — rewrite the P3 confirm section

**No new files.** No new endpoints. No new config.

---

### Task 1: Update `tests/test_registry.py` — drop confirm tests, add 2 negative tests

**Files:**
- Modify: `tests/test_registry.py:118-131` (simplify `test_dangerous_logs_warning`)
- Delete: `tests/test_registry.py:134-199` (entire `TestDangerousConfirmation` class — 8 methods)
- Delete: `tests/test_registry.py:213-222` (`test_skips_dangerous_when_disabled`)
- Delete: `tests/test_registry.py:224-231` (`test_includes_dangerous_by_default`)
- Add: 2 new tests after `test_dangerous_logs_warning`

**Why first (TDD):** Define the new contract as failing tests, then make them pass in Task 2. Drop the obsolete tests so the suite reflects the new design.

- [ ] **Step 1.1: Simplify `test_dangerous_logs_warning` to drop `confirm=True` and assert no "rejected" string**

Replace `tests/test_registry.py:118-131` (the entire `test_dangerous_logs_warning` method) with:

```python
    def test_dangerous_logs_warning(self, caplog):
        server = _FakeServer()
        spec = {
            "name": "dangerous_call",
            "description": "d",
            "routing": "tdx_call",
            "dangerous": True,
            "params": [{"name": "path", "type": "str", "required": True}],
        }
        fn = build_tool_function(spec, server)
        import asyncio
        with caplog.at_level("WARNING"):
            asyncio.run(fn(path="/api/test"))
        assert any("DANGEROUS call" in r.message for r in caplog.records)
        assert not any("rejected" in r.message for r in caplog.records)
        assert not any("unconfirmed" in r.message for r in caplog.records)
```

- [ ] **Step 1.2: Delete the entire `TestDangerousConfirmation` class (lines 134-199)**

Remove `tests/test_registry.py:134-199` (the whole class block including the `class TestDangerousConfirmation:` line and the `class TestRegisterToolsFromConfig:` that follows it after a blank line). Keep one blank line between `test_dangerous_logs_warning` and `class TestRegisterToolsFromConfig`.

- [ ] **Step 1.3: Delete `test_skips_dangerous_when_disabled` (lines 213-222) and `test_includes_dangerous_by_default` (lines 224-231)**

In `TestRegisterToolsFromConfig`, remove both methods. The class should retain only `test_registers_all`.

After the deletion, the class should look like:

```python
class TestRegisterToolsFromConfig:
    def test_registers_all(self):
        server = _FakeServer()
        specs = [
            {"name": "a", "description": "A", "routing": "a", "params": []},
            {"name": "b", "description": "B", "routing": "b", "params": []},
        ]
        names = register_tools_from_config(server, specs)
        assert names == ["a", "b"]
        assert set(server.mcp.tools.keys()) == {"a", "b"}
```

- [ ] **Step 1.4: Add two new tests asserting the post-removal contract**

Insert these two methods **after** `test_dangerous_logs_warning` (in `TestBuildToolFunction`), **before** `class TestRegisterToolsFromConfig`:

```python
    def test_dangerous_tool_signature_has_no_confirm(self):
        """Post-removal: dangerous tools expose only their upstream params, no confirm gate."""
        server = _FakeServer()
        spec = {
            "name": "delete_sector",
            "description": "⚠️ DANGER 删除板块 (高风险写操作，操作不可逆)",
            "routing": "delete_sector",
            "dangerous": True,
            "params": [{"name": "sector", "type": "str", "required": True}],
        }
        fn = build_tool_function(spec, server)
        sig = inspect.signature(fn)
        assert "confirm" not in sig.parameters
        assert "sector" in sig.parameters

    def test_dangerous_tool_calls_execute_cached_immediately(self):
        """Post-removal: dangerous tool dispatches to upstream without any confirm dance."""
        server = _FakeServer()
        spec = {
            "name": "delete_sector",
            "description": "⚠️ DANGER",
            "routing": "delete_sector",
            "dangerous": True,
            "params": [{"name": "sector", "type": "str", "required": True}],
        }
        fn = build_tool_function(spec, server)
        import asyncio
        result = asyncio.run(fn(sector="自选"))
        # Reaches _execute_cached (server.calls non-empty) and returns the upstream echo
        assert server.calls == [("delete_sector", {"sector": "自选"}, "delete_sector", None)]
        assert result["success"] is True
        # No refusal envelope
        assert "requires_confirmation" not in result
```

- [ ] **Step 1.5: Run the test suite — expect exactly 2 failures (the new tests) + several errors from the still-present `_CONFIRM_*` references**

Run: `python -m pytest tests/test_registry.py -v 2>&1 | tail -25`

Expected: the two new tests fail (because `build_tool_function` still has the confirm gate). The other 116 existing tests in test_registry.py should still pass. The full project suite (118 currently) will show 2 fails + 116 passed = 118 (since we deleted 10 and the file is mid-state).

If the test count or failures don't match, **stop** and verify the edits in Steps 1.1-1.4 are correct. Do not proceed.

- [ ] **Step 1.6: Commit the test changes alone (the implementation comes in Task 2)**

```bash
git add tests/test_registry.py
git commit -m "$(cat <<'EOF'
test(registry): rewrite dangerous-tool tests for confirm-free contract

Drop 10 tests that asserted the confirm-gate behavior (8 in
TestDangerousConfirmation, 2 disable_dangerous toggle tests). Add 2
negative tests that pin the new contract: dangerous tool signature
has no 'confirm' param, and calls dispatch to upstream immediately
without any refusal envelope.

The new tests fail at this commit because registry.py still has the
gate; Task 2 will make them pass.
EOF
)"
```

---

### Task 2: Remove the confirm gate from `src/registry.py`

**Files:**
- Modify: `src/registry.py:55-80` — delete `_CONFIRM_PARAM`, `_CONFIRM_SPEC`, `_CONFIRM_DOC`, `_confirmation_required`
- Modify: `src/registry.py:83-127` — rewrite `build_tool_function` to drop the gate
- Modify: `src/registry.py:130-145` — rewrite `register_tools_from_config` to drop `disable_dangerous`

- [ ] **Step 2.1: Delete the `_CONFIRM_*` block and `_confirmation_required` (lines 55-80)**

Remove `src/registry.py:55-80` (the entire block from `_CONFIRM_PARAM = "confirm"` through the `_confirmation_required` function definition). The file should now end `_normalize_param` with `return value` and have one blank line before `def build_tool_function`.

- [ ] **Step 2.2: Replace `build_tool_function` with the simplified version (lines 83-127)**

Replace `src/registry.py:83-127` (the entire current `build_tool_function` function) with:

```python
def build_tool_function(spec: dict, server: Any) -> Callable:
    """从 spec 构造带正确 signature/annotations/doc 的 async 函数。

    server 必须有 _execute_cached(name, params, route_key, ttl_key) 方法。
    dangerous=True 的 spec 会在每次调用时写一条 WARNING 审计日志，但
    不再做任何运行时拦截。
    """
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
```

- [ ] **Step 2.3: Replace `register_tools_from_config` with the no-`disable_dangerous` version (lines 130-145)**

Replace `src/registry.py:130-145` (the entire current `register_tools_from_config` function) with:

```python
def register_tools_from_config(
    server: Any,
    specs: list[dict],
) -> list[str]:
    """注册所有 spec 为 FastMCP tool。返回注册的 name 列表。"""
    registered: list[str] = []
    for spec in specs:
        fn = build_tool_function(spec, server)
        server.mcp.tool()(fn)
        registered.append(spec["name"])
    return registered
```

- [ ] **Step 2.4: Run the new tests — they should now pass**

Run: `python -m pytest tests/test_registry.py -v 2>&1 | tail -10`

Expected: All tests in `test_registry.py` pass. Total: `24 passed` (was 22; -10 deleted + -1 in TestRegisterToolsFromConfig + 2 added = 13 net, plus the 11 untouched tests in TestBuildSignature + TestValidateSpecs). If the count is off, **stop** and re-verify.

- [ ] **Step 2.5: Run the full suite — expect 1 new failure (gateway_server.py still calls `disable_dangerous=`)**

Run: `python -m pytest -q 2>&1 | tail -10`

Expected: `1 failed, 117 passed`. The failure is in `src/gateway_server.py` which still calls `register_tools_from_config(self, specs, disable_dangerous=disable_dangerous)`. Task 3 fixes that.

- [ ] **Step 2.6: Commit**

```bash
git add src/registry.py
git commit -m "$(cat <<'EOF'
refactor(registry): drop dangerous-tool confirmation gate

c63d05f added a per-call confirm=true gate that intercepted all 18
dangerous tools and returned a refusal envelope unless the caller
opted in. Per the 2026-09-04 design review, this gate is being
withdrawn — the LLM/operator is now responsible for whether to
invoke a dangerous tool, and the gateway's only role is the
WARNING audit log already present.

Changes:
- Drop _CONFIRM_PARAM, _CONFIRM_SPEC, _CONFIRM_DOC constants
- Drop _confirmation_required() function
- build_tool_function: _runtime no longer checks confirm; just logs
  WARNING when dangerous and dispatches
- _runtime.__doc__ is the YAML description as-is; no runtime
  suffix (YAML owns the ⚠️ DANGER prefix)
- register_tools_from_config: drop the disable_dangerous param
  (the operator kill switch is also being withdrawn)

Signature of dangerous tools loses the `confirm: bool = False`
parameter — 18 tools' MCP schema changes (breaking, by design).
Safe tools (124) are completely unaffected.
EOF
)"
```

---

### Task 3: Remove `disable_dangerous` from `src/gateway_server.py`

**Files:**
- Modify: `src/gateway_server.py:181-188` — drop the config read, the kwarg, and the log field

- [ ] **Step 3.1: Read the surrounding context to make a clean edit**

Run: `sed -n '170,195p' src/gateway_server.py`

Verify the block is exactly:

```python
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

- [ ] **Step 3.2: Replace the block with the simplified version**

Replace `src/gateway_server.py:175-189` (the validate_specs call through the closing `)` of the logger.info call) with:

```python
            validate_specs(
                specs,
                self.config.get("routing", {}),
                self.config.get("upstream_tool_mapping", {}),
                strict=strict,
            )
            registered = register_tools_from_config(self, specs)
            logger.info(
                f"Registered {len(registered)} tools from config "
                f"(dangerous={any(s.get('dangerous') for s in specs)})"
            )
```

- [ ] **Step 3.3: Run the full suite — all green**

Run: `python -m pytest -q 2>&1 | tail -5`

Expected: `118 passed in <X>s`. If anything fails, **stop** and re-verify the edit.

- [ ] **Step 3.4: Commit**

```bash
git add src/gateway_server.py
git commit -m "$(cat <<'EOF'
refactor(gateway): drop disable_dangerous config reading

The operator kill switch (config `disable_dangerous: true` to skip
all dangerous tools at registration time) is being withdrawn
alongside the per-call confirm gate. Both are special-case
behaviors around the same concept (extra friction before a
dangerous tool runs); the user asked to simplify both at once.

Registration now just calls register_tools_from_config(self, specs).
The info log no longer reports a `disabled=` field.
EOF
)"
```

---

### Task 4: Backfill `⚠️ DANGER` descriptions for 14 placeholder tools in `config/tools_tdx_tq_local.yaml`

**Files:**
- Modify: `config/tools_tdx_tq_local.yaml` — 14 description replacements

**Why:** Per the spec, 4 dangerous tools already have proper `⚠️ DANGER` descriptions (`tdx_call` in upstreams.yaml, `send_user_block` / `create_sector` / `delete_sector` in this file). The other 14 have a description that is literally the tool name — the operator/LLM sees no warning that the tool is destructive. This task fixes that.

- [ ] **Step 4.1: Read the file structure to understand the YAML format used for these tools**

Run: `grep -nB0 -A0 "^- name: refresh_cache" config/tools_tdx_tq_local.yaml | head -2`

Expected: a multi-line tool block beginning with `- name: refresh_cache`. The actual block will look something like:

```yaml
  - name: refresh_cache
    description: refresh_cache
    routing: refresh_cache
    params:
      - name: ...
    dangerous: true
```

(Note: the actual format may differ — list-form, block-form, or mixed. Use Read on the file around the `refresh_cache` entry to see the exact format before editing.)

- [ ] **Step 4.2: Read the exact current text for each of the 14 tools**

Use the Read tool on `config/tools_tdx_tq_local.yaml` and locate each of the 14 tools. The names are: `refresh_cache`, `refresh_kline`, `download_file`, `send_message`, `send_file`, `send_warn`, `send_bt_data`, `exec_to_tdx`, `rename_sector`, `clear_sector`, `formula_set_data`, `formula_set_data_info`, `order_stock`, `cancel_order_stock`.

For each, capture the exact `description: <name>` line(s) so the Edit's `old_string` is unique.

- [ ] **Step 4.3: For each of the 14 tools, replace the placeholder description with a proper `⚠️ DANGER` description**

For each tool, run an Edit like:

```python
# Example for refresh_cache:
old_string: "  - name: refresh_cache\n    description: refresh_cache\n    routing: refresh_cache"
new_string: "  - name: refresh_cache\n    description: \"⚠️ DANGER: TQ 刷新缓存 (清空并重建本地数据缓存), 不可逆.\"\n    routing: refresh_cache"
```

The exact phrasing for each tool is the implementer's call, but the **format must be**:

- Start with `⚠️ DANGER:` (the existing 4 tools use this colon; match the style)
- One short Chinese sentence in parentheses describing the destructive effect
- End with `不可逆.` if the operation is irreversible, or `.` if reversible

Recommended descriptions (the implementer may refine these based on what the tools actually do, by reading TQ-Local docs or by asking the user):

| Tool | Proposed description |
|------|---------------------|
| `refresh_cache` | `⚠️ DANGER: TQ 刷新本地数据缓存, 清空并重建. 不可逆.` |
| `refresh_kline` | `⚠️ DANGER: TQ 刷新本地 K 线数据. 不可逆.` |
| `download_file` | `⚠️ DANGER: TQ 从服务器下载文件到本地, 覆盖已有同名文件.` |
| `send_message` | `⚠️ DANGER: TQ 发送消息到 TQ 终端, 用户会立即看到.` |
| `send_file` | `⚠️ DANGER: TQ 发送文件到 TQ 终端.` |
| `send_warn` | `⚠️ DANGER: TQ 弹出警告消息到 TQ 终端, 不可撤销.` |
| `send_bt_data` | `⚠️ DANGER: TQ 发送回测数据到 TQ 终端.` |
| `exec_to_tdx` | `⚠️ DANGER: TQ 调用 TDX 函数 (任意代码执行), 不可逆.` |
| `rename_sector` | `⚠️ DANGER: TQ 重命名自定义板块, 修改本地数据. 不可逆.` |
| `clear_sector` | `⚠️ DANGER: TQ 清空自定义板块内全部股票, 不可逆.` |
| `formula_set_data` | `⚠️ DANGER: TQ 写入公式数据, 修改本地公式存储. 不可逆.` |
| `formula_set_data_info` | `⚠️ DANGER: TQ 写入公式元数据, 修改本地存储. 不可逆.` |
| `order_stock` | `⚠️ DANGER: TQ 下单买入/卖出股票, 真实资金变动, 不可撤销.` |
| `cancel_order_stock` | `⚠️ DANGER: TQ 撤销未成交委托, 修改订单状态.` |

If the implementer is unsure of the exact behavior of any of these 14, they should **stop and ask the user** rather than guess. The 4 already-documented tools (`send_user_block`, `create_sector`, `delete_sector`, `tdx_call`) can serve as style references.

- [ ] **Step 4.4: Verify the YAML is still parseable and the count is right**

Run: `python -c "import yaml; cfg = yaml.safe_load(open('config/tools_tdx_tq_local.yaml', encoding='utf-8')); dangerous = [t for t in cfg['tools'] if t.get('dangerous')]; bad = [t['name'] for t in dangerous if not t.get('description','').startswith('⚠️')]; print(f'{len(dangerous)} dangerous, {len(bad)} missing prefix: {bad}')"`

Expected output: `17 dangerous, 0 missing prefix: []`

(The 17 in this file + 1 in upstreams.yaml = 18 total dangerous tools, matching the spec.)

- [ ] **Step 4.5: Run the full suite — still all green (no behavior change, only metadata)**

Run: `python -m pytest -q 2>&1 | tail -3`

Expected: `118 passed in <X>s`. The YAML description change should not affect any test — these descriptions flow through to `inspect.getdoc(fn)` but no test asserts on docstring content. If a test fails, **stop** and re-check the YAML format.

- [ ] **Step 4.6: Commit**

```bash
git add config/tools_tdx_tq_local.yaml
git commit -m "$(cat <<'EOF'
fix(tools): backfill ⚠️ DANGER descriptions for 14 placeholder entries

After dropping the confirm gate, the YAML description is the only
warning signal an LLM/caller sees on a dangerous tool. 4 of the
17 generated-yaml dangerous tools already had proper ⚠️ DANGER
descriptions (send_user_block / create_sector / delete_sector,
and tdx_call in upstreams.yaml). The other 14 had descriptions
that were just the tool name — leaving the operator blind to
the destructive nature.

Each placeholder now reads "⚠️ DANGER: TQ <effect>, 不可逆." (or
"可撤销" where appropriate), matching the style of the 4
already-documented tools. No behavior change; this is metadata
only.
EOF
)"
```

---

### Task 5: Update the review spec to mark the confirm gate as reverted

**Files:**
- Modify: `docs/superpowers/specs/2026-09-04-unihive-mcp-review.md:42-45`

- [ ] **Step 5.1: Replace the 4-line confirm section with the reverted note**

Replace `docs/superpowers/specs/2026-09-04-unihive-mcp-review.md:42-45` (the 4 lines under the `c63d05f` reference) with:

```markdown
- ~~`tdx_call` 等危险 tool 在 FastMCP 层加独立 confirmation hook~~ → `c63d05f feat(registry): gate dangerous tools behind an explicit confirm flag` (**已撤回，见下**)
  - **2026-09-04 撤回** — 用户决定所有入口都不再要求 confirm=true; 18 个 dangerous tool 的运行时拦截、`_CONFIRM_*` 常量、`disable_dangerous` operator kill switch 一并撤掉
  - 设计: [`2026-09-04-dangerous-tool-confirmation-removal-design.md`](./2026-09-04-dangerous-tool-confirmation-removal-design.md)
  - 当前行为: 危险工具的签名中无 `confirm` 参数; 调用直接进入上游; 仅保留 (a) 描述前缀 `⚠️ DANGER`, (b) 每次调用一条 WARNING 审计日志
  - 原 8 个新测试 (`tests/test_registry.py::TestDangerousConfirmation`) 已删除
```

- [ ] **Step 5.2: Verify the spec file is still well-formed markdown**

Run: `python -c "import re; text = open('docs/superpowers/specs/2026-09-04-unihive-mcp-review.md', encoding='utf-8').read(); print(f'{len(text)} chars, {len(text.splitlines())} lines, {text.count(chr(10))} newlines')"`

Expected: file size within a few hundred chars of the original (was ~3.3 KB; should be ~3.5 KB after the addition). No syntax errors.

- [ ] **Step 5.3: Commit**

```bash
git add docs/superpowers/specs/2026-09-04-unihive-mcp-review.md
git commit -m "$(cat <<'EOF'
docs(review): mark the dangerous-tool confirm gate as reverted

The P3 future-work section that documented c63d05f (the confirm
gate) is now annotated as reverted on 2026-09-04. Points to the
new design spec and summarizes the current behavior (no
runtime gate, just ⚠️ description + WARNING log).

T-3 itself remains "closed" — the underlying problem (LLM/caller
not aware of dangerous tools) is still addressed via the
description prefix and the audit log, which were the parts kept.
EOF
)"
```

---

### Task 6: Final verification + atomic consolidation

**Files:** None modified — this is a checkpoint.

- [ ] **Step 6.1: Run the full test suite one more time**

Run: `python -m pytest -q 2>&1 | tail -5`

Expected: `118 passed in <X>s`. If not, **stop** — something regressed.

- [ ] **Step 6.2: Verify the registry source has zero `confirm` / `disable_dangerous` / `_CONFIRM` references**

Run: `grep -nE "confirm|disable_dangerous|_CONFIRM" src/registry.py src/gateway_server.py || echo "✓ All references gone"`

Expected: `✓ All references gone`. If anything matches, **stop** and re-check.

- [ ] **Step 6.3: Verify all 18 dangerous tool descriptions start with `⚠️ DANGER`**

Run: `python -c "
import yaml
all_dangerous = []
for path in ['config/upstreams.yaml', 'config/tools_tdx_tq_local.yaml']:
    cfg = yaml.safe_load(open(path, encoding='utf-8'))
    all_dangerous.extend(t for t in cfg.get('tools', []) if t.get('dangerous'))
print(f'Total: {len(all_dangerous)}')
bad = [t['name'] for t in all_dangerous if not t.get('description','').startswith('⚠️')]
print(f'Missing prefix: {bad}')
assert not bad, f'{len(bad)} tools still lack the prefix'
print('✓ All 18 dangerous tools have ⚠️ DANGER descriptions')
"`

Expected: `Total: 18`, `Missing prefix: []`, `✓ All 18 dangerous tools have ⚠️ DANGER descriptions`.

- [ ] **Step 6.4: Show the commit graph for this work**

Run: `git log --oneline -10`

Expected: 5 new commits at the top of master (one per Task 1-5), in order:

1. `test(registry): rewrite dangerous-tool tests for confirm-free contract`
2. `refactor(registry): drop dangerous-tool confirmation gate`
3. `refactor(gateway): drop disable_dangerous config reading`
4. `fix(tools): backfill ⚠️ DANGER descriptions for 14 placeholder entries`
5. `docs(review): mark the dangerous-tool confirm gate as reverted`

- [ ] **Step 6.5: (Optional) Push and open a PR if the user has authorized it**

**DO NOT** push or open a PR without explicit user authorization. Stop here and report completion to the user.

---

## Self-Review Checklist

Before declaring done, re-verify:

- [ ] Spec §4.1 (registry.py simplification) → covered by Task 2 Steps 2.1-2.3
- [ ] Spec §4.2 (gateway_server.py disable_dangerous removal) → covered by Task 3
- [ ] Spec §4.3 (YAML 14 placeholder fixes) → covered by Task 4
- [ ] Spec §4.4 (test_registry.py rewrites) → covered by Task 1 + verified by Task 2 Step 2.4
- [ ] Spec §4.5 (review spec P3 rewrite) → covered by Task 5
- [ ] Spec §9 acceptance criteria:
  - [ ] No `_CONFIRM_*` / `_confirmation_required` / `disable_dangerous` in registry.py → verified by Task 6 Step 6.2
  - [ ] 18 dangerous tools' `inspect.signature` has no `confirm` → covered by `test_dangerous_tool_signature_has_no_confirm`
  - [ ] Dangerous tools dispatch to `_execute_cached` without confirm → covered by `test_dangerous_tool_calls_execute_cached_immediately`
  - [ ] WARNING log per dangerous call → covered by simplified `test_dangerous_logs_warning`
  - [ ] All 18 dangerous tools' descriptions start with `⚠️ DANGER` → verified by Task 6 Step 6.3
  - [ ] `pytest -q` shows `118 passed` → verified by Task 6 Step 6.1
  - [ ] Review spec P3 marked reverted → covered by Task 5

All checklist items map to a task step. No placeholders, no TBDs. Type consistency: `build_tool_function` signature is consistent across all 4 mentions (Step 1.4 uses it, Step 2.2 defines it, Step 2.5 verifies it, Step 2.6 commits it). `register_tools_from_config` similarly consistent.

## Estimated Time

- Task 1: ~10 min (test file surgery)
- Task 2: ~5 min (mostly deletion, simple replacement)
- Task 3: ~2 min (4-line edit)
- Task 4: ~20 min (14 Edits + 1 verification + description research for ambiguous tools)
- Task 5: ~3 min (single markdown block)
- Task 6: ~2 min (final verification)

Total: ~45 min. The 14-tool YAML fix in Task 4 is the long pole — descriptions may need user clarification for ambiguous tools.
