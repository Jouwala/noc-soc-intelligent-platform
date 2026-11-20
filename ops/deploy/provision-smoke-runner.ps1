<#
  provision-smoke-runner.ps1 — 在项目内生成 / 修复冒烟运行副本
  ============================================================================
  为什么需要它（2026-11-19）：

    2026-11-18 02 切换成生产后，冒烟不能再在 02 根目录里跑：它会重建
    apps\_fe_dist、按副本根重写 nginx.conf，并在本副本根下 `nginx -s quit`
    —— 等于每冒烟一次就打断一次生产。当晚的应急做法是把隔离栈挪到项目外
    的 D:\02_olbr_portal_smoke（见 docs\workspace\cutover-2026-11-20.md）。
    但那是**手工搭出来的树**：clone + 7 个 junction + 手工复制 .env/db/dist，
    仓库里没有任何脚本能重建它；而且它的 junction 又指回项目，
    项目一搬走两边同时坏 —— 违反 README「可独立部署的便携副本」与
    apps\whatsapp-kb-bot\src\myAppPaths.js「便携副本必须完全自包含」。

    本脚本把这份运行副本收进项目内（默认 <项目>\.smoke，已在 .gitignore），
    并且做到可重复生成：clone + junction + 复制构建产物 + 从生产库克隆
    schema 再灌三张本地权威表。换机器 / 重建 02 时跑一次 -Apply 即可。

  用法（在项目根或任意目录，脚本按自身位置推导项目根）：
    powershell -NoProfile -File ops\deploy\provision-smoke-runner.ps1            # 干跑，只打印计划
    powershell -NoProfile -File ops\deploy\provision-smoke-runner.ps1 -Apply     # 真跑
    powershell -NoProfile -File ops\deploy\provision-smoke-runner.ps1 -Apply -RebuildDb
    powershell -NoProfile -File ops\deploy\provision-smoke-runner.ps1 -Apply -InstallTask

  默认目标：<项目>\.smoke（必须是项目内目录；不写死盘符）
  产物：ISOLATED_COPY.flag、.smoke-runner（二者是冒烟三个入口的守卫依据）、
        .env、apps\my_app\backend\.env、apps\my_app\db\app.db、apps\_fe_dist、
        apps\portal\{dist,auth-data,portal-default.json}、nginx-1.28.0、
        runtime / node_modules 的 junction。
  安全：默认干跑；不打印任何口令；不启动 / 不停止任何服务；不改计划任务
        （-InstallTask 才调用 install-smoke-watcher.ps1，且需要管理员）。
#>
[CmdletBinding()]
param(
    [string]$RunnerPath = '',
    [switch]$Apply,
    [switch]$RebuildDb,
    [string]$DbSource = '',
    # 副本代码从哪个 ref 取（默认项目当前 HEAD）。合并前验证分支时用
    # -SourceRef refs/heads/<分支>，避免把别人的在途分支当生产代码。
    [string]$SourceRef = 'HEAD',
    # 跳过完整性标签修复（默认在提权时把副本根改成 Medium；见下方说明）。
    [switch]$SkipIntegrityFix,
    [switch]$SkipHeadUpdate,
    [switch]$InstallTask
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
if ($ProjectRoot -like 'D:\01_olbr_portal*') {
    throw "拒绝执行：$ProjectRoot 是生产目录 01。冒烟副本只在本项目（02）里生成。"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'apps\my_app\backend\main.py'))) {
    throw "当前目录不像本项目根（缺 apps\my_app\backend\main.py）：$ProjectRoot"
}

