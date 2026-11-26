# Agent 协作协议（第一阶段，2026-11-13）

> 适用：Muse 1（总指挥）、Muse 2 / Muse 3（Coding Worker）、Grok Bot（原 QA Worker，2026-11-14 已退役；QA 现由 D:\02 本机 smoke-watcher 承担）。
> 唯一任务中心是 GitHub Issues，PR 是代码交付载体。
> 所有 Agent 自主轮询 GitHub，不再依赖人工复制粘贴任务描述与测试报告。
> 本文件与 `agent-registry.md`（谁是谁）、`agent-polling-setup.md`（怎么轮询）一起读。

## 一、基本原则

1. **任务只写在 Issue 里。** 任务说明、负责人、优先级、文件范围、依赖、验收标准、分支、状态，全部以 Issue 内容与标签为准。用户聊天、Muse 之间的私下对话都不是任务来源。
2. **状态只用标签表示。** 不要只在正文里写“已完成”，必须同步改标签。标签是机器读的状态机（见第三节）。
3. **进展只用评论汇报。** 认领、进度、结果、失败、测试报告，全部以 Issue 评论承载，格式见第二节。评论分两段：机器可读头（HTML 注释）+ 人可读正文。
4. **不直接调用对方。** 三个 Muse 之间没有 API、CLI、MCP、共享聊天（已调查确认），三台 VM 不能直接互访。Muse 1 不“派发”任务给谁，只把任务写进共享状态，等对方来拉。
5. **失败有出口。** 自动修复最多 3 次，超限打 `needs-replan` 标签，由 Muse 1 重新规划，不再自动重派。

## 二、15 类 Agent 消息（全部带 Task ID，以 Issue 评论承载）

每条评论的第一行必须是机器可读头（HTML 注释），格式固定，时间用 UTC 的 ISO 8601；第二行起是人可读正文。Task ID 就是 Issue 编号（如 `#12`）。

