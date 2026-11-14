# apps/agent-worker —— 分布式 Windows Agent Worker（TASK-03）

按 `docs/workspace/agent-protocol.md`（第一阶段语义）实现的 Worker：
以 GitHub Issues 为唯一任务中心，在 Windows 目标机上以 Worker 自身的
GitHub 身份运行，完成**注册、心跳、认领、执行、结果回传**全流程，
并处理**断线恢复**与**一任务一 Worktree**。

适配器在 `apps/control-plane/backend/adapters/github_issue.py`
（`GitHubIssueAdapter`），本目录只放 Worker 侧四件：

| 文件 | 职责 |
| --- | --- |
| `worker.py` | 主循环：注册 → 心跳 → 轮询认领 → 范围锁 → 执行 → 回传；断线恢复（结果先落盘、续租续传、过期放弃） |
| `worktree.py` | Worktree 分配器（一任务一 Worktree、分支 `agent/<任务>`）+ 钩子校验（专查 AGENTS.md 记录的 worktree hooksPath 静默失效坑） |
| `executor.py` | 在 Worktree 内执行命令/callable；范围守卫（超范围改动标出且不进提交）、超时、只提交范围内文件 |
| `watchdog.py` | 看门狗：拉起 Worker、读心跳文件，卡死/退出即重启（有上限） |
| `_bootstrap.py` | 按文件路径加载模块（两个目录名带连字符，不是合法包名） |

## 协议映射

- **认领**：评论首行 `<!-- agent-claim: muse-2, <ISO 8601 UTC> -->` + `TASK_ACCEPTED` 头，标签 `state:todo → state:claimed`。
- **租约**：认领后 4 小时无进展（无 push、无评论）即过期（协议第二节）；`TASK_PROGRESS` 评论即进展信号，续租就是发一条进展评论。租约时长可配（`lease_ttl`），测试用短租约仿真过期重派。
- **防重复执行**：每次执行有唯一 `attempt` 号，写进 `TASK_PROGRESS`；回传（`TASK_COMPLETED`）时适配器校验「当前持有者 + 当前 attempt + 租约未过期」，任一不符以 `StaleAttempt` 拒绝——断线重派后原 Worker 的迟到回传不会二次生效。
- **文件范围串行化**：任务范围从 Issue 正文「改动文件」一节解析；执行前用 `scope-lock:` 进展评论占锁，与任一在途任务重叠（同路径或目录前缀）即等待串行化；完成自动释放。
- **断线恢复**：执行结果先写入 Worktree 的 `TMP/execution-result.json` 再回传；回传遇传输失败退避重试，重连后先核对租约——仍属我且未过期则续租续传（同一 attempt，幂等），已被重派则放弃本地结果并释放 Worktree。

## 运行（Windows 目标机）

```powershell
# 环境变量：GITHUB_TOKEN（Worker 自身身份）、GITHUB_OWNER、GITHUB_REPO
python apps\agent-worker\worker.py --agent muse-2 --repo-root D:\02_olbr_portal --once   # 单周期
python apps\agent-worker\watchdog.py --agent muse-2 --repo-root D:\02_olbr_portal          # 常驻（看门狗托管）
```

任务的可执行内容来自 Issue 正文「执行命令」一节的代码块（第一行）；
没有该节的任务会被标记 `TASK_BLOCKED` 等待补充，不会臆测执行。

## 离线自测

```powershell
python -m pytest apps\agent-worker\tests -q
```

覆盖验收标准：注册/心跳/认领/执行/回传全流程、一任务一 Worktree 与钩子
校验、断网 5 分钟两种情形（租约未过期续租续传；过期重派且零重复执行）、
两个并行任务范围重叠串行化。全部走内存传输 + 假时钟，不访问真实 GitHub。
