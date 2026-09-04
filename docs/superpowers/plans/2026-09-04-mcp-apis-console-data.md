# MCP APIs Console Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the management console's "MCP APIs" panel show the same 147 tools the gateway actually exposes, with full param schema, TTL seconds, routing key, and fallback chain.

**Architecture:** Extract the YAML-merge logic from `gateway_server._load_all_tools()` into a new `src/tool_loader` module with pure helpers (`load_all_tools`, `get_cache_ttl`, `derive_fallback_chain`). Both gateway and console call them. Console `/api/interfaces` is enriched with three new fields per tool. `console.html` detail panel is updated to render them.

**Tech Stack:** Python 3.10+, PyYAML, FastMCP, vanilla JS in `console.html`, pytest.

---

## File Structure

- **`src/tool_loader.py`** (NEW) — pure helpers, file I/O only. No state, no logging side effects.
- **`tests/test_tool_loader.py`** (NEW) — unit tests for the three helpers.
- **`src/gateway_server.py`** (MODIFY) — `_load_all_tools` becomes a one-line delegation.
- **`src/console_server.py`** (MODIFY) — `get_interfaces` delegates and enriches.
- **`tests/test_load_all_tools.py`** (MODIFY) — update to assert it delegates to the helper.
- **`tests/test_console_interfaces.py`** (NEW) — regression + contract tests for `/api/interfaces`.
- **`console.html`** (MODIFY) — `buildDetailBody()` renders Params table, Cache, Routing sections.

---

### Task 1: Create `src/tool_loader.py` with `load_all_tools`

**Files:**
- Create: `src/tool_loader.py`
- Test: `tests/test_tool_loader.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tool_loader.py`:

```python
"""Unit tests for src.tool_loader."""
from pathlib import Path

import pytest


def test_load_all_tools_empty_config(tmp_path):
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}", encoding="utf-8")
    config = {"upstreams": {}, "tools": [], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    assert tools == []


def test_load_all_tools_manual_only(tmp_path):
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools:\n  - name: manual_tool\n    routing: manual_tool\n", encoding="utf-8")
    config = {"upstreams": {}, "tools": [{"name": "manual_tool", "routing": "manual_tool"}], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    assert [t["name"] for t in tools] == ["manual_tool"]


def test_load_all_tools_merges_generated_file(tmp_path):
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools:\n  - name: m\n", encoding="utf-8")
    gen = tmp_path / "tools_tdx_tq_local.yaml"
    gen.write_text("tools:\n  - name: g\n", encoding="utf-8")
    config = {"upstreams": {}, "tools": [{"name": "m"}], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    names = {t["name"] for t in tools}
    assert names == {"m", "g"}


def test_load_all_tools_generated_writes_mapping_to_top_level(tmp_path):
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools: []", encoding="utf-8")
    gen = tmp_path / "tools_tdx_tq_local.yaml"
    gen.write_text(
        "tools:\n  - name: g\n    routing: g_route\n    upstream_tool_mapping:\n        tdx_tq_local: g_upstream\n",
        encoding="utf-8",
    )
    config = {"upstreams": {}, "tools": [], "upstream_tool_mapping": {}}
    load_all_tools(cfg, config)
    assert config["upstream_tool_mapping"]["g_route"] == {"tdx_tq_local": "g_upstream"}


def test_load_all_tools_missing_generated_is_noop(tmp_path):
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools:\n  - name: m\n", encoding="utf-8")
    config = {"upstreams": {}, "tools": [{"name": "m"}], "upstream_tool_mapping": {}}
    # no generated file exists anywhere in tmp_path
    tools = load_all_tools(cfg, config)
    assert [t["name"] for t in tools] == ["m"]


def test_load_all_tools_duplicate_name_generated_wins(tmp_path):
    from src.tool_loader import load_all_tools
    cfg = tmp_path / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools:\n  - name: dup\n    description: manual\n", encoding="utf-8")
    gen = tmp_path / "tools_tdx_tq_local.yaml"
    gen.write_text("tools:\n  - name: dup\n    description: generated\n", encoding="utf-8")
    config = {"upstreams": {}, "tools": [{"name": "dup", "description": "manual"}], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    by_name = {t["name"]: t for t in tools}
    assert by_name["dup"]["description"] == "generated"


def test_load_all_tools_searches_subdir_config(tmp_path):
    """If config_path is `config/upstreams.yaml`, generated file at `config/tools_tdx_tq_local.yaml` is also found."""
    from src.tool_loader import load_all_tools
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    cfg = cfg_dir / "upstreams.yaml"
    cfg.write_text("upstreams: {}\ntools: []", encoding="utf-8")
    gen = cfg_dir / "tools_tdx_tq_local.yaml"
    gen.write_text("tools:\n  - name: g\n", encoding="utf-8")
    config = {"upstreams": {}, "tools": [], "upstream_tool_mapping": {}}
    tools = load_all_tools(cfg, config)
    assert [t["name"] for t in tools] == ["g"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.tool_loader'`