| # | 消息类型 | 机器可读头 | 谁发 | 人可读正文写什么 |
| --- | --- | --- | --- | --- |
| 1 | TASK_ASSIGNED | `<!-- agent-msg: TASK_ASSIGNED, task: #12, from: muse-1, to: muse-2, at: 2026-11-13T19:30:00Z -->` | Muse 1 | 任务已建，指派给谁、优先级、依赖、分支名 |
| 2 | TASK_ACCEPTED | `<!-- agent-msg: TASK_ACCEPTED, task: #12, from: muse-2, at: ... -->` | Muse 2/3 | 已认领：分支名、预计完成时间；同时打 `state:claimed` 标签 |
| 3 | TASK_PROGRESS | `<!-- agent-msg: TASK_PROGRESS, task: #12, from: muse-2, at: ... -->` | Muse 2/3 | 做到哪一步、还剩什么、是否遇到阻塞（阻塞另发 TASK_BLOCKED） |
| 4 | TASK_BLOCKED | `<!-- agent-msg: TASK_BLOCKED, task: #12, from: muse-2, at: ... -->` | 任何 Worker | 卡在哪、已尝试什么、需要什么（等依赖 / 等决定 / 等修复） |
| 5 | TASK_COMPLETED | `<!-- agent-msg: TASK_COMPLETED, task: #12, from: muse-2, at: ... -->` | Muse 2/3 | 改了什么、分支、提交、自检结果（贴 `agent-done-check.mjs` 命令与结果）；同时打 `state:in-review` 标签 |
| 6 | QA_STARTED | `<!-- agent-msg: QA_STARTED, task: #12, from: smoke-watcher, at: ... -->` | smoke-watcher（D:\02 本机） | 触发：watcher 检出 quality 全绿的 PR head、开始冒烟前。冒烟已开始：PR 号、HEAD SHA、预计时长；带去重标记 `qa-started: pr-<PR>-sha-<短SHA>`；同时打 `state:qa-ready` 标签（QA 状态单值化，见第三节） |
| 7 | QA_FAILED | `<!-- agent-msg: QA_FAILED, task: #12, from: smoke-watcher, at: ... -->` | smoke-watcher（D:\02 本机） | 结构化失败报告：PR、HEAD SHA（完整 40 位）、失败项（`  - ` 缩进列表行）、错误信息与报告入口；带去重标记 `qa-result: pr-<PR>-sha-<短SHA>`；同时打 `state:qa-failed` 标签 |
| 8 | QA_PASSED | `<!-- agent-msg: QA_PASSED, task: #12, from: smoke-watcher, at: ... -->` | smoke-watcher（D:\02 本机） | 冒烟通过：PR、HEAD SHA、通过项；带去重标记 `qa-result: pr-<PR>-sha-<短SHA>`；同时给 PR 打 `smoke-passed`、给 Issue 打 `state:qa-passed` 标签 |
| 9 | FIX_REQUESTED | `<!-- agent-msg: FIX_REQUESTED, task: #12, from: auto-fix, at: ... -->` | auto-fix 工作流（GitHub Actions） | 触发：Issue 被打 `state:qa-failed` 且有 QA_FAILED 评论（双信号）。修复任务已建：新 Issue 编号、失败报告链接、修复轮次（第 1/2/3 次）、负责人（回派原开发 Agent；取不到则注明待 Muse 1 指派） |
| 10 | FIX_COMPLETED | `<!-- agent-msg: FIX_COMPLETED, task: #12, from: muse-2, at: ... -->` | Muse 2/3（修复 Agent） | 发在**原 Issue** 上：修复内容、新提交、回归自检结果；修复 PR 重新进入评审与测试。无自动化代发，由修复 Issue 的验收清单要求修复者手工发 |
| 11 | PR_CREATED | `<!-- agent-msg: PR_CREATED, task: #12, from: muse-2, at: ... -->` | Muse 2/3 | PR 链接、改动摘要、CI 状态；PR 描述须自动汇总改动与测试结果 |
| 12 | PR_MERGED | `<!-- agent-msg: PR_MERGED, task: #12, from: github-actions, at: ... -->` | github-actions（pr-merged-writeback 工作流） | 合并提交、关闭 Issue；同时打 `state:merged` 标签、摘其余 `state:` 标签 |
| 13 | REVIEW_PASSED | `<!-- review-result: pr-<N> sha-<40> reviewer-<名> verdict=passed -->` | Muse 1（独立审核） | 审核对象 head、复核的测试命令与结果、结论；格式与判定见 `docs/workspace/review-record-protocol.md`（Phase 2 P2-1，#131） |
| 14 | REVIEW_FAILED | `<!-- review-result: pr-<N> sha-<40> reviewer-<名> verdict=changes-requested -->` | Muse 1（独立审核） | 逐条阻塞发现（文件:行号 + 证据）与要求的修复 |
| 15 | REVIEW_BLOCKED | `<!-- review-result: pr-<N> sha-<40> reviewer-<名> verdict=blocked -->` | Muse 1（独立审核） | 阻断原因（如涉生产库、须用户决定）与下一步 |

### 五类 QA/FIX 消息的落地口径（TASK-06-A #48 / TASK-06-B #49，2026-11-15）

第 6/7/8/9/10 类消息已全部有实现（第 10 类为人工发送），逐类口径如下；完整链路与逐环文件对照见 `docs/workspace/qa-fix-loop.md`。

