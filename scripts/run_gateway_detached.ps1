# Run UniHive MCP gateway detached from any interactive Claude Code session.
# PowerShell launches the installed console script 'unihive' via Start-Process
# with native stdout/stderr redirection (NOT `*>>` which mangles UTF-8 logs and
# dies silently under a hidden window).
#
# Why detached: when the gateway is a background task of a Claude Code session,
# the harness reclaims it on low memory. A Start-Process child is a first-class
# Windows process not tracked by the harness, so it survives low-memory reclaim
# and session exit.
#
# Logs: <repo>/logs/gateway_stdout.log + gateway_stderr.log (Python logging ->
# stderr). Manage the task with:
#   Start-ScheduledTask -TaskName UniHiveGateway   / schtasks /Run /TN UniHiveGateway
$ErrorActionPreference = 'Stop'

$Root     = Split-Path -Parent $PSScriptRoot          # <repo>/scripts/.. -> repo root
$LogDir   = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$StdoutLog = Join-Path $LogDir 'gateway_stdout.log'
$StderrLog = Join-Path $LogDir 'gateway_stderr.log'

$Unihive = 'C:\Users\admin\AppData\Local\Programs\Python\Python313\Scripts\unihive.exe'

# Load .env into THIS process so the detached child inherits UNIHIVE_CONSOLE_*,
# fuyao/thsdk/omni credentials, etc. (a fresh powershell has none of these).
# Without this the detached gateway logs "Console auth disabled" and upstreams
# that need secrets fail to authenticate.
. "$PSScriptRoot\_load_env.ps1"

# Launch gateway detached; cwd=repo root so relative config/data/logs resolve.
Start-Process -FilePath $Unihive `
    -ArgumentList '--transport', 'http', '--port', '18080' `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError  $StderrLog `
    -WindowStyle Hidden

Write-Output "launched detached (pid via Get-Process python); logs: $StderrLog"