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


def get_cache_ttl(config: dict, ttl_key: str | None) -> int | None:
    """Resolve ``cache.ttl[ttl_key]`` → seconds. Returns ``None`` when
    the key is empty, missing from config, or the cache section itself
    is not configured.
    """
    if not ttl_key:
        return None
    ttl = (config.get("cache") or {}).get("ttl") or {}
    return ttl.get(ttl_key)


def derive_routing_chain(config: dict, routing_key: str) -> list[str]:
    """Return the routing chain displayed in the console for ``routing_key``.

    Resolution order:
    1. If ``routing[routing_key].chain`` is defined → return it verbatim.
    2. Else if ``routing[routing_key]`` is a dict without a ``chain`` field
       (e.g. only has a ``description``) → return ``[]``. This is a deliberate
       divergence from ``src/router.py:route()``, which in this case would
       fall back to ``upstream_tool_mapping``. Rationale: an explicit
       ``routing.X`` block with a description but no chain is treated as a
       TODO marker (admin hasn't decided the route yet). Surfacing ``[]`` in
       the UI makes the missing route visible rather than silently using
       whatever upstream_tool_mapping declares.
    3. Else if ``upstream_tool_mapping[routing_key]`` exists → return its
       keys in insertion order.
    4. Else return ``[]``.

    This is intentionally NOT a "fallback by priority" — the gateway router
    walks a fixed list of upstreams that actually implement the tool, not
    every upstream sorted by some priority field. Capability filtering
    happens upstream via ``upstream_tool_mapping``.

    See also: ``src/router.py:_get_routing_chain`` (single-step lookup) and
    ``src/router.py:route()`` (full 2-tier resolution used at runtime).
    """
    routing = config.get("routing") or {}
    entry = routing.get(routing_key)
    if isinstance(entry, dict):
        if "chain" in entry:
            return list(entry.get("chain") or [])
        # routing entry exists but no chain field -> explicit routing block without chain
        return []
    mapping = config.get("upstream_tool_mapping") or {}
    if routing_key in mapping:
        return list(mapping[routing_key].keys())
    return []