- [ ] **Step 3: Write minimal implementation**

Create `src/tool_loader.py`:

```python
"""Shared YAML → tool-spec loader used by both gateway and console."""
from __future__ import annotations

from pathlib import Path

import yaml

_GENERATED_FILENAME = "tools_tdx_tq_local.yaml"


def load_all_tools(config_path: Path, config: dict) -> list[dict]:
    """Merge ``config[upstreams.yaml].tools`` with the generated tools file
    (``tools_tdx_tq_local.yaml`` looked up next to ``config_path`` or in
    ``./config/``). For every generated spec, copy its inline
    ``upstream_tool_mapping`` into ``config[upstream_tool_mapping]`` so
    downstream consumers can look up the source by routing key.

    Mutates ``config`` in place. Returns the merged tool-spec list.
    """
    tools: list[dict] = list(config.get("tools", []) or [])
    top_mapping = config.setdefault("upstream_tool_mapping", {})
    base_dir = config_path.parent.resolve()
    for candidate in (
        base_dir / _GENERATED_FILENAME,
        base_dir / "config" / _GENERATED_FILENAME,
        Path("config") / _GENERATED_FILENAME,
    ):
        if candidate.exists():
            with candidate.open(encoding="utf-8") as f:
                gen_cfg = yaml.safe_load(f) or {}
            gen_tools = list(gen_cfg.get("tools", []) or [])
            tools.extend(gen_tools)
            for spec in gen_tools:
                rk = spec.get("routing")
                if rk and rk not in top_mapping:
                    top_mapping[rk] = dict(spec.get("upstream_tool_mapping") or {})
            break
    return tools
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_loader.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/tool_loader.py tests/test_tool_loader.py
git commit -m "feat(tool_loader): extract YAML-merge helper from gateway"
```

---

### Task 2: Add `get_cache_ttl` to `tool_loader`

**Files:**
- Modify: `src/tool_loader.py`
- Modify: `tests/test_tool_loader.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tool_loader.py`:

```python
def test_get_cache_ttl_known_key():
    from src.tool_loader import get_cache_ttl
    config = {"cache": {"ttl": {"realtime_quote": 10}}}
    assert get_cache_ttl(config, "realtime_quote") == 10


def test_get_cache_ttl_unknown_key_returns_none():
    from src.tool_loader import get_cache_ttl
    config = {"cache": {"ttl": {"realtime_quote": 10}}}
    assert get_cache_ttl(config, "missing_key") is None


def test_get_cache_ttl_missing_cache_section_returns_none():
    from src.tool_loader import get_cache_ttl
    assert get_cache_ttl({}, "anything") is None


def test_get_cache_ttl_empty_key_returns_none():
    from src.tool_loader import get_cache_ttl
    config = {"cache": {"ttl": {"x": 5}}}
    assert get_cache_ttl(config, None) is None
    assert get_cache_ttl(config, "") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_loader.py -v -k "ttl"`
Expected: 4 failed with `ImportError: cannot import name 'get_cache_ttl'`

- [ ] **Step 3: Implement**

Append to `src/tool_loader.py`:

```python
def get_cache_ttl(config: dict, ttl_key: str | None) -> int | None:
    """Resolve ``cache.ttl[ttl_key]`` → seconds. Returns ``None`` when
    the key is empty, missing from config, or the cache section itself
    is not configured.
    """
    if not ttl_key:
        return None
    ttl = (config.get("cache") or {}).get("ttl") or {}
    return ttl.get(ttl_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_loader.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/tool_loader.py tests/test_tool_loader.py
git commit -m "feat(tool_loader): add get_cache_ttl helper"
```

---

### Task 3: Add `derive_fallback_chain` to `tool_loader`

