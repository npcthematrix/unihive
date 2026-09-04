# MCP APIs Console Data — Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the management console's "MCP APIs" panel show the same tools the gateway actually exposes, with enough schema detail to be useful (full params, TTL seconds, fallback chain).

**Architecture:** Extract the existing YAML-merge logic from `gateway_server._load_all_tools()` into a shared `tool_loader` module. Both `gateway_server` and `console_server` call it. Console enriches each tool entry with three new fields (full params, cache TTL seconds, fallback chain). Frontend renders the new fields in the existing detail panel; no other UI changes.

**Tech Stack:** Python 3.10+, PyYAML, FastMCP, vanilla JS in `console.html`.

---

## Problem

The console's `/api/interfaces` endpoint reads `config/upstreams.yaml` only and exposes 89 manual tools. The gateway merges both `config/upstreams.yaml` and `config/tools_tdx_tq_local.yaml` and registers 147 tools. The console also strips `params` to name-only and never shows TTL seconds.

Today, a user clicking the "MCP APIs" tab sees 89 tools and partial schema. The gateway actually exposes 147.

## Solution

Extract the YAML-merge logic to a shared module so both gateway and console read the same data. Enrich the API contract with the schema fields the gateway already knows. Render the new fields in the existing detail panel.

## Components

### New: `src/tool_loader.py`

Pure functions, no I/O except reading YAML files:

```python
def load_all_tools(config_path: Path) -> list[dict]:
    """Merge config/upstreams.yaml tools + config/tools_tdx_tq_local.yaml tools.
    Write each generated spec's upstream_tool_mapping back to top-level
    config['upstream_tool_mapping'] so downstream consumers (router, console)
    can resolve the source for every tool.
    """

def get_cache_ttl(config: dict, ttl_key: str | None) -> int | None:
    """Resolve cache.ttl[ttl_key] → seconds. Returns None when key missing or
    cache section not configured."""

def derive_fallback_chain(routing_config: dict, routing_key: str) -> list[str]:
    """Return the ordered upstream list from routing_config[routing_key].
    Empty list when missing."""
```

### Modify: `src/console_server.py`

- Delete the inline YAML merge inside `get_interfaces()`.
- Call `tool_loader.load_all_tools(config_path)` instead.
- Enrich each tool dict with the three new fields below.

### Modify: `src/gateway_server.py`

- `_load_all_tools()` keeps the same name and behavior but its body delegates to `tool_loader.load_all_tools(self.config_path)`.
- Behavior must be byte-identical post-refactor.

### Modify: `console.html`

Only `renderToolRow()` (and helpers) change. The list, sidebar groups, and toolbar stay the same. The detail panel expands to four sub-sections:
1. **Params table** — name | type | required | description | normalize
2. **Cache** — TTL seconds (or "no TTL configured" or "no cache")
3. **Routing** — `routing` key, then ordered `fallback_chain` as chips
4. **Dangerous badge** — top-right when `dangerous`

## Data flow

```
console.html (renderer)
        │ fetch /api/interfaces
        ▼
src/console_server.py :: get_interfaces()
        │
        ├─ tool_loader.load_all_tools(config_path)
        │     ├─ read config/upstreams.yaml
        │     ├─ read config/tools_tdx_tq_local.yaml  (if exists)
        │     └─ merge + write per-spec mappings back to top level
        │
        ├─ tool_loader.get_cache_ttl(config, spec.cache_ttl_key)
        ├─ tool_loader.derive_fallback_chain(routing_config, spec.routing)
        └─ assemble response:
            {
              timestamp: int,
              tools: [{
                name, description, source, dangerous,
                cache_ttl_key, cache_ttl_seconds,   ← NEW
                routing, fallback_chain,             ← NEW
                params: [{name, type, required, description, normalize}], ← was name-only
              }, ...]
            }
        ▼
console.html renders tool list (sidebar groups by source).
    click row → detail panel (Params / Cache / Routing / Dangerous)
```

## API contract: `GET /api/interfaces`

