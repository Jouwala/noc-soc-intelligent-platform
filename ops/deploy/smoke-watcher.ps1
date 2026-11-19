<#
  smoke-watcher.ps1 — 项目内冒烟副本（.smoke）的轮询器（替代 Grok Routine 的触发 + 回写）
  ============================================================================
  Grok 链路原来是：CI 变绿 → agent-orchestrator POST webhook → Grok Routine
  在 D:\02 上 detached checkout 到 PR head → 跑冒烟 → 贴评论、打
  smoke-passed → auto-merge 合入。这条脚本把中间两步搬到本机：每 60 秒
  轮询 GitHub，看到 CI 已绿、且没有 smoke-passed 的 open PR，**先切到该
  PR 的 head**，再跑 smoke-local.ps1，贴回评论、通过才打标签。0 token。

  ⛔ 生产边界（2026-11-14 用户明确要求）
  ----------------------------------------
  D:\01_olbr_portal 是生产，**不许动它里面任何东西**。本脚本只在项目内
  冒烟副本（默认 <项目>\.smoke，由 ops\deploy\provision-smoke-runner.ps1
  生成）里工作；开头有硬守卫，根目录落在 D:\01 下面、或缺 .smoke-runner
  标记都直接拒绝执行。冒烟链路对 01 只剩一处只读用途：开跑前后看 80/443
  的 nginx 归属是否逐字未变，从不写入。

  触发条件（与 agent-orchestrator 的去重逻辑一致）：
    · PR open、非草稿、base 是 main；
    · 同一 head SHA 的 quality 工作流有一次 completed + success；
    · 该 PR 还没有绑定当前 head 的 smoke-passed 标签（绑定旧 head 的会被摘除重跑），也没有 do-not-merge；
    · 同一 head SHA 没被本机冒烟过（状态文件 TMP\smoke-watcher-state.json）。

  为什么要 checkout（2026-11-14 实测补上）
  ----------------------------------------
  smoke-local.ps1 会校验「当前 HEAD == -HeadSha」，不等就 exit 2 并拒绝冒烟。
  Grok 当年是自己 checkout 的；watcher 若不 checkout，每个 PR 都只会拿到
  一条「HEAD 不匹配」的失败回帖，auto-merge 永远等不到门禁。所以这里做
  和 Grok 同样的事：
    1. 先记住当前分支与 HEAD；
    2. git fetch <remote> pull/<N>/head，校验取到的就是 PR head；
    3. git checkout --detach <sha>；
    4. 跑冒烟；
    5. **finally 里一定切回原分支/原提交**（冒烟失败、脚本异常都恢复）。
  安全阀：工作树有**未提交的已跟踪改动**时不动它，跳过该 PR 等下一轮，
  绝不把别人的在途改动 checkout 掉。未跟踪文件（TMP、构建产物）不影响。

  标签与 head SHA 的绑定：贴标签前重新读一次 PR，head 变了就放弃本次
  结果、不贴标签，等下一轮对新 head 重跑。绝不给旧 head 的结果贴到新
  head 上（phase-0 评估里的 P1 风险）。

  QA 结果回写 Issue（TASK-06-A #48，协议第 6/7/8 类消息）：
  冒烟开始前给关联 Issue 打 state:qa-ready 并发 QA_STARTED；通过发
  QA_PASSED + state:qa-passed，失败发 QA_FAILED（结构化：PR、HEAD SHA、
  失败项、错误信息、报告入口）+ state:qa-failed。评论带去重标记
  （qa-started: / qa-result: pr-<N>-sha-<短SHA>），同一 (PR, SHA) 不重复
  发；发前先查 Issue 评论里有没有同标记，不依赖本机状态文件。

  预检拒绝（smoke-local 退出码 2）：02 端口被别的栈占住、HEAD 不匹配、
  冒烟锁冲突时，smoke-local 什么都没验证就退出。这类结果**不算通过也
  不算失败**：不贴评论、不打 smoke-passed、不写 failed 状态、不回写
  state:qa-failed，下一轮自动重试同一个 head（2026-11-16 修：旧实现把它
  当失败写进 failed，该 head 就永不再跑，Issue 被误打 qa-failed 并触发
  auto-fix，实测见 #100/#104）。

  smoke-passed 的 SHA 绑定（TASK-06-A #48，堵过期结果触发合并）：
  贴到 PR 的冒烟报告评论首行带机器可读标记
  <!-- smoke-result: pr-<N> sha-<40位SHA> result=passed|failed -->，
  auto-merge.yml 合并前独立核对最后一条 passed 标记的 SHA 是否等于 PR
  当前 head，对不上就跳过（不依赖本机进程在场）。若 PR 已有 smoke-passed
  但绑定的是旧 head（或查不到绑定），watcher 摘除过期标签并对新 head
  重跑冒烟，不再像以前那样见标签就永久跳过。

  Token：优先环境变量 SMOKE_GITHUB_TOKEN，其次 GITHUB_TOKEN / GH_TOKEN；
  都没有时回落到本机已登录的 `gh auth token`（token 只在进程内存里，
  不写文件、不进日志）。全部拿不到时直接退出，不猜、不裸奔。

  日志：同时写 stdout 与 TMP\smoke-watcher.log（超过 5MB 轮转为 .1）。
  单实例：TMP\smoke-watcher.pid 守护，发现已有活的 watcher 就直接退出。

  用法（在冒烟副本根目录，如 <项目>\.smoke）：
    powershell -NoProfile -File ops\deploy\smoke-watcher.ps1 -Once -DryRun
    powershell -NoProfile -File ops\deploy\smoke-watcher.ps1 -Once
    powershell -NoProfile -File ops\deploy\smoke-watcher.ps1 -OnlyPr 45 -Once
    powershell -NoProfile -File ops\deploy\smoke-watcher.ps1
  常驻：用 ops\deploy\install-smoke-watcher.ps1 注册计划任务
  （登录时自启 + 崩溃自动重启 + 已在跑就不再起第二个）。
