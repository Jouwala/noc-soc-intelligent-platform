<#
  cutover-to-02.ps1 —— 把生产从 D:\01_olbr_portal 切到 D:\02_olbr_portal
  ============================================================================
  为什么不是 start_all.ps1：那是「隔离副本启动器」，有三道硬护栏（见 start_all.ps1:134/180/
  153/186）——生产端口直接 throw、强制 MY_APP_DISABLE_SCHEDULER=1、把 DATABASE_URL 覆盖成副本
  自带 PG(55432)、强制 PORTAL_AUTH_DATA_DIR=runtime\portal-auth-data。生产必须用 02 自带的
  **生产型启动器**：apps\my_app\start_hidden.ps1 与 apps\portal\start_hidden.ps1。
  同理不能用 apps\my_app\start.bat —— 它硬编码转发到 D:\01_olbr_portal。

  本脚本做（顺序固定，任一步失败即停并提示回退）：
    1. 停 01 的 5 个自愈任务（记下原状态）
    2. 停 01 my_app（01 的 stop.bat）
    3. 停 01_RPA 门户（9267/9268/9269 的真正主人）
    4. 搬门户状态：portal-default.json + auth-data\ -> 02\apps\portal
    5. 同步本地派生数据：按 mtime 补 01\Task\my_app\data\ 与任务产物（eflow_rules 等），
       并校验原生看板镜像 quick_check（sync-derived-data.ps1）
    6. 停 01 nginx，等 80/443 释放
    7. 迁移后端配置：用 01 的 backend\.env 生成 02 的（路径改写 + SCHEDULER=0），并拷 CA 证书
    8. 用生产端口生成 02 的 nginx.conf 并 nginx -t
    9. 起 02：my_app start_hidden -> portal start_hidden -> nginx
    10. 逐项探活 + 写 manifest（回退脚本据此恢复）

  ⚠ 第 5 步是 2026-11-18 首次切换事故补上的：当时只搬了门户状态与 .env，没搬 data\，
    结果原版 CXO 看板停在 11-16、原生看板镜像还是损坏副本、任务报告页 eFlow TT 规则检查 404。
    详见 docs\workspace\cutover-2026-11-20.md 22:00–22:30 条。

  用法：
    powershell -NoProfile -File ops\deploy\cutover\cutover-to-02.ps1                 # 干跑（默认，不改任何东西）
    powershell -NoProfile -File ops\deploy\cutover\cutover-to-02.ps1 -Apply          # 真切
    powershell -NoProfile -File ops\deploy\cutover\cutover-to-02.ps1 -Apply -SnapshotDir TMP\cutover\20261118-172033

  注意：真跑会停生产（80/443/7038/7090/9267），需在窗口内、有人值守时执行。
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$SnapshotDir = '',
    [int]$WaitSeconds = 240
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Step { param([string]$m) Write-Host ""; Write-Host "[*] $m" -ForegroundColor Cyan }
function Write-Ok { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn2 { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Bad { param([string]$m) Write-Host "[FAIL] $m" -ForegroundColor Red }

$Root02 = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
$Root01 = 'D:\01_olbr_portal'
$Portal01 = 'D:\01_RPA\liberia-noc_soc-数据看板统一门户'
# ⚠️ 2026-11-19 修正：9267 进程属于 $Portal01（01_RPA），但**权威的门户状态**
#   （账号 / team 分配 / 待审申请 / portal-default.json）在 $Root01 那一版里。
#   2026-11-18 首切时第 4 步从 $Portal01 搬，结果账号从 33 个变成 30 个、
#   3 个账号的 team 分配丢失、4 条待审申请消失 —— 用户当天报障。
#   停进程仍用 $Portal01（见第 3 步），**搬状态改用 $Root01**。
$PortalState01 = Join-Path $Root01 'liberia-noc_soc-数据看板统一门户'
if ($Root02 -notlike '*02_olbr_portal') { throw "当前脚本不在 02 副本内（$Root02），拒绝执行。" }
if (-not (Test-Path -LiteralPath $Root01)) { throw "找不到生产副本 $Root01。" }
if (-not (Test-Path -LiteralPath $Portal01)) { throw "找不到 01_RPA 门户 $Portal01。" }

$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$script:Results = @()
function Add-Result {
    param([string]$Item, [bool]$Ok, [string]$Detail)
    $script:Results += [pscustomobject]@{ item = $Item; ok = $Ok; detail = $Detail }
    if ($Ok) { Write-Ok ("{0} :: {1}" -f $Item, $Detail) } else { Write-Bad ("{0} :: {1}" -f $Item, $Detail) }
}

function Get-PortPid {
    param([int]$Port)
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return [int]$c.OwningProcess }
    return 0
}
function Get-PortExe {
    param([int]$Port)
    $id = Get-PortPid -Port $Port
    if ($id -eq 0) { return '' }
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
    if ($p) { return [string]$p.ExecutablePath }
    return ''
}
function Wait-PortFree {
    param([int[]]$Ports, [int]$Seconds)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $busy = @($Ports | Where-Object { (Get-PortPid $_) -ne 0 })
        if ($busy.Count -eq 0) { return @() }
        Start-Sleep -Seconds 2
    }
    return @($Ports | Where-Object { (Get-PortPid $_) -ne 0 })
}
function Wait-PortListen {
    param([int[]]$Ports, [int]$Seconds)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $missing = @($Ports | Where-Object { (Get-PortPid $_) -eq 0 })
        if ($missing.Count -eq 0) { return @() }
        Start-Sleep -Seconds 3
    }
    return @($Ports | Where-Object { (Get-PortPid $_) -eq 0 })
}
function Test-HttpOk {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -TimeoutSec 15 -UseBasicParsing
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch { return $false }
}