- **QA_STARTED（第 6 类）**：smoke-watcher 在开始冒烟前发（`ops/deploy/smoke-watcher.ps1:519` 调用，`:380` 实现）。先经 `Set-IssueQaState`（`:342`）把 Issue 的 QA 状态单值化为 `state:qa-ready`——摘 `state:in-review` 与其余三个 qa 状态、只留目标状态，下同。正文第二行带去重标记 `qa-started: pr-<PR>-sha-<短SHA>`；发前查 Issue 评论里有无同标记（`Test-IssueMarker`，`:352`），有则不重发，不依赖本机状态文件。
- **QA_PASSED / QA_FAILED（第 7/8 类）**：smoke-watcher 冒烟结束后发（`:574` 调用，`:402` 实现）。标签先行单值化（通过 → `state:qa-passed`，失败 → `state:qa-failed`），去重标记 `qa-result: pr-<PR>-sha-<短SHA>` 同上。QA_FAILED 的失败项是正文中以两空格缩进的列表行（`  - …`，从冒烟报告里抽取含「失败/✗/FAIL/未通过/结论」的行，上限 12 条）——这是 auto-fix 解析失败项的固定格式，改格式必须两边同改。QA 回写失败只记 watcher 日志、不挡冒烟主流程（`:520`/`:574` 的 try/catch）。
- **FIX_REQUESTED（第 9 类）**：auto-fix 工作流发（`.github/workflows/auto-fix.yml:211-227`），`from: auto-fix`，不是 Muse 1 手工发。触发是双信号：Issue 被打 `state:qa-failed`（`issues: labeled` 事件，`:44-61`）且评论里有最新一条 QA_FAILED 机器可读头——缺一不动手（`:86-101` 复核）。工作流从 QA_FAILED 正文解析 PR 编号（`qa-result: pr-<N>` 标记）、HEAD SHA（第一个 40 位十六进制串）与失败项（`  - ` 行），见 `:95-109`。
- **FIX_COMPLETED（第 10 类）**：无自动化代发。修复 Issue 的验收清单要求修复者在**原 Issue** 发此评论（清单由 auto-fix 写进修复 Issue 正文，`.github/workflows/auto-fix.yml:187`）；发没发、内容全不全目前靠 Muse 1 评审时核对，**无机器校验（待实现）**。

补充规则：

- **认领评论**（TASK_ACCEPTED 的一种固定写法）：`<!-- agent-claim: muse-2, 2026-11-13T19:30:00Z -->`，认领后 4 小时无进展（无 push、无评论）即租约过期，Muse 1 摘 `state:claimed` 标签重派。
- **CLAIM_EXPIRED（自动化补充，不属于上表 15 类）**：`lease-reaper.yml` 每 5 分钟巡检，认领后 4 小时无进展时自动发 `<!-- agent-msg: CLAIM_EXPIRED, task: #N, from: lease-reaper, at: ... -->`，随即摘 `state:claimed`、补 `state:todo`；同一次认领只发一条（评论里的 `claim-expiry: issue-N claimed-at-<ts>` 标记用于去重）。它替代了「Muse 1 人工摘标签」这一步，脚本判定不了的例外仍由 Muse 1 处理。
- **测试结果必须与 Commit 一致（可实现判据，#48 落地口径）**：冒烟结果以**绑定标记**而不是标签本身承载 SHA——watcher 贴到 PR 的冒烟报告评论首行是 `<!-- smoke-result: pr-<N> sha-<40 位 SHA> result=passed|failed -->`（`ops/deploy/smoke-watcher.ps1:560`），`smoke-passed` 标签只是指针。`auto-merge.yml` 合并前独立复核（不依赖 watcher 在场）：取 PR 评论里**最后一条** `result=passed` 标记的 SHA（`:160`），与 PR 当前 head SHA（`:143`）比对——无标记（`:161`）或对不上（`:162`）一律跳过合并；之后再验该 head 上的 quality 运行（`:168`）与全部 check-runs（`:180`）为成功，最后合并请求自身也带 `sha` 锁定（`:189`）。反向地，watcher 发现 PR 上的 `smoke-passed` 绑定的是旧 head 时，先摘标签再对新 head 重跑（`smoke-watcher.ps1:471-491`），不让过期标签长期挂着。
- **QA 不写代码**（第一阶段，用户决定）：QA 只发 QA_STARTED / QA_FAILED / QA_PASSED，不发 TASK_COMPLETED / FIX_COMPLETED，不提交修复代码。2026-11-14 起 QA 由 D:\02 本机 smoke-watcher 承担，Grok Bot 已退役。
- **QA/FIX 消息的消费方**：QA_FAILED 是 auto-fix 的输入信号（见上）；QA_STARTED / QA_PASSED 目前**没有自动消费方**，是给人与其他 Agent 看的回写（信息性），不要假设有工作流在等它们。FIX_REQUESTED 由 auto-fix 自己发出，不需要谁再转发。

