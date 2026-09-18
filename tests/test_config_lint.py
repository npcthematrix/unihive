"""config lint 纯规则测试 (P0 of YAML+SQLite overlay 设计).

lint_config 不做 IO, 只对合并后的 config dict + spec 列表做静态一致性校验。
"""
from __future__ import annotations

import pytest

from unihive.core.config_lint import LintIssue, lint_config


def _upstream(name: str = "fuyao_meta", *, enabled: bool = True, type_: str = "http") -> dict:
    cfg = {"enabled": enabled, "type": type_}
    if type_ == "http":
        cfg.update(base_url="https://example.com/mcp", api_key="k")
    return cfg


def _spec(
    name: str = "foo",
    *,
    routing: str | None = "foo",
    backer: str = "fuyao_meta",
    upstream_tool: str | None = "foo",
    params: list[dict] | None = None,
    ttl_key: str | None = None,
) -> dict:
    spec: dict = {"name": name, "description": "x"}
    if routing is not None:
        spec["routing"] = routing
    if backer is not None:
        spec["upstream_tool_mapping"] = {backer: upstream_tool or name}
    if params is not None:
        spec["params"] = params
    if ttl_key is not None:
        spec["cache_ttl_key"] = ttl_key
    return spec


def _clean_config(**over) -> dict:
    config: dict = {
        "upstreams": {"fuyao_meta": _upstream()},
        "routing": {},
        "upstream_tool_mapping": {"foo": {"fuyao_meta": "foo"}},
        "cache": {"ttl": {"historical": 86400}},
    }
    config.update(over)
    return config


def _codes(issues: list[LintIssue]) -> list[str]:
    return [i.code for i in issues]


def test_clean_config_has_no_issues() -> None:
    config = _clean_config()
    specs = [_spec()]
    assert lint_config(config, specs) == []


# --- 结构校验 (validate_config 透传) ---

def test_enabled_http_upstream_missing_api_key_is_error() -> None:
    config = _clean_config(
        upstreams={"bad": {"enabled": True, "type": "http", "base_url": "u"}},
        upstream_tool_mapping={},
    )
    issues = lint_config(config, [])
    c001 = [i for i in issues if i.code == "C001"]
    assert c001 and all(i.severity == "error" for i in c001)
    assert any("api_key" in i.message for i in c001)


def test_disabled_upstream_not_validated_for_connection_fields() -> None:
    config = _clean_config(
        upstreams={"bad": {"enabled": False, "type": "http"}},
        upstream_tool_mapping={},
    )
    assert lint_config(config, []) == []


# --- spec 路由校验 (validate_specs 透传) ---

def test_spec_missing_routing_is_error() -> None:
    config = _clean_config(upstream_tool_mapping={})
    issues = lint_config(config, [_spec(routing=None, backer=None)])
    assert [c for c in _codes(issues) if c == "T001"]


def test_spec_routing_unknown_everywhere_is_error() -> None:
    config = _clean_config(upstream_tool_mapping={})
    issues = lint_config(config, [_spec(routing="ghost", backer=None)])
    assert [c for c in _codes(issues) if c == "T001"]


# --- 工具名 / 参数 ---

def test_duplicate_tool_names_is_error() -> None:
    config = _clean_config(
        upstream_tool_mapping={"foo": {"fuyao_meta": "foo"}},
    )
    specs = [_spec(), _spec()]
    issues = lint_config(config, specs)
    dup = [i for i in issues if i.code == "T002"]
    assert len(dup) == 1 and dup[0].severity == "error"
    assert "'foo'" in dup[0].message


def test_duplicate_param_names_is_error() -> None:
    config = _clean_config()
    specs = [
        _spec(params=[
            {"name": "code", "type": "str", "required": True},
            {"name": "code", "type": "str", "required": False},
        ])
    ]
    issues = lint_config(config, specs)
    dup = [i for i in issues if i.code == "T003"]
    assert len(dup) == 1 and "'code'" in dup[0].message


def test_unknown_param_type_is_warning() -> None:
    config = _clean_config()
    specs = [_spec(params=[{"name": "x", "type": "DateTime", "required": True}])]
    issues = lint_config(config, specs)
    w = [i for i in issues if i.code == "W001"]
    assert len(w) == 1 and w[0].severity == "warning"
    assert "DateTime" in w[0].message


