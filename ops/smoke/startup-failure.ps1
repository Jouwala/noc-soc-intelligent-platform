<#
  ops\smoke\startup-failure.ps1 -- start_all 失败分类器（#303，2026-11-26）
  =============================================================================
  为什么要有这个文件：2026-11-22 起冒烟反复以含糊的 `start_all exit-1` 失败，
  其中至少两类根因完全不同：
    1) 共享 pgdata 的 alembic 迁移链发散（旧分支缺 0013，alembic 报
       `Can't locate revision identified by '0013_network_topology'`）—— 要 rebase；
    2) 资源争用导致启动探测超时（后端 7039 / 便携 PG 55432 / 门户 9277）—— 重跑即可。
  两者都记成同一个 exit-1 时，人只能翻日志猜。本文件把判读集中到一处，
  只读日志、不启动/停止任何进程。

  用法（被 ops\smoke\smoke.ps1 点源引入）：
    . "$PSScriptRoot\startup-failure.ps1"
    $fc = Get-StartAllFailureClass -TmpDir $Tmp -LogBase $LogBase -TimedOut $sr.TimedOut
    $fc.Class / $fc.Revision / $fc.Component / $fc.Detail / $fc.Hint

  自测（不依赖冒烟副本，任何机器可跑）：
    powershell -NoProfile -ExecutionPolicy Bypass -File ops\smoke\startup-failure.ps1 -SelfTest
#>
param(
    [switch]$SelfTest,
    [string]$TmpDir = '',
    [string]$LogBase = '',
    [switch]$TimedOut
)

function Read-LogText {
    param([string]$Path)
    if ($Path -and (Test-Path -LiteralPath $Path)) {
        try { return [string][System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8) } catch { return '' }
    }
    return ''
}

function Get-PortComponent {
    param([int]$Port)
    switch ($Port) {
        7039 { return 'my_app 后端' }
        55432 { return '便携 PostgreSQL' }
        9277 { return '统一门户' }
        7091 { return 'my_app 前端' }
        8080 { return 'nginx' }
        default { return "端口 $Port 服务" }
    }
}

# 返回 Classification：alembic-diverged / startup-timeout / start-all-timeout / start-all-error / none
function Get-StartAllFailureClass {
    param(
        [string]$TmpDir = '',
        [string]$LogBase = '',
        [bool]$TimedOut = $false
    )

    $result = [pscustomobject]@{
        Class     = 'none'
        Revision  = ''
        Component = ''
        Detail    = ''
        Hint      = ''
    }

    # ---- 1) alembic 迁移链发散 -------------------------------------------
    # start_all 自己写的分类文件优先（稳定标记，不受日志尾部截断影响）。
    if ($TmpDir) {
        $classFile = Join-Path $TmpDir 'portable-alembic.class'
        $classText = (Read-LogText $classFile).Trim()
        if ($classText -match '^DIVERGED\s+(\S+)') {
            $result.Class = 'alembic-diverged'
            $result.Revision = $Matches[1]
            $result.Detail = "alembic 缺少 revision $($result.Revision)：pgdata 已到该版本，当前代码树没有这份迁移文件"
            $result.Hint = "把分支 rebase 到最新 platform/main 后重跑（缺 $($result.Revision)）。不要 alembic stamp：共享 pgdata 被 stamp 成旧分支的 head 会让后续迁移错位。"
            return $result
        }
    }

    # ---- 2) 启动探测超时 -------------------------------------------------
    $startText = ''
    if ($LogBase) {
        $startText = (Read-LogText "$LogBase`_start_all.err") + "`n" + (Read-LogText "$LogBase`_start_all.out")
    }
    if (-not $startText.Trim() -and $TmpDir) {
        $startText = (Read-LogText (Join-Path $TmpDir 'portable-backend.err')) + "`n" +
                     (Read-LogText (Join-Path $TmpDir 'portable-backend.out'))
    }

    # 当前这次 start_all 的报错优先：新版本写 ALEMBIC_DIVERGED，旧版本把 alembic stderr
    # 尾部带进 throw。不能先读 portable-alembic.err —— 那是上一次运行的残留，本轮若在
    # 迁移前就超时，会把 PG 超时误判成 alembic 发散。
    $rev = ''
    if ($startText -match 'ALEMBIC_DIVERGED\s+revision=(\S+)') { $rev = $Matches[1] }
    elseif ($startText -match "Can't locate revision identified by '([^']+)'") { $rev = $Matches[1] }
    if (-not $rev -and -not $startText.Trim() -and $TmpDir) {
        # 没有任何 start_all 日志时，才退回复用 alembic 自己的输出（例如手工调用 start_all）。
        $alembicText = (Read-LogText (Join-Path $TmpDir 'portable-alembic.err')) + "`n" +
                       (Read-LogText (Join-Path $TmpDir 'portable-alembic.out'))
        if ($alembicText -match "Can't locate revision identified by '([^']+)'") { $rev = $Matches[1] }
    }
    if ($rev) {
        $result.Class = 'alembic-diverged'
        $result.Revision = $rev
        $result.Detail = "alembic 缺少 revision $rev：pgdata 已到该版本，当前代码树没有这份迁移文件"
        $result.Hint = "把分支 rebase 到最新 platform/main 后重跑（缺 $rev）。不要 alembic stamp：共享 pgdata 被 stamp 成旧分支的 head 会让后续迁移错位。"
        return $result
    }

    if ($startText -match '未在\s*(\d+)\s*s?\s*内监听\s*(\d+)') {
        $wait = [int]$Matches[1]
        $port = [int]$Matches[2]
        $result.Class = 'startup-timeout'
        $result.Component = Get-PortComponent -Port $port
        $result.Detail = "$($result.Component)未在 ${wait}s 内监听端口 ${port}"
        $result.Hint = '冒烟已把探测窗口放宽并在失败后空载重试一次；若仍失败，先确认机器空闲（不要在冒烟期间跑 pytest -n 8、前端构建等重活）。'
        return $result
    }
    if ($startText -match '便携 PG 未在\s*(\d+)s\s*内进入可连接状态') {
        $result.Class = 'startup-timeout'
        $result.Component = '便携 PostgreSQL'
        $result.Detail = "便携 PostgreSQL 未在 $($Matches[1])s 内进入可连接状态（端口已监听但拒绝连接）"
        $result.Hint = '多为 PG 恢复/初始化与重活抢资源；冒烟已放宽窗口并空载重试一次，仍失败再看 portable-pg.log。'
        return $result
    }
    if ($TimedOut) {
        $result.Class = 'start-all-timeout'
        $result.Detail = 'start_all 超过冒烟总超时仍未结束（可能卡在启动探测）'
        $result.Hint = '看归档里的 portable-backend.out / portable-pg.log 尾部；确认是否与其他重活并行。'
        return $result
    }

    # ---- 3) 未匹配到已知分类 ---------------------------------------------
    $tail = ($startText -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 6) -join ' | '
    if ($tail) { $tail = '；日志尾部：' + $tail }
    $result.Class = 'start-all-error'
    $result.Detail = 'start_all 非 0 退出，且未匹配已知分类' + $tail
    $result.Hint = '按归档的 start_all.err / portable-*.out 定位；若反复出现，补一条分类规则而不是放宽判定。'
    return $result
}

