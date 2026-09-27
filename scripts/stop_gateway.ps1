# Clean-stop the UniHive MCP gateway that was started by watchdog_gateway.ps1
# (or a manual run_gateway_detached.ps1 invocation).
#
# Strategy:
#   1) Write logs/.stop_watchdog sentinel → watchdog loop top check sees it
#      and exits without relaunching.
#   2) Stop-Process the child by reading logs/gateway.pid; fall back to
#      Get-Process matching 'unihive' if the pid file is missing or stale.
#   3) Stop the scheduled task itself so schtasks /End triggers task scheduler
#      to tear down the watchdog host powershell.exe. This guarantees the
#      watchdog exits within a few seconds; without it, the watchdog would only
#      exit on its own loop iteration (≤ PollSeconds delay).
#
# Safe to run multiple times (idempotent: removing a missing sentinel is a no-op,
# Stop-Process on a dead pid is a no-op).

$ErrorActionPreference = 'Stop'

$ScriptsDir = Split-Path -Parent $PSCommandPath
$Root       = Split-Path -Parent $ScriptsDir
$LogDir     = Join-Path $Root 'logs'
$PidFile    = Join-Path $LogDir 'gateway.pid'
$Sentinel   = Join-Path $LogDir '.stop_watchdog'

# 1) sentinel
"user-initiated stop at $(Get-Date -Format o)" | Set-Content -Path $Sentinel -Encoding UTF8
Write-Output "wrote stop sentinel: $Sentinel"

# 2) child
$pid_ = $null
if (Test-Path $PidFile) {
    $line = (Get-Content $PidFile -EA SilentlyContinue | Select-Object -First 1)
    if (-not [string]::IsNullOrWhiteSpace($line)) {
        $tmp = 0
        if ([int]::TryParse($line.Trim(), [ref]$tmp)) { $pid_ = $tmp }
    }
}

if ($pid_) {
    $proc = Get-Process -Id $pid_ -EA SilentlyContinue
    if ($proc) {
        Write-Output "stopping gateway pid=$pid_"
        try { Stop-Process -Id $pid_ -Force -EA SilentlyContinue } catch { }
    } else {
        Write-Output "pid file says $pid_ but no such process; skipping Stop-Process"
    }
} else {
    Write-Output "no valid pid file; falling back to Get-Process unihive"
    $pros = Get-Process unihive -EA SilentlyContinue
    foreach ($p in $pros) {
        try {
            Write-Output "stopping unihive pid=$($p.Id)"
            Stop-Process -Id $p.Id -Force -EA SilentlyContinue
        } catch { }
    }
}

# 3) scheduled task — 让 schtasks 强杀 watchdog host。
#   /End 立即返回, 实际进程树清理是 task scheduler 在后台做的, 通常 < 5s 完成。
$taskName = 'UniHiveGateway'
$taskExists = $true
try { schtasks /Query /TN $taskName | Out-Null } catch { $taskExists = $false }
if ($taskExists) {
    Write-Output "ending scheduled task: $taskName"
    schtasks /End /TN $taskName 2>$null | Out-Null
} else {
    Write-Output "no scheduled task '$taskName'; watchdog not running via task scheduler"
}

Write-Output "stop complete (sentinel left in place for any future watchdog to consume)"