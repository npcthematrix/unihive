# UniHive MCP Gateway - PowerShell Startup Script
#
# Usage:
#   .\scripts\start_gateway.ps1                  # HTTP mode (default), port from config/config.yaml
#   .\scripts\start_gateway.ps1 -Transport stdio # STDIO mode (Claude Desktop / Cursor)
#   .\scripts\start_gateway.ps1 -Transport http  # HTTP mode explicit
#
# HIGH-1 (2026-09-14 audit): 之前的版本在 ps1 里用 regex 抓 YAML 首个
# `port:` 数字, 抓到的可能是 cache.port / fuyao_*.port 而非 gateway.port。
# 现在改用 python 调用 yaml.safe_load 直接读 gateway.port, 失败再 fallback
# 到默认值 18080。
#
# M11 (round 9 audit): gateway.host/port/mcp_path 是 config/config.yaml 的
# bootstrap 段 (CLAUDE.md: config.yaml 贡献 console + 监听键, 永不入库)。
# 旧版只读 upstreams.yaml → 改 config.yaml 的端口不生效, 静默回落 18080。
# 现在优先从 config.yaml 取, 再回落 upstreams.yaml, 最后 18080。
#
# Round 12 (2026-09-19):
# - H1 stdio 模式下 stdout 是 MCP JSON-RPC 帧通道, banner/警告一律不得写
#   stdout (Write-Host 在 PS 5.1 管道重定向下落进 stdout, 会让宿主握手失败)。
#   banner 只在 http 模式打印; stdio 同时跳过无用的端口探测。
# - MED 锚定 CWD 到仓库根: `python -m src.unihive...` 依赖 src 布局 + CWD,
#   MCP 宿主的工作目录不保证是仓库根。
# - MED 透传子进程退出码 (supervisor / MCP 宿主靠它判失败与重启)。
#   宿主请用 powershell -File 形式启动; -Command "& ..." 在
#   $ErrorActionPreference=Stop + 子进程非零退出时会把退出码改写为 1。

param(
    [ValidateSet("http", "stdio")]
    [string]$Transport = "http"
)

$ErrorActionPreference = "Stop"

# 锚定仓库根 (console script `unihive` 无此依赖, 但 python -m 形式需要)
Set-Location (Resolve-Path (Join-Path $PSScriptRoot ".."))

# Load .env into the current shell so the gateway subprocess inherits it.
. (Join-Path $PSScriptRoot "_load_env.ps1")

# Round-12 簇I: 必须在 python 进程启动**之前**设置 —— 解释器只在启动瞬间
# 读 PYTHONIOENCODING 决定 stdio 编码, 进程内再设对自身无效。stdio 模式
# stdout 承载 MCP JSON-RPC 帧, 中文 Windows 默认 cp936, 帧内含中文会炸。
# (进程内另有 sys.stdout/stdin.reconfigure 兜底不经 ps1 的启动路径。)
$env:PYTHONIOENCODING = "utf-8"

if ($Transport -eq "stdio") {
    # stdio: stdout 只承载 JSON-RPC, 不做端口探测、不打印 banner
    python -m src.unihive.gateway_server --transport stdio
    exit $LASTEXITCODE
}

# ---- http only ----

# Resolve gateway port via Python (避免在 PowerShell 里手撕 YAML)
$GatewayPort = 18080
$ConfigYamlPath = Join-Path $PSScriptRoot "..\config\config.yaml"
$UpstreamsYamlPath = Join-Path $PSScriptRoot "..\config\upstreams.yaml"

try {
    $portStr = python -c @"
import sys, os
import yaml
def _gw_port(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    gw = cfg.get('gateway') or {}
    p = gw.get('port')
    return int(p) if p is not None else None
# 优先 config.yaml (bootstrap 段), 再回落 upstreams.yaml
for _p in (r"$($ConfigYamlPath -replace '\\','/')", r"$($UpstreamsYamlPath -replace '\\','/')"):
    try:
        _port = _gw_port(_p)
    except Exception:
        _port = None
    if _port and _port > 0:
        print(_port)
        sys.exit(0)
print(18080)
"@
    if ($LASTEXITCODE -eq 0) {
        $parsed = [int]$portStr.Trim()
        if ($parsed -gt 0) {
            $GatewayPort = $parsed
        }
    }
} catch {
    [Console]::Error.WriteLine("failed to read gateway.port, falling back to 18080: $_")
}

Write-Host ""
Write-Host "UniHive MCP Gateway" -ForegroundColor Green
Write-Host ("Transport: {0}" -f $Transport) -ForegroundColor Yellow
Write-Host ("Port:      {0}" -f $GatewayPort) -ForegroundColor Yellow
Write-Host ""

python -m src.unihive.gateway_server --transport http --port $GatewayPort
exit $LASTEXITCODE
