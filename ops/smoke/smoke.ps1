<#
  ops\smoke\smoke.ps1 -- 02 隔离副本 PR 冒烟（§8.1~§8.4 + 可选并行 pytest）
  =============================================================================
  用法（只能在项目内冒烟副本里跑；默认 <项目>\.smoke，脚本只碰 $Root）:
    powershell -NoProfile -ExecutionPolicy Bypass -File <项目>\.smoke\ops\smoke\smoke.ps1 -PR 26
    powershell -NoProfile -ExecutionPolicy Bypass -File <项目>\.smoke\ops\smoke\smoke.ps1 -PR 26 -Sha 1998577 -Pytest

  参数:
    -PR <n>                必填，PR 号（只用于结果文件名与报告标题）
    -Sha <sha>             可选，默认取当前 HEAD 前 7 位；用于结果文件名，不 checkout
    -Pytest                可选，stop_all 之后跑离线 pytest(-m "not live"，与 CI quality.yml 一致)
    -PytestWorkers <s>     可选，pytest-xdist 的 -n 值，默认 '8'（实测 8 比 auto=28 更稳，也省 CPU）；'0' = 串行
    -PytestDist <s>        可选，xdist 调度策略，默认 'load'（可选 loadfile）
    -PytestTimeoutSec      可选，pytest 超时（默认 900 秒）
    -SkipFeBuild           可选，即使前端/lockfile 有改动也不重建 _fe_dist
    -ForceFeBuild          可选，无论有无改动都重建 my_app 前端（及门户，若存在）
    -BaseRef <ref>         可选，§8.1 对比基线；默认依次尝试 platform/main、origin/main、main
    -FeBuildTimeoutSec     可选，单次 npm run build 超时（默认 600 秒）
    -KnownFailures <ids>   可选，pytest 节点 id 列表；仅这些失败且无其它 ERROR/失败时冒烟仍算通过
    -NoIgnoreKnownFailures 可选，关闭上述忽略（任何失败都判 FAIL）

  产物（都在 <副本根>\TMP，已在 .gitignore 里）:
    smoke_result_<PR>_<sha7>.md      检查项 + 分阶段耗时 + 末行(RESULT: PASS / FAIL)
    smoke_pytest_<PR>_<sha7>.out     pytest 完整输出（若 -Pytest）
    smoke_<PR>_<sha7>_*.out/.err     start_all / stop_all / nginx -t / fe-build 日志

  退出码: 0 = 全部通过（含「仅已知 NUR 失败被忽略」）; 1 = 有失败项;
          2 = 预检拒绝（缺 02 标志，或 02 端口已被占用=可能别人正在冒烟），
              此时本脚本**不会启动或停止任何进程**就退出

  安全约束（对齐方案 §8 隔离冒烟）:
    · 只动冒烟副本根（默认 <项目>\.smoke，由 ops\deploy\provision-smoke-runner.ps1 生成）；
      不改 D:\01、80/443、5432、7038、7090、9267。
    · 启停只走 ops\deploy\start_all.ps1 / stop_all.ps1（按 pid 文件，不按端口盲杀）。若从无控制台环境启动，会先把本用户默认终端临时改为 Windows Console Host，再以 CREATE_NEW_CONSOLE+SW_HIDE 重启动自身（SMOKE_HEADLESS=1），让全部子进程继承这个隐藏控制台（GetConsoleWindow≠0，避免 Node/npm/powershell 再弹出 Windows Terminal）。跑完立即恢复原默认终端（原值写入 TMP\smoke_console_delegation.bak，异常退出后下次启动会先按该文件恢复）。§8.1 npm run build 改为直接启动进程并等待该进程对象，避免 vite 已完成后仍因残留 node/esbuild 占用重定向句柄而空等 600 秒。
    · stop_all 放在 finally 里：中途失败、脚本被中断也会停本副本进程。
    · 只使用 git 只读命令（rev-parse / diff --name-only），不 commit / push / checkout。
    · local_authority_tools.py 只用 check（只读打开源/目标 app.db），绝不自动 migrate。
    · 不打印口令、Token 或凭据表内容。
#>
param(
    [Parameter(Mandatory = $true)][int]$PR,
    [string]$Sha = '',
    [switch]$Pytest,
    [string]$PytestWorkers = '8',
    [ValidateSet('load', 'loadfile', 'loadscope', 'worksteal')][string]$PytestDist = 'load',
    [int]$PytestTimeoutSec = 900,
    [switch]$SkipFeBuild,
    [switch]$ForceFeBuild,
    [string]$BaseRef = '',
    # §8.2 的权威源（本地权威三表）。默认取冒烟副本上一级项目根的
    # apps\my_app\db\app.db；换机器/独立副本时显式传入。
    [string]$AuthSource = '',
    [int]$FeBuildTimeoutSec = 600,
    # 已知失败基线（#111，2026-11-17）：以下两条 NUR 用例按 2026-10-06
    # 快照校准（名单 46 单 / 名单周全量 1107），D:\02 的名单（库表
    # tt_exclude，人工上传维护）或 eflow_tt 数据漂移后与快照不符而失败，
    # 非代码回归（同期 nur_calc 无语义改动）。移除条件：D:\02 侧提供
    # 冒烟 pytest 实际值并按现状重新校准数据/名单后，两条在 D:\02 连跑
    # 通过，即从本清单删除。其余失败一律判 FAIL。
    [string[]]$KnownFailures = @(
        'tests/test_nur_exclude_semantics.py::test_Y_subtracts_the_list',
        'tests/test_nur_exclude_semantics.py::test_Y_is_not_the_list_itself'
    ),
    [switch]$NoIgnoreKnownFailures
)
$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# 根目录按脚本位置推导：脚本在哪棵就只动哪棵，不写死盘符。
# 2026-11-18 之前这里写死 'D:\02_olbr_portal'；当晚切生产后副本曾放到项目外
# （D:\02_olbr_portal_smoke），2026-11-19 起收进项目内（默认 <项目>\.smoke），
# 由 ops\deploy\provision-smoke-runner.ps1 生成，.smoke-runner 是它的身份标记。
$Root        = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$Tmp         = Join-Path $Root 'TMP'
$Here        = Split-Path -Parent $MyInvocation.MyCommand.Path

# ⛔ 只允许在项目内冒烟副本里跑。缺这个标记说明当前树不是 provision 生成的
# 副本（例如直接在生产 02 根、或旧的 D:\02_olbr_portal_smoke 上）——那会重建
# 生产前端的 _fe_dist、并按当前根重写 nginx.conf，等于打断线上。
if (-not (Test-Path -LiteralPath (Join-Path $Root '.smoke-runner'))) {
    Write-Host "拒绝执行：$Root 不是冒烟副本（缺 .smoke-runner）。" -ForegroundColor Red
    Write-Host '请先跑 ops\deploy\provision-smoke-runner.ps1 -Apply 生成 <项目>\.smoke。' -ForegroundColor Red
    exit 2
}
$ProjectRoot = Split-Path -Parent $Root