#>
[CmdletBinding()]
param(
    [int]$PollSeconds = 60,
    [switch]$Once,
    [switch]$DryRun,
    [string]$Repo = 'Jouwala/noc-soc-intelligent-platform',
    [string]$Remote = 'platform',
    [int]$OnlyPr = 0
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$TmpDir = Join-Path $Root 'TMP'
$StateFile = Join-Path $TmpDir 'smoke-watcher-state.json'
$PidFile = Join-Path $TmpDir 'smoke-watcher.pid'
$LogFile = Join-Path $TmpDir 'smoke-watcher.log'
$SmokeScript = Join-Path $PSScriptRoot 'smoke-local.ps1'

# ⛔ 生产边界硬守卫：宁可不动，也不许任何路径落到 D:\01 下面。
$ProductionRoot = 'D:\01_olbr_portal'
if ($Root.TrimEnd('\') -eq $ProductionRoot -or $Root -like ($ProductionRoot + '\*')) {
    throw "拒绝执行：推导出的根目录是 $Root，落在生产目录 $ProductionRoot 下面。本链路只在 D:\02_olbr_portal 隔离副本上跑。"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root '.smoke-runner'))) {
    throw "拒绝执行：$Root 不是项目内冒烟副本（缺 .smoke-runner）。请先跑 ops\deploy\provision-smoke-runner.ps1 -Apply 生成 <项目>\.smoke。"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'ISOLATED_COPY.flag'))) {
    throw '缺少 ISOLATED_COPY.flag，拒绝执行。'
}
if (-not (Test-Path -LiteralPath $SmokeScript)) { throw "找不到冒烟脚本：$SmokeScript" }

# ---------- token：环境变量优先，最后回落本机 gh 会话 ----------
$Token = $env:SMOKE_GITHUB_TOKEN
$TokenSource = 'SMOKE_GITHUB_TOKEN'
if (-not $Token) { $Token = $env:GITHUB_TOKEN; $TokenSource = 'GITHUB_TOKEN' }
if (-not $Token) { $Token = $env:GH_TOKEN; $TokenSource = 'GH_TOKEN' }
if (-not $Token) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $ghOut = (& gh auth token 2>$null | Select-Object -First 1)
        if ($ghOut -and ("$ghOut" -match '^gh[pousr]_')) {
            $Token = "$ghOut".Trim()
            $TokenSource = 'gh auth token'
        }
    } catch { } finally { $ErrorActionPreference = $prevEap }
}
if (-not $Token) { $TokenSource = 'none' }
if ((-not $Token) -and (-not $DryRun)) {
    throw '没有 GitHub token：请设置环境变量 SMOKE_GITHUB_TOKEN（细粒度 PAT，本仓库 Issues 读写即可），或先 gh auth login。'
}

