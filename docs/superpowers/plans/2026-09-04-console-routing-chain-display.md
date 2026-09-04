# Console Routing Chain Display Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix console.html "Fallback" display so it shows the actual routing chain the router walks, not a derived-by-priority list that includes upstreams the tool doesn't actually use.

**Architecture:** Replace `derive_fallback_chain(config, upstream_name)` (which sorts all upstreams by priority) with `derive_routing_chain(config, routing_key)` (which returns `routing[routing_key].chain` from YAML, falling back to `upstream_tool_mapping[routing_key].keys()`). The console API renames `fallback_chain` → `chain`, and console.html label changes "Fallback" → "Chain". The displayed values now match what `src/router.py:_get_routing_chain()` actually returns.

**Tech Stack:** Python 3.14, pytest, FastMCP, vanilla JS/HTML.

---

## Problem Statement

`src/console_server.py:get_interfaces()` currently calls `derive_fallback_chain(config, upstream_name)` (`src/tool_loader.py:52-67`) which:
1. Sorts ALL `config[upstreams]` by `priority` (ascending)
2. Moves the requested upstream to the front
3. Returns the full list

This produces wrong values for tools with shorter chains. Example: `get_minute_bar` YAML `chain: ["tokenwave_tdx", "tdx_local"]` (2 entries) but UI shows 6 entries because `derive_fallback_chain` doesn't filter by what each upstream implements. The router (`src/router.py:103`) only walks the YAML chain, never the "expanded" list.

`get_minute_bar` UI today: `tokenwave_tdx → tdx_local → tdx_tq_local → fuyao_ashare → fuyao_index → fuyao_meta → fuyao_fund`
`get_minute_bar` router actually: `tokenwave_tdx → tdx_local` (then error)

The four extra `fuyao_*` entries are display lies — they never get tried.

---

## File Structure

- **Modify** `src/tool_loader.py` — replace `derive_fallback_chain` with `derive_routing_chain`
- **Modify** `src/console_server.py` — call new function, rename response field
- **Modify** `console.html` — change label "Fallback" → "Chain"
- **Modify** `tests/test_tool_loader.py` — update + add tests for `derive_routing_chain`
- **Modify** `tests/test_console_interfaces.py` — rename field assertion + add regression test

---

## Task 1: Replace `derive_fallback_chain` with `derive_routing_chain` in tool_loader

**Files:**
- Modify: `src/tool_loader.py:52-67` (replace function)
- Modify: `tests/test_tool_loader.py:114-135` (update + add tests)

- [ ] **Step 1: Update existing tests to expect new function name & behavior**

In `tests/test_tool_loader.py`, replace lines 114-135 (the three `derive_fallback_chain` tests) with:

```python
def test_derive_routing_chain_from_explicit_routing_config():
    """When YAML routing.X.chain is defined, return it verbatim."""
    from src.tool_loader import derive_routing_chain
    config = {
        "routing": {
            "get_minute_bar": {"chain": ["tokenwave_tdx", "tdx_local"]},
        },
        "upstream_tool_mapping": {
            "get_minute_bar": {"tokenwave_tdx": "x", "tdx_local": "y",
                               "fuyao_fund": "z"},  # noise — chain should win
        },
    }
    assert derive_routing_chain(config, "get_minute_bar") == [
        "tokenwave_tdx", "tdx_local",
    ]


def test_derive_routing_chain_falls_back_to_tool_mapping_keys():
    """When routing.X is missing, derive chain from upstream_tool_mapping keys (insertion order)."""
    from src.tool_loader import derive_routing_chain
    config = {
        "upstream_tool_mapping": {
            "search_stock": {"tdx_local": "a", "fuyao_meta": "b"},
        },
    }
    assert derive_routing_chain(config, "search_stock") == [
        "tdx_local", "fuyao_meta",
    ]


def test_derive_routing_chain_unknown_routing_key_returns_empty():
    from src.tool_loader import derive_routing_chain
    config = {
        "routing": {"get_minute_bar": {"chain": ["tokenwave_tdx"]}},
        "upstream_tool_mapping": {},
    }
    assert derive_routing_chain(config, "no_such_tool") == []


def test_derive_routing_chain_empty_when_nothing_defined():
    """Empty config — neither routing nor upstream_tool_mapping has the key."""
    from src.tool_loader import derive_routing_chain
    config = {"routing": {}, "upstream_tool_mapping": {}}
    assert derive_routing_chain(config, "anything") == []


def test_derive_routing_chain_routing_block_without_chain_field_returns_empty():
    """If routing.X exists but has no chain field, return empty (don't fall back to mapping)."""
    from src.tool_loader import derive_routing_chain
    config = {
        "routing": {"get_minute_bar": {"description": "分钟K线行情 (TDX)"}},
        "upstream_tool_mapping": {
            "get_minute_bar": {"tokenwave_tdx": "x"},
        },
    }
    # Spec decision: explicit routing block with no chain = no fallback.
    # The router (`src/router.py:_get_routing_chain`) treats empty chain as "no route",
    # so we match that contract here. Console callers see empty chain consistently.
    assert derive_routing_chain(config, "get_minute_bar") == []
```