## 三、标签状态机

边分两类：**[自动]** = GitHub Actions 工作流或 D:\02 本机 smoke-watcher 机械执行；**[人/Agent]** = Muse 1 或开发/修复 Agent 的动作。图里标 [自动] 的边没人需要盯着推；标 [人/Agent] 的边没有自动化兜底，卡住只能靠轮询规则（第五节）发现。

```
state:todo ──(TASK_ACCEPTED [人/Agent])──▶ state:claimed ──(TASK_COMPLETED + PR [人/Agent])──▶ state:in-review
     ▲                                                                                                │
     │ 4 小时无进展，租约过期重派 [人/Agent: Muse 1]                                                  ▼
     │                                                                                     state:qa-ready
     │                                                                               [自动: watcher 冒烟开始时打,
     │                                                                                先摘 in-review 与其余 qa 状态]
     │                                                                                                │
     │                                                                ┌───────────────┴───────────────┐
     │                                                                ▼                               ▼
     │                                                state:qa-passed                    state:qa-failed
     │                                                [自动: watcher 发 QA_PASSED]        [自动: watcher 发 QA_FAILED]
     │                                                (PR 同时打 smoke-passed, 可合)                  │
     │                                                                │                               ▼
     │                                                                │                  [自动: auto-fix 建修复 Issue
     │                                                                │                    (state:todo, 回派原开发 Agent)
     │                                                                │                    + 在原 Issue 发 FIX_REQUESTED]
     │                                                                │                               │
     │                                                                │                               ▼
     │                                                                │                  修复 Issue 独立走同一状态机:
     │                                                                │                  todo → claimed → in-review
     │                                                                │                  → qa-ready → qa-passed / qa-failed
     │                                                                │                  (链式返工按根任务计轮次, 见第四节)
     │                                                                ▼                               │
     └────────────────── state:merged ◀──(auto-merge [自动])──────────┘                               │
                                                                                                      ▼
                                                              根任务第 4 次 QA 失败 [自动: auto-fix 打 needs-replan
                                                              + 说明评论, 不再建修复 Issue] ──▶ Muse 1 重新规划 [人/Agent]
```

- **QA 四个标签的进入/退出**：`state:qa-ready` 由 watcher 在冒烟开始时进入；`state:qa-passed` / `state:qa-failed` 由 watcher 在冒烟结束时进入。退出方式统一：watcher 的 `Set-IssueQaState`（`ops/deploy/smoke-watcher.ps1:342`）每次写 QA 状态都先摘掉 `state:in-review` 与其余三个 qa 状态，保证 Issue 任一时刻至多一个 QA 状态标签；合并后 `state:merged` 由 pr-merged-writeback 打上并摘掉其余全部 `state:` 标签。QA 状态的进入全部是 [自动] 边，没有人工打 qa 标签的口径。
- **审核与 QA 并行**：`state:in-review` 表示“PR 已开、待独立审核”（Phase 2 P2-1，#131），由开发 Agent 在 TASK_COMPLETED 时打上；watcher 在冒烟开始时按上条摘掉它（既有行为，QA 状态单值化），此后审核状态不由 Issue 标签承载，而由 PR 评论里的 review-result 标记承载（passed / changes-requested / blocked，绑定 head SHA），与 QA 标签并行存在、互不覆盖。`state:in-review` 的进入/退出仍是“开发 Agent 打、watcher 摘”，不是审核者打的——这是已知缺口，待 Phase 2 后续收口。
- `state:blocked` 可从任何状态进入（依赖未满足、等用户决定），解除阻塞后回到原状态。
- `needs-replan` 不是状态，是终止标记：进入条件是**同一根任务第 4 次 QA 失败**（已有 3 轮修复 Issue 后再失败），由 auto-fix 打上并停手（`.github/workflows/auto-fix.yml:126-146`）；已带 `needs-replan` 的 Issue，auto-fix 整体跳过（`:90-93`）。退出只能靠 Muse 1 重新规划后人工处理 [人/Agent]。
- `risk:high` 与状态标签并存：涉及生产环境、数据库结构、VMAX、nginx、生产服务重启的任务必须打，合并前须用户明确批准。
- `do-not-merge` 一票否决：有它在，`auto-merge.yml` 绝不合并（`.github/workflows/auto-merge.yml:153`）。

