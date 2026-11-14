<#
  smoke-watcher.ps1 — D:\02 本机冒烟轮询器（替代 Grok Routine 的触发 + 回写）
  ============================================================================
  Grok 链路原来是：CI 变绿 → agent-orchestrator POST webhook → Grok Routine
  在 D:\02 跑冒烟 → Grok 贴评论、打 smoke-passed → auto-merge 合入。
  这条脚本把中间两步搬到本机：每 60 秒轮询 GitHub，看到 CI 已绿、
  且没有 smoke-passed 的 open PR，就地跑 smoke-local.ps1，贴回评论、
  通过才打标签。全程 0 token。

  触发条件（与 agent-orchestrator 的去重逻辑一致）：
    · PR open、非草稿、base 是 main；
    · 同一 head SHA 的 quality 工作流有一次 completed + success；
    · 该 PR 还没有 smoke-passed 标签；
    · 同一 head SHA 没被本机冒烟过（状态文件 TMP\smoke-watcher-state.json）。

  标签与 head SHA 的绑定：贴标签前重新读一次 PR，head 变了就放弃
  本次结果、不贴标签，等下一轮对新 head 重跑。绝不给旧 head 的
  结果贴到新 head 上（这是 phase-0 评估里的 P1 风险）。

  Token：细粒度 PAT，只需本仓库 Issues 读写 + PR 只读、Actions 只读。
  放到环境变量 SMOKE_GITHUB_TOKEN（优先），或 GITHUB_TOKEN / GH_TOKEN。
  没有 token 时直接退出，不猜、不裸奔。不要把 token 写进脚本或报告。

  用法（在 D:\02_olbr_portal 根目录）：
    powershell -NoProfile -File ops\deploy\smoke-watcher.ps1 -Once -DryRun
    powershell -NoProfile -File ops\deploy\smoke-watcher.ps1 -Once
    powershell -NoProfile -File ops\deploy\smoke-watcher.ps1
  常驻跑法：用计划任务或 NSSM 包成服务，每次启动跑本脚本（不带 -Once
  就是死循环，每轮间隔 -PollSeconds，默认 60 秒）。
