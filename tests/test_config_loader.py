"""config_loader 单元测试"""
import os
from pathlib import Path

import pytest

from src.config_loader import (
    load_config,
    resolve_env_vars,
    validate_config,
)


class TestResolveEnvVars:
    def test_simple_substitution(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        out, missing = resolve_env_vars({"k": "${FOO}"})
        assert out == {"k": "bar"}
        assert missing == []

    def test_missing_var_strict_raises(self, monkeypatch):
        monkeypatch.delenv("NOPE_X", raising=False)
        with pytest.raises(KeyError, match="NOPE_X"):
            resolve_env_vars({"k": "${NOPE_X}"}, strict=True)

    def test_missing_var_nonstrict_keeps(self, monkeypatch):
        monkeypatch.delenv("NOPE_Y", raising=False)
        out, missing = resolve_env_vars({"k": "${NOPE_Y}"}, strict=False)
        assert out == {"k": "${NOPE_Y}"}
        assert "NOPE_Y" in missing

    def test_nested_dict(self, monkeypatch):
        monkeypatch.setenv("API", "sk-123")
        out, _ = resolve_env_vars({
            "upstreams": {
                "x": {"api_key": "${API}"},
                "y": {"env": {"TOK": "${API}"}}
            }
        })
        assert out["upstreams"]["x"]["api_key"] == "sk-123"
        assert out["upstreams"]["y"]["env"]["TOK"] == "sk-123"

    def test_non_string_passthrough(self):
        out, _ = resolve_env_vars({"a": 1, "b": [1, 2], "c": None})
        assert out == {"a": 1, "b": [1, 2], "c": None}

    def test_partial_in_string(self, monkeypatch):
        monkeypatch.setenv("HOST", "example.com")
        out, _ = resolve_env_vars({"url": "https://${HOST}/api"})
        assert out["url"] == "https://example.com/api"


class TestLoadConfig:
    def test_load_with_env_substitution(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_TOKEN", "abc123")
        cfg_file = tmp_path / "cfg.yaml"
        cfg_file.write_text(
            "upstreams:\n"
            "  test_one:\n"
            "    enabled: true\n"
            "    type: http\n"
            "    base_url: https://api.example.com\n"
            "    api_key: ${TEST_TOKEN}\n"
            "    timeout_seconds: 30\n",
            encoding="utf-8"
        )
        cfg = load_config(cfg_file, strict_env=True)
        assert cfg["upstreams"]["test_one"]["api_key"] == "abc123"

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nope.yaml")

    def test_load_missing_env_strict_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("REQUIRED_VAR", raising=False)
        cfg_file = tmp_path / "cfg.yaml"
        cfg_file.write_text("upstreams:\n  x:\n    api_key: ${REQUIRED_VAR}\n", encoding="utf-8")
        with pytest.raises(KeyError, match="REQUIRED_VAR"):
            load_config(cfg_file, strict_env=True)

    def test_load_missing_env_nonstrict_keeps_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPT_VAR", raising=False)
        cfg_file = tmp_path / "cfg.yaml"
        cfg_file.write_text("k: ${OPT_VAR}\n", encoding="utf-8")
        cfg = load_config(cfg_file, strict_env=False)
        assert cfg["k"] == "${OPT_VAR}"


class TestValidateConfig:
    def test_empty_upstreams(self):
        errors = validate_config({"upstreams": {}})
        assert any("No upstreams" in e for e in errors)

    def test_http_missing_url(self):
        errors = validate_config({
            "upstreams": {"x": {"enabled": True, "type": "http", "api_key": "k"}}
        })
        assert any("base_url" in e for e in errors)

    def test_http_missing_key(self):
        errors = validate_config({
            "upstreams": {"x": {"enabled": True, "type": "http", "base_url": "u"}}
        })
        assert any("api_key" in e for e in errors)

    def test_npx_missing_command(self):
        errors = validate_config({
            "upstreams": {"x": {"enabled": True, "type": "npx"}}
        })
        assert any("command" in e or "package" in e for e in errors)

    def test_disabled_upstream_skipped(self):
        errors = validate_config({
            "upstreams": {"x": {"enabled": False, "type": "http"}}
        })
        assert errors == []

    def test_unknown_type(self):
        errors = validate_config({
            "upstreams": {"x": {"enabled": True, "type": "weird"}}
        })
        assert any("unknown type" in e for e in errors)

    def test_valid_http(self):
        errors = validate_config({
            "upstreams": {"x": {
                "enabled": True, "type": "http",
                "base_url": "https://x", "api_key": "k"
            }}
        })
        assert errors == []