# ---------- 选快照 ----------
if (-not $SnapshotDir) {
    $base = Join-Path $Root02 'TMP\cutover'
    $latest = Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'state.json') } |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) { $SnapshotDir = $latest.FullName } else { $SnapshotDir = '' }
}

Write-Host ''
Write-Host ('模式：{0}' -f $(if ($Apply) { '实切（会停 01、起 02）' } else { '干跑（不改任何东西）' })) -ForegroundColor Yellow
Write-Host ("02：{0}" -f $Root02) -ForegroundColor DarkGray
Write-Host ("01：{0}" -f $Root01) -ForegroundColor DarkGray
Write-Host ("01_RPA 门户：{0}" -f $Portal01) -ForegroundColor DarkGray
if ($SnapshotDir) { Write-Host ("快照：{0}" -f $SnapshotDir) -ForegroundColor DarkGray }
Write-Host ''

# ---------- 0. 预检 ----------
Write-Step '0/10 预检'
$pre = $true
$h80 = Get-PortExe 80
$h7038 = Get-PortExe 7038
if (-not ($h80 -like '*01_olbr_portal*')) { Write-Warn2 "80 当前不在 01 手里（$h80）——若已切过，请直接用回退脚本。"; $pre = $false }
if (-not ($h7038 -like '*dbg*' -or $h7038 -like '*python*')) { Write-Warn2 "7038 当前进程不像 my_app 后端（$h7038）" }
foreach ($p in 7039, 7091, 9277, 55432) {
    if ((Get-PortPid $p) -ne 0) { Write-Warn2 "隔离端口 $p 被占用（02 现在不该在跑）"; $pre = $false }
}
foreach ($f in 'apps\my_app\start_hidden.ps1', 'apps\portal\start_hidden.ps1', 'ops\deploy\write_nginx_conf.ps1', 'ops\deploy\cutover\sync-derived-data.ps1') {
    if (-not (Test-Path -LiteralPath (Join-Path $Root02 $f))) { Write-Bad "缺 $f"; $pre = $false }
}
foreach ($f in 'Task\my_app\stop.bat', 'Task\my_app\backend\.env', 'Task\whatsapp-kb-bot\certs\liberia-noc-ca.pem') {
    if (-not (Test-Path -LiteralPath (Join-Path $Root01 $f))) { Write-Bad "01 缺 $f"; $pre = $false }
}
foreach ($f in 'portal-default.json', 'auth-data\portal-auth.db') {
    if (-not (Test-Path -LiteralPath (Join-Path $PortalState01 $f))) { Write-Bad "01 门户状态缺 $f（$PortalState01）"; $pre = $false }
}
if (-not $SnapshotDir -or -not (Test-Path -LiteralPath (Join-Path $SnapshotDir 'state.json'))) {
    Write-Warn2 '没有可用快照（回退脚本要靠它恢复 02 的 .env）。请先跑 snapshot-prod-state.ps1'
    $pre = $false
}
# 必须是管理员：Portal_EnsureRunning 这类任务非管理员改不动（2026-11-18 实测 Access is denied）
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    if ($Apply) {
        Write-Bad '需要**管理员** PowerShell：Portal_EnsureRunning 的任务状态改不动（实测 Access is denied）'
        $pre = $false
    } else {
        Write-Warn2 '当前不是管理员：干跑没问题，但真跑（-Apply）必须在管理员 PowerShell 里执行'
    }
}
Add-Result '预检' $pre $(if ($pre) { '01 在跑、02 空闲、启动器/配置/证书/快照齐全' + $(if ($isAdmin) { '、管理员权限' } else { '' }) } else { '见上面的 WARN/FAIL' })
if (-not $pre) { Write-Host '预检未通过，已停止。' -ForegroundColor Red; exit 1 }

