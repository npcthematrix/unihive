"""DB 实体 → 运行时 config dict 的装配器 (v2, 纯函数, 不做 IO)。

与 :mod:`unihive.core.template_normalize` 互为逆操作, 由 golden 双向往返测试
锁死等价性: YAML 模板 → 实体 → 装配 ≡ 现行加载链产出的合并 dict + spec 列表。

装配产出的 dict 仍是**未解析 ${VAR}** 的形态; 调用方随后走与今天相同的
resolve_env_vars(strict) → validate_config → filter_specs_by_env/enabled 链路。
"""
from __future__ import annotations

import copy
from typing import Any

from ..utils.config_loader import merge_config_overlay
from .template_normalize import (
    BOOTSTRAP_GATEWAY_KEYS,
    NormalizedTemplate,
    ToolEntity,
    UpstreamEntity,
)


def entities_from_json(bundle: dict[str, Any]) -> NormalizedTemplate:
    """把存储层/快照里的纯 JSON bundle 还原为实体 dataclass。"""
    upstreams = [
        UpstreamEntity(
            name=u["name"],
            type=u["type"],
            enabled=bool(u["enabled"]),
            config=u.get("config", {}),
        )
        for u in bundle.get("upstreams", [])
    ]
    tools = [
        ToolEntity(
            name=t["name"],
            routing=t["routing"],
            description=t.get("description", ""),
            cache_ttl_key=t.get("cache_ttl_key"),
            requires_env=t.get("requires_env"),
            publish_mode=t.get("publish_mode", "default"),
            mapping=t.get("mapping", {}),
            spec=t["spec"],
            source_file=t.get("source_file", "upstreams.yaml"),
            origin=t.get("origin", "builtin"),
        )
        for t in bundle.get("tools", [])
    ]
    return NormalizedTemplate(
        sections=bundle.get("sections", {}),
        upstreams=upstreams,
        tools=tools,
    )


def assemble_config(template: NormalizedTemplate) -> dict[str, Any]:
    """实体 → 与现行加载链同形的原始 config dict (深拷贝, 不 mutate 实体)。

    - sections 原样铺回顶层;
    - upstream 行重建为 {enabled, type, **异型配置} 块;
    - upstream_tool_mapping 由各工具行的有效 mapping 按 routing key 重建
      (空 mapping 的行不产生顶层条目, 与 YAML 现状一致);
    - tools 为完整 spec 列表 (顺序即种子入库顺序)。
    """
    config: dict[str, Any] = copy.deepcopy(template.sections)

    config["upstreams"] = {
        u.name: {**copy.deepcopy(u.config), "enabled": u.enabled, "type": u.type}
        for u in template.upstreams
    }

    mapping: dict[str, dict[str, str]] = {}
    for tool in template.tools:
        if tool.mapping:
            mapping[tool.routing] = copy.deepcopy(tool.mapping)
    config["upstream_tool_mapping"] = mapping

    config["tools"] = [copy.deepcopy(tool.spec) for tool in template.tools]
    return config


def extract_bootstrap(config_yaml_raw: dict[str, Any]) -> dict[str, Any]:
    """从原始 config.yaml 抽取只允许留在 YAML 的 bootstrap 子集。

    - console 整段 (账号/密码哈希/session_secret);
    - gateway 的 host/port/mcp_path。
    """
    bootstrap: dict[str, Any] = {}
    console = config_yaml_raw.get("console")
    if isinstance(console, dict) and console:
        bootstrap["console"] = copy.deepcopy(console)
    gateway = config_yaml_raw.get("gateway")
    if isinstance(gateway, dict):
        gateway_bootstrap = {k: copy.deepcopy(v) for k, v in gateway.items() if k in BOOTSTRAP_GATEWAY_KEYS}
        if gateway_bootstrap:
            bootstrap["gateway"] = gateway_bootstrap
    return bootstrap


def merge_bootstrap(config: dict[str, Any], config_yaml_raw: dict[str, Any]) -> dict[str, Any]:
    """把 config.yaml 的 bootstrap 子集浅合并回装配结果 (就地修改并返回)。

    合并语义与今天的 merge_config_overlay 一致 (顶层 dict 浅合并一层),
    但 config.yaml 的 cache/logging 等段在 db 模式下**不**参与 —— 那些已入库,
    避免文件值盖住控制台对 DB 的修改。
    """
    return merge_config_overlay(config, extract_bootstrap(config_yaml_raw))