- [ ] **Step 2: Run updated tests — they should fail (function doesn't exist yet)**

Run: `python -m pytest tests/test_tool_loader.py -k derive_routing_chain -v`
Expected: FAIL with `ImportError: cannot import name 'derive_routing_chain'`

- [ ] **Step 3: Implement `derive_routing_chain` in src/tool_loader.py**

In `src/tool_loader.py`, replace lines 52-67 (the entire `derive_fallback_chain` function) with:

```python
def derive_routing_chain(config: dict, routing_key: str) -> list[str]:
    """Return the routing chain the gateway router would walk for ``routing_key``.

    Resolution order (matches ``src/router.py:_get_routing_chain``):
    1. If ``routing[routing_key].chain`` is defined (list, possibly empty), return it.
    2. Else return ``upstream_tool_mapping[routing_key].keys()`` in insertion order.
    3. Else return ``[]``.

    This is intentionally NOT a "fallback by priority" — the router walks a fixed
    list of upstreams that actually implement the tool, not every upstream sorted
    by some priority field. Filtering by capability happens upstream via
    ``upstream_tool_mapping``.
    """
    routing = config.get("routing") or {}
    entry = routing.get(routing_key)
    if isinstance(entry, dict) and "chain" in entry:
        return list(entry.get("chain") or [])
    mapping = config.get("upstream_tool_mapping") or {}
    if routing_key in mapping:
        return list(mapping[routing_key].keys())
    return []
```

- [ ] **Step 4: Run tests — they should pass**

Run: `python -m pytest tests/test_tool_loader.py -k derive_routing_chain -v`
Expected: 5 passed

- [ ] **Step 5: Verify no other consumer still references `derive_fallback_chain`**

Run: `grep -rn "derive_fallback_chain" src/ tests/`
Expected: no matches (only the new name should exist)

- [ ] **Step 6: Commit**

```bash
git add src/tool_loader.py tests/test_tool_loader.py
git commit -m "refactor(tool_loader): derive_routing_chain replaces derive_fallback_chain

Routing display must match what src/router.py actually walks. The old
derive_fallback_chain returned ALL upstreams sorted by priority, including
ones that don't implement the tool — pure display lies for short chains
like get_minute_bar (chain=[tokenwave_tdx,tdx_local] but UI showed 6).

derive_routing_chain resolves routing[routing_key].chain first, falling
back to upstream_tool_mapping keys — same precedence as the router."
```

---

## Task 2: Update console_server to emit `chain` (not `fallback_chain`)

**Files:**
- Modify: `src/console_server.py:225,239,250`
- Modify: `tests/test_console_interfaces.py:97-112` + add regression test

- [ ] **Step 1: Update `test_get_interfaces_includes_fallback_chain` and add a new regression test**

In `tests/test_console_interfaces.py`, replace the existing `test_get_interfaces_includes_fallback_chain` (lines 97-112) with:

```python
def test_get_interfaces_includes_chain():
    """Contract: response includes chain field (router-walk order, not priority sort)."""
    import yaml
    from unittest.mock import patch
    from src.console_server import get_interfaces

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.console_server.load_config", return_value=full_config):
        from src.console_server import get_interfaces
        result = get_interfaces()

    tools_with_chain = [t for t in result["tools"] if t.get("chain")]
    assert tools_with_chain, "Expected at least one tool with chain"
    for tool in tools_with_chain:
        assert "chain" in tool, f"Missing chain field for {tool.get('name')}"
        assert isinstance(tool["chain"], list), "chain should be a list"
        # Contract: no legacy fallback_chain field
        assert "fallback_chain" not in tool, (
            f"{tool.get('name')} still emits legacy fallback_chain field"
        )


def test_get_interfaces_chain_matches_router_for_short_chain_tools():
    """Regression: get_minute_bar must show exactly 2 sources (the YAML chain),
    NOT the full upstream list (which was the derive_fallback_chain bug).

    Pre-fix: get_minute_bar chain showed [tokenwave_tdx, tdx_local, tdx_tq_local,
    fuyao_ashare, fuyao_index, fuyao_meta, fuyao_fund] — a lie, since the router
    only walks the first 2.
    """
    import yaml
    from unittest.mock import patch
    from src.console_server import get_interfaces

    config_path = "config/upstreams.yaml"
    with open(config_path, encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    with patch("src.console_server.load_config", return_value=full_config):
        result = get_interfaces()

    by_name = {t["name"]: t for t in result["tools"]}

    # get_minute_bar: YAML chain is [tokenwave_tdx, tdx_local]
    minute_bar = by_name.get("get_minute_bar")
    assert minute_bar is not None, "get_minute_bar not exposed"
    assert minute_bar["chain"] == ["tokenwave_tdx", "tdx_local"], (
        f"get_minute_bar chain mismatch: {minute_bar['chain']}"
    )
    # Specifically: no fuyao_* entries (those upstreams don't implement get_minute_bar)
    fuyao_in_chain = [u for u in minute_bar["chain"] if u.startswith("fuyao_")]
    assert fuyao_in_chain == [], (
        f"get_minute_bar chain leaked non-implementing upstreams: {fuyao_in_chain}"
    )

    # get_realtime_quote: YAML chain is [tokenwave_tdx, tdx_local, fuyao_ashare]
    rtq = by_name.get("get_realtime_quote")
    assert rtq is not None, "get_realtime_quote not exposed"
    assert rtq["chain"] == ["tokenwave_tdx", "tdx_local", "fuyao_ashare"], (
        f"get_realtime_quote chain mismatch: {rtq['chain']}"
    )
```

- [ ] **Step 2: Run new tests — they should fail (console_server still uses fallback_chain)**

Run: `python -m pytest tests/test_console_interfaces.py::test_get_interfaces_includes_chain tests/test_console_interfaces.py::test_get_interfaces_chain_matches_router_for_short_chain_tools -v`
Expected: FAIL with assertion errors (chain field missing, or value mismatch)

- [ ] **Step 3: Update src/console_server.py:get_interfaces to call derive_routing_chain**

In `src/console_server.py`:

**Change line 225** from:
```python
from .tool_loader import load_all_tools, get_cache_ttl, derive_fallback_chain
```
to:
```python
from .tool_loader import load_all_tools, get_cache_ttl, derive_routing_chain
```

**Replace lines 230-239** (the upstream_name derivation + fallback_chain call) with:

```python
        # routing_key → chain (what the router actually walks)
        routing_key = spec.get("routing")
        chain = derive_routing_chain(config, routing_key) if routing_key else []
```

(Delete the old `upstream_name` derivation — no longer needed.)

**Change line 249** from:
```python
            "routing": routing_key,
            "fallback_chain": fallback_chain,
```
to:
```python
            "routing": routing_key,
            "chain": chain,
```

- [ ] **Step 4: Re-run tests — they should pass**

Run: `python -m pytest tests/test_console_interfaces.py -k "chain or fallback_chain" -v`
Expected: all pass (the new `test_get_interfaces_includes_chain` and `test_get_interfaces_chain_matches_router_for_short_chain_tools` pass)

- [ ] **Step 5: Run full test suite to confirm nothing else broke**

Run: `python -m pytest -q`
Expected: 146 passed (was 144 before; we added 2 new tests and removed 1 — net +1)

If fewer tests pass, the most likely cause is another consumer of `fallback_chain` — search with `grep -rn "fallback_chain" src/ tests/ console.html` and update or remove it.

- [ ] **Step 6: Commit**

```bash
git add src/console_server.py tests/test_console_interfaces.py
git commit -m "feat(console): emit 'chain' field (router-walk order) instead of fallback_chain

Console /api/interfaces now returns 'chain' — the actual list of upstreams
the gateway router walks for each tool, in walk order. Previously returned
'fallback_chain' which was ALL upstreams sorted by priority, including
upstreams that don't implement the tool (display lie for short chains).

get_minute_bar went from 6 displayed sources to its true 2.
get_realtime_quote went from 7 to its true 3."
```

---

## Task 3: Update console.html label "Fallback" → "Chain"

**Files:**
- Modify: `console.html:1178` (label text)
- Modify: `console.html:178-179` (CSS class rename optional but recommended for consistency)
- Modify: `tests/test_console_interfaces.py:175-186` (update assertion)

- [ ] **Step 1: Update the UI test that asserts on `/api/status` and `descByName`**

In `tests/test_console_interfaces.py`, replace `test_console_html_uses_upstream_description` (lines 175-186) with:

```python
def test_console_html_uses_upstream_description():
    """Regression: console.html onboarding must fetch /api/upstreams and prefer description."""
    html_path = "console.html"
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    assert "/api/status" in html, "console.html must fetch /api/status for upstream descriptions"
    assert "descByName" in html, "console.html must build descByName map from upstream description"


def test_console_html_routes_section_uses_chain_label():
    """Regression: console.html must label the routing walk order as 'Chain', not 'Fallback'.

    Pre-fix: label was 'Fallback' but data was an unfiltered priority-sorted list —
    mismatched with src/router.py behavior.
    """
    html_path = "console.html"
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    # The interface detail panel must use Chain as the key label
    assert "Fallback" not in html or html.count("Fallback") == html.count("Chain"), (
        "console.html still uses 'Fallback' label for routing walk order; "
        "should be 'Chain' to match src/router.py behavior"
    )
    # Sanity: at least one Chain label exists for routing
    assert ">Chain<" in html, "console.html missing 'Chain' label for routing section"
```

- [ ] **Step 2: Run new test — should fail (label is still "Fallback")**

Run: `python -m pytest tests/test_console_interfaces.py::test_console_html_routes_section_uses_chain_label -v`
Expected: FAIL with assertion error about Fallback/Chain count mismatch

- [ ] **Step 3: Update console.html: change label text + CSS class**

In `console.html`:

**Change line 178** from:
```css
        .ifs-fallback-item { display: inline-block; padding: 2px 8px; background: #e2e8f0; border-radius: 3px; font-size: 0.75rem; margin: 0 2px; }
        .ifs-fallback-item.primary { background: var(--primary); color: white; font-weight: 500; }
```
to:
```css
        .ifs-chain-item { display: inline-block; padding: 2px 8px; background: #e2e8f0; border-radius: 3px; font-size: 0.75rem; margin: 0 2px; }
        .ifs-chain-item.primary { background: var(--primary); color: white; font-weight: 500; }
```

**Change line 1174-1178** (the chain rendering block) from:
```javascript
                if (t.fallback_chain && t.fallback_chain.length) {
                    const chainHtml = t.fallback_chain.map((u, i) => {
                        const isPrimary = i === 0;
                        return `<span class="ifs-fallback-item${isPrimary ? ' primary' : ''}">${escapeHtml(u)}</span>`;
                    }).join(' → ');
                    parts.push(`<div class="ifs-detail-grid"><div class="ifs-detail-key">Fallback</div><div class="ifs-detail-val">${chainHtml}</div></div>`);
                }
```
to:
```javascript
                if (t.chain && t.chain.length) {
                    const chainHtml = t.chain.map((u, i) => {
                        const isPrimary = i === 0;
                        return `<span class="ifs-chain-item${isPrimary ? ' primary' : ''}">${escapeHtml(u)}</span>`;
                    }).join(' → ');
                    parts.push(`<div class="ifs-detail-grid"><div class="ifs-detail-key">Chain</div><div class="ifs-detail-val">${chainHtml}</div></div>`);
                }
```

- [ ] **Step 4: Run the UI test again — should pass**

Run: `python -m pytest tests/test_console_interfaces.py::test_console_html_routes_section_uses_chain_label -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to confirm nothing broke**

Run: `python -m pytest -q`
Expected: 148 passed (was 146; +2 from this round: chain label test + new chain field tests added in Task 2 — actual count depends on whether we kept the replaced `test_get_interfaces_includes_fallback_chain` or not; the plan replaces it so net = 145 + 3 new = 148).

- [ ] **Step 6: Commit**

```bash
git add console.html tests/test_console_interfaces.py
git commit -m "feat(console): rename 'Fallback' label and CSS class to 'Chain'

Match the actual semantics — console now displays the chain the router
walks, not a priority-sorted superset. CSS class .ifs-fallback-item
renamed to .ifs-chain-item for consistency."
```

---

## Self-Review Checklist

- [x] **Spec coverage:** Three explicit user requests covered — (1) derive_routing_chain returns actual chain, (2) API field renamed, (3) UI label updated.
- [x] **Placeholder scan:** No "TBD", "TODO", or vague steps. Each task shows exact code.
- [x] **Type consistency:** `derive_routing_chain(config, routing_key)` signature is consistent in all 3 tasks (test → impl → caller).
- [x] **No over-building:** Only touches what's broken — no opportunistic refactoring of router.py or YAML.
- [x] **Each task = working state:** After Task 1 the function exists; after Task 2 the API emits correct field; after Task 3 the UI displays correctly. All three can ship independently.