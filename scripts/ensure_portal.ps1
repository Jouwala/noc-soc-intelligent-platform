# Portal_EnsureRunning 的计划任务改不了（权限不够），任务仍调用
# D:\01_RPA 里的 start_ensure.bat。那个 bat 转到本脚本。
# 门户目录名含中文，任务用的 ANSI/VBS 写不进路径，所以在这里按前缀查找。
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $root 'apps\my_app'))) {
    $root = 'D:\01_olbr_portal'
}
$hit = Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like 'liberia-noc_soc-*' } |
    Select-Object -First 1
if (-not $hit) { exit 2 }
$env:PATH = 'C:\Program Files\nodejs;' + $env:APPDATA + '\npm;' + $env:PATH
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $hit.FullName 'start_hidden.ps1') -Quiet
exit $LASTEXITCODE