## 四、失败与返工流程

1. **QA 失败（信号成立）**：D:\02 本机 smoke-watcher 发 QA_FAILED 结构化报告（PR、HEAD SHA、失败项、错误信息、报告入口），打 `state:qa-failed` 标签（TASK-06-A #48，`ops/deploy/smoke-watcher.ps1:402`）。下游只认**双信号**：标签 + 最新一条 QA_FAILED 机器可读头评论同时成立，缺一不动手。
2. **自动建修复 Issue（auto-fix 工作流，TASK-06-B #49）**：`.github/workflows/auto-fix.yml` 在 `state:qa-failed` 被打上时触发（或人工 `workflow_dispatch` 重放，`:44-61`）。它解析 QA_FAILED 拿到 PR/SHA/失败项，按 `agent-task.md` 模板八节建修复 Issue：正文写 `依赖：depends on #<原 Issue>`、失败项逐条转成验收项、标题 `[修复] #<原> QA 失败（第 R 轮）：<原标题>`；回派只认原 Issue 上的 `agent:muse-2` / `agent:muse-3` 标签（Muse 1 是协调者不回派），取不到就**不派给任何人**、在正文与 FIX_REQUESTED 里注明请 Muse 1 介入指派，绝不猜人（`:149-154`、`:210-227`）。建完在原 Issue 发 FIX_REQUESTED（第 9 类，`from: auto-fix`），带新 Issue 编号、失败报告链接与轮次。**幂等**：同一根任务 + 同一失败 HEAD SHA 只建一个修复 Issue，重复触发直接跳过（`:111-124`）。
3. **修复与回写**：原开发 Agent 认领修复 Issue、修复、开修复 PR；修完在**原 Issue** 发 FIX_COMPLETED（第 10 类，新提交 + 回归自检结果）。修复 PR 重新走 quality + 冒烟全流程（同一状态机递归，见第三节）。
4. **链式返工按根任务计数**：修复 Issue 的 PR 再失败时，QA_FAILED 落在修复 Issue 上，auto-fix 照样触发；轮次不按单个 Issue 数，而按**根任务**数——修复 Issue 正文首行带机器可读标记 `<!-- auto-fix: for-issue-<直属任务> root-issue-<根任务> round-<R> sha-<失败HEAD> -->`，计数 = 全仓 Issue 里 `root-issue` 等于本链根任务的标记数（`:15-24`、`:156`），整条链共用一个 3 次上限，不会无限套娃。修复 Issue 与原 Issue 的依赖关系有两条：正文 `depends on #<直属任务>`，以及标记里的 `root-issue` 指回链首任务。
5. **达上限停手**：已有 3 轮修复 Issue 后再出现 QA 失败（即第 4 次失败），auto-fix **不再建 Issue**，只给当前 Issue 打 `needs-replan` + 发说明评论（根任务、失败报告、PR 与 SHA），然后退出（`:126-146`）。之后由 Muse 1 重新规划（拆分任务、换方案或升级给用户）[人/Agent]，系统不再自动重派。

