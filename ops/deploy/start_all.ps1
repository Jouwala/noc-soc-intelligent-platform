<#
  start_all.ps1 —— 离线副本一键启动（由 build_olbr_portable.ps1 -Stage config 铺到 <副本根>\ops\deploy\）
  =============================================================================
  入口：副本根目录的 start.bat（薄壳，只调本脚本）。
  真相源：副本根 .env（模板 ops\deploy\env_template.txt）；端口与盘符都不写死。

  与生产启动脚本的区别（重要，别照抄 my_portal_ctl.ps1）：
    · 生产用 start_hidden.ps1 拉 backend_watchdog + worker 组 + 开机自启任务；
      副本**全部不用** —— MY_APP_DISABLE_SCHEDULER=1 且不设 MY_APP_TASK_QUEUE，
      否则采集任务会连生产上游、并可能重复投递 WhatsApp/邮件告警。
    · 副本直接起 uvicorn / vite preview / tsx server，进程由本脚本用 pid 文件托管；
      stop_all.ps1 只按 pid 文件停，绝不按端口盲杀（源机可能正在跑生产）。
    · 解释器一律用副本自带的 runtime\python312 / runtime\node，路径由 $Root 推出。

  PowerShell 5.1 兼容：不用 ::new()、三元、-Parallel、$IsWindows。
  P1-6 外部目录（ZIP 绿色部署）：设了进程环境变量 NOC_HOME 后，配置/数据/
  日志/运行时一律走部署根下的外部目录（解析见同目录 noc-paths.ps1），版本
  目录在启动后零写入；不设 NOC_HOME 时全部路径与旧行为一致。
  用法：
    powershell -NoProfile -File ops\deploy\start_all.ps1
    powershell -NoProfile -File ops\deploy\start_all.ps1 -DryRun
  =============================================================================
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipNginxConf,
    # 生产模式：不套用「隔离副本」那套改写，直接委托 portal-ctl.ps1 走三个自愈任务。
    [switch]$Production
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# PowerShell 5.1 的 Start-Process 在进程环境里同时存在「同名不同大小写」的键时
# 会抛 "Item has already been added"（典型：终端注入了 HTTP_PROXY 和 http_proxy），
# 导致 backend / frontend / portal / pg_ctl 全部起不来。只在本进程规范化：
# 保留大写键，删掉全小写的重复键；不写系统/用户环境，大写代理照常生效。
$dupEnv = [Environment]::GetEnvironmentVariables('Process')
foreach ($k in @($dupEnv.Keys)) {
    if ($k -ceq $k.ToLowerInvariant() -and $dupEnv.ContainsKey($k.ToUpperInvariant())) {
        [Environment]::SetEnvironmentVariable($k, $null, 'Process')
    }
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()

# 本脚本位于 <Root>\ops\deploy\，故根 = 上两级
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
# P1-6：目录解析集中在 noc-paths.ps1（与 stop_all.ps1 共用，唯一真相源）；
# 缺失则拒绝启动——静默回退会让外部模式的写目标悄悄落回版本目录。
$nocPathsLib = Join-Path $PSScriptRoot 'noc-paths.ps1'
if (-not (Test-Path -LiteralPath $nocPathsLib)) { throw "缺少目录解析脚本: $nocPathsLib（请从仓库重新取 ops\deploy\）" }
. $nocPathsLib
$paths = Get-NocPaths -Root $Root
if ($paths.Mode -eq 'external') { Assert-NocPathsOutsideRoot -Paths $paths }
$TmpDir = $paths.TmpDir
$RuntimeDir = $paths.RuntimeDir
$PyExe = $paths.PyExe
$NodeExe = $paths.NodeExe
$PgBin = $paths.PgBin
$NginxDir = $paths.NginxDir
$NginxExe = Join-Path $NginxDir 'nginx.exe'

if ($Production) {
    # 生产模式下本脚本不做任何「副本改造」：不改端口、不关调度器、不把 DATABASE_URL 指向副本 PG。
    # 起停都交给 portal-ctl.ps1（它 schtasks /run 三个自愈任务，与 5 分钟周期同一路径）。
    Write-Host '[*] 生产模式：委托 ops\deploy\portal-ctl.ps1 -Action start' -ForegroundColor Cyan
    & (Join-Path $Root 'ops\deploy\portal-ctl.ps1') -Action start
    exit $LASTEXITCODE
}
$MyAppDir = Join-Path $Root 'apps\my_app'
$FeDir = Join-Path $MyAppDir 'frontend'
$BotDir = Join-Path $Root 'apps\whatsapp-kb-bot'
$PortalRoot = Join-Path $Root 'apps\portal'
if (-not (Test-Path -LiteralPath $PortalRoot)) { $PortalRoot = $null }

# 防误跑：本脚本只应在 build 出来的副本里执行。主仓库同样有 apps\my_app，
# 但没有 runtime\node\node.exe —— 少了这道检查，误在主仓库跑会往源码树写 .env。
if (-not (Test-Path -LiteralPath $NodeExe) -or
    -not (Test-Path -LiteralPath (Join-Path $MyAppDir 'backend\main.py'))) {
    throw "当前目录不是有效副本（缺 $NodeExe 或 apps\my_app\backend\main.py）：$Root`n本脚本只能在 build_olbr_portable.ps1 生成的副本或 ZIP 部署里运行（外部模式请检查 NOC_HOME/NOC_RUNTIME_DIR）。"
}

function Write-Step { param([string]$m) Write-Host "[*] $m" }
function Write-Ok { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn2 { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Err2 { param([string]$m) Write-Host "[FAIL] $m" -ForegroundColor Red }
function Write-Skip { param([string]$m) Write-Host "[SKIP] $m" -ForegroundColor DarkGray }

if (-not $DryRun -and -not (Test-Path -LiteralPath $TmpDir)) {
    New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null
}

# ---------------------------------------------------------------- .env 载入
# 隔离原则：副本根 .env 是唯一配置真相源，**优先于**继承的进程/系统环境变量。
# 否则在生产机本机试跑时，父进程里的生产端口/路径会污染副本，抢生产服务。
function Import-DotEnv {
    param([string]$Path)
    $cfg = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $cfg }
    foreach ($line in [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#')) { continue }
        $i = $t.IndexOf('=')
        if ($i -le 0) { continue }
        $k = $t.Substring(0, $i).Trim()
        $v = $t.Substring($i + 1).Trim()
        $cfg[$k] = $v
    }
    return $cfg
}

$envFile = $paths.EnvFile
$envTpl = Join-Path $PSScriptRoot 'env_template.txt'
if (-not (Test-Path -LiteralPath $envFile)) {
    if (-not (Test-Path -LiteralPath $envTpl)) { throw "缺少 .env 模板: $envTpl" }
    if (-not $DryRun) {
        $envParent = Split-Path $envFile -Parent
        if ($envParent -and -not (Test-Path -LiteralPath $envParent)) { New-Item -ItemType Directory -Path $envParent -Force | Out-Null }
        Copy-Item -LiteralPath $envTpl -Destination $envFile -Force
        Write-Step "首次启动：已从模板生成 $envFile"
    } else {
        # #192：干跑不写文件，别报「已生成」
        Write-Step "<干跑> 首次启动将从模板生成 $envFile（本次不写文件）"
    }
}
$cfg = Import-DotEnv $envFile

function Get-Cfg {
    param([string]$Key, [string]$Default = '')
    if ($cfg.ContainsKey($Key) -and $cfg[$Key]) { return $cfg[$Key] }
    $existing = [Environment]::GetEnvironmentVariable($Key, 'Process')
    if ($existing) { return $existing }
    return $Default
}
function Set-Cfg {
    param([string]$Key, [string]$Value)
    [Environment]::SetEnvironmentVariable($Key, $Value, 'Process')
}

# ⚠ 下面两个块必须放在 Set-Cfg 定义**之后**：PowerShell 的 function 语句按执行
# 顺序生效，不能像编译语言那样先调用后定义。此前它们写在 $cfg 载入处（本段上方），
# 只要副本里已存在 .env 就会报 "Set-Cfg is not recognized"，start_all 退出 1；
# 只有在全新解包、还没有 .env 的干跑里才碰巧通过 —— 2026-11-19 冒烟 PR #182
# 连续 4 轮卡在这里（start_all.err 448 字节），别再挪回去。

# 把 .env 里的**所有**键导出到本进程环境。
#
# 此前只导出下面 $derived 里的派生键，于是写在 .env 的普通配置（QA_OWU_BASE、
# PORTAL_INITIAL_ADMIN_PASSWORD、START_WA_ANALYSIS…）**根本不生效** —— 用户改了
# .env 却看不到任何变化，是最难排查的一类问题。模板里的空值不导出（保持默认）。
# 顺序很重要：先导 .env，再由下面的 $derived 覆盖派生目录/端口（派生值必须由
# 副本根推导，不能被 .env 或父进程残留值顶掉）。
foreach ($k in @($cfg.Keys)) {
    if ($cfg[$k]) { Set-Cfg $k $cfg[$k] }
}

# 目标机可能没有配套的 runtime ZIP，改用系统安装的 Python / Node / PG。
# 这三个键允许在 .env 里覆盖 noc-paths.ps1 推出来的默认路径；未设置时行为不变。
# 例：NOC_PYTHON_EXE=C:\Python312\python.exe
# （不提供 nginx 目录覆盖：nginx 的 conf/logs/certs 路径由 noc-paths.ps1 一次算出，
#   启动后再改目录会与已展开的配置错位；需要时把 nginx 放到 <根>\nginx-1.28.0\ 或
#   <根>\runtime\nginx-1.28.0\，解析器按这个顺序找。）
$exeOverrides = @(
    @{ Key = 'NOC_PYTHON_EXE'; Var = 'PyExe'; Name = 'Python' },
    @{ Key = 'NOC_NODE_EXE'; Var = 'NodeExe'; Name = 'Node' },
    @{ Key = 'NOC_PG_BIN'; Var = 'PgBin'; Name = 'PostgreSQL bin' }
)
foreach ($o in $exeOverrides) {
    $v = [Environment]::GetEnvironmentVariable($o.Key, 'Process')
    if ([string]::IsNullOrWhiteSpace($v)) { continue }
    Set-Variable -Name $o.Var -Value $v
    Write-Step ("{0} 使用 .env 覆盖: {1}={2}" -f $o.Name, $o.Key, $v)
}
$NginxExe = Join-Path $NginxDir 'nginx.exe'

$HttpPort = [int](Get-Cfg 'NGINX_HTTP_PORT' '8080')
$HttpsPort = [int](Get-Cfg 'NGINX_HTTPS_PORT' '8443')
$BePort = [int](Get-Cfg 'MY_APP_BE_PORT' '7039')
$FePort = [int](Get-Cfg 'MY_APP_FE_PORT' '7091')
$PortalPort = [int](Get-Cfg 'PORTAL_PORT' '9277')
$PgPort = [int](Get-Cfg 'PGPORT' '55432')
$QaPort = [int](Get-Cfg 'QA_HTTP_PORT' '9279')
$WaAnalysisPort = [int](Get-Cfg 'WA_ANALYSIS_PORT' '9278')
$FmsProxyPort = [int](Get-Cfg 'MY_APP_PROXY_FMS_PORT' '8887')
$VmaxProxyPort = [int](Get-Cfg 'MY_APP_PROXY_VMAX_PORT' '8890')
$PgUser = Get-Cfg 'MY_APP_DB_USER' 'myapp'
$PgDb = Get-Cfg 'MY_APP_DB_NAME' 'myapp'
if ($PgUser -notmatch '^[A-Za-z0-9_]+$' -or $PgDb -notmatch '^[A-Za-z0-9_]+$') {
    throw 'MY_APP_DB_USER / MY_APP_DB_NAME 只允许字母数字下划线（会被拼进 psql 命令）'
}
# 隔离熔断：副本端口绝不允许落在生产端口上，且彼此不得重复。
$prodPorts = @(80, 443, 7038, 7090, 9267, 9268, 9269, 5432, 8787, 8790)
$allPorts = @($HttpPort, $HttpsPort, $BePort, $FePort, $PortalPort, $PgPort, $QaPort, $WaAnalysisPort, $FmsProxyPort, $VmaxProxyPort)
foreach ($p in $allPorts) {
    if ($prodPorts -contains $p) { throw "副本端口 $p 与生产端口冲突，请在副本 .env 改到隔离端口后再启动" }
}
$dupPorts = $allPorts | Group-Object | Where-Object { $_.Count -gt 1 }
if ($dupPorts) { throw "副本端口配置有重复: $(($dupPorts | ForEach-Object { $_.Name }) -join ',')" }

# ------------------------------------------------------- 派生变量（盘符无关）
$PgData = $paths.PgData
$FeDist = (Join-Path $Root 'apps\_fe_dist').Replace('\', '/')
$AuthDir = $paths.AuthDir
if (-not $DryRun -and -not (Test-Path -LiteralPath $AuthDir)) {
    New-Item -ItemType Directory -Path $AuthDir -Force | Out-Null
}

# 便携 PG 用 trust 认证且只绑回环，连接串里刻意没有口令 —— 绝不把生产 .env 的
# 明文口令带进副本。database.py 的 load_dotenv(override=False) 保证进程环境优先。
$dbUrl = 'postgresql://{0}@127.0.0.1:{1}/{2}' -f $PgUser, $PgPort, $PgDb
$derived = [ordered]@{
    'PGDATA' = $PgData
    'FE_DIST_OUT' = $FeDist
    'PORTAL_AUTH_DATA_DIR' = $AuthDir
    'PORTAL_QA_NODE' = $NodeExe
    'PORTAL_QA_PORT' = [string]$QaPort
    'PORTAL_QA_HOST' = '127.0.0.1'
    'QA_HTTP_HOST' = '127.0.0.1'
    'QA_HTTP_PORT' = [string]$QaPort
    'MY_APP_API_BASE' = 'http://127.0.0.1:{0}' -f $BePort
    'MY_APP_BACKEND_PORT' = [string]$BePort
    'MY_APP_DB_MODE' = 'pg'
    'DATABASE_URL' = $dbUrl
    'PORT' = [string]$PortalPort
    'PORTAL_HOST' = '127.0.0.1'
    'PGHOSTADDR' = '127.0.0.1'
    'PGPORT' = [string]$PgPort
    # 实时代理隔离：副本用独立端口与自带解释器，绝不复用/击杀生产 8787/8790。
    'MY_APP_PROXY_FMS_PORT' = [string]$FmsProxyPort
    'MY_APP_PROXY_VMAX_PORT' = [string]$VmaxProxyPort
    'MY_APP_PROXY_PY' = $PyExe
    'MY_APP_REPORT_PY' = $PyExe
    # eflow 问答知识库：指向副本同级目录（inspect_tt 等工具据此定位 app.db）。
    'EFLOW_QA_DIR' = (Join-Path $Root 'apps\eflow_Q&A')
}
# P1-6 外部模式：把代码已支持环境变量覆盖的运行时写路径一并指向外部目录。
# 便携模式不设这些键——代码默认值即旧行为，绝不改动。KNOWLEDGE_DIR 故意不设：
# 知识库是随包只读内容，仍在版本目录内（bot 的 config.js 默认即读它）。
if ($paths.Mode -eq 'external') {
    $botData = $paths.BotDataDir
    $derived['PORTAL_DEFAULT_FILE'] = Join-Path $paths.DataDir 'portal-default.json'
    $derived['TT_PYTHON'] = $PyExe
    $derived['CHAT_CA_FILE'] = Join-Path $paths.CertsDir 'liberia-noc-ca.pem'
    $derived['INDEX_PATH'] = Join-Path $botData 'index.json'
    $derived['LOOKUPS_PATH'] = Join-Path $botData 'lookups.json'
    $derived['AUTH_DIR'] = Join-Path $botData 'auth'
    $derived['QA_LOG_PATH'] = Join-Path $botData 'qa.sqlite'
    $derived['NOTIFY_AUTH_DIR'] = Join-Path $botData 'auth-notify'
    $derived['MYAPP_LOG_DIR'] = $paths.LogsDir
    # P1-7：业务代码新增的三项路径覆盖（MY_APP_DB_PATH / MW_LINKS_PUBLIC_DIR / BOT_LOG_DIR）。
    $derived['MY_APP_DB_PATH'] = Join-Path $paths.DataDir 'app.db'
    $derived['MW_LINKS_PUBLIC_DIR'] = 'off'
    $derived['BOT_LOG_DIR'] = Join-Path $paths.LogsDir 'bot'
}
# 无条件覆盖：派生值必须由副本根推导，不能被父进程残留的生产值顶掉。
foreach ($k in $derived.Keys) { Set-Cfg $k $derived[$k] }

# 安全熔断：副本必须禁用调度器，不允许被 .env 改回 0
if ((Get-Cfg 'MY_APP_DISABLE_SCHEDULER' '1') -ne '1') {
    Write-Warn2 '.env 里 MY_APP_DISABLE_SCHEDULER != 1，已强制改回 1（副本禁止连生产采集上游）'
}
$cfg['MY_APP_DISABLE_SCHEDULER'] = '1'
Set-Cfg 'MY_APP_DISABLE_SCHEDULER' '1'
Remove-Item Env:\MY_APP_TASK_QUEUE -ErrorAction SilentlyContinue
# 副本绝不接管真实 WhatsApp 会话/投递：即使 .env 误设 1 也强制关（需时另行扫码）。
$cfg['START_WA_BOT'] = '0'
Set-Cfg 'START_WA_BOT' '0'
# 清除可能让副本解释器加载源仓库模块/依赖的环境污染。
foreach ($k in @('PYTHONPATH', 'PYTHONHOME', 'NODE_PATH')) {
    Remove-Item "Env:\$k" -ErrorAction SilentlyContinue
}

# 子进程优先用副本自带解释器；PATH 剔除指向源仓库（\01_RPA\）的条目，避免误用源 venv。
$copyNodeDir = Split-Path $NodeExe -Parent
$copyPyDir = Split-Path $PyExe -Parent
$pathParts = @($env:PATH -split ';' | Where-Object { $_ -and ($_ -notmatch '\\01_RPA\\') })
$env:PATH = (@($copyNodeDir, $copyPyDir) + $pathParts) -join ';'

# backend\.env 只写本地 trust 连接串，不含任何真实口令；已存在则不覆盖（使用者可能自己改过）
# P1-6 外部模式：不写——database.py 的 load_dotenv(override=False) 保证进程环境优先，
# 本脚本已把同一批值放进进程环境；往版本目录写文件会破坏零写入。服务必须经本脚本启动。
$backendEnv = Join-Path $MyAppDir 'backend\.env'
if ($paths.Mode -eq 'external') {
    Write-Step '外部模式：跳过 backend\.env 生成（连接串已由进程环境携带）'
} elseif (-not (Test-Path -LiteralPath $backendEnv)) {
    if (-not $DryRun) {
        $utf8NoBom = New-Object System.Text.UTF8Encoding $false
        $body = "MY_APP_DB_MODE=pg`r`nDATABASE_URL=$dbUrl`r`nMY_APP_DISABLE_SCHEDULER=1`r`n"
        [System.IO.File]::WriteAllText($backendEnv, $body, $utf8NoBom)
    }
    if ($DryRun) { Write-Step "<干跑> 将生成 $backendEnv（仅本地回环连接，无口令）" }
    else { Write-Step "已生成 $backendEnv（仅本地回环连接，无口令）" }
}

# P1-6：目录规划（两种模式都打印；干跑时它是零写入核查的输入）。
Write-Host ''
Write-Host '==================== 目录规划 ====================' -ForegroundColor Cyan
Write-Host ("  模式       {0}" -f $paths.Mode)
Write-Host ("  版本目录   {0}" -f $Root)
if ($paths.Mode -eq 'external') { Write-Host ("  部署根     {0}" -f $paths.NocHome) }
Write-Host ("  配置       {0}" -f $paths.EnvFile)
Write-Host ("  数据       {0}" -f $paths.DataDir)
Write-Host ("  日志       {0}" -f $paths.LogsDir)
Write-Host ("  运行时     {0}" -f $paths.RuntimeDir)
Write-Host ("  PG 数据    {0}" -f $PgData)
Write-Host ("  nginx 配置 {0}" -f $paths.NginxConfOut)
Write-Host ''

# ------------------------------------------------------------------ 通用工具
function Test-PortListening {
    param([int]$Port, [int]$TimeoutMs = 1200)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch { return $false }
    finally { try { $client.Close(); $client.Dispose() } catch { } }
}
function Wait-Port {
    param([int]$Port, [int]$Seconds = 30)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return (Test-PortListening $Port)
}
function Quote-Arg {
    param([string]$a)
    if ($a -match '\s' -and -not $a.StartsWith('"')) { return '"' + $a + '"' }
    return $a
}
function Get-PidFile { param([string]$Name) Join-Path $TmpDir ("portable-$Name.pid") }
function Save-Pid {
    param([string]$Name, [int]$ProcessId)
    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $p) { return }
    $ticks = $p.StartTime.Ticks
    [System.IO.File]::WriteAllText((Get-PidFile $Name), "$ProcessId|$($p.ProcessName)|$ticks")
}

# 按 pid 文件判定「本副本上一轮是否已起过」，避免重复启动，也避免抢别人的端口。
# 外部占用一律抛错停止（绝不假成功、绝不抢端口、绝不杀生产进程）。
function Test-OwnPort {
    param([string]$Name, [int]$Port)
    if (-not (Test-PortListening $Port)) { return $false }
    $pf = Get-PidFile $Name
    if (Test-Path -LiteralPath $pf) {
        $parts = ([System.IO.File]::ReadAllText($pf)).Trim() -split '\|'
        if ($parts.Count -eq 3) {
            $p = Get-Process -Id ([int]$parts[0]) -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -eq $parts[1] -and $p.StartTime.Ticks -eq [long]$parts[2]) {
                $exe = try { $p.Path } catch { '' }
                # 外部模式解释器在 $RuntimeDir（版本目录外），同样算本副本。
                if ($exe -and ($exe.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) -or $exe.StartsWith($RuntimeDir, [StringComparison]::OrdinalIgnoreCase))) {
                    Write-Skip "$Name 已由本副本启动（pid=$($parts[0])），端口 $Port 不重复拉起"
                    return $true
                }
            }
        }
    }
    throw "端口 $Port 被外部进程占用（非本副本 $Name），拒绝启动以免抢占生产服务。请先释放该端口或改副本 .env。"
}

function Start-Detached {
    param(
        [string]$Name,
        [string]$Exe,
        # 不能叫 $Args：那是 PowerShell 的自动变量，作参数名会导致传入的参数被吞掉
        [string[]]$ArgList,
        [string]$WorkDir,
        [int]$Port = 0,
        [int]$WaitSeconds = 40
    )
    if ($Port -gt 0 -and (Test-OwnPort $Name $Port)) { return $true }
    if (-not (Test-Path -LiteralPath $Exe)) { Write-Err2 "$Name 缺解释器: $Exe"; return $false }
    if (-not (Test-Path -LiteralPath $WorkDir)) { Write-Err2 "$Name 缺工作目录: $WorkDir"; return $false }

    $argText = ($ArgList | ForEach-Object { Quote-Arg $_ }) -join ' '
    if ($DryRun) { Write-Step "<干跑> $Name : `"$Exe`" $argText   (cwd=$WorkDir)"; return $true }

    $out = Join-Path $TmpDir ("portable-$Name.out")
    $err = Join-Path $TmpDir ("portable-$Name.err")
    # PS 5.1 的 Start-Process 不允许 stdout/stderr 指向同一文件
    $proc = Start-Process -FilePath $Exe -ArgumentList $argText -WorkingDirectory $WorkDir `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    Save-Pid $Name $proc.Id
    if ($Port -gt 0) {
        if (Wait-Port $Port $WaitSeconds) { Write-Ok "$Name 已监听 $Port (pid=$($proc.Id))"; return $true }
        Write-Err2 "$Name 未在 ${WaitSeconds}s 内监听 $Port，日志：$out / $err"
        # 失败时把日志尾部直接回显：最常被问的就是「到底停在哪一步」。
        # 注意 stdout 可能仍是块缓冲（调用方若没加 -u），尾部只代表最后一次刷盘，
        # 不代表真实最后一步 —— 回显是为了少开一次文件，不是断言卡点。
        foreach ($logFile in @($out, $err)) {
            if (-not (Test-Path -LiteralPath $logFile)) { continue }
            $tail = @(Get-Content -LiteralPath $logFile -Tail 8 -ErrorAction SilentlyContinue)
            if ($tail.Count -eq 0) { continue }
            Write-Host ("    ---- {0} 尾部 ----" -f (Split-Path -Leaf $logFile)) -ForegroundColor DarkGray
            foreach ($line in $tail) { Write-Host ("    {0}" -f $line) -ForegroundColor DarkYellow }
        }
        return $false
    }
    Write-Ok "$Name 已启动 (pid=$($proc.Id))"
    return $true
}

# ------------------------------------------------------------- 0) 启动预检
# 先验角色标记与所有核心入口，避免起了一半才发现缺文件（部分启动会留下脏端口）。
# 2026-11-19 起两个标记互斥：PRODUCTION.flag = 这台机跑生产 80/443（本脚本的
# 非 -Production 分支会改 backend\.env、起便携 PG 55432、往 8080/8443 起第二套，
# 所以必须先拒绝）；ISOLATED_COPY.flag = 真正的便携副本。
if (Test-Path -LiteralPath (Join-Path $Root 'PRODUCTION.flag')) {
    throw @'
这是生产树（根目录有 PRODUCTION.flag），拒绝用隔离启动器启动。
生产启停走 ops\deploy\portal-ctl.ps1 -Action start|stop（桌面 olbr-*.bat 也是它），
或本脚本加 -Production（等价于委托 portal-ctl）。
继续走非 -Production 分支会改 backend\.env、起便携 PG 55432、往 8080/8443 起第二套。
'@
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'ISOLATED_COPY.flag'))) {
    throw '缺 ISOLATED_COPY.flag：本脚本只能在便携副本里运行，拒绝在主仓库启动'
}
$preflight = @(
    @{ Name = 'Python 解释器'; Path = $PyExe },
    @{ Name = 'Node 解释器'; Path = $NodeExe },
    @{ Name = 'my_app 后端入口'; Path = (Join-Path $MyAppDir 'backend\main.py') },
    @{ Name = '前端 vite'; Path = (Join-Path $FeDir 'node_modules\vite\bin\vite.js') },
    @{ Name = '便携 PG pg_ctl'; Path = (Join-Path $PgBin 'pg_ctl.exe') }
)
if ($PortalRoot) {
    $preflight += @{ Name = '门户 tsx'; Path = (Join-Path $PortalRoot 'node_modules\tsx\dist\cli.mjs') }
}
foreach ($item in $preflight) {
    if (-not (Test-Path -LiteralPath $item.Path)) {
        if ($DryRun) { Write-Warn2 "预检缺 $($item.Name) -> $($item.Path)" }
        else { throw "启动预检失败：缺 $($item.Name) -> $($item.Path)（请先补齐依赖/运行时）" }
    }
}
if (-not $DryRun) { Write-Ok '启动预检通过（隔离标志 + 核心解释器/入口齐全）' }