**Files:**
- Modify: `src/tool_loader.py`
- Modify: `tests/test_tool_loader.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tool_loader.py`:

```python
def test_derive_fallback_chain_known_key_preserves_order():
    from src.tool_loader import derive_fallback_chain
    routing = {"get_quote": ["tdx_local", "rhths_ashare"]}
    assert derive_fallback_chain(routing, "get_quote") == ["tdx_local", "rhths_ashare"]


def test_derive_fallback_chain_unknown_key_returns_empty():
    from src.tool_loader import derive_fallback_chain
    assert derive_fallback_chain({"get_quote": ["tdx_local"]}, "missing") == []


def test_derive_fallback_chain_empty_routing_returns_empty():
    from src.tool_loader import derive_fallback_chain
    assert derive_fallback_chain({}, "get_quote") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tool_loader.py -v -k "fallback"`
Expected: 3 failed with `ImportError`

- [ ] **Step 3: Implement**

Append to `src/tool_loader.py`:

```python
def derive_fallback_chain(routing_config: dict, routing_key: str) -> list[str]:
    """Return the ordered upstream list from ``routing_config[routing_key]``.
    Returns ``[]`` when the key is absent or ``routing_config`` is empty.
    Order is preserved — this is the live fallback chain.
    """
    if not routing_config:
        return []
    chain = routing_config.get(routing_key)
    if not chain:
        return []
    return list(chain)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tool_loader.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/tool_loader.py tests/test_tool_loader.py
git commit -m "feat(tool_loader): add derive_fallback_chain helper"
```

---

### Task 4: Update `gateway_server._load_all_tools` to delegate

**Files:**
- Modify: `src/gateway_server.py:168-195`

- [ ] **Step 1: Run existing gateway test as baseline**

Run: `pytest tests/test_load_all_tools.py -v`
Expected: 1 passed (existing test still passes against current implementation)

- [ ] **Step 2: Replace `_load_all_tools` body with delegation**

In `src/gateway_server.py`, replace the entire `_load_all_tools` method (lines 168-195) with:

```python
    def _load_all_tools(self) -> list[dict]:
        """合并 config.upstreams.yaml 与 config/tools_tdx_tq_local.yaml 的 tools 列表。

        生成 spec 自带 upstream_tool_mapping；为了 Router 能在运行时按 gateway_tool
        查表，把每个生成 spec 的映射也写回顶层 upstream_tool_mapping。
        """
        from .tool_loader import load_all_tools
        return load_all_tools(self.config_path, self.config)
```

- [ ] **Step 3: Run existing test to verify behavior unchanged**

Run: `pytest tests/test_load_all_tools.py -v`
Expected: 1 passed

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v`
Expected: all previously-passing tests still pass (no regression)

- [ ] **Step 5: Commit**

```bash
git add src/gateway_server.py
git commit -m "refactor(gateway): delegate _load_all_tools to tool_loader"
```

---

### Task 5: Make `console_server.get_interfaces` delegate to loader

**Files:**
- Modify: `src/console_server.py:220-236`
- Create: `tests/test_console_interfaces.py`

- [ ] **Step 1: Write the failing regression test**

Create `tests/test_console_interfaces.py`:

```python
"""Regression + contract tests for console /api/interfaces data shape."""
from pathlib import Path

import pytest


