<#
  install-smoke-watcher.ps1 — 把 smoke-watcher.ps1 注册成常驻计划任务
  ============================================================================
  为什么要有这个脚本：watcher 要「常驻」，靠手工开的窗口活不过会话。
  但计划任务本身只存在本机注册表里，换机器/重建就没了。所以把注册动作
  写成脚本随仓库走，谁拿到这台机器都能一条命令重建同样的任务。

  任务形态：OLBR_SmokeWatcher
    · 触发：当前用户登录时（AtLogOn）自启；
    · 崩溃自动重启：最多 999 次、间隔 1 分钟；
    · 无执行时长上限（常驻轮询，不能到点被杀）；
    · 单实例：MultipleInstances=IgnoreNew，已在跑就不会起第二个；
    · 以当前用户「交互式」身份运行（S4U 会丢网络凭据，访问不了 GitHub）。

  注意：交互式身份意味着**该用户登录后**才会自启；注销/关机后不自启。
  这是刻意的——冒烟要起停本机隔离栈（8080/8443/55432），必须在用户会话里跑。
  任务的工作目录就是「当前这份脚本所在的项目内冒烟副本」：在
  <项目>\.smoke\ops\deploy 里运行它，注册出来的任务就指向 .smoke；
  换副本位置后重跑一次本脚本即可（不要手改计划任务里的路径）。

  用法：
    powershell -NoProfile -File ops\deploy\install-smoke-watcher.ps1 -Status
    powershell -NoProfile -File ops\deploy\install-smoke-watcher.ps1
    powershell -NoProfile -File ops\deploy\install-smoke-watcher.ps1 -Start
    powershell -NoProfile -File ops\deploy\install-smoke-watcher.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'OLBR_SmokeWatcher',
    [int]$PollSeconds = 60,
    [switch]$Uninstall,
    [switch]$Status,
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$Watcher = Join-Path $Root 'ops\deploy\smoke-watcher.ps1'
$PsExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath (Join-Path $Root '.smoke-runner'))) {
    throw "当前目录不是项目内冒烟副本（$Root，缺 .smoke-runner）。请在 <项目>\.smoke 里运行；生成副本用 ops\deploy\provision-smoke-runner.ps1 -Apply。"
}
if (-not (Test-Path -LiteralPath $Watcher)) { throw "找不到 watcher：$Watcher" }

function Stop-RunningWatchers {
    $w = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match '(^|[\s\\/])smoke-watcher\.ps1' }
    foreach ($p in @($w)) {
        if (-not $p) { continue }
        Write-Host "停止正在运行的 watcher：pid=$($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    $pidPath = Join-Path $Root 'TMP\smoke-watcher.pid'
    if (Test-Path -LiteralPath $pidPath) {
        try { Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue } catch { }
    }
}

function Show-Status {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) {
        Write-Host "计划任务 '$TaskName' 不存在。"
        $w = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match '(^|[\s\\/])smoke-watcher\.ps1' }
        if ($w) {
            Write-Host "但检测到手工启动的 watcher 进程：$($w.ProcessId -join ', ')"
        }
        return $false
    }
    $i = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "任务:     $TaskName"
    Write-Host "状态:     $($t.State)"
    Write-Host "上次运行: $($i.LastRunTime)  结果: 0x$('{0:X}' -f $i.LastTaskResult)  下次: $($i.NextRunTime)"
    Write-Host "动作:     $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"
    Write-Host "工作目录: $($t.Actions[0].WorkingDirectory)"
    $w = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match '(^|[\s\\/])smoke-watcher\.ps1' }
    if ($w) { Write-Host "当前 watcher 进程: $($w.ProcessId -join ', ')" }
    else { Write-Host "当前 watcher 进程: 无" }
    if (Test-Path -LiteralPath (Join-Path $Root 'TMP\smoke-watcher.log')) {
        Write-Host "--- TMP\smoke-watcher.log 末尾 8 行 ---"
        Get-Content -LiteralPath (Join-Path $Root 'TMP\smoke-watcher.log') -Tail 8
    }
    return $true
}

if ($Status) { $null = Show-Status; exit 0 }

if ($Uninstall) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        if ($t.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已删除计划任务 '$TaskName'。"
    } else {
        Write-Host "计划任务 '$TaskName' 本来就不存在。"
    }
    $w = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match '(^|[\s\\/])smoke-watcher\.ps1' }
    foreach ($p in @($w)) {
        if ($p) { Write-Host "仍在运行的 watcher 进程 pid=$($p.ProcessId)（未杀，需要时手工停）" }
    }
    exit 0
}

# ---------- 注册 ----------
$userId = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $PsExe `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -PollSeconds {1}' -f $Watcher, $PollSeconds) `
    -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "旧的 '$TaskName' 已移除，准备重建。"
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description 'D:\02 本机冒烟轮询器：CI 变绿且无 smoke-passed 的 open PR 就地 checkout + 冒烟 + 贴评论 + 打标签（替代 Grok Routine 的冒烟部分，0 token）' | Out-Null

Write-Host "已注册计划任务 '$TaskName'（用户 $userId，登录时自启，崩溃每分钟重启）。"

if ($Start) {
    # 计划任务的 IgnoreNew 不会杀掉已在跑的旧进程；改脚本后必须显式停旧再起。
    Stop-RunningWatchers
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 3
    Write-Host "已启动。"
}

$null = Show-Status
Write-Host ''
Write-Host '下一步自检：'
Write-Host ('  powershell -NoProfile -File "{0}" -Once -DryRun' -f $Watcher)
Write-Host ('  然后看日志：Get-Content "{0}" -Tail 20 -Wait' -f (Join-Path $Root 'TMP\smoke-watcher.log'))