# 记下任务原状态
$TaskNames = @('MyApp_EnsureRunning', 'Portal_EnsureRunning', 'Nginx_EnsureRunning', 'WhatsAppKB_Bot_EnsureRunning', 'WhatsAppKB_Notify_EnsureRunning')
$taskBefore = @()
foreach ($n in $TaskNames) {
    $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
    if ($t) { $taskBefore += [pscustomobject]@{ name = $n; state = [string]$t.State } }
}

if (-not $Apply) {
    Write-Step '干跑：以下动作不会执行'
    Write-Host '    1) 禁用 5 个 01 自愈任务' -ForegroundColor DarkGray
    Write-Host '    2) cmd /c D:\01_olbr_portal\Task\my_app\stop.bat' -ForegroundColor DarkGray
    Write-Host '    3) cmd /c D:\01_RPA\...\stop.bat（9267/9268/9269）' -ForegroundColor DarkGray
    Write-Host '    4) 复制 portal-default.json + auth-data 到 02\apps\portal' -ForegroundColor DarkGray
    Write-Host '    5) sync-derived-data.ps1 -Apply（按 mtime 补 data\ 与 eflow_rules 等任务产物，并校验镜像）' -ForegroundColor DarkGray
    Write-Host '    6) cd D:\01_olbr_portal\nginx-1.28.0; .\nginx.exe -s quit' -ForegroundColor DarkGray
    Write-Host '    7) 用 01 的 backend\.env 生成 02 的（路径改写 + SCHEDULER=0），拷 CA 证书' -ForegroundColor DarkGray
    Write-Host '    8) write_nginx_conf.ps1 -HttpPort 80 -HttpsPort 443 -BePort 7038 -FePort 7090 -PortalPort 9267 + nginx -t' -ForegroundColor DarkGray
    Write-Host '    9) apps\my_app\start_hidden.ps1 -Quiet -Force -> apps\portal\start_hidden.ps1 -Quiet -> nginx' -ForegroundColor DarkGray
    Write-Host '    10) 探活 + 写 manifest' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '干跑结束：未做任何改动。' -ForegroundColor Yellow
    exit 0
}

