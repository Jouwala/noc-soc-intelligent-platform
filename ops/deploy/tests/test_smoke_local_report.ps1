<#
  test_smoke_local_report.ps1 — smoke-local.ps1 报告渲染的离线测试
  ======================================================================
  背景：PR #145 的冒烟评论判失败，但正文里 §8.1–§8.4 全写「通过」，
  没有任何一项写「失败」——失败原因无法从评论查到（Issue #150）。
  本测试用合成的 smoke.ps1 结果表喂给 Convert-SmokeResultToReport，
  断言生成的 PR 评论能把失败项列出来。

  运行（本机 Linux VM 已装 PowerShell 7.4.6：~/pwsh/pwsh）：
    ~/pwsh/pwsh -NoProfile -File ops/deploy/tests/test_smoke_local_report.ps1

  原理：用 AST 只取 smoke-local.ps1 里的报告函数（Get-MdCell、
  Convert-SmokeResultToReport、Write-SmokeReport、Get-MdFailRows），
  不执行脚本顶层的 D:\02 守卫（生产边界、HEAD 校验、锁），所以能在
  任何机器上跑；真正的冒烟流程不受影响。
#>
[CmdletBinding()]
param(
    [string]$SmokeLocalPath = (Join-Path $PSScriptRoot '..\smoke-local.ps1')
)

$ErrorActionPreference = 'Stop'

