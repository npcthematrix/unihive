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
Write-Host "UniHive Console"
Write-Host "========================================"

$env:PYTHONIOENCODING = "utf-8"

try {
    $PythonVersion = python --version 2>&1
    Write-Host "Python: $PythonVersion"
} catch {
    Write-Host "[ERROR] Python not found. Please install Python 3.10+" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Starting console server on http://127.0.0.1:18080" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""

Start-Process python -ArgumentList "-m src.console_server", "18080" -NoNewWindow
Start-Sleep -Seconds 2
Start-Process "http://127.0.0.1:18080"
