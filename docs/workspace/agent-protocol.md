# Agent 协作协议（第一阶段，2026-11-13）

> 适用：Muse 1（总指挥）、Muse 2 / Muse 3（Coding Worker）、Grok Bot（QA Worker）。
> 唯一任务中心是 GitHub Issues，PR 是代码交付载体。
> 所有 Agent 自主轮询 GitHub，不再依赖人工复制粘贴任务描述与测试报告。
> 本文件与 `agent-registry.md`（谁是谁）、`agent-polling-setup.md`（怎么轮询）一起读。

## 一、基本原则

1. **任务只写在 Issue 里。** 任务说明、负责人、优先级、文件范围、依赖、验收标准、分支、状态，全部以 Issue 内容与标签为准。用户聊天、Muse 之间的私下对话都不是任务来源。
2. **状态只用标签表示。** 不要只在正文里写“已完成”，必须同步改标签。标签是机器读的状态机（见第三节）。
3. **进展只用评论汇报。** 认领、进度、结果、失败、测试报告，全部以 Issue 评论承载，格式见第二节。评论分两段：机器可读头（HTML 注释）+ 人可读正文。
4. **不直接调用对方。** 三个 Muse 之间没有 API、CLI、MCP、共享聊天（已调查确认），三台 VM 不能直接互访。Muse 1 不“派发”任务给谁，只把任务写进共享状态，等对方来拉。
5. **失败有出口。** 自动修复最多 3 次，超限打 `needs-replan` 标签，由 Muse 1 重新规划，不再自动重派。

## 二、12 类 Agent 消息（全部带 Task ID，以 Issue 评论承载）

每条评论的第一行必须是机器可读头（HTML 注释），格式固定，时间用 UTC 的 ISO 8601；第二行起是人可读正文。Task ID 就是 Issue 编号（如 `#12`）。

| # | 消息类型 | 机器可读头 | 谁发 | 人可读正文写什么 |
| --- | --- | --- | --- | --- |
| 1 | TASK_ASSIGNED | `<!-- agent-msg: TASK_ASSIGNED, task: #12, from: muse-1, to: muse-2, at: 2026-11-13T19:30:00Z -->` | Muse 1 | 任务已建，指派给谁、优先级、依赖、分支名 |
| 2 | TASK_ACCEPTED | `<!-- agent-msg: TASK_ACCEPTED, task: #12, from: muse-2, at: ... -->` | Muse 2/3 | 已认领：分支名、预计完成时间；同时打 `state:claimed` 标签 |
| 3 | TASK_PROGRESS | `<!-- agent-msg: TASK_PROGRESS, task: #12, from: muse-2, at: ... -->` | Muse 2/3 | 做到哪一步、还剩什么、是否遇到阻塞（阻塞另发 TASK_BLOCKED） |
| 4 | TASK_BLOCKED | `<!-- agent-msg: TASK_BLOCKED, task: #12, from: muse-2, at: ... -->` | 任何 Worker | 卡在哪、已尝试什么、需要什么（等依赖 / 等决定 / 等修复） |
| 5 | TASK_COMPLETED | `<!-- agent-msg: TASK_COMPLETED, task: #12, from: muse-2, at: ... -->` | Muse 2/3 | 改了什么、分支、提交、自检结果（贴 `agent-done-check.mjs` 命令与结果）；同时打 `state:in-review` 标签 |
| 6 | QA_STARTED | `<!-- agent-msg: QA_STARTED, task: #12, from: grok, at: ... -->` | Grok | 冒烟已开始：PR 号、HEAD SHA、预计时长 |
| 7 | QA_FAILED | `<!-- agent-msg: QA_FAILED, task: #12, from: grok, at: ... -->` | Grok | 结构化失败报告：PR、HEAD SHA、失败项、错误信息、日志链接；同时打 `state:qa-failed` 标签 |
| 8 | QA_PASSED | `<!-- agent-msg: QA_PASSED, task: #12, from: grok, at: ... -->` | Grok | 冒烟通过：PR、HEAD SHA、通过项；同时打 `smoke-passed` 与 `state:qa-passed` 标签 |
| 9 | FIX_REQUESTED | `<!-- agent-msg: FIX_REQUESTED, task: #12, from: muse-1, at: ... -->` | Muse 1 | 修复任务已建：新 Issue 编号、失败报告链接、修复轮次（第 1/2/3 次） |
| 10 | FIX_COMPLETED | `<!-- agent-msg: FIX_COMPLETED, task: #12, from: muse-2, at: ... -->` | Muse 2/3 | 修复内容、新提交、回归自检结果；重新进入评审与测试 |
| 11 | PR_CREATED | `<!-- agent-msg: PR_CREATED, task: #12, from: muse-2, at: ... -->` | Muse 2/3 | PR 链接、改动摘要、CI 状态；PR 描述须自动汇总改动与测试结果 |
| 12 | PR_MERGED | `<!-- agent-msg: PR_MERGED, task: #12, from: muse-1, at: ... -->` | Muse 1 | 合并提交、关闭 Issue；同时打 `state:merged` 标签 |

补充规则：

- **认领评论**（TASK_ACCEPTED 的一种固定写法）：`<!-- agent-claim: muse-2, 2026-11-13T19:30:00Z -->`，认领后 4 小时无进展（无 push、无评论）即租约过期，Muse 1 摘 `state:claimed` 标签重派。
- **测试结果必须与 Commit 一致**：QA_PASSED / QA_FAILED 里的 HEAD SHA 必须等于 PR 当前 head SHA；过期测试结果（HEAD 已变）不得触发合并，`auto-merge.yml` 只认 head SHA 上的检查结果。
- **Grok 不写代码**（第一阶段，用户决定）：Grok 只发 QA_STARTED / QA_FAILED / QA_PASSED，不发 TASK_COMPLETED / FIX_COMPLETED，不提交修复代码。