```json
{
  "timestamp": 1735860000,
  "tools": [
    {
      "name": "get_market_snapshot",
      "description": "TQ-Local 行情快照",
      "source": "tdx_tq_local",
      "dangerous": false,
      "cache_ttl_key": null,
      "cache_ttl_seconds": null,
      "routing": "get_market_snapshot",
      "fallback_chain": ["tdx_tq_local"],
      "params": []
    },
    {
      "name": "get_quote",
      "description": "TDX 实时行情",
      "source": "tdx_local",
      "dangerous": false,
      "cache_ttl_key": "realtime_quote",
      "cache_ttl_seconds": 10,
      "routing": "get_quote",
      "fallback_chain": ["tdx_local"],
      "params": [
        {"name": "code", "type": "str", "required": true, "description": "股票代码", "normalize": "code"}
      ]
    }
  ]
}
```

## Error handling

| Case | Console (`/api/interfaces`) | Gateway | UI |
|---|---|---|---|
| YAML parse error | 200 with `{"tools": [], "error": "<reason>"}` | startup fails (unchanged) | red banner "配置加载失败" |
| `tools_tdx_tq_local.yaml` missing | only manual tools, no error | log info, only manual tools (unchanged) | works as today |
| Tool name collision | generated wins, log warning | generated wins, log warning (unchanged) | shows generated |
| `routing` key not in `routing_config` & no inline `upstream_tool_mapping` | `fallback_chain: []`, `routing` field still shown | startup log warning (unchanged) | "no upstream configured" |
| `cache_ttl_key` missing from `cache.ttl` | `cache_ttl_seconds: null` | runtime warning at first call (unchanged) | "key=`{key}` (no TTL configured)" |
| `cache_ttl_key` empty | `cache_ttl_seconds: null` | runtime bypass (unchanged) | "no cache" |
| `param.normalize = "code"` but type ≠ `str` | out of scope | out of scope | n/a |

## Testing

### New: `tests/test_tool_loader.py`

- `load_all_tools`
  - empty config → `[]`
  - manual-only → returns manual tools
  - generated-only → returns generated tools
  - both → merged list (89 + 58 = 147)
  - generated `upstream_tool_mapping` written back to top level
  - duplicate name → generated wins, warning emitted
  - missing generated file → no-op, manual tools returned

- `get_cache_ttl`
  - known key → resolved seconds
  - unknown key → `None`
  - missing `cache.ttl` section → `None`

- `derive_fallback_chain`
  - known routing key → ordered list
  - unknown key → `[]`
  - no routing config → `[]`

### Modify: `tests/test_load_all_tools.py`

Keep the existing assertion (147 tools merged), but the function-under-test now delegates to `tool_loader.load_all_tools`. Update the import path or refactor to import the helper.

### Regression: original bug

New pytest asserts `get_interfaces()` returns **147** tools (not 89). This is the smoke test that catches the bug we fixed.

### Console API contract

- `get_interfaces()` with sample config: `tools[i].params` is full schema, `cache_ttl_seconds` resolves when key set, `routing` and `fallback_chain` populated
- Edge case tests for missing TTL / missing routing / missing generated YAML

### Manual smoke

- Run console, click MCP APIs tab → count goes from 89 → 147
- Click `get_market_snapshot` → detail panel shows `Cache: no cache`, `Routing: get_market_snapshot → [tdx_tq_local]`, full Params table
- Click `get_quote` → detail panel shows `Cache: 10s`, full Params table including `normalize: code`

### Out of scope

- Playwright E2E (manual smoke only)
- Performance / load (file is read each request; acceptable)
- `param.normalize = "code"` validation (pre-existing issue)

## Out of scope

- Per-tool live status / reachability
- Hot-reload of config
- Per-tool documentation links

## Files affected

- **New**: `src/tool_loader.py`, `tests/test_tool_loader.py`
- **Modify**: `src/console_server.py`, `src/gateway_server.py`, `console.html`, `tests/test_load_all_tools.py`

## Backwards compatibility

`/api/interfaces` adds fields. Existing fields unchanged. Frontend is updated to use new fields; old fields are still emitted.