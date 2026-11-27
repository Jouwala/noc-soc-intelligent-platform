# MSNOC1 文件任务队列（v1）

> 背景：MuseLink 连接器只能读写 D:\02 上的文件、不能在 MSNOC1 上执行命令。
> 所以用文件当队列：Muse 1 把任务 JSON 写进 inbox\，MSNOC1 上的计划任务每 60 秒
> 触发 `ops\deploy\muse-job-runner.ps1` 跑一轮（单次执行后退出，不常驻）。
> 设计全文见 `~/workspace/msnoc1-job-queue-design.md`（设计稿，未入库）。

## 目录结构

队列根目录固定为 `D:\02_olbr_portal\TMP\muse-jobs`（首次运行自动创建）：

```
D:\02_olbr_portal\TMP\muse-jobs\
  inbox\      待处理任务（任务 JSON，一个文件一个任务）
  claimed\    已认领、执行中的任务（同盘符重命名，原子认领）
  done\       执行成功的任务原文件
  failed\     被拒绝 / 执行失败 / 超时的任务原文件
  results\    每个任务的结果文件 <id>.result.json
  audit.log   审计日志（认领 / 开始 / 完成 / 失败 / 拒绝 / 重认领）
  runner.lock 单实例锁（活进程 pid + 时间戳；死锁或超 4 小时视为过期）
```

`done\` / `failed\` / `results\` 各保留最新 100 条，超出的每轮自动清理。

## 任务格式

单文件 ≤16KB，未知字段、非法 JSON 直接拒绝进 `failed\`。
`id` 须匹配 `^[A-Za-z0-9_-]{8,64}$`；`timeout_seconds` 可选，默认 1800，范围 60..7200。

```json
{"id":"20261126-1717-smoke-287","type":"smoke-rerun","payload":{"pr":287},
 "created_by":"muse-1","created_at":"2026-11-26T17:17:00Z","timeout_seconds":1800}
```

结果文件 `results\<id>.result.json`：

```json
{"id":"...","type":"smoke-rerun","status":"completed","exit_code":0,
 "started_at":"...","finished_at":"...","output_tail":"...","error":""}
```

`status` 取值：`completed` / `failed` / `rejected` / `timeout`。

## v1 任务类型（只做这两类，都是幂等，可重跑）

| type | payload | 实际执行 |
| --- | --- | --- |
| `smoke-rerun` | `{"pr": <纯数字>}` | `.smoke\ops\deploy\smoke-watcher.ps1 -OnlyPr <N> -Once` |
| `restart-smoke-services` | `{"services": ["backend", ...]}` | `.smoke\ops\deploy\stop_all.ps1` 再 `.smoke\ops\deploy\start_all.ps1`（整栈重启，现有脚本不支持单服务启停） |

服务名白名单：`backend`、`frontend`、`portal`、`nginx`、`postgres`、`wa_bot`、`wa_analysis`
（只允许 `start_all.ps1` / `stop_all.ps1` 里出现的 smoke 副本服务名）。

### 安全红线

- 永远不执行任务文件里的命令字符串，禁用 `Invoke-Expression`；只按白名单调固定脚本、固定参数。
- 只动 `.smoke` 副本（`D:\02_olbr_portal\.smoke`）：执行前校验 `ISOLATED_COPY.flag` 在、`PRODUCTION.flag` 不在；生产 `D:\02` 的脚本一律不调。
- `claimed\` 里超过 1 小时无结果的任务下一轮重新认领（两类都是幂等，可重跑）。
- **v1 不做**：生产重启、生产 git pull / 同步、任意命令。生产操作留待 v2，按任务单独审批。

## 注册计划任务（MSNOC1 上手动执行一次）

在 MSNOC1 上**以管理员身份**打开 PowerShell，执行：

```powershell
$taskName = 'OLBR_MuseJobRunner'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "D:\02_olbr_portal\ops\deploy\muse-job-runner.ps1"'
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Seconds 60) -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
```

验证注册成功：

```powershell
Get-ScheduledTask -TaskName 'OLBR_MuseJobRunner' | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName 'OLBR_MuseJobRunner' | Select-Object LastRunTime, LastTaskResult
```

注销（不再需要时）：`Unregister-ScheduledTask -TaskName 'OLBR_MuseJobRunner' -Confirm:$false`

## 试运行步骤（用已合并的 #285 验证，不动生产）

1. 确认计划任务已注册且状态为 `Ready`，`ops\deploy\muse-job-runner.ps1` 已同步到 `D:\02_olbr_portal`。
2. 写一个 smoke-rerun 任务（PR #285 已合并，冒烟应直接通过；不要用未合并的 PR 做首次验证）：

   ```powershell
   $task = '{"id":"20261127-trial-smoke-285","type":"smoke-rerun","payload":{"pr":285},"created_by":"muse-1","created_at":"2026-11-27T09:00:00Z"}'
   Set-Content -Path 'D:\02_olbr_portal\TMP\muse-jobs\inbox\trial-smoke-285.json' -Value $task -Encoding UTF8
   ```

3. 等 60 秒内的一轮（或手动跑一次 `powershell -NoProfile -ExecutionPolicy Bypass -File D:\02_olbr_portal\ops\deploy\muse-job-runner.ps1`），检查：
   - `D:\02_olbr_portal\TMP\muse-jobs\results\20261127-trial-smoke-285.result.json` 里 `status` 为 `completed`；
   - 任务原文件已移到 `done\`；
   - `audit.log` 里有 `[claim]` / `[start]` / `[completed]` 三行。
4. 再试一个 restart-smoke-services 任务（只重启 `.smoke` 副本，生产不受影响）：

   ```powershell
   $task = '{"id":"20261127-trial-restart-01","type":"restart-smoke-services","payload":{"services":["backend","nginx"]},"created_by":"muse-1","created_at":"2026-11-27T09:00:00Z"}'
   Set-Content -Path 'D:\02_olbr_portal\TMP\muse-jobs\inbox\trial-restart-01.json' -Value $task -Encoding UTF8
   ```

   等一轮后同样核对结果文件为 `completed`，并确认生产端口（80/443/7038/7090/9267）未受影响。

## 自测（开发机）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ops\deploy\muse-job-runner.ps1 -SelfTest
```

在临时目录里用桩脚本跑 27 个断言：合法 smoke-rerun / restart 的认领→执行→done；
非法 JSON / 未知 type / 非法 id / 超大文件 / 未知字段 / pr 非数字 / 未知服务名进 failed；
claimed 超 1 小时重认领；保留上限修剪到 100；超时杀进程；单实例锁；审计日志。
