# Long-lived watchdog for the UniHive MCP gateway.
#
# Invoked by the UniHiveGateway scheduled task (ONLOGON + user account).
# Polls every PollSeconds (default 5s); if the gateway child process is gone,
# relaunches via run_gateway_detached.ps1's Launch-Gateway path with an
# exponential backoff that caps at MaxBackoffSec (default 300s). If the child
# is alive but /mcp is unresponsive for > ZombieMcpTimeoutSec (default 120s),
# treat as a zombie: Stop-Process + let the next iteration relaunch.
#
# Self-exits cleanly on either of two conditions:
#   * logs/.stop_watchdog sentinel exists  → user-initiated stop (stop_gateway.ps1
#     or manual `Remove-Item`). Sentinel is consumed (deleted) before exit so
#     the next start doesn't immediately self-stop.
#   * parent powershell.exe no longer in Win32_Process  → user logoff (session
#     ending). We do NOT write a sentinel in this branch; on next logon the
#     scheduled task fires again and a fresh watchdog starts.
#
# Crash-loop fuse: MaxCrashesIn30Min (default 10) crashes within 30 minutes
# disable further restarts and write logs/watchdog_disabled.log with reason.
# User must Remove-Item logs/.stop_watchdog AND logs/watchdog_disabled.log to
# re-arm; otherwise manual start_gateway.ps1 will also clear the sentinel.
#
# Self-logs to logs/watchdog.log with a simple append + tail-truncate (keep last
# 3MB) so the file never grows unbounded across long sessions.

param(
    [int]$Port = 18080,
    [int]$PollSeconds = 5,
    [int]$ZombieMcpTimeoutSec = 120,
    [int]$MaxCrashesIn30Min = 10,
    [int]$InitialBackoffSec = 5,
    [int]$MaxBackoffSec = 300,
    [int]$CrashWindowMinutes = 30,
    [int]$LogMaxBytes = 5MB,
    [int]$LogKeepTailBytes = 3MB
)

$ErrorActionPreference = 'Stop'

# --- paths -------------------------------------------------------------------

$ScriptsDir = Split-Path -Parent $PSCommandPath
$Root       = Split-Path -Parent $ScriptsDir
$LogDir     = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$PidFile        = Join-Path $LogDir 'gateway.pid'
$StopSentinel   = Join-Path $LogDir '.stop_watchdog'
$WatchdogLog    = Join-Path $LogDir 'watchdog.log'
$DisabledLog    = Join-Path $LogDir 'watchdog_disabled.log'
$StdoutLog      = Join-Path $LogDir 'gateway_stdout.log'
$StderrLog      = Join-Path $LogDir 'gateway_stderr.log'

# --- env + unihive resolution -----------------------------------------------

. "$ScriptsDir\_load_env.ps1"

# watchdog 自己解析 unihive.exe 路径 (不 dot-source run_gateway_detached.ps1
# —— 那个脚本会真的 Start-Process 一次, dot-source 等于自动启动, 与 watchdog
# 自己控制启动时机冲突)。与 run_gateway_detached.ps1 内的同名函数定义保持
# 一致, 任何一方路径解析逻辑变更需同步另一处。
function Local-Resolve-UnihiveExe {
    $cmd = Get-Command unihive -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path $cmd.Source)) { return $cmd.Source }
    $fallback = 'C:\Users\admin\AppData\Local\Programs\Python\Python313\Scripts\unihive.exe'
    if (Test-Path $fallback) { return $fallback }
    throw "unihive.exe not found on PATH and fallback missing: $fallback"
}
$exe = Local-Resolve-UnihiveExe

# --- logging -----------------------------------------------------------------

