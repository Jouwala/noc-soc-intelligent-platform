<#
  smoke-local.ps1 — 项目内冒烟副本（.smoke）的冒烟薄壳（替代 Grok Routine 的冒烟部分）
  ============================================================================
  目标：把 Grok 每次在 D:\02 上手工跑的 §8.1–§8.4 + 离线 pytest，变成
  一条确定性脚本，0 token、0 LLM 判断。结果格式与 Grok 在 PR 下的
  回帖一致，下游 auto-merge 只认 `smoke-passed` 标签（由 watcher
  在通过后打，本脚本只写报告，不打标签）。

  只在项目内冒烟副本里跑（默认 <项目>\.smoke，由 ops\deploy\provision-smoke-runner.ps1
  生成；.smoke-runner 是它的身份标记）。真正的检查委托给已验证的
  ops\smoke\smoke.ps1（隐藏控制台、隔离等待 start_all/stop_all、
  注册表 Delegation 用完即恢复）。本脚本负责：UTF-8 BOM 可被
  PS 5.1 解析、TMP\smoke.lock、校验当前 HEAD 与 -HeadSha 一致
  （不 checkout / 不删分支；checkout 由调用方负责，watcher 会在
  冒烟前切到 PR head 并在 finally 里切回）、把结果写成
  TMP\smoke-pr-<PR>-<sha7>.md（干跑写 .dryrun.md，不覆盖真跑报告）。

  用法（在冒烟副本根目录，或用 -File 绝对路径）：
    powershell -NoProfile -ExecutionPolicy Bypass -File <项目>\.smoke\ops\deploy\smoke-local.ps1 -PrNumber 44 -HeadSha <40位>
    powershell -NoProfile -ExecutionPolicy Bypass -File <项目>\.smoke\ops\deploy\smoke-local.ps1 -PrNumber 44 -HeadSha <40位> -DryRun

  退出码：0 通过（含「仅已知 NUR 失败被忽略」）；1 冒烟失败；
          2 前置条件不满足（blocked：目录不对、HEAD 不匹配、锁被占用等）。
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

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$ScriptVersion = 'smoke-local.ps1 v1.1.0'
$StartedAt = Get-Date
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$TmpDir = Join-Path $Root 'TMP'
$HeadSha = $HeadSha.Trim().ToLowerInvariant()
if ($HeadSha.Length -ge 7) { $ShortSha = $HeadSha.Substring(0, 7) } else { $ShortSha = $HeadSha }
$ReportFile = Join-Path $TmpDir ("smoke-pr-{0}-{1}.md" -f $PrNumber, $ShortSha)
$LogFile = Join-Path $TmpDir ("smoke-pr-{0}-{1}.log" -f $PrNumber, $ShortSha)
if ($DryRun) {
    # 干跑不得覆盖同 PR/同 SHA 的真跑报告与日志：加 .dryrun 后缀区分。
    # （原先两者同名，跑一次 dry run 就把上一次真跑的证据冲掉了。）
    $ReportFile = Join-Path $TmpDir ("smoke-pr-{0}-{1}.dryrun.md" -f $PrNumber, $ShortSha)
    $LogFile = Join-Path $TmpDir ("smoke-pr-{0}-{1}.dryrun.log" -f $PrNumber, $ShortSha)
}
$LockPath = Join-Path $TmpDir 'smoke.lock'
$SmokePs1 = Join-Path $Root 'ops\smoke\smoke.ps1'
$Results = @{}
$Notes = @()
$FailItems = @()  # Convert-SmokeResultToReport 填：结果表 FAIL 行 + 缺失的预期检查行（Issue #150）
$script:LockOwned = $false
$script:LockStream = $null
$script:ExitCode = 1

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Message
    Write-Host $line
    try { Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch { }
}

function Get-HeadShaFull {
    try { return ((& git -C $Root rev-parse HEAD 2>$null) | Out-String).Trim().ToLowerInvariant() } catch { return '' }
}

function Resolve-BaseRef {
    $candidates = @()
    $candidates += @('platform/main', 'origin/main', 'main')
    foreach ($c in $candidates) {
        & git -C $Root rev-parse --verify $c 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $c }
    }
    return ''
}

