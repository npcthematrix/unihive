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


def derive_fallback_chain(config: dict, upstream: str) -> list[str]:
    """Derive the fallback chain for ``upstream`` by sorting all upstreams
    by priority (ascending). The requested upstream comes first, then all
    others in priority order. Returns empty list if upstream not found.
    """
    upstreams = config.get("upstreams") or {}
    if upstream not in upstreams:
        return []
    # Sort by priority, ascending
    sorted_upstreams = sorted(upstreams.items(), key=lambda kv: kv[1].get("priority", 999))
    names = [name for name, _ in sorted_upstreams]
    # Move requested upstream to front if present
    if upstream in names:
        names.remove(upstream)
        names.insert(0, upstream)
    return names
