<#
  sync-derived-data.ps1 —— 切换前把「上一版生产」的本地派生数据补到新树
  ============================================================================
  为什么需要它（2026-11-18 切生产实测事故）：
    切换脚本只搬了门户状态与 backend\.env，**没有搬 apps\my_app\data\**。
    02 的 data\ 是 11-16 的隔离副本快照：
      · 原版 CXO 看板停在 11-16 08:41；
      · data\native_board_mirror\db_sqlite3.sqlite3 还是 01 在 11-16 标成
        .corrupt 的损坏件，18 个原生看板自 11-11 起没有更新；
      · 任务报告页的「eFlow TT 规则检查」直接 404（矫正表在
        backend\services\eflow_rules\，不在 data\ 下，更没被搬到）。
    当天是手工按 mtime 补了 66 个文件 + 替换损坏镜像才恢复的。本脚本把这件事固化。

  做什么：
    1. 按 mtime 补 data\ 下 01 比 02 新的文件（只补旧，不覆盖新的）；
    2. 补 data\ 之外、由定时任务生成的功能性文件（白名单见 $ArtifactPaths）；
    3. 对 data\native_board_mirror\*.sqlite3 跑 PRAGMA quick_check，失败即非零退出。

  用法（默认干跑，只打印要复制什么）：
    powershell -NoProfile -File ops\deploy\cutover\sync-derived-data.ps1
    powershell -NoProfile -File ops\deploy\cutover\sync-derived-data.ps1 -Apply
    # 也可指定任意两棵树（用于演练/自测）
    ... -SourceRoot D:\tmp\a -TargetRoot D:\tmp\b -Apply

  幂等：重复运行只会补仍然更旧/缺失的文件；不会删除目标侧任何文件。
