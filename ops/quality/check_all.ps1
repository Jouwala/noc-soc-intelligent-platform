# check_all.ps1 — 02 隔离副本离线质量检查（完整版，第一轮重构批 E）
#
# 顺序见《NOC-SOC-Intelligent-Platform最终重构执行方案》第 7 节批 E。
# 同一份检查也在 CI 里跑（.github/workflows/quality.yml，windows-latest）。
#
# 用法（必须在 D:\02_olbr_portal 根目录运行；不在 01、不在 RPA 里跑）：
#   powershell -NoProfile -File ops\quality\check_all.ps1
#   powershell -NoProfile -File ops\quality\check_all.ps1 -SkipSteps 6,7 -SkipReason '门户 node_modules 尚未安装'
#
# 步骤：
#   1. 当前目录是 02，存在 ISOLATED_COPY.flag
#   2. .env 端口不在生产端口集合里
#   3. 被跟踪文件里没有 .env、数据库、私钥、session（用 git ls-files，只查已跟踪文件）
#   4. python -m compileall -q apps ops\deploy
#   5. 在 apps\my_app 跑 ruff E9,F821
#   6. 在 apps\portal 跑 npm run lint 和 npm test
#   7. 在 apps\portal 跑 npm run check:docs
#   8. 在 apps\my_app 跑 python tests\test_doc_consistency.py
#   9. 路径断言：bot 引擎脚本、eflow_Q&A\tools\inspect_tt.py、_fe_dist 的相对关系
#
# 第 6 到 8 步在依赖或基线还没准备好时可以单独跳过：
#   - 自动跳过：前置条件不满足时（例如没有 npm、没有 node_modules）脚本打印
#     SKIP + 原因并继续，不记为通过；
#   - 手动跳过：-SkipSteps 指定步骤号，必须同时给 -SkipReason，否则拒绝执行。
# 跳过的步骤汇总在脚本末尾的 SKIP 清单里。任何一步失败（非跳过）都立即非零退出。
# 本脚本不访问 UME、eFlow、生产库、邮箱、WhatsApp、内网地址；不跑全量 pytest。
$ErrorActionPreference = 'Stop'

param(
    [int[]]$SkipSteps = @(),
    [string]$SkipReason = ''
)

$Root = (Get-Location).Path
$Skipped = @()

function Write-StepPass {
    param([int]$Step, [string]$Message)
    Write-Host "[$Step/9] PASS $Message"
}

function Write-StepSkip {
    param([int]$Step, [string]$Reason)
    $script:Skipped += "步骤 $Step ：$Reason"
    Write-Host "[$Step/9] SKIP $Reason"
}

function Test-SkipRequested {
    param([int]$Step)
    if ($Step -in $SkipSteps) {
        if (-not $SkipReason) {
            throw "步骤 $Step 要求跳过，但没有给 -SkipReason：跳过必须说明原因，不能静默跳过。"
        }
        Write-StepSkip -Step $Step -Reason "手动跳过：$SkipReason"
        return $true
    }
    return $false
}

function Invoke-Checked {
    param([int]$Step, [string]$What, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What 失败（exit=$LASTEXITCODE）" }
    Write-StepPass -Step $Step -Message "$What 通过"
}

# 1. 当前目录是 02，且存在 ISOLATED_COPY.flag
if ($Root -notlike '*02_olbr_portal') {
    throw "当前目录不是 02（$Root），拒绝执行。"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root 'ISOLATED_COPY.flag'))) {
    throw "缺少 ISOLATED_COPY.flag，拒绝执行：当前目录可能不是隔离副本。"
}
Write-StepPass -Step 1 -Message "目录是 02，ISOLATED_COPY.flag 存在"