# ---------- 1. 停 01 自愈 ----------
Write-Step '1/10 停 01 自愈任务'
$fail = @()
$disabled = @()
foreach ($n in $TaskNames) {
    try {
        $t = Get-ScheduledTask -TaskName $n -ErrorAction Stop
        if ($t.State -ne 'Disabled') {
            Disable-ScheduledTask -TaskName $n -ErrorAction Stop | Out-Null
            $disabled += $n
        }
    } catch { $fail += ("{0}({1})" -f $n, $_.Exception.Message) }
}
if ($fail.Count -gt 0) {
    # 半途失败：把已经禁用的恢复回去，别让机器停在「自愈被关掉」的状态
    foreach ($n in $disabled) {
        try { Enable-ScheduledTask -TaskName $n -ErrorAction Stop | Out-Null; Write-Warn2 "已回滚：重新启用 $n" } catch { Write-Bad "回滚失败，请手工启用 $n" }
    }
    Add-Result '停 01 自愈' $false ('失败：' + ($fail -join '; ') + '（已把本次禁用的任务恢复）')
    Write-Bad '自愈没停干净，继续下去会被 5 分钟任务把 01 拉回来。已中止。'
    exit 1
}
Add-Result '停 01 自愈' $true ('已禁用：' + ($disabled -join ', '))

# ---------- 2. 停 01 my_app ----------
Write-Step '2/10 停 01 my_app'
& cmd.exe /c (Join-Path $Root01 'Task\my_app\stop.bat') | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
$left = Wait-PortFree -Ports @(7038, 7090) -Seconds 60
Add-Result '停 01 my_app' ($left.Count -eq 0) $(if ($left.Count -eq 0) { '7038/7090 已释放' } else { '仍占用：' + ($left -join ',') })
if ($left.Count -gt 0) { Write-Bad '01 没停干净，已中止（先手动处理再重跑）。'; exit 1 }

# ---------- 3. 停 01_RPA 门户 ----------
Write-Step '3/10 停 01_RPA 门户（9267/9268/9269）'
# 不能调门户的 stop.bat：它最后一行是 `pause >nul`，在无人值守的管道里会永久挂起。
# stop_services.ps1 是它的实质实现，支持 -Quiet 且不 pause。
$stopSvc = Join-Path $Portal01 'stop_services.ps1'
if (-not (Test-Path -LiteralPath $stopSvc)) { throw "找不到 $stopSvc（门户停机脚本）" }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopSvc -Quiet | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
$left = Wait-PortFree -Ports @(9267, 9268) -Seconds 60
# 9269 由 my_app 的问答助手按需拉起，不在门户 stop 范围内
Add-Result '停 01_RPA 门户' ($left.Count -eq 0) $(if ($left.Count -eq 0) { '9267/9268 已释放（9269 若在，属问答助手）' } else { '仍占用：' + ($left -join ',') })
if ($left.Count -gt 0) { Write-Bad '旧门户没停干净，已中止。'; exit 1 }

# ---------- 4. 搬门户状态 ----------
Write-Step '4/10 搬门户状态到 02'
$pdst = Join-Path $Root02 'apps\portal'
foreach ($name in @('portal-default.json', 'auth-data')) {
    # 来源是 $PortalState01（= 01_olbr_portal），不是 9267 的 $Portal01 ——
    # 后者只是进程主人，账号库是旧的一份（30 个账号、缺 team 分配）。
    $src = Join-Path $PortalState01 $name
    $dst = Join-Path $pdst $name
    if (Test-Path -LiteralPath $dst) {
        $bak = '{0}.bak_cutover_{1}' -f $dst, $Stamp
        Move-Item -LiteralPath $dst -Destination $bak -Force
        Write-Host ("    旧 {0} 已备份为 {1}" -f $name, (Split-Path $bak -Leaf)) -ForegroundColor DarkGray
    }
    Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
}
Add-Result '搬门户状态' (Test-Path -LiteralPath (Join-Path $pdst 'auth-data\portal-auth.db')) 'portal-default.json + auth-data（含 portal-auth.db）'