无效冒烟不算数（不进返工计数）：D:\02 被其他任务占用、MSNOC1 掉线等原因导致冒烟中断的，该次结果作废——watcher 不发 QA_PASSED/QA_FAILED、不打 qa 状态标签，重新冒烟即可；它既不是通过也不是失败，不触发 auto-fix。

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
- 分支落后时**不再自动更新**（2026-11-24 流程提速批）：分支保护未要求
  「必须最新」，落后分支可直接合并；有冲突时由开发 Agent 自己 rebase，
  或手动调 `PUT /pulls/{n}/update-branch` 更新一次。原「main 前进就批量
  update-branch」的做法会让每个在途 PR 换 head，CI、冒烟、审核记录全部
  作废重跑（实测 PR #242 三小时重跑三遍），已停用。

## 七、自动化实现（TASK-07，2026-11-14）

本协议里以下机械动作已由 GitHub Actions 自动执行，实现细节与安全阀见
`docs/workspace/github-automation.md`：

- 认领（`state:claimed`）后自动建分支、造脚手架提交、开草稿 PR
  （`issue-to-pr.yml`）——第二节的 PR_CREATED 仍由开发 Agent 在转正式
  PR 后发，自动化只做脚手架并在 Issue 下留普通评论，不冒充 Agent 消息。
- CI（quality）失败自动通知 builder（`ci-failure-notify.yml`）——第五节
  轮询规则的自动补位，只通知、不打标签。
- ~~main 前进时自动更新落后的 `agent/*` 分支（`auto-merge.yml` 的
  `update-branches` 作业）~~——**2026-11-24 起停用**，只在人工
  `workflow_dispatch` 且勾选 `update_branches=true` 时批量更新；原因见
  第六节最后一条。
- 卡死运行自动兜底（2026-11-24 新增 `run-watchdog.yml`）：`quality`
  运行 `queued`/`in_progress` 超 30 分钟自动 cancel、`completed+failure`
  且 0 个 job 自动 rerun（最多 2 次尝试），避免一次异常把 PR 卡几小时。
- PR 合并后自动回写任务状态：第 12 类 PR_MERGED 评论由
  `pr-merged-writeback.yml` 发出（机器可读头 `from: github-actions`），
  同时打 `state:merged` 并关闭 Issue；Muse 1 只做最终汇总。
- quality 全绿后的冒烟由 D:\02 本机 smoke-watcher 执行（2026-11-14 起）：
  watcher 轮询检出 CI 已绿的 PR head，在本机跑 `ops\smoke\smoke.ps1`，
  贴回结果评论，通过才打 `smoke-passed`。原 `agent-orchestrator.yml`
  自动 POST webhook 请 Grok 冒烟的路径已随 Grok 退役摘除（工作流在
  Actions 层面 disabled、只剩手动 dispatch，待删除），不再生效。
- PR 合并后自动分派下一个任务（2026-11-14 新增）：`auto-dispatch.yml`
  在 PR 合入 main 后找下一个就绪任务（`state:todo` + 注册表白名单
  `agent:muse-2/3/4/5` + 依赖全关 + 无认领/分派评论），发第 1 类 TASK_ASSIGNED 评论
  （机器可读头 `from: github-actions`），优先派给本次合并空闲出来的
  builder；不再等每小时巡逻（`dispatch-check.yml` 只做检查与标签，
  不分派任务）。
- 触发路径（2026-11-14 修正）：上面两步由 auto-merge 在合并成功后显式
  POST workflow dispatch 触发（`workflow_dispatch`，填 PR 号）。原因是
  auto-merge 用默认 GITHUB_TOKEN 合并，GitHub 不会为该 token 触发的
  事件再启动新工作流，`pull_request closed` 事件被抑制（实测 PR #31
  合入后两者均为 0 次运行）。手动在网页上合并（用人 token）时
  pull_request closed 事件正常触发，两条路径都保留；两工作流也都支持
  手动 workflow_dispatch 补救。

