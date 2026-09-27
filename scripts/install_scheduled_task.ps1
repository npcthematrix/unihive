# Install (or uninstall / restart-and-verify) the UniHive gateway as a Windows
# Scheduled Task so it starts automatically at every user logon — detached from
# any Claude Code session, with the watchdog_gateway.ps1 monitor keeping the
# child process alive across crashes.
#
# Requires ADMIN (the task is created with /RU <user>; the /XML carries
# Principal.UserId; first-time task creation needs the user context of the
# installer terminal). Run from an elevated terminal:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_scheduled_task.ps1
#
# Design:
#   * Default trigger is ONLOGON for the current user ($env:USERNAME), so .env
#     resolution matches a manual `start_gateway.ps1` invocation (avoids the
#     SYSTEM-profile surprises an ONSTART+SYSTEM task can hit on some Win 11
#     builds). Override with -Trigger ONSTART + -Account SYSTEM if you prefer.
#   * The scheduled action invokes watchdog_gateway.ps1, which polls every 5s
#     and restarts the detached unihive child on crash (exp backoff capped at
#     5 min, fuse after 10 crashes / 30 min — see watchdog_gateway.ps1).
#   * <ExecutionTimeLimit>PT999999M</ExecutionTimeLimit> lets the watchdog run
#     the whole session; <AllowHardTerminate>true</AllowHardTerminate> lets
#     schtasks tear it down at logoff; <DisallowStartIfOnBatteries>false<
#     /DisallowStartIfOnBatteries> works on laptops on battery.
#   * Install idempotency: query existing task XML; if trigger/account differ,
#     require -ForceMigrate to overwrite (prevents accidentally changing a
#     user's pre-deployed task).
#
# Switches:
#   -Uninstall      delete the task + write stop sentinel (kills running child too)
#   -VerifyNow      trigger the task and poll /mcp until it answers (or fails)
#   -ForceMigrate   when an existing task has different settings, overwrite anyway
param(
    [switch]$Uninstall,
    [switch]$VerifyNow,
    [switch]$ForceMigrate,
    [string]$TaskName  = 'UniHiveGateway',
    [string]$Trigger   = 'ONLOGON',
    [string]$Account   = $env:USERNAME,
    [string]$Worker    = (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\watchdog_gateway.ps1'),
    [int]   $Port      = 18080
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Worker)) {
    Write-Error "worker script not found: $Worker"
    exit 2
}

# ---------------------------------------------------------------------------
# Uninstall: kill the running child + end the task + remove the schedule.
# ---------------------------------------------------------------------------
if ($Uninstall) {
    Write-Output "==> uninstalling $TaskName (writing stop sentinel + ending task + deleting schedule)"
    $sentinel = Join-Path (Split-Path -Parent $PSScriptRoot) 'logs\.stop_watchdog'
    New-Item -ItemType Directory -Force -Path (Split-Path $sentinel) | Out-Null
    "uninstall at $(Get-Date -Format o)" | Set-Content -Path $sentinel -Encoding UTF8
    try { schtasks /End    /TN $TaskName | Out-Null } catch { }
    try { schtasks /Delete /TN $TaskName /F | Out-Null } catch { }
    # 也尝试 Stop-Process 残留 unihive (task end 可能没等到 child 自己响应 sentinel)
    $pros = Get-Process unihive -EA SilentlyContinue
    foreach ($p in $pros) {
        try { Stop-Process -Id $p.Id -Force -EA SilentlyContinue } catch { }
    }
    Write-Output "removed scheduled task: $TaskName"
    exit 0
}

# ---------------------------------------------------------------------------
# Build the task XML in-memory.
#   - <Principal><UserId>$Account</UserId><LogonType>InteractiveToken</LogonType>
#     <RunLevel>Highest</RunLevel></Principal>   → /IT + HIGHEST
#   - <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
#   - <ExecutionTimeLimit>PT999999M</ExecutionTimeLimit>  (~694 days, effectively ∞)
#   - <AllowHardTerminate>true</AllowHardTerminate>
#   - <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
# ---------------------------------------------------------------------------

