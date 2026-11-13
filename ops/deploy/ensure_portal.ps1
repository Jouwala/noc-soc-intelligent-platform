# Portal_EnsureRunning 的计划任务改不了（权限不够），任务仍调用
# D:\01_RPA 里的 start_ensure.bat。那个 bat 转到本脚本。
# 门户已固定在 apps\portal（纯 ASCII），直接定位，不再按前缀查找。
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath (Join-Path $root 'apps\my_app'))) {
    $root = 'D:\01_olbr_portal'
}
$portalDir = Join-Path $root 'apps\portal'
if (-not (Test-Path -LiteralPath $portalDir)) { exit 2 }
$env:PATH = 'C:\Program Files\nodejs;' + $env:APPDATA + '\npm;' + $env:PATH
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File (Join-Path $portalDir 'start_hidden.ps1') -Quiet
exit $LASTEXITCODE