function Invoke-StartupFailureSelfTest {
    $root = Join-Path $env:TEMP ('smoke-failure-selftest-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $failed = 0
    try {
        function Assert-Class {
            param([string]$Name, $Expected, $Actual, [string]$Extra = '')
            $ok = ($Actual.Class -eq $Expected)
            if ($ok -and $Extra) { $ok = ($Actual | Select-Object -ExpandProperty $Extra) -ne '' }
            if ($ok) { Write-Host "[PASS] $Name -> $($Actual.Class)" -ForegroundColor Green }
            else { Write-Host "[FAIL] $Name 期望 $Expected，实际 $($Actual.Class)（$($Actual.Detail)）" -ForegroundColor Red; $script:failed++ }
        }

        # 1) alembic 发散：只认 alembic stderr 的原文
        Set-Content -LiteralPath (Join-Path $root 'portable-alembic.err') -Encoding UTF8 -Value @(
            'INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.',
            "alembic.util.exc.CommandError: Can't locate revision identified by '0013_network_topology'"
        )
        $r = Get-StartAllFailureClass -TmpDir $root
        Assert-Class 'alembic-diverged（stderr）' 'alembic-diverged' $r 'Revision'

        # 2) start_all 自己写的分类文件优先（日志可能被截断）
        Remove-Item -LiteralPath (Join-Path $root 'portable-alembic.err') -Force
        Set-Content -LiteralPath (Join-Path $root 'portable-alembic.class') -Encoding UTF8 -Value 'DIVERGED 0013_network_topology'
        $r = Get-StartAllFailureClass -TmpDir $root
        Assert-Class 'alembic-diverged（class 文件）' 'alembic-diverged' $r 'Revision'

        # 3) 后端探测超时：从 start_all.err 读端口
        Remove-Item -LiteralPath (Join-Path $root 'portable-alembic.class') -Force
        $logBase = Join-Path $root 'smoke_999_abcdef0'
        Set-Content -LiteralPath "$logBase`_start_all.err" -Encoding UTF8 -Value 'my_app 后端 未在 150s 内监听 7039，日志：portable-backend.out / .err'
        $r = Get-StartAllFailureClass -TmpDir $root -LogBase $logBase
        Assert-Class 'startup-timeout（后端）' 'startup-timeout' $r 'Component'
        if ($r.Component -ne 'my_app 后端') { Write-Host "[FAIL] 组件映射错误：$($r.Component)" -ForegroundColor Red; $script:failed++ }

        # 4) 外部总超时（没有能匹配的启动日志时才落到这一类）
        Remove-Item -LiteralPath "$logBase`_start_all.err" -Force -ErrorAction SilentlyContinue
        $r = Get-StartAllFailureClass -TmpDir $root -LogBase $logBase -TimedOut $true
        Assert-Class 'start-all-timeout' 'start-all-timeout' $r 'Detail'

        # 5) 兜底分类
        Remove-Item -LiteralPath "$logBase`_start_all.err" -Force -ErrorAction SilentlyContinue
        Set-Content -LiteralPath "$logBase`_start_all.err" -Encoding UTF8 -Value 'FATAL: something unexpected'
        $r = Get-StartAllFailureClass -TmpDir $root -LogBase $logBase
        Assert-Class 'start-all-error（兜底）' 'start-all-error' $r 'Detail'

        return $script:failed
    } finally {
        Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($SelfTest) {
    $script:failed = 0
    $n = Invoke-StartupFailureSelfTest
    if ($n -eq 0) { Write-Host 'SELFTEST: PASS' -ForegroundColor Green; exit 0 }
    Write-Host "SELFTEST: FAIL ($n)" -ForegroundColor Red
    exit 1
}

# 被点源引入时只暴露函数，不执行任何动作。
return
