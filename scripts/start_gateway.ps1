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
# 到默认值 18080。同时去掉了之前重复 4 次的 banner 打印。
#
# M11 (round 9 audit): gateway.host/port/mcp_path 是 config/config.yaml 的
# bootstrap 段 (CLAUDE.md: config.yaml 贡献 console + 监听键, 永不入库)。
# 旧版只读 upstreams.yaml → 改 config.yaml 的端口不生效, 静默回落 18080。
# 现在优先从 config.yaml 取, 再回落 upstreams.yaml, 最后 18080。
# 同时 dot-source _load_env.ps1, 让 .env 里的 env var 进父 shell, 被
# gateway 子进程继承 (UNIHIVE_CONSOLE_* 等)。

param(
    [ValidateSet("http", "stdio")]
    [string]$Transport = "http"
)

$ErrorActionPreference = "Stop"

# Load .env into the parent shell so the gateway subprocess inherits it.
$LoadEnv = "$PSScriptRoot/_load_env.ps1"
if (Test-Path $LoadEnv) {
    . $LoadEnv
}

# Resolve gateway port via Python (避免在 PowerShell 里手撕 YAML)
$GatewayPort = 18080
$ConfigYamlPath = "$PSScriptRoot/../config/config.yaml"
$UpstreamsYamlPath = "$PSScriptRoot/../config/upstreams.yaml"

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
    Write-Warning "failed to read gateway.port, falling back to 18080: $_"
}

# Banner (single block, no duplication)
Write-Host ""
Write-Host "UniHive MCP Gateway" -ForegroundColor Green
Write-Host ("Transport: {0}" -f $Transport) -ForegroundColor Yellow
if ($Transport -eq "http") {
    Write-Host ("Port:      {0}" -f $GatewayPort) -ForegroundColor Yellow
}
Write-Host ""

# Start Python gateway server
if ($Transport -eq "stdio") {
    python -m src.unihive.gateway_server --transport stdio
} else {
    python -m src.unihive.gateway_server --transport http --port $GatewayPort
}
