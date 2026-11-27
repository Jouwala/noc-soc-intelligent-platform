<#
  muse-job-runner.ps1 —— MSNOC1 文件任务队列的执行器（v1）
  ============================================================================
  原理：MuseLink 连接器只能读写 D:\02 上的文件、不能在 MSNOC1 上执行命令。
  所以用文件当队列：Muse 1 把任务 JSON 写进 D:\02_olbr_portal\TMP\muse-jobs\inbox\，
  本脚本由计划任务每 60 秒触发一次（单次执行后退出，不常驻），取最旧的一个任务认领 → 校验 → 执行 → 写结果 → 归档 → 记审计日志。

  队列根目录（固定）：D:\02_olbr_portal\TMP\muse-jobs（inbox/claimed/done/failed/results/audit.log/runner.lock）
  任务 JSON（单文件 ≤16KB，未知字段、非法 JSON 直接拒绝）：
    {"id":"20261126-1717-smoke-287","type":"smoke-rerun","payload":{"pr":287},
     "created_by":"muse-1","created_at":"2026-11-26T17:17:00Z","timeout_seconds":1800}
    id 须匹配 ^[A-Za-z0-9_-]{8,64}$；timeout_seconds 可选，默认 1800，范围 60..7200。

  v1 任务类型（只做这两类，都是幂等，可重跑）：
    smoke-rerun
        payload {"pr": <纯数字>}
        → .smoke\ops\deploy\smoke-watcher.ps1 -OnlyPr <N> -Once（-OnlyPr 已核实存在）
    restart-smoke-services
        payload {"services": ["backend", ...]}
        → .smoke\ops\deploy\stop_all.ps1 再 .smoke\ops\deploy\start_all.ps1
          （服务名只允许 start_all/stop_all 里出现的 smoke 副本服务名；现有脚本不支持单服务启停，故为整栈重启）

  ⛔ 安全红线
  · 永远不执行任务文件里的命令字符串，不用 Invoke-Expression；只按白名单调固定脚本、固定参数（PR 号纯数字、服务名只允许预设值）。
  · 只动 .smoke 副本（D:\02_olbr_portal\.smoke）：执行前校验 ISOLATED_COPY.flag 在、PRODUCTION.flag 不在；生产 D:\02 的脚本一律不调。
  · 超时默认 1800 秒，超时杀进程进 failed\；claimed\ 里超过 1 小时无结果的任务下一轮重新认领；done/failed/results 各保留最新 100 条。

  用法：见 docs/workspace/muse-job-queue.md；自测：-SelfTest
