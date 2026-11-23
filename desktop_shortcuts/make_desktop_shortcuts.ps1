<#
  make_desktop_shortcuts.ps1 —— 生成 OLBR 的桌面快捷方式（总控 + 单组件）
  ==========================================================================
  2026-11-18 重写：
    · 根目录不再写死，按本脚本位置推导（desktop_shortcuts\ -> 仓库根）；
    · 不再依赖 my_portal_ctl.ps1（那是 01_RPA 时代的 12 键控制器）；
    · 三个总控直接调 ops\deploy\olbr-*.bat（内部走 portal-ctl.ps1）；
    · 六个单组件调各子系统自己的 start/stop（与以前一样：my_app / 门户 / nginx 分开起停）。
  2026-11-23 补齐（对照 01_RPA 时代的 12 键 my_portal）：
    · 补回 nginx-Reload（老 3 号）：调 nginx-1.28.0\reload_nginx.bat，先 nginx -t，通过才 reload；
    · 补回 WhatsApp bot 起停（老 10/11 号）+ 只读状态：调 ops\deploy\olbr-bot.bat
      （bot 不占端口，11-18 重写时漏掉了）；
    · 修备注乱码：WScript.Shell 写 Description 会按 ANSI 代码页（本机 1252）转码，
      中文全部变成 ?；现在备注改由 Shell.Application（Unicode）写入，并回读校验。

  用法：
    powershell -NoProfile -File desktop_shortcuts\make_desktop_shortcuts.ps1              # 只生成到 TMP\shortcuts
    powershell -NoProfile -File desktop_shortcuts\make_desktop_shortcuts.ps1 -WriteDesktop  # 写到桌面
#>
param(
    [switch]$WriteDesktop
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Deploy = Join-Path $Root 'ops\deploy'
$Desktop = [Environment]::GetFolderPath('Desktop')

foreach ($need in 'ops\deploy\portal-ctl.ps1', 'ops\deploy\olbr-bot.bat', 'apps\my_app\start_hidden.ps1', 'apps\portal\start_hidden.ps1', 'apps\whatsapp-kb-bot\autostart.ps1', 'nginx-1.28.0\nginx.exe', 'nginx-1.28.0\reload_nginx.bat') {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $need))) { throw "根目录不像完整部署（缺 $need）：$Root" }
}

