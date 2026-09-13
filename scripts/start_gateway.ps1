# UniHive MCP Gateway - PowerShell Startup Script
#
# Usage:
#   .\scripts\start_gateway.ps1                  # HTTP mode (default), port from config/upstreams.yaml
#   .\scripts\start_gateway.ps1 -Transport stdio # STDIO mode (Claude Desktop / Cursor)
#   .\scripts\start_gateway.ps1 -Transport http  # HTTP mode explicit
#
# HIGH-1 (2026-09-14 audit): 之前的版本在 ps1 里用 regex 抓 YAML 首个
# `port:` 数字, 抓到的可能是 cache.port / fuyao_*.port 而非 gateway.port。
# 现在改用 python 调用 yaml.safe_load 直接读 gateway.port, 失败再 fallback
# 到默认值 18080。同时去掉了之前重复 4 次的 banner 打印。

param(
    [ValidateSet("http", "stdio")]
    [string]$Transport = "http"
)

$ErrorActionPreference = "Stop"

# Resolve gateway port via Python (避免在 PowerShell 里手撕 YAML)
$GatewayPort = 18080
$YamlPath = "$PSScriptRoot/../config/upstreams.yaml"

if (Test-Path $YamlPath) {
    try {
        $portStr = python -c @"
import sys
try:
    import yaml
    with open(r"$($YamlPath -replace '\\','/')", encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    gw = cfg.get('gateway') or {}
    print(int(gw.get('port', 18080)))
except Exception as e:
    print(f'ERR:{e}', file=sys.stderr)
    sys.exit(1)
"@
        if ($LASTEXITCODE -eq 0) {
            $parsed = [int]$portStr.Trim()
            if ($parsed -gt 0) {
                $GatewayPort = $parsed
            }
        }
    } catch {
        Write-Warning "failed to read gateway.port from $YamlPath, falling back to 18080: $_"
    }
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
    python -m src.gateway_server --transport stdio
} else {
    python -m src.gateway_server --transport http --port $GatewayPort
}