# 2. .env 里的端口不是生产端口（80、443、5432、7038、7090、9267）
# 只比对端口赋值的值，不做子串匹配（否则 8080 会误命中 80、8443 会误命中 443）。
$ProdPorts = @('80', '443', '5432', '7038', '7090', '9267')
$EnvFile = Join-Path $Root '.env'
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "缺少 .env，拒绝执行。"
}
$bad = @()
foreach ($line in (Get-Content -LiteralPath $EnvFile)) {
    if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
    $value = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'")
    if ($value -in $ProdPorts) {
        $bad += $line.Trim()
    }
}
if ($bad.Count -gt 0) {
    throw ".env 用了生产端口：`n$($bad -join "`n")"
}
Write-StepPass -Step 2 -Message ".env 端口不在生产端口集合里"

# 3. 被跟踪文件里没有 .env、数据库、私钥、session
# 用 git ls-files，只查已跟踪文件，不扫描工作区全部文件（工作区里有未跟踪的
# 运行时产物，扫全量会误报）。注意刻意不用 "session" 子串匹配：
# apps\my_app\backend\services\site\site_outage_session.py 与
# apps\portal\v3\team_portal\assets\team-session.js 是正常源码。
$tracked = git ls-files
if ($LASTEXITCODE -ne 0) { throw "git ls-files 失败" }
$badTracked = @()
foreach ($f in $tracked) {
    $name = [System.IO.Path]::GetFileName($f)
    $isBad = $false
    $why = ''
    if ($name -match '^\.env(\..*)?$' -and $name -ne '.env.example') { $isBad = $true; $why = '.env' }
    elseif ($f -match '\.(db|sqlite|sqlite3)$') { $isBad = $true; $why = '数据库文件' }
    elseif ($f -match '\.(pem|key)$' -or $name -match '^(id_rsa|id_ed25519)') { $isBad = $true; $why = '私钥' }
    elseif ($name -match '\.session$' -or $name -eq 'session.json' -or $f -match '(^|[\\/])sessions?[\\/]' -or $f -match '(^|[\\/])auth-data[\\/]') { $isBad = $true; $why = 'session/登录态' }
    if ($isBad) { $badTracked += "$f（$why）" }
}
if ($badTracked.Count -gt 0) {
    throw "被跟踪文件里有不该入库的东西：`n$($badTracked -join "`n")"
}
Write-StepPass -Step 3 -Message "被跟踪文件里没有 .env、数据库、私钥、session"

# 4. compileall（apps 与 ops\deploy）
Invoke-Checked -Step 4 -What 'compileall（apps ops\deploy）' -Command {
    python -m compileall -q apps ops\deploy
}

# 5. ruff E9,F821（apps\my_app）
# ruff 是必过项，不可跳过；没装就报错并提示安装方式，不要假装通过。
Push-Location (Join-Path $Root 'apps\my_app')
try {
    $ruffVer = python -m ruff --version 2>$null
    if ($LASTEXITCODE -ne 0) { throw "ruff 未安装：python -m ruff 不可用，先 pip install ruff 再跑本脚本（步骤 5 不可跳过）。" }
    Invoke-Checked -Step 5 -What 'ruff E9,F821' -Command {
        python -m ruff check backend --select E9,F821
    }
} finally {
    Pop-Location
}

# 6. 门户 npm run lint 和 npm test
if (-not (Test-SkipRequested -Step 6)) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    $portalNodeModules = Join-Path $Root 'apps\portal\node_modules'
    if (-not $npm) {
        Write-StepSkip -Step 6 -Reason "本机没有 npm，跳过门户 lint/test（先装 Node 再跑）"
    } elseif (-not (Test-Path -LiteralPath $portalNodeModules)) {
        Write-StepSkip -Step 6 -Reason "apps\portal\node_modules 不存在，跳过门户 lint/test（先在 apps\portal 跑 npm ci）"
    } else {
        Push-Location (Join-Path $Root 'apps\portal')
        try {
            Invoke-Checked -Step 6 -What '门户 npm run lint' -Command { npm run lint }
            Invoke-Checked -Step 6 -What '门户 npm test' -Command { npm test }
        } finally {
            Pop-Location
        }
    }
}

# 7. 门户 npm run check:docs
if (-not (Test-SkipRequested -Step 7)) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    $portalNodeModules = Join-Path $Root 'apps\portal\node_modules'
    if (-not $npm) {
        Write-StepSkip -Step 7 -Reason "本机没有 npm，跳过门户 check:docs（先装 Node 再跑）"
    } elseif (-not (Test-Path -LiteralPath $portalNodeModules)) {
        Write-StepSkip -Step 7 -Reason "apps\portal\node_modules 不存在，跳过门户 check:docs（先在 apps\portal 跑 npm ci）"
    } else {
        Push-Location (Join-Path $Root 'apps\portal')
        try {
            Invoke-Checked -Step 7 -What '门户 npm run check:docs' -Command { npm run check:docs }
        } finally {
            Pop-Location
        }
    }
}