$OutDir = if ($WriteDesktop) { Join-Path $Desktop 'olbr_app' } else { Join-Path $Root 'TMP\shortcuts' }
if (-not (Test-Path -LiteralPath $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$ws = New-Object -ComObject WScript.Shell
$shellApp = New-Object -ComObject Shell.Application
$cmd = Join-Path $env:SystemRoot 'System32\cmd.exe'
$ps = Join-Path $env:SystemRoot 'System32\powershell.exe'
$icon = Join-Path $env:SystemRoot 'System32\shell32.dll'

# 备注（Description）不能交给 WScript.Shell：它按 ANSI 代码页（本机 Windows-1252）转码，
# 中文会无声地变成 ?（2026-11-23 实测：TMP\shortcuts 里 9 个快捷方式的备注全是 ??）。
# Shell.Application 的 ShellLinkObject 走 Unicode 接口，备注由它写，写完回读校验。
function Set-LnkDescription {
    param([string]$Path, [string]$Text)
    $leaf = Split-Path -Leaf $Path
    try {
        $dir = Split-Path -Parent $Path
        $link = $shellApp.Namespace($dir).ParseName($leaf).GetLink
        $link.Description = $Text
        $link.Save()
        $back = $shellApp.Namespace($dir).ParseName($leaf).GetLink.Description
        if ($back -ne $Text) {
            Write-Host ("  [WARN] {0} 的备注回读不一致：'{1}'" -f $leaf, $back) -ForegroundColor Yellow
        }
    } catch {
        Write-Host ("  [WARN] {0} 写备注失败：{1}" -f $leaf, $_.Exception.Message) -ForegroundColor Yellow
    }
}

# 中文文件名的 .lnk 在这台机器上用 WScript.Shell 直接 Save 会失败（ANSI 代码页），
# 所以统一先用 ASCII 名生成，再用文件系统 Move-Item 改成中文名。
function New-Lnk {
    param(
        [string]$AsciiName,
        [string]$Name,
        [string]$Target,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [int]$IconIndex,
        [string]$Description
    )
    $tmp = Join-Path $OutDir $AsciiName
    $lnk = $ws.CreateShortcut($tmp)
    $lnk.TargetPath = $Target
    $lnk.Arguments = $Arguments
    $lnk.WorkingDirectory = $WorkingDirectory
    $lnk.IconLocation = "$icon,$IconIndex"
    $lnk.Description = ''   # 真正的备注在改名之后由 Set-LnkDescription 写（见上）
    $lnk.Save()
    $final = Join-Path $OutDir $Name
    # 组件类快捷方式的 ASCII 名与最终名相同（本来就是 ASCII），此时不能再「先删后移」，
    # 否则会把刚生成的 .lnk 删掉（2026-11-18 实测）。
    if ($final -ne $tmp) {
        if (Test-Path -LiteralPath $final) { Remove-Item -LiteralPath $final -Force }
        Move-Item -LiteralPath $tmp -Destination $final -Force
    }
    Set-LnkDescription -Path $final -Text $Description
    Write-Host ("  {0}" -f $Name) -ForegroundColor DarkGray
}

Write-Host ("生成到：{0}" -f $OutDir) -ForegroundColor Cyan

# ---------- 三个总控（桌面根目录也放一份） ----------
$controls = @(
    @{ ascii = 'OLBR-start.lnk';  name = 'OLBR 启动.lnk'; bat = 'olbr-start.bat';  icon = 137; desc = '启动 OLBR 生产栈（my_app + 门户 + WhatsApp bot + nginx）' },
    @{ ascii = 'OLBR-stop.lnk';   name = 'OLBR 停止.lnk'; bat = 'olbr-stop.bat';   icon = 27;  desc = '停止 OLBR 生产栈（含 WhatsApp bot；二次确认）' },
    @{ ascii = 'OLBR-status.lnk'; name = 'OLBR 状态.lnk'; bat = 'olbr-status.bat'; icon = 221; desc = '查看 OLBR 生产栈状态' }
)
foreach ($c in $controls) {
    New-Lnk -AsciiName $c.ascii -Name $c.name -Target $cmd `
        -Arguments ('/c ""{0}""' -f (Join-Path $Deploy $c.bat)) -WorkingDirectory $Root `
        -IconIndex $c.icon -Description $c.desc
    if ($WriteDesktop) {
        Copy-Item -LiteralPath (Join-Path $OutDir $c.name) -Destination (Join-Path $Desktop $c.name) -Force
    }
}

# ---------- 十个单组件（与以前一样：能各自起停；含 nginx 重载与 WhatsApp bot） ----------
$components = @(
    @{ ascii = 'my_app-Start.lnk'; name = 'my_app-Start.lnk'; target = $ps; args = ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -Force' -f (Join-Path $Root 'apps\my_app\start_hidden.ps1')); wd = (Join-Path $Root 'apps\my_app'); icon = 137; desc = '启动 my_app（后端 + workers + 前端；PG 不动）' },
    @{ ascii = 'my_app-Stop.lnk';  name = 'my_app-Stop.lnk';  target = $cmd; args = ('/c ""{0}""' -f (Join-Path $Root 'apps\my_app\stop.bat')); wd = (Join-Path $Root 'apps\my_app'); icon = 27; desc = '停止 my_app（保留 PostgreSQL）' },
    @{ ascii = 'Portal-Start.lnk'; name = 'Portal-Start.lnk'; target = $cmd; args = ('/c ""{0}""' -f (Join-Path $Root 'apps\portal\start.bat')); wd = (Join-Path $Root 'apps\portal'); icon = 137; desc = '启动门户 + WhatsApp 分析（9267/9268）' },
    @{ ascii = 'Portal-Stop.lnk';  name = 'Portal-Stop.lnk';  target = $cmd; args = ('/c ""{0}""' -f (Join-Path $Root 'apps\portal\stop.bat')); wd = (Join-Path $Root 'apps\portal'); icon = 27; desc = '停止门户 + WhatsApp 分析' },
    @{ ascii = 'nginx-Start.lnk';  name = 'nginx-Start.lnk';  target = (Join-Path $Root 'nginx-1.28.0\nginx.exe'); args = ''; wd = (Join-Path $Root 'nginx-1.28.0'); icon = 137; desc = '启动 nginx（80/443）' },
    @{ ascii = 'nginx-Stop.lnk';   name = 'nginx-Stop.lnk';   target = (Join-Path $Root 'nginx-1.28.0\nginx.exe'); args = '-s quit'; wd = (Join-Path $Root 'nginx-1.28.0'); icon = 27; desc = '停止 nginx（在 nginx 目录内发信号）' },
    @{ ascii = 'nginx-Reload.lnk'; name = 'nginx-Reload.lnk'; target = $cmd; args = ('/c ""{0}""' -f (Join-Path $Root 'nginx-1.28.0\reload_nginx.bat')); wd = (Join-Path $Root 'nginx-1.28.0'); icon = 238; desc = '重载 nginx 配置（先 nginx -t 校验，通过才 reload）' },
    @{ ascii = 'WhatsAppBot-Start.lnk';  name = 'WhatsAppBot-Start.lnk';  target = $cmd; args = ('/c ""{0}" start"' -f (Join-Path $Deploy 'olbr-bot.bat'));  wd = (Join-Path $Root 'apps\whatsapp-kb-bot'); icon = 137; desc = '启动 WhatsApp 知识库 bot + 网页问答助手（9269）' },
    @{ ascii = 'WhatsAppBot-Stop.lnk';   name = 'WhatsAppBot-Stop.lnk';   target = $cmd; args = ('/c ""{0}" stop"' -f (Join-Path $Deploy 'olbr-bot.bat'));   wd = (Join-Path $Root 'apps\whatsapp-kb-bot'); icon = 27;  desc = '停止 WhatsApp 知识库 bot + 网页问答助手（二次确认；P1 告警推送不动）' },
    @{ ascii = 'WhatsAppBot-Status.lnk'; name = 'WhatsAppBot-Status.lnk'; target = $cmd; args = ('/c ""{0}" status"' -f (Join-Path $Deploy 'olbr-bot.bat')); wd = (Join-Path $Root 'apps\whatsapp-kb-bot'); icon = 221; desc = '查看 WhatsApp bot 心跳与 9269 状态（只读）' }
)
foreach ($c in $components) {
    New-Lnk -AsciiName $c.ascii -Name $c.name -Target $c.target -Arguments $c.args `
        -WorkingDirectory $c.wd -IconIndex $c.icon -Description $c.desc
}

Write-Host ''
Write-Host ("完成：{0} 个快捷方式 -> {1}" -f ($controls.Count + $components.Count), $OutDir) -ForegroundColor Green
if (-not $WriteDesktop) {
    Write-Host '（未加 -WriteDesktop，只写进了 TMP\shortcuts；要放桌面请加该开关）' -ForegroundColor DarkGray
}
