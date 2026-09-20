# Load .env into current process env (KEY=VALUE lines, ignore # and blanks)
# Usage: . "$PSScriptRoot\_load_env.ps1"
#
# Round 12 (2026-09-19):
# - Get-Content -Encoding UTF8: PS 5.1 对无 BOM 文件默认按系统 ANSI (GBK) 解码,
#   .env 里的非 ASCII 值 (如中文密码) 会被静默解错;
# - 缺失警告改走 stderr (stdio 模式 stdout 是 MCP JSON-RPC 通道);
# - 无引号值剥离空白后的行内注释 ("KEY=val # 备注");
# - 只剥成对的外层引号, 合法地以引号开头/结尾的值不再被改写。

param(
    [string]$EnvPath = (Join-Path $PSScriptRoot "..\.env")
)

$EnvPath = [System.IO.Path]::GetFullPath($EnvPath)

if (-not (Test-Path $EnvPath)) {
    [Console]::Error.WriteLine("[warn] .env not found at $EnvPath - relying on existing process env")
    return
}

Get-Content $EnvPath -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $kv = $line -split "=", 2
    if ($kv.Length -ne 2) { return }
    $name = $kv[0].Trim()
    # 支持 bash 风格 "export FOO=bar" — 复制 .env.example 时常带前缀
    if ($name.StartsWith("export ")) {
        $name = $name.Substring(7).Trim()
    }
    if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { return }

    $val = $kv[1].Trim()
    if ($val.Length -ge 2 -and (
            ($val.StartsWith('"') -and $val.EndsWith('"')) -or
            ($val.StartsWith("'") -and $val.EndsWith("'"))
        )) {
        # 成对外层引号: 剥引号, 内部 (含 # 与空格) 原样保留
        $val = $val.Substring(1, $val.Length - 2)
    } else {
        # 无引号值: 剥空白引导的行内注释 ("val # 备注" → "val"; "a#b" 保留)
        $val = ($val -replace '\s+#.*$', '').Trim()
    }
    Set-Item -Path "Env:\$name" -Value $val
}
