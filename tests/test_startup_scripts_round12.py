"""Round-12 启动壳测试: start_gateway.ps1 / _load_env.ps1。

行为测试用真实 powershell.exe + 临时 PATH 上的 fake python.cmd,
验证:
- H1 stdio 模式宿主 stdout 必须绝对干净 (MCP JSON-RPC 通道);
- MED 退出码必须透传;
- MED 脚本锚定 CWD 到仓库根;
- LOW _load_env UTF-8 / 行内注释 / 成对引号 / export 前缀。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
START_PS1 = SCRIPTS_DIR / "start_gateway.ps1"
LOAD_ENV_PS1 = SCRIPTS_DIR / "_load_env.ps1"

POWERSHELL = shutil.which("powershell") or shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(
    not POWERSHELL or sys.platform != "win32",
    reason="需要 Windows PowerShell",
)

UTF8_BOM = b"\xef\xbb\xbf"


def _run_ps(
    script: str,
    cwd: Path,
    env: dict | None = None,
    *,
    file: Path | None = None,
    file_args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    """执行 PowerShell。默认 -Command; 给 file 则走 -File (MCP 宿主真实调用形式,
    退出码经 exit $LASTEXITCODE 原样透传)。"""
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    args = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass"]
    if file is not None:
        args += ["-File", str(file), *(file_args or [])]
    else:
        args += ["-Command", script]
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=merged_env,
        capture_output=True,
        timeout=60,
    )


def _install_fake_python(bin_dir: Path, log_file: Path) -> Path:
    """放一个拦截 python 调用的 python.cmd, 记录 argv/CWD, 退出码/输出受 env 控制。"""
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "python.cmd"
    fake.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        f'echo ARGV=%* >> "{log_file}"\r\n'
        f'echo CWD=%CD% >> "{log_file}"\r\n'
        'if defined FAKE_PY_STDOUT echo %FAKE_PY_STDOUT%\r\n'
        'if not defined FAKE_PY_EXITCODE set FAKE_PY_EXITCODE=0\r\n'
        "exit /b %FAKE_PY_EXITCODE%\r\n",
        encoding="ascii",
    )
    return fake


# ---------------------------------------------------------------------------
# 编码 (LOW): PS 5.1 对无 BOM ps1 按系统 ANSI 解码, 含中文注释必须带 BOM
# ---------------------------------------------------------------------------


def test_ps1_files_have_utf8_bom():
    assert START_PS1.read_bytes()[:3] == UTF8_BOM
    assert LOAD_ENV_PS1.read_bytes()[:3] == UTF8_BOM


# ---------------------------------------------------------------------------
# _load_env.ps1 解析
# ---------------------------------------------------------------------------


def test_load_env_parsing(tmp_path):
    env_file = tmp_path / ".env"
    # UTF-8 无 BOM, 含中文值 — 旧实现 Get-Content 无 -Encoding UTF8,
    # PS 5.1 按 GBK 解码会乱码
    env_file.write_text(
        "# comment line\n"
        "PLAIN=val # 行内注释必须剥离\n"
        'QUOTED="quoted value"\n'
        "SINGLE='single value'\n"
        "HASHKEEP=a#b\n"
        "export EXPORTED=exp\n"
        "PAIRQUOTE='\"nested\"'\n"
        "UNICODE=行情密码\n",
        encoding="utf-8",
    )
    ps = (
        f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        f". '{LOAD_ENV_PS1}' -EnvPath '{env_file}'; "
        'Write-Output ("PLAIN=" + $env:PLAIN); '
        'Write-Output ("QUOTED=" + $env:QUOTED); '
        'Write-Output ("SINGLE=" + $env:SINGLE); '
        'Write-Output ("HASHKEEP=" + $env:HASHKEEP); '
        'Write-Output ("EXPORTED=" + $env:EXPORTED); '
        'Write-Output ("PAIRQUOTE=" + $env:PAIRQUOTE); '
        'Write-Output ("UNICODE=" + $env:UNICODE)'
    )
    proc = _run_ps(ps, tmp_path)
    assert proc.returncode == 0, proc.stderr.decode("gbk", "replace")
    out = proc.stdout.decode("utf-8", "replace")
    assert "PLAIN=val" in out
    assert "行内注释" not in out
    assert 'QUOTED=quoted value' in out
    assert "SINGLE=single value" in out
    assert "HASHKEEP=a#b" in out
    assert "EXPORTED=exp" in out
    assert 'PAIRQUOTE="nested"' in out
    assert "UNICODE=行情密码" in out


def test_load_env_missing_warns_on_stderr_not_stdout(tmp_path):
    """stdio 模式 .env 缺失的警告不能走 stdout (Write-Host 污染 MCP 通道)。"""
    missing = tmp_path / "nope.env"
    ps = f". '{LOAD_ENV_PS1}' -EnvPath '{missing}'"
    proc = _run_ps(ps, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == b"", "stdout 必须干净"
    assert b"not found" in proc.stderr or b"warn" in proc.stderr.lower()


# ---------------------------------------------------------------------------
# start_gateway.ps1
# ---------------------------------------------------------------------------


def _start_gateway(transport: str, tmp_path: Path, extra_env: dict | None = None):
    bin_dir = tmp_path / "fakebin"
    log_file = tmp_path / "calls.log"
    _install_fake_python(bin_dir, log_file)
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    if extra_env:
        env.update(extra_env)
    proc = _run_ps(
        "",
        tmp_path,  # 故意从仓库外的目录启动
        env=env,
        file=START_PS1,
        file_args=["-Transport", transport],
    )
    calls = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
    return proc, calls


def test_stdio_stdout_is_clean(tmp_path):
    """H1: stdio 模式 stdout 不能出现 banner 或任何宿主输出。"""
    proc, _ = _start_gateway("stdio", tmp_path)
    assert proc.stdout == b"", (
        f"stdio stdout 被污染: {proc.stdout[:200]!r}"
    )


def test_stdio_invokes_gateway_from_repo_root(tmp_path):
    """MED: 模块路径 src.unihive... 依赖 CWD=仓库根, 脚本必须 Set-Location。"""
    _, calls = _start_gateway("stdio", tmp_path)
    assert "src.unihive.gateway_server --transport stdio" in calls
    # fake python 记录的 CWD 必须是仓库根 (规范化后比较)
    assert f"CWD={REPO_ROOT}" in calls


def test_stdio_skips_port_probe(tmp_path):
    """H1 附带: stdio 模式不应多 spawn 一个 python 做端口探测。"""
    _, calls = _start_gateway("stdio", tmp_path)
    # 只有真正的网关一次调用 (记录两行 ARGV/CWD); 探测会是额外一次
    assert calls.count("ARGV=") == 1


def test_exit_code_propagated_stdio(tmp_path):
    """MED: 子进程非零退出码必须经 ps1 透传。"""
    proc, _ = _start_gateway("stdio", tmp_path, {"FAKE_PY_EXITCODE": "7"})
    assert proc.returncode == 7


def test_http_mode_shows_banner_and_port(tmp_path):
    proc, calls = _start_gateway("http", tmp_path)
    out = proc.stdout.decode("utf-8", "replace")
    assert "UniHive MCP Gateway" in out
    assert "18080" in calls  # fake python 让探测失败回落 18080
    assert "--transport http" in calls


def test_http_exit_code_propagated(tmp_path):
    proc, _ = _start_gateway("http", tmp_path, {"FAKE_PY_EXITCODE": "2"})
    assert proc.returncode == 2