# ------------------------------------------------------------- 1) 便携 PG
Write-Step '--- 1/6 便携 PostgreSQL ---'
$pgCtl = Join-Path $PgBin 'pg_ctl.exe'
$psql = Join-Path $PgBin 'psql.exe'
if (-not (Test-Path -LiteralPath $pgCtl)) {
    Write-Err2 "缺 pg_ctl.exe: $PgBin（请先跑 -Stage runtimes）"
} elseif (-not $DryRun) {
    if (-not (Test-Path -LiteralPath (Join-Path $PgData 'PG_VERSION'))) {
        $initdb = Join-Path $PgBin 'initdb.exe'
        Write-Step "首次启动，initdb -> $PgData"
        & $initdb -D $PgData -U $PgUser -A trust --encoding=UTF8 2>&1 |
            ForEach-Object { Write-Host "  $_" }
        if ($LASTEXITCODE -ne 0) { throw "initdb 失败（exit=$LASTEXITCODE）" }
    }
    $pgLog = Join-Path $TmpDir 'portable-pg.log'
    & $pgCtl -D $PgData status 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        # 不能用 `& pg_ctl ... 2>&1 | ForEach-Object`：pg_ctl 拉起的 postgres.exe
        # 会继承 PowerShell 管道的写句柄，管道永远等不到 EOF，脚本会永久卡在 1/6
        # （PG 其实早已 ready）。改成输出重定向到文件 + 不等待子进程树。
        $pgCtlOut = Join-Path $TmpDir 'portable-pg_ctl.out'
        $pgCtlErr = Join-Path $TmpDir 'portable-pg_ctl.err'
        $pgCtlArg = '-D "{0}" -l "{1}" -o "-p {2} -h 127.0.0.1" start' -f $PgData, $pgLog, $PgPort
        $pgProc = Start-Process -FilePath $pgCtl -ArgumentList $pgCtlArg -PassThru `
            -WindowStyle Hidden -RedirectStandardOutput $pgCtlOut -RedirectStandardError $pgCtlErr
        if (-not $pgProc) { throw "pg_ctl start 未能启动，见 $pgCtlErr" }
    }
    if (-not (Wait-Port $PgPort 30)) { throw "便携 PG 未在 30s 内监听 $PgPort，见 $pgLog / TMP\portable-pg_ctl.err" }
    # 端口 LISTEN != 可连接：PG 恢复阶段会拒绝普通连接并回
    # "the database system is starting up"，必须轮询到 SELECT 1 真正成功。
    # 同时 PS 5.1 会把 native stderr 变成 ErrorRecord，配合 Stop 会中断整个启动器，
    # 所以这几步只是探测/建库，临时降级为 Continue。
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $pgReady = $false
        for ($i = 0; $i -lt 60; $i++) {
            $probe = & $psql -w -h 127.0.0.1 -p $PgPort -U $PgUser -d postgres -tAc 'SELECT 1' 2>$null
            if ($LASTEXITCODE -eq 0 -and "$probe".Trim() -eq '1') { $pgReady = $true; break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $pgReady) { throw "便携 PG 未在 30s 内进入可连接状态，见 $pgLog" }
        # 写入前确认连上的就是副本自己的 PG 实例：data_directory 必须等于副本 pgdata，
        # 否则可能连到本机别的 PG（含生产），拒绝任何建库/写入。
        $dataDir = "$(& $psql -w -h 127.0.0.1 -p $PgPort -U $PgUser -d postgres -tAc 'SHOW data_directory' 2>$null)".Trim()
        $expectDir = (Resolve-Path -LiteralPath $PgData).Path
        $ddNorm = ($dataDir -replace '/', '\').TrimEnd('\').ToLowerInvariant()
        $expNorm = ($expectDir -replace '/', '\').TrimEnd('\').ToLowerInvariant()
        if ($ddNorm -ne $expNorm) {
            throw "便携 PG data_directory 不符（实际 '$dataDir'，期望 '$expectDir'）：可能连到别的实例，拒绝写入"
        }
        $dbExists = & $psql -w -h 127.0.0.1 -p $PgPort -U $PgUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$PgDb'" 2>$null
        if (-not $dbExists) {
            $createdb = Join-Path $PgBin 'createdb.exe'
            & $createdb -h 127.0.0.1 -p $PgPort -U $PgUser $PgDb 2>&1 | ForEach-Object { Write-Host "  $_" }
        }
        # P1-1：首次启动建 schema。alembic 迁移不带 schema 前缀，依赖 search_path 首位为 app；
        # 空库默认 search_path 只有 public，表会落错位置。两条语句均幂等，每次启动都跑，
        # 兼修复此前已建好的库（已有表不搬家，见 docs\workspace\zip-p1-1-clean-pg-record.md）。
        # 注意：此处 $ErrorActionPreference 为 Continue，失败不会自动抛，必须查 $LASTEXITCODE。
        $schemaOut = & $psql -w -h 127.0.0.1 -p $PgPort -U $PgUser -d $PgDb -tAc 'CREATE SCHEMA IF NOT EXISTS app' 2>&1
        if ($LASTEXITCODE -ne 0) { throw "创建 app schema 失败：$schemaOut" }
        $searchOut = & $psql -w -h 127.0.0.1 -p $PgPort -U $PgUser -d $PgDb -tAc "ALTER DATABASE $PgDb SET search_path TO app, public" 2>&1
        if ($LASTEXITCODE -ne 0) { throw "设置数据库 search_path 失败：$searchOut" }
    } finally { $ErrorActionPreference = $prevEap }
    Write-Ok "便携 PG 就绪 127.0.0.1:$PgPort / $PgDb"
    Write-Step '若数据库为空，请另跑 ops\deploy\bundle_data.ps1 灌入快照'
} else {
    Write-Step "<干跑> 跳过 initdb / pg_ctl start（$PgData）"
}

# ------------------------------------------- 数据库迁移（alembic upgrade head，D1）
# 绿包首次启动时库是空的：此前没有任何脚本跑 alembic，后端起来后表不存在。
# upgrade head 幂等（已是最新时为空操作），每次启动都跑；失败直接抛错，
# 不让后端带着未迁移的库启动。MY_APP_DB_MODE/DATABASE_URL 已由派生变量放进
# 进程环境（见上），alembic env.py 直接读它们，与后端同一套连接串。
Write-Step '--- 数据库迁移（alembic upgrade head） ---'
if ($DryRun) {
    Write-Step '<干跑> 跳过 alembic upgrade head'
} else {
    $alembicOut = Join-Path $TmpDir 'portable-alembic.out'
    $alembicErr = Join-Path $TmpDir 'portable-alembic.err'
    # 必须用 Start-Process 直接重定向文件，不能写 `& $PyExe ... > out 2> err`：
    # alembic 的 INFO 日志按设计写到 stderr，而本脚本 $ErrorActionPreference = 'Stop'，
    # Windows PowerShell 5.1 会把 native 命令的 stderr 包成 NativeCommandError 当终止
    # 错误抛出 —— 迁移成功也会在第一条 INFO 处炸掉，且两个重定向文件留 0 字节、真因
    # 只剩 start_all.err 里那一行（2026-11-19 冒烟 PR #151 实测，两文件皆 0 字节）。
    $alembicProc = Start-Process -FilePath $PyExe -ArgumentList @('-m', 'alembic', 'upgrade', 'head') `
        -WorkingDirectory $MyAppDir -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $alembicOut -RedirectStandardError $alembicErr
    if ($alembicProc.ExitCode -ne 0) {
        $tail = ''
        if (Test-Path -LiteralPath $alembicErr) { $tail = (Get-Content -LiteralPath $alembicErr -Tail 10) -join "`n" }
        throw "alembic upgrade head 失败（退出码 $($alembicProc.ExitCode)，见 $alembicErr）：$tail"
    }
    # alembic 正常日志在 stderr（INFO 行），一并回显，下次失败不用再翻文件。
    foreach ($alembicLog in @($alembicOut, $alembicErr)) {
        Get-Content -LiteralPath $alembicLog -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" }
    }
    Write-Ok 'alembic upgrade head 完成'
}