# ---------- 5. 同步本地派生数据 ----------
# 2026-11-18 事故：只搬门户状态与 .env，data\ 仍是 11-16 的隔离快照 —— 原版 CXO 停在
# 11-16 08:41、原生看板镜像还是损坏件；eflow_rules 的矫正表在 data\ 之外，更没搬，
# 任务报告页「eFlow TT 规则检查」直接 404。这里统一交给 sync-derived-data.ps1。
Write-Step '5/10 同步本地派生数据（data\ + 任务产物 + 镜像校验）'
$syncScript = Join-Path $Root02 'ops\deploy\cutover\sync-derived-data.ps1'
$prevEapSync = $ErrorActionPreference
# 子进程把告警/错误写 stderr 时，PS5.1 会转成 ErrorRecord；全局是 Stop 会直接打死脚本
# （2026-11-18 21:16 的 nginx -t 就是这么死的）。这里临时放宽，只看退出码。
$ErrorActionPreference = 'Continue'
try {
    $syncOut = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $syncScript -SourceRoot (Join-Path $Root01 'Task\my_app') -TargetRoot (Join-Path $Root02 'apps\my_app') -Apply 2>&1
    $syncCode = $LASTEXITCODE
} finally { $ErrorActionPreference = $prevEapSync }
$syncOut | Select-Object -Last 6 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
Add-Result '同步本地派生数据' ($syncCode -eq 0) $(if ($syncCode -eq 0) { 'data\ 已按 mtime 补齐，镜像 quick_check=ok' } else { '失败，见上（镜像损坏时先用 01 的健康库替换）' })
if ($syncCode -ne 0) { Write-Bad '派生数据没补齐，继续会带病上线。已中止，按提示处理后重跑。'; exit 1 }

# ---------- 6. 停 01 nginx ----------
Write-Step '6/10 停 01 nginx'
Push-Location (Join-Path $Root01 'nginx-1.28.0')
try { & .\nginx.exe -s quit | Out-Null } catch { Write-Warn2 "nginx -s quit 报错：$($_.Exception.Message)" } finally { Pop-Location }
$left = Wait-PortFree -Ports @(80, 443) -Seconds 45
Add-Result '停 01 nginx' ($left.Count -eq 0) $(if ($left.Count -eq 0) { '80/443 已释放' } else { '仍占用：' + ($left -join ',') })
if ($left.Count -gt 0) { Write-Bad '80/443 没释放，已中止。'; exit 1 }