function Write-WatchdogLog {
    param([string]$Message)
    $line = "[{0:yyyy-MM-dd HH:mm:ss}] {1}" -f (Get-Date), $Message
    Add-Content -Path $WatchdogLog -Value $line -Encoding UTF8
    # Tail-truncate: when log exceeds LogMaxBytes, keep last LogKeepTailBytes
    if ((Test-Path $WatchdogLog) -and ((Get-Item $WatchdogLog).Length -gt $LogMaxBytes)) {
        try {
            $bytes = [System.IO.File]::ReadAllBytes($WatchdogLog)
            $tail  = $bytes[-1 * $LogKeepTailBytes..($bytes.Length - 1)]
            [System.IO.File]::WriteAllBytes($WatchdogLog, $tail)
        } catch {
            # If truncate fails (file locked, etc.), drop and start fresh; next
            # write recreates it. We never want logging to crash the watchdog.
            Remove-Item -Force $WatchdogLog -ErrorAction SilentlyContinue
        }
    }
}

# --- helpers -----------------------------------------------------------------

function Test-ParentAlive {
    # 我们的父进程是 schtasks 起的 powershell.exe。Win32_Process.ParentProcessId
    # 在 powershell 进程退出后会被 OS 自动清理, 所以 Get-CimInstance 找不到父
    # 即代表 session/logoff 引发父进程死掉。$MyInvocation.MyCommand.Path 是
    # 当前脚本自身 PID, 但我们不需要 self PID — 用 ParentProcessId 反向查
    # 自己的父亲即可。
    $self = [System.Diagnostics.Process]::GetCurrentProcess()
    $parentId = $null
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($self.Id)" -EA SilentlyContinue
        if ($proc) { $parentId = [int]$proc.ParentProcessId }
    } catch { }
    if (-not $parentId) { return $false }
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$parentId" -EA SilentlyContinue
    return [bool]$parent
}

function Test-PortListening {
    param([int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(200)) {
            $client.Close(); return $false
        }
        $client.EndConnect($iar); $client.Close(); return $true
    } catch {
        try { $client.Close() } catch { }
        return $false
    }
}

function Test-McpHealthy {
    param([int]$Port)
    try {
        $body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"watchdog","version":"1"}}}'
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/mcp" -Method Post `
            -ContentType 'application/json' -Body $body -TimeoutSec 3 -EA Stop
        return [bool]$resp.result
    } catch {
        return $false
    }
}

function Launch-Gateway {
    # 清残留 sentinel: 正常启动路径。sentinel 残留会让 watchdog 误判为用户主动
    # 停而退出, 这里主动清。如果用户真的想停, sentinel 应当在 stop_gateway.ps1
    # 写完之后**并**在 Launch-Gateway 之前, 此时 Launch-Gateway 也不会被调
    # (因为 sentinel 在 loop 顶部检查时已经命中退出)。
    if (Test-Path $StopSentinel) { Remove-Item -Force $StopSentinel }

    $p = Start-Process -FilePath $exe `
        -ArgumentList '--transport', 'http', '--port', "$Port" `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError  $StderrLog `
        -WindowStyle Hidden `
        -PassThru
    [IO.File]::WriteAllText("$PidFile.tmp", $p.Id)
    Move-Item -Force "$PidFile.tmp" $PidFile
    Write-WatchdogLog "launched gateway pid=$($p.Id)"
    return $p.Id
}

function Read-ChildPid {
    if (-not (Test-Path $PidFile)) { return $null }
    $line = (Get-Content $PidFile -EA SilentlyContinue | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($line)) { return $null }
    $id = 0
    if (-not [int]::TryParse($line.Trim(), [ref]$id)) { return $null }
    if ($id -le 0) { return $null }
    return $id
}

# --- startup -----------------------------------------------------------------

Write-WatchdogLog "watchdog starting (poll=${PollSeconds}s, port=$Port, max_crashes=$MaxCrashesIn30Min/${CrashWindowMinutes}m)"

# 首启: 父进程存在 + 端口空闲 → 立刻拉起 (不计入 crash 计数)
$crashTimes = @()
if (Test-Path $StopSentinel) {
    Write-WatchdogLog "stop sentinel present at startup; exiting without launch"
    Remove-Item -Force $StopSentinel
    exit 0
}
if (-not (Test-PortListening -Port $Port)) {
    Launch-Gateway | Out-Null
} else {
    Write-WatchdogLog "port $Port already listening at startup; skipping initial launch (taking over existing process)"
}

