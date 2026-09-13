# UniHive MCP Gateway - PowerShell Startup Script
# Usage:
#   .\scripts\start_gateway.ps1                 # HTTP mode (default), port from config/upstreams.yaml
#   .\scripts\start_gateway.ps1 -Transport stdio # STDIO mode
#   .\scripts\start_gateway.ps1 -Transport http  # HTTP mode explicit, port from config/upstreams.yaml

param(
    [ValidateSet("http", "stdio")]
    [string]$Transport = "http"
)

$ErrorActionPreference = "SilentlyContinue"
$GatewayPort = 18080

try {
    $yamlPath = "$PSScriptRoot/../config/upstreams.yaml"
    if (Test-Path $yamlPath) {
        try {
            $raw = Get-Content $yamlPath -Raw
            if ($raw -match 'port:\s*(\d+)') {
                $parsed = [int]$matches[1]
                if ($parsed -gt 0) {
                    $GatewayPort = $parsed
                }
            }
        }
    } catch {}
} catch {}

# Write host output
Write-Host ""
Write-Host "UniHive MCP Gateway - PowerShell Startup Script" -ForegroundColor Green
Write-Host ("HTTP mode (port {0})" -f $GatewayPort) -ForegroundColor Yellow
Write-Host ""

Write-Host ""
Write-Host "UniHive MCP Gateway - PowerShell Startup Script" -ForegroundColor Green
Write-Host ("HTTP mode (port {0})" -f $GatewayPort) -ForegroundColor Yellow
Write-Host ""
Write-Host ""
Write-Host "UniHive MCP Gateway - PowerShell Startup Script" -ForegroundColor Green
Write-Host ("HTTP mode (port {0})" -f $GatewayPort) -ForegroundColor Yellow
Write-Host ""

# Start Python gateway server
python -m src.gateway_server --transport http --port $GatewayPort