if ($RunnerPath -ne '') {
    $Runner = [IO.Path]::GetFullPath($RunnerPath)
} else {
    $Runner = Join-Path $ProjectRoot '.smoke'
}
$projFull = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
$runnerFull = [IO.Path]::GetFullPath($Runner).TrimEnd('\')
if ($runnerFull -eq $projFull) {
    throw "冒烟副本不能就是项目根（会把生产树当副本启停）：$Runner"
}
if (-not $runnerFull.StartsWith($projFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "冒烟副本必须落在项目内（$projFull 下面），收到：$Runner"
}

$Py = Join-Path $ProjectRoot 'runtime\python312\python.exe'
if (-not (Test-Path -LiteralPath $Py)) {
    throw "缺项目自带的 Python：$Py（先按 runtime\README.md 补齐运行时）"
}

function Write-Step {
    param([string]$Text)
    Write-Host "  - $Text"
}

function Invoke-Git {
    param([string]$Repo, [string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & git -C $Repo @GitArgs 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    $text = (@($out) | ForEach-Object { "$_" }) -join "`n"
    if ($code -ne 0) { throw ("git {0} 失败（exit={1}）：{2}" -f ($GitArgs -join ' '), $code, $text.Trim()) }
    return $text.Trim()
}

function New-JunctionIfMissing {
    param([string]$Link, [string]$Target)
    if (Test-Path -LiteralPath $Link) {
        $item = Get-Item -LiteralPath $Link -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return }
        throw "目标位置已存在且不是 junction，拒绝覆盖：$Link"
    }
    $parent = Split-Path -Parent $Link
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null
}

function Copy-TreeIfMissing {
    param([string]$From, [string]$To, [switch]$SkipLogsAndTemp)
    if (-not (Test-Path -LiteralPath $From)) { return $false }
    if (Test-Path -LiteralPath $To) { return $false }
    $parent = Split-Path -Parent $To
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $args = @($From, $To, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1')
    if ($SkipLogsAndTemp) { $args += @('/XD', 'logs', 'temp') }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $null = & robocopy @args } finally { $ErrorActionPreference = $prev }
    # robocopy: 0-7 = 成功（含“有文件复制/无变化”），>=8 才是失败
    if ($LASTEXITCODE -ge 8) { throw "robocopy 失败（exit=$LASTEXITCODE）：$From -> $To" }
    return $true
}

function Copy-FileIfMissing {
    param([string]$From, [string]$To)
    if (-not (Test-Path -LiteralPath $From)) { return $false }
    if (Test-Path -LiteralPath $To) { return $false }
    $parent = Split-Path -Parent $To
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item -LiteralPath $From -Destination $To -Force
    return $true
}

$projectHead = Invoke-Git -Repo $ProjectRoot -GitArgs @('rev-parse', $SourceRef)
$platformUrl = ''
try {
    $platformUrl = Invoke-Git -Repo $ProjectRoot -GitArgs @('remote', 'get-url', 'platform')
} catch {
    Write-Warning "项目没有 platform remote，副本将无法 fetch PR head：$($_.Exception.Message)"
}

Write-Host ''
Write-Host "冒烟副本生成器（项目根：$ProjectRoot）"
Write-Host "目标：$Runner"
Write-Host ('模式：{0}' -f $(if ($Apply) { '真跑（-Apply）' } else { '干跑（只打印计划，不写任何东西）' }))
Write-Host ''

# ---------- 0.5 完整性标签（项目内副本的关键一步） ----------
# 工作区根（D:\02_olbr_portal）带有 Low 完整性标签，新目录会继承它。Low 标签的
# nginx.exe 在 smoke.ps1 的隐藏控制台/私有桌面链里会以 0xC0000142（DLL 初始化失败）
# 起不来，表现为 start_all 失败。提权时先建副本根并把标签改成 Medium，后续子文件
# 随继承；未提权时只告警，并打印要执行的命令。
$isAdmin = $false
try {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
} catch { }
if (-not $SkipIntegrityFix) {
    if (-not (Test-Path -LiteralPath $Runner)) {
        if ($Apply) { New-Item -ItemType Directory -Force -Path $Runner | Out-Null }
    }
    $label = ''
    try { $label = (icacls $Runner 2>&1 | Out-String) } catch { }
    if ($label -match 'Low Mandatory Level') {
        if ($isAdmin -and $Apply) {
            Write-Step '把副本根完整性标签改为 Medium（子文件继承）'
            $null = icacls $Runner /setintegritylevel "(OI)(CI)Medium" 2>&1
        } else {
            Write-Warning ('副本根仍是 Low 完整性标签，隐藏控制台冒烟会以 nginx 0xC0000142 失败。' +
                "请用提权 PowerShell 执行：icacls `"$Runner`" /setintegritylevel (OI)(CI)Medium /T")
        }
    }
}

# ---------- 1. clone / 校验副本自身 ----------
$isClone = Test-Path -LiteralPath (Join-Path $Runner '.git')
if (-not $isClone) {
    $existing = @(Get-ChildItem -LiteralPath $Runner -Force -ErrorAction SilentlyContinue)
    if ($existing.Count -gt 0) {
        throw "目标已存在且不是 git 副本（且非空），拒绝在该目录上生成：$Runner"
    }
    Write-Step ('clone 项目 -> {0}（--no-hardlinks，独立对象库；检出 {1}）' -f $Runner, $SourceRef)
    if ($Apply) {
        $null = Invoke-Git -Repo $ProjectRoot -GitArgs @('clone', '--quiet', '--no-hardlinks', '--no-checkout', $ProjectRoot, $Runner)
        $null = Invoke-Git -Repo $Runner -GitArgs @('checkout', '--quiet', '--detach', $projectHead)
    }
} else {
    if (-not (Test-Path -LiteralPath (Join-Path $Runner 'apps\my_app\backend\main.py'))) {
        throw "目标已存在但不像本项目副本（缺 apps\my_app\backend\main.py）：$Runner"
    }
    Write-Step '副本已存在：只补缺失项（不删、不重置）'
}

if (-not $Apply -and -not $isClone) {
    Write-Host ''
    Write-Host '干跑结束：目标还不存在，上面是将会生成的内容。确认后加 -Apply。'
    exit 0
}

# 副本可能是一个更早的 clone：先把源分支的最新提交取进来，否则下面的
# checkout 会报 "unable to read tree"（2026-11-19 实测，改完 provision 脚本
# 本身再重跑时必踩）。离线/源是裸 SHA 时只告警。
if ($Apply) {
    try {
        $null = Invoke-Git -Repo $Runner -GitArgs @('fetch', '--no-tags', '--quiet', 'origin', $SourceRef)
        Write-Step ('fetch origin {0}' -f $SourceRef)
    } catch {
        Write-Warning "fetch origin $SourceRef 失败（离线或源是裸 SHA？）：$($_.Exception.Message)"
    }
}
# ---------- 2. 远端（platform 供 watcher fetch PR head；origin 指回项目） ----------
if ($platformUrl -ne '') {
    $remotes = Invoke-Git -Repo $Runner -GitArgs @('remote')
    if (($remotes -split "`n") -notcontains 'platform') {
        Write-Step '添加 platform remote（与项目一致）'
        if ($Apply) { $null = Invoke-Git -Repo $Runner -GitArgs @('remote', 'add', 'platform', $platformUrl) }
    }
}

# 补一次 platform/main：冒烟 §8.1 的第一候选基线是 platform/main，缺失时会回退到
# origin/main（= 项目本地 main，可能落后于主线），导致每个 PR 都被判成「前端有改动」
# 而白重建一次 _fe_dist（实测 +93s）。网络不可用时只告警，不挡生成。
if ($platformUrl -ne '' -and $Apply) {
    try {
        $null = Invoke-Git -Repo $Runner -GitArgs @('fetch', '--no-tags', '--quiet', 'platform', 'main')
        Write-Step 'fetch platform/main（§8.1 基线）'
    } catch {
        Write-Warning "fetch platform main 失败（离线？）：$($_.Exception.Message)"
    }
}

# ---------- 3. 运行时 / node_modules 的 junction（指向项目内，不复制几个 GB） ----------
$junctions = @(
    @{ Link = 'runtime\python312'; Target = (Join-Path $ProjectRoot 'runtime\python312') },
    @{ Link = 'runtime\node'; Target = (Join-Path $ProjectRoot 'runtime\node') },
    @{ Link = 'runtime\postgresql16'; Target = (Join-Path $ProjectRoot 'runtime\postgresql16') },
    @{ Link = 'runtime\pgdata'; Target = (Join-Path $ProjectRoot 'runtime\pgdata') },
    @{ Link = 'apps\my_app\frontend\node_modules'; Target = (Join-Path $ProjectRoot 'apps\my_app\frontend\node_modules') },
    @{ Link = 'apps\portal\node_modules'; Target = (Join-Path $ProjectRoot 'apps\portal\node_modules') },
    @{ Link = 'apps\portal\scripts\whatsapp_analysis\node_modules'; Target = (Join-Path $ProjectRoot 'apps\portal\scripts\whatsapp_analysis\node_modules') }
)
foreach ($j in $junctions) {
    $link = Join-Path $Runner $j.Link
    if (-not (Test-Path -LiteralPath $j.Target)) {
        throw "junction 目标不存在：$($j.Target)（先按 runtime\README.md / npm ci 补齐）"
    }
    if (Test-Path -LiteralPath $link) { continue }
    Write-Step ('junction {0} -> {1}' -f $j.Link, $j.Target)
    if ($Apply) { New-JunctionIfMissing -Link $link -Target $j.Target }
}

# ---------- 4. 配置（.env / backend\.env） ----------
$envSrc = Join-Path $ProjectRoot '.env'
if (-not (Test-Path -LiteralPath $envSrc)) {
    $envSrc = Join-Path $ProjectRoot 'ops\deploy\env_template.txt'
}
if (-not (Test-Path -LiteralPath $envSrc)) { throw '既没有 .env 也没有 ops\deploy\env_template.txt' }
if (-not (Test-Path -LiteralPath (Join-Path $Runner '.env'))) {
    Write-Step ('复制配置：{0} -> .env（隔离端口 8080/8443/55432/7039…）' -f (Split-Path -Leaf $envSrc))
    if ($Apply) { Copy-FileIfMissing -From $envSrc -To (Join-Path $Runner '.env') | Out-Null }
}

$beEnvSrc = Join-Path $ProjectRoot 'apps\my_app\backend\.env'
if (-not (Test-Path -LiteralPath $beEnvSrc)) {
    throw '缺 apps\my_app\backend\.env（DATABASE_URL / 凭据来源），无法生成副本'
}
if (-not (Test-Path -LiteralPath (Join-Path $Runner 'apps\my_app\backend\.env'))) {
    Write-Step '复制 apps\my_app\backend\.env（不打印内容；start_all 会把 DATABASE_URL 指到副本 PG）'
    if ($Apply) { Copy-FileIfMissing -From $beEnvSrc -To (Join-Path $Runner 'apps\my_app\backend\.env') | Out-Null }
}

# ---------- 5. 构建产物 / 门户运行态 / nginx ----------
$copyDirs = @(
    @{ From = (Join-Path $ProjectRoot 'nginx-1.28.0'); To = (Join-Path $Runner 'nginx-1.28.0'); Skip = $true },
    @{ From = (Join-Path $ProjectRoot 'apps\_fe_dist'); To = (Join-Path $Runner 'apps\_fe_dist'); Skip = $false },
    @{ From = (Join-Path $ProjectRoot 'apps\portal\dist'); To = (Join-Path $Runner 'apps\portal\dist'); Skip = $false },
    @{ From = (Join-Path $ProjectRoot 'apps\portal\auth-data'); To = (Join-Path $Runner 'apps\portal\auth-data'); Skip = $false },
    @{ From = (Join-Path $ProjectRoot 'apps\my_app\backend\data'); To = (Join-Path $Runner 'apps\my_app\backend\data'); Skip = $false }
)
foreach ($c in $copyDirs) {
    if (-not (Test-Path -LiteralPath $c.From)) { continue }
    if (Test-Path -LiteralPath $c.To) { continue }
    Write-Step ('复制 {0}' -f ($c.To.Substring($Runner.Length).TrimStart('\')))
    if ($Apply) { Copy-TreeIfMissing -From $c.From -To $c.To -SkipLogsAndTemp:$c.Skip | Out-Null }
}
# nginx 的 logs/ 与 temp/ 是运行时目录（robocopy 刻意排除），但 nginx 启动前
# 必须能打开 error_log；缺了会以 0xC0000142/配置错误退出。这里显式建好。
$nginxDirR = Join-Path $Runner 'nginx-1.28.0'
if (Test-Path -LiteralPath $nginxDirR) {
    if ($Apply) {
        foreach ($d in @('logs', 'temp', 'conf')) {
            $dp = Join-Path $nginxDirR $d
            if (-not (Test-Path -LiteralPath $dp)) { New-Item -ItemType Directory -Force -Path $dp | Out-Null }
        }
    }
}
$fileCopies = @(
    @{ From = (Join-Path $ProjectRoot 'apps\portal\portal-default.json'); To = (Join-Path $Runner 'apps\portal\portal-default.json') }
)
foreach ($f in $fileCopies) {
    if (-not (Test-Path -LiteralPath $f.From)) { continue }
    if (Test-Path -LiteralPath $f.To) { continue }
    Write-Step ('复制 {0}' -f ($f.To.Substring($Runner.Length).TrimStart('\')))
    if ($Apply) { Copy-FileIfMissing -From $f.From -To $f.To | Out-Null }
}

# ---------- 6. 冒烟副本标记（三个入口的守卫依据） ----------
$flagPath = Join-Path $Runner 'ISOLATED_COPY.flag'
if (-not (Test-Path -LiteralPath $flagPath)) {
    Write-Step '写 ISOLATED_COPY.flag'
    if ($Apply) {
        $flagText = @(
            'NOC/SOC Intelligent Platform - 冒烟运行副本（项目内）',
            ('项目根：{0}' -f $ProjectRoot),
            ('生成时间：{0}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')),
            '由 ops\deploy\provision-smoke-runner.ps1 生成；不是生产，不得绑定 80/443/7038/7090/9267。'
        ) -join "`r`n"
        Set-Content -LiteralPath $flagPath -Value $flagText -Encoding UTF8
    }
}
$markerPath = Join-Path $Runner '.smoke-runner'
if (-not (Test-Path -LiteralPath $markerPath)) {
    Write-Step '写 .smoke-runner（冒烟入口守卫用；放在项目外的副本没有这个标记）'
    if ($Apply) {
        $marker = [ordered]@{
            project_root = $ProjectRoot
            created      = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
            generator    = 'ops/deploy/provision-smoke-runner.ps1'
        } | ConvertTo-Json
        Set-Content -LiteralPath $markerPath -Value $marker -Encoding UTF8
    }
}

# ---------- 7. app.db：只克隆 schema + 三张本地权威表 ----------
$dbDir = Join-Path $Runner 'apps\my_app\db'
$dbFile = Join-Path $dbDir 'app.db'
if ($RebuildDb -or -not (Test-Path -LiteralPath $dbFile)) {
    if ($DbSource -eq '') { $DbSource = Join-Path $ProjectRoot 'apps\my_app\db\app.db' }
    if (-not (Test-Path -LiteralPath $DbSource)) { throw "权威源 app.db 不存在：$DbSource" }
    if (Test-Path -LiteralPath (Join-Path $Runner 'TMP\smoke.lock')) {
        throw "副本里冒烟正在跑（TMP\smoke.lock 存在），不能重建 app.db；等它结束再跑。"
    }
    Write-Step ('重建 app.db：{0} -> 只复制 schema + 三张本地权威表' -f (Split-Path -Leaf $DbSource))
    if ($Apply) {
        if (-not (Test-Path -LiteralPath $dbDir)) { New-Item -ItemType Directory -Force -Path $dbDir | Out-Null }
        New-Item -ItemType Directory -Force -Path (Join-Path $dbDir 'attachments') | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $dbDir 'uploads') | Out-Null
        $schemaTool = Join-Path $ProjectRoot 'ops\deploy\clone_sqlite_schema.py'
        $authTool = Join-Path $ProjectRoot 'ops\deploy\local_authority_tools.py'
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $Py $schemaTool --source $DbSource --target $dbFile --force
            if ($LASTEXITCODE -ne 0) { throw "clone_sqlite_schema.py 失败（exit=$LASTEXITCODE）" }
            & $Py $authTool migrate --source $DbSource --target $dbFile --backup-dir (Join-Path $Runner 'TMP')
            if ($LASTEXITCODE -ne 0) { throw "local_authority_tools.py migrate 失败（exit=$LASTEXITCODE）" }
        } finally {
            $ErrorActionPreference = $prev
        }
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $Runner 'TMP'))) {
    Write-Step '建 TMP\'
    if ($Apply) { New-Item -ItemType Directory -Force -Path (Join-Path $Runner 'TMP') | Out-Null }
}

# ---------- 8. 代码 HEAD：跟项目当前提交对齐（watcher 空闲时不应该跑旧代码） ----------
if (-not $SkipHeadUpdate) {
    $runnerHead = Invoke-Git -Repo $Runner -GitArgs @('rev-parse', 'HEAD')
    if ($runnerHead -ne $projectHead) {
        $status = Invoke-Git -Repo $Runner -GitArgs @('status', '--porcelain', '--untracked-files=no')
        $locked = Test-Path -LiteralPath (Join-Path $Runner 'TMP\smoke.lock')
        if ($status -ne '') {
            Write-Warning ("副本有已跟踪未提交改动，跳过 HEAD 更新（保持 {0}）：{1}" -f $runnerHead.Substring(0, 8), $status)
        } elseif ($locked) {
            Write-Warning "副本正在冒烟（smoke.lock），跳过 HEAD 更新"
        } else {
            Write-Step ('HEAD 更新：{0} -> {1}' -f $runnerHead.Substring(0, 8), $projectHead.Substring(0, 8))
            if ($Apply) { $null = Invoke-Git -Repo $Runner -GitArgs @('checkout', '--quiet', '--detach', $projectHead) }
        }
    } else {
        Write-Step ('HEAD 已是项目当前提交 {0}' -f $projectHead.Substring(0, 8))
    }
}

# ---------- 9. 计划任务（可选） ----------
if ($InstallTask) {
    $installer = Join-Path $Runner 'ops\deploy\install-smoke-watcher.ps1'
    if (-not (Test-Path -LiteralPath $installer)) { throw "找不到 installer：$installer" }
    if (-not $Apply) {
        Write-Step '（干跑）将调用 install-smoke-watcher.ps1 -Start（需要管理员）'
        Write-Step '（干跑）将调用 maintain-smoke-runner.ps1 -InstallTask（维护任务，需项目根已含该脚本）'
    } else {
        Write-Host ''
        Write-Host '注册计划任务 OLBR_SmokeWatcher（需要管理员；失败请用提权 PowerShell 重跑本命令）'
        & $installer -Start
        if ($LASTEXITCODE -ne 0) { throw "install-smoke-watcher.ps1 失败（exit=$LASTEXITCODE）" }

        # 维护任务与 watcher 一起注册：数据同步、代码跟进、TMP 清理、每周重铺。
        # 固定用项目根那份脚本 —— runner 的检出随时会被 watcher 切到某个 PR head，
        # 那不是注册计划任务的稳定来源。
        $maintScript = Join-Path $ProjectRoot 'ops\deploy\maintain-smoke-runner.ps1'
        if (Test-Path -LiteralPath $maintScript) {
            Write-Host ''
            Write-Host '注册维护任务 OLBR_SmokeMaintenance / OLBR_SmokeReprovision'
            & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
                -NoProfile -ExecutionPolicy Bypass -File $maintScript -InstallTask
            if ($LASTEXITCODE -ne 0) { throw "maintain-smoke-runner.ps1 -InstallTask 失败（exit=$LASTEXITCODE）" }
        } else {
            Write-Step "跳过维护任务：项目根缺 $maintScript（合并「runner 例行维护」后再跑 -InstallTask）"
        }
    }
}

Write-Host ''
Write-Host '完成。下一步：'
Write-Host ('  1) 手测一次：powershell -NoProfile -File "{0}" -PrNumber <PR> -HeadSha <40位SHA>' -f (Join-Path $Runner 'ops\deploy\smoke-local.ps1'))
Write-Host ('  2) 改计划任务（需管理员）：powershell -NoProfile -File "{0}" -Start' -f (Join-Path $Runner 'ops\deploy\install-smoke-watcher.ps1'))
Write-Host '  3) 旧的项目外副本（若还有）确认不再被计划任务引用后再删除。'
Write-Host ''