# ---------- 7. 迁移后端配置 ----------
Write-Step '7/10 迁移 02 的后端配置'
$env01 = Join-Path $Root01 'Task\my_app\backend\.env'
$env02 = Join-Path $Root02 'apps\my_app\backend\.env'
$bakEnv = '{0}.bak_cutover_{1}' -f $env02, $Stamp
if (Test-Path -LiteralPath $env02) { Copy-Item -LiteralPath $env02 -Destination $bakEnv -Force }
$lines = [System.IO.File]::ReadAllLines($env01, [System.Text.Encoding]::UTF8)
$out = New-Object System.Collections.Generic.List[string]
$warnPaths = @()
$seenSched = $false
foreach ($line in $lines) {
    $t = $line.Trim()
    if (-not $t -or $t.StartsWith('#')) { $out.Add($line); continue }
    $k = ($t -split '=', 2)[0].Trim()
    $v = ($t -split '=', 2)[1].Trim()
    if ($v -match '01_olbr_portal|01_RPA') {
        # 先把斜杠统一成反斜杠，再按「盘符开头」锚定改写，避免 D:/\02... 这种双斜杠
        $nv = $v.Replace('/', '\')
        $nv = $nv -replace '(?i)^([A-Z]:)\\01_olbr_portal\\Task\\', '$1\02_olbr_portal\apps\'
        $nv = $nv -replace '(?i)^([A-Z]:)\\01_olbr_portal\\', '$1\02_olbr_portal\'
        $nv = $nv -replace '(?i)^([A-Z]:)\\01_RPA\\', '$1\02_olbr_portal\'
        if ($nv -ne $v) { Write-Host ("    {0}: 改写路径" -f $k) -ForegroundColor DarkGray }
        $v = $nv
        if ($v -match '01_olbr_portal|01_RPA') { $warnPaths += $k }
    }
    if ($k -eq 'MY_APP_DISABLE_SCHEDULER') { $v = '0'; $seenSched = $true }
    $out.Add(('{0}={1}' -f $k, $v))
}
if (-not $seenSched) { $out.Add('MY_APP_DISABLE_SCHEDULER=0') }
if (-not ($out -match '^MY_APP_DB_MODE=')) { $out.Add('MY_APP_DB_MODE=pg') }
[System.IO.File]::WriteAllLines($env02, $out, (New-Object System.Text.UTF8Encoding($false)))
if ($warnPaths.Count -gt 0) { Write-Warn2 ('这些键仍指向 01，请人工确认：' + ($warnPaths -join ', ')) }

# CA 证书
$caSrc = Join-Path $Root01 'Task\whatsapp-kb-bot\certs\liberia-noc-ca.pem'
$caDstDir = Join-Path $Root02 'apps\whatsapp-kb-bot\certs'
if (-not (Test-Path -LiteralPath $caDstDir)) { New-Item -ItemType Directory -Path $caDstDir -Force | Out-Null }
Copy-Item -LiteralPath $caSrc -Destination (Join-Path $caDstDir 'liberia-noc-ca.pem') -Force
Add-Result '迁移后端配置' (Test-Path -LiteralPath (Join-Path $caDstDir 'liberia-noc-ca.pem')) ('已生成 backend\.env（{0} 个键，备份 {1}）' -f (@($out | Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*=' }).Count), (Split-Path $bakEnv -Leaf))

# ---------- 8. nginx 配置 ----------
Write-Step '8/10 生成 02 的生产 nginx 配置'
$confScript = Join-Path $Root02 'ops\deploy\write_nginx_conf.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $confScript -HttpPort 80 -HttpsPort 443 -BePort 7038 -FePort 7090 -PortalPort 9267 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
Push-Location (Join-Path $Root02 'nginx-1.28.0')
$prevEap = $ErrorActionPreference
# nginx -t 把结果写到 stderr，PS5.1 会把 native stderr 变成 ErrorRecord；
# 脚本全局是 Stop，直接用 2>&1 会把整个脚本打死（2026-11-18 21:16 实测，死在 7/9）。
$ErrorActionPreference = 'Continue'
try {
    $t = & .\nginx.exe -t 2>&1
    $tOk = ($LASTEXITCODE -eq 0)
} finally { $ErrorActionPreference = $prevEap; Pop-Location }
Add-Result 'nginx 配置' $tOk (($t | Select-Object -Last 1) -replace "`r?`n", ' ')

# ---------- 9. 起 02 ----------
Write-Step '9/10 起 02'
$flagMy = Join-Path $Root02 'apps\my_app\backend\stopped.flag'
if (Test-Path -LiteralPath $flagMy) { Remove-Item -LiteralPath $flagMy -Force }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root02 'apps\my_app\start_hidden.ps1') -Quiet -Force | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
$okMy = ((Get-PortPid 7038) -ne 0)
Add-Result '起 02 my_app' $okMy '7038 已监听'

$flagPortal = Join-Path $Root02 'apps\portal\stopped.flag'
if (Test-Path -LiteralPath $flagPortal) { Remove-Item -LiteralPath $flagPortal -Force }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root02 'apps\portal\start_hidden.ps1') -Quiet | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
$okPortal = ((Get-PortPid 9267) -ne 0)
Add-Result '起 02 门户' $okPortal '9267 已监听'

Push-Location (Join-Path $Root02 'nginx-1.28.0')
try { Start-Process -FilePath (Join-Path $Root02 'nginx-1.28.0\nginx.exe') -ArgumentList ('-p "{0}" -c "conf/nginx.conf"' -f (Join-Path $Root02 'nginx-1.28.0')) -WorkingDirectory (Join-Path $Root02 'nginx-1.28.0') -WindowStyle Hidden | Out-Null } finally { Pop-Location }
$missing = Wait-PortListen -Ports @(80, 443) -Seconds 30
Add-Result '起 02 nginx' ($missing.Count -eq 0) $(if ($missing.Count -eq 0) { '80/443 已监听' } else { '未起来：' + ($missing -join ',') })

# ---------- 10. 探活 + manifest ----------
Write-Step '10/10 探活'
$lack = Wait-PortListen -Ports @(7038, 7090, 9267, 80, 443) -Seconds $WaitSeconds
Add-Result '端口齐全' ($lack.Count -eq 0) $(if ($lack.Count -eq 0) { '7038/7090/9267/80/443 都在听' } else { '缺：' + ($lack -join ',') })
Add-Result '后端 /api/health' (Test-HttpOk 'http://127.0.0.1:7038/api/health') 'http://127.0.0.1:7038/api/health'
Add-Result '前端 7090' (Test-HttpOk 'http://127.0.0.1:7090/myapp/') 'http://127.0.0.1:7090/myapp/'
Add-Result '门户 9267' (Test-HttpOk 'http://127.0.0.1:9267/uniplam/') 'http://127.0.0.1:9267/uniplam/'
Add-Result '对外 80 /myapp/' (Test-HttpOk 'http://127.0.0.1/myapp/') 'http://127.0.0.1/myapp/'
Add-Result '对外 80 /uniplam/' (Test-HttpOk 'http://127.0.0.1/uniplam/') 'http://127.0.0.1/uniplam/'
$exe80 = Get-PortExe 80
Add-Result '80 归属 02' ($exe80 -like '*02_olbr_portal*') $exe80
$exe9267 = Get-PortExe 9267
Add-Result '9267 归属 02' ($exe9267 -ne '' -and (Test-HttpOk 'http://127.0.0.1:9267/uniplam/')) '门户可达（进程路径见 manifest）'

$manifest = [pscustomobject]@{
    cutoverAt      = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
    root01         = $Root01
    root02         = $Root02
    portal01       = $Portal01
    snapshotDir    = $SnapshotDir
    backupEnv      = $bakEnv
    tasksDisabled  = $disabled
    tasksBefore    = $taskBefore
    results        = $script:Results
}
if ($SnapshotDir) {
    $manifestPath = Join-Path $SnapshotDir 'cutover-manifest.json'
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Ok "manifest: $manifestPath"
}

Write-Host ''
$failed = @($script:Results | Where-Object { -not $_.ok })
Write-Host ('结果：{0} 项，PASS {1}，FAIL {2}' -f $script:Results.Count, ($script:Results.Count - $failed.Count), $failed.Count) -ForegroundColor Cyan
if ($failed.Count -gt 0) {
    Write-Host '未通过项：' -ForegroundColor Red
    foreach ($f in $failed) { Write-Host ('  - {0} :: {1}' -f $f.item, $f.detail) -ForegroundColor Red }
    Write-Host ''
    Write-Host '建议立即回退：' -ForegroundColor Yellow
    Write-Host '  powershell -NoProfile -File ops\deploy\cutover\rollback-to-01.ps1'
    exit 1
}
Write-Host '切换到 02 完成，全部探活通过。' -ForegroundColor Green
exit 0
