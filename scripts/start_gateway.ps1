# UniHive MCP Gateway - PowerShell Startup Script
# Usage:
#   .\scripts\start_gateway.ps1                 # HTTP mode (default), port from config/upstreams.yaml
#   .\scripts\start_gateway.ps1 -Transport stdio # STDIO mode
#   .\scripts\start_gateway.ps1 -Port 19001      # Custom port

# 从 YAML 读取 gateway.port 作为默认端口
$DefaultPort = 18080
$YamlConfigPath = Join-Path $ProjectRoot "config\upstreams.yaml"
if (Test-Path $YamlConfigPath) {
    $yamlContent = Get-Content $YamlConfigPath -Raw -Encoding UTF8
    if ($yamlContent -match '^\s*port:\s*(\d+)') {
        $DefaultPort = [int]$matches[1]
    }
}

param(
    [ValidateSet("http", "stdio")]
    [string]$Transport = "http",
    [int]$Port = $DefaultPort
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir\..
$ProjectRoot = Get-Location

# 加载 .env
. "$ScriptDir\_load_env.ps1"

Write-Host "========================================"
Write-Host "UniHive Gateway (MCP + Console)"
Write-Host "========================================"
Write-Host "  MCP 接口: http://127.0.0.1:$Port/mcp"
Write-Host "  Console:  http://127.0.0.1:$Port"
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

if ($Transport -eq "http") {
    Write-Host ""
    Write-Host "Starting UniHive gateway (HTTP mode on http://127.0.0.1:$Port)..." -ForegroundColor Green
    Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
    Write-Host ""
    python -m src.gateway_server --transport http --port $Port
} else {
    Write-Host ""
    Write-Host "Starting UniHive gateway (STDIO mode)..." -ForegroundColor Green
    Write-Host "Press Ctrl+C to stop" -ForegroundColor Yellow
    Write-Host ""
    python -m src.gateway_server --transport stdio
}