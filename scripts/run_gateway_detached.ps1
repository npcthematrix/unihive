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
# stderr). The child PID is written to logs/gateway.pid so external watchers
# (watchdog_gateway.ps1 / stop_gateway.ps1 / healthcheck.py) can introspect.
# Manage the task with:
#   Start-ScheduledTask -TaskName UniHiveGateway   / schtasks /Run /TN UniHiveGateway
#
# Reusable function `Resolve-UnihiveExe` is defined here and dot-sourceable by
# watchdog_gateway.ps1 / stop_gateway.ps1 to keep path resolution in one place.
$ErrorActionPreference = 'Stop'

$Root     = Split-Path -Parent $PSScriptRoot          # <repo>/scripts/.. -> repo root
$LogDir   = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$StdoutLog = Join-Path $LogDir 'gateway_stdout.log'
$StderrLog = Join-Path $LogDir 'gateway_stderr.log'
$PidFile   = Join-Path $LogDir 'gateway.pid'

# 一处定义 unihive.exe 路径解析: Get-Command 优先 (随 pip install -e . 重装自动跟随),
# fallback 硬编码路径 (PATH 未配置时仍可用)。双重失败 throw 让 watchdog /
# install 捕获并明确报错, 而不是用错误路径启动后静默失败。
function Resolve-UnihiveExe {
    param()
    $cmd = Get-Command unihive -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path $cmd.Source)) { return $cmd.Source }
    $fallback = 'C:\Users\admin\AppData\Local\Programs\Python\Python313\Scripts\unihive.exe'
    if (Test-Path $fallback) { return $fallback }
    throw "unihive.exe not found on PATH and fallback missing: $fallback"
}

# Load .env into THIS process so the detached child inherits UNIHIVE_CONSOLE_*,
# fuyao/thsdk/omni credentials, etc. (a fresh powershell has none of these).
# Without this the detached gateway logs "Console auth disabled" and upstreams
# that need secrets fail to authenticate.
. "$PSScriptRoot\_load_env.ps1"

# 主入口: 可独立调用 (`powershell -File run_gateway_detached.ps1`), 也可被
# watchdog_gateway.ps1 dot-source 后用 `-Port` 调 Launch-Gateway 内部 helper。
# 加 -PassThru 拿 Process 对象 → 写 PID 文件供外部 introspection。
$exe = Resolve-UnihiveExe

# 清残留 sentinel: 用户可能上次 `stop_gateway.ps1` 后手起一次, sentinel 残留
# 会让 watchdog 误判本进程是"用户主动停", 直接退出。本脚本就是正常启动路径,
# 永远先清 sentinel。
$sentinel = Join-Path $LogDir '.stop_watchdog'
if (Test-Path $sentinel) { Remove-Item -Force $sentinel }

$p = Start-Process -FilePath $exe `
    -ArgumentList '--transport', 'http', '--port', '18080' `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError  $StderrLog `
    -WindowStyle Hidden `
    -PassThru

# 原子写 PID 文件 (同卷 rename 在 Windows 上是原子的)。直写覆盖会被 Start-Process
# 自身的并发读撞到, 加 .tmp 走两步: 写完 rename, watchdog / stop 读主文件。
[IO.File]::WriteAllText("$PidFile.tmp", $p.Id)
Move-Item -Force "$PidFile.tmp" $PidFile

Write-Output "launched detached (pid $($p.Id) via Get-Process); pid file: $PidFile"