# ------------------------------------------------- 2) nginx 配置（含 -t 校验）
Write-Step '--- 2/6 nginx 配置展开 ---'
$startNginx = (Get-Cfg 'START_NGINX' '1') -eq '1'
$confScript = Join-Path $Root 'ops\deploy\write_nginx_conf.ps1'
if ($startNginx -and -not $SkipNginxConf) {
    if (-not $DryRun) {
        if (-not (Test-Path -LiteralPath $confScript)) { throw "缺 $confScript（请先跑 -Stage config）" }
        & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
            -NoProfile -ExecutionPolicy Bypass -File $confScript `
            -EnvFile $envFile -NginxDir $NginxDir -ConfOut $paths.NginxConfOut `
            -LogsDir $paths.NginxLogsDir -TempDir $paths.NginxTempDir -CertsDir $paths.CertsDir
        if ($LASTEXITCODE -ne 0) { throw 'write_nginx_conf.ps1 失败，未继续启动' }
    } else { Write-Step '<干跑> 跳过 write_nginx_conf.ps1' }
}

# ------------------------------------------------------------- 3) my_app 后端
Write-Step '--- 3/6 my_app 后端 ---'
# -u（等价 PYTHONUNBUFFERED=1）：Start-Detached 把 stdout 重定向到文件，Python 默认
# 块缓冲，失败时 TMP\portable-backend.out 只到最后一个 8KB 刷盘点，看着像卡在
# 某一步（2026-11-21 PR #199/#201 两次冒烟都是这样，最后一行都停在 TT 预热）。
# 等待上限 90s：backend.main 导入热态实测 2.2s，冷缓存下会明显变慢；2026-11-21 两次
# 都在 45s 上限处失败，90s 留出冷启动余量，真实挂死仍会在 90s 报错。
$ok = Start-Detached -Name 'backend' -Exe $PyExe `
    -ArgList @('-u', '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', [string]$BePort) `
    -WorkDir $MyAppDir -Port $BePort -WaitSeconds 90