def test_known_param_types_pass() -> None:
    config = _clean_config()
    specs = [
        _spec(params=[
            {"name": f"p_{i}", "type": t, "required": True}
            for i, t in enumerate(
                ("str", "int", "float", "bool", "List[str]", "array")
            )
        ])
    ]
    assert lint_config(config, specs) == []


# --- 映射引用完整性 ---

def test_mapping_references_unknown_upstream_is_error() -> None:
    config = _clean_config(
        upstream_tool_mapping={"foo": {"ghost_upstream": "foo"}},
    )
    issues = lint_config(config, [_spec(backer=None)])
    m = [i for i in issues if i.code == "M001"]
    assert len(m) == 1 and m[0].severity == "error"
    assert "ghost_upstream" in m[0].message


def test_routing_chain_references_unknown_upstream_is_error() -> None:
    config = _clean_config(
        routing={"foo": {"chain": ["fuyao_meta", "ghost"]}},
        upstream_tool_mapping={},
    )
    specs = [_spec(backer=None)]
    issues = lint_config(config, specs)
    m = [i for i in issues if i.code == "M002"]
    assert len(m) == 1 and "ghost" in m[0].message


def test_routing_block_without_chain_and_no_mapping_is_warning() -> None:
    # routing.X 块只有 description: validate_specs 放行, 但运行时无 backer,
    # 属于"未决路由"标记, lint 必须显式告警。
    config = _clean_config(
        routing={"foo": {"description": "TODO"}},
        upstream_tool_mapping={},
    )
    specs = [_spec(backer=None)]
    issues = lint_config(config, specs)
    m = [i for i in issues if i.code == "M003"]
    assert len(m) == 1 and m[0].severity == "warning"


def test_all_backers_disabled_is_warning() -> None:
    config = _clean_config(
        upstreams={"fuyao_meta": _upstream(enabled=False)},
    )
    issues = lint_config(config, [_spec()])
    m = [i for i in issues if i.code == "M004"]
    assert len(m) == 1 and m[0].severity == "warning"


def test_one_backer_enabled_keeps_tool_quiet() -> None:
    config = {
        "upstreams": {
            "fuyao_meta": _upstream(enabled=True),
            "tdx_quant": {"enabled": False, "type": "tdx_quant", "tdx_root": "D:/x"},
        },
        "routing": {},
        "upstream_tool_mapping": {"foo": {"tdx_quant": "foo", "fuyao_meta": "foo"}},
    }
    assert lint_config(config, [_spec(backer=None)]) == []


def test_orphan_mapping_entry_is_warning() -> None:
    config = _clean_config(
        upstream_tool_mapping={
            "foo": {"fuyao_meta": "foo"},
            "nobody_uses_me": {"fuyao_meta": "x"},
        },
    )
    issues = lint_config(config, [_spec()])
    m = [i for i in issues if i.code == "M005"]
    assert len(m) == 1 and "nobody_uses_me" in m[0].message


def test_orphan_routing_chain_is_warning() -> None:
    config = _clean_config(
        routing={
            "foo": {"chain": ["fuyao_meta"]},
            "unused_chain": {"chain": ["fuyao_meta"]},
        },
        upstream_tool_mapping={},
    )
    specs = [_spec(backer=None)]
    issues = lint_config(config, specs)
    m = [i for i in issues if i.code == "M006"]
    assert len(m) == 1 and "unused_chain" in m[0].message


def test_issues_are_deterministic() -> None:
    config = _clean_config(
        upstreams={},
        routing={},
        upstream_tool_mapping={"a": {"nope": "a"}, "b": {"nope": "b"}},
    )
    specs = [_spec("a", routing="a", backer=None), _spec("b", routing="b", backer=None)]
    first = lint_config(config, specs)
    second = lint_config(config, specs)
    assert [(i.code, i.message) for i in first] == [(i.code, i.message) for i in second]


def test_error_severity_values() -> None:
    config = _clean_config(
        upstreams={"fuyao_meta": _upstream(enabled=False)},
        upstream_tool_mapping={"foo": {"ghost": "foo"}},
    )
    issues = lint_config(config, [_spec(backer=None)])
    severities = {i.code: i.severity for i in issues}
    assert severities["M001"] == "error"
    assert severities["M004"] == "warning"