function Get-ChangedPaths {
    param([string]$Base)
    $result = New-Object psobject -Property @{ Ok = $false; Paths = @(); Detail = '' }
    if (-not $Base) { return $result }
    $raw = & git -C $Root diff --name-only "$Base...HEAD" 2>&1
    if ($LASTEXITCODE -ne 0) {
        $result.Detail = "exit=$LASTEXITCODE out=$raw"
        return $result
    }
    $out = @()
    foreach ($p in @($raw)) {
        $n = ("$p".Trim() -replace '\\', '/')
        if ($n) { $out += $n }
    }
    $result.Ok = $true
    $result.Paths = $out
    return $result
}

function Test-PathMatchFe {
    param([string]$Path)
    return $Path -like 'apps/my_app/frontend/*'
}

function Test-PathMatchPortal {
    param([string]$Path)
    if ($Path -notlike 'apps/portal/*') { return $false }
    if ($Path -like 'apps/portal/docs/*') { return $false }
    if ($Path -like 'apps/portal/scripts/whatsapp_analysis/*') { return $false }
    $leaf = ($Path -split '/')[-1]
    if ($leaf -in @('package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'npm-shrinkwrap.json', 'package.json', 'index.html')) { return $true }
    if ($leaf -like 'vite.config.*' -or $leaf -like 'tsconfig*') { return $true }
    if ($Path -like 'apps/portal/src/*' -or $Path -like 'apps/portal/public/*' -or $Path -like 'apps/portal/server/*') { return $true }
    return $false
}

function Get-FeProbeText {
    # 干跑也做真实比较，但措辞必须标明这是探测，不得写成「已经无需重建」。
    if ($SkipFrontendBuild) {
        return '干跑：传入了 -SkipFrontendBuild，真跑将跳过前端重建。'
    }
    $baseUsed = Resolve-BaseRef
    if (-not $baseUsed) {
        return '干跑：找不到 platform/main、origin/main 或 main，无法比较前端改动；真跑将因基线缺失而失败（除非强制构建）。'
    }
    $diff = Get-ChangedPaths -Base $baseUsed
    if (-not $diff.Ok) {
        return "干跑：git diff $baseUsed...HEAD 失败，无法判断是否要重建。$($diff.Detail)"
    }
    $feHits = @($diff.Paths | Where-Object { Test-PathMatchFe $_ })
    $portalHits = @($diff.Paths | Where-Object { Test-PathMatchPortal $_ })
    $detail = "base=$baseUsed changed=$($diff.Paths.Count) feHits=$($feHits.Count) portalHits=$($portalHits.Count)"
    if ($feHits.Count -gt 0 -or $portalHits.Count -gt 0) {
        return "干跑：已相对 $baseUsed 比较，$detail。真跑将执行 npm run build（前端/lockfile 有改动）。"
    }
    return "干跑：已相对 $baseUsed 比较，$detail。真跑将沿用现有 apps\_fe_dist（前端与 lockfile 无改动）。"
}

function Test-SmokeHolderAlive {
    param([int]$OwnerPid = 0)
    if ($OwnerPid -gt 0 -and $OwnerPid -ne $PID) {
        $op = Get-CimInstance Win32_Process -Filter "ProcessId=$OwnerPid" -ErrorAction SilentlyContinue
        if ($op -and ("$($op.CommandLine)" -match 'smoke\.ps1|smoke-local\.ps1|start_all\.ps1|stop_all\.ps1')) {
            return $true
        }
    }
    $mine = $PID
    $hits = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and ($_.ProcessId -ne $mine) -and ($_.ParentProcessId -ne $mine) -and (
            $_.CommandLine -match 'ops\\smoke\\smoke\.ps1|ops\\deploy\\smoke-local\.ps1'
        )
    }
    return [bool]$hits
}

function Read-LockInfo {
    $info = @{ Text = ''; OwnerPid = 0; Owner = '' }
    if (-not (Test-Path -LiteralPath $LockPath)) { return $info }
    try { $info.Text = [IO.File]::ReadAllText($LockPath).Trim() } catch { $info.Text = '' }
    $owner = 0
    if ($info.Text -match 'pid=(\d+)') { [void][int]::TryParse($Matches[1], [ref]$owner) }
    $info.OwnerPid = $owner
    if ($info.Text -match 'owner=(\S+)') { $info.Owner = $Matches[1] }
    return $info
}

function New-SmokeLock {
    param([int]$WaitSec = 1800)
    $deadline = (Get-Date).AddSeconds($WaitSec)
    while ($true) {
        if (Test-Path -LiteralPath $LockPath) {
            $info = Read-LockInfo
            $held = Test-SmokeHolderAlive -OwnerPid $info.OwnerPid
            if ($held) {
                if ((Get-Date) -ge $deadline) {
                    throw "TMP\smoke.lock 仍被占用（$($info.Text)），等待 ${WaitSec}s 后放弃。"
                }
                Write-Log "锁被占用，等待中：$($info.Text)"
                Start-Sleep -Seconds 10
                continue
            }
            throw "TMP\smoke.lock 已存在但没有活动持有者（可能是残留）。内容：$($info.Text)。本脚本不删除他人的锁，退出码 2。请确认无冒烟后再删除该文件。"
        }
        try {
            $fs = [IO.File]::Open($LockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
            $msg = "PR=$PrNumber sha=$ShortSha owner=smoke-local pid=$PID created=$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ss') purpose=smoke-local"
            $bytes = [Text.Encoding]::UTF8.GetBytes($msg)
            $fs.Write($bytes, 0, $bytes.Length)
            $fs.Flush()
            $script:LockStream = $fs
            $script:LockOwned = $true
            Write-Log "已创建锁：$msg"
            return
        } catch [System.IO.IOException] {
            Start-Sleep -Seconds 2
            if ((Get-Date) -ge $deadline) {
                throw "无法创建 TMP\smoke.lock（CreateNew 失败）：$($_.Exception.Message)"
            }
        }
    }
}

function Remove-SmokeLockIfOwned {
    if (-not $script:LockOwned) { return }
    try { if ($script:LockStream) { $script:LockStream.Close(); $script:LockStream.Dispose() } } catch { }
    $script:LockStream = $null
    if (Test-Path -LiteralPath $LockPath) {
        $info = Read-LockInfo
        if ($info.Text -match 'owner=smoke-local' -and $info.Text -match ("pid=$PID")) {
            try {
                Remove-Item -LiteralPath $LockPath -Force -ErrorAction Stop
                Write-Log "已删除本脚本持有的锁 $LockPath"
            } catch {
                Write-Log "删除本脚本的锁失败：$($_.Exception.Message)"
            }
        } else {
            Write-Log "锁内容已不是本脚本所写，不删除：$($info.Text)"
        }
    }
    $script:LockOwned = $false
}

function Get-MdCell {
    param([string]$Text, [string]$CheckNeedle)
    $lines = $Text -split "`r?`n"
    $inTable = $false
    foreach ($ln in $lines) {
        if ($ln -match '^\|.*\|.*\|') {
            if ($ln -match '^\|[-| ]+\|$') { $inTable = $true; continue }
            $cols = @($ln.Split('|') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
            if ($cols.Count -ge 3 -and ($cols[1] -like "*$CheckNeedle*")) {
                return @{ Result = $cols[0]; Check = $cols[1]; Detail = $cols[2] }
            }
        }
    }
    return $null
}

function Get-MdFailRows {
    param([string]$Text)
    # 结果表里所有 FAIL 行（检查名 + 详情），供 PR 评论的「失败项」一节用。
    # #145 的教训：评论只写各节「通过/失败」，失败项本身没列出来，查不到原因。
    $out = @()
    foreach ($ln in ($Text -split "`r?`n")) {
        if ($ln -match '^\|.*\|.*\|') {
            $cols = @($ln.Split('|') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
            if ($cols.Count -ge 3 -and $cols[0] -eq 'FAIL') {
                $out += @{ Check = $cols[1]; Detail = $cols[2] }
            }
        }
    }
    return $out
}

function Convert-SmokeResultToReport {
    param([string]$ResultMdPath, [string]$VerdictHint)
    $txt = ''
    if (Test-Path -LiteralPath $ResultMdPath) {
        try { $txt = [IO.File]::ReadAllText($ResultMdPath) } catch { $txt = '' }
    }
    # 失败项清单：结果表里所有 FAIL 行 + 下面各节补记的缺失行。
    # 总结论（`$passLine）仍以结果表的 RESULT 为准，不在这里改写——
    # 清单只解决「评论里查不到失败原因」（Issue #150）。
    $script:FailItems = @(Get-MdFailRows $txt)
    $row81 = Get-MdCell $txt '§8.1'
    $row81b = Get-MdCell $txt '前端无需重建'
    $row81c = Get-MdCell $txt 'npm run build (apps\my_app\frontend'
    $row82dir = Get-MdCell $txt 'SHOW data_directory'
    $row82auth = Get-MdCell $txt 'local_authority_tools'
    $rowH = Get-MdCell $txt '/api/health'
    $rowWo = Get-MdCell $txt 'wo-dashboard/overview'
    $rowPortal = Get-MdCell $txt '(3) 门户'
    $row8080 = Get-MdCell $txt '8080/myapp'
    $row8443 = Get-MdCell $txt '8443/myapp'
    $rowHash = Get-MdCell $txt 'sha256'
    $rowNt = Get-MdCell $txt 'nginx -t'
    $rowStop = Get-MdCell $txt 'stop_all'
    $row80443 = Get-MdCell $txt '80/443'
    $rowFree = Get-MdCell $txt '已释放'
    $rowPy = Get-MdCell $txt 'pytest'

    $fe = @()
    if ($row81) { $fe += ($row81.Detail) }
    if ($row81b) { $fe += $row81b.Check + '：' + $row81b.Detail }
    elseif ($row81c) { $fe += $row81c.Check + '：' + $row81c.Detail }
    if ($fe.Count -eq 0) { $fe += '见 smoke.ps1 报告。' }
    $Results['8.1'] = "通过。" + ($fe -join '；')
    if ($row81b -and $row81b.Result -eq 'FAIL') { $Results['8.1'] = "失败。" + ($fe -join '；') }
    if ($row81c -and $row81c.Result -eq 'FAIL') { $Results['8.1'] = "失败。" + ($fe -join '；') }

    $d2 = @()
    if ($row82dir) { $d2 += $row82dir.Detail }
    if ($row82auth) { $d2 += $row82auth.Detail }
    $ok82 = $true
    foreach ($e in @(
        @{ Row = $row82dir; Label = '§8.2 SHOW data_directory' },
        @{ Row = $row82auth; Label = '§8.2 local_authority_tools check' }
    )) {
        if (-not $e.Row) {
            $ok82 = $false
            $script:FailItems += @{ Check = "未找到检查行：$($e.Label)"; Detail = '结果表里没有这一行，冒烟未执行该检查（见 D:\02 的完整报告）' }
        } elseif ($e.Row.Result -ne 'PASS') {
            $ok82 = $false
        }
    }
    $Results['8.2'] = ($(if ($ok82) { '通过。' } else { '失败。' }) + ($d2 -join '；'))

    $hashLine = ''
    if ($rowHash -and $rowHash.Detail -match '([0-9A-Fa-f]{16,})') { $hashLine = "；index.html sha256 ``$($Matches[1])``" }
    # §8.3 的 7 项检查都必须在结果表里出现：缺失的行说明该检查根本没跑，
    # 以往按「通过」算（$ok83 只看找到的行），#145 的评论因此写着「全部通过」
    # 却判失败、且查不到原因（Issue #150）。缺失即失败，并记进失败项清单。
    $ok83 = $true
    $expected83 = @(
        @{ Row = $rowH;      Label = '(1) /api/health' },
        @{ Row = $rowWo;     Label = '(2) 只读业务接口' },
        @{ Row = $rowPortal; Label = '(3) 门户 9277' },
        @{ Row = $row8080;   Label = '(4a) nginx 8080' },
        @{ Row = $row8443;   Label = '(4b) nginx 8443' },
        @{ Row = $rowHash;   Label = '(5) index.html sha256' },
        @{ Row = $rowNt;     Label = '(6) nginx -t' }
    )
    foreach ($e in $expected83) {
        if (-not $e.Row) {
            $ok83 = $false
            $script:FailItems += @{ Check = "未找到检查行：$($e.Label)"; Detail = '结果表里没有这一行，冒烟未执行该检查（见 D:\02 的完整报告）' }
        } elseif ($e.Row.Result -ne 'PASS') {
            $ok83 = $false
        }
    }
    $healthBit = '1. /api/health：见报告'
    if ($rowH) { $healthBit = '1. /api/health：' + $rowH.Detail }
    $woBit = '2. 只读业务接口：见报告'
    if ($rowWo) { $woBit = '2. 只读业务接口返回非空：' + $rowWo.Detail }
    $hashBit = '5. index.html sha256 未找到检查行'
    if ($rowHash) {
        if ($rowHash.Result -eq 'PASS') { $hashBit = "5. index.html sha256 一致$hashLine" }
        else { $hashBit = "5. index.html sha256 失败：$($rowHash.Detail)" }
    }
    $Results['8.3'] = "$(if ($ok83) { '全部通过。' } else { '失败。' })$healthBit；$woBit；3. 门户 9277：$(if ($rowPortal) { $rowPortal.Detail } else { '未找到检查行' })；4. nginx 8080/8443：$(if ($row8080) { $row8080.Detail } else { '未找到检查行' }) / $(if ($row8443) { $row8443.Detail } else { '未找到检查行' })；$hashBit；6. nginx -t：$(if ($rowNt) { $rowNt.Detail } else { '未找到检查行' })"

    $s4 = @()
    if ($rowStop) { $s4 += $rowStop.Check + '：' + $rowStop.Detail }
    if ($rowFree) { $s4 += $rowFree.Detail }
    if ($row80443) { $s4 += $row80443.Detail }
    $s4 += '预先存在的 realtime_proxy 8887/8890 未动。'
    $Results['8.4'] = ($s4 -join ' ')

    if ($rowPy) { $Results['pytest'] = "``$($rowPy.Detail)``" }
    else { $Results['pytest'] = '未解析到 pytest 摘要。' }

    $passLine = ''
    if ($txt -match 'RESULT:\s*(PASS|FAIL|REFUSED)') { $passLine = $Matches[1] }
    elseif ($txt -match 'TOTAL=\d+\s+FAIL=(\d+)') { if ($Matches[1] -eq '0') { $passLine = 'PASS' } else { $passLine = 'FAIL' } }
    if ($VerdictHint) { $Results['结论源'] = $VerdictHint }
    if ($passLine -eq 'REFUSED') {
        # 预检拒绝 ≠ 冒烟失败（2026-11-16 实测踩坑）：
        # smoke.ps1 预检发现 02 端口被占（或 HEAD 不匹配）时**什么都没验证**就 exit 2。
        # 旧实现把它写成「失败」，报告模板又把 §8.1–§8.3 填成「通过」，
        # watcher 再据此打 state:qa-failed，并把这个 head 记进 failed 永不再跑。
        # 这里统一改成「未执行」，并带上预检给出的拒绝原因，供报告与 watcher 判读。
        $pre = Get-MdCell $txt '预检'
        $why = $(if ($pre) { "$($pre.Detail)" } else { '见 ops\smoke\smoke.ps1 报告' })
        $script:FailItems = @()
        $Results['结论'] = '未执行：预检拒绝'
        foreach ($k in @('8.1', '8.2', '8.3', '8.4', 'pytest')) {
            $Results[$k] = "未执行：预检拒绝，冒烟脚本没有启动或停止任何进程。原因 $why"
        }
        return
    }
    if ($passLine -eq 'PASS') { $Results['结论'] = '通过' }
    else { $Results['结论'] = '失败' }

    $noteBlock = $false
    foreach ($ln in ($txt -split "`r?`n")) {
        if ($ln -match '^## 备注') { $noteBlock = $true; continue }
        if ($noteBlock) {
            if ($ln -match '^##' -or $ln -match '^TOTAL=') { break }
            if ($ln -match '^\s*-\s*(.+)$') { $Notes += $Matches[1] }
        }
    }
}

function Write-SmokeReport {
    $EndedAt = Get-Date
    $Duration = [math]::Round(($EndedAt - $StartedAt).TotalSeconds, 1)
    $passed = ($Results['结论'] -eq '通过')
    $isRefused = ($Results['结论'] -like '未执行*')
    $verdict = $(if ($passed) { '通过' } elseif ($isRefused) { '未执行（预检拒绝）' } else { '失败' })
    if ($DryRun) { $verdict = '干跑' }
    $report = @()
    $report += "## D:\02 冒烟结果：$verdict"
    $report += ''
    $report += "- 测试提交：``$HeadSha``"
    $report += "- 由本地冒烟脚本运行（$ScriptVersion），实际检查委托 ops\smoke\smoke.ps1（-Pytest）；无 LLM 参与"
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
    if ($script:FailItems.Count -gt 0) {
        $report += ''
        $report += '### 失败项'
        foreach ($f in $script:FailItems) { $report += "- $($f.Check)：$($f.Detail)" }
    }
    if ($Notes.Count -gt 0) {
        $report += ''
        $report += '### 备注'
        foreach ($n in $Notes) { $report += "- $n" }
    }
    $report += ''
    $report += '### 标签'
    if ($isRefused) {
        $report += '未打 `smoke-passed`：本次**未执行**（不算失败、不触发 auto-fix），watcher 下一轮会自动重试同一个 head。'
    } else {
        $report += '未打 `smoke-passed`。本脚本不负责贴标签，由 watcher 在通过后处理。'
    }
    $utf8Bom = New-Object System.Text.UTF8Encoding $true
    [IO.File]::WriteAllText($ReportFile, (($report -join "`n") + "`n"), $utf8Bom)
    Write-Log "报告已写：$ReportFile（结论：$($Results['结论'])，耗时 ${Duration}s）"
}

# ---------- 前置守卫：只在 02 跑，绝不动生产，不 checkout ----------
# ⛔ 生产边界（2026-11-14 用户明确要求）：D:\01_olbr_portal 是生产，不许动里面任何东西。
$ProductionRoot = 'D:\01_olbr_portal'
if ($Root.TrimEnd('\') -eq $ProductionRoot -or $Root -like ($ProductionRoot + '\*')) {
    throw "拒绝执行：$Root 落在生产目录 $ProductionRoot 下面。冒烟只在 D:\02_olbr_portal 隔离副本上跑。"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root '.smoke-runner'))) {
    throw "拒绝执行：$Root 不是项目内冒烟副本（缺 .smoke-runner）。请先跑 ops\deploy\provision-smoke-runner.ps1 -Apply 生成 <项目>\.smoke。"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'ISOLATED_COPY.flag'))) {
    throw '缺少 ISOLATED_COPY.flag，拒绝执行：当前目录可能不是隔离副本。'
}
if ($HeadSha -notmatch '^[0-9a-f]{40}$') { throw "HeadSha 不是 40 位小写 hex：$HeadSha" }
if (-not (Test-Path -LiteralPath $TmpDir)) { New-Item -ItemType Directory -Force -Path $TmpDir | Out-Null }
'' | Out-File -LiteralPath $LogFile -Encoding UTF8
Write-Log "$ScriptVersion PR #$PrNumber head $ShortSha（DryRun=$DryRun）"

$actual = Get-HeadShaFull
Write-Log "当前 HEAD=$actual  传入 HeadSha=$HeadSha  分支=$((git -C $Root rev-parse --abbrev-ref HEAD 2>$null))"
if ($actual -ne $HeadSha) {
    Write-Log "拒绝：当前 HEAD 与 -HeadSha 不一致。本脚本不再自行 checkout，请调用方先切到目标提交。"
    $Results['结论'] = "失败：HEAD $actual 与 -HeadSha $HeadSha 不一致"
    $Results['8.1'] = '未跑：HEAD 不匹配，已拒绝。'
    $Results['8.2'] = '未跑：HEAD 不匹配，已拒绝。'
    $Results['8.3'] = '未跑：HEAD 不匹配，已拒绝。'
    $Results['8.4'] = '未跑：HEAD 不匹配，已拒绝。'
    $Results['pytest'] = '未跑。'
    Write-SmokeReport
    exit 2
}
if ($Remote) { Write-Log "参数 -Remote=$Remote 已忽略：本脚本不再 fetch/checkout。" }

try {
    if ($DryRun) {
        if (-not (Test-Path -LiteralPath $SmokePs1)) {
            throw "找不到已验证的 $SmokePs1，干跑也无法确认真跑路径。"
        }
        if (Test-Path -LiteralPath $LockPath) {
            $info = Read-LockInfo
            $held = Test-SmokeHolderAlive -OwnerPid $info.OwnerPid
            $Notes += "干跑时已存在 TMP\smoke.lock（held=$held）：$($info.Text)"
            Write-Log $Notes[-1]
        } else {
            $Notes += '干跑未创建 TMP\smoke.lock；真跑会以 CreateNew 占用并在结束时只删除自己的锁。'
        }
        $Results['8.1'] = Get-FeProbeText
        $Results['8.2'] = '干跑：跳过 start_all / psql / local_authority_tools check。真跑将要求 data_directory 经斜杠归一化后落在 D:\02 下，且三表 source/target 为 20/74/10。'
        $Results['8.3'] = '干跑：跳过 6 项服务检查。真跑将要求 /api/health 的 db_mode=pg，overview 响应非空，以及 9277/8080/8443、index sha256、nginx -t。'
        $Results['8.4'] = '干跑：跳过停服务与端口确认。真跑由 smoke.ps1 调用 stop_all（隐藏控制台、隔离等待），并确认 80/443 仍归 D:\01、02 端口已释放。'
        $Results['pytest'] = '干跑：跳过 pytest。真跑使用 D:\02_olbr_portal\runtime\python312\python.exe，-m "not live"，-n 8（若已安装 xdist）；与 CI quality.yml 一样 deselect 两条 test_client_is_local_trust（::ffff:127.0.0.1 实现与文档不符，另开任务）；仅忽略 2 个已知 NUR 缺数据失败。'
        $Results['结论'] = '通过'
        $script:ExitCode = 0
    } else {
        if (-not (Test-Path -LiteralPath $SmokePs1)) {
            throw "找不到已验证的 $SmokePs1。真跑把 §8.1–§8.4 与 pytest 委托给该脚本，请确认 D:\02 上存在 ops\smoke\smoke.ps1。"
        }
        New-SmokeLock -WaitSec 1800
        $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $smokeArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $SmokePs1, '-PR', "$PrNumber", '-Sha', $ShortSha, '-Pytest')
        if ($SkipFrontendBuild) { $smokeArgs += '-SkipFeBuild' }
        Write-Log ("启动 $SmokePs1 " + ($smokeArgs -join ' '))
        # 不重定向标准流：start_all 的隔离等待在 smoke.ps1 内部完成（隐藏控制台 + run_exit.cmd）。
        # 外层也不用 1>$out 包一层，以免再引入句柄继承。
        & $psExe @smokeArgs
        $smokeExit = $LASTEXITCODE
        Write-Log "smoke.ps1 退出码=$smokeExit"
        $resultMd = Join-Path $TmpDir ("smoke_result_{0}_{1}.md" -f $PrNumber, $ShortSha)
        Convert-SmokeResultToReport -ResultMdPath $resultMd -VerdictHint "smoke.ps1 exit=$smokeExit"
        if ($smokeExit -eq 0 -and $Results['结论'] -eq '通过') { $script:ExitCode = 0 }
        elseif ($smokeExit -eq 2) { $script:ExitCode = 2; if ($Results['结论'] -eq '通过') { $Results['结论'] = '失败：预检拒绝' } }
        else {
            $script:ExitCode = 1
            if ($Results['结论'] -eq '通过') { $Results['结论'] = "失败：smoke.ps1 exit=$smokeExit" }
        }
    }
} catch {
    Write-Log "冒烟失败：$($_.Exception.Message)"
    $Results['结论'] = "失败：$($_.Exception.Message)"
    if (-not $Results.ContainsKey('8.1')) { $Results['8.1'] = "失败：$($_.Exception.Message)" }
    if (-not $Results.ContainsKey('8.2')) { $Results['8.2'] = '未完成。' }
    if (-not $Results.ContainsKey('8.3')) { $Results['8.3'] = '未完成。' }
    if (-not $Results.ContainsKey('8.4')) { $Results['8.4'] = '未完成。' }
    if (-not $Results.ContainsKey('pytest')) { $Results['pytest'] = '未完成。' }
    $msg = $_.Exception.Message
    if ($msg -match '锁' -or $msg -match 'HEAD') { $script:ExitCode = 2 } else { $script:ExitCode = 1 }
} finally {
    Remove-SmokeLockIfOwned
    if (-not $Results.ContainsKey('结论')) { $Results['结论'] = '失败' }
    if (-not $Results.ContainsKey('8.1')) { $Results['8.1'] = '未写入。' }
    if (-not $Results.ContainsKey('8.2')) { $Results['8.2'] = '未写入。' }
    if (-not $Results.ContainsKey('8.3')) { $Results['8.3'] = '未写入。' }
    if (-not $Results.ContainsKey('8.4')) { $Results['8.4'] = '未写入。' }
    if (-not $Results.ContainsKey('pytest')) { $Results['pytest'] = '未写入。' }
    Write-SmokeReport
}

exit $script:ExitCode