if (-not $ok) { throw 'my_app 后端启动失败（见 TMP\portable-backend.err / .out）' }

# ------------------------------------------------------------- 4) my_app 前端
Write-Step '--- 4/6 my_app 前端（vite preview） ---'
$viteCli = Join-Path $FeDir 'node_modules\vite\bin\vite.js'
$ok = Start-Detached -Name 'frontend' -Exe $NodeExe `
    -ArgList @($viteCli, 'preview', '--host', '127.0.0.1', '--port', [string]$FePort, '--strictPort') `
    -WorkDir $FeDir -Port $FePort -WaitSeconds 30
if (-not $ok) { throw 'my_app 前端启动失败（见 TMP\portable-frontend.err / .out）' }

# ----------------------------------------------------------------- 5) 门户
Write-Step '--- 5/6 统一门户 + 可选服务 ---'
if ($PortalRoot) {
    $tsxCli = Join-Path $PortalRoot 'node_modules\tsx\dist\cli.mjs'
    $ok = Start-Detached -Name 'portal' -Exe $NodeExe `
        -ArgList @($tsxCli, 'server/index.ts') -WorkDir $PortalRoot -Port $PortalPort -WaitSeconds 30
    if (-not $ok) { throw '统一门户启动失败（见 TMP\portable-portal.err / .out）' }
} else {
    Write-Warn2 '未找到门户目录（apps\portal），门户步骤跳过'
}

if ((Get-Cfg 'START_WA_ANALYSIS' '0') -eq '1') {
    $waDir = Join-Path $Root 'apps\portal\scripts\whatsapp_analysis'
    $waCli = Join-Path $waDir 'node_modules\tsx\dist\cli.mjs'
    if (Test-Path -LiteralPath $waCli) {
        # 分析服务读 PORT；临时改成自己的端口，避免继承门户 PORT 而撞端口。
        $prevPort = [Environment]::GetEnvironmentVariable('PORT', 'Process')
        Set-Cfg 'PORT' ([string]$WaAnalysisPort)
        $ok = Start-Detached -Name 'wa_analysis' -Exe $NodeExe `
            -ArgList @($waCli, 'server/index.ts') -WorkDir $waDir -Port $WaAnalysisPort -WaitSeconds 25
        if ($prevPort) { Set-Cfg 'PORT' $prevPort } else { Remove-Item Env:\PORT -ErrorAction SilentlyContinue }
        if (-not $ok) { throw 'WhatsApp 分析服务启动失败（见 TMP\portable-wa_analysis.err）' }
    } else { Write-Warn2 '分析服务依赖缺失，跳过' }
}