#>
[CmdletBinding()]
param(
    [string]$QueueRoot = 'D:\02_olbr_portal\TMP\muse-jobs',
    [string]$PowerShellExe = '',
    [switch]$SelfTest
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$script:MaxTaskBytes = 16KB; $script:MaxKeep = 100; $script:DefaultTimeoutSec = 1800
$script:MinTimeoutSec = 60; $script:MaxTimeoutSec = 7200
$script:ClaimStaleMinutes = 60; $script:LockStaleHours = 4; $script:AuditMaxBytes = 5MB
$script:IdPattern = '^[A-Za-z0-9_-]{8,64}$'
$script:TaskTypes = @('smoke-rerun', 'restart-smoke-services')
$script:ServiceWhitelist = @('backend', 'frontend', 'portal', 'nginx', 'postgres', 'wa_bot', 'wa_analysis')
$script:AllowedFields = @('id', 'type', 'payload', 'created_by', 'created_at', 'timeout_seconds')

function Get-UtcNow { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }
function Get-OnWindows {
    if ($PSVersionTable.PSEdition -eq 'Desktop') { return $true }
    return [bool]$IsWindows
}
function Get-PowerShellExe {
    if ($script:PowerShellExe) { return $script:PowerShellExe }
    if (($PSVersionTable.PSEdition -eq 'Core') -and -not $IsWindows) { return 'pwsh' }
    return 'powershell.exe'
}
function Get-QueueDirs {
    param([string]$Root)
    $dirs = @{ Root = $Root; Inbox = (Join-Path $Root 'inbox'); Claimed = (Join-Path $Root 'claimed'); Done = (Join-Path $Root 'done'); Failed = (Join-Path $Root 'failed'); Results = (Join-Path $Root 'results') }
    foreach ($d in @($dirs.Root, $dirs.Inbox, $dirs.Claimed, $dirs.Done, $dirs.Failed, $dirs.Results)) {
        if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }
    return $dirs
}
function Get-SmokeRoot {
    param([string]$QueueRootPath)
    # 队列根固定为 <项目>\TMP\muse-jobs，故项目根 = 上两级，冒烟副本 = 项目根\.smoke
    return (Join-Path (Split-Path (Split-Path $QueueRootPath -Parent) -Parent) '.smoke')
}
function Join-PathParts {  # 跨平台拼多段相对路径：'ops\deploy\file.ps1' 在 Linux 上会被当成一段带反斜杠的名字
    param([string]$Base, [string[]]$Parts)
    $p = $Base
    foreach ($part in $Parts) { $p = Join-Path $p $part }
    return $p
}
function Write-Audit {
    param([string]$QueueRootPath, [string]$Event, [string]$Detail)
    $log = Join-Path $QueueRootPath 'audit.log'
    try {
        $f = Get-Item -LiteralPath $log -ErrorAction SilentlyContinue
        if ($f -and $f.Length -gt $script:AuditMaxBytes) {
            Move-Item -LiteralPath $log -Destination (Join-Path $QueueRootPath 'audit.log.1') -Force -ErrorAction SilentlyContinue
        }
        Add-Content -LiteralPath $log -Value "$(Get-UtcNow) [$Event] $Detail" -Encoding UTF8 -ErrorAction Stop
    } catch { }
}
function Enter-RunnerLock {
    param([string]$LockPath)
    if (Test-Path -LiteralPath $LockPath) {
        $stale = $true
        try {
            $parts = (([System.IO.File]::ReadAllText($LockPath)).Trim() -split '\|')
            if ($parts.Count -eq 2) {
                $pidNum = 0; $ticks = [long]0
                if ([int]::TryParse($parts[0], [ref]$pidNum) -and [long]::TryParse($parts[1], [ref]$ticks)) {
                    $ageHours = ((Get-Date) - (New-Object DateTime($ticks))).TotalHours
                    if ((Get-Process -Id $pidNum -ErrorAction SilentlyContinue) -and ($ageHours -lt $script:LockStaleHours)) { $stale = $false }
                }
            }
        } catch { }
        if (-not $stale) { return $false }
        Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue
    }
    [System.IO.File]::WriteAllText($LockPath, "$PID|$((Get-Date).Ticks)")
    return $true
}
function Exit-RunnerLock {
    param([string]$LockPath)
    try {
        if (Test-Path -LiteralPath $LockPath) {
            $raw = ([System.IO.File]::ReadAllText($LockPath)).Trim()
            if ($raw -like "$PID|*") { Remove-Item -LiteralPath $LockPath -Force -ErrorAction SilentlyContinue }
        }
    } catch { }
}
function Invoke-Prune {
    param([string]$Dir, [int]$Keep)
    $files = @(Get-ChildItem -LiteralPath $Dir -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if ($files.Count -gt $Keep) {
        foreach ($f in @($files | Select-Object -Skip $Keep)) {
            Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue
        }
    }
}
function Invoke-PruneAll {
    param([hashtable]$Dirs)
    Invoke-Prune -Dir $Dirs.Done -Keep $script:MaxKeep
    Invoke-Prune -Dir $Dirs.Failed -Keep $script:MaxKeep
    Invoke-Prune -Dir $Dirs.Results -Keep $script:MaxKeep
    $cutoff = (Get-Date).AddDays(-1)
    # 子进程日志实际写出的是 <id>.out.log.stdout / <id>.out.log.stderr（restart 任务
    # 还有 .out.log.stop / .out.log.start 两段）。过滤器必须带尾随通配符 —— 原来的
    # '*.out.log' 永远匹配不到，claimed 里的日志会无上限堆积（#313）。
    foreach ($f in @(Get-ChildItem -LiteralPath $Dirs.Claimed -Filter '*.out.log*' -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $cutoff })) {
        Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue
    }
}
function Get-SafeResultId {
    param([string]$BaseName)
    $safe = ($BaseName -replace '[^A-Za-z0-9_-]', '_')
    if ($safe.Length -gt 40) { $safe = $safe.Substring(0, 40) }
    if (-not $safe) { $safe = 'invalid' }
    return "invalid-$safe-$((Get-Date).Ticks)"
}
function Write-Result {
    param([hashtable]$Dirs, [string]$ResultId, [string]$Status, [string]$Type, [int]$ExitCode,
        [string]$StartedAt, [string]$FinishedAt, [string]$OutputTail, [string]$Error)
    $obj = [ordered]@{ id = $ResultId; type = $Type; status = $Status; exit_code = $ExitCode; started_at = $StartedAt; finished_at = $FinishedAt; output_tail = $OutputTail; error = $Error }
    $final = Join-Path $Dirs.Results ("$ResultId.result.json")
    $tmp = Join-Path $Dirs.Results ("$ResultId.result.json.tmp-$PID")
    [System.IO.File]::WriteAllText($tmp, ($obj | ConvertTo-Json -Depth 3 -Compress), (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $final -Force
}
function Get-OutputTail {
    param([string[]]$Files, [int]$MaxChars = 2000)
    $text = ''
    foreach ($f in $Files) {
        if ($f -and (Test-Path -LiteralPath $f)) { try { $text += [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8) } catch { } }
    }
    if ($text.Length -gt $MaxChars) { $text = $text.Substring($text.Length - $MaxChars) }
    return $text
}

function Get-IdFromRawText {  # 校验失败时也尽量让结果文件以任务 id 命名（只做正则提取）
    param([string]$Text)
    $m = [regex]::Match($Text, '"id"\s*:\s*"([A-Za-z0-9_-]{8,64})"')
    if ($m.Success) { return $m.Groups[1].Value }
    return ''
}
# ------------------------------------------------------------------ 任务校验
function Test-TaskFile {
    param([string]$Path)
    $file = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    $bad = { param([string]$Msg, [string]$Raw)
        $rid = if ($Raw) { Get-IdFromRawText -Text $Raw } else { '' }
        if (-not $rid) { $rid = (Get-SafeResultId -BaseName $file.BaseName) }
        return @{ Ok = $false; Error = $Msg; ResultId = $rid } }
    if (-not $file) { return @{ Ok = $false; Error = "任务文件不存在：$Path"; ResultId = (Get-SafeResultId -BaseName 'missing') } }
    if ($file.Length -gt $script:MaxTaskBytes) {
        $head = if ($file.Length -lt 1MB) { [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8) } else { '' }
        return (& $bad "任务文件超过 16KB（$($file.Length) 字节），拒绝" $head)
    }
    try { $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8) }
    catch { return (& $bad "任务文件读失败：$($_.Exception.Message)" '') }
    try { $obj = ($raw | ConvertFrom-Json -ErrorAction Stop) }
    catch { return (& $bad "非法 JSON：$($_.Exception.Message)" $raw) }
    if (-not $obj -or ($obj -isnot [PSCustomObject])) { return (& $bad '任务 JSON 顶层必须是对象' $raw) }
    foreach ($f in @($obj.PSObject.Properties.Name)) {
        if ($script:AllowedFields -notcontains $f) { return (& $bad "未知字段：$f" $raw) }
    }
    $id = $obj.id
    if (($null -eq $id) -or ($id -isnot [string]) -or ($id -notmatch $script:IdPattern)) {
        return (& $bad "非法 id（须匹配 $($script:IdPattern)）：$id")
    }
    $type = $obj.type
    if (($null -eq $type) -or ($script:TaskTypes -notcontains $type)) {
        return @{ Ok = $false; Error = "非法 type（只允许 $($script:TaskTypes -join ' / ')）：$type"; ResultId = $id }
    }
    $payload = $obj.payload
    if (-not $payload -or ($payload -isnot [PSCustomObject])) {
        return @{ Ok = $false; Error = 'payload 必须是对象'; ResultId = $id }
    }
    $task = @{ Id = $id; Type = $type; StartedAt = (Get-UtcNow); TimeoutSec = $script:DefaultTimeoutSec; CreatedBy = '' }
    if ($null -ne $obj.created_by) {
        if ($obj.created_by -isnot [string]) { return @{ Ok = $false; Error = 'created_by 必须是字符串'; ResultId = $id } }
        $task.CreatedBy = $obj.created_by
    }
    # 注意：ConvertFrom-Json 会把 ISO 8601 时间字符串解析成 [datetime]，故 created_at 接受字符串或日期
    if (($null -ne $obj.created_at) -and ($obj.created_at -isnot [string]) -and ($obj.created_at -isnot [datetime])) {
        return @{ Ok = $false; Error = 'created_at 必须是字符串或日期'; ResultId = $id }
    }
    if ($null -ne $obj.timeout_seconds) {
        $t = $obj.timeout_seconds
        if (($t -isnot [int]) -and ($t -isnot [long])) { return @{ Ok = $false; Error = 'timeout_seconds 必须是整数'; ResultId = $id } }
        if ([int]$t -lt $script:MinTimeoutSec -or [int]$t -gt $script:MaxTimeoutSec) {
            return @{ Ok = $false; Error = "timeout_seconds 超出范围 $($script:MinTimeoutSec)..$($script:MaxTimeoutSec)：$t"; ResultId = $id }
        }
        $task.TimeoutSec = [int]$t
    }
    if ($type -eq 'smoke-rerun') {
        $keys = @($payload.PSObject.Properties.Name)
        if (($keys.Count -ne 1) -or ($keys[0] -ne 'pr')) { return @{ Ok = $false; Error = 'smoke-rerun 的 payload 只能含 pr'; ResultId = $id } }
        $pr = $payload.pr
        if (($pr -is [int]) -or ($pr -is [long])) {
            if ([long]$pr -lt 1 -or [long]$pr -gt [int]::MaxValue) { return @{ Ok = $false; Error = "pr 超出范围：$pr"; ResultId = $id } }
            $task.Pr = [int]$pr
        } elseif (($pr -is [string]) -and ($pr -match '^\d+$')) {
            $n = 0
            if (-not [int]::TryParse($pr, [ref]$n)) { return @{ Ok = $false; Error = "pr 超出范围：$pr"; ResultId = $id } }
            $task.Pr = $n
        } else { return @{ Ok = $false; Error = "pr 必须是纯数字：$pr"; ResultId = $id } }
    } else {
        $keys = @($payload.PSObject.Properties.Name)
        if (($keys.Count -ne 1) -or ($keys[0] -ne 'services')) { return @{ Ok = $false; Error = 'restart-smoke-services 的 payload 只能含 services'; ResultId = $id } }
        $services = $payload.services
        if (($null -eq $services) -or ($services -isnot [System.Array]) -or ($services.Count -eq 0)) {
            return @{ Ok = $false; Error = 'services 必须是非空数组'; ResultId = $id }
        }
        $clean = @()
        foreach ($s in $services) {
            if (($s -isnot [string]) -or ($script:ServiceWhitelist -notcontains $s)) {
                return @{ Ok = $false; Error = "非法服务名（只允许 $($script:ServiceWhitelist -join ' / ')）：$s"; ResultId = $id }
            }
            if ($clean -notcontains $s) { $clean += $s }
        }
        $task.Services = $clean
    }
    return @{ Ok = $true; Task = $task }
}

# ------------------------------------------------------------------ 子进程
function Invoke-ChildScript {
    param([string]$ScriptPath, [string[]]$Arguments, [string]$WorkDir, [int]$TimeoutSec, [string]$OutLogPath)
    $outFile = "$OutLogPath.stdout"; $errFile = "$OutLogPath.stderr"
    # ⚠️ Windows PowerShell 5.1：Start-Process 一旦带 RedirectStandardOutput/Error，
    # WaitForExit 之后 $proc.ExitCode 恒为 $null（实测），runner 会把所有任务判成 failed。
    # 所以不再让 Start-Process 重定向，而是生成一个临时 wrapper（.cmd / .sh），由 shell
    # 自己做 `> 文件 2> 文件`，父进程只等退出（超时照旧可杀）；shell 的退出码 = 子脚本退出码。
    $exe = Get-PowerShellExe
    $quotedArgs = (($Arguments | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' ')
    $inner = '"' + $exe + '" -NoProfile -ExecutionPolicy Bypass -File "' + $ScriptPath + '" ' + $quotedArgs + ' > "' + $outFile + '" 2> "' + $errFile + '"'
    $wrapperBase = Join-Path ([System.IO.Path]::GetTempPath()) ("muse-child-" + [Guid]::NewGuid().ToString('N'))
    if (Get-OnWindows) {
        $wrapper = "$wrapperBase.cmd"
        [System.IO.File]::WriteAllText($wrapper, "@echo off`r`n$inner`r`n", (New-Object System.Text.ASCIIEncoding))
        $splat = @{ FilePath = 'cmd.exe'; ArgumentList = @('/c', "`"$wrapper`"")
            WorkingDirectory = $WorkDir; PassThru = $true; WindowStyle = 'Hidden' }
    } else {
        $wrapper = "$wrapperBase.sh"
        [System.IO.File]::WriteAllText($wrapper, "#!/bin/sh`n$inner`n", (New-Object System.Text.UTF8Encoding($false)))
        $splat = @{ FilePath = '/bin/sh'; ArgumentList = @($wrapper); WorkingDirectory = $WorkDir; PassThru = $true }
    }
    try { $proc = Start-Process @splat }
    catch { Remove-Item -LiteralPath $wrapper -Force -ErrorAction SilentlyContinue
        return @{ ExitCode = -1; TimedOut = $false; OutputTail = ''; Error = "启动子进程失败：$($_.Exception.Message)" } }
    $exited = $false
    try { $exited = $proc.WaitForExit($TimeoutSec * 1000) } catch { $exited = $proc.HasExited }
    if (-not $exited) {
        try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
        try { $proc.WaitForExit() } catch { }
        Remove-Item -LiteralPath $wrapper -Force -ErrorAction SilentlyContinue
        return @{ ExitCode = -1; TimedOut = $true; OutputTail = (Get-OutputTail -Files @($outFile, $errFile)); Error = "子进程超时（>${TimeoutSec}s），已杀掉" }
    }
    $code = 0
    try { $code = $proc.ExitCode } catch { $code = -1 }
    if ($null -eq $code) { $code = -1 }
    Remove-Item -LiteralPath $wrapper -Force -ErrorAction SilentlyContinue
    return @{ ExitCode = $code; TimedOut = $false; OutputTail = (Get-OutputTail -Files @($outFile, $errFile)); Error = '' }
}

# ------------------------------------------------------------------ 守卫与执行
function Test-SmokeRoot {
    param([string]$QueueRootPath)
    $smoke = Get-SmokeRoot -QueueRootPath $QueueRootPath
    if (-not (Test-Path -LiteralPath $smoke)) { return @{ Ok = $false; Error = "冒烟副本不存在：$smoke" } }
    if (Test-Path -LiteralPath (Join-Path $smoke 'PRODUCTION.flag')) { return @{ Ok = $false; Error = "拒绝：$smoke 含 PRODUCTION.flag，不是隔离副本" } }
    if (-not (Test-Path -LiteralPath (Join-Path $smoke 'ISOLATED_COPY.flag'))) { return @{ Ok = $false; Error = "拒绝：$smoke 缺 ISOLATED_COPY.flag" } }
    return @{ Ok = $true; SmokeRoot = $smoke }
}
function Invoke-Task {
    param([hashtable]$Task, [hashtable]$Dirs, [string]$QueueRootPath)
    $deadline = (Get-Date).AddSeconds($Task.TimeoutSec)
    $outLog = Join-Path $Dirs.Claimed ("$($Task.Id).out.log")
    $g = Test-SmokeRoot -QueueRootPath $QueueRootPath
    if (-not $g.Ok) { return @{ ExitCode = 2; TimedOut = $false; OutputTail = ''; Error = $g.Error } }
    if ($Task.Type -eq 'smoke-rerun') {
        $watcher = Join-PathParts -Base $g.SmokeRoot -Parts @('ops', 'deploy', 'smoke-watcher.ps1')
        if (-not (Test-Path -LiteralPath $watcher)) { return @{ ExitCode = 2; TimedOut = $false; OutputTail = ''; Error = "找不到冒烟脚本：$watcher" } }
        if (-not (Test-Path -LiteralPath (Join-Path $g.SmokeRoot '.smoke-runner'))) {
            return @{ ExitCode = 2; TimedOut = $false; OutputTail = ''; Error = "拒绝：$($g.SmokeRoot) 缺 .smoke-runner 标记" }
        }
        return (Invoke-ChildScript -ScriptPath $watcher -Arguments @('-OnlyPr', "$($Task.Pr)", '-Once') -WorkDir $g.SmokeRoot -TimeoutSec $Task.TimeoutSec -OutLogPath $outLog)
    }
    # restart-smoke-services：整栈重启（现有脚本不支持单服务启停）
    $stop = Join-PathParts -Base $g.SmokeRoot -Parts @('ops', 'deploy', 'stop_all.ps1')
    $start = Join-PathParts -Base $g.SmokeRoot -Parts @('ops', 'deploy', 'start_all.ps1')
    if (-not (Test-Path -LiteralPath $stop)) { return @{ ExitCode = 2; TimedOut = $false; OutputTail = ''; Error = "找不到停止脚本：$stop" } }
    if (-not (Test-Path -LiteralPath $start)) { return @{ ExitCode = 2; TimedOut = $false; OutputTail = ''; Error = "找不到启动脚本：$start" } }
    $rStop = Invoke-ChildScript -ScriptPath $stop -Arguments @() -WorkDir $g.SmokeRoot -TimeoutSec $Task.TimeoutSec -OutLogPath "$outLog.stop"
    if ($rStop.TimedOut) { return $rStop }
    if ($rStop.ExitCode -ne 0) {
        return @{ ExitCode = $rStop.ExitCode; TimedOut = $false; OutputTail = $rStop.OutputTail; Error = "stop_all 退出码 $($rStop.ExitCode)，未执行 start_all（守卫可能未过）" }
    }
    $remain = [int](($deadline - (Get-Date)).TotalSeconds)
    if ($remain -lt $script:MinTimeoutSec) {
        return @{ ExitCode = -1; TimedOut = $true; OutputTail = $rStop.OutputTail; Error = "stop_all 耗时过长，start_all 剩余时间不足 ${remain}s" }
    }
    $rStart = Invoke-ChildScript -ScriptPath $start -Arguments @() -WorkDir $g.SmokeRoot -TimeoutSec $remain -OutLogPath "$outLog.start"
    $rStart.OutputTail = ($rStop.OutputTail + "`n" + $rStart.OutputTail)
    return $rStart
}

# ------------------------------------------------------------------ 重认领
function Invoke-ReclaimStale {
    param([hashtable]$Dirs)
    foreach ($cf in @(Get-ChildItem -LiteralPath $Dirs.Claimed -Filter *.json -File -ErrorAction SilentlyContinue)) {
        if (((Get-Date) - $cf.LastWriteTime).TotalMinutes -le $script:ClaimStaleMinutes) { continue }
        # 已有结果文件 = 上轮已跑完、只是归档失败：按结果归档，不重跑
        $resultId = ''
        try {
            $t = [System.IO.File]::ReadAllText($cf.FullName, [System.Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop
            if (($t.id -is [string]) -and ($t.id -match $script:IdPattern)) { $resultId = $t.id }
        } catch { }
        if ($resultId) {
            $rf = Join-Path $Dirs.Results ("$resultId.result.json")
            if (Test-Path -LiteralPath $rf) {
                try {
                    $r = [System.IO.File]::ReadAllText($rf, [System.Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop
                    $dest = if ($r.status -eq 'completed') { $Dirs.Done } else { $Dirs.Failed }
                    Move-Item -LiteralPath $cf.FullName -Destination (Join-Path $dest $cf.Name) -Force -ErrorAction Stop
                    continue
                } catch { }
            }
        }
        Move-Item -LiteralPath $cf.FullName -Destination (Join-Path $Dirs.Inbox $cf.Name) -Force -ErrorAction SilentlyContinue
        Write-Audit -QueueRootPath $Dirs.Root -Event 'reclaim' -Detail "claimed 超时重新认领：$($cf.Name)"
    }
}

# ------------------------------------------------------------------ 主流程
function Invoke-RunnerOnce {
    param([string]$QueueRootPath, [string]$PowerShellExeParam)
    # PowerShell 把 `Get-OnWindows -and (...)` 解析成「命令 + 参数」而不是布尔表达式，
    # 函数返回 $true 就直接进 throw —— Windows 上 runner 一律拒绝启动（SelfTest 也起不来）。
    # 给函数调用加括号，才是真正的 `-and` 表达式。
    if ((Get-OnWindows) -and ($QueueRootPath -notmatch '^[A-Za-z]:\\')) { throw "QueueRoot 必须是绝对路径：$QueueRootPath" }
    $script:PowerShellExe = $PowerShellExeParam
    $dirs = Get-QueueDirs -Root $QueueRootPath
    $lockPath = Join-Path $QueueRootPath 'runner.lock'
    if (-not (Enter-RunnerLock -LockPath $lockPath)) { Write-Host '已有 runner 在运行，本轮退出。'; return 0 }
    try {
        Invoke-ReclaimStale -Dirs $dirs
        Invoke-PruneAll -Dirs $dirs
        $inbox = @(Get-ChildItem -LiteralPath $dirs.Inbox -Filter *.json -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime, Name)
        if ($inbox.Count -eq 0) { Write-Host 'inbox 为空，本轮无任务。'; return 0 }
        $file = $inbox[0]
        $claimedPath = Join-Path $dirs.Claimed $file.Name
        try { Move-Item -LiteralPath $file.FullName -Destination $claimedPath -ErrorAction Stop }
        catch { Write-Audit -QueueRootPath $QueueRootPath -Event 'claim-failed' -Detail "$($file.Name)：$($_.Exception.Message)"; return 0 }
        Write-Audit -QueueRootPath $QueueRootPath -Event 'claim' -Detail $file.Name
        $startedAt = Get-UtcNow
        $v = Test-TaskFile -Path $claimedPath
        if (-not $v.Ok) {
            Write-Result -Dirs $dirs -ResultId $v.ResultId -Status 'rejected' -Type '' -ExitCode 2 -StartedAt $startedAt -FinishedAt (Get-UtcNow) -OutputTail '' -Error $v.Error
            Move-Item -LiteralPath $claimedPath -Destination (Join-Path $dirs.Failed $file.Name) -Force -ErrorAction SilentlyContinue
            Write-Audit -QueueRootPath $QueueRootPath -Event 'rejected' -Detail "$($file.Name)：$($v.Error)"
            Invoke-PruneAll -Dirs $dirs
            return 0
        }
        $task = $v.Task
        Write-Audit -QueueRootPath $QueueRootPath -Event 'start' -Detail "id=$($task.Id) type=$($task.Type) by=$($task.CreatedBy)"
        $r = Invoke-Task -Task $task -Dirs $dirs -QueueRootPath $QueueRootPath
        $finishedAt = Get-UtcNow
        if ($r.TimedOut) { $status = 'timeout' } elseif ($r.ExitCode -eq 0) { $status = 'completed' } else { $status = 'failed' }
        Write-Result -Dirs $dirs -ResultId $task.Id -Status $status -Type $task.Type -ExitCode $r.ExitCode -StartedAt $startedAt -FinishedAt $finishedAt -OutputTail $r.OutputTail -Error $r.Error
        $dest = if ($status -eq 'completed') { $dirs.Done } else { $dirs.Failed }
        Move-Item -LiteralPath $claimedPath -Destination (Join-Path $dest $file.Name) -Force -ErrorAction SilentlyContinue
        Write-Audit -QueueRootPath $QueueRootPath -Event $status -Detail "id=$($task.Id) type=$($task.Type) exit=$($r.ExitCode) $($r.Error)"
        Invoke-PruneAll -Dirs $dirs
        return 0
    }
    finally { Exit-RunnerLock -LockPath $lockPath }
}

# ------------------------------------------------------------------ 自测
$script:TestPass = 0; $script:TestFail = 0
function Assert-Test {
    param([string]$Name, [bool]$Condition)
    if ($Condition) { $script:TestPass++; Write-Host "[PASS] $Name" } else { $script:TestFail++; Write-Host "[FAIL] $Name" -ForegroundColor Red }
}
function Get-ResultStatus {
    param([hashtable]$Dirs, [string]$Id)
    $rf = Join-Path $Dirs.Results ("$Id.result.json")
    if (-not (Test-Path -LiteralPath $rf)) { return '' }
    try { return ([System.IO.File]::ReadAllText($rf, [System.Text.Encoding]::UTF8) | ConvertFrom-Json -ErrorAction Stop).status }
    catch { return '' }
}
function Invoke-SelfTest {
    $testPsExe = if (Get-OnWindows) { '' } else { Join-Path $PSHOME 'pwsh' }
    $testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("muse-job-runner-selftest-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    $queueRoot = Join-PathParts -Base (Join-Path $testRoot 'TMP') -Parts @('muse-jobs')
    $smokeRoot = Join-Path $testRoot '.smoke'
    $scriptDir = Join-PathParts -Base $smokeRoot -Parts @('ops', 'deploy')
    $callsLog = Join-Path $testRoot 'calls.log'
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    try {
        New-Item -ItemType Directory -Path $scriptDir -Force | Out-Null
        New-Item -ItemType File -Path (Join-Path $smokeRoot 'ISOLATED_COPY.flag') -Force | Out-Null
        New-Item -ItemType File -Path (Join-Path $smokeRoot '.smoke-runner') -Force | Out-Null
        $watcherStub = "param([int]`$OnlyPr = 0, [switch]`$Once); Add-Content -LiteralPath '$callsLog' -Value `"watcher OnlyPr=`$OnlyPr Once=`$Once`" -Encoding UTF8; exit 0"
        $stopStub = "Add-Content -LiteralPath '$callsLog' -Value 'stop_all' -Encoding UTF8; exit 0"
        $startStub = "Add-Content -LiteralPath '$callsLog' -Value 'start_all' -Encoding UTF8; exit 0"
        [System.IO.File]::WriteAllText((Join-Path $scriptDir 'smoke-watcher.ps1'), $watcherStub, $utf8)
        [System.IO.File]::WriteAllText((Join-Path $scriptDir 'stop_all.ps1'), $stopStub, $utf8)
        [System.IO.File]::WriteAllText((Join-Path $scriptDir 'start_all.ps1'), $startStub, $utf8)
        $dirs = Get-QueueDirs -Root $queueRoot
        $run = { Invoke-RunnerOnce -QueueRootPath $queueRoot -PowerShellExeParam $testPsExe | Out-Null }
        $writeTask = { param([string]$F, [string]$J) [System.IO.File]::WriteAllText((Join-Path $dirs.Inbox $F), $J, $utf8) }

        # 1) 合法 smoke-rerun：认领→执行→done + completed，桩收到 -OnlyPr
        & $writeTask 't1.json' '{"id":"20261127-test-smoke-285","type":"smoke-rerun","payload":{"pr":285},"created_by":"muse-1","created_at":"2026-11-27T08:00:00Z"}'
        & $run
        Assert-Test 'smoke-rerun 进 done' (Test-Path -LiteralPath (Join-Path $dirs.Done 't1.json'))
        Assert-Test 'smoke-rerun 结果 completed' ((Get-ResultStatus -Dirs $dirs -Id '20261127-test-smoke-285') -eq 'completed')
        $calls1 = if (Test-Path -LiteralPath $callsLog) { [System.IO.File]::ReadAllText($callsLog) } else { '' }
        Assert-Test 'watcher 收到 -OnlyPr 285 -Once' ($calls1 -match 'watcher OnlyPr=285 Once=True')

        # 2) 合法 restart-smoke-services：stop + start 都跑，结果 completed
        & $writeTask 't2.json' '{"id":"20261127-test-restart-01","type":"restart-smoke-services","payload":{"services":["backend","nginx"]},"created_by":"muse-1","created_at":"2026-11-27T08:00:00Z"}'
        & $run
        Assert-Test 'restart 进 done' (Test-Path -LiteralPath (Join-Path $dirs.Done 't2.json'))
        Assert-Test 'restart 结果 completed' ((Get-ResultStatus -Dirs $dirs -Id '20261127-test-restart-01') -eq 'completed')
        $calls = if (Test-Path -LiteralPath $callsLog) { [System.IO.File]::ReadAllText($callsLog) } else { '' }
        Assert-Test 'stop_all 与 start_all 都被调用' (($calls -match 'stop_all') -and ($calls -match 'start_all'))

        # 3) 拒绝类：非法 JSON / 未知 type / 非法 id / 超大文件 / 未知字段 / pr 非数字 / 未知服务名
        $rejects = @(
            @{ F = 't3.json'; J = '{"id": "bad-json", '; Id = ''; Why = '非法 JSON' }
            @{ F = 't4.json'; J = '{"id":"20261127-test-unknown-01","type":"reboot-prod","payload":{},"created_by":"muse-1","created_at":"2026-11-27T08:00:00Z"}'; Id = '20261127-test-unknown-01'; Why = '未知 type' }
            @{ F = 't5.json'; J = '{"id":"bad id!","type":"smoke-rerun","payload":{"pr":285},"created_by":"muse-1","created_at":"2026-11-27T08:00:00Z"}'; Id = ''; Why = '非法 id' }
            @{ F = 't6.json'; J = '{"id":"20261127-test-big-0001","type":"smoke-rerun","payload":{"pr":285},"created_by":"' + ('x' * 17000) + '"}'; Id = '20261127-test-big-0001'; Why = '超大文件' }
            @{ F = 't7.json'; J = '{"id":"20261127-test-extra-01","type":"smoke-rerun","payload":{"pr":285},"cmd":"whoami","created_by":"muse-1","created_at":"2026-11-27T08:00:00Z"}'; Id = '20261127-test-extra-01'; Why = '未知字段' }
            @{ F = 't8.json'; J = '{"id":"20261127-test-badpr-01","type":"smoke-rerun","payload":{"pr":"abc"},"created_by":"muse-1","created_at":"2026-11-27T08:00:00Z"}'; Id = '20261127-test-badpr-01'; Why = 'pr 非数字' }
            @{ F = 't9.json'; J = '{"id":"20261127-test-badsvc-1","type":"restart-smoke-services","payload":{"services":["backend2"]},"created_by":"muse-1","created_at":"2026-11-27T08:00:00Z"}'; Id = '20261127-test-badsvc-1'; Why = '未知服务名' }
        )
        foreach ($c in $rejects) {
            & $writeTask $c.F $c.J
            & $run
            Assert-Test "$($c.Why) 进 failed" (Test-Path -LiteralPath (Join-Path $dirs.Failed $c.F))
            if ($c.Id) { Assert-Test "$($c.Why) 结果 rejected" ((Get-ResultStatus -Dirs $dirs -Id $c.Id) -eq 'rejected') }
        }

        # 4) claimed 超 1 小时无结果 → 下一轮重新认领并执行
        & $writeTask 't10.json' '{"id":"20261127-test-stale-01","type":"smoke-rerun","payload":{"pr":286},"created_by":"muse-1","created_at":"2026-11-27T08:00:00Z"}'
        Move-Item -LiteralPath (Join-Path $dirs.Inbox 't10.json') -Destination (Join-Path $dirs.Claimed 't10.json') -Force
        (Get-Item -LiteralPath (Join-Path $dirs.Claimed 't10.json')).LastWriteTime = (Get-Date).AddHours(-2)
        & $run
        Assert-Test '超期 claimed 被重新认领执行' (Test-Path -LiteralPath (Join-Path $dirs.Done 't10.json'))
        Assert-Test '超期 claimed 结果 completed' ((Get-ResultStatus -Dirs $dirs -Id '20261127-test-stale-01') -eq 'completed')

        # 5) 保留上限：done/results 各 105 条 → 跑完剩 100 条
        1..105 | ForEach-Object {
            $p = Join-Path $dirs.Done ("seed-{0:D3}.json" -f $_)
            [System.IO.File]::WriteAllText($p, '{}', $utf8)
            (Get-Item -LiteralPath $p).LastWriteTime = (Get-Date).AddMinutes(-$_)
            $r = Join-Path $dirs.Results ("seed-{0:D3}.result.json" -f $_)
            [System.IO.File]::WriteAllText($r, '{}', $utf8)
            (Get-Item -LiteralPath $r).LastWriteTime = (Get-Date).AddMinutes(-$_)
        }
        & $run
        Assert-Test 'done 修剪到 100 条' ((@(Get-ChildItem -LiteralPath $dirs.Done -File)).Count -eq 100)
        Assert-Test 'results 修剪到 100 条' ((@(Get-ChildItem -LiteralPath $dirs.Results -File)).Count -eq 100)

        # 6) 超时：watcher 桩睡 90 秒，任务 timeout 60 秒（允许范围下限）→ timeout 进 failed
        [System.IO.File]::WriteAllText((Join-Path $scriptDir 'smoke-watcher.ps1'), 'param([int]$OnlyPr = 0, [switch]$Once); Start-Sleep -Seconds 90; exit 0', $utf8)
        & $writeTask 't12.json' '{"id":"20261127-test-timeout-1","type":"smoke-rerun","payload":{"pr":287},"created_by":"muse-1","created_at":"2026-11-27T08:00:00Z","timeout_seconds":60}'
        & $run
        Assert-Test '超时任务进 failed' (Test-Path -LiteralPath (Join-Path $dirs.Failed 't12.json'))
        Assert-Test '超时结果 status=timeout' ((Get-ResultStatus -Dirs $dirs -Id '20261127-test-timeout-1') -eq 'timeout')
        [System.IO.File]::WriteAllText((Join-Path $scriptDir 'smoke-watcher.ps1'), $watcherStub, $utf8)

        # 7) 单实例锁：锁住活进程 pid → 本轮不认领；释放后正常执行
        & $writeTask 't13.json' '{"id":"20261127-test-lock-0001","type":"smoke-rerun","payload":{"pr":288},"created_by":"muse-1","created_at":"2026-11-27T08:00:00Z"}'
        [System.IO.File]::WriteAllText((Join-Path $queueRoot 'runner.lock'), "$PID|$((Get-Date).Ticks)", $utf8)
        & $run
        Assert-Test '有锁时不认领（任务仍在 inbox）' (Test-Path -LiteralPath (Join-Path $dirs.Inbox 't13.json'))
        Remove-Item -LiteralPath (Join-Path $queueRoot 'runner.lock') -Force -ErrorAction SilentlyContinue
        & $run
        Assert-Test '锁释放后任务正常执行' (Test-Path -LiteralPath (Join-Path $dirs.Done 't13.json'))

        # 8) claimed 里的子进程日志：*.out.log.stdout / *.out.log.stderr（restart 还有
        #    .out.log.stop / .out.log.start 段）。超 1 天的要被清掉，当天的要保留。
        #    #313 回归：原过滤器 '*.out.log' 匹配不到这些文件名，日志永久堆积。
        foreach ($n in @('20261127-log-1.out.log.stdout', '20261127-log-1.out.log.stderr',
                         '20261127-log-2.out.log.stop.stdout', '20261127-log-2.out.log.start.stderr')) {
            $p = Join-Path $dirs.Claimed $n
            [System.IO.File]::WriteAllText($p, 'x', $utf8)
            (Get-Item -LiteralPath $p).LastWriteTime = (Get-Date).AddDays(-2)
        }
        $freshLog = Join-Path $dirs.Claimed '20261127-log-3.out.log.stdout'
        [System.IO.File]::WriteAllText($freshLog, 'x', $utf8)
        Invoke-PruneAll -Dirs $dirs
        Assert-Test 'claimed 里超期的 .out.log.stdout 被清掉' (-not (Test-Path -LiteralPath (Join-Path $dirs.Claimed '20261127-log-1.out.log.stdout')))
        Assert-Test 'claimed 里超期的 .out.log.stderr 被清掉' (-not (Test-Path -LiteralPath (Join-Path $dirs.Claimed '20261127-log-1.out.log.stderr')))
        Assert-Test 'claimed 里超期的 restart 段日志被清掉' (-not (Test-Path -LiteralPath (Join-Path $dirs.Claimed '20261127-log-2.out.log.stop.stdout')))
        Assert-Test 'claimed 里当天的日志保留' (Test-Path -LiteralPath $freshLog)

        # 9) 审计日志有记录
        $audit = if (Test-Path -LiteralPath (Join-Path $queueRoot 'audit.log')) { [System.IO.File]::ReadAllText((Join-Path $queueRoot 'audit.log')) } else { '' }
        Assert-Test 'audit.log 有认领/完成记录' (($audit -match '\[claim\]') -and ($audit -match '\[completed\]'))
    }
    finally { Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue }
    Write-Host ''
    Write-Host "SelfTest：通过 $($script:TestPass) / 失败 $($script:TestFail)"
    if ($script:TestFail -gt 0) { return 1 }
    return 0
}

# ------------------------------------------------------------------ 入口
if ($SelfTest) { exit (Invoke-SelfTest) }
exit (Invoke-RunnerOnce -QueueRootPath $QueueRoot -PowerShellExeParam $PowerShellExe)