function Write-WatchLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    try {
        if (Test-Path -LiteralPath $LogFile) {
            if ((Get-Item -LiteralPath $LogFile).Length -gt 5MB) {
                Move-Item -LiteralPath $LogFile -Destination ($LogFile + '.1') -Force -ErrorAction SilentlyContinue
            }
        }
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
    } catch { }
}

# 只记一次：避免常驻时每 60 秒重复写同一条「还没绿」
$script:SkipLogged = @{}
function Write-SkipOnce {
    param([string]$Key, [string]$Message)
    if ($script:SkipLogged.ContainsKey($Key)) { return }
    $script:SkipLogged[$Key] = $true
    Write-WatchLog $Message
}

function Invoke-GitHubApi {
    param([string]$Path, [string]$Method = 'GET', [object]$Body = $null)
    $headers = @{
        'Accept' = 'application/vnd.github+json'
        'User-Agent' = 'smoke-watcher'
    }
    if ($Token) { $headers['Authorization'] = "Bearer $Token" }
    $uri = "https://api.github.com$Path"
    if ($null -eq $Body) {
        return Invoke-RestMethod -Uri $uri -Method $Method -Headers $headers -TimeoutSec 60
    }

    # PS 5.1 的两个坑（2026-11-14 本机贴评论返回 400/422 实测）：
    # 1) 把 Get-Content -Raw 读出的字符串塞进 hashtable，ConvertTo-Json 会序列化成
    #    {"body":{"value":"..."}} —— body 变成对象，GitHub 判非法。必须显式 [string]。
    # 2) -Body 传字符串时 PS 5.1 按 ANSI 编码发，中文变 '?' —— 统一发 UTF-8 字节。
    $normalized = @{}
    if ($Body -is [hashtable]) {
        foreach ($k in $Body.Keys) {
            $v = $Body[$k]
            if ($v -is [string]) { $normalized[$k] = [string]$v } else { $normalized[$k] = $v }
        }
    } else {
        $normalized = $Body
    }
    $json = ($normalized | ConvertTo-Json -Depth 5 -Compress)
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    try {
        return Invoke-RestMethod -Uri $uri -Method $Method -Headers $headers -Body $bytes -ContentType 'application/json; charset=utf-8' -TimeoutSec 60
    } catch {
        # 把 GitHub 返回的错误体带出来：否则日志里只有 (400) Bad Request，没法查。
        $detail = $_.Exception.Message
        try {
            $resp = $_.Exception.Response
            if ($resp) {
                $sr = New-Object IO.StreamReader($resp.GetResponseStream())
                $detail = $detail + ' | ' + $sr.ReadToEnd()
            }
        } catch { }
        throw ("GitHub API $Method $Path 失败：" + $detail)
    }
}

function Invoke-Git {
    param([string[]]$GitArgs)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & git -C $Root @GitArgs 2>&1
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prevEap }
    $text = (@($out) | ForEach-Object { "$_" }) -join "`n"
    return @{ Exit = $code; Out = $text.Trim() }
}

function Enter-PrCheckout {
    param([int]$PrNumber, [string]$Sha)
    $plan = @{ Ok = $false; Why = ''; OrigBranch = ''; OrigHead = '' }

    # 安全阀：只挡「已跟踪文件的未提交改动」。未跟踪/被忽略的文件（TMP、构建产物）
    # 不影响 checkout，也不该让 watcher 永远跳过。
    $st = Invoke-Git @('status', '--porcelain', '--untracked-files=no')
    if ($st.Exit -ne 0) { $plan.Why = "git status 失败：$($st.Out)"; return $plan }
    if ($st.Out) {
        $plan.Why = '工作树有未提交的已跟踪改动，不动它（避免覆盖他人在途工作）；等下一轮。改动：' + ($st.Out -replace "`n", ' | ')
        return $plan
    }

    $br = Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD')
    $hd = Invoke-Git @('rev-parse', 'HEAD')
    if ($br.Exit -ne 0 -or $hd.Exit -ne 0) { $plan.Why = '读不到当前 HEAD / 分支'; return $plan }
    $plan.OrigBranch = $br.Out
    $plan.OrigHead = $hd.Out

    $f = Invoke-Git @('fetch', '--no-tags', $Remote, ("pull/{0}/head" -f $PrNumber))
    if ($f.Exit -ne 0) { $plan.Why = "git fetch $Remote pull/$PrNumber/head 失败：$($f.Out)"; return $plan }
    $fetched = (Invoke-Git @('rev-parse', 'FETCH_HEAD')).Out
    if ($fetched -ne $Sha) {
        $plan.Why = "fetch 拿到 $fetched，与 PR head $Sha 不一致（可能刚推了新提交）；本轮放弃，下一轮重试"
        return $plan
    }

    $co = Invoke-Git @('checkout', '--detach', '--quiet', $Sha)
    if ($co.Exit -ne 0) { $plan.Why = "git checkout --detach 失败：$($co.Out)"; return $plan }
    $now = (Invoke-Git @('rev-parse', 'HEAD')).Out
    if ($now -ne $Sha) { $plan.Why = "checkout 之后 HEAD 是 $now，不是 $Sha"; return $plan }

    $plan.Ok = $true
    return $plan
}