if ((Get-Cfg 'START_WA_BOT' '0') -eq '1') {
    Write-Warn2 'START_WA_BOT=1：会使用副本里的 WhatsApp 登录态接管会话，务必确认已在目标机重新扫码'
    $null = Start-Detached -Name 'wa_bot' -Exe $NodeExe `
        -ArgList @('--env-file-if-exists=.env', 'src/index.js') -WorkDir $BotDir -WaitSeconds 0
}

# 问答助手 9269 不在这里启动：由 my_app 后端 portal_qa_ctl.py 在首次提问时按需
# DETACHED 拉起（PORTAL_QA_NODE 已指向副本自带 node），这里只需把环境变量备好。

# ----------------------------------------------------------------- 6) nginx
Write-Step '--- 6/6 nginx ---'
if ($startNginx) {
    if (Test-OwnPort 'nginx' $HttpPort) { }
    elseif ($DryRun) { Write-Step '<干跑> 跳过 nginx 启动' }
    elseif (-not (Test-Path -LiteralPath $NginxExe)) { Write-Err2 "缺 $NginxExe" }
    else {
        # Windows nginx 自带 daemon 化，-p 指到 nginx 安装目录才能正确解析相对路径；
        # 配置文件用 -c 绝对路径（外部模式在 config/ 下）。
        # 同样不能用管道捕获输出：master/worker 常驻并继承管道句柄会把启动器挂死。
        $nginxOut = Join-Path $TmpDir 'portable-nginx.out'
        $nginxErr = Join-Path $TmpDir 'portable-nginx.err'
        # -c 用绝对路径（正斜杠）：外部模式配置在 config/ 下；便携模式与旧的
        # conf/nginx.conf 是同一文件。-p 仍指 nginx 安装目录（mime.types 相对它解析）。
        $nginxConfArg = $paths.NginxConfOut.Replace('\', '/')
        $nginxArg = '-p "{0}" -c "{1}"' -f $NginxDir.TrimEnd('\'), $nginxConfArg
        $nginxProc = Start-Process -FilePath $NginxExe -ArgumentList $nginxArg -PassThru `
            -WindowStyle Hidden -RedirectStandardOutput $nginxOut -RedirectStandardError $nginxErr
        if (-not $nginxProc) { Write-Err2 "nginx 未能启动，见 $nginxErr" }
        $nginxPidFile = Join-Path $paths.NginxLogsDir 'nginx.pid'
        if (Wait-Port $HttpPort 15) {
            if (Test-Path -LiteralPath $nginxPidFile) {
                $npid = ([System.IO.File]::ReadAllText($nginxPidFile)).Trim()
                if ($npid -match '^\d+$') { Save-Pid 'nginx' ([int]$npid) }
            }
            Write-Ok "nginx 已监听 $HttpPort"
        } else { Write-Err2 ("nginx 未在 15s 内监听 {0}，见 {1}\error.log" -f $HttpPort, $paths.NginxLogsDir) }
    }
} else { Write-Skip 'START_NGINX=0，不启动 nginx' }