## 八、中央调度器权威化（2026-11-14 多 Worker 修复，ADR 见 `multi-worker-scheduler-design.md`）

> 本节是对第一～七节的**修订**：GitHub Issue 评论不再是原子锁。任务描述、
> 验收、PR/评审、展示与审计仍在 GitHub；**认领、租约、文件范围锁、执行代次、
> 任务状态、完成幂等、分派登记由中央调度器裁决**（`apps/control-plane/backend/scheduler/`）。
> 评论与标签是 outbox 异步回写的镜像，允许短暂落后，不参与裁决。

### 8.1 身份四层

| 字段 | 含义 | 生成 |
| --- | --- | --- |
| `agent_id` | 逻辑 Worker 身份（muse-2 / muse-3），用于标签与注册 | 配置 |
| `instance_id` | 单次运行实例唯一身份 | Worker 进程启动时 |
| `attempt_id` | 一次任务执行尝试 | 认领时生成 |
| `fencing_token` | 任务级单调递增执行代次 | 每次认领成功 +1（调度器事务内） |

续租、状态迁移、完成、释放范围锁、取消确认**必须**带 `attempt_id + fencing_token`；
代次不符一律拒绝（旧实例恢复后不能提交或改写状态）。

### 8.2 状态机（调度器权威状态）

```
queued → claimed → running → completed / failed / blocked
                    ↓ 取消请求
             cancel_requested → cancelled
claimed/running --租约过期回收--> retryable/expired --新实例认领--> claimed
```

`completed / failed / cancelled` 是终态，不再接受认领；`blocked` / `retryable`
可被重新认领。调度器拒绝任何未列入 `TRANSITIONS` 的迁移。

### 8.3 与 GitHub 的对照

- 认领成功：调度器用 GitHub ref CAS（`agent-hwm/<issue>/<token>`）认领 → 写 `TASK_ACCEPTED`
  与 `state:claimed` 评论/标签（评论与标签是派生/审计，权威在 refs）；
- 完成：完成评论（含 `<!-- ref-completion-id: KEY -->`）是**权威幂等标记**，标签收敛到
  `state:in-review`；写失败整体失败并重试，重放先分页扫标记 → 不会产生第二条完成评论，
  **不再出现「完成评论已写、标签失败后永久卡死」**；
- 失败 / 取消：写 `ref-state` 标记并按代次收敛标签（`state:blocked` / `state:todo`），
  对应 `TASK_FAILED` / `TASK_CANCELLED`（本轮扩展的第 13/14 类消息，机器可读头格式与第二节一致；
  `state:qa-failed` 仍专指 QA 冒烟失败，不用于执行失败）；
- 范围锁：`agent-scope/*` refs 是权威；`scope-lock` / `scope-unlock` 评论仅作审计；
- 分派：`TASK_ASSIGNED` 之前必须先做原子登记（git ref CAS + dispatch_id），
  同一逻辑任务只形成一次有效分派。

### 8.4 运行要求

- Linux 训练机：Worker 以独立进程组运行，心跳线程默认 30s 原子写；看门狗阈值
  默认 180s（显著大于心跳间隔）；重启前必须确认旧进程退出（SIGTERM→SIGKILL→wait）。
- **权威唯一（PR-6，2026-11-14）**：调度器权威是 GitHub refs（ref-CAS，`agent-hwm` /
  `agent-lease` / `agent-scope` 等），不再有本地 SQLite 调度器数据库；`AGENT_SCHEDULER_DB`
  与 SQLite 权威实现在 PR-6 已删除，**不保留缓存或回退**（任何双权威都会导致状态分叉）。
- **多 VM**：ref-CAS 权威层跨 VM 由 GitHub 服务端原子裁决认领/fencing/范围锁；生产部署前
  仍须按部署清单做一次双机联调验证（见 `multi-worker-github-native-authority.md`）。
