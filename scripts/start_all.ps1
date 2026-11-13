<#
  start_all.ps1 —— 离线副本一键启动（由 build_olbr_portable.ps1 -Stage config 铺到 <副本根>\scripts\）
  =============================================================================
  入口：副本根目录的 start.bat（薄壳，只调本脚本）。
  真相源：副本根 .env（模板 scripts\env_template.txt）；端口与盘符都不写死。

  与生产启动脚本的区别（重要，别照抄 my_portal_ctl.ps1）：
    · 生产用 start_hidden.ps1 拉 backend_watchdog + worker 组 + 开机自启任务；
      副本**全部不用** —— MY_APP_DISABLE_SCHEDULER=1 且不设 MY_APP_TASK_QUEUE，
      否则采集任务会连生产上游、并可能重复投递 WhatsApp/邮件告警。
    · 副本直接起 uvicorn / vite preview / tsx server，进程由本脚本用 pid 文件托管；
      stop_all.ps1 只按 pid 文件停，绝不按端口盲杀（源机可能正在跑生产）。
    · 解释器一律用副本自带的 runtime\python312 / runtime\node，路径由 $Root 推出。

  PowerShell 5.1 兼容：不用 ::new()、三元、-Parallel、$IsWindows。
  用法：
    powershell -NoProfile -File scripts\start_all.ps1
    powershell -NoProfile -File scripts\start_all.ps1 -DryRun
  =============================================================================
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$SkipNginxConf
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

# 本脚本位于 <Root>\scripts\，故根 = 上一级
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$TmpDir = Join-Path $Root 'TMP'
$RuntimeDir = Join-Path $Root 'runtime'
$PyExe = Join-Path $RuntimeDir 'python312\python.exe'
$NodeExe = Join-Path $RuntimeDir 'node\node.exe'
$PgBin = Join-Path $RuntimeDir 'postgresql16\bin'
$NginxDir = Join-Path $Root 'nginx-1.28.0'
$NginxExe = Join-Path $NginxDir 'nginx.exe'
$MyAppDir = Join-Path $Root 'apps\my_app'
$FeDir = Join-Path $MyAppDir 'frontend'
$BotDir = Join-Path $Root 'apps\whatsapp-kb-bot'
$PortalRoot = Join-Path $Root 'apps\portal'
if (-not (Test-Path -LiteralPath $PortalRoot)) { $PortalRoot = $null }

# 防误跑：本脚本只应在 build 出来的副本里执行。主仓库同样有 apps\my_app，
# 但没有 runtime\node\node.exe —— 少了这道检查，误在主仓库跑会往源码树写 .env。
if (-not (Test-Path -LiteralPath $NodeExe) -or
    -not (Test-Path -LiteralPath (Join-Path $MyAppDir 'backend\main.py'))) {
    throw "当前目录不是有效副本（缺 runtime\node\node.exe 或 apps\my_app\backend\main.py）：$Root`n本脚本只能在 build_olbr_portable.ps1 生成的副本里运行。"
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

$envFile = Join-Path $Root '.env'
$envTpl = Join-Path $PSScriptRoot 'env_template.txt'
if (-not (Test-Path -LiteralPath $envFile)) {
    if (-not (Test-Path -LiteralPath $envTpl)) { throw "缺少 .env 模板: $envTpl" }
    if (-not $DryRun) { Copy-Item -LiteralPath $envTpl -Destination $envFile -Force }
    Write-Step "首次启动：已从模板生成 $envFile"
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
$PgData = Join-Path $RuntimeDir 'pgdata'
$FeDist = (Join-Path $Root 'apps\_fe_dist').Replace('\', '/')
$AuthDir = Join-Path $RuntimeDir 'portal-auth-data'
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
$backendEnv = Join-Path $MyAppDir 'backend\.env'
if (-not (Test-Path -LiteralPath $backendEnv)) {
    if (-not $DryRun) {
        $utf8NoBom = New-Object System.Text.UTF8Encoding $false
        $body = "MY_APP_DB_MODE=pg`r`nDATABASE_URL=$dbUrl`r`nMY_APP_DISABLE_SCHEDULER=1`r`n"
        [System.IO.File]::WriteAllText($backendEnv, $body, $utf8NoBom)
    }
    if ($DryRun) { Write-Step "<干跑> 将生成 $backendEnv（仅本地回环连接，无口令）" }
    else { Write-Step "已生成 $backendEnv（仅本地回环连接，无口令）" }
}

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
                if ($exe -and $exe.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase)) {
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
        return $false
    }
    Write-Ok "$Name 已启动 (pid=$($proc.Id))"
    return $true
}

# ------------------------------------------------------------- 0) 启动预检
# 先验隔离标志与所有核心入口，避免起了一半才发现缺文件（部分启动会留下脏端口）。
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
    } finally { $ErrorActionPreference = $prevEap }
    Write-Ok "便携 PG 就绪 127.0.0.1:$PgPort / $PgDb"
    Write-Step '若数据库为空，请另跑 scripts\bundle_data.ps1 灌入快照'
} else {
    Write-Step "<干跑> 跳过 initdb / pg_ctl start（$PgData）"
}

# ------------------------------------------------- 2) nginx 配置（含 -t 校验）
Write-Step '--- 2/6 nginx 配置展开 ---'
$startNginx = (Get-Cfg 'START_NGINX' '1') -eq '1'
$confScript = Join-Path $Root 'write_nginx_conf.ps1'
if ($startNginx -and -not $SkipNginxConf) {
    if (-not $DryRun) {
        if (-not (Test-Path -LiteralPath $confScript)) { throw "缺 $confScript（请先跑 -Stage config）" }
        & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
            -NoProfile -ExecutionPolicy Bypass -File $confScript
        if ($LASTEXITCODE -ne 0) { throw 'write_nginx_conf.ps1 失败，未继续启动' }
    } else { Write-Step '<干跑> 跳过 write_nginx_conf.ps1' }
}

# ------------------------------------------------------------- 3) my_app 后端
Write-Step '--- 3/6 my_app 后端 ---'
$ok = Start-Detached -Name 'backend' -Exe $PyExe `
    -ArgList @('-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', [string]$BePort) `
    -WorkDir $MyAppDir -Port $BePort -WaitSeconds 45
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
        # Windows nginx 自带 daemon 化，-p 指到副本自己的目录才能正确解析 logs/conf。
        # 同样不能用管道捕获输出：master/worker 常驻并继承管道句柄会把启动器挂死。
        $nginxOut = Join-Path $TmpDir 'portable-nginx.out'
        $nginxErr = Join-Path $TmpDir 'portable-nginx.err'
        $nginxArg = '-p "{0}" -c "conf/nginx.conf"' -f $NginxDir.TrimEnd('\')
        $nginxProc = Start-Process -FilePath $NginxExe -ArgumentList $nginxArg -PassThru `
            -WindowStyle Hidden -RedirectStandardOutput $nginxOut -RedirectStandardError $nginxErr
        if (-not $nginxProc) { Write-Err2 "nginx 未能启动，见 $nginxErr" }
        $nginxPidFile = Join-Path $NginxDir 'logs\nginx.pid'
        if (Wait-Port $HttpPort 15) {
            if (Test-Path -LiteralPath $nginxPidFile) {
                $npid = ([System.IO.File]::ReadAllText($nginxPidFile)).Trim()
                if ($npid -match '^\d+$') { Save-Pid 'nginx' ([int]$npid) }
            }
            Write-Ok "nginx 已监听 $HttpPort"
        } else { Write-Err2 "nginx 未在 15s 内监听 $HttpPort，见 $NginxDir\logs\error.log" }
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