# 8. my_app python tests\test_doc_consistency.py
if (-not (Test-SkipRequested -Step 8)) {
    $guard = Join-Path $Root 'apps\my_app\tests\test_doc_consistency.py'
    if (-not (Test-Path -LiteralPath $guard)) {
        throw "缺少文档一致性守卫 apps\my_app\tests\test_doc_consistency.py（它是已跟踪源码，不可跳过）。"
    }
    Push-Location (Join-Path $Root 'apps\my_app')
    try {
        Invoke-Checked -Step 8 -What 'my_app 文档一致性守卫' -Command {
            python tests\test_doc_consistency.py
        }
    } finally {
        Pop-Location
    }
}

# 9. 路径断言：bot 引擎脚本、eflow_Q&A\tools\inspect_tt.py、_fe_dist 的相对关系
$myAppDir = Join-Path $Root 'apps\my_app'
$appsDir = Join-Path $Root 'apps'
$botEngine = Join-Path $Root 'apps\whatsapp-kb-bot\scripts\tt-analyze.py'
$inspectTt = Join-Path $Root 'apps\eflow_Q&A\tools\inspect_tt.py'
$feDist = Join-Path $Root 'apps\_fe_dist'
# 9a. bot 引擎脚本存在，且 bot 与 my_app 同级（同一父目录 apps）
if (-not (Test-Path -LiteralPath $botEngine)) { throw "缺少 bot 引擎脚本 apps\whatsapp-kb-bot\scripts\tt-analyze.py" }
$botDir = Split-Path -Parent (Split-Path -Parent $botEngine)
if ((Split-Path -Parent $botDir) -ne $appsDir) { throw "whatsapp-kb-bot 与 my_app 不同级" }
if ((Split-Path -Parent $myAppDir) -ne $appsDir) { throw "my_app 不在 apps\ 下" }
# 9b. eflow_Q&A\tools\inspect_tt.py 存在，且 eflow_Q&A 在 my_app 的父目录
if (-not (Test-Path -LiteralPath $inspectTt)) { throw "缺少 apps\eflow_Q&A\tools\inspect_tt.py" }
$eflowDir = Split-Path -Parent (Split-Path -Parent $inspectTt)
if ((Split-Path -Parent $eflowDir) -ne $appsDir) { throw "eflow_Q&A 不在 my_app 的父目录" }
# 9c. _fe_dist 必须在 my_app 的上一级（apps\_fe_dist）；与 vite.config.ts、main.py 的写法一致
$viteConfig = Get-Content -LiteralPath (Join-Path $Root 'apps\my_app\frontend\vite.config.ts') -Raw
if ($viteConfig -notmatch "\.\./\.\./_fe_dist") { throw "vite.config.ts 的 outDir 不是 ../../_fe_dist" }
$mainPy = Get-Content -LiteralPath (Join-Path $Root 'apps\my_app\backend\main.py') -Raw
if ($mainPy -notmatch '_fe_dist') { throw "backend\main.py 里找不到 _fe_dist" }
if (Test-Path -LiteralPath $feDist) {
    if (-not (Test-Path -LiteralPath $feDist -PathType Container)) { throw "apps\_fe_dist 存在但不是目录" }
    Write-StepPass -Step 9 -Message "路径断言通过：bot 引擎脚本、inspect_tt.py、_fe_dist 均在 my_app 的父目录关系上"
} else {
    # _fe_dist 是构建产物，02 里没有它时不要为检查去建空目录（方案第 3.2 节）；
    # 只断言「它应该在的位置」，记为 SKIP，不记为通过。
    Write-StepSkip -Step 9 -Reason "apps\_fe_dist 不存在（尚未构建），相对关系按约定为 my_app 的上一级；构建后复核"
}

Write-Host ""
if ($Skipped.Count -gt 0) {
    Write-Host "check_all 完成，但以下步骤被跳过（未验证，不是“通过”）："
    foreach ($s in $Skipped) { Write-Host "  SKIP $s" }
} else {
    Write-Host "check_all 全部通过"
}
