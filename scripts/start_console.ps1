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

# HTTP mode requires GATEWAY_BEARER_TOKEN; stdio mode does not
if ([string]::IsNullOrWhiteSpace($env:GATEWAY_BEARER_TOKEN)) {
    Write-Host "[ERROR] GATEWAY_BEARER_TOKEN is not set; HTTP gateway will fail to start." -ForegroundColor Red
    Write-Host '  Add to .env (generate via: python -c "import secrets; print(secrets.token_urlsafe(32))"):' -ForegroundColor Yellow
    Write-Host "    GATEWAY_BEARER_TOKEN=<your-token>" -ForegroundColor Yellow
    exit 1
}

# 启动控制台
Start-Process python -ArgumentList "-m", "src.console_server" -NoNewWindow

# 启动 HTTP gateway (child inherits parent env, so token above is forwarded)
Start-Process python -ArgumentList "-m", "src.gateway_server", "--transport", "http", "--port", "18081" -NoNewWindow

# 等待 gateway HTTP 端口就绪（最多 15 秒）
$GatewayReady = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.BeginConnect("127.0.0.1", 18081, $null, $null) | Out-Null
        Start-Sleep -Milliseconds 50
        if ($tcp.Connected) {
            $GatewayReady = $true
            $tcp.Close()
            break
        }
        $tcp.Close()
    } catch { }
}
if ($GatewayReady) {
    Write-Host "Gateway HTTP port 18081 is ready" -ForegroundColor Green
} else {
    Write-Host "[WARN] Gateway HTTP port 18081 not ready after 15s, continuing anyway" -ForegroundColor Yellow
}

Start-Process "http://127.0.0.1:18080"
