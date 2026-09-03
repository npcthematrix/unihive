# UniHive MCP Gateway - PowerShell Startup Script
# Usage: .\scripts\start_gateway.ps1

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir\..
$ProjectRoot = Get-Location

# 加载 .env
. "$ScriptDir\_load_env.ps1"

Write-Host "========================================"
Write-Host "UniHive MCP Gateway"
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
Write-Host "Starting UniHive gateway (STDIO mode - 备选模式)..." -ForegroundColor Green
Write-Host "推荐使用 start_console.ps1 以 HTTP 模式启动网关" -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

# 运行网关
python -m src.gateway_server