#>
[CmdletBinding()]
param(
    [string]$SourceRoot = 'D:\01_olbr_portal\Task\my_app',
    [string]$TargetRoot = 'D:\02_olbr_portal\apps\my_app',
    [switch]$Apply,
    [switch]$SkipQuickCheck
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Ok { param([string]$m) Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn2 { param([string]$m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Bad { param([string]$m) Write-Host "[FAIL] $m" -ForegroundColor Red }

if (-not (Test-Path -LiteralPath $SourceRoot)) { throw "源目录不存在：$SourceRoot" }
if (-not (Test-Path -LiteralPath $TargetRoot)) { throw "目标目录不存在：$TargetRoot" }
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$TargetRoot = (Resolve-Path -LiteralPath $TargetRoot).Path
Write-Host ("模式：{0}" -f $(if ($Apply) { '实补（会写目标树）' } else { '干跑（只打印）' })) -ForegroundColor Yellow
Write-Host ("源：{0}" -f $SourceRoot) -ForegroundColor DarkGray
Write-Host ("目标：{0}" -f $TargetRoot) -ForegroundColor DarkGray

# data\ 下不参与同步的目录/文件：
#   portal_profiles\_tmp_*  Chrome 临时 profile（进程私有、可能被占用）
#   native_board_cache      可重建的磁盘缓存（不是权威数据）
$ExcludeDirs = @('__pycache__', '_tmp_', 'native_board_cache')
$ExcludeFiles = @('*.wal', '*.shm', '*.log', '*.tmp', '*.pid', '*.bak', '*.bak_*', '*_bak_*', '*.corrupt*', '*.rpltmp')

# data\ 之外、但由定时任务或管理员操作生成的**功能性**文件（不搬就会直接坏页面/丢配置）。
# 只列「任务产物 / 运行配置」，不列源码（源码走 git）、不列归档报告（outputs\）。
#
# ⚠️ 2026-11-19 补：`backend\data\*.json` 是**用户可见的运行时状态**，之前漏在这里。
#    切换后 v3 门户的看板目录为空 → 回落 MsPortal 内置 data.js：
#      · Domain Dashboard 的 CXO View 从 5 条掉到 3 条；
#      · OSS Portal Login（Friends Link）从 11 个链接掉到 1 个；
#      · eflow_qa 页的工具顺序/隐藏项回到出厂值。
#    这些文件 my_app 每次请求现读、复制即生效（无需重启），必须跟 data\ 一起搬。
$ArtifactPaths = @(
    'backend\data\portal_catalog.json',
    'backend\data\settings.json',
    'backend\data\ui_tool_order.json',
    'backend\data\ui_tool_visibility.json',
    'backend\data\ui_menu_layout.json',
    'backend\services\eflow_rules\*.xlsx',
    'backend\services\eflow_rules\*_latest.json',
    'backend\services\eflow_rules\tt_proivity_skill.json',
    'backend\services\eflow_rules\push_rules_snapshots\*.json',
    'backend\services\cn_kpl\KPI Template.xlsx',
    'backend\services\ai_model_config.json'
)

$copied = New-Object System.Collections.Generic.List[string]
$skipped = 0
$failed = New-Object System.Collections.Generic.List[string]

function Copy-IfNewer {
    param([string]$Src, [string]$Dst)
    $s = Get-Item -LiteralPath $Src
    if (Test-Path -LiteralPath $Dst) {
        $d = Get-Item -LiteralPath $Dst
        if ($s.LastWriteTimeUtc -le $d.LastWriteTimeUtc) { $script:skipped++; return }
    }
    $rel = $Dst.Substring($TargetRoot.Length).TrimStart('\')
    if (-not $Apply) { $script:copied.Add($rel); return }
    try {
        $dir = Split-Path -Parent $Dst
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Copy-Item -LiteralPath $Src -Destination $Dst -Force
        # Copy-Item 在部分 Windows 组合上不保留源 mtime，显式对齐（对「只补旧」判定是前提）
        (Get-Item -LiteralPath $Dst).LastWriteTimeUtc = $s.LastWriteTimeUtc
        $script:copied.Add($rel)
    } catch {
        $script:failed.Add(("{0} :: {1}" -f $rel, $_.Exception.Message))
    }
}

# ---------- 1. data\ 按 mtime 补 ----------
$dataSrc = Join-Path $SourceRoot 'data'
$dataDst = Join-Path $TargetRoot 'data'
if (Test-Path -LiteralPath $dataSrc) {
    $files = Get-ChildItem -LiteralPath $dataSrc -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
        $full = $_.FullName
        $rel = $full.Substring($dataSrc.Length)
        $skipDir = $false
        foreach ($ex in $ExcludeDirs) { if ($rel -like "*\$ex*") { $skipDir = $true; break } }
        if ($skipDir) { return $false }
        foreach ($pat in $ExcludeFiles) { if ($_.Name -like $pat) { return $false } }
        return $true
    }
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($dataSrc.Length).TrimStart('\')
        Copy-IfNewer -Src $f.FullName -Dst (Join-Path $dataDst $rel)
    }
    Write-Host ("data\：扫描 {0} 个文件" -f @($files).Count) -ForegroundColor DarkGray
} else {
    Write-Warn2 "源树没有 data\：$dataSrc"
}

# ---------- 2. 白名单里的任务产物 ----------
foreach ($pat in $ArtifactPaths) {
    $items = Get-ChildItem -Path (Join-Path $SourceRoot $pat) -ErrorAction SilentlyContinue -File
    if (-not $items) { continue }
    foreach ($f in $items) {
        $rel = $f.FullName.Substring($SourceRoot.Length).TrimStart('\')
        Copy-IfNewer -Src $f.FullName -Dst (Join-Path $TargetRoot $rel)
    }
}

# ---------- 3. 汇总 ----------
Write-Host ''
Write-Host ("{0}：{1} 个文件{2}" -f $(if ($Apply) { '已补' } else { '待补' }), $copied.Count, $(if ($Apply) { '' } else { '（加 -Apply 才会写）' })) -ForegroundColor Cyan
$copied | Select-Object -First 40 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
if ($copied.Count -gt 40) { Write-Host ("    ... 其余 {0} 个" -f ($copied.Count - 40)) -ForegroundColor DarkGray }
Write-Host ("未变跳过：{0}" -f $skipped) -ForegroundColor DarkGray
if ($failed.Count -gt 0) {
    $failed | ForEach-Object { Write-Bad $_ }
    exit 1
}

# ---------- 4. 镜像完整性 ----------
if (-not $SkipQuickCheck) {
    $mirror = Join-Path $TargetRoot 'data\native_board_mirror'
    $dbs = @(Get-ChildItem -LiteralPath $mirror -Filter '*.sqlite3' -File -ErrorAction SilentlyContinue)
    if ($dbs.Count -eq 0) {
        Write-Warn2 "没有找到原生看板镜像（$mirror）—— 下一轮 native_board_mirror_sync 会重建"
    } else {
        # runtime\ 在仓库根（my_app 的上两级），不是 apps\ 下
        $repoRoot = Split-Path -Parent (Split-Path -Parent $TargetRoot)
        $py = Join-Path $repoRoot 'runtime\python312\python.exe'
        if (-not (Test-Path -LiteralPath $py)) { $py = 'python' }
        $checker = Join-Path $PSScriptRoot 'sqlite_quick_check.py'
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'   # python 把结果写 stdout，非零退出由 $LASTEXITCODE 判
        try {
            $out = & $py $checker @($dbs | ForEach-Object { $_.FullName }) 2>&1
            $checkOk = ($LASTEXITCODE -eq 0)
        } finally { $ErrorActionPreference = $prevEap }
        foreach ($line in @($out)) {
            if ("$line" -match '\t') {
                $parts = "$line" -split "`t", 2
                if ($parts[1].Trim() -eq 'ok') { Write-Ok ("{0} quick_check=ok" -f $parts[0]) }
                else { Write-Bad ("{0} quick_check={1}" -f $parts[0], $parts[1]) }
            } elseif ("$line".Trim()) { Write-Host "    $line" -ForegroundColor DarkGray }
        }
        if (-not $checkOk) { Write-Bad '镜像不完整：先用 01 的健康库替换（损坏件留 .corrupt_<日期>），再重跑本脚本'; exit 1 }
    }
}

if (-not $Apply -and $copied.Count -gt 0) {
    Write-Host '干跑结束：以上文件在实切时会从上一版生产补到目标树。' -ForegroundColor Yellow
}
exit 0