# schtasks 创建用户级任务时, 需要把 XML 里的 <Principal> 字段正确填好。
# 注意: UserId 必须是机器上实际存在的账号 (本地账号写 .\user 或 DOMAIN\user);
# 本地账号最稳的是 S-1-5-21... 的 SID, 但 schtasks /Create /XML 也接受 user name。
# 我们让 schtasks 自己解析, UserId 写 bare username。
$runCommand = 'powershell -NoProfile -ExecutionPolicy Bypass -File "' + $Worker + '" -Port ' + $Port

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>UniHive MCP gateway watchdog — auto-starts at user logon, restarts the gateway child on crash. See scripts/watchdog_gateway.ps1.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>$Account</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$Account</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>Highest</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT999999M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -File "$Worker" -Port $Port</Arguments>
      <WorkingDirectory>$(Split-Path -Parent $Worker)</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

# ---------------------------------------------------------------------------
# Idempotency pre-check: if a task with the same name exists, compare trigger
# + principal; warn if different and require -ForceMigrate.
# ---------------------------------------------------------------------------

$existing = $null
try {
    $existing = schtasks /Query /TN $TaskName /V /FO LIST 2>$null
} catch { $existing = $null }

if ($existing) {
    Write-Output "==> existing task '$TaskName' detected; comparing trigger/account..."
    # 抓 Run As User + Trigger Type 两行
    $runAs = ($existing | Select-String '^Run As User:\s*(.+)$') | ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() }
    $triggerType = ($existing | Select-String '^Trigger Type:\s*(.+)$') | ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() }
    $differ = $false
    if ($runAs -and $runAs -ne $Account) {
        Write-Warning "  Run As User differs: existing='$runAs' new='$Account'"
        $differ = $true
    }
    if ($triggerType -and ($triggerType -notmatch 'logon|Logon')) {
        Write-Warning "  Trigger Type differs: existing='$triggerType' new='$Trigger'"
        $differ = $true
    }
    if ($differ -and -not $ForceMigrate) {
        Write-Error "task '$TaskName' has different settings; pass -ForceMigrate to overwrite"
        exit 3
    }
}

# ---------------------------------------------------------------------------
# Register the task via /XML (so we can set ExecutionTimeLimit + battery +
# MultipleInstancesPolicy atomically). Write XML to a temp file because
# schtasks /Create on some Windows builds reads XML from a file path, not
# stdin.
# ---------------------------------------------------------------------------

$xmlPath = Join-Path $env:TEMP "unihive_task_$PID.xml"
[System.IO.File]::WriteAllText($xmlPath, $xml, [System.Text.Encoding]::Unicode)

Write-Output "==> creating scheduled task '$TaskName' ($Trigger, $Account)"
schtasks /Create /XML $xmlPath /TN $TaskName /F
if ($LASTEXITCODE -ne 0) {
    Write-Error "schtasks /Create failed (need ELEVATED terminal). Task name: $TaskName"
    Remove-Item -Force $xmlPath -EA SilentlyContinue
    exit $LASTEXITCODE
}
Remove-Item -Force $xmlPath -EA SilentlyContinue

Write-Output "created. Query:"
schtasks /Query /TN $TaskName
Write-Output "gateway will auto-start at next user logon. Manual runs: schtasks /Run /TN $TaskName"

# ---------------------------------------------------------------------------
# VerifyNow: trigger the task and poll /mcp until it answers (or timeout).
# Timeout bumped 45s → 90s to accommodate watchdog's first-launch latency
# (worker spin-up + Start-Process + child port-bind).
# ---------------------------------------------------------------------------

if ($VerifyNow) {
    Write-Output ""
    Write-Output "==> triggering now and polling http://127.0.0.1:$Port/mcp ..."
    schtasks /Run /TN $TaskName | Out-Null

    $body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"verify","version":"1"}}}'
    $deadline = (Get-Date).AddSeconds(90)
    $ok = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/mcp" -Method Post `
                -ContentType 'application/json' -Body $body -TimeoutSec 5
            if ($resp.result.serverInfo) { $ok = $true; break }
        } catch {
            Start-Sleep -Seconds 3
        }
    }
    if ($ok) {
        Write-Output "OK: /mcp answered (serverInfo $(($resp.result.serverInfo.serverName) -join ''))"
    } else {
        Write-Error "TIMEOUT: /mcp not reachable within 90s — check logs/watchdog.log + logs/gateway_stderr.log"
        exit 1
    }
}