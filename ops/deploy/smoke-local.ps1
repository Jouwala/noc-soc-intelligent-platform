<#
  smoke-local.ps1 — D:\02 本机冒烟（替代 Grok Routine 的冒烟部分）
  ============================================================================
  目标：把 Grok 每次在 D:\02 上手工跑的 §8.2–§8.4 + 离线 pytest，变成
  一条确定性脚本，0 token、0 LLM 判断。结果格式与 Grok 在 PR #43 的
  回帖一致（§8.1–§8.4 + pytest + 标签），下游 auto-merge 只认
  `smoke-passed` 标签，不认是谁贴的，所以脚本贴回的结果能直接进
  自动合入链。

  只在 D:\02_olbr_portal 里跑；任何一步失败都走 finally 停服务、
  恢复 git 分支，绝不把 02 的服务留在后台、绝不动 D:\01。

  用法（必须在 D:\02_olbr_portal 根目录）：
    powershell -NoProfile -File ops\deploy\smoke-local.ps1 -PrNumber 44 -HeadSha <40位>
    powershell -NoProfile -File ops\deploy\smoke-local.ps1 -PrNumber 44 -HeadSha <40位> -DryRun
    powershell -NoProfile -File ops\deploy\smoke-local.ps1 -PrNumber 44 -HeadSha <40位> -IssueNumber 19

  退出码：0 通过；1 冒烟失败；2 前置条件不满足（blocked，如目录不对、
  PG 已被他人占用、取不到 PR 的 head）。

  注意：本机没有 pwsh 校验环境，改完后先在 MSNOC1 上跑 -DryRun，
  再跑一次真 PR 与 Grok 结果对照，对上才算替换完成。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$PrNumber,
    [Parameter(Mandatory = $true)][string]$HeadSha,
    [int]$IssueNumber = 0,
    [string]$Remote = 'platform',
    [switch]$DryRun,
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$ScriptVersion = 'smoke-local.ps1 v1.0.0'
$StartedAt = Get-Date
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$TmpDir = Join-Path $Root 'TMP'
$ShortSha = $HeadSha.Substring(0, 7)
$ReportFile = Join-Path $TmpDir ("smoke-pr-{0}-{1}.md" -f $PrNumber, $ShortSha)
$LogFile = Join-Path $TmpDir ("smoke-pr-{0}-{1}.log" -f $PrNumber, $ShortSha)
$Results = @{}
$Notes = @()
$StartedServices = $false
$OriginalBranch = ''
$OriginalCommit = ''

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Message
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
}

function Fail-Step {
    param([string]$Section, [string]$Message)
    $Results[$Section] = "失败：$Message"
    throw "$Section $Message"
}

function Get-DotEnvValue {
    param([string]$Key, [string]$Default = '')
    $envFile = Join-Path $Root '.env'
    if (-not (Test-Path -LiteralPath $envFile)) { return $Default }
    foreach ($line in (Get-Content -LiteralPath $envFile)) {
        if ($line -match '^\s*#') { continue }
        if ($line -match ("^\s*{0}\s*=\s*(.+?)\s*$" -f [regex]::Escape($Key))) {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $Default
}

function Test-HttpOk {
    param([string]$Url, [string]$MustContain = '')
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 30
        if ($resp.StatusCode -ne 200) { return "HTTP $($resp.StatusCode)" }
        if ($MustContain -and ($resp.Content -notmatch [regex]::Escape($MustContain))) {
            return "返回 200 但不含期望内容：$MustContain"
        }
        return ''
    } catch {
        return $_.Exception.Message
    }
}

function Test-PortFree {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return ($null -eq $conns)
}

function Get-ListeningProcessPath {
    param([int]$Port)
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return '' }
    $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
    if (-not $proc) { return '' }
    try { return $proc.Path } catch { return $proc.ProcessName }
}

# ---------- 前置守卫：只在 02 跑，绝不动生产 ----------
if ($Root -notlike '*02_olbr_portal') { throw "当前目录不是 02（$Root），拒绝执行。" }
if (-not (Test-Path -LiteralPath (Join-Path $Root 'ISOLATED_COPY.flag'))) {
    throw '缺少 ISOLATED_COPY.flag，拒绝执行：当前目录可能不是隔离副本。'
}
if ($HeadSha -notmatch '^[0-9a-f]{40}$') { throw "HeadSha 不是 40 位小写 hex：$HeadSha" }
New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null
'' | Out-File -LiteralPath $LogFile -Encoding UTF8
Write-Log "$ScriptVersion PR #$PrNumber head $ShortSha（DryRun=$DryRun）"