# ------------------------------------------------------------------ 状态汇总
Write-Host ''
Write-Host '==================== 副本服务状态 ====================' -ForegroundColor Cyan
$ports = [ordered]@{
    'nginx' = $HttpPort; 'my_app 前端' = $FePort; 'my_app 后端' = $BePort
    '统一门户' = $PortalPort; '便携 PG' = $PgPort
}
foreach ($k in $ports.Keys) {
    $up = Test-PortListening ([int]$ports[$k])
    Write-Host ('  {0,-14} {1,-6} {2}' -f $k, $ports[$k], $(if ($up) { 'LISTENING' } else { 'not listening' })) `
        -ForegroundColor $(if ($up) { 'Green' } else { 'Yellow' })
}
Write-Host ''
$httpsBase = if ($HttpsPort -eq 443) { 'https://localhost' } else { "https://localhost:$HttpsPort" }
$httpBase = if ($HttpPort -eq 80) { 'http://localhost' } else { "http://localhost:$HttpPort" }
Write-Host '  入口（HTTPS 自签证书，浏览器告警属正常；HTTP 同样可用）：' -ForegroundColor White
Write-Host ("    {0}/                 根导航" -f $httpsBase)
Write-Host ("    {0}/myapp/           Data Insight MVP" -f $httpsBase)
Write-Host ("    {0}/uniplam/         统一门户" -f $httpsBase)
Write-Host ("    {0}/v3/olbrportal/  问答助手挂件" -f $httpsBase)
Write-Host ("    {0}/                 HTTP 备用" -f $httpBase)
Write-Host ''
Write-Host '  停止：双击副本根目录 stop.bat（只停本副本进程）' -ForegroundColor DarkGray
$sw.Stop()
Write-Ok ("start_all 完成，耗时 {0:N1} 秒{1}" -f $sw.Elapsed.TotalSeconds, $(if ($DryRun) { '（干跑）' } else { '' }))