# ---- 从 smoke-local.ps1 取报告函数（不执行顶层守卫） ----
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $SmokeLocalPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) {
    throw "smoke-local.ps1 解析失败：$($parseErrors[0].Message)"
}
$wanted = @('Write-Log', 'Get-MdCell', 'Get-MdFailRows', 'Convert-SmokeResultToReport', 'Write-SmokeReport')
$found = @()
foreach ($fn in $ast.FindAll(
    { $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
    if ($fn.Name -in $wanted) {
        Invoke-Expression $fn.Extent.Text
        $found += $fn.Name
    }
}
foreach ($name in @('Write-Log', 'Get-MdCell', 'Convert-SmokeResultToReport', 'Write-SmokeReport')) {
    if ($name -notin $found) { throw "smoke-local.ps1 里找不到函数 $name" }
}

# ---- 报告函数依赖的脚本级变量 ----
$ScriptVersion = 'smoke-local.ps1 v1.1.0'
$StartedAt = Get-Date
$PrNumber = 145
$HeadSha = 'ffb7fdae2118f406553dbb03c4cbca3510bd2a9c'
$ShortSha = 'ffb7fda'
$IssueNumber = 0
$DryRun = $false
$LogFile = Join-Path ([System.IO.Path]::GetTempPath()) 'smoke-local-test.log'

$script:Failures = 0
function Assert-Contains([string]$Text, [string]$Needle, [string]$Msg) {
    if ($Text -notmatch [regex]::Escape($Needle)) {
        Write-Host "  FAIL: $Msg（缺：$Needle）"
        $script:Failures++
    } else {
        Write-Host "  ok: $Msg"
    }
}
function Assert-NotContains([string]$Text, [string]$Needle, [string]$Msg) {
    if ($Text -match [regex]::Escape($Needle)) {
        Write-Host "  FAIL: $Msg（不应出现：$Needle）"
        $script:Failures++
    } else {
        Write-Host "  ok: $Msg"
    }
}

$FailItems = @()
function Invoke-Case([string]$Name, [string]$ResultMd, [scriptblock]$Checks) {
    Write-Host "用例：$Name"
    $Results = @{}
    $Notes = @()
    $script:FailItems = @()
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("smoke-case-{0}.md" -f ([guid]::NewGuid().ToString('N')))
    [System.IO.File]::WriteAllText($tmp, $ResultMd, [System.Text.UTF8Encoding]::new($false))
    $ReportFile = Join-Path ([System.IO.Path]::GetTempPath()) ("smoke-report-{0}.md" -f ([guid]::NewGuid().ToString('N')))
    Convert-SmokeResultToReport -ResultMdPath $tmp -VerdictHint 'test'
    Write-SmokeReport
    $report = [System.IO.File]::ReadAllText($ReportFile)
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ReportFile -Force -ErrorAction SilentlyContinue
    & $Checks $report
}

function New-ResultMd([string]$Rows, [string]$Verdict, [int]$FailCount) {
    @"
# 冒烟结果 PR #145 @ ffb7fda

- 根目录: D:\02_olbr_portal
- HEAD(开始): ffb7fdae2118f406553dbb03c4cbca3510bd2a9c
- HEAD(结束): ffb7fdae2118f406553dbb03c4cbca3510bd2a9c
- 开始: 2026-11-18 08:59:26  结束: 2026-11-18 09:03:18
- pytest: MY_APP_DB_MODE=sqlite -m 'not live'

## 检查项

| 结果 | 检查 | 详情 |
|---|---|---|
$Rows

TOTAL=16 FAIL=$FailCount
RESULT: $Verdict
"@
}

$pass81 = '| PASS | §8.1 npm run build (apps\my_app\frontend, FE_DIST_OUT→apps\_fe_dist) | exit=0 elapsed=51.6s |'
$pass82dir = "| PASS | §8.2 SHOW data_directory 在 D:\02_olbr_portal 下（非生产 5432） | pgReady=True data_directory='D:/02_olbr_portal/runtime/pgdata' port=55432 |"
$pass82auth = '| PASS | §8.2 local_authority_tools check 三表行数 credentials/task_schedule_config/email_recipients = 20/74/10 | exit=0; credentials source=20 target=20; task_schedule_config source=74 target=74; email_recipients source=11 target=10 |'
$pass83_1 = "| PASS | (1) /api/health 返回 db_mode=pg | db_mode='pg' body={} |"
$pass83_2 = '| PASS | (2) /api/wo-dashboard/overview 返回非空业务数据 | len=1234 body={} |'
$pass83_3 = '| PASS | (3) 门户 http://127.0.0.1:9277/uniplam/ 返回 200 | http_code=200 |'
$pass83_4a = '| PASS | (4a) nginx http://127.0.0.1:8080/myapp/ 返回 200 | http_code=200 |'
$pass83_4b = '| PASS | (4b) nginx https://127.0.0.1:8443/myapp/ 返回 200 | http_code=200 |'
$pass83_5 = '| PASS | (5) 8080 提供的 index.html 与 apps\_fe_dist\index.html 同 sha256 | dist=ABCDEF1234567890 served=ABCDEF1234567890 |'
$pass83_6 = '| PASS | (6) 02 的 nginx -t 通过（02 自己的 nginx.exe 与 conf） | exit=0 out=syntax is ok |'
$failOwn = '| FAIL | 02 端口均由 D:\02_olbr_portal 下的进程监听 | 7039:1234->D:\other\python.exe |'
$passStop = '| PASS | stop_all.ps1 退出码为 0 | exit=0 timedOut=False |'
$pass80443 = '| PASS | 80/443 仍由 D:\01_olbr_portal\nginx-1.28.0\nginx.exe 监听 | 80: 24448->D:\01_olbr_portal\nginx-1.28.0\nginx.exe ; 443: 24448->D:\01_olbr_portal\nginx-1.28.0\nginx.exe |'
$passFree = '| PASS | 02 的 8080/8443/7039/7091/9277/55432 已释放 | 8080=0 8443=0 7039=0 7091=0 9277=0 55432=0 |'
$passPy = "| PASS | pytest 无未登记失败(-m `"not live`", 与 CI 一致) | exit=1 summary='2 failed, 967 passed, 7 skipped, 25 warnings in 100.84s (0:01:40)' out=TMP\smoke_pytest_145_ffb7fda.out ignored: tests/test_nur_exclude_semantics.py::test_Y_subtracts_the_list, tests/test_nur_exclude_semantics.py::test_Y_is_not_the_list_itself |"

# 用例 A：复现 #145——§8.3 的 (1)(2)(4a)(4b)(6) 行缺失，另有一项 FAIL，结论 FAIL。
# 旧行为：§8.3 写「全部通过」，且无失败项清单（查不到原因）。
$rowsA = ($pass81, $pass82dir, $pass82auth, $failOwn, $passStop, $pass80443, $passFree, $passPy) -join "`n"
Invoke-Case 'A：#145 复现——§8.3 行缺失 + 一项 FAIL' (New-ResultMd $rowsA 'FAIL' 1) {
    param($report)
    Assert-Contains $report '### 失败项' '失败时有「失败项」一节'
    Assert-Contains $report '02 端口均由' '失败项列出 FAIL 的检查名'
    Assert-Contains $report 'D:\other\python.exe' '失败项列出 FAIL 的详情'
    Assert-Contains $report '未找到' '缺失的 §8.3 检查行被标出'
    Assert-NotContains $report '全部通过' '§8.3 不再谎报「全部通过」'
}

# 用例 B：全通过——无失败项一节，§8.3 全部通过，(3) 显示门户详情。
$rowsB = ($pass81, $pass82dir, $pass82auth, $pass83_1, $pass83_2, $pass83_3, $pass83_4a, $pass83_4b, $pass83_5, $pass83_6, $passStop, $pass80443, $passFree, $passPy) -join "`n"
Invoke-Case 'B：全通过——无回归' (New-ResultMd $rowsB 'PASS' 0) {
    param($report)
    Assert-NotContains $report '### 失败项' '通过时不出现「失败项」一节'
    Assert-Contains $report '全部通过' '§8.3 写「全部通过」'
    Assert-Contains $report 'http_code=200' '(3) 显示门户检查的详情'
}

# 用例 C：(5) sha256 行 FAIL——§8.3 写失败，(5) 不再写「一致」。
$fail83_5 = '| FAIL | (5) 8080 提供的 index.html 与 apps\_fe_dist\index.html 同 sha256 | dist=AAAA served=BBBB |'
$rowsC = ($pass81, $pass82dir, $pass82auth, $pass83_1, $pass83_2, $pass83_3, $pass83_4a, $pass83_4b, $fail83_5, $pass83_6, $passStop, $pass80443, $passFree, $passPy) -join "`n"
Invoke-Case 'C：(5) sha256 失败' (New-ResultMd $rowsC 'FAIL' 1) {
    param($report)
    Assert-Contains $report '### 失败项' '失败时有「失败项」一节'
    Assert-Contains $report 'dist=AAAA served=BBBB' '失败项列出 (5) 行的详情'
    Assert-NotContains $report '5. index.html sha256 一致' '(5) 不再谎报「一致」'
}

# 用例 D：(3) 行存在 + 已释放行存在——「3. 门户 9277」显示门户详情，不取释放行。
$rowsD = ($pass81, $pass82dir, $pass82auth, $pass83_1, $pass83_2, $pass83_3, $pass83_4a, $pass83_4b, $pass83_5, $pass83_6, $passStop, $pass80443, $passFree, $passPy) -join "`n"
Invoke-Case 'D：(3) 行与已释放行并存' (New-ResultMd $rowsD 'PASS' 0) {
    param($report)
    Assert-Contains $report '3. 门户 9277：http_code=200' '(3) 显示门户检查的详情，不是释放行详情'
}

if ($script:Failures -gt 0) {
    Write-Host "`n失败：$($script:Failures) 个断言未通过"
    exit 1
}
Write-Host "`n全部通过"
exit 0
