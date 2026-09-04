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
    """Return the routing chain the gateway router would walk for ``routing_key``.

    Resolution order (matches ``src/router.py:_get_routing_chain``):
    1. If ``routing[routing_key].chain`` is defined (list, possibly empty), return it.
    2. If ``routing[routing_key]`` exists but has no ``chain`` field, return ``[]``.
    3. Else return ``upstream_tool_mapping[routing_key].keys()`` in insertion order.
    4. Else return ``[]``.

    This is intentionally NOT a "fallback by priority" — the router walks a fixed
    list of upstreams that actually implement the tool, not every upstream sorted
    by some priority field. Filtering by capability happens upstream via
    ``upstream_tool_mapping``.
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
