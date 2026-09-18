"""`python -m unihive config lint` 端到端测试 (临时配置目录)."""
from __future__ import annotations

import pytest

from unihive.cli import main

_CLEAN = """
upstreams:
  fuyao_meta:
    enabled: true
    type: http
    base_url: https://example.com/mcp
    api_key: test-key
upstream_tool_mapping:
  foo:
    fuyao_meta: foo
tools:
  - name: foo
    routing: foo
    description: test tool
    params:
      - {name: code, type: str, required: true}
"""


def _write_config(tmp_path, content: str) -> str:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    path = cfg_dir / "upstreams.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_lint_clean_config_exits_zero(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)  # 隔绝 load_all_tools 的相对 config/ 回落
    path = _write_config(tmp_path, _CLEAN)
    assert main(["config", "lint", "--config", path]) == 0
    out = capsys.readouterr().out
    assert "OK: no issues found" in out
    assert "1 tool specs" in out


def test_lint_broken_mapping_exits_one(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    broken = _CLEAN.replace("fuyao_meta: foo", "ghost_upstream: foo")
    path = _write_config(tmp_path, broken)
    assert main(["config", "lint", "--config", path]) == 1
    out = capsys.readouterr().out
    assert "M001" in out and "ghost_upstream" in out


def test_lint_warning_defaults_to_zero(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    disabled = _CLEAN.replace("enabled: true", "enabled: false")
    path = _write_config(tmp_path, disabled)
    assert main(["config", "lint", "--config", path]) == 0
    assert "M004" in capsys.readouterr().out


def test_lint_warnings_as_errors_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    disabled = _CLEAN.replace("enabled: true", "enabled: false")
    path = _write_config(tmp_path, disabled)
    assert main(
        ["config", "lint", "--config", path, "--warnings-as-errors"]
    ) == 1


def test_lint_missing_config_exits_two(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(
        ["config", "lint", "--config", str(tmp_path / "nope.yaml")]
    ) == 2


def test_lint_merges_sibling_config_yaml(tmp_path, monkeypatch) -> None:
    """config.yaml 与 upstreams.yaml 同目录时按覆盖合并后再校验。"""
    monkeypatch.chdir(tmp_path)
    path = _write_config(tmp_path, _CLEAN)
    (tmp_path / "config" / "config.yaml").write_text(
        """
upstreams:
  fuyao_meta:
    enabled: false
""",
        encoding="utf-8",
    )
    # 默认 warning 不致命
    assert main(["config", "lint", "--config", path]) == 0
    # 覆盖生效: 上游被禁用 → M004
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(["config", "lint", "--config", path])
    assert rc == 0
    assert "M004" in buf.getvalue()