# --- main loop ----------------------------------------------------------------

while ($true) {
    Start-Sleep -Seconds $PollSeconds

    # 1) user-initiated stop
    if (Test-Path $StopSentinel) {
        Write-WatchdogLog "stop sentinel seen; exiting (user-initiated)"
        Remove-Item -Force $StopSentinel
        exit 0
    }

    # 2) parent gone (user logoff / session ending)
    if (-not (Test-ParentAlive)) {
        Write-WatchdogLog "parent process gone (logoff); exiting without restart"
        exit 0
    }

    # 3) child alive?
    $pid_ = Read-ChildPid
    $alive = $false
    if ($pid_) {
        $proc = Get-Process -Id $pid_ -EA SilentlyContinue
        $alive = [bool]$proc
    }

    if (-not $alive) {
        # child dead (or never started)
        if (Test-PortListening -Port $Port) {
            # 端口被外部占用 (例如另一个 unihive 实例) — 不接管, 也不重启
            Write-WatchdogLog "port $Port already listening (external); skipping restart"
            continue
        }

        $crashTimes += Get-Date
        $cutoff = (Get-Date).AddMinutes(-1 * $CrashWindowMinutes)
        $crashTimes = @($crashTimes | Where-Object { $_ -ge $cutoff })

        if ($crashTimes.Count -ge $MaxCrashesIn30Min) {
            Write-WatchdogLog "CRASH FUSE TRIPPED: $($crashTimes.Count) crashes within ${CrashWindowMinutes}m; disabling restarts"
            try {
                "watchdog disabled at $(Get-Date -Format o); $MaxCrashesIn30Min crashes within $CrashWindowMinutes minutes" `
                    | Set-Content -Path $DisabledLog -Encoding UTF8
            } catch { }
            # 不再重启, 但继续空转 (sleep + 检查 sentinel + 父进程), 让用户能用
            # stop_gateway.ps1 干净停, 或 Remove-Item sentinel 后手动重启
            while ($true) {
                Start-Sleep -Seconds $PollSeconds
                if (Test-Path $StopSentinel) {
                    Remove-Item -Force $StopSentinel
                    Write-WatchdogLog "stop sentinel seen (post-fuse); exiting"
                    exit 0
                }
                if (-not (Test-ParentAlive)) {
                    Write-WatchdogLog "parent gone (post-fuse); exiting"
                    exit 0
                }
            }
        }

        # 指数退避: 5 * 2^(n-1) 秒, 上限 MaxBackoffSec
        $delay = [Math]::Min($MaxBackoffSec, [int]($InitialBackoffSec * [Math]::Pow(2, $crashTimes.Count - 1)))
        Write-WatchdogLog "child gone; restarting after ${delay}s (crashes in window: $($crashTimes.Count))"
        Start-Sleep -Seconds $delay
        # 重启前再确认没 sentinel (用户可能在 backoff 期间写了)
        if (Test-Path $StopSentinel) {
            Remove-Item -Force $StopSentinel
            Write-WatchdogLog "stop sentinel appeared during backoff; exiting"
            exit 0
        }
        Launch-Gateway | Out-Null
        continue
    }

    # 4) alive but possibly zombie (mcp 不响应)
    $proc = Get-Process -Id $pid_ -EA SilentlyContinue
    if ($proc -and $proc.StartTime) {
        $age = ((Get-Date) - $proc.StartTime).TotalSeconds
        if ($age -gt $ZombieMcpTimeoutSec -and -not (Test-McpHealthy -Port $Port)) {
            Write-WatchdogLog "zombie detected (age=${age}s, mcp unresponsive); killing pid=$pid_"
            try { Stop-Process -Id $pid_ -Force -EA SilentlyContinue } catch { }
            # 下一轮迭代自然进 crash 路径
        }
    }
}