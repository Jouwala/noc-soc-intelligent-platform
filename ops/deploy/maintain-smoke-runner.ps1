<#
  maintain-smoke-runner.ps1 —— 冒烟 runner（<项目>\.smoke）的例行维护
  ============================================================================
  为什么需要它（2026-11-20）：

    冒烟 runner 的数据是「铺装那一刻」从生产复制过来的，之后不再更新。生产
    在设置页新增一条凭据后，runner 的 credentials 仍停在旧行数，而
    ops\smoke\smoke.ps1 §8.2 要求「三张本地权威表行数与源一致」——于是所有
    PR 的冒烟都会因为**数据落后**而失败，看起来像代码坏了。

    2026-11-19/20 实测（PR #182）：代码 bug 修好以后，冒烟又卡在
    credentials source=21 target=20；手工 migrate 一次才通过。这不该靠人记得。

  维护内容（四项，可按需组合）：

    -SyncData     把生产 app.db 的三张本地权威表同步进 runner（只读生产、只写
                  runner；行数一致时不做任何事，避免每次备份）。
    -UpdateCode   让 runner 的代码跟进 platform/main（工作树干净且无冒烟锁时才动）。
    -CleanTmp     清理 runner TMP\ 下超过 N 天的冒烟日志/报告/迁移备份；
                  watcher 日志、状态、锁、pid 永不删。
    -Reprovision  调 provision-smoke-runner.ps1 -Apply 修补 junction/配置/构建产物；
                  加 -RebuildDb 时按生产 schema + 三表重建 runner 的 app.db。

  接线：
    · smoke-local.ps1 在真正冒烟前调用 -SyncData（失败不挡冒烟），保证 §8.2
      不会因为生产新增凭据而误报。
    · 计划任务 OLBR_SmokeMaintenance（每天 04:10）跑 -Daily；
      OLBR_SmokeReprovision（每周日 04:40）跑 -Reprovision -SyncData -CleanTmp。
    · 冒烟锁 TMP\smoke.lock 存在时，除 -Status 外一律跳过本轮（不和服务抢）；
      只有「自己就是持锁方」的调用（smoke-local 在冒烟前同步）才用 -IgnoreLock 放行。

  用法（在项目根，或任意目录；脚本按自身位置推导）：
    powershell -NoProfile -File ops\deploy\maintain-smoke-runner.ps1                 # 干跑，只打印计划
    powershell -NoProfile -File ops\deploy\maintain-smoke-runner.ps1 -Apply          # 同步数据 + 清理
    powershell -NoProfile -File ops\deploy\maintain-smoke-runner.ps1 -Apply -Daily   # 数据 + 代码 + 清理
    powershell -NoProfile -File ops\deploy\maintain-smoke-runner.ps1 -Apply -Reprovision [-RebuildDb]
    powershell -NoProfile -File ops\deploy\maintain-smoke-runner.ps1 -Status
    powershell -NoProfile -File ops\deploy\maintain-smoke-runner.ps1 -InstallTask [-Start]

  安全：只动 <项目>\.smoke；生产 01 目录、非 runner 目录、runner == 项目根一律拒绝。
        不启动/停止任何服务，不打印任何口令。
