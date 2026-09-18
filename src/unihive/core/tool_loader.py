"""Shared YAML → tool-spec loader used by both gateway and console."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..utils.env_utils import env_flag

_GENERATED_FILENAMES = [
    "tools_tdx_quant.yaml",
    "tools_mootdx2.yaml",
    "tools_omni.yaml",
    "tools_thsdk.yaml",
    "tools_ths_local.yaml",
]


def filter_specs_by_env(specs: list[dict]) -> list[dict]:
    """Drop specs gated by ``requires_env`` when that env var is not truthy.

    Gate evaluated at load time only — there is deliberately no runtime
    toggle; a disabled spec never reaches tool registration nor the
    upstream_tool_mapping merge.
    """
    return [
        spec
        for spec in specs
        if not spec.get("requires_env") or env_flag(spec["requires_env"])
    ]


def load_all_tools(
    config_path: Path,
    config: dict,
    *,
    include_env_gated: bool = False,
) -> list[dict]:
    """Merge ``config[upstreams.yaml].tools`` with the generated tools file
    (``tools_tdx_quant.yaml`` looked up next to ``config_path`` or in
    ``./config/``). For every generated spec, copy its inline
    ``upstream_tool_mapping`` into ``config[upstream_tool_mapping]`` so
    downstream consumers can look up the source by routing key.

    Mutates ``config`` in place. Returns the merged tool-spec list.

    Args:
        include_env_gated: 连 ``requires_env`` 门控的 spec 一并加载。
            运行时注册保持门控 (默认 False); ``unihive config lint`` 传 True,
            因为目录正确性校验不应依赖本机是否打开了写工具开关。
    """
    source_tools = list(config.get("tools", []) or [])
    tools: list[dict] = (
        list(source_tools)
        if include_env_gated
        else filter_specs_by_env(source_tools)
    )
    top_mapping = config.setdefault("upstream_tool_mapping", {})
    base_dir = config_path.parent.resolve()
    for filename in _GENERATED_FILENAMES:
        for candidate in (
            base_dir / filename,
            base_dir / "config" / filename,
            Path("config") / filename,
        ):
            if candidate.exists():
                with candidate.open(encoding="utf-8") as f:
                    gen_cfg = yaml.safe_load(f) or {}
                gen_source = list(gen_cfg.get("tools", []) or [])
                gen_tools = (
                    gen_source
                    if include_env_gated
                    else filter_specs_by_env(gen_source)
                )
                tools.extend(gen_tools)
                for spec in gen_tools:
                    rk = spec.get("routing")
                    if rk and rk not in top_mapping:
                        top_mapping[rk] = dict(spec.get("upstream_tool_mapping") or {})
                break
    return tools


def backing_upstreams(routing_key: str, config: dict) -> list[str]:
    """返回网关工具运行时实际会尝试的上游名 (与 ``router.route()`` 同规则)。

    ``routing[routing_key].chain`` 非空时以 chain 为权威 (chain 里允许出现
    mapping 未列出的上游, router 会以上游工具名=网关工具名兜底);
    chain 缺失/为空时回落到 ``upstream_tool_mapping[routing_key]`` 的键。

    注意与上方 ``derive_routing_chain`` 的区别: 后者是控制台**展示**用,
    对"有 routing 块但没写 chain"刻意返回 ``[]`` 以暴露未决路由; 本函数用于
    启用态过滤, 必须复刻 router 的实际回落行为, 不能跟着展示逻辑走。
    """
    routing = config.get("routing") or {}
    entry = routing.get(routing_key)
    chain = entry.get("chain") if isinstance(entry, dict) else None
    if chain:
        return list(chain)
    mapping = config.get("upstream_tool_mapping") or {}
    return list((mapping.get(routing_key) or {}).keys())


def enabled_upstream_names(config: dict) -> set[str]:
    """``config.upstreams`` 中 ``enabled`` 为真的上游名集合。

    缺省 (字段缺失) 视为禁用, 与启动循环 ``cfg.get("enabled", False)`` 一致。
    """
    upstreams = config.get("upstreams") or {}
    return {
        name
        for name, cfg in upstreams.items()
        if isinstance(cfg, dict) and cfg.get("enabled", False)
    }


def filter_specs_by_enabled(specs: list[dict], config: dict) -> list[dict]:
    """只保留"至少一个 backer 上游处于启用态"的工具 spec。

    用于启动注册与运行时热开关: 全部 backer 都禁用的工具不发布到对外 /mcp。
    无 backer 的 spec 防御性保留 —— 正常配置经 validate_specs 后不应出现,
    但不能因配置缺映射把工具静默吞掉。
    """
    enabled = enabled_upstream_names(config)
    kept: list[dict] = []
    for spec in specs:
        routing_key = spec.get("routing") or spec.get("name")
        backers = backing_upstreams(routing_key, config)
        if not backers or any(b in enabled for b in backers):
            kept.append(spec)
    return kept


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