# ---------------------------------------------------------------------------
# 无控制台父进程（Agent Shell / 任务计划）下，控制台子系统子进程会被 Win11 默认
# 终端接管，弹出 Windows Terminal / OpenConsole -Embedding。
# conhost --headless 的 GetConsoleWindow 为 0，Node 的 spawn/spawnSync（npm.cmd、
# powershell，且默认 windowsHide=false）会误判“没有控制台”再弹出 WT。
# 因此：临时把 HKCU\Console\%%Startup 的默认终端改为 Console Host，再用
# CREATE_NEW_CONSOLE + SW_HIDE 重启动自身（隐藏窗口且 GetConsoleWindow≠0），
# 子进程继承；结束时务必恢复注册表。
# ---------------------------------------------------------------------------
if ($env:SMOKE_HEADLESS -ne '1') {
    $psExe   = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $cmdExe  = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $self    = $MyInvocation.MyCommand.Path
    if (-not $self) { $self = Join-Path $Here 'smoke.ps1' }
    if (-not (Test-Path -LiteralPath $self)) {
        Write-Host "找不到脚本自身：$self" -ForegroundColor Red
        exit 2
    } else {
        if (-not (Test-Path -LiteralPath $Tmp)) { New-Item -ItemType Directory -Path $Tmp -Force | Out-Null }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $relog  = Join-Path $Tmp ("smoke_headless_{0}_{1}.out" -f $PR, $stamp)
        $recode = "$relog.code"
        $runner = Join-Path $Tmp ("smoke_headless_{0}_{1}.cmd" -f $PR, $stamp)
        Remove-Item -LiteralPath $recode -Force -ErrorAction SilentlyContinue

        function Quote-SmokeArg {
            param([string]$s)
            if ($null -eq $s) { return '""' }
            if ($s -notmatch '[ \t&|<>^()"]') { return $s }
            return ('"{0}"' -f $s.Replace('"', '""'))
        }
        $childArgs = New-Object System.Collections.Generic.List[string]
        foreach ($k in $PSBoundParameters.Keys) {
            $v = $PSBoundParameters[$k]
            if ($v -is [System.Management.Automation.SwitchParameter]) {
                if ($v.IsPresent) { [void]$childArgs.Add("-$k") }
            } elseif (($v -is [System.Array]) -and -not ($v -is [string])) {
                [void]$childArgs.Add("-$k")
                foreach ($item in @($v)) { [void]$childArgs.Add((Quote-SmokeArg "$item")) }
            } else {
                [void]$childArgs.Add("-$k")
                [void]$childArgs.Add((Quote-SmokeArg "$v"))
            }
        }
        $argStr = ($childArgs -join ' ')
        $cmdBody = @(
            '@echo off',
            'set "SMOKE_HEADLESS=1"',
            ('set "SMOKE_HEADLESS_LOG={0}"' -f $relog),
            ('"{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" {2}' -f $psExe, $self, $argStr),
            ('>"{0}" echo %ERRORLEVEL%' -f $recode)
        ) -join "`r`n"
        [System.IO.File]::WriteAllText($runner, $cmdBody)

        if (-not ('SmokeRelaunchProc' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class SmokeRelaunchProc {
    public const uint CREATE_NEW_CONSOLE = 0x00000010;
    public const int STARTF_USESHOWWINDOW = 0x00000001;
    public const short SW_HIDE = 0;
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct STARTUPINFO {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute;
        public int dwFlags;
        public short wShowWindow;
        public short cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION {
        public IntPtr hProcess, hThread;
        public int dwProcessId, dwThreadId;
    }
    public const uint GENERIC_ALL = 0x10000000;
    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern IntPtr CreateDesktop(string lpszDesktop, IntPtr lpszDevice, IntPtr pDevmode, int dwFlags, uint dwDesiredAccess, IntPtr lpsa);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool CloseDesktop(IntPtr hDesktop);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool CreateProcess(string lpApplicationName, StringBuilder lpCommandLine, IntPtr lpProcessAttributes, IntPtr lpThreadAttributes, bool bInheritHandles, uint dwCreationFlags, IntPtr lpEnvironment, string lpCurrentDirectory, ref STARTUPINFO lpStartupInfo, out PROCESS_INFORMATION lpProcessInformation);
    [DllImport("kernel32.dll", SetLastError = true)] public static extern bool CloseHandle(IntPtr hObject);
}
'@
        }
        # 把默认终端临时改为 Windows Console Host，SW_HIDE 才会被遵守，且子进程 GetConsoleWindow≠0。
        # 原值落到磁盘：若上次异常退出没能恢复，本次启动先按备份文件还原。
        $conhostGuid = '{B23D10C0-E52E-411E-9D5B-C09FDF709C7D}'
        $startupKey = 'HKCU:\Console\%%Startup'
        $delegBak = Join-Path $Tmp 'smoke_console_delegation.bak'
        function Read-SmokeDelegationProps {
            $hadC = $false; $savedC = $null; $hadT = $false; $savedT = $null
            if (Test-Path -LiteralPath $startupKey) {
                $prop = Get-ItemProperty -LiteralPath $startupKey -ErrorAction SilentlyContinue
                if ($prop -and ($prop.PSObject.Properties.Name -contains 'DelegationConsole')) { $hadC = $true; $savedC = $prop.DelegationConsole }
                if ($prop -and ($prop.PSObject.Properties.Name -contains 'DelegationTerminal')) { $hadT = $true; $savedT = $prop.DelegationTerminal }
            }
            return @{ HadC = $hadC; Console = $savedC; HadT = $hadT; Terminal = $savedT }
        }
        function Restore-SmokeDelegationTo {
            param($HadC, $Console, $HadT, $Terminal)
            if (-not (Test-Path -LiteralPath $startupKey)) {
                if ($HadC -or $HadT) { New-Item -LiteralPath $startupKey -Force | Out-Null }
                else { return }
            }
            if ($HadC) { Set-ItemProperty -LiteralPath $startupKey -Name DelegationConsole -Value $Console -Type String }
            else { Remove-ItemProperty -LiteralPath $startupKey -Name DelegationConsole -ErrorAction SilentlyContinue }
            if ($HadT) { Set-ItemProperty -LiteralPath $startupKey -Name DelegationTerminal -Value $Terminal -Type String }
            else { Remove-ItemProperty -LiteralPath $startupKey -Name DelegationTerminal -ErrorAction SilentlyContinue }
        }
        function Restore-SmokeDelegationFromBakIfStale {
            if (-not (Test-Path -LiteralPath $delegBak)) { return }
            $map = @{}
            foreach ($ln in (Get-Content -LiteralPath $delegBak -ErrorAction SilentlyContinue)) {
                if ($ln -match '^(HadC|Console|HadT|Terminal|OwnerPid)=(.*)$') { $map[$Matches[1]] = $Matches[2] }
            }
            $owner = 0
            if ($map.ContainsKey('OwnerPid')) { [void][int]::TryParse($map['OwnerPid'], [ref]$owner) }
            $alive = $false
            if ($owner -gt 0) {
                $op = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue
                if ($op -and ("$($op.CommandLine)" -match 'smoke\.ps1')) { $alive = $true }
            }
            if ($alive) { return }
            Restore-SmokeDelegationTo -HadC:($map['HadC'] -eq '1') -Console $map['Console'] -HadT:($map['HadT'] -eq '1') -Terminal $map['Terminal']
            Remove-Item -LiteralPath $delegBak -Force -ErrorAction SilentlyContinue
            Write-Host "已从 $delegBak 恢复默认终端设置（上次冒烟异常退出留下的备份）"
        }
        Restore-SmokeDelegationFromBakIfStale
        $savedC = $null; $savedT = $null; $hadC = $false; $hadT = $false
        $cur = Read-SmokeDelegationProps
        $hadC = [bool]$cur.HadC; $savedC = $cur.Console; $hadT = [bool]$cur.HadT; $savedT = $cur.Terminal
        if (-not (Test-Path -LiteralPath $startupKey)) { New-Item -LiteralPath $startupKey -Force | Out-Null }
        function Restore-SmokeDefaultTerminal {
            Restore-SmokeDelegationTo -HadC:$hadC -Console $savedC -HadT:$hadT -Terminal $savedT
            Remove-Item -LiteralPath $delegBak -Force -ErrorAction SilentlyContinue
        }
        $bakLines = @(
            ('HadC={0}' -f $(if ($hadC) { '1' } else { '0' })),
            ('Console={0}' -f $savedC),
            ('HadT={0}' -f $(if ($hadT) { '1' } else { '0' })),
            ('Terminal={0}' -f $savedT),
            ('OwnerPid={0}' -f $PID),
            ('SavedAt={0}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'))
        )
        [System.IO.File]::WriteAllText($delegBak, ($bakLines -join "`r`n"))
        $n = 2
        $timedOut = $false
        $hDesk = [IntPtr]::Zero
        try {
            New-ItemProperty -LiteralPath $startupKey -Name DelegationConsole -Value $conhostGuid -PropertyType String -Force | Out-Null
            New-ItemProperty -LiteralPath $startupKey -Name DelegationTerminal -Value $conhostGuid -PropertyType String -Force | Out-Null

            $deskName = 'SmokeHeadless_' + $stamp
            $hDesk = [SmokeRelaunchProc]::CreateDesktop($deskName, [IntPtr]::Zero, [IntPtr]::Zero, 0, [SmokeRelaunchProc]::GENERIC_ALL, [IntPtr]::Zero)
            $si = New-Object SmokeRelaunchProc+STARTUPINFO
            $si.cb = [System.Runtime.InteropServices.Marshal]::SizeOf($si)
            $si.dwFlags = [SmokeRelaunchProc]::STARTF_USESHOWWINDOW
            $si.wShowWindow = [SmokeRelaunchProc]::SW_HIDE
            if ($hDesk -ne [IntPtr]::Zero) { $si.lpDesktop = $deskName }
            $pi = New-Object SmokeRelaunchProc+PROCESS_INFORMATION
            $cmdline = '"{0}" /c "{1}"' -f $cmdExe, $runner
            $sb = New-Object System.Text.StringBuilder $cmdline
            $ok = [SmokeRelaunchProc]::CreateProcess($cmdExe, $sb, [IntPtr]::Zero, [IntPtr]::Zero, $false, [SmokeRelaunchProc]::CREATE_NEW_CONSOLE, [IntPtr]::Zero, $Root, [ref]$si, [ref]$pi)
            if (-not $ok) {
                Write-Host ('无法以隐藏控制台重启动 smoke.ps1，Win32=' + [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()) -ForegroundColor Red
                $n = 2
            } else {
                $procId = $pi.dwProcessId
                [void][SmokeRelaunchProc]::CloseHandle($pi.hThread)
                [void][SmokeRelaunchProc]::CloseHandle($pi.hProcess)
                $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
                $shown = 0
                $deadline = (Get-Date).AddSeconds(3600)
                while ($true) {
            if (Test-Path -LiteralPath $relog) {
                $fs = $null; $sr = $null
                try {
                    $fs = New-Object System.IO.FileStream($relog, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
                    $sr = New-Object System.IO.StreamReader($fs, $true)
                    $txt = $sr.ReadToEnd()
                    if ($txt.Length -gt $shown) {
                        [Console]::Out.Write($txt.Substring($shown))
                        [Console]::Out.Flush()
                        $shown = $txt.Length
                    }
                } catch { } finally {
                    if ($sr) { $sr.Dispose() }
                    if ($fs) { $fs.Dispose() }
                }
            }
            $done = $false
            try {
                if (-not $proc) { $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue }
                if ($proc) { $done = $proc.HasExited } else { $done = $true }
            } catch { $done = $true }
            if ($done -and (Test-Path -LiteralPath $recode)) { break }
            if ((Get-Date) -gt $deadline) {
                Write-Host "隐藏重启动等待超时，见 $relog" -ForegroundColor Red
                # 不用 taskkill.exe：无控制台父进程会再弹出 Windows Terminal
                $toKill = New-Object System.Collections.Generic.List[int]
                $toKill.Add([int]$procId)
                $i = 0
                while ($i -lt $toKill.Count) {
                    $cur = $toKill[$i]; $i++
                    try {
                        Get-CimInstance Win32_Process -Filter "ParentProcessId=$cur" -ErrorAction SilentlyContinue |
                            ForEach-Object { [void]$toKill.Add([int]$_.ProcessId) }
                    } catch { }
                }
                foreach ($kid in ($toKill | Select-Object -Unique | Sort-Object -Descending)) {
                    try { Stop-Process -Id $kid -Force -ErrorAction SilentlyContinue } catch { }
                }
                $n = 2
                $timedOut = $true
                break
            }
            if ($done -and -not (Test-Path -LiteralPath $recode)) {
                Start-Sleep -Milliseconds 100
                continue
            }
            Start-Sleep -Milliseconds 300
        }
        if (Test-Path -LiteralPath $relog) {
            try {
                $fs = New-Object System.IO.FileStream($relog, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
                $sr = New-Object System.IO.StreamReader($fs, $true)
                $txt = $sr.ReadToEnd()
                $sr.Dispose(); $fs.Dispose()
                if ($txt.Length -gt $shown) {
                    [Console]::Out.Write($txt.Substring($shown))
                    [Console]::Out.Flush()
                }
            } catch { }
        }
                if (Test-Path -LiteralPath $recode) {
                    $raw = ([System.IO.File]::ReadAllText($recode)).Trim()
                    if (-not [int]::TryParse($raw, [ref]$n)) { $n = -2 }
                } elseif ($timedOut) {
                    $n = 2
                } else {
                    $n = -1
                }
            }
        } finally {
            Restore-SmokeDefaultTerminal
            if ($hDesk -and ($hDesk -ne [IntPtr]::Zero)) {
                [void][SmokeRelaunchProc]::CloseDesktop($hDesk)
            }
        }
        exit $n
    }
}
if ($env:SMOKE_HEADLESS -eq '1' -and $env:SMOKE_HEADLESS_LOG) {
    try { Start-Transcript -Path $env:SMOKE_HEADLESS_LOG -Force | Out-Null } catch { }
}
function Exit-Smoke {
    param([int]$Code)
    if ($env:SMOKE_HEADLESS -eq '1') { try { Stop-Transcript | Out-Null } catch { } }
    exit $Code
}

$Helper      = Join-Path $Here 'run_exit.cmd'
$FeDistDir   = Join-Path $Root 'apps\_fe_dist'
$FeDistIndex = Join-Path $FeDistDir 'index.html'
$FeDir       = Join-Path $Root 'apps\my_app\frontend'
$PortalDir   = Join-Path $Root 'apps\portal'
$NodeDir     = Join-Path $Root 'runtime\node'
$NpmCmd      = Join-Path $NodeDir 'npm.cmd'
$PsqlExe     = Join-Path $Root 'runtime\postgresql16\bin\psql.exe'
$PgPort      = 55432
$PgUser      = 'myapp'
# 生产 nginx 的路径会随切换而变（2026-11-18 起是 D:\02_olbr_portal\nginx-1.28.0），
# 所以不再写死路径：开跑前记录 80/443 的 pid->exe，收尾要求逐字一致。
$ProdNginx   = ''
$PyExe       = Join-Path $Root 'runtime\python312\python.exe'
$AppDir      = Join-Path $Root 'apps\my_app'
$AuthSrc     = 'D:\01_olbr_portal\Task\my_app\db\app.db'
$AuthTgt     = Join-Path $Root 'apps\my_app\db\app.db'
# 权威源（三张本地权威表）：
#   2026-11-19 之前写死 'D:\01_olbr_portal\Task\my_app\db\app.db' —— 那是**旧生产**，
#   既在项目外（违反便携副本自包含），又会在 01 退役后让冒烟直接判失败。
#   现在默认取副本上一级项目根的 app.db（项目内、随 02 当前状态走）；
#   换机器/独立副本时用 -AuthSource 显式指定。
$AuthSrc = $(if ($AuthSource -ne '') { $AuthSource } else { Join-Path $ProjectRoot 'apps\my_app\db\app.db' })
# 只列「必须出现在 check 输出里的表名」。行数**不再硬编码**——2026-11-18 实测踩过：
# 副本补了一条 email_recipients（10→11，补的是生产本来就有的那条），硬编码的期望值
# 把一次正确迁移判成了冒烟失败。判据是「源库可读时两侧必须一致」；源库缺失时
# 预检直接拒绝（退出码 2），不再静默判通过 —— 拿不到真相源的检查等于没做。
$AuthTables  = @('credentials', 'task_schedule_config', 'email_recipients')
$TestPorts   = @(8080, 8443, 7039, 7091, 9277, 55432)
# 后端派生的实时代理（start_all 不写它们的 pid，故 stop_all 停不掉）-- 只记录，不杀
$ExtraPorts  = @(8887, 8890)

# ---------------------------------------------------------------- 记录结构
$results = New-Object System.Collections.ArrayList
$stages  = New-Object System.Collections.ArrayList
$notes   = New-Object System.Collections.ArrayList
$swTotal = [System.Diagnostics.Stopwatch]::StartNew()
$startedAt = Get-Date

function Add-R {
    param([string]$Name, [bool]$Ok, [string]$Detail)
    $tag = if ($Ok) { 'PASS' } else { 'FAIL' }
    [void]$results.Add([pscustomobject]@{ Check = $Name; Result = $tag; Detail = $Detail })
    Write-Host ("[{0}] {1} :: {2}" -f $tag, $Name, $Detail)
}
function Add-Note { param([string]$Text) [void]$notes.Add($Text); Write-Host "[NOTE] $Text" }
$script:stageSw = $null; $script:stageName = $null
function Start-Stage {
    param([string]$Name)
    Stop-Stage
    $script:stageName = $Name
    $script:stageSw = [System.Diagnostics.Stopwatch]::StartNew()
    Write-Host ("===== {0} =====" -f $Name) -ForegroundColor Cyan
}
function Stop-Stage {
    if ($script:stageSw) {
        $script:stageSw.Stop()
        [void]$stages.Add([pscustomobject]@{ Stage = $script:stageName; Sec = [math]::Round($script:stageSw.Elapsed.TotalSeconds, 1) })
        $script:stageSw = $null; $script:stageName = $null
    }
}
function Get-ListenPid {
    param([int]$Port)
    $out = @()
    foreach ($line in (netstat -ano)) {
        if ($line -match '^\s*TCP\s+(\S+):(\d+)\s+(\S+):(\S+)\s+LISTENING\s+(\d+)\s*$') {
            if ([int]$Matches[2] -eq $Port) { $out += [int]$Matches[5] }
        }
    }
    return @($out | Sort-Object -Unique)
}
function Get-ExePath {
    param([int]$ProcessId)
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if ($p) { return $p.ExecutablePath } else { return '' }
}
function Get-Status {
    param([string]$Url, [switch]$Insecure, [string]$OutFile = '')
    $a = @('-s', '-m', '20', '-o', $(if ($OutFile) { $OutFile } else { 'NUL' }), '-w', '%{http_code}', $Url)
    if ($Insecure) { $a = @('-k') + $a }
    $code = & curl.exe @a
    return "$code".Trim()
}
function Get-Body {
    param([string]$Url)
    $f = Join-Path $Tmp ('_smoke_body_{0}.txt' -f ([guid]::NewGuid().ToString('N')))
    & curl.exe -s -m 20 -o $f $Url | Out-Null
    $txt = ''
    if (Test-Path -LiteralPath $f) {
        $txt = [System.IO.File]::ReadAllText($f, [System.Text.Encoding]::UTF8)
        Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
    }
    return $txt
}
function Short { param([string]$s, [int]$n = 300) $t = ($s -replace '\s+', ' '); if ($t.Length -gt $n) { $t.Substring(0, $n) + '.' } else { $t } }
# 不用 Start-Process -Wait：PS 5.1 会在子进程（start_all 拉起的常驻服务）上挂死。
# 改用独立进程：子进程写完退出码文件，本脚本轮询到 .code。
#
# 窗口说明（不能改 start_all.ps1）：
# start_all 的 Start-Detached / pg_ctl / nginx 都是
#   Start-Process ... -WindowStyle Hidden -RedirectStandardOutput/Error
# 在 PS 5.1 里一旦重定向，UseShellExecute 变为 false，-WindowStyle 被忽略，
# 且不会设 CREATE_NO_WINDOW。若父进程没有控制台，python/node/pg_ctl/nginx
# 这类控制台子系统进程就会各自弹出一个可见窗口。
# 因此这里用 CreateProcess(CREATE_NEW_CONSOLE + SW_HIDE) 给 start_all/stop_all
# 一个「真实但隐藏」的控制台，子服务继承它，不再新建可见窗口。
# 日志与退出码仍由包装脚本写到原来的 .out/.err/.code，不走管道，避免常驻子进程卡句柄。
function Ensure-SmokeNativeProc {
    if ('SmokeNativeProc' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class SmokeNativeProc {
    public const uint CREATE_NEW_CONSOLE = 0x00000010;
    public const int STARTF_USESHOWWINDOW = 0x00000001;
    public const short SW_HIDE = 0;
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct STARTUPINFO {
        public int cb;
        public IntPtr lpReserved;
        public IntPtr lpDesktop;
        public IntPtr lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute;
        public int dwFlags;
        public short wShowWindow;
        public short cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION {
        public IntPtr hProcess, hThread;
        public int dwProcessId, dwThreadId;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool CreateProcess(
        string lpApplicationName,
        StringBuilder lpCommandLine,
        IntPtr lpProcessAttributes,
        IntPtr lpThreadAttributes,
        bool bInheritHandles,
        uint dwCreationFlags,
        IntPtr lpEnvironment,
        string lpCurrentDirectory,
        ref STARTUPINFO lpStartupInfo,
        out PROCESS_INFORMATION lpProcessInformation);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr hObject);
}
'@
}

function Start-HiddenConsoleProcess {
    # 启动带隐藏控制台的 powershell -File <wrapper>，返回 pid；失败返回 0
    param([string]$PowerShellExe, [string]$WrapperPath, [string]$WorkingDirectory)
    Ensure-SmokeNativeProc
    $cmdLine = '"{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}"' -f $PowerShellExe, $WrapperPath
    $si = New-Object SmokeNativeProc+STARTUPINFO
    $si.cb = [System.Runtime.InteropServices.Marshal]::SizeOf($si)
    $si.dwFlags = [SmokeNativeProc]::STARTF_USESHOWWINDOW
    $si.wShowWindow = [SmokeNativeProc]::SW_HIDE
    $pi = New-Object SmokeNativeProc+PROCESS_INFORMATION
    $sb = New-Object System.Text.StringBuilder $cmdLine
    $ok = [SmokeNativeProc]::CreateProcess(
        $PowerShellExe, $sb,
        [IntPtr]::Zero, [IntPtr]::Zero, $false,
        [SmokeNativeProc]::CREATE_NEW_CONSOLE,
        [IntPtr]::Zero, $WorkingDirectory,
        [ref]$si, [ref]$pi)
    if (-not $ok) { return 0 }
    $pidStarted = $pi.dwProcessId
    [void][SmokeNativeProc]::CloseHandle($pi.hThread)
    [void][SmokeNativeProc]::CloseHandle($pi.hProcess)
    return $pidStarted
}


function Start-InheritedCmd {
    # 在已有隐藏控制台中直接启动 cmd /c，让子进程继承（不要再开 conhost --headless，
    # 否则 GetConsoleWindow=0，Node 仍会弹出 Windows Terminal）。
    param([string]$CmdLine, [string]$WorkingDirectory)
    $cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo.FileName = $cmdExe
    $p.StartInfo.Arguments = '/c ' + $CmdLine
    $p.StartInfo.WorkingDirectory = $WorkingDirectory
    $p.StartInfo.UseShellExecute = $false
    $p.StartInfo.CreateNoWindow = $false
    if ($p.Start()) { return $p.Id }
    return 0
}
function Start-HeadlessCmd {
    # 在独立的隐藏控制台里跑 cmd /c <CmdLine>，返回 pid（给 start_all/stop_all 用）。
    # 必须是新进程+新控制台：若与本脚本（Start-Transcript）共享句柄，start_all 拉起的常驻
    # 服务会继承这些句柄，run_exit.cmd 要等服务退出才写 .code（实测卡满 300s）。
    # 不要在 SMOKE_HEADLESS 时改走 Start-InheritedCmd。
    # 优先 conhost.exe --headless；失败再回退 CreateProcess / Start-Process。
    param([string]$CmdLine, [string]$WorkingDirectory)
    $conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
    $cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
    if (Test-Path -LiteralPath $conhost) {
        $p = Start-Process -FilePath $conhost -ArgumentList @('--headless', $cmdExe, '/c', $CmdLine) `
            -WorkingDirectory $WorkingDirectory -PassThru -WindowStyle Hidden
        if ($p) { return $p.Id }
    }
    $cmdLineFull = '"{0}" /c {1}' -f $cmdExe, $CmdLine
    $started = 0
    try {
        Ensure-SmokeNativeProc
        $si = New-Object SmokeNativeProc+STARTUPINFO
        $si.cb = [System.Runtime.InteropServices.Marshal]::SizeOf($si)
        $si.dwFlags = [SmokeNativeProc]::STARTF_USESHOWWINDOW
        $si.wShowWindow = [SmokeNativeProc]::SW_HIDE
        $pi = New-Object SmokeNativeProc+PROCESS_INFORMATION
        $sb = New-Object System.Text.StringBuilder $cmdLineFull
        $ok = [SmokeNativeProc]::CreateProcess($cmdExe, $sb, [IntPtr]::Zero, [IntPtr]::Zero, $false, [SmokeNativeProc]::CREATE_NEW_CONSOLE, [IntPtr]::Zero, $WorkingDirectory, [ref]$si, [ref]$pi)
        if ($ok) {
            $started = $pi.dwProcessId
            [void][SmokeNativeProc]::CloseHandle($pi.hThread)
            [void][SmokeNativeProc]::CloseHandle($pi.hProcess)
        }
    } catch { $started = 0 }
    if ($started) { return $started }
    $p = Start-Process -FilePath $cmdExe -ArgumentList @('/c', $CmdLine) -WorkingDirectory $WorkingDirectory -PassThru -WindowStyle Hidden
    if ($p) { return $p.Id }
    return 0
}

function Wait-ExitCodeFile {
    param([string]$CodeFile, [int]$TimeoutSec, [int]$RunnerPid = 0)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while (-not (Test-Path -LiteralPath $CodeFile) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
    if (-not (Test-Path -LiteralPath $CodeFile)) {
        if ($RunnerPid -gt 0) {
            try { & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID $RunnerPid /T /F 2>$null | Out-Null } catch { }
        }
        return [pscustomobject]@{ Code = -1; TimedOut = $true }
    }
    Start-Sleep -Milliseconds 200
    $code = ([System.IO.File]::ReadAllText($CodeFile)).Trim()
    Remove-Item -LiteralPath $CodeFile -Force -ErrorAction SilentlyContinue
    $n = 0
    if (-not [int]::TryParse($code, [ref]$n)) { $n = -2 }
    return [pscustomobject]@{ Code = $n; TimedOut = $false }
}

function Invoke-DeployScript {
    param([string]$ScriptPath, [string]$OutFile, [string]$ErrFile, [int]$TimeoutSec)
    $codeFile = "$OutFile.code"
    Remove-Item -LiteralPath $codeFile -Force -ErrorAction SilentlyContinue
    # 仍用 run_exit.cmd：由 cmd 重定向并写退出码。不要用 PowerShell 的 1>$out 包一层，
    # 否则 start_all 拉起的常驻进程会继承该重定向句柄，包装进程要等服务退出才结束（实测卡满 300s）。
    $cmdLine = '"{0}" "{1}" "{2}" "{3}" "{4}"' -f $Helper, $ScriptPath, $OutFile, $ErrFile, $codeFile
    $runnerPid = Start-HeadlessCmd -CmdLine $cmdLine -WorkingDirectory $Root
    if (-not $runnerPid) { return [pscustomobject]@{ Code = -3; TimedOut = $false } }
    return (Wait-ExitCodeFile -CodeFile $codeFile -TimeoutSec $TimeoutSec -RunnerPid $runnerPid)
}

function Get-Head { try { return ((& git -C $Root rev-parse HEAD 2>$null) | Out-String).Trim() } catch { return '' } }

function Resolve-BaseRef {
    $candidates = @()
    if ($BaseRef) { $candidates += $BaseRef }
    $candidates += @('platform/main', 'origin/main', 'main')
    foreach ($c in $candidates) {
        if ([string]::IsNullOrWhiteSpace($c)) { continue }
        & git -C $Root rev-parse --verify $c 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $c }
    }
    return ''
}

function Get-ChangedPaths {
    param([string]$Base)
    # 返回对象，避免 PowerShell 把空数组展开成 $null（与真正的失败分不清）
    $result = New-Object psobject -Property @{ Ok = $false; Paths = @() }
    if (-not $Base) { return $result }
    $raw = & git -C $Root diff --name-only "$Base...HEAD" 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        $result | Add-Member -NotePropertyName Detail -NotePropertyValue ("exit=$code out=" + (Short ("$raw") 200)) -Force
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

function Get-DescendantPids {
    param([int]$RootPid)
    $ids = New-Object System.Collections.Generic.List[int]
    if ($RootPid -le 0) { return @() }
    $ids.Add($RootPid)
    $i = 0
    while ($i -lt $ids.Count) {
        $cur = $ids[$i]; $i++
        try {
            Get-CimInstance Win32_Process -Filter "ParentProcessId=$cur" -ErrorAction SilentlyContinue |
                ForEach-Object { [void]$ids.Add([int]$_.ProcessId) }
        } catch { }
    }
    return @($ids | Select-Object -Unique)
}
function Stop-ProcessTree {
    param([int]$RootPid)
    $killed = New-Object System.Collections.Generic.List[string]
    foreach ($id in ((Get-DescendantPids -RootPid $RootPid) | Sort-Object -Descending)) {
        $pr = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
        $nm = if ($pr) { $pr.Name } else { '?' }
        try {
            Stop-Process -Id $id -Force -ErrorAction Stop
            [void]$killed.Add(("$nm/$id"))
        } catch { }
    }
    return @($killed)
}
function Test-FeBuildOutputReady {
    param([string]$OutFile, [string]$IndexPath, [datetime]$StartedAt)
    $built = $false
    if (Test-Path -LiteralPath $OutFile) {
        try {
            $fs = New-Object IO.FileStream($OutFile, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            $sr = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8, $true)
            $txt = $sr.ReadToEnd()
            $sr.Dispose(); $fs.Dispose()
            if ($txt -match 'built in\s+[\d\.]+s') { $built = $true }
        } catch { }
    }
    $fresh = $false
    if ($IndexPath -and (Test-Path -LiteralPath $IndexPath)) {
        $resolved = [IO.Path]::GetFullPath($IndexPath)
        if ($resolved.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) -and ($resolved -notlike 'D:\01_*')) {
            $mt = (Get-Item -LiteralPath $IndexPath).LastWriteTime
            if ($mt -ge $StartedAt.AddSeconds(-2)) { $fresh = $true }
        }
    }
    return ($built -and $fresh)
}
function Invoke-RedirectedProcess {
    # 直接 Start ProcessStartInfo：stdout/stderr 重定向到本函数创建的文件流（不是父进程管道），
    # 等待该 Process 对象。不设 CreateNoWindow，以便继承 SMOKE_HEADLESS 隐藏控制台
    #（GetConsoleWindow≠0，避免 Node 再弹出 Windows Terminal）。stdio 已重定向到文件。
    param([string]$FileName, [string]$Arguments, [string]$WorkingDirectory, [string]$OutFile, [string]$ErrFile, [int]$TimeoutSec, [hashtable]$ExtraEnv, [scriptblock]$EarlyDone)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FileName
    $psi.Arguments = $Arguments
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    if ($ExtraEnv) {
        foreach ($k in $ExtraEnv.Keys) {
            $psi.EnvironmentVariables[$k] = [string]$ExtraEnv[$k]
        }
    }
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    $outFs = $null; $errFs = $null
    $startedAt = Get-Date
    try {
        $outFs = New-Object IO.FileStream($OutFile, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
        $errFs = New-Object IO.FileStream($ErrFile, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
        if (-not $p.Start()) {
            return [pscustomobject]@{ Code = -3; TimedOut = $false; Pid = 0; Killed = @(); Detail = "无法启动 $FileName"; ElapsedSec = 0 }
        }
        try { $p.StandardInput.Close() } catch { }
        $outCopy = $p.StandardOutput.BaseStream.CopyToAsync($outFs)
        $errCopy = $p.StandardError.BaseStream.CopyToAsync($errFs)
        $rootPid = $p.Id
        $deadline = $startedAt.AddSeconds($TimeoutSec)
        $earlyAt = $null
        $killed = @()
        while (-not $p.HasExited -and (Get-Date) -lt $deadline) {
            if ($EarlyDone -and -not $earlyAt) {
                try { if (& $EarlyDone $startedAt) { $earlyAt = Get-Date } } catch { }
            }
            # vite/rolldown 写完产物后，残留 esbuild/node 可能仍占着重定向句柄，父 npm 一直不退出。
            # 产物已就绪后再宽限 15 秒，仍不退出则只杀掉本进程树。
            if ($earlyAt -and ((Get-Date) - $earlyAt).TotalSeconds -ge 15) {
                $killed = @(Stop-ProcessTree -RootPid $rootPid)
                break
            }
            Start-Sleep -Milliseconds 300
        }
        $timedOut = $false
        if (-not $p.HasExited) {
            if (-not $killed -or $killed.Count -eq 0) { $killed = @(Stop-ProcessTree -RootPid $rootPid) }
            if (-not $earlyAt) { $timedOut = $true }
            try { [void]$p.WaitForExit(8000) } catch { }
        }
        try { [void]$outCopy.Wait(2000) } catch { }
        try { [void]$errCopy.Wait(2000) } catch { }
        try { $outFs.Flush(); $errFs.Flush() } catch { }
        $code = -1
        if ($p.HasExited) { $code = $p.ExitCode }
        $elapsed = [math]::Round(((Get-Date) - $startedAt).TotalSeconds, 1)
        return [pscustomobject]@{
            Code = $code; TimedOut = $timedOut; Pid = $rootPid; Killed = $killed
            EarlyDone = [bool]$earlyAt; Detail = ''; ElapsedSec = $elapsed
        }
    } finally {
        if ($outFs) { try { $outFs.Dispose() } catch { } }
        if ($errFs) { try { $errFs.Dispose() } catch { } }
        try { $p.Dispose() } catch { }
    }
}

function Invoke-NpmBuild {
    param([string]$WorkDir, [string]$OutFile, [string]$ErrFile, [int]$TimeoutSec, [string]$FeDistOut)
    if (-not (Test-Path -LiteralPath $NpmCmd)) {
        return [pscustomobject]@{ Code = -3; TimedOut = $false; Detail = "缺少 $NpmCmd" }
    }
    if (-not (Test-Path -LiteralPath $WorkDir)) {
        return [pscustomobject]@{ Code = -3; TimedOut = $false; Detail = "缺少目录 $WorkDir" }
    }
    $cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $extra = @{}
    $extra['PATH'] = ($NodeDir + ';' + $env:PATH)
    if ($FeDistOut) { $extra['FE_DIST_OUT'] = $FeDistOut }
    $early = $null
    if ($FeDistOut) {
        $idx = $FeDistIndex
        $early = { param($startedAt) Test-FeBuildOutputReady -OutFile $OutFile -IndexPath $idx -StartedAt $startedAt }.GetNewClosure()
    }
    $args = '/d /s /c npm.cmd run build'
    $r = Invoke-RedirectedProcess -FileName $cmdExe -Arguments $args -WorkingDirectory $WorkDir `
        -OutFile $OutFile -ErrFile $ErrFile -TimeoutSec $TimeoutSec -ExtraEnv $extra -EarlyDone $early
    $idxMt = ''
    if (Test-Path -LiteralPath $FeDistIndex) { $idxMt = (Get-Item -LiteralPath $FeDistIndex).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') }
    $ready = $false
    if ($FeDistOut) { $ready = Test-FeBuildOutputReady -OutFile $OutFile -IndexPath $FeDistIndex -StartedAt (Get-Date).AddSeconds(-[math]::Max($r.ElapsedSec, 1)) }
    # 若已在等待中判定产物就绪，$ready 用文件内容再确认一次（index mtime 相对现在可能已超过 startedAt 窗口）
    if (-not $ready -and $FeDistOut -and (Test-Path -LiteralPath $OutFile) -and (Test-Path -LiteralPath $FeDistIndex)) {
        $txt = ''
        try { $txt = [IO.File]::ReadAllText($OutFile) } catch { }
        $resolved = [IO.Path]::GetFullPath($FeDistIndex)
        if (($txt -match 'built in\s+[\d\.]+s') -and $resolved.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase)) { $ready = $true }
    }
    $code = $r.Code
    $timedOut = $r.TimedOut
    if ($timedOut -and $ready) { $timedOut = $false; if ($code -ne 0) { $code = 0 } }
    if ($r.EarlyDone -and $ready -and $r.Killed.Count -gt 0 -and $code -ne 0) { $code = 0 }
    $bits = @("exit=$code", "elapsed=$($r.ElapsedSec)s", "pid=$($r.Pid)", "log=$OutFile")
    if ($idxMt) { $bits += "indexMtime=$idxMt" }
    if ($r.EarlyDone) { $bits += 'viteDone=1' }
    if ($r.Killed -and $r.Killed.Count -gt 0) { $bits += ('killed=' + ($r.Killed -join ',')) }
    if ($timedOut) { $bits += "超时 ${TimeoutSec}s" }
    return [pscustomobject]@{ Code = $code; TimedOut = $timedOut; Detail = ($bits -join ' ') }
}

function Test-IsKnownFailure {
    param([string]$NodeId)
    if ($NoIgnoreKnownFailures) { return $false }
    foreach ($k in @($KnownFailures)) {
        if ([string]::IsNullOrWhiteSpace($k)) { continue }
        if ($NodeId -eq $k -or $NodeId.StartsWith($k + '[')) { return $true }
    }
    return $false
}

# ---------------------------------------------------------------- 结果文件
$headFull = Get-Head
if (-not $Sha) { $Sha = $headFull }
if ($Sha -notmatch '^[0-9a-fA-F]{7,40}$') { Write-Host "-Sha 必须是 7~40 位十六进制: $Sha" -ForegroundColor Red; Exit-Smoke 2 }
$sha7 = $Sha.Substring(0, 7).ToLower()
$tag = "{0}_{1}" -f $PR, $sha7
$ResultFile = Join-Path $Tmp ("smoke_result_{0}.md" -f $tag)
$PytestOut  = Join-Path $Tmp ("smoke_pytest_{0}.out" -f $tag)
$LogBase    = Join-Path $Tmp ("smoke_{0}" -f $tag)
$pytestSummary = ''

function Save-Result {
    param([string]$Verdict)
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("# 冒烟结果 PR #$PR @ $sha7")
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine("- 根目录: $Root")
    [void]$sb.AppendLine("- HEAD(开始): $headFull")
    [void]$sb.AppendLine("- HEAD(结束): $(Get-Head)")
    [void]$sb.AppendLine("- 开始: $($startedAt.ToString('yyyy-MM-dd HH:mm:ss'))  结束: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))")
    [void]$sb.AppendLine("- pytest: $(if ($Pytest) { "MY_APP_DB_MODE=sqlite -m 'not live' -n $PytestWorkers --dist $PytestDist" } else { '未跑（未传 -Pytest）' })")
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('## 检查项')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('| 结果 | 检查 | 详情 |')
    [void]$sb.AppendLine('|---|---|---|')
    foreach ($r in $results) { [void]$sb.AppendLine(("| {0} | {1} | {2} |" -f $r.Result, $r.Check, (($r.Detail -replace '\|', '/') -replace '\r?\n', ' '))) }
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('## 分阶段耗时（秒）')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('| 阶段 | 秒 |')
    [void]$sb.AppendLine('|---|---|')
    foreach ($s in $stages) { [void]$sb.AppendLine(("| {0} | {1} |" -f $s.Stage, $s.Sec)) }
    [void]$sb.AppendLine(("| **合计** | **{0}** |" -f [math]::Round($swTotal.Elapsed.TotalSeconds, 1)))
    if ($pytestSummary) { [void]$sb.AppendLine(''); [void]$sb.AppendLine("pytest 摘要: $pytestSummary") }
    if ($notes.Count -gt 0) {
        [void]$sb.AppendLine(''); [void]$sb.AppendLine('## 备注')
        foreach ($n in $notes) { [void]$sb.AppendLine("- $n") }
    }
    $fail = @($results | Where-Object { $_.Result -eq 'FAIL' }).Count
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine("TOTAL=$($results.Count) FAIL=$fail")
    [void]$sb.AppendLine("RESULT: $Verdict")
    [System.IO.File]::WriteAllText($ResultFile, $sb.ToString(), $Utf8NoBom)
}

# ============================================================ 0. 预检（不启动、不停止任何进程）
Start-Stage '0. 预检'
$refuse = @()
if (-not (Test-Path -LiteralPath (Join-Path $Root 'ISOLATED_COPY.flag'))) { $refuse += '缺少 ISOLATED_COPY.flag' }
if (-not (Test-Path -LiteralPath $AuthSrc)) { $refuse += "缺权威源 app.db：$AuthSrc（可用 -AuthSource 指定）" }
if (-not (Test-Path -LiteralPath $Helper)) { $refuse += "缺少 $Helper" }
$busy = @()
foreach ($p in $TestPorts) { $pp = @(Get-ListenPid $p); if ($pp.Count -gt 0) { $busy += ("{0}->{1}" -f $p, ($pp -join ',')) } }
if ($busy.Count -gt 0) { $refuse += ("02 端口已被占用（可能有别的冒烟在跑）: " + ($busy -join ' ')) }
$prodPre = @(); foreach ($x in (Get-ListenPid 80)) { $prodPre += ("{0}->{1}" -f $x, (Get-ExePath $x)) }
$prod443Pre = @(); foreach ($x in (Get-ListenPid 443)) { $prod443Pre += ("{0}->{1}" -f $x, (Get-ExePath $x)) }
$script:ProdBefore = @($prodPre + $prod443Pre)
Write-Host ("  启动前 80 归属: {0}" -f ($prodPre -join ', '))
Write-Host ("  启动前 443 归属: {0}" -f ($prod443Pre -join ', '))
if ($refuse.Count -gt 0) {
    foreach ($r in $refuse) { Add-R '预检' $false $r }
    Stop-Stage; Save-Result 'REFUSED'
    Write-Host "预检拒绝，未启动或停止任何进程。报告: $ResultFile" -ForegroundColor Red
    Exit-Smoke 2
}
Add-R '预检:02 标志存在，02 端口空闲' $true ("80->" + ($prodPre -join ', '))

# ============================================================ 1. §8.1 前端构建（start_all 之前）
Start-Stage '1. §8.1 前端构建'
$feDistOutSlash = $FeDistDir.Replace('\', '/')
$needFe = $false
$needPortal = $false
$changedList = @()
$diff = $null
$baseUsed = Resolve-BaseRef
if ($SkipFeBuild -and $ForceFeBuild) {
    Add-Note '同时传了 -SkipFeBuild 与 -ForceFeBuild，以 -SkipFeBuild 为准'
}
if ($SkipFeBuild) {
    Add-R '§8.1 前端构建' $true '按 -SkipFeBuild 跳过重建'
} else {
    if ($ForceFeBuild) {
        $needFe = $true
        if (Test-Path -LiteralPath $PortalDir) { $needPortal = $true }
        Add-Note '按 -ForceFeBuild 强制重建 my_app 前端与门户'
    } elseif (-not $baseUsed) {
        Add-R '§8.1 解析对比基线 (platform/main 或 main)' $false '找不到 platform/main、origin/main 或 main，且未传 -BaseRef；未重建。可用 -ForceFeBuild 强制构建'
    } else {
        $diff = Get-ChangedPaths -Base $baseUsed
        if (-not $diff.Ok) {
            Add-R '§8.1 git diff --name-only' $false ("git diff $baseUsed...HEAD 失败，未重建。可用 -ForceFeBuild 强制构建。 " + $diff.Detail)
            $changedList = @()
        } else {
            $changedList = @($diff.Paths)
            $feHits = @($changedList | Where-Object { Test-PathMatchFe $_ })
            $portalHits = @($changedList | Where-Object { Test-PathMatchPortal $_ })
            $needFe = ($feHits.Count -gt 0)
            $needPortal = ($portalHits.Count -gt 0)
            $detail = "base=$baseUsed changed=$($changedList.Count) feHits=$($feHits.Count) portalHits=$($portalHits.Count)"
            if ($feHits.Count -gt 0) { $detail += ' fe=' + (($feHits | Select-Object -First 8) -join ', ') }
            if ($portalHits.Count -gt 0) { $detail += ' portal=' + (($portalHits | Select-Object -First 8) -join ', ') }
            Add-R ("§8.1 相对 {0} 检测前端/lockfile 改动" -f $baseUsed) $true $detail
        }
    }
    if ($needFe) {
        $br = Invoke-NpmBuild -WorkDir $FeDir -OutFile "$LogBase`_fe_build.out" -ErrFile "$LogBase`_fe_build.err" `
            -TimeoutSec $FeBuildTimeoutSec -FeDistOut $feDistOutSlash
        $buildOk = ($br.Code -eq 0) -and (-not $br.TimedOut)
        Add-R '§8.1 npm run build (apps\my_app\frontend, FE_DIST_OUT→apps\_fe_dist)' $buildOk $br.Detail
        $resolved = ''
        if (Test-Path -LiteralPath $FeDistDir) { $resolved = [IO.Path]::GetFullPath($FeDistDir) }
        $placeOk = ($resolved -ne '') -and $resolved.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $FeDistIndex)
        $notProd = ($resolved -notlike 'D:\01_*')
        Add-R ("§8.1 构建产物落在 $Root\apps\_fe_dist（未写入 01）") ($placeOk -and $notProd) "dir=$resolved indexExists=$(Test-Path -LiteralPath $FeDistIndex)"
    } elseif (-not $SkipFeBuild -and $baseUsed -and ($null -ne $diff) -and $diff.Ok) {
        Add-R '§8.1 前端无需重建' $true "相对 $baseUsed，apps\my_app\frontend 与 lockfile 无改动；沿用现有 apps\_fe_dist"
    }
    if ($needPortal) {
        $prb = Invoke-NpmBuild -WorkDir $PortalDir -OutFile "$LogBase`_portal_build.out" -ErrFile "$LogBase`_portal_build.err" `
            -TimeoutSec $FeBuildTimeoutSec -FeDistOut $null
        $pOk = ($prb.Code -eq 0) -and (-not $prb.TimedOut)
        Add-R '§8.1 npm run build (apps\portal)' $pOk $prb.Detail
    }
}
Stop-Stage

$started = $false
try {
    # ======================================================== 2. start_all
    Start-Stage '2. start_all'
    $started = $true
    $sr = Invoke-DeployScript -ScriptPath (Join-Path $Root 'ops\deploy\start_all.ps1') -OutFile "$LogBase`_start_all.out" -ErrFile "$LogBase`_start_all.err" -TimeoutSec 300
    $startOk = ($sr.Code -eq 0) -and (-not $sr.TimedOut)
    Add-R 'start_all.ps1 退出码为 0' $startOk ("exit=$($sr.Code) timedOut=$($sr.TimedOut) log=$LogBase`_start_all.out")

    # ======================================================== 3. §8.2 数据（PG 已由 start_all 拉起；不单独 pg_ctl）
    Start-Stage '3. §8.2 数据'
    $dataDir = ''
    $pgReady = $false
    if (Test-Path -LiteralPath $PsqlExe) {
        $oldTmo = $env:PGCONNECT_TIMEOUT
        $env:PGCONNECT_TIMEOUT = '3'
        $tries = if ($startOk) { 30 } else { 3 }
        for ($i = 0; $i -lt $tries; $i++) {
            $probe = & $PsqlExe -w -h 127.0.0.1 -p $PgPort -U $PgUser -d postgres -tAc 'SELECT 1' 2>$null
            if ($LASTEXITCODE -eq 0 -and "$probe".Trim() -eq '1') { $pgReady = $true; break }
            Start-Sleep -Seconds 1
        }
        if ($pgReady) {
            $dataDir = "$(& $PsqlExe -w -h 127.0.0.1 -p $PgPort -U $PgUser -d postgres -tAc 'SHOW data_directory' 2>$null)".Trim()
        }
        if ($null -eq $oldTmo) { Remove-Item Env:\PGCONNECT_TIMEOUT -ErrorAction SilentlyContinue } else { $env:PGCONNECT_TIMEOUT = $oldTmo }
    } else {
        Add-Note "缺少 $PsqlExe，无法执行 SHOW data_directory"
    }
    $normDir = ($dataDir -replace '/', '\')
    $dirOk = ($dataDir -ne '') -and $normDir.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) -and ($normDir -notlike 'D:\01_*')
    Add-R ("§8.2 SHOW data_directory 在 $Root 下（非生产 5432）") $dirOk "pgReady=$pgReady data_directory='$dataDir' port=$PgPort"

    $authOut = ''
    $authCode = -1
    if (Test-Path -LiteralPath $PyExe) {
        $authOut = & $PyExe (Join-Path $Root 'ops\deploy\local_authority_tools.py') check --source $AuthSrc --target $AuthTgt 2>&1 | Out-String
        $authCode = $LASTEXITCODE
    } else {
        $authOut = "缺少 $PyExe"
    }
    $got = @{}
    foreach ($line in ($authOut -split "`r?`n")) {
        if ($line -match '^\s*(credentials|task_schedule_config|email_recipients)\s+source=(-?\d+)\s+target=(-?\d+)') {
            $got[$Matches[1]] = @{ Src = [int]$Matches[2]; Tgt = [int]$Matches[3] }
        }
    }
    $countOk = $true
    $countBits = @("exit=$authCode")
    $countParts = @()
    foreach ($t in $AuthTables) {
        if (-not $got.ContainsKey($t)) {
            $countOk = $false
            $countBits += "$t=MISSING"
            $countParts += ("{0}=?" -f $t)
        } else {
            $srcN = $got[$t].Src
            $tgtN = $got[$t].Tgt
            $countBits += ("{0} source={1} target={2}" -f $t, $srcN, $tgtN)
            $countParts += ("{0}={1}" -f $t, $tgtN)
            if ($srcN -gt 0 -and $tgtN -ne $srcN) { $countOk = $false }
        }
    }
    Add-R ('§8.2 local_authority_tools check 三表行数与源一致（' + ($countParts -join '/') + '）') `
        (($authCode -eq 0) -and $countOk) ($countBits -join '; ')

    # ======================================================== 4. §8.3 检查
    Start-Stage '4. §8.3 检查'
    if (-not $startOk) {
        Add-R '§8.3 检查' $false 'start_all 失败，跳过 §8.3 检查（见 start_all 日志）'
    } else {
        # 后端偶尔比 start_all 稍晚就绪：/api/health 最多等 15 秒
        $health = ''; $dbMode = ''
        for ($i = 0; $i -lt 15; $i++) {
            $health = Get-Body 'http://127.0.0.1:7039/api/health'
            try { $dbMode = ($health | ConvertFrom-Json).db_mode } catch { $dbMode = '' }
            if ($dbMode) { break }
            Start-Sleep -Seconds 1
        }
        Add-R '(1) /api/health 返回 db_mode=pg' ($dbMode -eq 'pg') ("db_mode='$dbMode' body=$(Short $health)")

        # 与 /api/health 同理：后端偶发比 start_all 晚就绪（2026-11-18 #157 实测 wo 首秒 len=0）
        $wo = ''; $woOk = $false
        for ($i = 0; $i -lt 15; $i++) {
            $wo = Get-Body 'http://127.0.0.1:7039/api/wo-dashboard/overview'
            $woOk = ($wo.Length -gt 20) -and ($wo -notmatch '^\s*\{\s*\}\s*$') -and ($wo -notmatch '^\s*\[\s*\]\s*$')
            if ($woOk) { break }
            Start-Sleep -Seconds 1
        }
        Add-R '(2) /api/wo-dashboard/overview 返回非空业务数据' $woOk ("len=$($wo.Length) body=$(Short $wo 200)")

        $c = Get-Status 'http://127.0.0.1:9277/uniplam/'
        Add-R '(3) 门户 http://127.0.0.1:9277/uniplam/ 返回 200' ($c -eq '200') "http_code=$c"

        $servedFile = "$LogBase`_served_index.html"
        $c = Get-Status 'http://127.0.0.1:8080/myapp/' -OutFile $servedFile
        Add-R '(4a) nginx http://127.0.0.1:8080/myapp/ 返回 200' ($c -eq '200') "http_code=$c"
        $c = Get-Status 'https://127.0.0.1:8443/myapp/' -Insecure
        Add-R '(4b) nginx https://127.0.0.1:8443/myapp/ 返回 200' ($c -eq '200') "http_code=$c"

        $distHash = ''; $servedHash = ''
        if (Test-Path -LiteralPath $FeDistIndex) { $distHash = (Get-FileHash -LiteralPath $FeDistIndex -Algorithm SHA256).Hash }
        if (Test-Path -LiteralPath $servedFile) { $servedHash = (Get-FileHash -LiteralPath $servedFile -Algorithm SHA256).Hash }
        Add-R '(5) 8080 提供的 index.html 与 apps\_fe_dist\index.html 同 sha256' ($servedHash -ne '' -and $servedHash -eq $distHash) "dist=$distHash served=$servedHash"

        $nginxDir = Join-Path $Root 'nginx-1.28.0'
        $ntOut = "$LogBase`_nginx_t.out"; $ntErr = "$LogBase`_nginx_t.err"
        $nginxExe = Join-Path $nginxDir 'nginx.exe'
        $ntArgs = ('-t -p {0} -c conf/nginx.conf' -f ($nginxDir.TrimEnd('\') + '\'))
        $ntRun = Invoke-RedirectedProcess -FileName $nginxExe -Arguments $ntArgs -WorkingDirectory $nginxDir `
            -OutFile $ntOut -ErrFile $ntErr -TimeoutSec 30 -ExtraEnv @{} -EarlyDone $null
        $ntxt = ((@(Get-Content $ntOut -ErrorAction SilentlyContinue) + @(Get-Content $ntErr -ErrorAction SilentlyContinue)) -join ' | ')
        Add-R '(6) 02 的 nginx -t 通过（02 自己的 nginx.exe 与 conf）' ((-not $ntRun.TimedOut) -and ($ntRun.Code -eq 0)) ("exit=$($ntRun.Code) out=$(Short $ntxt 300)")

        $own = @()
        foreach ($p2 in $TestPorts) { foreach ($x in (Get-ListenPid $p2)) { $own += ("{0}:{1}->{2}" -f $p2, $x, (Get-ExePath $x)) } }
        $ownOk = ($own.Count -gt 0) -and (@($own | Where-Object { $_ -notlike ('*->' + $Root + '\*') }).Count -eq 0)
        Add-R ("副本端口均由 $Root 下的进程监听") $ownOk ($own -join '; ')
    }
}
finally {
    # ======================================================== 5. stop_all（无论成败都停本副本）
    if ($started) {
        Start-Stage '5. stop_all'
        $tr = Invoke-DeployScript -ScriptPath (Join-Path $Root 'ops\deploy\stop_all.ps1') -OutFile "$LogBase`_stop_all.out" -ErrFile "$LogBase`_stop_all.err" -TimeoutSec 180
        Add-R 'stop_all.ps1 退出码为 0' (($tr.Code -eq 0) -and (-not $tr.TimedOut)) ("exit=$($tr.Code) timedOut=$($tr.TimedOut) log=$LogBase`_stop_all.out")
    }
}

# ============================================================ 6. §8.4 停止后确认
Start-Stage '6. §8.4 停止后确认'
$releaseOk = $false; $rel = @()
for ($i = 0; $i -lt 15; $i++) {      # 端口释放最多等 15 秒
    $rel = @(); $releaseOk = $true
    foreach ($p3 in $TestPorts) { $n = @(Get-ListenPid $p3).Count; $rel += ("{0}={1}" -f $p3, $n); if ($n -gt 0) { $releaseOk = $false } }
    if ($releaseOk) { break }
    Start-Sleep -Seconds 1
}
$postProd = @(); foreach ($x in (Get-ListenPid 80)) { $postProd += ("{0}->{1}" -f $x, (Get-ExePath $x)) }
$post443 = @(); foreach ($x in (Get-ListenPid 443)) { $post443 += ("{0}->{1}" -f $x, (Get-ExePath $x)) }
$prodAfter = @($postProd + $post443)
$prodOk = ($postProd.Count -gt 0) -and ($post443.Count -gt 0) -and `
    (($prodAfter -join '|') -eq ($script:ProdBefore -join '|'))
Add-R '80/443 未被冒烟触碰（归属进程与开跑前逐字一致）' $prodOk ("before: " + ($script:ProdBefore -join ', ') + " ; after: " + ($prodAfter -join ', '))
Add-R ('02 的 {0} 已释放' -f ($TestPorts -join '/')) $releaseOk ($rel -join ' ')
foreach ($p4 in $ExtraPorts) {
    foreach ($x in (Get-ListenPid $p4)) { Add-Note ("端口 {0} 仍被 pid {1} ({2}) 监听：后端派生代理，stop_all 不托管，仅记录不杀" -f $p4, $x, (Get-ExePath $x)) }
}

# ============================================================ 7. 可选：离线 pytest
if ($Pytest) {
    Start-Stage ("7. pytest (-n {0})" -f $PytestWorkers)
    $xdist = $false
    if ($PytestWorkers -ne '0') {
        & $PyExe -c "import xdist" 2>$null
        $xdist = ($LASTEXITCODE -eq 0)
        if (-not $xdist) { Add-Note '未安装 pytest-xdist，pytest 改为串行' }
    }
    $pa = @('-m', 'pytest', 'tests', '-m', '"not live"', '-p', 'no:cacheprovider', '--tb=short')
    if ($xdist) { $pa += @('-n', $PytestWorkers, '--dist', $PytestDist) }
    $errFile = "$PytestOut.err"
    $codeFile = "$PytestOut.code"
    $runner = "$PytestOut.runner.cmd"
    # 与 CI 一致：不要连已关闭的 PG（stop_all 后 55432 已释放），强制走 sqlite
    # 在 runner.cmd 里设环境，避免本进程环境泄漏；参数逐个加引号。
    $qargs = @()
    foreach ($a in $pa) {
        $tok = "$a"
        if ($tok.StartsWith('"') -and $tok.EndsWith('"')) { $qargs += $tok } else { $qargs += ('"{0}"' -f $tok.Replace('"', '""')) }
    }
    Remove-Item -LiteralPath $codeFile -Force -ErrorAction SilentlyContinue
    $pytestExe = $PyExe
    $cmdBody = @(
        '@echo off',
        'set "MY_APP_DB_MODE=sqlite"',
        ('cd /d "{0}"' -f $AppDir),
        ('"{0}" {1} 1>"{2}" 2>"{3}"' -f $pytestExe, ($qargs -join ' '), $PytestOut, $errFile),
        ('>"{0}" echo %ERRORLEVEL%' -f $codeFile)
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($runner, $cmdBody)
    # pytest 必须继承本脚本已有的隐藏经典控制台（GetConsoleWindow≠0）。
    # 若再开 conhost --headless，GetConsoleWindow=0，tests 里 node spawnSync(npm.cmd/powershell)
    # 会弹出 Windows Terminal。
    $runnerPid = Start-InheritedCmd -CmdLine ('"{0}"' -f $runner) -WorkingDirectory $AppDir
    if (-not $runnerPid) {
        $runnerPid = Start-HeadlessCmd -CmdLine ('"{0}"' -f $runner) -WorkingDirectory $AppDir
    }
    $wait = Wait-ExitCodeFile -CodeFile $codeFile -TimeoutSec $PytestTimeoutSec -RunnerPid $runnerPid
    if ($wait.TimedOut) {
        Add-R 'pytest 无未登记失败' $false "超时 ${PytestTimeoutSec}s，已停止 pytest 进程树 pid=$runnerPid"
    } else {
        $procExit = $wait.Code
        $txt = ''
        if (Test-Path -LiteralPath $PytestOut) { $txt = [System.IO.File]::ReadAllText($PytestOut, [System.Text.Encoding]::UTF8) }
        $line = @($txt -split "`r?`n" | Where-Object { $_ -match '\d+ (passed|failed|error)' -and $_ -match ' in [\d\.]+s' }) | Select-Object -Last 1
        $pytestSummary = ("$line" -replace '=', '').Trim()
        $failedIds = New-Object System.Collections.ArrayList
        $errorIds = New-Object System.Collections.ArrayList
        foreach ($ln in ($txt -split "`r?`n")) {
            $tln = $ln.Trim()
            if ($tln -match '^(FAILED|ERROR)\s+(\S+)') {
                $kind = $Matches[1]
                $nid = $Matches[2]
                if ($nid -match '^(.*?)(?:\s+-|$)') { $nid = $Matches[1] }
                if ($kind -eq 'ERROR') { [void]$errorIds.Add($nid) } else { [void]$failedIds.Add($nid) }
            }
        }
        $ignored = @($failedIds | Where-Object { Test-IsKnownFailure $_ } | Select-Object -Unique)
        $unexpected = @($failedIds | Where-Object { -not (Test-IsKnownFailure $_) } | Select-Object -Unique)
        $detail = "exit=$procExit summary='$pytestSummary' out=$PytestOut"
        if ($ignored.Count -gt 0) {
            $detail += ' ignored: ' + ($ignored -join ', ')
            Add-Note ('已忽略已知失败（按缺数据处理，不挡冒烟）: ' + ($ignored -join ', '))
        }
        if ($unexpected.Count -gt 0) { $detail += ' unexpected: ' + (($unexpected | ForEach-Object { Short $_ 120 }) -join ' ; ') }
        if ($errorIds.Count -gt 0) { $detail += ' errors: ' + (($errorIds | ForEach-Object { Short $_ 120 }) -join ' ; ') }
        $pytestOk = $false
        if ($procExit -eq 0) {
            $pytestOk = $true
        } elseif (($errorIds.Count -eq 0) -and ($unexpected.Count -eq 0) -and ($ignored.Count -gt 0)) {
            $pytestOk = $true
        }
        Add-R 'pytest 无未登记失败(-m "not live", 与 CI 一致)' $pytestOk $detail
    }
    if ((Test-Path -LiteralPath $errFile) -and ((Get-Item -LiteralPath $errFile).Length -gt 0)) {
        Add-Content -LiteralPath $PytestOut -Value "`r`n===== stderr =====" -Encoding UTF8
        Get-Content -LiteralPath $errFile -Encoding UTF8 | Add-Content -LiteralPath $PytestOut -Encoding UTF8
    }
    Remove-Item -LiteralPath $errFile -Force -ErrorAction SilentlyContinue
}
Stop-Stage

$headEnd = Get-Head
if ($headFull -and $headEnd -ne $headFull) { Add-R 'HEAD 保持不变' $false "start=$headFull end=$headEnd" }

$fail = @($results | Where-Object { $_.Result -eq 'FAIL' }).Count
$verdict = if ($fail -eq 0) { 'PASS' } else { 'FAIL' }
Save-Result $verdict
Write-Host ''
Write-Host ("===== {0}:共 {1} 项,FAIL {2},合计 {3:N1}s =====" -f $verdict, $results.Count, $fail, $swTotal.Elapsed.TotalSeconds) -ForegroundColor $(if ($fail -eq 0) { 'Green' } else { 'Red' })
foreach ($s in $stages) { Write-Host ("  {0,-28} {1,7}s" -f $s.Stage, $s.Sec) }
Write-Host "报告: $ResultFile"
if ($fail -gt 0) { Exit-Smoke 1 } else { Exit-Smoke 0 }
