# Install (or uninstall / restart-and-verify) the UniHive gateway as a Windows
# Scheduled Task so it starts automatically on boot — detached from any Claude
# Code session, safe from the harness's low-memory reclaim.
#
# Requires ADMIN (the task runs as SYSTEM). Run from an elevated terminal:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_scheduled_task.ps1
#
# Design:
#   * Task runs 'run_gateway_detached.ps1', which (a) loads .env, (b)
#     Start-Process a detached 'unihive' child (own logs), (c) exits. The child
#     is disowned, so it survives both harness reclaim and the task itself
#     finishing.
#   * /SC ONSTART + /RU SYSTEM = fires at every boot, no user login needed and
#     no stored password. Fits a headless HTTP gateway. (Prefer ONLOGON + your
#     account instead? change $Trigger / $Account below.)
#
# Switches:
#   -Uninstall      delete the task
#   -VerifyNow      trigger the task and poll /mcp until it answers (or fails)
param(
    [switch]$Uninstall,
    [switch]$VerifyNow,
    [string]$TaskName = 'UniHiveGateway',
    [string]$Trigger   = 'ONSTART',
    [string]$Account   = 'SYSTEM'
)

$ErrorActionPreference = 'Stop'

$Root   = Split-Path -Parent $PSScriptRoot
$Worker = Join-Path $PSScriptRoot 'run_gateway_detached.ps1'
$Port   = 18080
$McpUrl = "http://127.0.0.1:$Port/mcp"

if (-not (Test-Path $Worker)) {
    Write-Error "worker script not found: $Worker"
    exit 2
}

if ($Uninstall) {
    schtasks /Delete /TN $TaskName /F | Out-Null
    Write-Output "removed scheduled task: $TaskName"
    exit 0
}

# schtasks /TR is one command line; quote the worker path in case it has spaces.
$RunCommand = 'powershell -NoProfile -ExecutionPolicy Bypass -File "' + $Worker + '"'

Write-Output "==> creating scheduled task '$TaskName' ($Trigger, $Account)"
schtasks /Create /SC $Trigger /RU $Account /TN $TaskName /TR $RunCommand /F
if ($LASTEXITCODE -ne 0) {
    Write-Error "schtasks /Create failed (need an ELEVATED terminal). Task name: $TaskName"
    exit $LASTEXITCODE
}

Write-Output "created. Query:"
schtasks /Query /TN $TaskName
Write-Output "gateway will auto-start on next boot. Manual runs: schtasks /Run /TN $TaskName"

if ($VerifyNow) {
    Write-Output ""
    Write-Output "==> triggering now and polling $McpUrl ..."
    schtasks /Run /TN $TaskName | Out-Null

    $body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"verify","version":"1"}}}'
    $deadline = (Get-Date).AddSeconds(45)
    $ok = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-RestMethod -Uri $McpUrl -Method Post `
                -ContentType 'application/json' -Body $body -TimeoutSec 5
            if ($resp.result.serverInfo) { $ok = $true; break }
        } catch {
            Start-Sleep -Seconds 3
        }
    }
    if ($ok) {
        Write-Output "OK: /mcp answered (serverInfo $(($resp.result.serverInfo.serverName) -join ''))"
    } else {
        Write-Error "TIMEOUT: /mcp not reachable within 45s — check logs/gateway_stderr.log"
        exit 1
    }
}