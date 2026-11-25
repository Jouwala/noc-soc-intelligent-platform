# port-status.ps1 —— 只读打印指定端口的监听状态（供启停/重启脚本收尾展示）
# ==========================================================================
# 用法：powershell -NoProfile -File port-status.ps1 -Ports 7038,7090
# 只读：不改变任何进程/服务，仅 Get-NetTCPConnection + 进程名查询。
# 注意：-File 传参是「字符串」不是数组，故 -Ports 收 [string] 再按逗号拆分
#       （写成 [int[]] 会在 -File 调用下绑定失败，2026-11-25 实测）。
# 输出刻意用 ASCII（UP/DOWN）：调用方多为未 chcp 65001 的 cmd 窗口，
# 中文会在 ANSI 代码页下乱码；ASCII 在任何代码页都可读。
param(
    [string]$Ports = '80,443,7038,7090,9267,9268'
)
$ErrorActionPreference = 'Continue'
foreach ($s in ($Ports -split ',')) {
    $p = 0
    if (-not [int]::TryParse($s.Trim(), [ref]$p)) { continue }
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) {
        $procId = [int]$c.OwningProcess
        $name = ''
        try {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction Stop
            if ($proc) { $name = [string]$proc.Name }
        } catch { }
        Write-Host ("  port {0,-5} : UP    pid={1} {2}" -f $p, $procId, $name)
    } else {
        Write-Host ("  port {0,-5} : DOWN" -f $p)
    }
}