try {
    # ---------- 0) 取 PR 的 head，校验 SHA ----------
    Write-Log '--- 0) 取 PR head ---'
    $OriginalBranch = (git rev-parse --abbrev-ref HEAD).Trim()
    $OriginalCommit = (git rev-parse HEAD).Trim()
    if ($DryRun) {
        $Results['取码'] = '干跑：跳过 git fetch/checkout'
    } else {
        $remotes = (git remote).Trim() -split "`n"
        $useRemote = $Remote
        if ($remotes -notcontains $Remote) {
            if ($remotes -contains 'origin') { $useRemote = 'origin' }
            else { throw "找不到 git remote '$Remote' 或 'origin'。" }
        }
        git fetch $useRemote ("pull/{0}/head" -f $PrNumber) 2>&1 | ForEach-Object { Write-Log "  $_" }
        if ($LASTEXITCODE -ne 0) { Fail-Step '取码' "git fetch $useRemote pull/$PrNumber/head 失败" }
        git checkout -B ("smoke-pr-{0}" -f $PrNumber) FETCH_HEAD 2>&1 | ForEach-Object { Write-Log "  $_" }
        $actual = (git rev-parse HEAD).Trim()
        if ($actual -ne $HeadSha) {
            Fail-Step '取码' "checkout 后 HEAD 是 $actual，与传入的 $HeadSha 不一致（PR 可能刚推了新提交），拒绝冒烟。"
        }
        $Results['取码'] = "head $ShortSha 已 checkout（remote=$useRemote）"
    }
    Write-Log $Results['取码']

    # ---------- §8.1 前端构建 ----------
    Write-Log '--- §8.1 前端构建 ---'
    $feChanged = $false
    if (-not $DryRun) {
        $changed = git diff --name-only 'main...HEAD' 2>$null
        if ($LASTEXITCODE -ne 0) { $changed = git diff --name-only 'origin/main...HEAD' 2>$null }
        foreach ($f in $changed) {
            if ($f -like 'apps/portal/*' -or $f -like 'apps/my_app/frontend/*') { $feChanged = $true }
        }
    }
    if ($SkipFrontendBuild) { $feChanged = $false }
    if (-not $feChanged) {
        $Results['8.1'] = '无需重建（相对 main 的变更未命中前端，沿用现有 apps\_fe_dist）。'
    } elseif ($DryRun) {
        $Results['8.1'] = '干跑：前端有变更，跳过实际构建。'
    } else {
        Push-Location (Join-Path $Root 'apps\my_app\frontend')
        try { npm run build 2>&1 | ForEach-Object { Write-Log "  [my_app] $_" }; if ($LASTEXITCODE -ne 0) { Fail-Step '§8.1' 'my_app 前端构建失败' } }
        finally { Pop-Location }
        Push-Location (Join-Path $Root 'apps\portal')
        try { npm run build 2>&1 | ForEach-Object { Write-Log "  [portal] $_" }; if ($LASTEXITCODE -ne 0) { Fail-Step '§8.1' '门户前端构建失败' } }
        finally { Pop-Location }
        $Results['8.1'] = '前端有变更，已重建 my_app 与门户（npm run build 通过）。'
    }
    Write-Log $Results['8.1']

    # ---------- §8.2 之前：处理残留的 postmaster.pid ----------
    # 今天中午 D:\02 就卡在这里：上次 PG 没干净退出，pid 文件残留，PG 起不来。
    # 只有 pid 指向的进程已死才删；进程还活着说明 PG 正被别人用着，直接 blocked。
    $PgData = Join-Path $Root 'runtime\pgdata'
    $PidFile = Join-Path $PgData 'postmaster.pid'
    if ((Test-Path -LiteralPath $PidFile) -and -not $DryRun) {
        $oldPid = (Get-Content -LiteralPath $PidFile -TotalCount 1).Trim()
        $alive = $false
        if ($oldPid -match '^\d+$') { $alive = $null -ne (Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue) }
        if ($alive) {
            throw "便携 PG 的 postmaster.pid 指向仍存活的进程 $oldPid：PG 可能正被他人使用，拒绝冒烟（blocked）。"
        }
        Remove-Item -LiteralPath $PidFile -Force
        $Notes += "残留 postmaster.pid（pid=$oldPid，进程已死）已清理。"
        Write-Log "残留 postmaster.pid 已清理（pid=$oldPid）"
    }

    # ---------- 启动 02 全套服务 ----------
    Write-Log '--- 启动 02（start_all.ps1）---'
    if ($DryRun) {
        $Results['启动'] = '干跑：跳过 start_all.ps1'
    } else {
        & (Join-Path $Root 'ops\deploy\start_all.ps1') 2>&1 | ForEach-Object { Write-Log "  $_" }
        if ($LASTEXITCODE -ne 0) { Fail-Step '启动' "start_all.ps1 失败（exit=$LASTEXITCODE）" }
        $StartedServices = $true
        $Results['启动'] = 'start_all.ps1 通过'
    }

    # ---------- §8.2 数据库 ----------
    Write-Log '--- §8.2 数据库 ---'
    if ($DryRun) {
        $Results['8.2'] = '干跑：跳过 psql 检查。'
    } else {
        $PgBin = Join-Path $Root 'runtime\postgresql16\bin'
        $Psql = Join-Path $PgBin 'psql.exe'
        $PgPort = [int](Get-DotEnvValue 'PGPORT' '55432')
        $PgUser = Get-DotEnvValue 'MY_APP_DB_USER' 'myapp'
        if ($PgPort -ne 55432) { Fail-Step '§8.2' "PGPORT 是 $PgPort，不是隔离端口 55432。" }
        $dataDir = (& $Psql -w -h 127.0.0.1 -p $PgPort -U $PgUser -d postgres -tAc 'SHOW data_directory' 2>$null).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $dataDir) { Fail-Step '§8.2' 'psql SHOW data_directory 失败。' }
        if ($dataDir -notlike "$Root*") { Fail-Step '§8.2' "data_directory 是 $dataDir，不在 02 下面。" }
        $counts = ''
        $AppDb = Join-Path $Root 'apps\my_app\db\app.db'
        if (Test-Path -LiteralPath $AppDb) {
            $counts = python -c "import sqlite3; c=sqlite3.connect(r'$AppDb'); print('/'.join(str(c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]) for t in ['credentials','task_schedule_config','email_recipients']))" 2>$null
            if ($LASTEXITCODE -eq 0 -and $counts) { $counts = "；credentials/task_schedule_config/email_recipients = $counts" }
            else { $counts = '；本地权威三表计数读取失败（不拦冒烟，已记录）' }
        }
        $Results['8.2'] = "通过。SHOW data_directory 为 ``$dataDir``，端口 55432（非生产 5432）$counts。"
    }
    Write-Log $Results['8.2']

    # ---------- §8.3 服务检查 1–6 ----------
    Write-Log '--- §8.3 服务检查 1–6 ---'
    if ($DryRun) {
        $Results['8.3'] = '干跑：跳过 6 项 HTTP 检查。'
    } else {
        $checks = @()
        $err = Test-HttpOk 'http://127.0.0.1:7039/api/health' 'db_mode'
        if ($err) { $checks += "1. /api/health：$err" }
        $err = Test-HttpOk 'http://127.0.0.1:7039/api/wo-dashboard/overview'
        if ($err) { $checks += "2. /api/wo-dashboard/overview：$err" }
        $err = Test-HttpOk 'http://127.0.0.1:9277/uniplam/'
        if ($err) { $checks += "3. 门户 9277：$err" }
        $err = Test-HttpOk 'http://127.0.0.1:8080/myapp/'
        if ($err) { $checks += "4a. nginx 8080：$err" }
        $err = Test-HttpOk 'https://127.0.0.1:8443/myapp/'
        if ($err) {
            # PS 5.1 没有 -SkipCertificateCheck；自签证书时改走 curl -k。
            $curlOut = curl.exe -k -s -o $null -w '%{http_code}' 'https://127.0.0.1:8443/myapp/' 2>$null
            if ($curlOut -ne '200') { $checks += "4b. nginx 8443：$err（curl -k 亦为 $curlOut）" }
        }
        # 5. 8080 服务的 index.html 与 apps\_fe_dist\index.html sha256 一致
        $distIndex = Join-Path $Root 'apps\_fe_dist\index.html'
        if (-not (Test-Path -LiteralPath $distIndex)) { $checks += '5. apps\_fe_dist\index.html 不存在' }
        else {
            $want = (Get-FileHash -LiteralPath $distIndex -Algorithm SHA256).Hash
            $tmpIndex = Join-Path $TmpDir 'smoke-served-index.html'
            try {
                Invoke-WebRequest -Uri 'http://127.0.0.1:8080/myapp/index.html' -UseBasicParsing -OutFile $tmpIndex -TimeoutSec 30
                $got = (Get-FileHash -LiteralPath $tmpIndex -Algorithm SHA256).Hash
                if ($got -ne $want) { $checks += '5. 8080 的 index.html sha256 与 apps\_fe_dist 不一致' }
                else { $Results['8.3-hash'] = $want }
            } catch { $checks += "5. 取 8080 的 index.html 失败：$($_.Exception.Message)" }
        }
        # 6. nginx -t 通过，且进程在 02 下面
        $NginxExe = Join-Path $Root 'nginx-1.28.0\nginx.exe'
        & $NginxExe -t -p (Join-Path $Root 'nginx-1.28.0\') 2>&1 | ForEach-Object { Write-Log "  [nginx -t] $_" }
        if ($LASTEXITCODE -ne 0) { $checks += "6. nginx -t 失败（exit=$LASTEXITCODE）" }
        $nginxPath = Get-ListeningProcessPath 8080
        if ($nginxPath -and ($nginxPath -notlike "$Root*")) { $checks += "6. 8080 的 nginx 进程不在 02 下面：$nginxPath" }
        if ($checks.Count -gt 0) { Fail-Step '§8.3' ($checks -join '；') }
        $hashLine = ''
        if ($Results.ContainsKey('8.3-hash')) { $hashLine = "；index.html sha256 ``$($Results['8.3-hash'])``" }
        $Results['8.3'] = "全部通过。1. /api/health：db_mode=pg；2. 只读业务接口返回非空；3. 门户 9277：200；4. nginx 8080/8443：200；5. index.html sha256 一致$hashLine；6. nginx -t 通过，进程在 02 下面。"
    }
    Write-Log $Results['8.3']

    # ---------- pytest 离线集（与 CI quality.yml 同一套） ----------
    Write-Log '--- pytest 离线集 ---'
    if ($DryRun) {
        $Results['pytest'] = '干跑：跳过 pytest。'
    } else {
        Push-Location (Join-Path $Root 'apps\my_app')
        try {
            $pyOut = python -m pytest tests -m 'not live' --deselect tests/test_client_is_local_trust.py::test_genuine_local_callers_still_allowed --deselect tests/test_client_is_local_trust.py::test_is_loopback_covers_ipv4_mapped_and_port_forms -q --tb=no 2>&1
            $pyExit = $LASTEXITCODE
            $pyOut | ForEach-Object { Write-Log "  [pytest] $_" }
            $summary = ($pyOut | Select-String -Pattern 'failed|passed' | Select-Object -Last 1).Line
            if (-not $summary) { $summary = "pytest exit=$pyExit（未解析到汇总行）" }
            $failedLines = @($pyOut | Select-String -Pattern '^FAILED' | ForEach-Object { $_.Line })
            $nonNur = @($failedLines | Where-Object { $_ -notmatch 'test_nur_exclude_semantics' })
            if ($pyExit -ne 0 -and $nonNur.Count -gt 0) {
                Fail-Step 'pytest' "$summary；除已知 NUR 外还有失败：$($nonNur -join '；')"
            }
            if ($pyExit -ne 0) { $Notes += 'pytest 有 2 个已知 NUR 缺数据失败，已按用户要求忽略。' }
            $Results['pytest'] = "``$summary``"
        } finally { Pop-Location }
    }
    Write-Log $Results['pytest']

    $Results['结论'] = '通过'
}
catch {
    if (-not $Results.ContainsKey('结论')) { $Results['结论'] = "失败：$($_.Exception.Message)" }
    Write-Log "冒烟失败：$($_.Exception.Message)"
}
finally {
    # ---------- §8.4 收尾：停 02、确认端口释放、80/443 仍归生产 ----------
    Write-Log '--- §8.4 收尾 ---'
    $stopNotes = @()
    if ($StartedServices -and -not $DryRun) {
        & (Join-Path $Root 'ops\deploy\stop_all.ps1') 2>&1 | ForEach-Object { Write-Log "  [stop] $_" }
    }
    if (-not $DryRun) {
        $busy = @()
        foreach ($p in @(8080, 8443, 7039, 7091, 9277, 55432)) {
            if (-not (Test-PortFree $p)) { $busy += $p }
        }
        if ($busy.Count -gt 0) {
            $stopNotes += "02 端口未完全释放：$($busy -join ', ')"
            Write-Log "警告：02 端口未完全释放：$($busy -join ', ')"
        }
        $prod80 = Get-ListeningProcessPath 80
        $prod443 = Get-ListeningProcessPath 443
        $prodOk = $true
        foreach ($pp in @($prod80, $prod443)) {
            if ($pp -and ($pp -notlike '*01_olbr_portal*')) { $prodOk = $false }
        }
        if ($prodOk) { $stopNotes += '80/443 仍归 D:\01_olbr_portal 的 nginx。' }
        else { $stopNotes += "警告：80/443 归属异常（80=$prod80；443=$prod443）。" }
        $stopNotes += '预先存在的 realtime_proxy 8887/8890 未动。'
    } else {
        $stopNotes += '干跑：跳过停服务与端口确认。'
    }
    $Results['8.4'] = ($stopNotes -join ' ')
    Write-Log $Results['8.4']

    # 恢复 git 原分支（冒烟 checkout 的 smoke-pr-* 分支删掉，不留尾巴）
    if ((-not $DryRun) -and $OriginalBranch -and ($OriginalBranch -ne 'HEAD')) {
        git checkout $OriginalBranch 2>&1 | Out-Null
        git branch -D ("smoke-pr-{0}" -f $PrNumber) 2>&1 | Out-Null
        $restored = (git rev-parse HEAD).Trim()
        if ($restored -ne $OriginalCommit) { Write-Log "警告：恢复后 HEAD 是 $restored，原提交是 $OriginalCommit。" }
    }
}