def test_get_interfaces_returns_147_tools(tmp_path, monkeypatch):
    """Regression: console must show the same 147 tools the gateway registers."""
    from src.console_server import get_interfaces

    # Minimal upstreams.yaml matching the project's real one (89 manual tools)
    upstreams_yaml = tmp_path / "upstreams.yaml"
    manual_lines = ["upstreams:", "  rhths_meta:", "    enabled: true", "    type: http",
                    "    base_url: http://127.0.0.1:9999", "tools:"]
    for i in range(89):
        manual_lines.append(f"  - {{name: tool_{i}, routing: tool_{i}, description: 'm'}}")
    upstreams_yaml.write_text("\n".join(manual_lines) + "\n", encoding="utf-8")

    gen_yaml = tmp_path / "tools_tdx_tq_local.yaml"
    gen_lines = ["tools:"]
    for i in range(58):
        gen_lines.append(
            f"  - {{name: gen_{i}, routing: gen_{i}, description: 'g', "
            f"upstream_tool_mapping: {{tdx_tq_local: gen_{i}}}}}"
        )
    gen_yaml.write_text("\n".join(gen_lines) + "\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    # Re-import get_interfaces with patched config path
    import src.console_server as cs
    cs.GATEWAY_CONFIG_PATH = str(upstreams_yaml)
    result = get_interfaces()
    assert len(result["tools"]) == 89 + 58
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_console_interfaces.py -v`
Expected: FAIL with `assert 89 == 147` (current code reads only upstreams.yaml)

- [ ] **Step 3: Replace `get_interfaces` body**

In `src/console_server.py`, replace the entire `get_interfaces` function (lines 220-236) with:

```python
def get_interfaces() -> dict:
    from .tool_loader import load_all_tools
    config = load_config()
    config_path = Path(GATEWAY_CONFIG_PATH)
    specs = load_all_tools(config_path, config)
    upstream_tool_mapping = config.get("upstream_tool_mapping", {}) or {}
    routing_config = config.get("routing", {}) or {}
    from .tool_loader import get_cache_ttl, derive_fallback_chain

    tools = []
    for spec in specs:
        tools.append({
            "name": spec.get("name"),
            "description": spec.get("description", ""),
            "source": _derive_source(spec, upstream_tool_mapping),
            "dangerous": bool(spec.get("dangerous", False)),
            "cache_ttl_key": spec.get("cache_ttl_key"),
            "params": [
                p.get("name") if isinstance(p, dict) else p
                for p in (spec.get("params") or [])
            ],
        })
    return {"timestamp": int(time.time()), "tools": tools}
```

(Note: full enrichment comes in Task 6; this task only fixes the count.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_console_interfaces.py -v`
Expected: 1 passed

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: no regressions

- [ ] **Step 6: Commit**

```bash
git add src/console_server.py tests/test_console_interfaces.py
git commit -m "fix(console): merge both YAML files in get_interfaces (89 -> 147)"
```

---

### Task 6: Enrich `get_interfaces` with cache_ttl_seconds + routing + fallback_chain + full params

**Files:**
- Modify: `src/console_server.py:220-237`
- Modify: `tests/test_console_interfaces.py`

- [ ] **Step 1: Write the failing contract tests**

Append to `tests/test_console_interfaces.py`:

```python
def test_get_interfaces_includes_full_params(tmp_path, monkeypatch):
    from src.console_server import get_interfaces
    import src.console_server as cs

    upstreams = tmp_path / "upstreams.yaml"
    upstreams.write_text(
        "upstreams: {}\n"
        "cache:\t: {}\n"
        "routing:\t: {}\n"
        "tools:\n"
        "  - name: get_quote\n"
        "    description: 'TDX 实时行情'\n"
        "    routing: get_quote\n"
        "    upstream_tool_mapping: {tdx_local: get_quote}\n"
        "    cache_ttl_key: realtime_quote\n"
        "    params:\n"
        "      - {name: code, type: str, required: true, description: '股票代码', normalize: code}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    cs.GATEWAY_CONFIG_PATH = str(upstreams)

    result = get_interfaces()
    tool = result["tools"][0]
    assert tool["params"] == [
        {"name": "code", "type": "str", "required": True, "description": "股票代码", "normalize": "code"}
    ]
    assert tool["cache_ttl_seconds"] is None  # no cache.ttl config in this fixture


def test_get_interfaces_resolves_cache_ttl_seconds(tmp_path, monkeypatch):
    from src.console_server import get_interfaces
    import src.console_server as cs

    upstreams = tmp_path / "upstreams.yaml"
    upstreams.write_text(
        "upstreams: {}\n"
        "cache:\n"
        "  ttl:\n"
        "    realtime_quote: 10\n"
        "routing:\n"
        "  get_quote: [tdx_local]\n"
        "tools:\n"
        "  - name: get_quote\n"
        "    routing: get_quote\n"
        "    upstream_tool_mapping: {tdx_local: get_quote}\n"
        "    cache_ttl_key: realtime_quote\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    cs.GATEWAY_CONFIG_PATH = str(upstreams)

    result = get_interfaces()
    tool = result["tools"][0]
    assert tool["cache_ttl_seconds"] == 10


def test_get_interfaces_includes_routing_and_fallback_chain(tmp_path, monkeypatch):
    from src.console_server import get_interfaces
    import src.console_server as cs

    upstreams = tmp_path / "upstreams.yaml"
    upstreams.write_text(
        "upstreams: {}\n"
        "routing:\n"
        "  get_quote: [tdx_local, rhths_ashare]\n"
        "tools:\n"
        "  - name: get_quote\n"
        "    routing: get_quote\n"
        "    upstream_tool_mapping: {tdx_local: get_quote}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    cs.GATEWAY_CONFIG_PATH = str(upstreams)

    result = get_interfaces()
    tool = result["tools"][0]
    assert tool["routing"] == "get_quote"
    assert tool["fallback_chain"] == ["tdx_local", "rhths_ashare"]


def test_get_interfaces_missing_routing_returns_empty_chain(tmp_path, monkeypatch):
    from src.console_server import get_interfaces
    import src.console_server as cs

    upstreams = tmp_path / "upstreams.yaml"
    upstreams.write_text(
        "upstreams: {}\n"
        "tools:\n"
        "  - name: orphan\n"
        "    routing: orphan\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    cs.GATEWAY_CONFIG_PATH = str(upstreams)

    result = get_interfaces()
    tool = result["tools"][0]
    assert tool["routing"] == "orphan"
    assert tool["fallback_chain"] == []
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `pytest tests/test_console_interfaces.py -v`
Expected: 4 failed with `KeyError` or assertion on missing field

- [ ] **Step 3: Enrich the response in `get_interfaces`**

Replace the `get_interfaces` function body in `src/console_server.py` with:

```python
def get_interfaces() -> dict:
    from .tool_loader import (
        derive_fallback_chain,
        get_cache_ttl,
        load_all_tools,
    )

    config = load_config()
    config_path = Path(GATEWAY_CONFIG_PATH)
    specs = load_all_tools(config_path, config)
    upstream_tool_mapping = config.get("upstream_tool_mapping", {}) or {}
    routing_config = config.get("routing", {}) or {}

    tools = []
    for spec in specs:
        routing = spec.get("routing") or spec.get("name") or ""
        tools.append({
            "name": spec.get("name"),
            "description": spec.get("description", ""),
            "source": _derive_source(spec, upstream_tool_mapping),
            "dangerous": bool(spec.get("dangerous", False)),
            "cache_ttl_key": spec.get("cache_ttl_key"),
            "cache_ttl_seconds": get_cache_ttl(config, spec.get("cache_ttl_key")),
            "routing": routing,
            "fallback_chain": derive_fallback_chain(routing_config, routing),
            "params": list(spec.get("params") or []),
        })
    return {"timestamp": int(time.time()), "tools": tools}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_console_interfaces.py tests/test_load_all_tools.py tests/test_tool_loader.py -v`
Expected: all pass

- [ ] **Step 5: Run full suite**

Run: `pytest tests/ -v`
Expected: no regressions

- [ ] **Step 6: Commit**

```bash
git add src/console_server.py tests/test_console_interfaces.py
git commit -m "feat(console): enrich /api/interfaces with cache_ttl/routing/fallback_chain"
```

---

### Task 7: Update `console.html` detail panel to render new fields

**Files:**
- Modify: `console.html:1128-1139`

- [ ] **Step 1: Replace `buildDetailBody` in `console.html`**

Find the `function buildDetailBody(t)` (around line 1128). Replace it with:

```javascript
        function buildDetailBody(t) {
            const parts = [];
            if (t.dangerous) parts.push('<div class="ifs-danger-banner">⚠️ 高风险工具:可透传任意路径到上游,需谨慎授权</div>');
            // ---- Cache ----
            const cacheParts = [];
            if (t.cache_ttl_seconds != null) {
                cacheParts.push(`TTL ${t.cache_ttl_seconds}s`);
            } else if (t.cache_ttl_key) {
                cacheParts.push(`key=\`${escapeHtml(t.cache_ttl_key)}\` (no TTL configured)`);
            } else {
                cacheParts.push('no cache');
            }
            parts.push('<div class="ifs-detail-section"><div class="ifs-detail-key">Cache</div><div class="ifs-detail-val">' + cacheParts.join(' · ') + '</div></div>');
            // ---- Routing ----
            const routingParts = [`<code>${escapeHtml(t.routing || '-')}</code>`];
            if (t.fallback_chain && t.fallback_chain.length) {
                routingParts.push('→ ' + t.fallback_chain.map(s => `<span class="badge">${escapeHtml(s)}</span>`).join(' → '));
            } else {
                routingParts.push('<span class="ifs-muted">(no upstream configured)</span>');
            }
            parts.push('<div class="ifs-detail-section"><div class="ifs-detail-key">Routing</div><div class="ifs-detail-val">' + routingParts.join(' ') + '</div></div>');
            // ---- Params ----
            const params = (t.params || []).filter(p => p && typeof p === 'object');
            if (params.length) {
                const rows = params.map(p => {
                    const flag = p.required ? '<span class="ifs-req">required</span>' : '<span class="ifs-opt">optional</span>';
                    const type = p.type ? `<code>${escapeHtml(p.type)}</code>` : '';
                    const normalize = p.normalize ? ` <span class="ifs-muted">normalize=${escapeHtml(p.normalize)}</span>` : '';
                    const desc = p.description ? `<div class="ifs-param-desc">${escapeHtml(p.description)}</div>` : '';
                    return `<tr><td><code>${escapeHtml(p.name)}</code></td><td>${type}</td><td>${flag}</td><td>${desc}${normalize}</td></tr>`;
                }).join('');
                parts.push('<div class="ifs-detail-section"><div class="ifs-detail-key">Params</div><div class="ifs-detail-val"><table class="ifs-params-table"><thead><tr><th>name</th><th>type</th><th>required</th><th>description</th></tr></thead><tbody>' + rows + '</tbody></table></div></div>');
            } else {
                parts.push('<div class="ifs-detail-section"><div class="ifs-detail-key">Params</div><div class="ifs-detail-val ifs-muted">无参数</div></div>');
            }
            // ---- Source ----
            if (t.source) {
                parts.push('<div class="ifs-detail-section"><div class="ifs-detail-key">Source</div><div class="ifs-detail-val">' + escapeHtml(t.source) + '</div></div>');
            }
            return parts.join('');
        }
```

- [ ] **Step 2: Manually smoke-test**

- Start console: `powershell -File scripts/start_console.ps1` (with valid `GATEWAY_BEARER_TOKEN` in `.env`)
- Open browser to http://127.0.0.1:18080, click "MCP APIs" tab
- Confirm: count is 147 (not 89)
- Click `get_market_snapshot` row → click "详情"
- Confirm: detail panel shows
  - Cache: `no cache`
  - Routing: `get_market_snapshot` → `[tdx_tq_local]`
  - Params: `无参数`
- Click `get_quote` row → click "详情"
- Confirm: detail panel shows
  - Cache: `TTL 10s`
  - Routing: `get_quote` → `[tdx_local]`
  - Params table with `code` row showing `required`, type `str`, description, normalize=code

- [ ] **Step 3: Commit**

```bash
git add console.html
git commit -m "feat(console): render cache TTL, routing chain, and full params in MCP APIs detail panel"
```

---

### Task 8: End-to-end smoke + cleanup

**Files:**
- none (manual verification)

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: all pass; total test count = existing + 14 (tool_loader) + 5 (console_interfaces) + 1 (load_all_tools regression)

- [ ] **Step 2: Re-start console with fresh process**

```bash
powershell -File scripts/start_console.ps1
```

Open browser, navigate to MCP APIs tab, confirm:
- Tool count badge shows 147
- A previously-missing generated tool (`get_market_snapshot`) now appears under `tdx_tq_local` group
- Click any tool row's "详情" button shows the four sub-sections

- [ ] **Step 3: Commit (if any follow-up fixes)**

If any commit was made in this task, ensure it's a `chore:` or `fix:` commit.

---

## Self-Review

- [x] **Spec coverage**: T1+T2+T3 cover `tool_loader` (3 helpers). T4 covers gateway delegation. T5+T6 cover console delegation + enrichment. T7 covers UI rendering. T8 covers E2E. Every spec requirement maps to a task.
- [x] **Placeholder scan**: No "TBD"/"TODO"/"implement later". Every code step has the actual code.
- [x] **Type consistency**: `load_all_tools(config_path, config)` signature is the same in T1 (impl), T4 (gateway call), T5 (console call). `get_cache_ttl(config, ttl_key)` consistent across T2 + T6. `derive_fallback_chain(routing_config, routing_key)` consistent across T3 + T6.
- [x] **Smoke coverage**: T7 step 2 + T8 cover the manual smoke; T5 step 4 covers automated regression.