#>
[CmdletBinding()]
param(
    [int]$PollSeconds = 60,
    [switch]$Once,
    [switch]$DryRun,
    [string]$Repo = 'Jouwala/noc-soc-intelligent-platform'
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$TmpDir = Join-Path $Root 'TMP'
$StateFile = Join-Path $TmpDir 'smoke-watcher-state.json'
$SmokeScript = Join-Path $PSScriptRoot 'smoke-local.ps1'

if ($Root -notlike '*02_olbr_portal') { throw "当前目录不是 02（$Root），拒绝执行。" }
if (-not (Test-Path -LiteralPath (Join-Path $Root 'ISOLATED_COPY.flag'))) {
    throw '缺少 ISOLATED_COPY.flag，拒绝执行。'
}
if (-not (Test-Path -LiteralPath $SmokeScript)) { throw "找不到冒烟脚本：$SmokeScript" }

$Token = $env:SMOKE_GITHUB_TOKEN
if (-not $Token) { $Token = $env:GITHUB_TOKEN }
if (-not $Token) { $Token = $env:GH_TOKEN }
if ((-not $Token) -and (-not $DryRun)) {
    throw '没有 GitHub token：请设置环境变量 SMOKE_GITHUB_TOKEN（细粒度 PAT，本仓库 Issues 读写即可）。'
}

function Write-WatchLog {
    param([string]$Message)
    Write-Host "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Message
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
    $json = ($Body | ConvertTo-Json -Depth 5 -Compress)
    return Invoke-RestMethod -Uri $uri -Method $Method -Headers $headers -Body $json -ContentType 'application/json' -TimeoutSec 60
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

function Test-QualityGreen {
    param([string]$Sha)
    $runs = Invoke-GitHubApi "/repos/$Repo/actions/workflows/quality.yml/runs?head_sha=$Sha&per_page=5"
    foreach ($run in $runs.workflow_runs) {
        if ($run.status -eq 'completed' -and $run.conclusion -eq 'success') { return $true }
    }
    return $false
}

function Invoke-OneCycle {
    $state = Get-State

    $prs = Invoke-GitHubApi "/repos/$Repo/pulls?state=open&per_page=100"
    foreach ($pr in $prs) {
        $n = $pr.number
        $sha = $pr.head.sha
        $labels = @($pr.labels | ForEach-Object { $_.name })
        if ($pr.draft) { continue }
        if ($pr.base.ref -ne 'main') { continue }
        if ($labels -contains 'smoke-passed') { continue }
        if ($labels -contains 'do-not-merge') { Write-WatchLog "PR #$n 有 do-not-merge，跳过。"; continue }
        $key = "$n"
        if ($state['smoked'][$key] -eq $sha) { continue }
        if ($state['failed'][$key] -eq $sha) { Write-WatchLog "PR #$n head $($sha.Substring(0,7)) 本机已冒烟失败过，等新 head 再跑。"; continue }

        Write-WatchLog "PR #$n head $($sha.Substring(0,7))：查 CI…"
        try { $green = Test-QualityGreen $sha } catch {
            Write-WatchLog "PR #$n 查 CI 失败：$($_.Exception.Message)，下一轮重试。"
            continue
        }
        if (-not $green) { Write-WatchLog "PR #$n CI 还没绿，跳过。"; continue }

        Write-WatchLog "PR #$n CI 已绿，开始冒烟（head $($sha.Substring(0,7))）。"
        if ($DryRun) { Write-WatchLog "干跑：PR #$n 会跑冒烟，不实际执行、不贴评论。"; continue }

        $issue = 0
        if ($pr.body -match '(closes|fixes|resolves)\s+#(\d+)') { $issue = [int]$Matches[2] }
        elseif ($pr.body -match 'task:\s*#(\d+)') { $issue = [int]$Matches[1] }
        elseif ($pr.head.ref -match '^agent/task-(\d+)') { $issue = [int]$Matches[1] }

        $smokeArgs = @('-NoProfile', '-File', $SmokeScript, '-PrNumber', $n, '-HeadSha', $sha)
        if ($issue -gt 0) { $smokeArgs += @('-IssueNumber', $issue) }
        & powershell.exe @smokeArgs
        $smokeExit = $LASTEXITCODE
        $passed = ($smokeExit -eq 0)
        $short = $sha.Substring(0, 7)
        $reportFile = Join-Path $TmpDir ("smoke-pr-{0}-{1}.md" -f $n, $short)
        $report = ''
        if (Test-Path -LiteralPath $reportFile) { $report = Get-Content -LiteralPath $reportFile -Raw }
        else { $report = "## D:\02 冒烟结果：失败`n`n冒烟脚本未生成报告（exit=$smokeExit），请看 TMP\smoke-pr-$n-$short.log。" }

        # 贴标签前重读 PR：head 变了就放弃本次结果，不贴标签。
        $fresh = Invoke-GitHubApi "/repos/$Repo/pulls/$n"
        if ($fresh.head.sha -ne $sha) {
            Write-WatchLog "PR #$n 冒烟期间 head 变了（$short -> $($fresh.head.sha.Substring(0,7))），本次结果作废、不贴标签，下一轮重跑。"
            continue
        }
        if ($report.Length -gt 60000) { $report = $report.Substring(0, 60000) + "`n`n（报告过长已截断，完整版在 D:\02 的 TMP\smoke-pr-$n-$short.md）" }
        Invoke-GitHubApi "/repos/$Repo/issues/$n/comments" 'POST' @{ body = $report } | Out-Null
        Write-WatchLog "PR #$n 冒烟评论已贴（通过=$passed）。"
        if ($passed) {
            $current = @((Invoke-GitHubApi "/repos/$Repo/issues/$n/labels") | ForEach-Object { $_.name })
            if ($current -notcontains 'smoke-passed') { $current += 'smoke-passed' }
            Invoke-GitHubApi "/repos/$Repo/issues/$n/labels" 'PUT' @{ labels = $current } | Out-Null
            $state['smoked'][$key] = $sha
            Write-WatchLog "PR #$n 已打 smoke-passed（head $short）。"
        } else {
            $state['failed'][$key] = $sha
            Write-WatchLog "PR #$n 冒烟未通过，未打标签；等新 head 再跑。"
        }
        Save-State $state
    }
}

Write-WatchLog "smoke-watcher 启动：repo=$Repo，轮询 ${PollSeconds}s，Once=$Once，DryRun=$DryRun"
if ($Once) {
    Invoke-OneCycle
    Write-WatchLog '单轮完成。'
} else {
    while ($true) {
        try { Invoke-OneCycle } catch { Write-WatchLog "本轮出错：$($_.Exception.Message)，下一轮重试。" }
        Start-Sleep -Seconds $PollSeconds
    }
}
