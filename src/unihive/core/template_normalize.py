"""YAML 出厂模板 → DB 实体的归一化 (v2 配置全量入库, 纯函数, 不做 IO)。

设计见 docs/design/2026-09-18-sqlite-config-full-database.md。

实体形态 (实体行 + JSON 列混合):

- ``sections``: 非实体杂项顶层段 (cache / gateway 调参 / logging / routing /
  fuyao_cache / strict_registry ...), 段名 → 完整 dict/标量。
- ``upstreams``: 每个上游一行, 异型连接块原样收进 ``config``。
- ``tools``: 每个 gateway 工具一行; 为保证往返零损耗, 完整 spec 存 ``spec``,
  另抽 description / cache_ttl_key / requires_env / mapping 供检索与控制台渲染。

bootstrap 内容永不入库: ``console`` 整段 (账号/密码哈希/session_secret),
以及 gateway 的 host/port/mcp_path —— 这些只留在 config.yaml, 装配后由
:func:`unihive.core.db_assembler.merge_bootstrap` 合回。

归一化输入必须是 **未解析 ${VAR}** 的原始 dict (见 :func:`load_raw_config`),
否则明文密钥会被写进 DB。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .tool_loader import load_all_tools

#: 不进实体表的顶层键 (upstreams/tools 是实体; mapping 由工具行重建;
#: console 是 bootstrap, 永不入库)。
ENTITY_TOP_KEYS = {"upstreams", "tools", "upstream_tool_mapping", "console"}

#: gateway 段中只属于 bootstrap YAML 的键 (监听地址/路径锁在库外)。
BOOTSTRAP_GATEWAY_KEYS = {"host", "port", "mcp_path"}

#: 疑似密钥字段 (api_key / password / secret / token 结尾)。
_SECRET_KEY_RE = re.compile(r"(?:^|_)(api_key|password|passwd|secret|token)$", re.IGNORECASE)

#: 合法 ${VAR} 占位符。
_ENV_PLACEHOLDER_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")

BUILTIN_ORIGIN = "builtin"
DEFAULT_PUBLISH_MODE = "default"


@dataclass(frozen=True)
class UpstreamEntity:
    """上游实例行。config 为去掉 enabled/type 的完整异型连接块。"""

    name: str
    type: str
    enabled: bool
    config: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "enabled": self.enabled,
            "config": copy.deepcopy(self.config),
        }


@dataclass(frozen=True)
class ToolEntity:
    """工具行。spec 是完整原始 spec (JSON 往返零损耗), 其余字段为抽取列。"""

    name: str
    routing: str
    description: str
    cache_ttl_key: str | None
    requires_env: str | None
    publish_mode: str
    mapping: dict[str, str]
    spec: dict[str, Any]
    source_file: str
    origin: str = BUILTIN_ORIGIN

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "routing": self.routing,
            "description": self.description,
            "cache_ttl_key": self.cache_ttl_key,
            "requires_env": self.requires_env,
            "publish_mode": self.publish_mode,
            "mapping": copy.deepcopy(self.mapping),
            "spec": copy.deepcopy(self.spec),
            "source_file": self.source_file,
            "origin": self.origin,
        }


@dataclass(frozen=True)
class NormalizedTemplate:
    """归一化后的完整出厂模板。"""

    sections: dict[str, Any]
    upstreams: list[UpstreamEntity]
    tools: list[ToolEntity]

    def to_json(self) -> dict[str, Any]:
        return {
            "sections": copy.deepcopy(self.sections),
            "upstreams": [u.to_json() for u in self.upstreams],
            "tools": [t.to_json() for t in self.tools],
        }


def load_raw_config(path: str | Path) -> dict[str, Any]:
    """只做 yaml.safe_load, **不解析** ${VAR}、不读 .env。

    入库 seed 必须走这里 (而非 config_loader.load_config) —— 后者会用环境
    变量替换占位符, 导致明文密钥落库。文件缺失/为空返回 {}。
    """
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return raw or {}


def load_raw_template(
    config_path: str | Path = "config/upstreams.yaml",
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str], Path]:
    """加载出厂模板 bundle (未解析 env): 合并后的原始 dict + 全量 spec + 来源表。

    与运行时加载链保持同样的文件/合并语义:
    upstreams.yaml 为底, 同目录 config.yaml 浅合并覆盖
    (复用 merge_config_overlay), 再经 load_all_tools 合入 5 个 tools_*.yaml
    并回填顶层 upstream_tool_mapping。

    Returns:
        (merged_raw, specs_include_env_gated, provenance, resolved_config_path)
    """
    from ..utils.config_loader import merge_config_overlay

    path = Path(config_path)
    merged = load_raw_config(path)
    config_yaml = path.parent / "config.yaml"
    if config_yaml.exists():
        merge_config_overlay(merged, load_raw_config(config_yaml))

    provenance: dict[str, str] = {}
    specs = load_all_tools(
        path,
        merged,
        include_env_gated=True,
        provenance=provenance,
    )
    return merged, specs, provenance, path


def normalize_template(
    config: dict[str, Any],
    specs: list[dict[str, Any]],
    provenance: dict[str, str] | None = None,
) -> NormalizedTemplate:
    """把合并后的原始模板 dict + spec 列表归一化为实体 bundle (纯函数)。

    输入不会被修改; 输出全部为深拷贝。重复工具名直接 ValueError
    (实体表以名为 PK, 重复即模板错误, 应由 lint 拦截在前)。
    """
    provenance = provenance or {}
    sections = _extract_sections(config)
    upstreams = _extract_upstreams(config)

    top_mapping = config.get("upstream_tool_mapping") or {}
    tools: list[ToolEntity] = []
    seen: set[str] = set()
    for spec in specs:
        name = spec.get("name")
        if not name:
            raise ValueError("Tool spec missing 'name'")
        if name in seen:
            raise ValueError(f"Duplicate tool name in template: {name!r}")
        seen.add(name)
        routing_key = spec.get("routing") or name
        mapping = dict(top_mapping.get(routing_key) or {})
        tools.append(
            ToolEntity(
                name=name,
                routing=routing_key,
                description=spec.get("description", "") or "",
                cache_ttl_key=spec.get("cache_ttl_key"),
                requires_env=spec.get("requires_env"),
                publish_mode=DEFAULT_PUBLISH_MODE,
                mapping=mapping,
                spec=copy.deepcopy(spec),
                source_file=provenance.get(name, "upstreams.yaml"),
            )
        )
    return NormalizedTemplate(sections=sections, upstreams=upstreams, tools=tools)


def _extract_sections(config: dict[str, Any]) -> dict[str, Any]:
    sections: dict[str, Any] = {}
    for key, value in config.items():
        if key in ENTITY_TOP_KEYS:
            continue
        if key == "gateway":
            # gateway 混合段: 仅剥离 bootstrap 监听键, 调参部分 (per_upstream_timeout /
            # rate_limit ...) 入库; 剥空则该段不入库。
            if isinstance(value, dict):
                rest = {k: v for k, v in value.items() if k not in BOOTSTRAP_GATEWAY_KEYS}
                if rest:
                    sections[key] = copy.deepcopy(rest)
                continue
        sections[key] = copy.deepcopy(value)
    return sections


def _extract_upstreams(config: dict[str, Any]) -> list[UpstreamEntity]:
    upstreams_raw = config.get("upstreams") or {}
    entities: list[UpstreamEntity] = []
    for name, block in upstreams_raw.items():
        if not isinstance(block, dict):
            raise ValueError(f"upstream {name!r} block must be a mapping")
        if "type" not in block:
            raise ValueError(f"upstream {name!r} missing 'type'")
        entities.append(
            UpstreamEntity(
                name=name,
                type=block["type"],
                enabled=bool(block.get("enabled", False)),
                config={k: copy.deepcopy(v) for k, v in block.items() if k not in ("enabled", "type")},
            )
        )
    return entities


def canonical_payload(template: NormalizedTemplate) -> bytes:
    """模板实体的规范 JSON 字节 (sort_keys, 无多余空白), 用于版本 hash/快照。"""
    return json.dumps(
        template.to_json(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def template_version(template: NormalizedTemplate) -> str:
    """出厂模板内容版本: 规范实体 JSON 的 sha256 hex。"""
    return hashlib.sha256(canonical_payload(template)).hexdigest()


def find_plaintext_secrets_in(node: Any, path_prefix: str = "") -> list[str]:
    """扫描任意 JSON 形态节点, 返回疑似明文密钥字段的点路径。

    值形如 ``${VAR}`` 的占位符合法; 疑似密钥键名的非占位字符串视为泄露。
    控制台写上游连接块前与 seed 全量扫描共用此函数。
    """
    found: list[str] = []

    def walk(sub: Any, path: str) -> None:
        if isinstance(sub, dict):
            for key, value in sub.items():
                child = f"{path}.{key}" if path else key
                if isinstance(value, str) and _SECRET_KEY_RE.search(str(key)):
                    if not _ENV_PLACEHOLDER_RE.match(value):
                        found.append(child)
                walk(value, child)
        elif isinstance(sub, list):
            for index, value in enumerate(sub):
                walk(value, f"{path}[{index}]")

    walk(node, path_prefix)
    return found


def find_plaintext_secrets(template: NormalizedTemplate) -> list[str]:
    """扫描入库实体, 返回疑似明文密钥字段的点路径 (seed 拒绝明文密钥)。

    值形如 ``${VAR}`` 的占位符合法; 疑似密钥键名的非占位字符串视为泄露。
    console 段已在归一化时剥离, 不会出现在这里。
    """
    bundle = template.to_json()
    found: list[str] = []
    found.extend(find_plaintext_secrets_in(bundle.get("sections", {}), "sections"))
    for upstream in bundle.get("upstreams", []):
        found.extend(
            find_plaintext_secrets_in(
                upstream.get("config", {}), f"upstreams.{upstream['name']}"
            )
        )
    for tool in bundle.get("tools", []):
        found.extend(
            find_plaintext_secrets_in(
                tool.get("spec", {}), f"tools.{tool['name']}.spec"
            )
        )
    return found
