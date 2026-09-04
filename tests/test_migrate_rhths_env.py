"""Tests for scripts/migrate_rhths_env.py."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_rhths_env.py"


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=cwd or ROOT,
    )


def _write_env(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_migrate_renames_var(tmp_path: Path):
    """含 RHTHS_API_KEY=v 的 .env 运行后变成 FUYAO_API_KEY=v, 且 .env.bak = 原内容."""
    env = tmp_path / ".env"
    _write_env(env, ["# header", "RHTHS_API_KEY=secret123", "OTHER=keep"])
    result = _run(["--path", str(env)])
    assert result.returncode == 0, result.stderr
    text = env.read_text(encoding="utf-8")
    assert "FUYAO_API_KEY=secret123" in text
    assert "RHTHS_API_KEY=" not in text
    assert "OTHER=keep" in text
    bak = tmp_path / ".env.bak"
    assert bak.exists()
    assert "RHTHS_API_KEY=secret123" in bak.read_text(encoding="utf-8")


def test_migrate_idempotent(tmp_path: Path):
    """第二次运行 exit 0 且 mtime 不变."""
    env = tmp_path / ".env"
    _write_env(env, ["FUYAO_API_KEY=already"])
    mtime_before = env.stat().st_mtime
    result = _run(["--path", str(env)])
    assert result.returncode == 0, result.stderr
    assert "already present" in result.stderr
    assert env.stat().st_mtime == mtime_before


def test_migrate_dry_run_no_write(tmp_path: Path):
    """--dry-run 模式下不写盘, 不生成 .env.bak."""
    env = tmp_path / ".env"
    _write_env(env, ["RHTHS_API_KEY=v"])
    mtime_before = env.stat().st_mtime
    result = _run(["--path", str(env), "--dry-run"])
    assert result.returncode == 0, result.stderr
    assert "would rename" in result.stdout
    assert env.stat().st_mtime == mtime_before
    assert not (tmp_path / ".env.bak").exists()


def test_migrate_both_vars_error(tmp_path: Path):
    """两 var 同存 → exit 1, 不写盘."""
    env = tmp_path / ".env"
    _write_env(env, ["RHTHS_API_KEY=old", "FUYAO_API_KEY=new"])
    result = _run(["--path", str(env)])
    assert result.returncode == 1, result.stderr
    assert "both vars present" in result.stderr
    assert env.read_text(encoding="utf-8").count("API_KEY=") == 2


def test_migrate_no_env_error(tmp_path: Path):
    """.env 不存在 → exit 2."""
    env = tmp_path / ".env"
    result = _run(["--path", str(env)])
    assert result.returncode == 2, result.stderr
    assert "not found" in result.stderr


def test_migrate_never_prints_value(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """脚本 stdout/stderr 永不打真实 key value."""
    secret = "x" * 64
    env = tmp_path / ".env"
    _write_env(env, [f"RHTHS_API_KEY={secret}"])
    _run(["--path", str(env)])
    out = capsys.readouterr()
    assert secret not in out.out
    assert secret not in out.err


def test_migrate_already_new_skips(tmp_path: Path):
    """仅 FUYAO_API_KEY → exit 0, stderr 含 'already present', 不写盘."""
    env = tmp_path / ".env"
    _write_env(env, ["FUYAO_API_KEY=here"])
    mtime_before = env.stat().st_mtime
    result = _run(["--path", str(env)])
    assert result.returncode == 0
    assert "already present" in result.stderr
    assert env.stat().st_mtime == mtime_before
