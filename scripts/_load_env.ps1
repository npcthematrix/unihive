# Load .env into current process env (KEY=VALUE lines, ignore # and blanks)
# Usage: . "$PSScriptRoot\_load_env.ps1"

param(
    [string]$EnvPath = (Join-Path $PSScriptRoot "..\.env")
)

$EnvPath = [System.IO.Path]::GetFullPath($EnvPath)

if (-not (Test-Path $EnvPath)) {
    Write-Host "[warn] .env not found at $EnvPath - relying on existing process env" -ForegroundColor Yellow
    return
}

Get-Content $EnvPath | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $kv = $line -split "=", 2
    if ($kv.Length -ne 2) { return }
    $name = $kv[0].Trim()
    $val = $kv[1].Trim().Trim('"').Trim("'")
    if ($name -match '^[A-Za-z_][A-Za-z0-9_]*$') {
        Set-Item -Path "Env:\$name" -Value $val
    }
}
