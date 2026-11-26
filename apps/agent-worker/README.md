# apps/agent-worker —— 分布式 Agent Worker（TASK-03）

按 `docs/workspace/agent-protocol.md` 与多 Worker 中央调度器契约实现的 Worker：
以 GitHub Issues 为**任务发现入口**，任务认领、租约、范围锁、完成与取消的
**权威状态走中央调度器 `apps/control-plane/backend/scheduler/`**（`Scheduler` 协议）；
Worker 完成注册、持续心跳、认领、执行、结果回传与统一清理，并处理断线恢复、
取消与「一任务一 Worktree」。

适配器在 `apps/control-plane/backend/adapters/github_issue.py`
（`GitHubIssueAdapter`，Worker 侧只用于 `list_claimable()` 只读发现）。
**GitHub 评论/状态标签回写仅由 `GitHubMirror` 的 outbox 重放镜像完成**，
Worker 不再自己发 `TASK_COMPLETED`（避免双写与重复评论）。

| 文件 | 职责 |
| --- | --- |
| `worker.py` | 主循环编排：注册 → 心跳 → 只读发现 → 调度器认领 → 范围锁 → 执行 → 幂等完成；try/finally 覆盖全部退出路径 |
| `task_lifecycle.py` | 任务生命周期写操作：调度器状态迁移（running/blocked/retryable/终态）、执行期续租、取消确认、`GitHubMirror.drain` 与统一清理 |
| `executor.py` | 在 Worktree 内执行 argv/callable；独立进程组/Session，超时/取消时 SIGTERM→宽限→SIGKILL 整棵进程树（Windows `taskkill /T`）、`wait()` 回收并确认树退出；范围守卫与只提交范围内文件 |
| `watchdog.py` | 看门狗：拉起 Worker、读心跳文件；卡死/退出即重启（有上限）；重启前确认旧实例退出，处理 SIGTERM/SIGINT，容忍心跳解析失败与上一次运行残留的旧心跳 |
| `heartbeat.py` | Worker 存活心跳：临时文件 + `flush/fsync` + `os.replace` 原子写（含 Windows 并发读重试），后台线程默认 30s 刷新，内容含 agent/instance/pid/at/task/state |
| `cancellation.py` | 取消轮询线程（默认 5s）：查 `scheduler.is_cancel_requested`，命中置 Event 供执行器终止进程组 |
| `hostinfo.py` | 主机信息与能力标签：`HOSTNAME`→`socket.gethostname()`，按 `sys.platform` 生成 `windows-worker` / `linux-worker`（TASK-11） |
| `windows_tree.py` | Windows 进程树快照/强杀/逐个确认（Toolhelp32→wmic→CIM 兜底），保证 `tree_exited` 不假阳性 |
| `command_parse.py` | 「执行命令」解析：结构化 `json` argv 优先，否则平台感知 shlex（Windows 保留反斜杠），永不 `shell=True`（TASK-13） |
| `worktree.py` | Worktree 分配器（一任务一 Worktree、分支 `agent/<任务>`）+ 钩子校验（worktree hooksPath 静默失效坑、POSIX 可执行位 644 拒绝）+ 孤儿 worktree 回收接口 |
| `_bootstrap.py` | 按文件路径加载模块（目录名带连字符不是合法包名）；`load_scheduler()` 加载中央调度器包 |

## 协议映射（中央调度器 + 镜像）

- **发现**：`GitHubIssueAdapter.list_claimable()` 只读拉取 Issue。
- **认领**：`scheduler.upsert_task(...)`（幂等，不改状态）→ `scheduler.claim(task_id, agent_id, instance_id, attempt_id, lease_ttl)`；`task_id = issue-<N>`，回来带 `attempt_id + fencing_token` 作为本次执行身份。
- **租约与心跳分离**：心跳线程只回答「Worker 进程活着」；任务租约由执行期 `scheduler.renew(...)` 续期（默认 600s TTL），两者互不代偿。
- **防重复执行**：完成以 `attempt_id` 作幂等键，`scheduler.complete(...)` 重放返回 `created=False`；旧实例的完成/迁移收 `StaleFencing` 一律作废，绝不覆盖新实例状态。
- **文件范围串行化**：范围从 Issue「改动文件」解析；执行前 `scheduler.acquire_scope`，与在途任务重叠即等待，超时转阻塞。
- **取消**：独立轮询线程发现 `is_cancel_requested` → 执行器终止进程组 → `scheduler.ack_cancel` 落 `cancelled`；取消任务**不再** `complete`。
- **断线恢复**：结果先写 Worktree 的 `TMP/execution-result.json`；GitHub 回写由 outbox + `GitHubMirror.drain` 重放（失败只留待重试，不回滚本地终态），重连/重启后自动收敛。
- **统一清理**：成功保留 worktree；失败/超时/阻塞保留诊断产物但不留租约/范围锁；取消/内部异常在子进程确认退出后回收 worktree；清理异常只记录，不覆盖原始异常。

## 运行

```powershell
# 环境变量：GITHUB_TOKEN（Worker 自身身份，需 contents:write 建/删 refs、
# issues:write 评论/标签）、GITHUB_OWNER、GITHUB_REPO
python apps\agent-worker\worker.py --agent muse-2 --repo-root D:\02_olbr_portal --once   # 单周期
python apps\agent-worker\watchdog.py --agent muse-2 --repo-root D:\02_olbr_portal          # 常驻（看门狗托管）
```