function Exit-PrCheckout {
    param([hashtable]$Plan)
    if (-not $Plan -or -not $Plan.Ok) { return }
    $short = $Plan.OrigHead.Substring(0, 7)

    if ($Plan.OrigBranch -and $Plan.OrigBranch -ne 'HEAD') {
        $r = Invoke-Git @('checkout', '--quiet', $Plan.OrigBranch)
        if ($r.Exit -ne 0) { Write-WatchLog "恢复分支 $($Plan.OrigBranch) 失败：$($r.Out)，改按原提交恢复" }
    }
    $now = (Invoke-Git @('rev-parse', 'HEAD')).Out
    if ($now -eq $Plan.OrigHead) {
        Write-WatchLog "已恢复原分支 $($Plan.OrigBranch)（HEAD $short）"
        return
    }
    $null = Invoke-Git @('checkout', '--quiet', '--detach', $Plan.OrigHead)
    $now2 = (Invoke-Git @('rev-parse', 'HEAD')).Out
    if ($now2 -eq $Plan.OrigHead) {
        Write-WatchLog "已按原提交恢复（detached HEAD $short）"
    } else {
        Write-WatchLog "警告：恢复原 HEAD 失败，现在 $now2，应为 $($Plan.OrigHead)"
    }
}

function Enter-WatcherPidGuard {
    if (Test-Path -LiteralPath $PidFile) {
        $old = 0
        try { $old = [int]((Get-Content -LiteralPath $PidFile -Raw).Trim()) } catch { $old = 0 }
        if ($old -gt 0 -and $old -ne $PID) {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$old" -ErrorAction SilentlyContinue
            if ($p -and ("$($p.CommandLine)" -match '(^|[\s\\/])smoke-watcher\.ps1')) {
                throw "已有 smoke-watcher 在跑（pid=$old），本实例退出，避免两个轮询器同时冒烟。"
            }
        }
    }
    Set-Content -LiteralPath $PidFile -Value "$PID" -Encoding ASCII
}

function Exit-WatcherPidGuard {
    try {
        if (Test-Path -LiteralPath $PidFile) {
            $cur = (Get-Content -LiteralPath $PidFile -Raw).Trim()
            if ($cur -eq "$PID") { Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue }
        }
    } catch { }
}

function Get-State {
    # 状态一律用 hashtable（PS 5.1 可用），不用 PSCustomObject 的动态属性：
    # PR 号是纯数字键，"44" 这种属性名在 $obj.$key 写法下不可靠。
    $state = @{ smoked = @{}; failed = @{} }
    if (Test-Path -LiteralPath $StateFile) {
        try {
            $raw = (Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json)
            foreach ($prop in @('smoked', 'failed')) {
                if ($null -ne $raw.$prop) {
                    foreach ($p in $raw.$prop.PSObject.Properties) { $state[$prop][$p.Name] = $p.Value }
                }
            }
        } catch { }
    }
    return $state
}

function Save-State {
    param([object]$State)
    New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null
    ($State | ConvertTo-Json -Depth 5) | Out-File -LiteralPath $StateFile -Encoding UTF8
}

function Normalize-GitHubList {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return @() }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        if ($Value.PSObject.Properties.Name -contains 'number') { return @($Value) }
    }
    if ($Value -is [System.Array]) { return [object[]]$Value }
    $asList = @($Value)
    if ($asList.Count -eq 1 -and $asList[0] -is [System.Array]) { return [object[]]$asList[0] }
    return $asList
}

