# UniHive - Start Both Gateway and Console
# Usage: .\scripts\start_all.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir\..
$ProjectRoot = Get-Location

# 加载 .env
. "$ScriptDir\_load_env.ps1"

Write-Host "========================================"
Write-Host "UniHive - Starting All Services"
Write-Host "========================================"

$env:PYTHONIOENCODING = "utf-8"

$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Write-Host "Starting gateway server (stdio)..." -ForegroundColor Cyan
Start-Process python -ArgumentList "-m src.gateway_server" -WorkingDirectory $ProjectRoot -NoNewWindow

Write-Host "Starting console server on http://127.0.0.1:18080..." -ForegroundColor Cyan
Start-Process python -ArgumentList "-m src.console_server", "18080" -WorkingDirectory $ProjectRoot -NoNewWindow

Start-Sleep -Seconds 3
Start-Process "http://127.0.0.1:18080"

Write-Host ""
Write-Host "Done! Gateway and Console are running." -ForegroundColor Green
Write-Host "  - Gateway: stdio mode (for MCP clients)" -ForegroundColor Gray
Write-Host "  - Console: http://127.0.0.1:18080" -ForegroundColor Gray