### 常驻 + 自动执行（2026-11-24 流程提速批）

`ops\deploy\install-agent-worker.ps1` 把 watchdog 装成登录时启动的计划任务，
并给 Worker 配两个能力：

| 参数 / 环境变量 | 作用 |
| --- | --- |
| `--require-labels` / `MUSE_REQUIRE_LABELS` | 只处理带齐这些标签的 Issue。常驻 Worker 用 `worker:auto` 做**显式 opt-in**，没打标签的任务不会被自动跑掉 |
| `--agent-cmd` / `MUSE_AGENT_CMD` | Issue 未写「执行命令」时的 agent 命令模板；占位符 `{issue}` / `{prompt_file}` / `{worktree}` / `{branch}` |

流程：Issue 打上 `agent:muse-2` + `state:todo` + `worker:auto` → Worker 认领、
建 worktree、把 Issue 正文渲染成任务书写进 `<worktree>\TMP\agent-task.md` →
按模板拉起 coding agent（默认 `claude -p --permission-mode acceptEdits`）→
agent 在分支上改代码、跑验证、推送并开 PR → Worker 只提交范围内文件、回写状态。
PR 的合并仍由 `auto-merge.yml` 的双门禁裁决，Worker 不合并。

```powershell
# 安装（写 TMP\agent-worker-<agent>.json + 注册计划任务，登录时启动）
powershell -NoProfile -File ops\deploy\install-agent-worker.ps1 -Action Install -Agent muse-2
# 看状态 / 最近日志
powershell -NoProfile -File ops\deploy\install-agent-worker.ps1 -Action Status -Agent muse-2
# 卸载
powershell -NoProfile -File ops\deploy\install-agent-worker.ps1 -Action Uninstall -Agent muse-2
```

- 令牌不落盘：任务每次启动用 `gh auth token` 取当前登录态写进进程环境变量。
- 默认**不启动**任何非 opt-in 任务；`risk:high` 任务不要打 `worker:auto`。

## 权威层与多 VM 语义（ADR-7）

**GitHub refs 是唯一权威**（ADR-7 §零）：认领/租约/fencing/范围锁/完成幂等的状态
全部落在 GitHub（ref + 带幂等标记的评论），不存在本地权威或第二真相源。
`scheduler/api.py` 的 `Scheduler` 协议不变，Worker 代码只走协议；`main()` 经
`scheduler/factory.py` 组装 `GitHubRefScheduler`，**不再有 SQLite 后端回退**
（SQLite 权威实现已在 PR-6 删除；要回滚就 revert PR，不做运行时切换权威）。

多 VM 运行要点：

- 每台 VM 用同一个 `--agent`（逻辑身份）+ 各自的 `instance_id`（默认随机 hex，也可由
  `WorkerConfig.instance_id` 固定）运行；认领/租约按 `instance_id` 区分。
- 执行期续租（`task_lifecycle.renew`）会**推进 `fencing_token`**，Worker 采纳返回值
  更新 `active.fencing_token`，后续 complete/release 都用新代次；旧代次的写操作一律
  `StaleFencing`（结果作废，不覆盖新实例）。
- 过期租约由 `.github/workflows/lease-reaper.yml`（cron 每 5 分钟 / 手动 dispatch）
  调用 `apps/control-plane/backend/dispatch/lease_reaper.py` 回收：Reaper 先按
  `(issue, token)` 用 ref-CAS 抢「回收认领权」（`agent-reap/issue-<N>/<token:08d>`，
  全局唯一，防两个 Reaper 同时收；输家 422 放弃），再删过期 lease ref，并清理孤儿
  scope ref；**HWM ref 永不删除**（token 单调，不因回收被重用）。
- 范围锁是「创建后校验」：冲突时 Worker 按现有等待循环退让重试，超时转 blocked。

任务的可执行内容来自 Issue 正文「执行命令」一节：优先 `json` 代码块里的字符串数组，
否则取代码块第一行按平台 shlex 解析；没有该节的任务会被标记阻塞等待补充，不会臆测执行。

## 离线自测

```powershell
python -m pytest apps\agent-worker\tests -q
```

覆盖：持续心跳（长任务 / FakeClock 30 分钟不误杀 / 原子写并发读）、看门狗不拉起双实例
（旧实例确认退出 + 旧心跳文件不误杀）、执行器超时/取消杀整棵进程树、取消后不提交结果、
无执行命令 / Hook 失败 / 锁超时 / 执行失败 / 内部异常 / 断网的清理矩阵、POSIX Hook
可执行位、平台能力标签、Windows/POSIX 命令解析，以及真实 `GitHubRefScheduler +
RestRefStore + fake GitHub HTTP server`（ref-CAS 权威层）的端到端集成。全部离线（本地 fake server），
不访问真实 GitHub。

**POSIX 进程组与可执行位测试**（`test_executor_process_tree.py` /
`test_worktree_guard.py` 中标记 `skipif(os.name != "posix")` 的用例）在 Windows 上跳过，
由 CI 的 `ubuntu-latest` 作业真实执行；本机 Windows 不代跑。