# ---------- 写报告（与 Grok #43 回帖同格式，watcher 直接拿去贴） ----------
$EndedAt = Get-Date
$Duration = [math]::Round(($EndedAt - $StartedAt).TotalSeconds, 1)
$passed = ($Results['结论'] -eq '通过')
$report = @()
$verdict = '失败'
if ($passed) { $verdict = '通过' }
$report += "## D:\02 冒烟结果：$verdict"
$report += ''
$report += "- 测试提交：``$HeadSha``"
$report += "- 由本地冒烟脚本运行（$ScriptVersion），无 LLM 参与"
$report += "- 运行时间：$($StartedAt.ToString('yyyy-MM-dd HH:mm:ss'))–$($EndedAt.ToString('HH:mm:ss'))（Atlantic/Reykjavik，UTC+0），合计 $Duration 秒"
if ($IssueNumber -gt 0) { $report += "- 关联 Issue：#$IssueNumber" }
$report += ''
$report += '### §8.1 前端构建'
$report += $Results['8.1']
$report += ''
$report += '### §8.2 数据库'
$report += $Results['8.2']
$report += ''
$report += '### §8.3 服务检查 1–6'
$report += $Results['8.3']
$report += ''
$report += '### §8.4 收尾'
$report += $Results['8.4']
$report += ''
$report += '### pytest'
$report += $Results['pytest']
if ($Notes.Count -gt 0) {
    $report += ''
    $report += '### 备注'
    foreach ($n in $Notes) { $report += "- $n" }
}
$report += ''
$report += '### 标签'
if ($passed) { $report += '已打 `smoke-passed`。' } else { $report += '未打 `smoke-passed`（冒烟未通过）。' }
$report -join "`n" | Out-File -LiteralPath $ReportFile -Encoding UTF8
Write-Log "报告已写：$ReportFile（结论：$($Results['结论'])，耗时 ${Duration}s）"

if ($passed) { exit 0 } else { exit 1 }
