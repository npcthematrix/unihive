"""v2 配置全量入库: YAML 模板 → 实体 → 装配的双向往返 golden 等价性。

这是 P2 的硬关卡: 实体形态不能丢失/改变任何运行时配置 ——
装配结果 (合回 bootstrap 后) 必须与现行加载链产出的合并 dict 逐字段相等,
工具 spec 全字段相等 (实测 292 个, 含 requires_env 门控)。
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from unihive.core.db_assembler import (
    assemble_config,
    entities_from_json,
    extract_bootstrap,
    merge_bootstrap,
)
from unihive.core.template_normalize import (
    BOOTSTRAP_GATEWAY_KEYS,
    find_plaintext_secrets,
    load_raw_config,
    load_raw_template,
    normalize_template,
    template_version,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "upstreams.yaml"


@pytest.fixture(scope="module")
def bundle():
    merged, specs, provenance, path = load_raw_template(CONFIG_PATH)
    template = normalize_template(merged, specs, provenance)
    return merged, specs, provenance, path, template


def test_real_config_loads_292_tools(bundle):
    merged, specs, _provenance, _path, template = bundle
    assert len(specs) == 292
    assert len(template.tools) == 292
    # 9 个上游块 (4 个 fuyao 端点各自独立 + mootdx2/tdx_quant/omni/thsdk/THS-LOCAL)
    assert len(template.upstreams) == 9
    # 内联在 upstreams.yaml 的只有 53 个 fuyao/fqgate spec, 其余 240 来自 5 个 tools_*.yaml
    assert len(merged["tools"]) == 53
    by_source: dict[str, int] = {}
    for tool in template.tools:
        by_source[tool.source_file] = by_source.get(tool.source_file, 0) + 1
    assert by_source["upstreams.yaml"] == 53
    assert sum(by_source.values()) == 292


def test_sections_exclude_entity_and_bootstrap_keys(bundle):
    _merged, _specs, _p, _path, template = bundle
    assert "upstreams" not in template.sections
    assert "tools" not in template.sections
    assert "upstream_tool_mapping" not in template.sections
    assert "console" not in template.sections
    # gateway 调参保留, 监听 bootstrap 键剥离
    assert "per_upstream_timeout" in template.sections.get("gateway", {})
    assert BOOTSTRAP_GATEWAY_KEYS.isdisjoint(template.sections.get("gateway", {}))
    # 其余杂项段在
    assert "cache" in template.sections
    assert template.sections["routing"] == {}


def test_upstream_entities_strip_only_type_and_enabled(bundle):
    merged, _specs, _p, _path, template = bundle
    for entity in template.upstreams:
        raw_block = merged["upstreams"][entity.name]
        assert entity.type == raw_block["type"]
        assert entity.enabled == bool(raw_block.get("enabled", False))
        # config_json 与去掉两个标量列后的原块逐字段相等
        assert entity.config == {
            k: v for k, v in raw_block.items() if k not in ("type", "enabled")
        }


def test_placeholders_not_resolved(bundle):
    """seed 输入必须是未解析 env 的原始值 (api_key 仍是 ${VAR})。"""
    _merged, _specs, _p, _path, template = bundle
    fuyao = next(u for u in template.upstreams if u.name == "fuyao_ashare")
    assert fuyao.config["api_key"] == "${FUYAO_API_KEY}"


def test_tool_entities_extract_columns_and_keep_full_spec(bundle):
    _merged, specs, _provenance, _path, template = bundle
    by_name = {t.name: t for t in template.tools}
    for spec in specs:
        entity = by_name[spec["name"]]
        # 完整 spec 零损耗
        assert entity.spec == spec
        assert entity.routing == spec.get("routing", spec["name"])
        assert entity.description == (spec.get("description") or "")
        assert entity.cache_ttl_key == spec.get("cache_ttl_key")
        assert entity.requires_env == spec.get("requires_env")
        assert entity.origin == "builtin"
        assert entity.publish_mode == "default"
        assert entity.source_file.endswith(".yaml")


def test_golden_assemble_plus_bootstrap_equals_merged_raw(bundle):
    merged, specs, _p, path, template = bundle
    assembled = assemble_config(template)
    config_yaml_raw = load_raw_config(path.parent / "config.yaml")
    merge_bootstrap(assembled, config_yaml_raw)
    # tools 语义差异 (v2 故意为之): 现行加载链 config['tools'] 只放内联 53 条,
    # 全量靠运行时再读 5 个 tools_*.yaml; db 模式装配结果直接持全量 292,
    # 运行时不再回读 YAML。其余顶层段必须逐字段相等。
    assert assembled["tools"] == specs
    assert {k: v for k, v in assembled.items() if k != "tools"} == {
        k: v for k, v in merged.items() if k != "tools"
    }


def test_golden_assembled_tool_specs_equal_loaded_specs(bundle):
    merged, specs, _p, path, template = bundle
    assembled = assemble_config(template)
    assert assembled["tools"] == specs
    assert [t["name"] for t in assembled["tools"]] == [s["name"] for s in specs]


def test_golden_upstream_mapping_rebuilt(bundle):
    merged, _specs, _p, _path, template = bundle
    assembled = assemble_config(template)
    assert assembled["upstream_tool_mapping"] == merged["upstream_tool_mapping"]


def test_json_roundtrip_preserves_entities_and_version(bundle):
    _merged, _specs, _p, _path, template = bundle
    payload = template.to_json()
    restored = entities_from_json(payload)
    assert assemble_config(restored) == assemble_config(template)
    assert template_version(restored) == template_version(template)
    # 版本稳定 (二次序列化)
    assert template_version(normalize_template(*_re_normalize(bundle))) == template_version(template)


def _re_normalize(bundle):
    """用装配结果 (不含 bootstrap 键) 再归一化一次, 来源表保持不变, 版本稳定。"""
    _merged, specs, provenance, _path, template = bundle
    assembled = assemble_config(template)
    return assembled, specs, provenance


def test_bootstrap_extract_only_console_and_listen_keys():
    raw = {
        "console": {"username": "admin", "password_hash": "x", "session_secret": "y"},
        "gateway": {"host": "0.0.0.0", "port": 18080, "mcp_path": "/mcp", "per_upstream_timeout": 10},
        "cache": {"ttl": {"historical": 1}},
        "logging": {"level": "INFO"},
    }
    bootstrap = extract_bootstrap(raw)
    assert set(bootstrap.keys()) == {"console", "gateway"}
    assert set(bootstrap["gateway"].keys()) == BOOTSTRAP_GATEWAY_KEYS
    assert "cache" not in bootstrap and "logging" not in bootstrap


def test_normalize_rejects_duplicate_tool_name():
    config = {"upstreams": {"a": {"type": "omni", "enabled": True}}, "upstream_tool_mapping": {}}
    specs = [
        {"name": "t1", "routing": "t1", "description": "x"},
        {"name": "t1", "routing": "t1", "description": "y"},
    ]
    with pytest.raises(ValueError, match="Duplicate tool name"):
        normalize_template(config, specs)


def test_normalize_rejects_upstream_without_type():
    config = {"upstreams": {"bad": {"enabled": True}}}
    with pytest.raises(ValueError, match="missing 'type'"):
        normalize_template(config, [])


def test_assemble_does_not_mutate_entities(bundle):
    _m, _s, _p, _path, template = bundle
    snapshot = copy.deepcopy(template.to_json())
    assembled = assemble_config(template)
    assembled["upstreams"]["fuyao_ashare"]["enabled"] = not assembled["upstreams"]["fuyao_ashare"]["enabled"]
    assembled["tools"][0]["description"] = "MUTATED"
    assert template.to_json() == snapshot


def test_plaintext_secret_scan_detects_and_allows_placeholders():
    merged, specs, provenance, _path = load_raw_template(CONFIG_PATH)
    template = normalize_template(merged, specs, provenance)
    # 真实模板: 密钥全部是占位符
    assert find_plaintext_secrets(template) == []

    # 植入明文 api_key → 必须被抓
    planted = normalize_template(
        {**merged, "upstreams": {**merged["upstreams"], "leak": {"type": "http", "enabled": True, "api_key": "sk-literal-123"}}},
        specs,
        provenance,
    )
    found = find_plaintext_secrets(planted)
    assert any("leak" in path and path.endswith("api_key") for path in found)

    # 占位符形式合法
    ok = normalize_template(
        {**merged, "upstreams": {**merged["upstreams"], "leak": {"type": "http", "enabled": True, "api_key": "${SOME_KEY}"}}},
        specs,
        provenance,
    )
    assert not [p for p in find_plaintext_secrets(ok) if "leak" in p]
