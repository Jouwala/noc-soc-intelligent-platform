# check_all.ps1 — 02 隔离副本离线质量检查（最小版，批 0）
# 只做四项；完整顺序见《NOC-SOC-Intelligent-Platform最终重构执行方案》第 7 节批 E。
# 必须在 D:\02_olbr_portal 根目录运行；不在 01、不在 RPA 里跑。
$ErrorActionPreference = 'Stop'

$Root = (Get-Location).Path

# 1. 当前目录是 02，且存在 ISOLATED_COPY.flag
if ($Root -notlike '*02_olbr_portal') {
    throw "当前目录不是 02（$Root），拒绝执行。"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'ISOLATED_COPY.flag'))) {
    throw "缺少 ISOLATED_COPY.flag，拒绝执行：当前目录可能不是隔离副本。"
}
Write-Host "[1/4] 目录是 02，ISOLATED_COPY.flag 存在"

# 2. .env 里的端口不是生产端口（80、443、5432、7038、7090、9267）
# 只比对端口赋值的值，不做子串匹配（否则 8080 会误命中 80、8443 会误命中 443）。
$ProdPorts = @('80', '443', '5432', '7038', '7090', '9267')
$EnvFile = Join-Path $Root '.env'
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "缺少 .env，拒绝执行。"
}
$bad = @()
foreach ($line in (Get-Content -LiteralPath $EnvFile)) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $value = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
    if ($value -in $ProdPorts) {
        $bad += $line.Trim()
    }
}
if ($bad.Count -gt 0) {
    throw ".env 用了生产端口：`n$($bad -join "`n")"
}
Write-Host "[2/4] .env 端口不在生产端口集合里"

# 3. compileall（批 0 时目录还是 Task\；批 A 之后由批 E 改成 apps）
python -m compileall -q Task scripts
if ($LASTEXITCODE -ne 0) { throw "compileall 失败" }
Write-Host "[3/4] compileall 通过"

# 4. ruff E9,F821（Task\my_app；批 A 之后由批 E 改成 apps\my_app）
Push-Location (Join-Path $Root 'Task\my_app')
try {
    python -m ruff check backend --select E9,F821
    if ($LASTEXITCODE -ne 0) { throw "ruff E9,F821 失败" }
} finally {
    Pop-Location
}
Write-Host "[4/4] ruff E9,F821 通过"

Write-Host "check_all（最小版）全部通过"