function Test-QualityGreen {
    param([string]$Sha)
    $runs = Invoke-GitHubApi "/repos/$Repo/actions/workflows/quality.yml/runs?head_sha=$Sha&per_page=5"
    foreach ($run in (Normalize-GitHubList $runs.workflow_runs)) {
        if ($run.status -eq 'completed' -and $run.conclusion -eq 'success') { return $true }
    }
    return $false
}

function Resolve-IssueNumber {
    param([object]$Pr)
    if ($Pr.body -match '(closes|fixes|resolves)\s+#(\d+)') { return [int]$Matches[2] }
    if ($Pr.body -match 'task:\s*#(\d+)') { return [int]$Matches[1] }
    if ($Pr.head.ref -match '^agent/task-(\d+)') { return [int]$Matches[1] }
    return 0
}

function Get-UtcNowIso {
    return (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}

function Get-IssueLabelNames {
    param([int]$Number)
    # 必须过 Normalize-GitHubList：恰好 1 个标签时 API 返回单对象而非数组。
    return @((Normalize-GitHubList (Invoke-GitHubApi "/repos/$Repo/issues/$Number/labels")) | ForEach-Object { $_.name })
}

function Set-IssueLabels {
    param([int]$Number, [string[]]$Labels)
    Invoke-GitHubApi "/repos/$Repo/issues/$Number/labels" 'PUT' @{ labels = $Labels } | Out-Null
}

function Remove-IssueLabel {
    param([int]$Number, [string]$Label)
    Invoke-GitHubApi "/repos/$Repo/issues/$Number/labels/$Label" 'DELETE' | Out-Null
}

function Set-IssueQaState {
    # Issue 的 QA 状态标签单值化：摘掉 in-review 与三个 qa 状态，只留目标状态。
    param([int]$Issue, [string]$NewState)
    $qaStates = @('state:in-review', 'state:qa-ready', 'state:qa-passed', 'state:qa-failed')
    $current = Get-IssueLabelNames -Number $Issue
    $next = @($current | Where-Object { $qaStates -notcontains $_ })
    if ($next -notcontains $NewState) { $next += $NewState }
    if (($next -join ',') -ne ($current -join ',')) { Set-IssueLabels -Number $Issue -Labels $next }
}

function Test-IssueMarker {
    # 去重：Issue 评论里已有同标记就不再发（不依赖本机状态文件）。
    param([int]$Issue, [string]$Marker)
    $comments = Normalize-GitHubList (Invoke-GitHubApi "/repos/$Repo/issues/$Issue/comments?per_page=100")
    foreach ($c in $comments) {
        if ($c.body -and $c.body.Contains($Marker)) { return $true }
    }
    return $false
}

function Send-IssueComment {
    param([int]$Issue, [string]$Body)
    Invoke-GitHubApi "/repos/$Repo/issues/$Issue/comments" 'POST' @{ body = $Body } | Out-Null
}

function Get-SmokeResultBoundSha {
    # 读 PR 评论里最后一条 result=passed 的 smoke-result 标记，返回其绑定的 SHA；没有返回 ''。
    param([int]$PrNumber)
    $comments = Normalize-GitHubList (Invoke-GitHubApi "/repos/$Repo/issues/$PrNumber/comments?per_page=100")
    $bound = ''
    foreach ($c in $comments) {
        if ($c.body -and $c.body -match 'smoke-result:\s*pr-\d+\s+sha-([0-9a-f]{40})\s+result=passed') {
            $bound = $Matches[1]
        }
    }
    return $bound
}

function Send-QaStarted {
    param([int]$Issue, [int]$PrNumber, [string]$Sha, [string]$Short)
    Set-IssueQaState -Issue $Issue -NewState 'state:qa-ready'
    $marker = "qa-started: pr-$PrNumber-sha-$Short"
    if (Test-IssueMarker -Issue $Issue -Marker $marker) {
        Write-WatchLog "PR #$PrNumber QA_STARTED 已存在（$marker），不重复发。"
        return
    }
    $body = @(
        "<!-- agent-msg: QA_STARTED, task: #$Issue, from: smoke-watcher, at: $(Get-UtcNowIso) -->",
        "<!-- $marker -->",
        '',
        '冒烟已开始（D:\02 本机 smoke-watcher）。',
        '',
        "- PR：#$PrNumber",
        "- HEAD SHA：``$Sha``",
        '- 预计时长：约 3–5 分钟'
    ) -join "`n"
    Send-IssueComment -Issue $Issue -Body $body
    Write-WatchLog "PR #$PrNumber QA_STARTED 已回写 Issue #$Issue（head $Short）。"
}

function Send-QaResult {
    param([int]$Issue, [int]$PrNumber, [string]$Sha, [string]$Short, [bool]$Passed, [string]$Report)
    $marker = "qa-result: pr-$PrNumber-sha-$Short"
    if ($Passed) {
        Set-IssueQaState -Issue $Issue -NewState 'state:qa-passed'
        $type = 'QA_PASSED'
    } else {
        Set-IssueQaState -Issue $Issue -NewState 'state:qa-failed'
        $type = 'QA_FAILED'
    }
    if (Test-IssueMarker -Issue $Issue -Marker $marker) {
        Write-WatchLog "PR #$PrNumber $type 已存在（$marker），不重复发。"
        return
    }
    $lines = @(
        "<!-- agent-msg: $type, task: #$Issue, from: smoke-watcher, at: $(Get-UtcNowIso) -->",
        "<!-- $marker -->",
        ''
    )
    if ($Passed) {
        $lines += '冒烟通过（D:\02 本机 smoke-watcher）。'
        $lines += ''
        $lines += "- PR：#$PrNumber"
        $lines += "- HEAD SHA：``$Sha``"
        $lines += "- 通过项：冒烟全流程通过，完整报告见 PR #$PrNumber 的冒烟评论（报告首行 smoke-result 标记绑定本 SHA）。"
        $lines += '- 标签：PR 已打 smoke-passed；本 Issue 已打 state:qa-passed。'
    } else {
        $lines += '冒烟失败（D:\02 本机 smoke-watcher）。'
        $lines += ''
        $lines += "- PR：#$PrNumber"
        $lines += "- HEAD SHA：``$Sha``"
        $lines += '- 失败项：'
        $failLines = @()
        foreach ($ln in ($Report -split "`n")) {
            $t = $ln.Trim()
            if ($t.StartsWith('<!--')) { continue }
            if ($t -match '失败|✗|FAIL|未通过|结论') { $failLines += "  - $t" }
            if ($failLines.Count -ge 12) { break }
        }
        if ($failLines.Count -eq 0) { $failLines += '  - 见完整报告。' }
        $lines += $failLines
        $lines += "- 错误信息：见完整报告 https://github.com/$Repo/pull/$PrNumber 的冒烟评论；本机日志：TMP\smoke-pr-$PrNumber-$Short.log"
        $lines += '- 标签：本 Issue 已打 state:qa-failed；PR 未打 smoke-passed，等新 head 重跑。'
    }
    Send-IssueComment -Issue $Issue -Body ($lines -join "`n")
    Write-WatchLog "PR #$PrNumber $type 已回写 Issue #$Issue（head $Short）。"
}

function Test-DirtyTrackedTree {
    $st = Invoke-Git @('status', '--porcelain', '--untracked-files=no')
    if ($st.Exit -ne 0) { return @{ Dirty = $true; Why = "git status 失败：$($st.Out)" } }
    if ($st.Out) {
        $preview = ($st.Out -replace "`r?`n", ' | ')
        if ($preview.Length -gt 240) { $preview = $preview.Substring(0, 240) + '…' }
        return @{ Dirty = $true; Why = $preview }
    }
    return @{ Dirty = $false; Why = '' }
}

function Invoke-OneCycle {
    $state = Get-State
    $tree = Test-DirtyTrackedTree
    if ($tree.Dirty) {
        Write-SkipOnce 'dirty-tree' "工作树有已跟踪未提交改动，本轮跳过所有 PR 的 checkout/冒烟（避免覆盖在途工作）：$($tree.Why)"
        return
    }

    if ($OnlyPr -gt 0) {
        $prs = Normalize-GitHubList (Invoke-GitHubApi "/repos/$Repo/pulls/$OnlyPr")
        Write-WatchLog "只处理 PR #$OnlyPr（-OnlyPr，忽略本机已冒烟记录）"
    } else {
        $prs = Normalize-GitHubList (Invoke-GitHubApi "/repos/$Repo/pulls?state=open&per_page=100")
    }

    foreach ($pr in $prs) {
        $n = $pr.number
        $sha = $pr.head.sha
        $short = $sha.Substring(0, 7)
        $labels = @($pr.labels | ForEach-Object { $_.name })

        if ($pr.draft) { continue }
        if ($pr.base.ref -ne 'main') { continue }
        if ($labels -contains 'do-not-merge') { Write-SkipOnce "dnm-$n-$sha" "PR #$n 有 do-not-merge，跳过。"; continue }

        $key = "$n"
        if ($labels -contains 'smoke-passed') {
            # 已有 smoke-passed：只认绑定到当前 head 的结果（TASK-06-A #48）。
            # 本机记录对得上、或 PR 评论里的 smoke-result 标记对得上，才算本 head 已过；
            # 都对不上 = 标签已过期（旧 head 的结果），摘除后对新 head 重跑冒烟。
            if ($state['smoked'][$key] -eq $sha) { continue }
            $boundSha = ''
            try { $boundSha = Get-SmokeResultBoundSha -PrNumber $n } catch {
                Write-WatchLog "PR #$n 读 smoke-result 绑定失败：$($_.Exception.Message)，本轮跳过。"
                continue
            }
            if ($boundSha -eq $sha) {
                $state['smoked'][$key] = $sha
                Save-State $state
                continue
            }
            Write-WatchLog "PR #$n 的 smoke-passed 绑定的是旧 head（绑定=$boundSha），当前 head $short 已过期：摘除标签，对新 head 重跑冒烟。"
            try { Remove-IssueLabel -Number $n -Label 'smoke-passed' } catch {
                Write-WatchLog "PR #$n 摘除过期 smoke-passed 失败：$($_.Exception.Message)，本轮跳过。"
                continue
            }
            $labels = @($labels | Where-Object { $_ -ne 'smoke-passed' })
        }
        if ($OnlyPr -le 0) {
            if ($state['smoked'][$key] -eq $sha) { continue }
            if ($state['failed'][$key] -eq $sha) {
                Write-SkipOnce "failed-$n-$sha" "PR #$n head $short 本机已冒烟失败过，等新 head 再跑。"
                continue
            }
        }

        Write-WatchLog "PR #$n head $short：查 CI…"
        try { $green = Test-QualityGreen $sha } catch {
            Write-WatchLog "PR #$n 查 CI 失败：$($_.Exception.Message)，下一轮重试。"
            continue
        }
        if (-not $green) {
            Write-SkipOnce "ci-$n-$sha" "PR #$n head $short CI 还没绿，等它绿。"
            continue
        }

        Write-WatchLog "PR #$n CI 已绿，开始冒烟（head $short）。"
        if ($DryRun) {
            Write-WatchLog "干跑：PR #$n 会 checkout $short 并跑冒烟，不实际执行、不贴评论、不打标签。"
            continue
        }

        $issue = Resolve-IssueNumber $pr
        if ($issue -gt 0) {
            try { Send-QaStarted -Issue $issue -PrNumber $n -Sha $sha -Short $short }
            catch { Write-WatchLog "PR #$n QA_STARTED 回写 Issue #$issue 失败：$($_.Exception.Message)（不挡冒烟主流程）" }
        }

        # ---- 切到 PR head（Grok 当年做的事），跑冒烟，finally 一定切回 ----
        $plan = Enter-PrCheckout -PrNumber $n -Sha $sha
        if (-not $plan.Ok) {
            Write-SkipOnce "co-$n-$sha" "PR #$n 暂不冒烟：$($plan.Why)"
            continue
        }
        Write-WatchLog "已切到 PR #$n head $short（原分支 $($plan.OrigBranch) / $($plan.OrigHead.Substring(0,7))）"

        $smokeExit = 1
        try {
            $smokeArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $SmokeScript, '-PrNumber', "$n", '-HeadSha', $sha)
            if ($issue -gt 0) { $smokeArgs += @('-IssueNumber', "$issue") }
            Write-WatchLog ("执行：powershell " + ($smokeArgs -join ' '))
            & powershell.exe @smokeArgs
            $smokeExit = $LASTEXITCODE
            Write-WatchLog "smoke-local 退出码=$smokeExit"
        } finally {
            Exit-PrCheckout -Plan $plan
        }

        if ($smokeExit -eq 2) {
            # 预检拒绝：冒烟脚本没启动/停止任何进程，什么都没验证。
            # 记成失败会让这个 head 永久不再重跑（state['failed']），Issue 被
            # 误打 state:qa-failed 并触发 auto-fix（2026-11-16 实测 #100/#104）。
            Write-SkipOnce "refused-$n-$sha" "PR #$n head $short 冒烟预检拒绝（02 端口被占/环境未就绪），本轮不计失败；下一轮重试同一 head。"
            continue
        }

        $passed = ($smokeExit -eq 0)
        $reportFile = Join-Path $TmpDir ("smoke-pr-{0}-{1}.md" -f $n, $short)
        $report = ''
        if (Test-Path -LiteralPath $reportFile) { $report = [string](Get-Content -LiteralPath $reportFile -Raw) }
        else { $report = "## D:\02 冒烟结果：失败`n`n冒烟脚本未生成报告（exit=$smokeExit），请看 TMP\smoke-pr-$n-$short.log。" }

        # 贴标签前重读 PR：head 变了就放弃本次结果，不贴标签。
        $fresh = Invoke-GitHubApi "/repos/$Repo/pulls/$n"
        if ($fresh.head.sha -ne $sha) {
            Write-WatchLog "PR #$n 冒烟期间 head 变了（$short -> $($fresh.head.sha.Substring(0,7))），本次结果作废、不贴标签，下一轮重跑。"
            continue
        }
        if ($report.Length -gt 60000) {
            $report = $report.Substring(0, 60000) + "`n`n（报告过长已截断，完整版在 D:\02 的 TMP\smoke-pr-$n-$short.md）"
        }
        $resultWord = 'failed'
        if ($passed) { $resultWord = 'passed' }
        $report = ("<!-- smoke-result: pr-{0} sha-{1} result={2} -->" -f $n, $sha, $resultWord) + "`n`n" + $report
        Invoke-GitHubApi "/repos/$Repo/issues/$n/comments" 'POST' @{ body = $report } | Out-Null
        Write-WatchLog "PR #$n 冒烟评论已贴（通过=$passed）。"
        if ($passed) {
            $current = @((Normalize-GitHubList (Invoke-GitHubApi "/repos/$Repo/issues/$n/labels")) | ForEach-Object { $_.name })
            if ($current -notcontains 'smoke-passed') { $current += 'smoke-passed' }
            Invoke-GitHubApi "/repos/$Repo/issues/$n/labels" 'PUT' @{ labels = $current } | Out-Null
            $state['smoked'][$key] = $sha
            Write-WatchLog "PR #$n 已打 smoke-passed（head $short）。"
        } else {
            $state['failed'][$key] = $sha
            Write-WatchLog "PR #$n 冒烟未通过，未打标签；等新 head 再跑。"
        }
        if ($issue -gt 0) {
            try { Send-QaResult -Issue $issue -PrNumber $n -Sha $sha -Short $short -Passed $passed -Report $report }
            catch { Write-WatchLog "PR #$n QA 结果回写 Issue #$issue 失败：$($_.Exception.Message)" }
        }
        Save-State $state
    }
}

Write-WatchLog "smoke-watcher 启动：repo=$Repo remote=$Remote 根=$Root 轮询=${PollSeconds}s Once=$Once DryRun=$DryRun OnlyPr=$OnlyPr token来源=$TokenSource"
# 干跑只读轮询，不与常驻实例抢 pid 文件（否则排障时 -Once -DryRun 永远起不来）。
# -OnlyPr 为手工补跑单条 PR，允许与常驻实例并存（靠 smoke.lock 防并发冒烟）。
if ((-not $DryRun) -and ($OnlyPr -le 0)) { Enter-WatcherPidGuard }
try {
    if ($Once) {
        Invoke-OneCycle
        Write-WatchLog '单轮完成。'
    } else {
        while ($true) {
            try { Invoke-OneCycle } catch {
                Write-WatchLog "本轮出错：$($_.Exception.Message)，下一轮重试。"
            }
            Start-Sleep -Seconds $PollSeconds
        }
    }
} finally {
    if ((-not $DryRun) -and ($OnlyPr -le 0)) { Exit-WatcherPidGuard }
}