## 三、标签状态机

```
state:todo ──(TASK_ACCEPTED)──▶ state:claimed ──(TASK_COMPLETED + PR)──▶ state:in-review
     ▲                                                                          │
     │ 4 小时无进展，租约过期重派                                              ▼
     │                                                              state:qa-ready ──(Grok 触发)
     │                                                                          │
     │                                                          ┌───────────────┴───────────────┐
     │                                                          ▼                               ▼
     │                                              state:qa-passed                    state:qa-failed
     │                                              (smoke-passed, 可合)               (修复任务，见第四节)
     │                                                          │                               │
     │                                                          ▼                               │
     └────────────────── state:merged ◀──(auto-merge)──────────┘                               │
                                                                                                ▼
                                                                                   修复后重新走
                                                                                   in-review → qa-ready
```

- `state:blocked` 可从任何状态进入（依赖未满足、等用户决定），解除阻塞后回到原状态。
- `needs-replan` 不是状态，是终止标记：自动修复达 3 次上限时打上，Muse 1 重新规划。
- `risk:high` 与状态标签并存：涉及生产环境、数据库结构、VMAX、nginx、生产服务重启的任务必须打，合并前须用户明确批准。
- `do-not-merge` 一票否决：有它在，`auto-merge.yml` 绝不合并。

## 四、失败与返工流程

1. QA 失败：Grok 发 QA_FAILED 结构化报告（PR、HEAD SHA、错误信息、日志链接），打 `state:qa-failed` 标签。
2. Muse 1 建修复 Issue（用 `agent-task.md` 模板），附失败报告链接，写明修复轮次（第 1/2/3 次），发 FIX_REQUESTED，指派回原开发 Agent。
3. 原开发 Agent 修复后发 FIX_COMPLETED，重新走评审与测试。
4. 同一任务修复达 3 次仍失败：打 `needs-replan` 标签，Muse 1 重新规划（拆分任务、换方案或升级给用户），不再自动重派。
5. 无效冒烟不算数：D:\02 被其他任务占用、MSNOC1 掉线等原因导致冒烟中断的，该次结果作废，重新 dispatch，不记为通过也不记为失败。

## 五、轮询规则（三个必查时点，不依赖人工转发）

每个 Agent 必须在以下三个时点主动拉 GitHub（具体设置见 `agent-polling-setup.md`）：

1. **会话开始**：先拉取自己负责的、状态为 `state:todo` 且指派给自己的 Issue，再开始工作。
2. **每次 push 后**：push 完立即拉一次，看 CI 状态与有没有新评论（QA 结果、Muse 1 的反馈）。
3. **认领新任务前**：先拉一次，确认依赖的 Issue 已关闭、任务仍是 `state:todo` 且无人认领，避免撞单。

另外：

- **TASK_ASSIGNED 24 小时无人认领**：Muse 1 在 Issue 下追问（评论提醒被指派的 Worker）。
- **48 小时无回应**：升级给用户，由用户决定换人或改派。
- **认领后 4 小时无进展**：租约过期，Muse 1 摘标签重派（见第二节补充规则）。

## 六、分支与冲突规则

- 一任务一分支：`agent/<任务>`（如 `agent/task-12-data-access`）；一任务一 Worktree：`.agent-worktrees/<任务>`。
- Muse 1 拆分时做文件范围查重：重叠文件范围的任务串行化，高冲突文件强制串行，不并行。
- 禁直接推 `main`：所有代码走分支 + PR + CI + QA，`auto-merge.yml` 只在 `smoke-passed` + CI 全绿时合并。
- 分支落后时自动更新分支（`PUT /pulls/{n}/update-branch`），冲突由开发 Agent 自己 rebase 解决，不自动合冲突 PR。

## 七、自动化实现（TASK-07，2026-11-14）

本协议里以下机械动作已由 GitHub Actions 自动执行，实现细节与安全阀见
`docs/workspace/github-automation.md`：

- 认领（`state:claimed`）后自动建分支、造脚手架提交、开草稿 PR
  （`issue-to-pr.yml`）——第二节的 PR_CREATED 仍由开发 Agent 在转正式
  PR 后发，自动化只做脚手架并在 Issue 下留普通评论，不冒充 Agent 消息。
- CI（quality）失败自动通知 builder（`ci-failure-notify.yml`）——第五节
  轮询规则的自动补位，只通知、不打标签。
- main 前进时自动更新落后的 `agent/*` 分支（`auto-merge.yml` 的
  `update-branches` 作业）——落实第六节的自动更新分支。
- PR 合并后自动回写任务状态：第 12 类 PR_MERGED 评论由
  `pr-merged-writeback.yml` 发出（机器可读头 `from: github-actions`），
  同时打 `state:merged` 并关闭 Issue；Muse 1 只做最终汇总。
- quality 全绿自动请 Grok 冒烟（2026-11-14 新增）：`agent-orchestrator.yml`
  在 `quality` 工作流跑完且结论为 success 时自动 POST webhook，不再
  需要人工打 `state:qa-ready` 标签；打标签与手动 dispatch 仍可用。
  同一 head SHA 只触发一次（去重见 `docs/workspace/github-automation.md`）。
- PR 合并后自动分派下一个任务（2026-11-14 新增）：`auto-dispatch.yml`
  在 PR 合入 main 后找下一个就绪任务（`state:todo` + `agent:muse-2/3` +
  依赖全关 + 无认领/分派评论），发第 1 类 TASK_ASSIGNED 评论
  （机器可读头 `from: github-actions`），优先派给本次合并空闲出来的
  builder；不再等每小时巡逻（`dispatch-check.yml` 只做检查与标签，
  不分派任务）。
