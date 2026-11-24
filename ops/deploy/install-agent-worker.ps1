<#
  ops\deploy\install-agent-worker.ps1 -- 常驻 Muse Agent Worker 安装/运行/卸载
  =============================================================================
  背景（2026-11-24 流程提速批）：任务慢的主因之一是「等某个会话有人来认领」。
  apps\agent-worker 本来就能常驻轮询 GitHub Issue，但一直没有装成计划任务，
  本脚本把它接上：

    Install   写配置 + 注册计划任务（登录时启动），任务常驻、看门狗托底
    Uninstall 停止并删除计划任务（配置与日志保留）
    Status    看任务状态与最近日志
    Run       任务内部实际执行的入口：循环拉起 watchdog.py（不要手工调用）
    DryRun    只打印将要做的事，不落盘、不注册

  安全边界（务必阅读）：
    · 只处理显式打了 `-RequireLabel`（默认 worker:auto）标签的 Issue，
      没打标签的任务不会被自动跑掉；
    · 令牌不落盘：每次启动用 `gh auth token` 取当前登录态，写进进程环境变量；
    · 编码要求：本文件必须是 UTF-8 + BOM（CI 的 check-ps1-bom 会拦）。

  示例：
    powershell -NoProfile -File ops\deploy\install-agent-worker.ps1 -Action Install
    powershell -NoProfile -File ops\deploy\install-agent-worker.ps1 -Action Status
    powershell -NoProfile -File ops\deploy\install-agent-worker.ps1 -Action Install -DryRun
#>
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall', 'Status', 'Run', 'DryRun')]
    [string]$Action = 'Status',

    # Worker 逻辑身份（muse-2 / muse-3 ...），决定认领哪些 agent:* 标签的任务
    [string]$Agent = 'muse-2',

    # 只有带齐这些标签的 Issue 才会被自动执行（逗号分隔）
    [string]$RequireLabel = 'worker:auto',

    # 默认 agent 命令：Claude Code 非交互模式，读任务书执行
    [string]$AgentCmd = 'claude -p --permission-mode acceptEdits "读取 {prompt_file} 并完成其中的任务：只改 Issue 声明的文件范围，完成后提交到当前分支并推送、创建或更新 PR，不要合并 PR。"',

    # 仓库根（默认按本脚本位置上推两级）
    [string]$RepoRoot = '',

    # 计划任务名（默认按 Agent 生成）
    [string]$TaskName = '',

    # 运行模式用：安装时写下的配置路径
    [string]$Config = '',

    # python 解释器（默认取 PATH 里的 python）
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
if (-not $TaskName) { $TaskName = "Muse_AgentWorker_$Agent" }
if (-not $Python) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { throw '找不到 python，请先安装或显式传 -Python <路径>' }
    $Python = $py.Source
}

$workerPy  = Join-Path $RepoRoot 'apps\agent-worker\watchdog.py'
$tmpDir    = Join-Path $RepoRoot 'TMP'
$cfgPath   = Join-Path $tmpDir ("agent-worker-{0}.json" -f $Agent)
$logPath   = Join-Path $tmpDir ("agent-worker-{0}.log" -f $Agent)
$owner     = 'Jouwala'
$repo      = 'noc-soc-intelligent-platform'

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Write-Host $line
    try {
        if (-not (Test-Path -LiteralPath $tmpDir)) { New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null }
        Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
    } catch { }
}

function Assert-Prerequisites {
    if (-not (Test-Path -LiteralPath $workerPy)) {
        throw "找不到 worker 入口：$workerPy"
    }
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw '找不到 gh CLI：Worker 需要它取 GitHub 令牌'
    }
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) {
        Write-Warning '找不到 claude CLI：任务会以「执行命令不存在」失败。请先安装 Claude Code 或改用 -AgentCmd。'
    }
}

