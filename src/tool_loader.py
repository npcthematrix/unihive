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
    candidates = [
        base_dir / _GENERATED_FILENAME,
        base_dir / "config" / _GENERATED_FILENAME,
    ]
    # Only check cwd-relative "config/" candidate when config_path is at project root.
    if base_dir == Path(".").resolve():
        candidates.append(base_dir / "config" / _GENERATED_FILENAME)
    for candidate in candidates:
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
