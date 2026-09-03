# UniHive Console - PowerShell Startup Script
# Usage: .\scripts\start_console.ps1
# Opens http://127.0.0.1:18080 in browser

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir\..
$ProjectRoot = Get-Location

# 加载 .env
. "$ScriptDir\_load_env.ps1"

Write-Host "========================================"
Write-Host "UniHive Console + HTTP Gateway"
Write-Host "========================================"

$env:PYTHONIOENCODING = "utf-8"

try {
    $PythonVersion = python --version 2>&1
    Write-Host "Python: $PythonVersion"
} catch {
    Write-Host "[ERROR] Python not found. Please install Python 3.10+" -ForegroundColor Red
    exit 1
}

# 创建日志目录
$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Write-Host ""
Write-Host "Starting UniHive console (HTTP mode)..." -ForegroundColor Green
Write-Host "  - 控制台 HTML:        http://127.0.0.1:18080/" -ForegroundColor Cyan
Write-Host "  - 管理 API:            http://127.0.0.1:18080/api/*" -ForegroundColor Cyan
Write-Host "  - MCP HTTP Gateway:    http://127.0.0.1:18081/mcp" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

# 启动控制台
Start-Process python -ArgumentList "-m", "src.console_server" -NoNewWindow

# 启动 HTTP gateway
Start-Process python -ArgumentList "-m", "src.gateway_server", "--transport", "http", "--port", "18081" -NoNewWindow

Start-Sleep -Seconds 3
Start-Process "http://127.0.0.1:18080"