switch ($Action) {
    'Run' {
        if (-not $Config -or -not (Test-Path -LiteralPath $Config)) {
            throw "Run 模式缺少有效 -Config：$Config"
        }
        $cfg = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
        $env:MUSE_REQUIRE_LABELS = [string]$cfg.requireLabels
        $env:MUSE_AGENT_CMD      = [string]$cfg.agentCmd
        $env:GITHUB_OWNER        = [string]$cfg.owner
        $env:GITHUB_REPO         = [string]$cfg.repo
        $workerRoot              = [string]$cfg.repoRoot

        while ($true) {
            $token = ''
            try { $token = ([string](& gh auth token 2>$null)).Trim() } catch { }
            if (-not $token) {
                Write-Log '取不到 gh 令牌（未登录？），5 分钟后重试'
                Start-Sleep -Seconds 300
                continue
            }
            $env:GITHUB_TOKEN = $token
            Write-Log ("拉起 watchdog：agent={0} labels={1}" -f $cfg.agent, $cfg.requireLabels)
            $watchdog = Join-Path $workerRoot 'apps\agent-worker\watchdog.py'
            try {
                & $Python $watchdog --agent ([string]$cfg.agent) --repo-root $workerRoot *>> $logPath
            } catch {
                Write-Log ("watchdog 异常退出：{0}" -f $_.Exception.Message)
            }
            Write-Log 'watchdog 已退出，60 秒后重启'
            Start-Sleep -Seconds 60
        }
    }

    'Install' {
        Assert-Prerequisites
        if (-not (Test-Path -LiteralPath $tmpDir)) { New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null }
        $cfg = [ordered]@{
            agent        = $Agent
            repoRoot     = $RepoRoot
            requireLabels = $RequireLabel
            agentCmd     = $AgentCmd
            owner        = $owner
            repo         = $repo
        }
        $json = $cfg | ConvertTo-Json
        [System.IO.File]::WriteAllText($cfgPath, $json, (New-Object System.Text.UTF8Encoding($false)))

        $argList = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Action Run -Config "{1}"' -f $PSCommandPath, $cfgPath
        Write-Log ("将注册计划任务 {0}" -f $TaskName)
        Write-Log ("  命令：powershell.exe {0}" -f $argList)
        Write-Log ("  配置：{0}" -f $cfgPath)
        Write-Log ("  日志：{0}" -f $logPath)

        $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argList
        # 不指定 -User：默认就是当前登录用户，避免域名/用户名格式不一致时注册失败
        $trigger    = New-ScheduledTaskTrigger -AtLogOn
        $settings   = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                        -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
                        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
        Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger `
            -Settings $settings -Description 'NOC/SOC 常驻 Muse Agent Worker（opt-in: worker:auto）' -Force | Out-Null
        Write-Log '计划任务已注册（登录时启动）。立即启动一次：Start-ScheduledTask -TaskName <任务名>'
        Start-ScheduledTask -TaskName $TaskName
        Write-Log '已触发首次启动'
    }

    'Uninstall' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Log ("已卸载计划任务 {0}（配置与日志保留）" -f $TaskName)
        } else {
            Write-Log ("计划任务 {0} 不存在" -f $TaskName)
        }
    }

    'Status' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host ("计划任务 {0}：未安装" -f $TaskName)
        } else {
            $info = Get-ScheduledTaskInfo -TaskName $TaskName
            Write-Host ("计划任务 {0}：{1}" -f $task.TaskName, $task.State)
            Write-Host ("  上次运行：{0}  结果：{1}" -f $info.LastRunTime, $info.LastTaskResult)
            Write-Host ("  下次运行：{0}" -f $info.NextRunTime)
        }
        if (Test-Path -LiteralPath $cfgPath) { Write-Host ("配置：{0}" -f $cfgPath) } else { Write-Host '配置：未生成' }
        if (Test-Path -LiteralPath $logPath) {
            Write-Host ("最近日志（{0}）：" -f $logPath)
            Get-Content -LiteralPath $logPath -Tail 10 | ForEach-Object { Write-Host ("  " + $_) }
        }
    }

    'DryRun' {
        Assert-Prerequisites
        Write-Host ("Agent      : {0}" -f $Agent)
        Write-Host ("仓库根     : {0}" -f $RepoRoot)
        Write-Host ("标签门槛   : {0}" -f $RequireLabel)
        Write-Host ("agent 命令 : {0}" -f $AgentCmd)
        Write-Host ("计划任务   : {0}" -f $TaskName)
        Write-Host ("配置路径   : {0}" -f $cfgPath)
        Write-Host 'DryRun：未写配置、未注册任务。'
    }
}