#>
[CmdletBinding()]
param(
    [string]$RunnerPath = '',
    [switch]$Apply,
    [switch]$SyncData,
    [switch]$UpdateCode,
    [switch]$Reprovision,
    [switch]$RebuildDb,
    [switch]$CleanTmp,
    [int]$CleanTmpDays = 7,
    [switch]$Daily,
    [switch]$Status,
    [switch]$InstallTask,
    [switch]$Start,
    [switch]$IgnoreLock,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ---------------------------------------------------------------- 路径推导
# 本脚本既可能在项目根（D:\02_olbr_portal\ops\deploy）被执行，也可能在 runner
# 自己（D:\02_olbr_portal\.smoke\ops\deploy）里被执行 —— 后者是 smoke-local 调用
# 的路径。两种位置都要能正确找到「项目根」与「runner」。
$TwoUp = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if ((Split-Path $TwoUp -Leaf) -eq '.smoke') {
    $Runner = $TwoUp
    $ProjectRoot = (Split-Path $Runner -Parent)
} else {
    $ProjectRoot = $TwoUp
    $Runner = $(if ($RunnerPath) { [IO.Path]::GetFullPath($RunnerPath) } else { Join-Path $ProjectRoot '.smoke' })
}
$Runner = [IO.Path]::GetFullPath($Runner).TrimEnd('\')
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')

if ($ProjectRoot -like 'D:\01_olbr_portal*' -or $Runner -like 'D:\01_olbr_portal*') {
    throw "拒绝执行：路径落在生产 01 目录（project=$ProjectRoot runner=$Runner）。本脚本只服务 02 的冒烟副本。"
}
if ($Runner -eq $ProjectRoot) {
    throw "冒烟 runner 不能就是项目根（会误改生产树）：$Runner"
}
if (-not $Runner.StartsWith($ProjectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "冒烟 runner 必须落在项目内（$ProjectRoot 下面），收到：$Runner"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'apps\my_app\backend\main.py'))) {
    throw "项目根不像本项目（缺 apps\my_app\backend\main.py）：$ProjectRoot"
}

$TmpDir = Join-Path $Runner 'TMP'
$LogFile = Join-Path $TmpDir 'smoke-maintenance.log'
$LockFile = Join-Path $TmpDir 'smoke.lock'

function Write-MaintLog {
    param([string]$Message, [string]$Level = 'INFO')
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    if (-not $Quiet -or $Level -ne 'INFO') { Write-Host $line }
    try {
        if (-not (Test-Path -LiteralPath $TmpDir)) { New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null }
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch { }
}

function Write-Step { param([string]$Message) Write-MaintLog $Message }
function Write-Warn { param([string]$Message) Write-MaintLog $Message 'WARN' }
function Write-Bad { param([string]$Message) Write-MaintLog $Message 'FAIL' }

function Invoke-Git {
    param([string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & git -C $Runner @GitArgs 2>&1
        $code = $LASTEXITCODE
        return @{ Out = (($out | ForEach-Object { "$_" }) -join "`n").Trim(); Code = $code }
    } finally { $ErrorActionPreference = $prev }
}

# 幂等：先确认 runner 是 runner，再动手。
function Assert-Runner {
    if (-not (Test-Path -LiteralPath $Runner)) {
        throw "runner 不存在：$Runner（先跑 ops\deploy\provision-smoke-runner.ps1 -Apply）"
    }
    foreach ($marker in @('.smoke-runner', 'ISOLATED_COPY.flag')) {
        if (-not (Test-Path -LiteralPath (Join-Path $Runner $marker))) {
            throw "runner 缺 $marker：$Runner（不是冒烟副本，拒绝维护）"
        }
    }
}

function Test-SmokeBusy {
    return (Test-Path -LiteralPath $LockFile)
}

# ---------------------------------------------------------------- 步骤 1：同步本地权威表
function Invoke-SyncData {
    Write-Step '--- 同步本地权威表（生产 app.db -> runner app.db）---'
    $tool = Join-Path $Runner 'ops\deploy\local_authority_tools.py'
    $src = Join-Path $ProjectRoot 'apps\my_app\db\app.db'
    $tgt = Join-Path $Runner 'apps\my_app\db\app.db'
    $py = Join-Path $Runner 'runtime\python312\python.exe'
    if (-not (Test-Path -LiteralPath $py)) { $py = Join-Path $ProjectRoot 'runtime\python312\python.exe' }

    if (-not (Test-Path -LiteralPath $tool)) { throw "runner 缺 local_authority_tools.py：$tool" }
    if (-not (Test-Path -LiteralPath $py)) { throw "找不到 Python：$py" }
    if (-not (Test-Path -LiteralPath $src)) { throw "找不到生产 app.db：$src" }
    if (-not (Test-Path -LiteralPath $tgt)) { throw "找不到 runner app.db：$tgt" }

    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = & $py $tool check --json --source $src --target $tgt 2>&1
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
    if ($code -ne 0 -and $code -ne 2) {
        throw "local_authority_tools check 失败（exit=$code）：$((@($raw) | Select-Object -Last 3) -join ' | ')"
    }

    $jsonLine = @($raw | ForEach-Object { "$_" } | Where-Object { $_.Trim().StartsWith('{') } | Select-Object -Last 1)
    if (-not $jsonLine) { throw "local_authority_tools check 没有输出 JSON，无法判断行数" }
    $info = $jsonLine | ConvertFrom-Json

    $drift = @()
    $bits = @()
    foreach ($p in @($info.info.tables.PSObject.Properties)) {
        $s = [int]$p.Value.source_rows
        $t = [int]$p.Value.target_rows
        $bits += ("{0} source={1} target={2}" -f $p.Name, $s, $t)
        if ($s -gt 0 -and $t -ne $s) { $drift += $p.Name }
    }
    Write-Step ("行数：" + ($bits -join '; '))

    if ($drift.Count -eq 0) {
        Write-Step '已是最新，无需迁移。'
        return $true
    }

    Write-Step ("落后：" + ($drift -join ', ') + "，执行 migrate（只写 runner，生产只读）")
    if (-not $Apply) { Write-Step '<干跑> 将执行 local_authority_tools.py migrate'; return $true }

    # 备份目录必须显式指定：local_authority_tools 的默认推导会落到 <target>\..\..\..\TMP
    # （= apps\TMP，通常不存在），再退回 Path.cwd()/TMP —— 计划任务的 cwd 是 System32，
    # 备份会写不进去或散落在奇怪的地方。这里固定在 runner TMP\maintenance-backup。
    $backupDir = Join-Path $TmpDir 'maintenance-backup'
    if (-not (Test-Path -LiteralPath $backupDir)) { New-Item -ItemType Directory -Force -Path $backupDir | Out-Null }

    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $py $tool migrate --source $src --target $tgt --backup-dir $backupDir 2>&1
        $mcode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
    foreach ($ln in @($out)) { Write-Step "  $ln" }
    if ($mcode -ne 0) { throw "migrate 失败（exit=$mcode），见上方输出" }
    Write-Step '同步完成。'
    return $true
}

# ---------------------------------------------------------------- 步骤 2：跟进代码
function Invoke-UpdateCode {
    Write-Step '--- runner 代码跟进 platform/main ---'
    $head = (Invoke-Git @('rev-parse', '--short', 'HEAD'))
    if ($head.Code -ne 0) { throw "读 runner HEAD 失败：$($head.Out)" }
    $dirty = (Invoke-Git @('status', '--porcelain', '--untracked-files=no'))
    if ($dirty.Code -ne 0) { throw "runner git status 失败：$($dirty.Out)" }
    if ($dirty.Out) {
        Write-Warn "runner 工作树有已跟踪改动用，跳过代码更新：$($dirty.Out -replace "`r?`n", ' | ')"
        return $true
    }

    $fetch = (Invoke-Git @('fetch', 'platform', 'main'))
    if ($fetch.Code -ne 0) {
        Write-Warn "fetch platform main 失败，跳过代码更新：$($fetch.Out)"
        return $true
    }
    $target = (Invoke-Git @('rev-parse', '--short', 'platform/main'))
    if ($target.Code -ne 0) { throw "读 platform/main 失败：$($target.Out)" }

    if ($head.Out -eq $target.Out) {
        Write-Step "已是 platform/main（$($head.Out)）。"
        return $true
    }
    if (-not $Apply) {
        Write-Step ("<干跑> 将 checkout --detach platform/main：{0} -> {1}" -f $head.Out, $target.Out)
        return $true
    }
    $co = (Invoke-Git @('checkout', '-q', '--detach', 'platform/main'))
    if ($co.Code -ne 0) { throw "checkout platform/main 失败：$($co.Out)" }
    Write-Step ("代码已更新：{0} -> {1}" -f $head.Out, $target.Out)
    return $true
}

# ---------------------------------------------------------------- 步骤 3：清理 TMP
function Invoke-CleanTmp {
    Write-Step ("--- 清理 runner TMP（>{0} 天）---" -f $CleanTmpDays)
    if (-not (Test-Path -LiteralPath $TmpDir)) { Write-Step 'TMP 不存在，跳过。'; return $true }
    $cutoff = (Get-Date).AddDays(-$CleanTmpDays)
    # 只清「冒烟一次性产物」；watcher 日志/状态/pid、smoke.lock 与维护日志永不匹配。
    $patterns = @(
        'smoke_pytest_*', 'smoke_headless_*', 'smoke_result_*', 'smoke-pr-*',
        'smoke_*_start_all.*', 'smoke_*_stop_all.*',
        'smoke_*_portal_build.*', 'smoke_*_fe_build.*',
        'portable-*.out', 'portable-*.err', 'portable-*.log',
        '_bak_local_authority_*.json'
    )
    $victims = @()
    $dirs = @($TmpDir, (Join-Path $TmpDir 'maintenance-backup')) | Where-Object { Test-Path -LiteralPath $_ }
    foreach ($dir in $dirs) {
        foreach ($f in @(Get-ChildItem -LiteralPath $dir -File -ErrorAction SilentlyContinue)) {
            if ($f.LastWriteTime -ge $cutoff) { continue }
            foreach ($pat in $patterns) {
                if ($f.Name -like $pat) { $victims += $f; break }
            }
        }
    }
    if ($victims.Count -eq 0) { Write-Step '没有需要清理的文件。'; return $true }
    if (-not $Apply) {
        Write-Step ("<干跑> 将删除 {0} 个文件，最早 {1:yyyy-MM-dd HH:mm}" -f $victims.Count, ($victims | Sort-Object LastWriteTime | Select-Object -First 1).LastWriteTime)
        return $true
    }
    $freed = 0
    foreach ($f in $victims) {
        $freed += $f.Length
        Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue
    }
    Write-Step ("已删除 {0} 个文件，释放 {1:N1} MB" -f $victims.Count, ($freed / 1MB))
    return $true
}

# ---------------------------------------------------------------- 步骤 4：重新铺装
function Invoke-Reprovision {
    Write-Step '--- 重新铺装 runner（provision-smoke-runner.ps1 -Apply）---'
    $prov = Join-Path $ProjectRoot 'ops\deploy\provision-smoke-runner.ps1'
    if (-not (Test-Path -LiteralPath $prov)) { throw "找不到铺装脚本：$prov" }
    if (-not $Apply) {
        $extra = $(if ($RebuildDb) { ' -RebuildDb' } else { '' })
        Write-Step ("<干跑> 将执行 provision-smoke-runner.ps1 -Apply -SkipHeadUpdate{0}" -f $extra)
        return $true
    }
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $prov, '-Apply', '-SkipHeadUpdate', '-RunnerPath', $Runner)
    if ($RebuildDb) { $argList += '-RebuildDb' }
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" @argList
    if ($LASTEXITCODE -ne 0) { throw "重新铺装失败（exit=$LASTEXITCODE）" }
    Write-Step '重新铺装完成。'
    return $true
}

# ---------------------------------------------------------------- 计划任务
function Install-MaintTask {
    $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # 计划任务固定用「项目根」那份，而不是 runner 里随 PR 检出的副本：
    # runner 可能正被 watcher 切到某个 PR head，那份脚本不一定存在/稳定。
    $scriptPath = Join-Path $ProjectRoot 'ops\deploy\maintain-smoke-runner.ps1'
    if (-not (Test-Path -LiteralPath $scriptPath)) { throw "找不到项目根下的维护脚本：$scriptPath" }
    $userId = "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)

    $specs = @(
        @{
            Name = 'OLBR_SmokeMaintenance'
            Desc = 'D:\02 冒烟 runner 例行维护：同步本地权威表 + 跟进 main + 清理 TMP（零 token）'
            Args = ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -Daily -Apply -Quiet' -f $scriptPath)
            Trigger = (New-ScheduledTaskTrigger -Daily -At '04:10')
        },
        @{
            Name = 'OLBR_SmokeReprovision'
            Desc = 'D:\02 冒烟 runner 每周重新铺装：修补 junction/配置/构建产物 + 同步数据 + 清理'
            Args = ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -Reprovision -SyncData -CleanTmp -Apply -Quiet' -f $scriptPath)
            Trigger = (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '04:40')
        }
    )
    foreach ($s in $specs) {
        $action = New-ScheduledTaskAction -Execute $psExe -Argument $s.Args -WorkingDirectory $ProjectRoot
        Register-ScheduledTask -TaskName $s.Name -Action $action -Trigger $s.Trigger `
            -Settings $settings -Principal $principal -Description $s.Desc -Force | Out-Null
        Write-Step ("已注册计划任务 {0}（{1}）" -f $s.Name, $(if ($s.Name -like '*Reprovision') { '每周日 04:40' } else { '每天 04:10' }))
        if ($Start) { Start-ScheduledTask -TaskName $s.Name; Write-Step "已启动 $($s.Name)" }
    }
}

function Show-Status {
    $head = (Invoke-Git @('rev-parse', '--short', 'HEAD'))
    $branch = (Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD'))
    Write-Host ''
    Write-Host '===== 冒烟 runner 维护状态 =====' -ForegroundColor Cyan
    Write-Host ("  项目根     {0}" -f $ProjectRoot)
    Write-Host ("  runner     {0}" -f $Runner)
    Write-Host ("  代码 HEAD  {0} ({1})" -f $head.Out, $branch.Out)
    Write-Host ("  冒烟锁     {0}" -f $(if (Test-SmokeBusy) { '存在（正在冒烟，维护会跳过）' } else { '无' }))
    $watcherPidFile = Join-Path $TmpDir 'smoke-watcher.pid'
    if (Test-Path -LiteralPath $watcherPidFile) {
        $wpid = ([IO.File]::ReadAllText($watcherPidFile)).Trim()
        $alive = $false
        if ($wpid -match '^\d+$') { $alive = [bool](Get-Process -Id ([int]$wpid) -ErrorAction SilentlyContinue) }
        Write-Host ("  watcher    pid {0} ({1})" -f $wpid, $(if ($alive) { 'alive' } else { 'dead' }))
    }
    foreach ($name in @('OLBR_SmokeWatcher', 'OLBR_SmokeMaintenance', 'OLBR_SmokeReprovision')) {
        $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        Write-Host ("  任务 {0,-22} {1}" -f $name, $(if ($t) { $t.State } else { '未注册' }))
    }
    if (Test-Path -LiteralPath $LogFile) {
        Write-Host '  --- 最近维护日志 ---'
        Get-Content -LiteralPath $LogFile -Tail 6 | ForEach-Object { Write-Host ("    {0}" -f $_) }
    }
    Write-Host ''
}

# ---------------------------------------------------------------- 主流程
$explicit = $SyncData -or $UpdateCode -or $Reprovision -or $CleanTmp
if ($Daily) { $SyncData = $true; $UpdateCode = $true; $CleanTmp = $true; $explicit = $true }
if ($Apply -and -not $explicit -and -not $Status -and -not $InstallTask) {
    $SyncData = $true; $CleanTmp = $true
}

if ($Status) { Assert-Runner; Show-Status; exit 0 }
if ($InstallTask) { Install-MaintTask; exit 0 }

Assert-Runner

if ((Test-SmokeBusy) -and -not $IgnoreLock) {
    Write-Step '检测到 TMP\smoke.lock：冒烟进行中，本轮维护跳过。'
    exit 0
}

$planned = @()
if ($Reprovision) { $planned += ('Reprovision{0}' -f $(if ($RebuildDb) { '+RebuildDb' } else { '' })) }
if ($UpdateCode) { $planned += 'UpdateCode' }
if ($SyncData) { $planned += 'SyncData' }
if ($CleanTmp) { $planned += ('CleanTmp>{0}d' -f $CleanTmpDays) }
if ($planned.Count -eq 0) {
    Write-Step '没有指定任何维护项；用 -Daily 或 -SyncData/-UpdateCode/-CleanTmp/-Reprovision 指定。'
    exit 0
}
if (-not $Apply) {
    Write-Step ("干跑：将执行 {0}（加 -Apply 才落盘/执行）" -f ($planned -join ' + '))
}

$failed = $false
foreach ($step in @(
        @{ On = $Reprovision; Run = { Invoke-Reprovision } }
        @{ On = $UpdateCode; Run = { Invoke-UpdateCode } }
        @{ On = $SyncData; Run = { Invoke-SyncData } }
        @{ On = $CleanTmp; Run = { Invoke-CleanTmp } }
    )) {
    if (-not $step.On) { continue }
    try { & $step.Run | Out-Null }
    catch { $failed = $true; Write-Bad $_.Exception.Message }
}

if ($failed) { exit 1 }
Write-Step '维护完成。'
exit 0
